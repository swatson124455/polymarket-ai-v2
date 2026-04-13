"""
Check: Frozen prices — open positions with stale price data (>6h unchanged).

A frozen price means stop-loss and take-profit triggers can't fire, leaving
positions unprotected.  Only flags positions on active, unresolved markets
(resolved markets are expected to stop updating).

Severity: WARNING — operational risk, not data corruption.
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck


class FrozenPriceCheck(BaseCheck):
    name = "frozen_prices"
    tables_queried = ["positions", "market_prices_latest", "markets"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        rows = await session.execute(text("""
            SELECT p.source_bot, p.market_id, p.token_id, p.size,
                   mpl.price, mpl.timestamp,
                   EXTRACT(EPOCH FROM (NOW() - mpl.timestamp)) / 3600 AS hours_stale
            FROM positions p
            JOIN market_prices_latest mpl ON mpl.token_id = p.token_id
            JOIN markets m ON (CAST(m.id AS TEXT) = p.market_id
                               OR m.condition_id = p.market_id)
            WHERE p.status = 'open'
              AND p.size > 0
              AND m.active = TRUE
              AND m.resolved = FALSE
              AND EXTRACT(EPOCH FROM (NOW() - mpl.timestamp)) > 21600  -- 6 hours
            ORDER BY hours_stale DESC
            LIMIT 200
        """))

        for row in rows.fetchall():
            bot, mid, tid, sz, px, updated, hours = row
            violations.append(AuditViolation(
                recon_type="FROZEN_PRICE",
                bot_name=bot or "",
                market_id=mid,
                severity="WARNING",
                details={
                    "token_id": tid,
                    "size": float(sz) if sz else 0,
                    "last_price": float(px) if px else None,
                    "last_updated": str(updated),
                    "hours_stale": round(float(hours), 1),
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} frozen price(s) (>6h stale)",
        )
