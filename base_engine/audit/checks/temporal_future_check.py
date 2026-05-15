"""Temporal future check — flag any resolution-observation timestamp set to
a future date.

Sibling to temporal_order_check.py (which guards the past direction:
EXIT/RESOLUTION before ENTRY). This guards the future direction across all
6 resolution-observation columns.

Tolerance: 5 minutes ahead of NOW(), to absorb VPS-vs-DB clock skew and
NTP correction without admitting the bug class (which produces dates months
or years in the future).
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck


# (table, column, severity) — single source of truth for the columns guarded
_TARGETS = [
    ("markets",                  "resolved_at", "WARNING"),
    ("paper_trades",             "resolved_at", "WARNING"),
    ("trade_events",             "event_time",  "CRITICAL"),
    ("prediction_log",           "resolved_at", "WARNING"),
    ("mirror_rejected_signals",  "resolved_at", "WARNING"),
    ("traded_markets",           "resolved_at", "WARNING"),
]


class TemporalFutureCheck(BaseCheck):
    name = "temporal_future"
    tables_queried = [t for t, _, _ in _TARGETS]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        for table, column, severity in _TARGETS:
            try:
                result = await session.execute(text(
                    f"SELECT COUNT(*) FROM {table} "
                    f"WHERE {column} > NOW() + INTERVAL '5 minutes'"
                ))
                count = int(result.scalar() or 0)
            except Exception as e:
                violations.append(AuditViolation(
                    recon_type="TEMPORAL_FUTURE_CHECK_ERROR",
                    bot_name="",
                    market_id=None,
                    severity="WARNING",
                    details={"table": table, "column": column, "error": str(e)[:200]},
                ))
                continue

            if count == 0:
                continue

            violations.append(AuditViolation(
                recon_type="TEMPORAL_FUTURE",
                bot_name="",
                market_id=None,
                severity=severity,
                details={
                    "table": table,
                    "column": column,
                    "future_row_count": count,
                    "tolerance": "5 minutes",
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} future-dated row population(s)",
        )
