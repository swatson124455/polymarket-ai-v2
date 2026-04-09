"""
Check: Prices coverage — open positions with NO price data at all.

Worse than frozen prices: these positions have zero stop-loss protection
because the price pipeline has never found a price for them.

Severity: WARNING — operational risk, not data corruption.
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck


class PricesCoverageCheck(BaseCheck):
    name = "prices_coverage"
    tables_queried = ["positions", "market_prices_latest"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        rows = await session.execute(text("""
            SELECT p.source_bot, p.market_id, p.token_id, p.side,
                   p.size, p.entry_price, p.opened_at
            FROM positions p
            LEFT JOIN market_prices_latest mpl ON mpl.token_id = p.token_id
            WHERE p.status = 'open'
              AND p.size > 0
              AND mpl.token_id IS NULL
            ORDER BY p.opened_at ASC
            LIMIT 200
        """))

        for row in rows.fetchall():
            bot, mid, tid, side, sz, entry_px, opened = row
            violations.append(AuditViolation(
                recon_type="NO_PRICE_COVERAGE",
                bot_name=bot or "",
                market_id=mid,
                severity="WARNING",
                details={
                    "token_id": tid,
                    "side": side,
                    "size": float(sz) if sz else 0,
                    "entry_price": float(entry_px) if entry_px else None,
                    "opened_at": str(opened),
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} position(s) with no price data",
        )
