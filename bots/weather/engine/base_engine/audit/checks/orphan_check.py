"""
Check 2B: Orphan RESOLUTION events (no matching ENTRY).
"""
import time
from decimal import Decimal
from typing import List

from sqlalchemy import text

from bots.weather.engine.base_engine.audit.check_result import AuditViolation, CheckResult
from bots.weather.engine.base_engine.audit.checks.base_check import BaseCheck


class OrphanCheck(BaseCheck):
    name = "orphan_resolution"
    tables_queried = ["trade_events"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        rows = await session.execute(text("""
            SELECT te.market_id, te.bot_name, te.side,
                   CAST(te.size AS DOUBLE PRECISION),
                   CAST(te.realized_pnl AS DOUBLE PRECISION),
                   te.sequence_num
            FROM trade_events te
            WHERE te.event_type = 'RESOLUTION'
              AND NOT EXISTS (
                  SELECT 1 FROM trade_events te2
                  WHERE te2.market_id = te.market_id
                    AND te2.bot_name  = te.bot_name
                    AND te2.event_type = 'ENTRY'
              )
            LIMIT 200
        """))
        for row in rows.fetchall():
            market_id, bot_name, side, size, pnl, seq = row
            violations.append(AuditViolation(
                recon_type="ORPHAN_RESOLUTION",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "side": side,
                    "size": round(size, 6) if size else 0,
                    "pnl": round(pnl, 4) if pnl else 0,
                    "resolution_seq": seq,
                },
                internal_value=Decimal(str(round(pnl, 4))) if pnl else None,
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} orphan RESOLUTION event(s)",
        )
