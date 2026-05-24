"""
Check 6C: Schema drift detection via information_schema.

Queries information_schema.tables and columns to detect:
1. Expected tables that no longer exist → CRITICAL (deployment or migration failure)
2. Expected critical columns that are missing from their table → CRITICAL

Expected table set is hardcoded — this is intentional. It must match what the
system actually requires. Missing tables = schema migration not applied.

Only checks for MISSING objects (not for extra tables/columns — those are safe).
"""
import time
from typing import Dict, List, Set

from sqlalchemy import text

from bots.weather.engine.base_engine.audit.check_result import AuditViolation, CheckResult
from bots.weather.engine.base_engine.audit.checks.base_check import BaseCheck

# Core tables that must exist
_REQUIRED_TABLES: Set[str] = {
    "trade_events",
    "paper_trades",
    "positions",
    "markets",
    "traded_markets",
    "market_prices",
    "trades",
    "shadow_fills",
    "fill_analysis",
    "trade_signals",
    "prediction_log",
    "bot_health_states",
    "dead_letter_queue",
    "equity_snapshots",
    "reconciliation_breaks",
    "audit_runs",
    "system_config",
    "system_kv",
    "daily_counters",
    "sync_log",
    "bot_market_params",
}

# Critical columns per table: {table: [columns]}
_REQUIRED_COLUMNS: Dict[str, List[str]] = {
    "trade_events":    ["event_type", "market_id", "bot_name", "size", "price",
                        "fees", "realized_pnl", "event_time", "sequence_num",
                        "correlation_id", "execution_mode"],
    "paper_trades":    ["bot_name", "market_id", "side", "size", "price",
                        "realized_pnl", "resolution", "status"],
    "positions":       ["source_bot", "market_id", "side", "size", "entry_price",
                        "current_price", "unrealized_pnl", "status"],
    "markets":         ["id", "condition_id", "resolved", "active"],
    "traded_markets":  ["bot_names", "market_id", "first_trade_at", "last_trade_at",
                        "trade_count", "status"],
    "reconciliation_breaks": ["recon_type", "bot_name", "status", "violation_hash", "audit_run_id"],
    "audit_runs":      ["run_id", "run_type", "started_at", "status", "triggered_by"],
    "equity_snapshots": ["bot_name", "snapshot_date", "total_equity",
                         "realized_pnl", "unrealized_pnl", "open_positions"],
    "dead_letter_queue": ["event_type", "error_type", "status", "created_at",
                          "retry_count", "source_bot"],
    "shadow_fills":    ["bot_name", "market_id", "correlation_id", "trade_executed",
                        "order_size_shares", "signal_price", "side"],
    "prediction_log":  ["bot_name", "market_id", "predicted_prob", "resolution",
                        "was_correct", "trade_executed", "prediction_time", "created_at"],
    "bot_health_states": ["bot_name", "state", "recorded_at", "failure_count"],
    "fill_analysis":   ["source_bot", "market_id", "fill_price", "fill_side",
                        "fill_time", "price_30s", "price_300s"],
    "trade_signals":   ["bot_name", "market_id", "signal_direction", "signal_confidence",
                        "signal_source", "created_at"],
}


class SchemaDriftCheck(BaseCheck):
    name = "schema_drift"
    tables_queried = ["information_schema.tables", "information_schema.columns"]

    async def execute(self, session) -> CheckResult:
        t0 = time.monotonic()
        violations: List[AuditViolation] = []

        # Get all existing tables in public schema
        existing_tables_rows = await session.execute(text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
              AND table_type = 'BASE TABLE'
        """))
        existing_tables: Set[str] = {row[0] for row in existing_tables_rows.fetchall()}

        # Check required tables
        for table in sorted(_REQUIRED_TABLES):
            if table not in existing_tables:
                violations.append(AuditViolation(
                    recon_type="SCHEMA_DRIFT",
                    bot_name="",
                    market_id=None,
                    severity="CRITICAL",
                    details={
                        "reason": "required_table_missing",
                        "table": table,
                    },
                ))

        # Get all existing columns for tables we care about
        tables_to_check = list(_REQUIRED_COLUMNS.keys())
        if not tables_to_check:
            pass
        else:
            existing_cols_rows = await session.execute(text("""
                SELECT table_name, column_name
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ANY(:tables)
            """), {"tables": tables_to_check})
            existing_cols: Dict[str, Set[str]] = {}
            for row in existing_cols_rows.fetchall():
                t_name, col_name = row
                existing_cols.setdefault(t_name, set()).add(col_name)

            for table, required_cols in _REQUIRED_COLUMNS.items():
                if table not in existing_tables:
                    continue  # Already reported as missing table
                table_cols = existing_cols.get(table, set())
                for col in required_cols:
                    if col not in table_cols:
                        violations.append(AuditViolation(
                            recon_type="SCHEMA_DRIFT",
                            bot_name="",
                            market_id=None,
                            severity="CRITICAL",
                            details={
                                "reason": "required_column_missing",
                                "table": table,
                                "column": col,
                            },
                        ))

        return CheckResult(
            check_name=self.name,
            passed=len(violations) == 0,
            violations=violations,
            duration_ms=(time.monotonic() - t0) * 1000,
            tables_queried=self.tables_queried,
            summary=f"{len(violations)} schema drift violation(s)",
        )
