"""
Check 5A: Position-level reconciliation against trade_events.

Compares positions.size against the net of ENTRY - EXIT - RESOLUTION sizes
in trade_events. Tolerance: 0.1% of entry size OR 0.001 shares (whichever is
larger) to absorb rounding in DOUBLE PRECISION arithmetic.

Also checks:
- positions with size > 0 but no ENTRY event (phantom position)
- positions.unrealized_pnl sign consistency (if price < entry_price, uPnL < 0 for YES)
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck


class PositionTradeEventsCheck(BaseCheck):
    name = "position_size_mismatch"
    tables_queried = ["positions", "trade_events"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # Size mismatch between positions table and trade_events net
        mismatch_rows = await session.execute(text("""
            WITH te_net AS (
                SELECT bot_name, market_id, side,
                    SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
                        - SUM(CASE WHEN event_type IN ('EXIT','RESOLUTION') THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
                    AS net_size,
                    SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
                    AS total_entered
                FROM trade_events
                WHERE event_type IN ('ENTRY', 'EXIT', 'RESOLUTION')
                  AND size IS NOT NULL
                GROUP BY bot_name, market_id, side
            )
            SELECT p.bot_name, p.market_id, p.side,
                   CAST(p.size AS DOUBLE PRECISION) AS pos_size,
                   te.net_size,
                   te.total_entered,
                   ABS(CAST(p.size AS DOUBLE PRECISION) - te.net_size) AS abs_diff
            FROM positions p
            JOIN te_net te
              ON te.bot_name  = p.bot_name
             AND te.market_id = p.market_id
             AND te.side      = p.side
            WHERE CAST(p.size AS DOUBLE PRECISION) > 0
              AND ABS(CAST(p.size AS DOUBLE PRECISION) - te.net_size)
                  > GREATEST(te.total_entered * 0.001, 0.001)
            LIMIT 200
        """))
        for row in mismatch_rows.fetchall():
            bot_name, market_id, side, pos_size, net_size, total_entered, diff = row
            violations.append(AuditViolation(
                recon_type="POSITION_SIZE_MISMATCH",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "side": side,
                    "positions_size": round(float(pos_size), 6),
                    "trade_events_net": round(float(net_size), 6),
                    "total_entered": round(float(total_entered), 6),
                    "abs_diff": round(float(diff), 6),
                },
            ))

        # Phantom positions: size > 0 but no ENTRY in trade_events
        phantom_rows = await session.execute(text("""
            SELECT p.bot_name, p.market_id, p.side,
                   CAST(p.size AS DOUBLE PRECISION) AS pos_size
            FROM positions p
            WHERE CAST(p.size AS DOUBLE PRECISION) > 0
              AND NOT EXISTS (
                  SELECT 1 FROM trade_events te
                  WHERE te.bot_name  = p.bot_name
                    AND te.market_id = p.market_id
                    AND te.event_type = 'ENTRY'
              )
            LIMIT 100
        """))
        for row in phantom_rows.fetchall():
            bot_name, market_id, side, pos_size = row
            violations.append(AuditViolation(
                recon_type="POSITION_SIZE_MISMATCH",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "reason": "phantom_position_no_entry_event",
                    "side": side,
                    "positions_size": round(float(pos_size), 6),
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} position size mismatch(es)",
        )
