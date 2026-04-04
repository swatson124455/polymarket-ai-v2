"""
Automated Position Management - Auto stop-loss/take-profit management.

Features:
- Automatic stop-loss/take-profit
- Position rebalancing
- Profit-taking automation
- Loss-cutting automation
- Position monitoring
"""
import asyncio
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from structlog import get_logger
from config.settings import settings
from base_engine.data.database import Database, Position
from base_engine.execution.execution_engine import ExecutionEngine
from base_engine.execution.advanced_orders import AdvancedOrderManager
from base_engine.execution.exit_strategy import ExitStrategy, ExitParams

logger = get_logger()

# Cooldown for failed exit attempts to prevent spam (seconds)
_EXIT_RETRY_COOLDOWN = 300  # 5 minutes


class AutomatedPositionManager:
    """
    Automated position management system.
    
    Monitors positions and automatically executes stop-loss/take-profit orders.
    When order_gateway is set, all orders go through it (kill switch, risk, paper).
    """
    
    def __init__(
        self,
        execution_engine: ExecutionEngine,
        order_manager: AdvancedOrderManager,
        db: Database,
        prediction_engine: Optional[Any] = None,
        alerting: Optional[Any] = None,
    ):
        self.execution_engine = execution_engine
        self.order_manager = order_manager
        self.db = db
        self.prediction_engine = prediction_engine
        self.alerting = alerting  # Session 51 P1-4: price staleness alerts
        self.order_gateway = None  # Set by BaseEngine so stop-loss/take-profit respect kill switch
        self.risk_manager = None   # Set by BaseEngine to record consecutive trade outcomes
        # Session 45: Intelligent Exit Engine — dynamic cost/vol/TTR/regime-aware exits
        self.exit_strategy = ExitStrategy(db=db)
        self.monitoring = False
        self.monitor_task: Optional[asyncio.Task] = None
        self.check_interval_seconds = 10.0
        self._api_price_cache: Dict[str, tuple] = {}  # token_id -> (price, timestamp) for CLOB fallback
        # Configurable base thresholds (wider defaults for prediction markets)
        self.default_stop_loss_pct = getattr(settings, "PM_STOP_LOSS_PCT", 0.30)
        self.default_take_profit_pct = getattr(settings, "PM_TAKE_PROFIT_PCT", 0.60)
        self._exit_cooldowns: Dict[int, float] = {}  # position_id -> cooldown_until (monotonic)
        # Adaptive exit learning: per-market multipliers from churn + outcome analysis
        self._market_exit_mult: Dict[str, float] = {}  # market_id -> stop multiplier (>1=wider)
        self._last_learning_refresh: float = 0.0
        self._learning_refresh_interval: float = float(
            getattr(settings, "PM_LEARNING_REFRESH_SECONDS", 1800)
        )

    def set_order_gateway(self, gateway) -> None:
        """Route orders through gateway (kill switch, risk, paper)."""
        self.order_gateway = gateway

    def set_risk_manager(self, risk_manager) -> None:
        """Wire risk manager for consecutive loss tracking after stop-loss/take-profit exits."""
        self.risk_manager = risk_manager

    async def _refresh_exit_learning(self) -> None:
        """Learn per-market stop multipliers from churn + resolved outcome data.

        Queries paper_trades for the last 72h and computes per-market adjustments:
        - Churn detection: markets with repeated exit→rebuy cycles get wider stops
        - Resolution outcomes: if resolved markets show stops were premature, widen
        - Markets with no data keep multiplier=1.0 (base settings)
        """
        if not getattr(settings, "PM_ADAPTIVE_EXITS", True):
            return
        now = time.monotonic()
        if now - self._last_learning_refresh < self._learning_refresh_interval:
            return
        self._last_learning_refresh = now
        try:
            from sqlalchemy import text as sa_text
            async with self.db.get_session() as session:
                # 1) Churn: count loss exits per market (last 72h)
                #    Uses positions table (status='closed', unrealized_pnl < 0)
                #    instead of paper_trades SELL records (no longer persisted).
                rows = (await session.execute(sa_text(
                    "SELECT market_id, "
                    "  SUM(CASE WHEN status='closed' AND unrealized_pnl < 0 THEN 1 ELSE 0 END) AS loss_exits, "
                    "  SUM(CASE WHEN status='open' THEN 1 ELSE 0 END) AS buys "
                    "FROM positions "
                    "WHERE opened_at > NOW() - INTERVAL '72 hours' "
                    "GROUP BY market_id "
                    "HAVING SUM(CASE WHEN status='closed' AND unrealized_pnl < 0 THEN 1 ELSE 0 END) >= 2"
                ))).fetchall()

                mults: Dict[str, float] = {}
                for r in rows:
                    mid = str(r.market_id)
                    loss_exits = int(r.loss_exits or 0)
                    buys = int(r.buys or 0)
                    if loss_exits < 2:
                        continue
                    # Churn ratio: buys/exits ≈ 1.0 means every exit was followed by rebuy
                    churn = buys / loss_exits if loss_exits else 0.0
                    # High churn = premature exits → widen stop (mult > 1)
                    # Low churn = exits were final → keep stop (mult ≈ 1)
                    mult = 1.0 + max(0.0, churn - 0.5) * 1.0  # churn=1.0 → mult=1.5
                    mults[mid] = min(3.0, max(0.5, mult))

                # 2) Resolution outcomes: check resolved markets
                #    Uses positions table (closed positions with unrealized_pnl)
                #    instead of paper_trades SELL records.
                resolved = (await session.execute(sa_text(
                    "SELECT p.market_id, "
                    "  AVG(p.unrealized_pnl) AS avg_pnl, COUNT(*) AS cnt "
                    "FROM positions p "
                    "JOIN markets m ON CAST(p.market_id AS TEXT) = CAST(m.id AS TEXT) "
                    "WHERE p.status = 'closed' AND m.resolved = true "
                    "  AND p.opened_at > NOW() - INTERVAL '7 days' "
                    "GROUP BY p.market_id"
                ))).fetchall()

                for r in resolved:
                    mid = str(r.market_id)
                    avg_pnl = float(r.avg_pnl or 0)
                    if avg_pnl < -5.0:
                        # Exits on this resolved market lost money → stops were premature
                        mults[mid] = mults.get(mid, 1.0) * 1.3
                    elif avg_pnl > 0:
                        # Exits were profitable → stops worked correctly
                        mults[mid] = mults.get(mid, 1.0) * 0.8

                # Clamp all multipliers
                self._market_exit_mult = {
                    k: min(3.0, max(0.5, v)) for k, v in mults.items()
                }
                if mults:
                    logger.info(
                        "Adaptive exits refreshed: %d markets, avg_mult=%.2f",
                        len(mults),
                        sum(mults.values()) / len(mults),
                    )
        except Exception as e:
            logger.debug("Adaptive exit learning refresh failed: %s", e)
    
    async def start_monitoring(self):
        """Start monitoring positions."""
        if self.monitoring:
            return
        
        self.monitoring = True
        self.monitor_task = asyncio.create_task(self._monitor_positions())
        logger.info("Automated position management started")
    
    async def stop_monitoring(self):
        """Stop monitoring positions."""
        self.monitoring = False
        if self.monitor_task:
            self.monitor_task.cancel()
            try:
                await self.monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("Automated position management stopped")
    
    def _cycle_count(self) -> int:
        """Cycle counter for periodic tasks (e.g. adverse fill persist every ~2 min)."""
        if not hasattr(self, "_monitor_cycle"):
            self._monitor_cycle = 0
        self._monitor_cycle += 1
        return self._monitor_cycle

    async def _run_adverse_fill_persistence(self) -> None:
        """Update post-fill prices and persist fill_analysis to DB (every ~2 min)."""
        tracker = getattr(self.order_gateway, "adverse_selection_tracker", None) if self.order_gateway else None
        if not tracker:
            return
        try:
            await tracker.update_post_fill_prices()
            n = await tracker.persist_fills_to_db()
            if n:
                logger.debug("fill_analysis persisted %s rows", n)
        except Exception as e:
            logger.debug("adverse fill persistence failed: %s", e)

    async def refresh_positions(self) -> None:
        """One-shot position price refresh. Called after WS reconnect to close stale-price gap."""
        if not self.db or not getattr(self.db, "session_factory", None):
            return
        try:
            from sqlalchemy import select
            async with self.db.get_session() as session:
                result = await session.execute(
                    select(Position).where(Position.status.in_(["open", "reserving"]))
                )
                positions = result.scalars().all()
                if positions:
                    await self._update_current_prices(session, positions)
        except Exception as e:
            logger.debug("refresh_positions failed: %s", e)

    async def _monitor_positions(self):
        """Monitor all positions and execute stop-loss/take-profit."""
        # Initial delay: let startup DB connections settle before position monitoring begins.
        # 10s is sufficient — precompute starts at t+150s; warm task is background (non-blocking).
        await asyncio.sleep(10)
        _last_known_count = 0   # In-memory position count — skip DB query when 0
        _force_check_cycle = 0  # Force a re-check every 3rd cycle (30s) even if empty
        while self.monitoring:
            try:
                if not self.db.session_factory:
                    await asyncio.sleep(self.check_interval_seconds)
                    continue

                _force_check_cycle += 1
                # Skip DB query when no positions known; force re-check every 3rd cycle (~30s)
                # to detect newly opened positions. Reduces idle load from 6 queries/min → 2/min.
                if _last_known_count == 0 and _force_check_cycle % 3 != 0:
                    await asyncio.sleep(self.check_interval_seconds)
                    continue

                # Every ~2 min (12 * 10s) run adverse fill update + persist
                if self._cycle_count() % 12 == 0:
                    await self._run_adverse_fill_persistence()

                # Refresh adaptive exit multipliers periodically (timeout prevents blocking stop-losses)
                try:
                    await asyncio.wait_for(self._refresh_exit_learning(), timeout=5.0)
                except asyncio.TimeoutError:
                    logger.debug("_refresh_exit_learning timed out — using cached thresholds")

                async with self.db.get_session() as session:
                    from sqlalchemy import select

                    # Get all open positions (status 'open' or 'reserving')
                    result = await session.execute(
                        select(Position).where(Position.status.in_(["open", "reserving"]))
                    )
                    positions = result.scalars().all()
                    _last_known_count = len(positions)

                    # Session 46: Auto-close positions on expired markets (past end_date)
                    if positions:
                        positions = await self._close_expired_positions(session, positions)

                    # SESSION 44 FIX: Update current_price from market_prices before checking exits.
                    # Without this, current_price == entry_price forever, causing every SELL to lose
                    # (exit at stale entry_price minus slippage = guaranteed loss).
                    if positions:
                        await self._update_current_prices(session, positions)

                    for position in positions:
                        await self._check_position(position)

                await asyncio.sleep(self.check_interval_seconds)
            except Exception as e:
                logger.error(f"Error monitoring positions: {str(e)}", exc_info=True)
                await asyncio.sleep(self.check_interval_seconds)
    
    async def _close_expired_positions(self, session, positions: list) -> list:
        """Close positions on markets past end_date_iso. Returns remaining active positions.

        Session 46: Markets past their end_date are resolved (or about to be).
        Selling is pointless — resolution is free. Mark as closed in DB.
        """
        from sqlalchemy import text as sa_text

        market_ids = list({str(p.market_id) for p in positions if p.market_id})
        if not market_ids:
            return positions

        try:
            # Query end dates for all markets with open positions
            result = await session.execute(
                sa_text("""
                    SELECT id::text, end_date_iso FROM markets
                    WHERE id::text = ANY(:market_ids) AND end_date_iso IS NOT NULL
                """),
                {"market_ids": market_ids},
            )
            end_dates = {str(r[0]): r[1] for r in result.fetchall() if r[1]}
        except Exception as e:
            logger.debug("_close_expired_positions query failed (non-fatal): %s", e)
            return positions

        now = datetime.now(timezone.utc)
        active = []
        closed_count = 0
        for pos in positions:
            mid = str(pos.market_id)
            end_date = end_dates.get(mid)
            if end_date and hasattr(end_date, "tzinfo"):
                # Ensure tz-aware comparison
                if end_date.tzinfo is None:
                    end_date = end_date.replace(tzinfo=timezone.utc)
                if end_date < now:
                    pos.status = "closed"
                    closed_count += 1
                    logger.info(
                        "Auto-closed expired position %s (market %s expired %s)",
                        pos.id, mid, end_date.isoformat(),
                    )
                    continue
            active.append(pos)

        if closed_count > 0:
            try:
                await session.commit()
                logger.info("Closed %d positions on expired markets", closed_count)
            except Exception as e:
                logger.warning("Failed to commit expired position closures: %s", e)
                # Do NOT rollback here — rollback expires all ORM objects in the session,
                # causing MissingGreenlet when _check_position accesses position attributes.
                # The session context manager in _monitor_positions handles cleanup on exit.
                return positions  # Return all positions if commit failed

        return active

    async def _update_current_prices(self, session, positions: list) -> None:
        """Fetch latest market prices and update current_price + unrealized_pnl on open positions.

        Uses a single SQL query (DISTINCT ON per token) to get the most recent price
        for all open positions' token_ids. This prevents the stale-price bug where
        current_price == entry_price forever, causing every exit to be a guaranteed loss.

        Session 44 fix: root cause of 0% sell win rate.
        Session 51: also checks price age and alerts on staleness > 1 hour.
        """
        from sqlalchemy import text as sa_text
        from datetime import datetime, timezone

        # Collect all token_ids from open positions
        token_ids = list({str(p.token_id) for p in positions if p.token_id})
        if not token_ids:
            return

        try:
            # S156: Query market_prices_latest first (tiny table, O(1) per token).
            # For any tokens NOT found, fall back to time-bounded historical lookup.
            result = await session.execute(
                sa_text("""
                    SELECT token_id, price, timestamp
                    FROM market_prices_latest
                    WHERE token_id = ANY(:token_ids)
                """),
                {"token_ids": token_ids}
            )
            latest_prices = {}
            _stale_tokens = []
            _stale_threshold = 3600  # 1 hour
            _now = datetime.now(timezone.utc)
            for r in result.fetchall():
                if r[1] is not None and float(r[1]) > 0:
                    latest_prices[str(r[0])] = float(r[1])
                    if r[2] is not None:
                        try:
                            _age = (_now - r[2]).total_seconds()
                            if _age > _stale_threshold:
                                _stale_tokens.append((str(r[0])[:16], round(_age / 3600, 1)))
                        except Exception:
                            pass

            # S156: Fallback for tokens missing from latest table — time-bounded
            # lateral join on historical market_prices (capped at 7 days).
            _missing = [t for t in token_ids if t not in latest_prices]
            if _missing:
                try:
                    _fb_result = await session.execute(
                        sa_text("""
                            SELECT t.token_id, lp.price, lp.timestamp
                            FROM unnest(:miss_ids::text[]) AS t(token_id)
                            CROSS JOIN LATERAL (
                                SELECT price, timestamp
                                FROM market_prices mp
                                WHERE mp.token_id = t.token_id
                                  AND mp.timestamp > NOW() - INTERVAL '7 days'
                                ORDER BY mp.timestamp DESC
                                LIMIT 1
                            ) lp
                        """),
                        {"miss_ids": _missing}
                    )
                    _fb_count = 0
                    for r in _fb_result.fetchall():
                        if r[1] is not None and float(r[1]) > 0:
                            latest_prices[str(r[0])] = float(r[1])
                            _fb_count += 1
                    if _fb_count > 0:
                        logger.debug("price_fallback_historical", found=_fb_count, missed=len(_missing))
                except Exception as _fb_err:
                    logger.debug("price_fallback_failed", error=str(_fb_err))

            if not latest_prices:
                return

            updated = 0
            for pos in positions:
                tid = str(pos.token_id) if pos.token_id else ""
                if tid not in latest_prices:
                    continue
                new_price = latest_prices[tid]
                # Only update if price actually changed (avoid unnecessary DB writes)
                old_price = float(pos.current_price) if pos.current_price is not None else None
                if old_price is not None and abs(new_price - old_price) < 1e-6:
                    continue
                pos.current_price = new_price
                # Recompute unrealized P&L — raw price movement (Session 51 fix)
                # Costs (slippage, fees) are handled by trade decision code, not display P&L
                entry = float(pos.entry_price) if pos.entry_price else 0.5
                size = float(pos.size) if pos.size else 0.0
                pos.unrealized_pnl = (new_price - entry) * size
                updated += 1

            # Session 51: CLOB API fallback for positions not in market_prices (e.g. MirrorBot)
            _pm_client = getattr(self.execution_engine, "client", None) if self.execution_engine else None
            if _pm_client and hasattr(_pm_client, "get_orderbook"):
                _now_mono = time.monotonic()
                _API_COOLDOWN = 60  # seconds between API fetches per token
                for pos in positions:
                    tid = str(pos.token_id) if pos.token_id else ""
                    if not tid or tid in latest_prices:
                        continue  # already handled by market_prices
                    mid = str(pos.market_id) if pos.market_id else ""
                    # Throttle: skip if fetched recently
                    _cached = self._api_price_cache.get(tid)
                    if _cached and (_now_mono - _cached[1]) < _API_COOLDOWN:
                        _api_price = _cached[0]
                    else:
                        try:
                            _book = await _pm_client.get_orderbook(mid, tid)
                            _bids = _book.get("bids", []) if _book else []
                            _asks = _book.get("asks", []) if _book else []
                            if _bids and _asks:
                                _best_bid = float(_bids[0].get("price", 0))
                                _best_ask = float(_asks[0].get("price", 0))
                                if 0 < _best_bid <= _best_ask < 1 and (_best_ask - _best_bid) < 0.5:
                                    _api_price = (_best_bid + _best_ask) / 2
                                    self._api_price_cache[tid] = (_api_price, _now_mono)
                                else:
                                    continue
                            else:
                                continue
                        except Exception:
                            continue
                    # Update position price
                    old_price = float(pos.current_price) if pos.current_price is not None else None
                    if old_price is not None and abs(_api_price - old_price) < 1e-6:
                        continue
                    pos.current_price = _api_price
                    entry = float(pos.entry_price) if pos.entry_price else 0.5
                    size = float(pos.size) if pos.size else 0.0
                    pos.unrealized_pnl = (_api_price - entry) * size
                    updated += 1

            # Session 57: markets table fallback for CLOB tokens with wide spreads.
            # CLOB esports tokens have bid=$0.01/ask=$0.99 (spread > 0.5) so the
            # orderbook fallback skips them. The markets.yes_price/no_price columns
            # are refreshed by EsportsMarketService every 5 min via CLOB API.
            _still_missing = [p for p in positions
                              if str(p.token_id or "") not in latest_prices
                              and str(p.token_id or "") not in self._api_price_cache]
            if _still_missing:
                _missing_mids = list({str(p.market_id) for p in _still_missing if p.market_id})
                if _missing_mids:
                    try:
                        _mkt_result = await session.execute(
                            sa_text("""
                                SELECT id::text, yes_token_id, no_token_id, yes_price, no_price
                                FROM markets
                                WHERE id::text = ANY(:mids)
                                  AND yes_price IS NOT NULL
                            """),
                            {"mids": _missing_mids}
                        )
                        _mkt_prices = {}
                        for r in _mkt_result.fetchall():
                            if r[1] and r[3] is not None:
                                _mkt_prices[str(r[1])] = float(r[3])  # yes_token → yes_price
                            if r[2] and r[4] is not None:
                                _mkt_prices[str(r[2])] = float(r[4])  # no_token → no_price
                        for pos in _still_missing:
                            tid = str(pos.token_id) if pos.token_id else ""
                            if tid not in _mkt_prices:
                                continue
                            new_price = _mkt_prices[tid]
                            if new_price <= 0 or new_price >= 1:
                                continue  # skip resolved (0/1) prices
                            old_price = float(pos.current_price) if pos.current_price is not None else None
                            if old_price is not None and abs(new_price - old_price) < 1e-6:
                                continue
                            pos.current_price = new_price
                            entry = float(pos.entry_price) if pos.entry_price else 0.5
                            size = float(pos.size) if pos.size else 0.0
                            pos.unrealized_pnl = (new_price - entry) * size
                            updated += 1
                    except Exception:
                        pass

            if updated > 0:
                await session.commit()
                logger.debug("Updated current_price for %d/%d positions", updated, len(positions))

            # Session 51 P1-4: Alert on stale prices
            if _stale_tokens and self.alerting:
                try:
                    from base_engine.monitoring.alerting import AlertSeverity
                    await self.alerting.send_alert(
                        title=f"Stale prices for {len(_stale_tokens)} position(s)",
                        message=f"Oldest: {max(t[1] for t in _stale_tokens)}h. Tokens: {[t[0] for t in _stale_tokens[:5]]}",
                        severity=AlertSeverity.WARNING,
                        source="position_manager.price_staleness",
                        metadata={"stale_count": len(_stale_tokens)},
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.warning("current_price update failed (non-fatal): %s", e)
            # Do NOT rollback here — rollback expires all ORM objects in the session,
            # causing MissingGreenlet when _check_position accesses position attributes.
            # The session context manager in _monitor_positions handles cleanup on exit.

    async def _check_position(self, position: Position):
        """Check a single position for stop-loss/take-profit, model reversal, and edge depletion.

        Session 45: Uses ExitStrategy for dynamic, cost-aware thresholds (replaces fixed 30%/60%).
        When PM_COST_AWARE_EXITS=false (kill switch), falls back to static defaults.
        """
        # S125: Bot exclusion — bots with their own exit logic opt out of PM exits
        _bot = getattr(position, "bot_id", None) or getattr(position, "bot_name", None) or ""
        if _bot in getattr(settings, "PM_EXCLUDE_BOTS", []):
            return

        if not position.current_price:
            position.current_price = position.entry_price
        entry_price = float(position.entry_price or 0.5)
        current_price = float(position.current_price or entry_price)
        size = float(position.size or 0.0)

        # --- Compute dynamic exit params (cached per market, 5-min TTL) ---
        try:
            params = await asyncio.wait_for(
                self.exit_strategy.compute_exit_params(position), timeout=3.0
            )
        except Exception:
            params = ExitParams()  # Fall back to static defaults on any error

        # --- Cost-aware P&L ---
        # Deduct entry cost + estimated exit cost for realistic profit/loss
        _entry_cost = params.entry_cost
        _est_exit_cost = params.est_exit_cost
        raw_pnl = (current_price - entry_price) * size
        cost_adjusted_pnl = raw_pnl - (_entry_cost + _est_exit_cost)
        cost_pnl_pct = cost_adjusted_pnl / (entry_price * size) if (entry_price * size) > 0 else 0.0
        # Raw P&L pct (for backwards compat with adaptive learning and logging)
        raw_pnl_pct = (current_price - entry_price) / entry_price if entry_price > 0 else 0.0

        # --- Grace period: only gates model reversal, not stop-loss/take-profit ---
        _GRACE_PERIOD_SECONDS = 1200  # 20 minutes
        _in_grace_period = False
        opened_at = getattr(position, "opened_at", None) or getattr(position, "created_at", None)
        if opened_at:
            try:
                age_seconds = (datetime.now(timezone.utc) - opened_at).total_seconds()
                if age_seconds < _GRACE_PERIOD_SECONDS:
                    _in_grace_period = True
            except Exception:
                # Timestamp parse failed — safer to assume grace period than exit early
                _in_grace_period = True
        else:
            # No timestamp — assume grace period to prevent premature model-reversal exits
            _in_grace_period = True

        # --- Model reversal: cost-gated (Session 45) ---
        # Only exit on reversal if: (a) strong conviction, (b) price above breakeven, or (c) deep real loss
        _pe = getattr(self, "prediction_engine", None)
        if (
            not _in_grace_period
            and _pe is not None
            and getattr(_pe, "initialized", False)
            and getattr(_pe, "_feature_cache_warmed", False)
        ):
            try:
                _MODEL_REVERSAL_THRESHOLD = float(getattr(settings, "MODEL_REVERSAL_THRESHOLD", 0.45))
                _token_id = getattr(position, "token_id", "") or ""
                _pred = await asyncio.wait_for(
                    _pe.predict(
                        market_id=str(position.market_id),
                        token_id=_token_id,
                        price=current_price,
                    ),
                    timeout=2.0,
                )
                if _pred and isinstance(_pred, dict):
                    _prob = float(_pred.get("prediction", 0.5))
                    _side = (position.side or "").upper()
                    _is_reversal = False

                    if _side in ("YES", "BUY") and _prob < _MODEL_REVERSAL_THRESHOLD:
                        _is_reversal = True
                    elif _side == "NO" and _prob > (1.0 - _MODEL_REVERSAL_THRESHOLD):
                        _is_reversal = True

                    if _is_reversal:
                        # Session 45 cost-gated reversal logic:
                        # 1) Near resolution & in-the-money → HOLD (free settlement)
                        if params.hold_to_resolution:
                            logger.info(
                                "Near resolution, holding to settlement (free exit): pos=%s prob=%.3f hrs=%.1f",
                                position.id, _prob, params.hours_to_resolution or 0,
                            )
                        # 2) Above breakeven → exit is profitable, take it
                        elif current_price >= params.breakeven_price and params.breakeven_price > 0:
                            await self._execute_exit(position, f"reversal_profitable (prob={_prob:.3f})")
                            return
                        # 3) Strong reversal (below strong threshold) → forced exit
                        elif _prob < params.strong_reversal_threshold or (
                            _side == "NO" and _prob > (1.0 - params.strong_reversal_threshold)
                        ):
                            await self._execute_exit(position, f"reversal_strong (prob={_prob:.3f})")
                            return
                        # 4) Cost floor breached → real loss exceeds holding cost
                        elif cost_adjusted_pnl < params.min_exit_pnl:
                            await self._execute_exit(position, f"reversal_cost_stop (pnl=${cost_adjusted_pnl:.2f})")
                            return
                        # 5) Weak reversal in cost trap → HOLD (let stop-loss or resolution handle it)
                        else:
                            logger.info(
                                "Weak reversal blocked (cost trap): pos=%s prob=%.3f cost_pnl=$%.2f breakeven=%.3f",
                                position.id, _prob, cost_adjusted_pnl, params.breakeven_price,
                            )
            except asyncio.TimeoutError:
                pass
            except Exception:
                pass

        # --- Dynamic stop-loss / take-profit (replaces fixed 30%/60%) ---
        # Adaptive learning multiplier still applied on top of dynamic thresholds
        _mid = str(getattr(position, "market_id", ""))
        _mult = self._market_exit_mult.get(_mid, 1.0)
        _eff_stop = params.stop_loss_pct * _mult
        _eff_take = params.take_profit_pct / _mult

        if cost_pnl_pct <= -_eff_stop:
            await self._execute_stop_loss(position, cost_pnl_pct)
        elif cost_pnl_pct >= _eff_take:
            await self._execute_take_profit(position, cost_pnl_pct)
    
    async def _execute_exit(self, position: Position, reason: str) -> None:
        """Exit position (model reversal or edge depleted). Cooldown prevents spam."""
        # Cooldown: don't retry exit if it failed recently
        now = time.monotonic()
        cooldown_until = self._exit_cooldowns.get(position.id, 0)
        if now < cooldown_until:
            return  # Still in cooldown, skip silently
        # P3-3: Prune expired cooldown entries to prevent unbounded dict growth (memory leak).
        # Keep only entries that are still within 2× the cooldown window.
        _prune_threshold = now - 2 * _EXIT_RETRY_COOLDOWN
        _stale_ids = [pid for pid, until in self._exit_cooldowns.items() if until < _prune_threshold]
        for pid in _stale_ids:
            del self._exit_cooldowns[pid]
        # Theoretical hardening: skip zero-size positions (avoids fee-only SELL with no tokens)
        _exit_size = position.size or 0
        if _exit_size <= 0:
            logger.warning("Skipping exit for position %s: size=%.6f (zero/negative)", position.id, _exit_size)
            return
        logger.info("Executing exit for position %s (%s)", position.id, reason, market_id=position.market_id)
        try:
            # ALL exits are SELL — you sell the token you hold (YES or NO).
            # Polymarket: YES/NO are BUY operations; SELL means closing position.
            exit_side = "SELL"
            place = (self.order_gateway or self.execution_engine).place_order
            result = await place(
                bot_name=position.bot_id or getattr(position, "bot_name", None) or "default",
                market_id=position.market_id,
                token_id=position.token_id or "",
                side=exit_side,
                size=_exit_size,
                price=position.current_price or position.entry_price or 0.5,
                confidence=0.0,
            )
            if result.get("success") and self.db.session_factory:
                async with self.db.get_session() as session:
                    from sqlalchemy import select
                    r = await session.execute(select(Position).where(Position.id == position.id).with_for_update())
                    pos = r.scalar_one_or_none()
                    if pos:
                        pos.status = "closed"
                        # Compute actual P&L from exit price vs entry price, less taker fee
                        exit_price = position.current_price or position.entry_price or 0.5
                        entry_price = position.entry_price or 0.5
                        _taker_fee_rate = getattr(settings, "TAKER_FEE_BPS", 150) / 10000.0
                        _exit_fee = _taker_fee_rate * _exit_size * exit_price
                        pos.unrealized_pnl = (exit_price - entry_price) * _exit_size - _exit_fee
                        await session.commit()
                logger.info("Exit executed for position %s (pnl=%.4f)", position.id,
                            (position.current_price or 0.5) - (position.entry_price or 0.5))
                # Record outcome for consecutive loss tracking (model reversal can be win or loss)
                if self.risk_manager:
                    _bot = getattr(position, "bot_id", None) or getattr(position, "bot_name", None)
                    _realized = (exit_price - entry_price) * _exit_size - _exit_fee
                    if _bot:
                        self.risk_manager.record_trade_outcome(_bot, was_profitable=(_realized > 0))
                # Clear cooldown on success
                self._exit_cooldowns.pop(position.id, None)
            else:
                err_msg = result.get("error", "")
                # Ghost position: DB says open but paper engine has no position → close in DB
                if "Insufficient position" in str(err_msg):
                    logger.warning(
                        "Ghost position %s (no paper position) — marking closed in DB",
                        position.id, market_id=position.market_id,
                    )
                    if self.db.session_factory:
                        async with self.db.get_session() as session:
                            from sqlalchemy import select as sel
                            r = await session.execute(sel(Position).where(Position.id == position.id).with_for_update())
                            pos = r.scalar_one_or_none()
                            if pos:
                                pos.status = "closed"
                                pos.unrealized_pnl = 0.0
                                await session.commit()
                    self._exit_cooldowns.pop(position.id, None)
                else:
                    # Other failure — set cooldown to prevent spam
                    self._exit_cooldowns[position.id] = time.monotonic() + _EXIT_RETRY_COOLDOWN
                    logger.debug("Exit failed for position %s, cooldown %ds", position.id, _EXIT_RETRY_COOLDOWN)
        except Exception as e:
            self._exit_cooldowns[position.id] = time.monotonic() + _EXIT_RETRY_COOLDOWN
            logger.error("Exit failed for position %s: %s", position.id, e, exc_info=True)

    async def _execute_stop_loss(self, position: Position, pnl_pct: float):
        """Execute stop-loss for a position."""
        # Cooldown: don't retry if recently failed (same pattern as _execute_exit)
        now = time.monotonic()
        cooldown_until = self._exit_cooldowns.get(position.id, 0)
        if now < cooldown_until:
            return  # Still in cooldown, skip silently
        # Skip zero-size positions (avoids fee-only SELL with no tokens)
        _exit_size = position.size or 0
        if _exit_size <= 0:
            logger.warning("Skipping stop-loss for position %s: size=%.6f (zero/negative)", position.id, _exit_size)
            return
        logger.info(
            f"Executing stop-loss for position {position.id}",
            pnl_pct=pnl_pct,
            market_id=position.market_id
        )

        try:
            # ALL exits are SELL — selling the token you hold (YES or NO)
            exit_side = "SELL"

            place = (self.order_gateway or self.execution_engine).place_order
            result = await place(
                bot_name=position.bot_id or position.bot_name,
                market_id=position.market_id,
                token_id=position.token_id,
                side=exit_side,
                size=_exit_size,
                price=position.current_price or position.entry_price,
                confidence=1.0
            )

            if result.get("success"):
                # Mark position as closed (re-select in session so update is persisted)
                if self.db.session_factory:
                    async with self.db.get_session() as session:
                        from sqlalchemy import select
                        r = await session.execute(select(Position).where(Position.id == position.id).with_for_update())
                        pos = r.scalar_one_or_none()
                        if pos:
                            pos.status = "closed"
                            # P&L = (exit_price - entry_price) × size - exit_fee
                            # Same formula as _execute_exit — correct for both YES and NO tokens.
                            _exit_price = float(position.current_price or position.entry_price or 0.5)
                            _entry_price = float(position.entry_price or 0.5)
                            _size = float(_exit_size)
                            _taker_fee_rate = getattr(settings, "TAKER_FEE_BPS", 150) / 10000.0
                            _exit_fee = _taker_fee_rate * _size * _exit_price
                            pos.unrealized_pnl = (_exit_price - _entry_price) * _size - _exit_fee
                            await session.commit()
                logger.info("Stop-loss executed for position %s", position.id)
                # Record outcome for consecutive loss tracking (stop-loss = loss)
                if self.risk_manager:
                    _bot = getattr(position, "bot_id", None) or getattr(position, "bot_name", None)
                    if _bot:
                        self.risk_manager.record_trade_outcome(_bot, was_profitable=(pnl_pct > 0))
                # Clear cooldown on success
                self._exit_cooldowns.pop(position.id, None)
            else:
                err_msg = result.get("error", "")
                # Ghost position: DB says open but paper engine has no position → close in DB
                if "Insufficient position" in str(err_msg):
                    logger.warning(
                        "Ghost position %s (stop-loss, no paper position) — marking closed in DB",
                        position.id, market_id=position.market_id,
                    )
                    if self.db.session_factory:
                        async with self.db.get_session() as session:
                            from sqlalchemy import select as sel
                            r = await session.execute(sel(Position).where(Position.id == position.id).with_for_update())
                            pos = r.scalar_one_or_none()
                            if pos:
                                pos.status = "closed"
                                pos.unrealized_pnl = 0.0
                                await session.commit()
                    self._exit_cooldowns.pop(position.id, None)
                else:
                    # Other failure — set cooldown to prevent spam
                    self._exit_cooldowns[position.id] = time.monotonic() + _EXIT_RETRY_COOLDOWN
                    logger.debug("Stop-loss failed for position %s, cooldown %ds", position.id, _EXIT_RETRY_COOLDOWN)
        except Exception as e:
            self._exit_cooldowns[position.id] = time.monotonic() + _EXIT_RETRY_COOLDOWN
            logger.error("Error executing stop-loss: %s", e, exc_info=True)

    async def _execute_take_profit(self, position: Position, pnl_pct: float):
        """Execute take-profit for a position."""
        # Cooldown: don't retry if recently failed (same pattern as _execute_exit)
        now = time.monotonic()
        cooldown_until = self._exit_cooldowns.get(position.id, 0)
        if now < cooldown_until:
            return  # Still in cooldown, skip silently
        # Skip zero-size positions (avoids fee-only SELL with no tokens)
        _exit_size = position.size or 0
        if _exit_size <= 0:
            logger.warning("Skipping take-profit for position %s: size=%.6f (zero/negative)", position.id, _exit_size)
            return
        logger.info(
            f"Executing take-profit for position {position.id}",
            pnl_pct=pnl_pct,
            market_id=position.market_id
        )

        try:
            # ALL exits are SELL — selling the token you hold (YES or NO)
            exit_side = "SELL"

            place = (self.order_gateway or self.execution_engine).place_order
            result = await place(
                bot_name=position.bot_id or position.bot_name,
                market_id=position.market_id,
                token_id=position.token_id,
                side=exit_side,
                size=_exit_size,
                price=position.current_price or position.entry_price,
                confidence=1.0
            )

            if result.get("success"):
                # Mark position as closed (re-select in session so update is persisted)
                if self.db.session_factory:
                    async with self.db.get_session() as session:
                        from sqlalchemy import select
                        r = await session.execute(select(Position).where(Position.id == position.id).with_for_update())
                        pos = r.scalar_one_or_none()
                        if pos:
                            pos.status = "closed"
                            # P&L = (exit_price - entry_price) × size - exit_fee
                            # Same formula as _execute_exit — correct for both YES and NO tokens.
                            _exit_price = float(position.current_price or position.entry_price or 0.5)
                            _entry_price = float(position.entry_price or 0.5)
                            _size = float(_exit_size)
                            _taker_fee_rate = getattr(settings, "TAKER_FEE_BPS", 150) / 10000.0
                            _exit_fee = _taker_fee_rate * _size * _exit_price
                            pos.unrealized_pnl = (_exit_price - _entry_price) * _size - _exit_fee
                            await session.commit()
                logger.info("Take-profit executed for position %s", position.id)
                # Record outcome for consecutive loss tracking (take-profit = win)
                if self.risk_manager:
                    _bot = getattr(position, "bot_id", None) or getattr(position, "bot_name", None)
                    if _bot:
                        self.risk_manager.record_trade_outcome(_bot, was_profitable=(pnl_pct > 0))
                # Clear cooldown on success
                self._exit_cooldowns.pop(position.id, None)
            else:
                err_msg = result.get("error", "")
                # Ghost position: DB says open but paper engine has no position → close in DB
                if "Insufficient position" in str(err_msg):
                    logger.warning(
                        "Ghost position %s (take-profit, no paper position) — marking closed in DB",
                        position.id, market_id=position.market_id,
                    )
                    if self.db.session_factory:
                        async with self.db.get_session() as session:
                            from sqlalchemy import select as sel
                            r = await session.execute(sel(Position).where(Position.id == position.id).with_for_update())
                            pos = r.scalar_one_or_none()
                            if pos:
                                pos.status = "closed"
                                pos.unrealized_pnl = 0.0
                                await session.commit()
                    self._exit_cooldowns.pop(position.id, None)
                else:
                    # Other failure — set cooldown to prevent spam
                    self._exit_cooldowns[position.id] = time.monotonic() + _EXIT_RETRY_COOLDOWN
                    logger.debug("Take-profit failed for position %s, cooldown %ds", position.id, _EXIT_RETRY_COOLDOWN)
        except Exception as e:
            self._exit_cooldowns[position.id] = time.monotonic() + _EXIT_RETRY_COOLDOWN
            logger.error("Error executing take-profit: %s", e, exc_info=True)

    async def set_position_limits(
        self,
        position_id: int,
        stop_loss_pct: Optional[float] = None,
        take_profit_pct: Optional[float] = None
    ) -> bool:
        """
        Set custom stop-loss/take-profit for a position.
        
        Args:
            position_id: Position ID
            stop_loss_pct: Stop-loss percentage (e.g., 0.10 for 10%)
            take_profit_pct: Take-profit percentage (e.g., 0.20 for 20%)
        
        Returns:
            True if successful
        """
        if not self.db.session_factory:
            return False
        
        async with self.db.get_session() as session:
            from sqlalchemy import select
            
            result = await session.execute(
                select(Position).where(Position.id == position_id)
            )
            position = result.scalar_one_or_none()
            
            if not position:
                return False
            
            # Store limits in metadata (would need to add metadata field to Position model)
            # For now, use a separate tracking mechanism
            logger.info(
                f"Position limits set for {position_id}",
                stop_loss=stop_loss_pct,
                take_profit=take_profit_pct
            )
            
            return True
