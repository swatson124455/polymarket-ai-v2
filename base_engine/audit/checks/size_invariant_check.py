"""
Check 2A: Size invariant and negative size violations in trade_events.

- EXIT + RESOLUTION size must not exceed ENTRY size (0.1% tolerance)
- No negative sizes anywhere
Uses SUM() aggregation per (bot, market) — side excluded because historical
EXIT events used side='SELL' while ENTRYs used YES/NO (S163 transition).
"""
import time
from decimal import Decimal
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck


class SizeInvariantCheck(BaseCheck):
    name = "size_invariant"
    tables_queried = ["trade_events"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # Size invariant: EXIT + RESOLUTION must not exceed ENTRY (with 0.1% tolerance)
        # S164: GROUP BY market_id, bot_name only (no side). Historical EXIT events
        # used side='SELL' while ENTRYs used YES/NO — per-side grouping created
        # false positives. event_type is the correct discriminator, not side.
        rows = await session.execute(text("""
            SELECT market_id, bot_name,
                SUM(CASE WHEN event_type = 'ENTRY'      THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS entry_sz,
                SUM(CASE WHEN event_type = 'EXIT'       THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS exit_sz,
                SUM(CASE WHEN event_type = 'RESOLUTION' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) AS res_sz
            FROM trade_events
            WHERE event_type IN ('ENTRY', 'EXIT', 'RESOLUTION')
              AND size IS NOT NULL
            GROUP BY market_id, bot_name
            HAVING
                SUM(CASE WHEN event_type IN ('EXIT','RESOLUTION') THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END)
                > SUM(CASE WHEN event_type = 'ENTRY' THEN CAST(size AS DOUBLE PRECISION) ELSE 0 END) * 1.001
        """))
        for row in rows.fetchall():
            market_id, bot_name, entry_sz, exit_sz, res_sz = row
            overage = (exit_sz + res_sz) - entry_sz
            violations.append(AuditViolation(
                recon_type="SIZE_INVARIANT",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "entry_size": round(entry_sz, 6),
                    "exit_size": round(exit_sz, 6),
                    "resolution_size": round(res_sz, 6),
                    "overage": round(overage, 6),
                },
                internal_value=Decimal(str(round(entry_sz, 6))),
                external_value=Decimal(str(round(exit_sz + res_sz, 6))),
                difference=Decimal(str(round(overage, 6))),
            ))

        # Negative sizes
        neg_rows = await session.execute(text("""
            SELECT event_type, market_id, bot_name, side, CAST(size AS DOUBLE PRECISION)
            FROM trade_events
            WHERE CAST(size AS DOUBLE PRECISION) < 0
            LIMIT 50
        """))
        for row in neg_rows.fetchall():
            event_type, market_id, bot_name, side, size = row
            violations.append(AuditViolation(
                recon_type="NEGATIVE_SIZE",
                bot_name=bot_name or "",
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "event_type": event_type,
                    "side": side,
                    "size": round(size, 6),
                },
                internal_value=Decimal(str(round(size, 6))),
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} size violation(s)",
        )
