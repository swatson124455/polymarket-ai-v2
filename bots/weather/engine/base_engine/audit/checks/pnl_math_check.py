"""
Check 3A: P&L mathematical verification.

Weighted average entry price across all ENTRY events per (bot, market, side).
CTF tokens are fungible — no FIFO/LIFO ambiguity.
Tolerance: $0.01 absolute difference between expected and actual realized P&L.

Expected P&L = sum of EXIT/RESOLUTION realized_pnl from trade_events.
Math: for EXIT events, realized_pnl should equal (exit_price - wavg_entry) * size.
For RESOLUTION events, the market resolved and the P&L reflects the resolution value.
We cross-check that reported realized_pnl is internally consistent:
  abs(sum(realized_pnl) - computed_pnl) > 0.01
"""
import time
from typing import List

from sqlalchemy import text

from bots.weather.engine.base_engine.audit.check_result import AuditViolation, CheckResult
from bots.weather.engine.base_engine.audit.checks.base_check import BaseCheck


class PnlMathCheck(BaseCheck):
    name = "pnl_math"
    tables_queried = ["trade_events"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        rows = await session.execute(text("""
            WITH entry_stats AS (
                SELECT
                    bot_name, market_id, side,
                    SUM(CAST(size AS DOUBLE PRECISION) * CAST(price AS DOUBLE PRECISION))
                        / NULLIF(SUM(CAST(size AS DOUBLE PRECISION)), 0)  AS wavg_entry_price,
                    SUM(CAST(size AS DOUBLE PRECISION))                    AS total_entered
                FROM trade_events
                WHERE event_type = 'ENTRY'
                  AND size IS NOT NULL
                  AND price IS NOT NULL
                  AND CAST(size AS DOUBLE PRECISION) > 0
                GROUP BY bot_name, market_id, side
            ),
            exit_stats AS (
                SELECT
                    bot_name, market_id, side,
                    SUM(CAST(size AS DOUBLE PRECISION) * CAST(price AS DOUBLE PRECISION))
                        / NULLIF(SUM(CAST(size AS DOUBLE PRECISION)), 0) AS wavg_exit_price,
                    SUM(CAST(size AS DOUBLE PRECISION))                   AS total_exited,
                    SUM(CAST(realized_pnl AS DOUBLE PRECISION))           AS reported_pnl
                FROM trade_events
                WHERE event_type = 'EXIT'
                  AND size IS NOT NULL
                  AND price IS NOT NULL
                  AND realized_pnl IS NOT NULL
                  AND CAST(size AS DOUBLE PRECISION) > 0
                GROUP BY bot_name, market_id, side
            )
            SELECT
                e.bot_name, e.market_id, e.side,
                e.wavg_entry_price,
                x.wavg_exit_price,
                x.total_exited,
                x.reported_pnl,
                -- Expected P&L = (exit_price - entry_price) * total_exited
                (x.wavg_exit_price - e.wavg_entry_price) * x.total_exited AS expected_pnl,
                ABS(x.reported_pnl - (x.wavg_exit_price - e.wavg_entry_price) * x.total_exited)
                    AS abs_discrepancy
            FROM entry_stats e
            JOIN exit_stats x
              ON x.bot_name  = e.bot_name
             AND x.market_id = e.market_id
             AND x.side      = e.side
            WHERE ABS(x.reported_pnl - (x.wavg_exit_price - e.wavg_entry_price) * x.total_exited)
                  > 0.01
              AND x.total_exited > 0
            LIMIT 200
        """))
        for row in rows.fetchall():
            (bot_name, market_id, side, wavg_entry, wavg_exit,
             total_exited, reported_pnl, expected_pnl, discrepancy) = row
            violations.append(AuditViolation(
                recon_type="PNL_MATH",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "side": side,
                    "wavg_entry_price": round(float(wavg_entry), 6) if wavg_entry else 0,
                    "wavg_exit_price": round(float(wavg_exit), 6) if wavg_exit else 0,
                    "total_exited": round(float(total_exited), 6),
                    "reported_pnl": round(float(reported_pnl), 6),
                    "expected_pnl": round(float(expected_pnl), 6) if expected_pnl else 0,
                    "abs_discrepancy": round(float(discrepancy), 6),
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} P&L math discrepancy(s)",
        )
