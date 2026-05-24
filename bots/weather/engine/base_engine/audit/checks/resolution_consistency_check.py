"""
Check 4C: Resolution consistency across markets, trade_events, and paper_trades.

Three sub-checks:
1. markets.resolved=TRUE but no RESOLUTION event in trade_events for any bot
   that held a position → WARNING (resolution backfill may be broken)
2. RESOLUTION event in trade_events but markets.resolved=FALSE or markets row missing
   → CRITICAL (ghost resolution — money recorded for unresolved market)
3. paper_trades with outcome set but markets.resolved=FALSE
   → WARNING (paper trade marked resolved before market resolved)
"""
import time
from typing import List

from sqlalchemy import text

from bots.weather.engine.base_engine.audit.check_result import AuditViolation, CheckResult
from bots.weather.engine.base_engine.audit.checks.base_check import BaseCheck


class ResolutionConsistencyCheck(BaseCheck):
    name = "resolution_consistency"
    tables_queried = ["markets", "trade_events", "paper_trades"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # RESOLUTION event for markets.resolved=FALSE (ghost resolution)
        ghost_rows = await session.execute(text("""
            SELECT te.bot_name, te.market_id, te.side,
                   CAST(te.realized_pnl AS DOUBLE PRECISION) AS pnl,
                   m.resolved, te.sequence_num
            FROM trade_events te
            LEFT JOIN markets m ON m.id = te.market_id
            WHERE te.event_type = 'RESOLUTION'
              AND (m.resolved IS NULL OR m.resolved = FALSE OR m.id IS NULL)
            LIMIT 200
        """))
        for row in ghost_rows.fetchall():
            bot_name, market_id, side, pnl, resolved, seq = row
            violations.append(AuditViolation(
                recon_type="RESOLUTION_INCONSISTENCY",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "reason": "resolution_event_for_unresolved_market",
                    "side": side,
                    "realized_pnl": round(pnl, 4) if pnl else 0,
                    "market_resolved": bool(resolved) if resolved is not None else None,
                    "sequence_num": seq,
                },
            ))

        # markets.resolved=TRUE but bot that had ENTRY has no RESOLUTION event
        missing_res = await session.execute(text("""
            SELECT te_entry.bot_name, te_entry.market_id,
                   COUNT(*) AS entry_count, m.resolved_at
            FROM trade_events te_entry
            JOIN markets m ON m.id = te_entry.market_id
            WHERE te_entry.event_type = 'ENTRY'
              AND m.resolved = TRUE
              AND m.resolved_at IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1 FROM trade_events te_res
                  WHERE te_res.bot_name  = te_entry.bot_name
                    AND te_res.market_id = te_entry.market_id
                    AND te_res.event_type = 'RESOLUTION'
              )
            GROUP BY te_entry.bot_name, te_entry.market_id, m.resolved_at
            LIMIT 200
        """))
        for row in missing_res.fetchall():
            bot_name, market_id, count, resolved_at = row
            violations.append(AuditViolation(
                recon_type="RESOLUTION_INCONSISTENCY",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "missing_resolution_event_for_resolved_market",
                    "entry_count": int(count),
                    "resolved_at": str(resolved_at) if resolved_at else None,
                },
            ))

        # paper_trades outcome set but market not resolved
        pt_rows = await session.execute(text("""
            SELECT pt.bot_name, pt.market_id, pt.resolution,
                   COUNT(*) AS trade_count
            FROM paper_trades pt
            LEFT JOIN markets m ON m.id = pt.market_id
            WHERE pt.resolution IS NOT NULL
              AND (m.resolved IS NULL OR m.resolved = FALSE)
            GROUP BY pt.bot_name, pt.market_id, pt.resolution
            LIMIT 100
        """))
        for row in pt_rows.fetchall():
            bot_name, market_id, resolution, count = row
            violations.append(AuditViolation(
                recon_type="RESOLUTION_INCONSISTENCY",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "paper_trade_resolved_before_market",
                    "resolution": resolution,
                    "trade_count": int(count),
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} resolution consistency violation(s)",
        )
