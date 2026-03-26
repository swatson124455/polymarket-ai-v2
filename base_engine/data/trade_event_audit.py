"""
Trade event P&L integrity audit.

Runs 5 checks against trade_events to detect impossible states.
Called after resolution backfill (every 30 min). Read-only — never writes.
"""
import structlog

logger = structlog.get_logger(__name__)


async def audit_trade_events(db) -> dict:
    """Scan trade_events for impossible states. Returns violation counts."""
    if db.session_factory is None:
        return {}

    result = {}
    try:
        from sqlalchemy import text
        async with db.get_session() as session:
            # Check 1-3: Size invariant violations (single query)
            # EXIT + RESOLUTION size must never exceed ENTRY size
            rows = await session.execute(text("""
                SELECT market_id, bot_name,
                    SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS entry_sz,
                    SUM(CASE WHEN event_type = 'EXIT' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS exit_sz,
                    SUM(CASE WHEN event_type = 'RESOLUTION' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS res_sz
                FROM trade_events
                WHERE event_type IN ('ENTRY', 'EXIT', 'RESOLUTION')
                GROUP BY market_id, bot_name
                HAVING
                    SUM(CASE WHEN event_type IN ('EXIT', 'RESOLUTION') THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
                    > SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) * 1.001
                    OR SUM(CASE WHEN event_type = 'RESOLUTION' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
                       > SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) * 1.001
                    OR SUM(CASE WHEN event_type = 'EXIT' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
                       > SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) * 1.001
            """))
            size_violations = rows.fetchall()
            result["size_violations"] = len(size_violations)
            for v in size_violations:
                logger.warning(
                    "trade_event_audit_size_violation",
                    market_id=str(v[0])[:20],
                    bot_name=v[1],
                    entry_size=float(v[2]),
                    exit_size=float(v[3]),
                    resolution_size=float(v[4]),
                    disposal=float(v[3]) + float(v[4]),
                )

            # Check 4: Orphan RESOLUTION with no ENTRY
            rows2 = await session.execute(text("""
                SELECT te.market_id, te.bot_name, te.side,
                       CAST(te.size AS DOUBLE PRECISION), CAST(te.realized_pnl AS DOUBLE PRECISION)
                FROM trade_events te
                WHERE te.event_type = 'RESOLUTION'
                  AND NOT EXISTS (
                      SELECT 1 FROM trade_events te2
                      WHERE te2.market_id = te.market_id
                        AND te2.bot_name = te.bot_name
                        AND te2.event_type = 'ENTRY'
                  )
            """))
            orphans = rows2.fetchall()
            result["orphan_resolutions"] = len(orphans)
            for o in orphans:
                logger.warning(
                    "trade_event_audit_orphan_resolution",
                    market_id=str(o[0])[:20],
                    bot_name=o[1],
                    side=o[2],
                    size=float(o[3]) if o[3] else 0,
                    pnl=float(o[4]) if o[4] else 0,
                )

            # Check 5: Negative sizes
            rows3 = await session.execute(text("""
                SELECT event_type, market_id, bot_name, CAST(size AS DOUBLE PRECISION)
                FROM trade_events
                WHERE CAST(size AS DOUBLE PRECISION) < 0
                LIMIT 10
            """))
            negatives = rows3.fetchall()
            result["negative_sizes"] = len(negatives)
            for n in negatives:
                logger.warning(
                    "trade_event_audit_negative_size",
                    event_type=n[0],
                    market_id=str(n[1])[:20],
                    bot_name=n[2],
                    size=float(n[3]),
                )

    except Exception as e:
        logger.debug("trade_event_audit failed (non-fatal): %s", e)
        result["error"] = str(e)

    total = sum(v for k, v in result.items() if isinstance(v, int))
    if total > 0:
        logger.warning("trade_event_audit_complete", total_violations=total, **result)
    else:
        logger.info("trade_event_audit_clean", total_violations=0)

    return result
