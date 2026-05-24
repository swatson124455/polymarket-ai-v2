"""
Check 5B: traded_markets status drift — tracker row says 'open' but paper_trades
has realized rows for the same market.

Semantic: a market was resolved (paper_trades.realized_pnl IS NOT NULL) but the
traded_markets.status column was not flipped to 'closed'. Scanners that filter
by traded_markets.status will keep treating the market as tradeable.

Replaces the STALE_POSITION emit path in base_engine/data/database.py
run_reconciliation() — which wrote directly to reconciliation_breaks with
audit_run_id=0 and bypassed the orchestrator entirely. Same recon_type
('STALE_POSITION') is retained so downstream consumers filter identically;
legacy rows (audit_run_id=0) and new rows (audit_run_id>0) distinguish
by correlation.

Kill-switch: set TRADED_MARKETS_STATUS_DRIFT_CHECK_ENABLED=false to disable
without a revert. Default 'true'.
"""
import os
import time
from typing import List

from sqlalchemy import text

from bots.weather.engine.base_engine.audit.check_result import AuditViolation, CheckResult
from bots.weather.engine.base_engine.audit.checks.base_check import BaseCheck


class TradedMarketsStatusDriftCheck(BaseCheck):
    name = "traded_markets_status_drift"
    tables_queried = ["traded_markets", "paper_trades"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()

        if os.getenv("TRADED_MARKETS_STATUS_DRIFT_CHECK_ENABLED", "true").lower() == "false":
            return CheckResult(
                check_name=self.name,
                passed=True,
                violations=[],
                duration_ms=(time.monotonic() - t0) * 1000,
                tables_queried=self.tables_queried,
                summary="check disabled via TRADED_MARKETS_STATUS_DRIFT_CHECK_ENABLED=false",
            )

        violations: List[AuditViolation] = []

        rows = await session.execute(text("""
            SELECT tm.market_id,
                   tm.bot_names,
                   COUNT(*) FILTER (WHERE pt.realized_pnl IS NOT NULL) AS resolved_trade_count,
                   MIN(pt.created_at) AS earliest_created,
                   MAX(pt.resolved_at) AS latest_resolved
            FROM traded_markets tm
            JOIN paper_trades pt ON pt.market_id = tm.market_id
            WHERE tm.status = 'open'
              AND pt.realized_pnl IS NOT NULL
              AND pt.side IN ('YES', 'NO')
            GROUP BY tm.market_id, tm.bot_names
            LIMIT 200
        """))

        for row in rows.fetchall():
            market_id, bot_names, resolved_trade_count, earliest_created, latest_resolved = row
            bot = str(bot_names).split(",")[0] if bot_names else "unknown"
            violations.append(AuditViolation(
                recon_type="STALE_POSITION",
                bot_name=bot,
                market_id=str(market_id) if market_id else None,
                severity="CRITICAL",
                details={
                    "source": "traded_markets_status_drift",
                    "reason": "traded_markets.status='open' but paper_trades has resolved row(s)",
                    "resolved_trade_count": int(resolved_trade_count),
                    "earliest_created": str(earliest_created) if earliest_created else None,
                    "latest_resolved": str(latest_resolved) if latest_resolved else None,
                },
            ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} traded_markets status drift(s)",
        )
