"""
Check 6E: Bot health state anomaly detection.

Bots stuck in 'failed' or 'safe_mode' for >1 hour → CRITICAL.
Bots with no health state update for >30 minutes → WARNING (silent failure).
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck

_STUCK_CRITICAL_MINUTES = 60
_SILENT_WARNING_MINUTES = 30


class BotHealthStateCheck(BaseCheck):
    name = "bot_health_state_anomaly"
    tables_queried = ["bot_health_states"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # Bots stuck in failed/safe_mode > 1 hour
        stuck_rows = await session.execute(text("""
            SELECT bot_name, status, recorded_at,
                   EXTRACT(EPOCH FROM (NOW() - recorded_at)) / 60 AS stuck_minutes
            FROM bot_health_states
            WHERE status IN ('failed', 'safe_mode')
              AND recorded_at < NOW() - INTERVAL '1 hour'
        """))
        for row in stuck_rows.fetchall():
            bot_name, status, recorded_at, stuck_min = row
            violations.append(AuditViolation(
                recon_type="BOT_HEALTH_STATE_ANOMALY",
                bot_name=bot_name or "",
                market_id=None,
                severity="CRITICAL",
                details={
                    "reason": "bot_stuck_in_failed_state",
                    "status": status,
                    "recorded_at": str(recorded_at) if recorded_at else None,
                    "stuck_minutes": round(float(stuck_min), 1) if stuck_min else None,
                },
            ))

        # Bots with no health update > 30 minutes
        silent_rows = await session.execute(text("""
            SELECT bot_name, status, recorded_at,
                   EXTRACT(EPOCH FROM (NOW() - recorded_at)) / 60 AS silent_minutes
            FROM bot_health_states
            WHERE recorded_at < NOW() - INTERVAL '30 minutes'
              AND status NOT IN ('failed', 'safe_mode')
        """))
        for row in silent_rows.fetchall():
            bot_name, status, recorded_at, silent_min = row
            violations.append(AuditViolation(
                recon_type="BOT_HEALTH_STATE_ANOMALY",
                bot_name=bot_name or "",
                market_id=None,
                severity="WARNING",
                details={
                    "reason": "bot_health_state_stale",
                    "status": status,
                    "recorded_at": str(recorded_at) if recorded_at else None,
                    "silent_minutes": round(float(silent_min), 1) if silent_min else None,
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} bot health state anomaly(s)",
        )
