"""
Orphan cleanup: remove trades (and optionally market_prices) that reference missing markets.
Phase 3 (5.2). Used by scripts/orphan_cleanup.py and optionally IngestionScheduler.
"""
from typing import Any, Dict

from sqlalchemy import text
from structlog import get_logger

logger = get_logger()


async def run_orphan_cleanup(db, dry_run: bool = False, cleanup_prices: bool = False) -> Dict[str, Any]:
    """
    Delete trades with market_id not in markets; optionally delete market_prices for missing markets.
    Returns dict with deleted_trades, deleted_prices, dry_run.
    """
    if not db or not getattr(db, "session_factory", None):
        return {"error": "no_db", "deleted_trades": 0, "deleted_prices": 0, "dry_run": dry_run}
    out = {"deleted_trades": 0, "deleted_prices": 0, "dry_run": dry_run}
    async with db.get_session() as session:
        # trades.market_id stores condition_id (hex hash) OR numeric id as string
        r = await session.execute(text("""
            SELECT COUNT(*)::int FROM trades t
            WHERE t.market_id IS NOT NULL AND t.market_id != ''
            AND NOT EXISTS (
                SELECT 1 FROM markets m
                WHERE m.id::text = t.market_id OR m.condition_id = t.market_id
            )
        """))
        orphan_trades = (r.scalar() or 0) if r else 0
        if orphan_trades > 0:
            if not dry_run:
                del_r = await session.execute(text("""
                    DELETE FROM trades WHERE market_id IS NOT NULL AND market_id != ''
                    AND NOT EXISTS (
                        SELECT 1 FROM markets m
                        WHERE m.id::text = trades.market_id OR m.condition_id = trades.market_id
                    )
                """))
                out["deleted_trades"] = getattr(del_r, "rowcount", 0) or orphan_trades
            else:
                out["deleted_trades"] = orphan_trades
            logger.info("Orphan cleanup: %s trades (dry_run=%s)", out["deleted_trades"], dry_run)
        if cleanup_prices:
            r2 = await session.execute(text("""
                SELECT COUNT(*)::bigint FROM market_prices mp
                WHERE NOT EXISTS (SELECT 1 FROM markets m WHERE m.id = mp.market_id)
            """))
            orphan_prices = (r2.scalar() or 0) if r2 else 0
            if orphan_prices > 0:
                if not dry_run:
                    del_p = await session.execute(text("""
                        DELETE FROM market_prices mp
                        WHERE NOT EXISTS (SELECT 1 FROM markets m WHERE m.id = mp.market_id)
                    """))
                    out["deleted_prices"] = int(getattr(del_p, "rowcount", 0) or orphan_prices)
                else:
                    out["deleted_prices"] = int(orphan_prices)
                logger.info("Orphan cleanup: %s prices (dry_run=%s)", out["deleted_prices"], dry_run)
        if not dry_run and (out["deleted_trades"] or out["deleted_prices"]):
            await session.commit()
    return out
