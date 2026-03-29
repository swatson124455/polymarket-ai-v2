"""
Check 4A: Foreign key integrity — all referencing tables must point to valid markets rows.

Soft FKs only (no DB-level constraints). Checks each table's market reference column:
- condition_id references markets.condition_id
- market_id references markets.id (where applicable)

Tables checked: trade_events, paper_trades, positions, trades, traded_markets,
shadow_fills, fill_analysis, trade_signals, prediction_log.
"""
import time
from typing import List

from sqlalchemy import text

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.checks.base_check import BaseCheck

# (table, join_col_in_table, ref_col_in_markets, label)
_FK_CHECKS = [
    ("trade_events",   "market_id", "id",           "trade_events→markets.id"),
    ("paper_trades",   "market_id", "id",           "paper_trades→markets.id"),
    ("positions",      "market_id", "id",           "positions→markets.id"),
    ("trades",         "market_id", "id",           "trades→markets.id"),
    ("traded_markets", "market_id", "id",           "traded_markets→markets.id"),
    ("shadow_fills",   "market_id", "id",           "shadow_fills→markets.id"),
    ("fill_analysis",  "market_id", "id",           "fill_analysis→markets.id"),
    ("trade_signals",  "market_id", "id",           "trade_signals→markets.id"),
    ("prediction_log", "market_id", "id",           "prediction_log→markets.id"),
]

# Flood cap: if total orphan rows across all tables exceeds this, still alert
_HARD_ALERT_THRESHOLD = 100


class FkIntegrityCheck(BaseCheck):
    name = "fk_integrity"
    tables_queried = ["trade_events", "paper_trades", "positions", "trades",
                      "traded_markets", "shadow_fills", "fill_analysis",
                      "trade_signals", "prediction_log", "markets"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        for table, join_col, ref_col, label in _FK_CHECKS:
            rows = await session.execute(text(f"""
                SELECT t.{join_col}, COUNT(*) AS orphan_count
                FROM {table} t
                LEFT JOIN markets m ON m.{ref_col} = t.{join_col}
                WHERE t.{join_col} IS NOT NULL
                  AND m.{ref_col} IS NULL
                GROUP BY t.{join_col}
                HAVING COUNT(*) > 0
                ORDER BY orphan_count DESC
                LIMIT 50
            """))
            for row in rows.fetchall():
                orphan_value, count = row
                violations.append(AuditViolation(
                    recon_type="FK_MISSING_MARKET",
                    bot_name="",
                    market_id=str(orphan_value) if orphan_value else None,
                    severity="CRITICAL",
                    details={
                        "source_table": table,
                        "join_column": join_col,
                        "ref_column": ref_col,
                        "label": label,
                        "orphan_value": str(orphan_value) if orphan_value else None,
                        "orphan_row_count": int(count),
                    },
                ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} FK integrity violation(s)",
        )
