"""
Check: Resolution verification — duplicate RESOLUTION events per (bot, market).

S167 added server-side dedup (one RESOLUTION per bot+market), but this check
catches any that slip through or existed before the fix.

No API calls — audit checks run on a timer and API failures would pollute results.
API verification is in scripts/verify_resolutions.py (on-demand).

Severity: CRITICAL — duplicate resolutions corrupt P&L and training data.

S169: Data quality verification pipeline.
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck


class ResolutionVerificationCheck(BaseCheck):
    name = "resolution_verification"
    tables_queried = ["trade_events"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # Find (bot, market) pairs with >1 RESOLUTION event
        rows = await session.execute(text("""
            SELECT te.bot_name, te.market_id, COUNT(*) AS res_count,
                   MIN(te.event_time) AS first_resolution,
                   MAX(te.event_time) AS last_resolution
            FROM trade_events te
            WHERE te.event_type = 'RESOLUTION'
            GROUP BY te.bot_name, te.market_id
            HAVING COUNT(*) > 1
            ORDER BY COUNT(*) DESC
            LIMIT 200
        """))

        for row in rows.fetchall():
            bot, mid, count, first_ts, last_ts = row
            violations.append(AuditViolation(
                recon_type="DUPLICATE_RESOLUTION",
                bot_name=bot or "",
                market_id=mid,
                severity="CRITICAL",
                details={
                    "resolution_count": int(count),
                    "first_resolution": str(first_ts),
                    "last_resolution": str(last_ts),
                    "gap_seconds": (last_ts - first_ts).total_seconds() if first_ts and last_ts else 0,
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} market(s) with duplicate RESOLUTION events",
        )
