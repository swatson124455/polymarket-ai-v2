"""
EliteUserDetector - Flags elite traders by performance.
Uses session-based DB and users table (total_trades, win_rate, total_profit, is_elite).
"""
from typing import Optional
from structlog import get_logger
from base_engine.data.database import Database
from sqlalchemy import text, select, update, func
from base_engine.data.database import User
from config.settings import settings

logger = get_logger()


def _get_elite_thresholds() -> dict:
    """Elite thresholds: 100 trades OR $10k volume, 55% win, focus on high vol+return."""
    return {
        "min_trades": getattr(settings, "ELITE_MIN_TRADES", 100),
        "min_win_rate": getattr(settings, "ELITE_MIN_WIN_RATE", 0.55),
        "min_profit": getattr(settings, "ELITE_MIN_PROFIT_USD", 0.0),
        "min_volume": getattr(settings, "ELITE_MIN_VOLUME_USD", 10000.0),
    }


# Exported for tests
ELITE_THRESHOLDS = _get_elite_thresholds()


class EliteUserDetector:
    """Detects and flags elite traders based on performance."""

    def __init__(self, db: Database, thresholds: Optional[dict] = None):
        self.db = db
        self.thresholds = thresholds or _get_elite_thresholds()

    async def recalculate_user_stats_from_trades(self) -> int:
        """Recalculate user stats (total_trades, win_rate) from trades in last year. Returns count updated.
        Only overwrites when we have >= min_trades resolved trades; preserves API stats for sparse users."""
        if not self.db.session_factory:
            return 0
        lookback_days = getattr(settings, "ELITE_LOOKBACK_DAYS", 365)
        min_trades = self.thresholds["min_trades"]
        try:
            async with self.db.get_session() as session:
                # Update total_trades: count only trades on resolved markets in last year
                # Only overwrite when we have enough resolved trades; else preserve API stats.
                # BUG-15 fix: UNION ALL instead of OR-JOIN to allow index usage
                result = await session.execute(text("""
                    UPDATE users u
                    SET total_trades = COALESCE(trade_counts.cnt, 0)
                    FROM (
                        SELECT user_address, COUNT(*) as cnt
                        FROM (
                            SELECT DISTINCT t.user_address, t.id
                            FROM trades t
                            JOIN markets m ON t.market_id = CAST(m.id AS TEXT)
                            WHERE t.user_address IS NOT NULL
                            AND t.market_id IS NOT NULL
                            AND m.resolved = TRUE AND m.resolution IN ('YES', 'NO')
                            AND t.timestamp >= NOW() - INTERVAL '1 day' * :lookback_days
                            UNION
                            SELECT DISTINCT t.user_address, t.id
                            FROM trades t
                            JOIN markets m ON t.market_id = m.condition_id
                            WHERE t.user_address IS NOT NULL
                            AND t.market_id IS NOT NULL
                            AND m.resolved = TRUE AND m.resolution IN ('YES', 'NO')
                            AND t.timestamp >= NOW() - INTERVAL '1 day' * :lookback_days
                        ) deduped
                        GROUP BY user_address
                        HAVING COUNT(*) >= :min_trades
                    ) trade_counts
                    WHERE u.address = trade_counts.user_address
                    AND (u.total_trades IS NULL OR u.total_trades != trade_counts.cnt)
                """), {"lookback_days": lookback_days, "min_trades": min_trades})
                trades_updated = result.rowcount
                
                # Calculate win_rate from resolved trades in last year
                # BUG-15 fix: UNION ALL instead of OR-JOIN for win_rate calc
                wr_result = await session.execute(text("""
                    UPDATE users u
                    SET win_rate = COALESCE(wr.win_rate, 0.5)
                    FROM (
                        SELECT
                            user_address,
                            CASE WHEN COUNT(*) > 0
                                THEN SUM(won::int)::float / COUNT(*)
                                ELSE 0.5
                            END as win_rate
                        FROM (
                            SELECT DISTINCT ON (t.id) t.user_address, t.id,
                                CASE
                                    WHEN (t.side = 'YES' AND m.resolution = 'YES') OR (t.side = 'NO' AND m.resolution = 'NO')
                                    OR (t.token_id = m.yes_token_id AND m.resolution = 'YES')
                                    OR (t.token_id = m.no_token_id AND m.resolution = 'NO')
                                    THEN TRUE ELSE FALSE
                                END as won
                            FROM trades t
                            JOIN markets m ON t.market_id = CAST(m.id AS TEXT)
                            WHERE t.user_address IS NOT NULL
                            AND t.market_id IS NOT NULL
                            AND m.resolved = TRUE AND m.resolution IN ('YES', 'NO')
                            AND t.timestamp >= NOW() - INTERVAL '1 day' * :lookback_days
                            UNION
                            SELECT DISTINCT ON (t.id) t.user_address, t.id,
                                CASE
                                    WHEN (t.side = 'YES' AND m.resolution = 'YES') OR (t.side = 'NO' AND m.resolution = 'NO')
                                    OR (t.token_id = m.yes_token_id AND m.resolution = 'YES')
                                    OR (t.token_id = m.no_token_id AND m.resolution = 'NO')
                                    THEN TRUE ELSE FALSE
                                END as won
                            FROM trades t
                            JOIN markets m ON t.market_id = m.condition_id
                            WHERE t.user_address IS NOT NULL
                            AND t.market_id IS NOT NULL
                            AND m.resolved = TRUE AND m.resolution IN ('YES', 'NO')
                            AND t.timestamp >= NOW() - INTERVAL '1 day' * :lookback_days
                        ) deduped
                        GROUP BY user_address
                        HAVING COUNT(*) >= :min_trades
                    ) wr
                    WHERE u.address = wr.user_address
                    AND u.win_rate IS DISTINCT FROM wr.win_rate
                """), {"lookback_days": lookback_days, "min_trades": min_trades})
                wr_updated = wr_result.rowcount
                
                await session.commit()
                if trades_updated > 0 or wr_updated > 0:
                    logger.info("Recalculated user stats: %d trade counts, %d win rates", trades_updated, wr_updated)
                return trades_updated + wr_updated
        except Exception as e:
            logger.warning("recalculate_user_stats_from_trades failed: %s", e)
            return 0

    async def _update_market_maker_flags(self, session) -> None:
        """Flag users who trade both YES and NO on >60% of their markets (market-making heuristic)."""
        ratio = getattr(settings, "ELITE_MARKET_MAKER_BOTH_SIDES_RATIO", 0.6)
        try:
            await session.execute(text("""
                UPDATE users u
                SET is_likely_market_maker = (mm.both_sides_ratio > :ratio)
                FROM (
                    SELECT user_address,
                        COUNT(*) FILTER (WHERE sides_traded > 1)::float / NULLIF(COUNT(*), 0) as both_sides_ratio
                    FROM (
                        SELECT user_address, market_id, COUNT(DISTINCT side) as sides_traded
                        FROM trades
                        WHERE user_address IS NOT NULL AND market_id IS NOT NULL
                        GROUP BY user_address, market_id
                    ) per_market
                    GROUP BY user_address
                ) mm
                WHERE u.address = mm.user_address
            """), {"ratio": ratio})
        except Exception as e:
            logger.debug("Market-maker flag update failed (column may not exist): %s", e)

    async def update_elite_status(self) -> None:
        """Recalculate elite status for all users using session-based DB."""
        if not self.db.session_factory:
            return
        
        # First recalculate trade counts from trades table
        await self.recalculate_user_stats_from_trades()
        
        mt = self.thresholds["min_trades"]
        mw = self.thresholds["min_win_rate"]
        mp = self.thresholds["min_profit"]
        mv = self.thresholds.get("min_volume", 10000.0)
        try:
            async with self.db.get_session() as session:
                await self._update_market_maker_flags(session)
                # COALESCE so NULL total_profit passes when min_profit=0 (preserves API-ingested users)
                profit_ok = func.coalesce(User.total_profit, 0) >= mp
                # Activity gate: 100 trades OR $10k volume (either proves meaningful activity)
                from sqlalchemy import or_, and_
                activity_ok = or_(
                    User.total_trades >= mt,
                    func.coalesce(User.total_volume, 0) >= mv,
                )
                await session.execute(
                    update(User).where(
                        activity_ok,
                        User.win_rate >= mw,
                        profit_ok,
                    ).values(is_elite=True)
                )
                # Mark non-elite: anyone who doesn't meet activity + win_rate thresholds
                non_activity = and_(
                    or_(User.total_trades.is_(None), User.total_trades < mt),
                    or_(User.total_volume.is_(None), User.total_volume < mv),
                )
                non_elite_cond = or_(
                    non_activity,
                    User.win_rate.is_(None),
                    User.win_rate < mw,
                )
                if mp > 0:
                    non_elite_cond = or_(non_elite_cond, User.total_profit.is_(None), User.total_profit < mp)
                await session.execute(update(User).where(non_elite_cond).values(is_elite=False))
                await session.commit()
            logger.info("Elite status updated")
        except Exception as e:
            logger.warning("update_elite_status failed: %s", e)

    async def get_near_elite_users(self, limit: int = 100) -> list:
        """Get near-elite users: meet lower thresholds but not full elite criteria.
        Useful for expanding the signal pool when elite data is sparse."""
        if not self.db.session_factory or not getattr(settings, "NEAR_ELITE_ENABLED", True):
            return []
        ne_trades = getattr(settings, "NEAR_ELITE_MIN_TRADES", 30)
        ne_wr = getattr(settings, "NEAR_ELITE_MIN_WIN_RATE", 0.45)
        try:
            async with self.db.get_session() as session:
                result = await session.execute(text("""
                    SELECT address, total_trades, win_rate, total_profit
                    FROM users
                    WHERE total_trades >= :ne_trades
                    AND win_rate >= :ne_wr
                    AND (is_elite IS NOT TRUE)
                    AND (is_likely_market_maker IS NOT TRUE)
                    ORDER BY win_rate DESC, total_trades DESC
                    LIMIT :lim
                """), {"ne_trades": ne_trades, "ne_wr": ne_wr, "lim": limit})
                rows = result.fetchall()
                return [
                    {"address": r[0], "total_trades": r[1], "win_rate": float(r[2] or 0), "total_profit": float(r[3] or 0), "tier": "near_elite"}
                    for r in rows
                ]
        except Exception as e:
            logger.debug("get_near_elite_users failed: %s", e)
            return []

    async def ingest_user_performance(self, address: str, trade_result: dict) -> None:
        """Update user stats after a trade. Uses session + User model."""
        if not self.db.session_factory:
            return
        pnl = float(trade_result.get("pnl", 0))
        is_win = pnl > 0
        try:
            async with self.db.get_session() as session:
                r = await session.execute(select(User).where(User.address == address))
                u = r.scalar_one_or_none()
                if u:
                    u.total_trades = (u.total_trades or 0) + 1
                    u.total_profit = (u.total_profit or 0) + pnl
                    u.wins = (getattr(u, "wins", 0) or 0) + (1 if is_win else 0)
                    u.losses = (getattr(u, "losses", 0) or 0) + (0 if is_win else 1)
                    u.win_rate = (u.wins or 0) / max(1, u.total_trades)
                else:
                    session.add(User(
                        address=address,
                        total_trades=1,
                        total_profit=pnl,
                        wins=1 if is_win else 0,
                        losses=0 if is_win else 1,
                        win_rate=1.0 if is_win else 0.0,
                    ))
                await session.commit()
        except Exception as e:
            logger.warning("ingest_user_performance failed: %s", e)
