"""
Check 6B: Equity snapshot gap detection.

equity_snapshots are written periodically (typically every 5 minutes) per bot.
Gaps indicate the bot stopped running or the snapshot writer failed.

Checks:
1. No snapshot for any active bot in the last 30 minutes → CRITICAL
2. No snapshot for any active bot in the last 10 minutes → WARNING
3. total_equity decreasing monotonically over the last 3+ consecutive snapshots
   (drawdown trend) → WARNING
4. total_equity IS NULL or <= 0 in any snapshot → WARNING (data quality)
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck

_CRITICAL_GAP_MINUTES = 30
_WARNING_GAP_MINUTES  = 10


class EquitySnapshotCheck(BaseCheck):
    name = "equity_snapshot_gap"
    tables_queried = ["equity_snapshots"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # Gap detection — last snapshot per bot
        gap_rows = await session.execute(text("""
            SELECT bot_name, MAX(snapshot_date) AS last_snapshot,
                   EXTRACT(EPOCH FROM (NOW() - MAX(snapshot_date))) / 60 AS gap_minutes
            FROM equity_snapshots
            GROUP BY bot_name
            HAVING EXTRACT(EPOCH FROM (NOW() - MAX(snapshot_date))) / 60 > :warn_gap
            ORDER BY gap_minutes DESC
        """), {"warn_gap": _WARNING_GAP_MINUTES})
        for row in gap_rows.fetchall():
            bot_name, last_snap, gap_min = row
            severity = "CRITICAL" if (gap_min or 0) > _CRITICAL_GAP_MINUTES else "WARNING"
            violations.append(AuditViolation(
                recon_type="EQUITY_SNAPSHOT_GAP",
                bot_name=bot_name or "",
                market_id=None,
                severity=severity,
                details={
                    "reason": "equity_snapshot_gap",
                    "last_snapshot": str(last_snap) if last_snap else None,
                    "gap_minutes": round(float(gap_min), 1) if gap_min else None,
                    "threshold_minutes": _CRITICAL_GAP_MINUTES if severity == "CRITICAL" else _WARNING_GAP_MINUTES,
                },
            ))

        # Monotonic drawdown over last 3 consecutive snapshots
        drawdown_rows = await session.execute(text("""
            WITH recent AS (
                SELECT bot_name, snapshot_date,
                       CAST(total_equity AS DOUBLE PRECISION) AS eq,
                       ROW_NUMBER() OVER (PARTITION BY bot_name ORDER BY snapshot_date DESC) AS rn
                FROM equity_snapshots
                WHERE total_equity IS NOT NULL
                  AND CAST(total_equity AS DOUBLE PRECISION) > 0
            ),
            ranked_3 AS (
                SELECT bot_name,
                       MAX(CASE WHEN rn = 1 THEN eq END) AS eq1,
                       MAX(CASE WHEN rn = 2 THEN eq END) AS eq2,
                       MAX(CASE WHEN rn = 3 THEN eq END) AS eq3
                FROM recent
                WHERE rn <= 3
                GROUP BY bot_name
                HAVING COUNT(*) = 3
            )
            SELECT bot_name, eq1, eq2, eq3
            FROM ranked_3
            WHERE eq1 < eq2 AND eq2 < eq3
        """))
        for row in drawdown_rows.fetchall():
            bot_name, eq1, eq2, eq3 = row
            violations.append(AuditViolation(
                recon_type="EQUITY_SNAPSHOT_GAP",
                bot_name=bot_name or "",
                market_id=None,
                severity="WARNING",
                details={
                    "reason": "consecutive_equity_drawdown",
                    "equity_t0": round(float(eq1), 2),
                    "equity_t1": round(float(eq2), 2),
                    "equity_t2": round(float(eq3), 2),
                },
            ))

        # NULL or zero total_equity in recent snapshots
        bad_value_rows = await session.execute(text("""
            SELECT bot_name, COUNT(*) AS bad_count
            FROM equity_snapshots
            WHERE snapshot_date >= NOW() - INTERVAL '1 hour'
              AND (total_equity IS NULL OR CAST(total_equity AS DOUBLE PRECISION) <= 0)
            GROUP BY bot_name
            HAVING COUNT(*) > 0
        """))
        for row in bad_value_rows.fetchall():
            bot_name, count = row
            violations.append(AuditViolation(
                recon_type="EQUITY_SNAPSHOT_GAP",
                bot_name=bot_name or "",
                market_id=None,
                severity="WARNING",
                details={
                    "reason": "null_or_zero_total_equity",
                    "bad_count_1h": int(count),
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} equity snapshot issue(s)",
        )
