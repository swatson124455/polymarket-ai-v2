"""
Post-stage canary queries for pipeline sanity checks.
Run after markets, trades, prices, or resolution backfill to verify counts and freshness.
Phase 3: close loop on pipeline reliability.
"""
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import text
from structlog import get_logger

logger = get_logger()


async def run_canary_after_markets(db) -> Dict[str, Any]:
    """Run after market ingest: count markets, count with token IDs, latest updated_at."""
    if not db or not getattr(db, "session_factory", None):
        return {"status": "skipped", "reason": "no_db"}
    try:
        async with db.get_session() as session:
            r = await session.execute(text("""
                SELECT
                    COUNT(*)::int AS total,
                    COUNT(*) FILTER (WHERE yes_token_id IS NOT NULL AND yes_token_id != '')::int AS with_tokens,
                    MAX(updated_at) AS latest_updated
                FROM markets
            """))
            row = r.one_or_none()
            if not row:
                return {"status": "ok", "markets_total": 0, "markets_with_tokens": 0}
            total, with_tokens, latest = row[0] or 0, row[1] or 0, row[2]
            out = {
                "status": "ok",
                "markets_total": total,
                "markets_with_tokens": with_tokens,
                "latest_updated": str(latest) if latest else None,
            }
            if total == 0:
                logger.warning("Canary (markets): zero markets in DB")
            else:
                logger.debug("Canary (markets): total=%s with_tokens=%s latest=%s", total, with_tokens, latest)
            return out
    except Exception as e:
        logger.warning("Canary (markets) failed: %s", e)
        return {"status": "error", "error": str(e)}


async def run_canary_after_trades(db) -> Dict[str, Any]:
    """Run after trade ingest: count trades, latest timestamp."""
    if not db or not getattr(db, "session_factory", None):
        return {"status": "skipped", "reason": "no_db"}
    try:
        async with db.get_session() as session:
            r = await session.execute(text("""
                SELECT COUNT(*)::int AS total, MAX(COALESCE(entry_time, timestamp)) AS latest
                FROM trades
            """))
            row = r.one_or_none()
            if not row:
                return {"status": "ok", "trades_total": 0}
            total, latest = row[0] or 0, row[1]
            out = {"status": "ok", "trades_total": total, "latest_trade": str(latest) if latest else None}
            logger.debug("Canary (trades): total=%s latest=%s", total, latest)
            return out
    except Exception as e:
        logger.warning("Canary (trades) failed: %s", e)
        return {"status": "error", "error": str(e)}


async def run_canary_after_prices(db) -> Dict[str, Any]:
    """Run after price ingest: count market_prices, latest timestamp."""
    if not db or not getattr(db, "session_factory", None):
        return {"status": "skipped", "reason": "no_db"}
    try:
        async with db.get_session() as session:
            r = await session.execute(text("""
                SELECT COUNT(*)::bigint AS total, MAX(timestamp) AS latest FROM market_prices
            """))
            row = r.one_or_none()
            if not row:
                return {"status": "ok", "prices_total": 0}
            total, latest = row[0] or 0, row[1]
            out = {"status": "ok", "prices_total": int(total), "latest_price": str(latest) if latest else None}
            logger.debug("Canary (prices): total=%s latest=%s", total, latest)
            return out
    except Exception as e:
        logger.warning("Canary (prices) failed: %s", e)
        return {"status": "error", "error": str(e)}


async def run_canary_after_resolution_backfill(db) -> Dict[str, Any]:
    """Run after resolution backfill: count resolved markets, prediction_log updated."""
    if not db or not getattr(db, "session_factory", None):
        return {"status": "skipped", "reason": "no_db"}
    try:
        async with db.get_session() as session:
            r = await session.execute(text("""
                SELECT COUNT(*)::int FROM markets
                WHERE resolution IN ('YES', 'NO')
            """))
            resolved = (r.scalar() or 0) if r else 0
            try:
                r2 = await session.execute(text("""
                    SELECT COUNT(*)::int FROM prediction_log WHERE resolution IN ('YES', 'NO')
                """))
                pred_resolved = (r2.scalar() or 0) if r2 else 0
            except Exception:
                pred_resolved = 0
            out = {
                "status": "ok",
                "markets_resolved": resolved,
                "prediction_log_resolved": pred_resolved,
            }
            logger.debug("Canary (resolution): markets_resolved=%s prediction_log=%s", resolved, pred_resolved)
            return out
    except Exception as e:
        logger.warning("Canary (resolution) failed: %s", e)
        return {"status": "error", "error": str(e)}


async def run_all_canaries(db, stages: Optional[list] = None) -> Dict[str, Dict[str, Any]]:
    """Run canaries for given stages (default: markets, trades, prices, resolution)."""
    stages = stages or ["markets", "trades", "prices", "resolution"]
    fns = {
        "markets": run_canary_after_markets,
        "trades": run_canary_after_trades,
        "prices": run_canary_after_prices,
        "resolution": run_canary_after_resolution_backfill,
    }
    results = {}
    for stage in stages:
        if stage in fns:
            results[stage] = await fns[stage](db)
    return results
