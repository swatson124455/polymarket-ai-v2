"""
Check 4B: traded_markets drift — rows in traded_markets must align with trade activity.

Two sub-checks:
1. traded_markets row with no corresponding ENTRY in trade_events
   → WARNING (stale tracker row, potential memory leak)
2. ENTRY in trade_events with no traded_markets row for that (bot_name, market_id)
   → WARNING (trading happened but tracking row never created)

Both are WARNING — neither represents a financial error, but they indicate
data pipeline inconsistencies that corrupt position accounting.
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck


class TradedMarketsCheck(BaseCheck):
    name = "traded_markets_drift"
    tables_queried = ["traded_markets", "trade_events"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # traded_markets rows with no ENTRY in trade_events
        stale_rows = await session.execute(text("""
            SELECT tm.bot_names, tm.market_id, tm.first_trade_at, tm.last_trade_at
            FROM traded_markets tm
            WHERE NOT EXISTS (
                SELECT 1 FROM trade_events te
                WHERE te.bot_name = ANY(tm.bot_names)
                  AND te.market_id = tm.market_id
                  AND te.event_type = 'ENTRY'
            )
            LIMIT 100
        """))
        for row in stale_rows.fetchall():
            bot_names, market_id, first_at, last_at = row
            violations.append(AuditViolation(
                recon_type="TRADED_MARKETS_DRIFT",
                bot_name=",".join(bot_names) if bot_names else "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "stale_traded_markets_row",
                    "first_trade_at": str(first_at) if first_at else None,
                    "last_trade_at": str(last_at) if last_at else None,
                },
            ))

        # ENTRY in trade_events with no traded_markets row
        missing_rows = await session.execute(text("""
            SELECT te.bot_name, te.market_id, COUNT(*) AS entry_count
            FROM trade_events te
            WHERE te.event_type = 'ENTRY'
              AND NOT EXISTS (
                  SELECT 1 FROM traded_markets tm
                  WHERE tm.bot_names @> ARRAY[te.bot_name]
                    AND tm.market_id = te.market_id
              )
            GROUP BY te.bot_name, te.market_id
            ORDER BY entry_count DESC
            LIMIT 100
        """))
        for row in missing_rows.fetchall():
            bot_name, market_id, count = row
            violations.append(AuditViolation(
                recon_type="TRADED_MARKETS_DRIFT",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="WARNING",
                details={
                    "reason": "missing_traded_markets_row",
                    "entry_count": int(count),
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} traded_markets drift(s)",
        )
