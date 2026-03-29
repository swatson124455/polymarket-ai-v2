"""
Check 6A: Dead-letter queue spike detection.

A sudden increase in DLQ items indicates a systemic processing failure.
Checks:
1. More than 10 unprocessed DLQ items → WARNING
2. More than 50 unprocessed DLQ items → CRITICAL (spike)
3. DLQ items older than 1h that are still unprocessed → CRITICAL (stuck backlog)
4. Error type frequency analysis — same error type >10 times → WARNING
   (systematic failure, not isolated incidents)
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck

_WARN_THRESHOLD     = 10
_CRITICAL_THRESHOLD = 50
_STUCK_AGE_HOURS    = 1
_RECURRING_MIN_COUNT = 10


class DlqCheck(BaseCheck):
    name = "dlq_spike"
    tables_queried = ["dead_letter_queue"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # Count total unprocessed items
        count_row = await session.execute(text("""
            SELECT COUNT(*) AS total,
                   COUNT(*) FILTER (WHERE created_at < NOW() - INTERVAL '1 hour') AS stuck
            FROM dead_letter_queue
            WHERE processed = FALSE OR processed IS NULL
        """))
        row = count_row.fetchone()
        if row:
            total, stuck = int(row[0] or 0), int(row[1] or 0)
            if total >= _CRITICAL_THRESHOLD:
                violations.append(AuditViolation(
                    recon_type="DLQ_SPIKE",
                    bot_name="",
                    market_id=None,
                    severity="CRITICAL",
                    details={
                        "reason": "dlq_critical_spike",
                        "unprocessed_count": total,
                        "threshold": _CRITICAL_THRESHOLD,
                    },
                ))
            elif total >= _WARN_THRESHOLD:
                violations.append(AuditViolation(
                    recon_type="DLQ_SPIKE",
                    bot_name="",
                    market_id=None,
                    severity="WARNING",
                    details={
                        "reason": "dlq_warning_spike",
                        "unprocessed_count": total,
                        "threshold": _WARN_THRESHOLD,
                    },
                ))

            if stuck > 0:
                violations.append(AuditViolation(
                    recon_type="DLQ_SPIKE",
                    bot_name="",
                    market_id=None,
                    severity="CRITICAL",
                    details={
                        "reason": "dlq_stuck_items",
                        "stuck_count": stuck,
                        "stuck_age_hours": _STUCK_AGE_HOURS,
                    },
                ))

        # Recurring error types
        error_rows = await session.execute(text("""
            SELECT error_type, COUNT(*) AS count
            FROM dead_letter_queue
            WHERE (processed = FALSE OR processed IS NULL)
              AND error_type IS NOT NULL
            GROUP BY error_type
            HAVING COUNT(*) >= :min_count
            ORDER BY count DESC
            LIMIT 20
        """), {"min_count": _RECURRING_MIN_COUNT})
        for row in error_rows.fetchall():
            error_type, count = row
            violations.append(AuditViolation(
                recon_type="DLQ_SPIKE",
                bot_name="",
                market_id=None,
                severity="WARNING",
                details={
                    "reason": "recurring_dlq_error_type",
                    "error_type": str(error_type),
                    "count": int(count),
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} DLQ spike/stuck issue(s)",
        )
