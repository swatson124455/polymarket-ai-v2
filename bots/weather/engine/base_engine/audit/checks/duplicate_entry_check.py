"""
Check 2D: Duplicate ENTRY events within a single position lifecycle.

Lifecycle = OPEN while total_exited < total_entered * 0.95.
Legitimate re-entries (full exit then new entry) pass because
total_exited ≈ total_entered at that point.
"""
import time
from typing import List

from sqlalchemy import text

from bots.weather.engine.base_engine.audit.check_result import AuditViolation, CheckResult
from bots.weather.engine.base_engine.audit.checks.base_check import BaseCheck


class DuplicateEntryCheck(BaseCheck):
    name = "duplicate_entry"
    tables_queried = ["trade_events"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        rows = await session.execute(text("""
            -- S164: GROUP BY market_id, bot_name only (no side). Historical EXIT
            -- events used side='SELL' while ENTRYs used YES/NO.
            WITH position_lifecycles AS (
                SELECT bot_name, market_id,
                    SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS total_entered,
                    SUM(CASE WHEN event_type = 'EXIT'  THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS total_exited,
                    COUNT(CASE WHEN event_type = 'ENTRY' THEN 1 END)                                   AS entry_count,
                    BOOL_OR(event_type = 'RESOLUTION')                                                 AS has_resolution
                FROM trade_events
                WHERE event_type IN ('ENTRY', 'EXIT', 'RESOLUTION')
                GROUP BY bot_name, market_id
            )
            SELECT bot_name, market_id, entry_count, total_entered, total_exited
            FROM position_lifecycles
            WHERE entry_count > 1
              AND has_resolution = FALSE
              AND total_entered > 0
              AND total_exited < total_entered * 0.95
            LIMIT 200
        """))
        for row in rows.fetchall():
            bot_name, market_id, entry_count, total_entered, total_exited = row
            violations.append(AuditViolation(
                recon_type="DUPLICATE_ENTRY",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "entry_count": int(entry_count),
                    "total_entered": round(float(total_entered), 6),
                    "total_exited": round(float(total_exited), 6),
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} duplicate ENTRY lifecycle(s)",
        )
