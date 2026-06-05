"""
Check 2C: Temporal ordering — EXIT/RESOLUTION must not precede ENTRY by >5 seconds.
5-second tolerance absorbs legitimate sub-second clock skew between API timestamps
and bot system clock without masking genuine ordering bugs.
S164: Removed side from JOIN — historical EXIT events used side='SELL' while
ENTRYs used YES/NO, causing missed matches.
"""
import time
from typing import List

from sqlalchemy import text

from bots.weather.engine.base_engine.audit.check_result import AuditViolation, CheckResult
from bots.weather.engine.base_engine.audit.checks.base_check import BaseCheck


class TemporalOrderCheck(BaseCheck):
    name = "temporal_order"
    tables_queried = ["trade_events"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        rows = await session.execute(text("""
            WITH first_entry AS (
                SELECT bot_name, market_id,
                       MIN(event_time) AS first_entry_time
                FROM trade_events
                WHERE event_type = 'ENTRY'
                GROUP BY bot_name, market_id
            )
            SELECT te.event_type, te.market_id, te.bot_name, te.side,
                   te.event_time, fe.first_entry_time, te.sequence_num,
                   EXTRACT(EPOCH FROM (fe.first_entry_time - te.event_time)) AS skew_seconds
            FROM trade_events te
            JOIN first_entry fe
              ON fe.bot_name  = te.bot_name
             AND fe.market_id = te.market_id
            WHERE te.event_type IN ('EXIT', 'RESOLUTION')
              AND te.event_time < fe.first_entry_time - INTERVAL '5 seconds'
            LIMIT 200
        """))
        for row in rows.fetchall():
            event_type, market_id, bot_name, side, event_time, entry_time, seq, skew = row
            violations.append(AuditViolation(
                recon_type="TEMPORAL_ORDER",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "event_type": event_type,
                    "side": side,
                    "event_seq": seq,
                    "event_time": str(event_time),
                    "first_entry_time": str(entry_time),
                    "skew_seconds": round(float(skew), 2) if skew else 0,
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} temporal ordering violation(s)",
        )
