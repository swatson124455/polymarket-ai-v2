"""
Comprehensive schema drift check: ORM definitions vs actual PostgreSQL database.

Reports:
  - Tables defined in ORM but missing from DB
  - Tables in DB but not in ORM (migration-only or external)
  - Columns defined in ORM but missing from DB table
  - Columns in DB table but not in ORM model
  - Specific known references (risk_state, decision_events, paper_trades.original_side, etc.)

Usage:
    python scripts/schema_check.py
"""
import asyncio
import io
import os
import sys

os.environ["SIMULATION_MODE"] = "true"
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# ORM table/column catalog -- extracted from database.py ORM model classes
# ---------------------------------------------------------------------------
# fmt: off
ORM_TABLES: dict[str, list[str]] = {
    "markets": [
        "id", "condition_id", "question", "description", "slug", "category",
        "resolution_source", "end_date_iso", "image", "active", "liquidity",
        "volume", "yes_token_id", "no_token_id", "yes_price", "no_price",
        "outcome_prices", "resolved", "resolution", "resolution_source_method",
        "resolved_at", "created_at", "updated_at",
        "price_fetch_attempts", "last_price_fetch_empty",
        # Note: neg_risk + outcome_count are commented out in ORM (migration 017 not applied to ORM)
    ],
    "market_prices": [
        "id", "market_id", "token_id", "price", "side", "timestamp", "partition_month",
    ],
    "trades": [
        "id", "market_id", "token_id", "user_address", "bot_id", "side",
        "size", "price", "pnl", "entry_time", "exit_time", "timestamp", "partition_month",
    ],
    "users": [
        "address", "total_profit", "total_volume", "win_rate", "total_trades",
        "wins", "losses", "roi", "is_elite", "is_likely_market_maker", "last_updated",
    ],
    "predictions": [
        "id", "market_id", "token_id", "confidence", "model_type", "features", "timestamp",
    ],
    "positions": [
        "id", "bot_id", "source_bot", "market_id", "token_id", "side", "size",
        "entry_price", "current_price", "unrealized_pnl", "opened_at", "status", "is_paper",
    ],
    "system_config": [
        "key", "value",
    ],
    "sync_log": [
        "id", "sync_type", "component", "started_at", "completed_at", "status",
        "records_processed", "records_inserted", "records_failed", "error_message",
        "metadata",  # Column name in DB; Python attr is 'extra'
    ],
    "snapshots": [
        "id", "description", "created_at", "statistics",
    ],
    "healing_log": [
        "id", "timestamp", "issues_detected", "fixes_applied", "details",
    ],
    "ml_features": [
        "market_id", "computed_at", "features", "updated_at",
    ],
    "fill_analysis": [
        "id", "market_id", "source_bot", "fill_price", "fill_side", "fill_time",
        "price_30s", "price_60s", "price_300s", "adverse_move_30s", "adverse_move_300s",
        "created_at",
    ],
    "learning_patterns": [
        "id", "pattern_type", "pattern_key", "wins", "losses", "total",
        "confidence", "sample_size", "updated_at",
    ],
    "prediction_log": [
        "id", "market_id", "token_id", "model_name", "predicted_prob", "market_price",
        "edge", "prediction_time", "fallback_level", "confidence", "resolution",
        "resolved_at", "was_correct", "realized_edge", "trade_executed", "trade_side",
        "trade_size", "trade_price", "trade_pnl", "ensemble_pred", "learning_conf",
        "feature_snapshot", "created_at",
    ],
    "paper_trades": [
        "id", "order_id", "market_id", "token_id", "bot_name", "side", "size",
        "price", "confidence", "created_at", "resolution", "resolved_at", "realized_pnl",
    ],
    "ml_models": [
        "id", "model_name", "model_type", "model_data", "scaler_data", "metrics",
        "version", "is_active", "trained_at", "created_at",
    ],
    "signals": [
        "id", "market_id", "source_type", "source_name", "direction", "confidence",
        "raw_text", "extracted_entities", "time_sensitivity", "is_breaking",
        "created_at", "expires_at", "acted_on", "priority_score",
        "outcome_correct", "resolution_at", "market_resolution",
    ],
    "scheduled_events": [
        "id", "market_id", "event_type", "event_name", "scheduled_time",
        "source_url", "description", "created_at", "notified",
    ],
    "performance_records": [
        "id", "trade_id", "bot_name", "market_id", "market_category",
        "entry_price_range", "time_to_resolution_days", "liquidity_level",
        "signal_source", "market_regime", "day_of_week", "hour_of_day",
        "profit", "profit_pct", "hold_time_hours", "was_winner",
        "entry_time", "exit_time", "recorded_at",
    ],
    "whale_movements": [
        "id", "trade_id", "user_address", "market_id", "token_id", "side",
        "size", "price", "value_usd", "timestamp", "smart_money_rank",
        "trader_category_accuracy", "is_clustered", "cluster_id",
    ],
    "data_quality_issues": [
        "id", "market_id", "issue_type", "description", "detected_at",
    ],
}
# fmt: on

# Tables referenced in code via raw SQL but NOT defined in ORM models
# These are created only via migration SQL files
MIGRATION_ONLY_TABLES: dict[str, str] = {
    "risk_state": "migration 004 — used by risk_manager.py (raw SQL), dashboard.py",
    "decision_events": "migration 020 — used by event_bus.py (raw SQL)",
    "schema_migrations": "migration 001 — tracks applied migrations",
    "data_lineage": "migration 002 — data lineage tracking",
    "data_quality_sla": "migration 002 — SLA monitoring",
    "mirror_performance": "migration 005 — mirror bot stats",
    "execution_quality": "migration 005 — execution quality tracking",
    "signal_quality": "migration 005 — signal quality tracking",
    "momentum_false_signals": "migration 005 — momentum false signal log",
    "confidence_calibration": "migration 005 — calibration tracking",
    "kill_switch_events": "migration 013 — kill switch event log",
    "tunable_config": "migration 013 — tunable configuration",
    "tax_transactions": "migration 013 — tax transaction tracking",
    "cross_platform_arb_opportunities": "migration 015 — cross-platform arb",
    "sports_game_state": "migration 015 — sports game state",
    "oracle_proposals": "migration 015 — oracle proposals",
    "regulatory_alerts": "migration 015 — regulatory alerts",
    "airdrop_events": "migration 015 — airdrop events",
    "wash_trading_alerts": "migration 015 — wash trading alerts",
    "rag_documents": "migration 020 — RAG document store (needs pgvector)",
    "market_embeddings": "migration 020 — market embeddings (needs pgvector)",
}

# Specific column references to check (code references columns that may not exist)
SPECIFIC_CHECKS = [
    {
        "table": "paper_trades",
        "column": "original_side",
        "referenced_by": "order_gateway.py passes original_side to paper_trading.py, but paper_trading.py does NOT persist it to DB (only uses it in-memory for position tracking). The ORM PaperTradeRecord model does NOT define this column.",
        "severity": "LOW — in-memory only; not persisted. But if anyone adds DB persistence later, it will fail.",
    },
    {
        "table": "risk_state",
        "column": None,  # Entire table
        "referenced_by": "risk_manager.py (_get_risk_state, _update_pnl), database.py (get_risk_state_pnl), dashboard.py — all raw SQL",
        "severity": "CRITICAL — if migration 004 was never run, risk limit checks silently fail (caught by try/except)",
    },
    {
        "table": "decision_events",
        "column": None,  # Entire table
        "referenced_by": "event_bus.py (EventSourcingBus._persist_event, replay_events) — raw SQL",
        "severity": "MEDIUM — event sourcing bus silently drops events if table missing (try/except)",
    },
    {
        "table": "markets",
        "column": "neg_risk",
        "referenced_by": "migration 017 adds it, ORM has it commented out. Code may reference via raw SQL.",
        "severity": "LOW — ORM intentionally omits it; raw SQL queries handle absence gracefully",
    },
    {
        "table": "markets",
        "column": "outcome_count",
        "referenced_by": "migration 017 adds it, ORM has it commented out.",
        "severity": "LOW — same as neg_risk",
    },
    {
        "table": "markets",
        "column": "outcome",
        "referenced_by": "User reported code reference — checking if it actually exists anywhere",
        "severity": "CHECKING — may be a false alarm",
    },
]


async def main():
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()

    if db.session_factory is None:
        print("ERROR: Could not connect to database. Check DATABASE_URL in .env")
        return

    print("=" * 80)
    print("SCHEMA DRIFT CHECK — ORM vs Actual Database")
    print("=" * 80)

    async with db.get_session() as session:
        # ---------------------------------------------------------------
        # 1. Get ALL actual tables from the database
        # ---------------------------------------------------------------
        r = await session.execute(text(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
            "ORDER BY table_name"
        ))
        db_tables = {row[0] for row in r.fetchall()}
        print(f"\nActual DB tables ({len(db_tables)}): {sorted(db_tables)}")

        # ---------------------------------------------------------------
        # 2. Get columns for every actual table
        # ---------------------------------------------------------------
        db_columns: dict[str, set[str]] = {}
        for tbl in sorted(db_tables):
            r = await session.execute(text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = :tbl "
                "ORDER BY ordinal_position"
            ), {"tbl": tbl})
            db_columns[tbl] = {row[0] for row in r.fetchall()}

        # ---------------------------------------------------------------
        # 3. Compare ORM tables vs DB tables
        # ---------------------------------------------------------------
        orm_table_names = set(ORM_TABLES.keys())
        all_known_tables = orm_table_names | set(MIGRATION_ONLY_TABLES.keys())

        print("\n" + "=" * 80)
        print("SECTION 1: TABLE-LEVEL COMPARISON")
        print("=" * 80)

        # ORM tables missing from DB
        orm_missing = orm_table_names - db_tables
        if orm_missing:
            print(f"\n[CRITICAL] ORM tables MISSING from DB ({len(orm_missing)}):")
            for t in sorted(orm_missing):
                print(f"  - {t}  (defined in ORM but does not exist in database)")
        else:
            print("\n[OK] All ORM tables exist in DB.")

        # Migration-only tables: check which exist
        print(f"\nMigration-only tables (not in ORM, may or may not exist in DB):")
        for t, desc in sorted(MIGRATION_ONLY_TABLES.items()):
            exists = t in db_tables
            status = "EXISTS" if exists else "MISSING"
            severity = "CRITICAL" if ("risk_state" == t or "decision_events" == t) and not exists else ""
            marker = "[!!]" if severity else "[  ]"
            print(f"  {marker} {t}: {status}  ({desc})")

        # DB tables not known to ORM or migrations
        unknown_db = db_tables - all_known_tables
        if unknown_db:
            print(f"\nDB tables NOT in ORM or known migrations ({len(unknown_db)}):")
            for t in sorted(unknown_db):
                cols = sorted(db_columns.get(t, set()))
                print(f"  - {t}  columns: {cols}")

        # ---------------------------------------------------------------
        # 4. Column-level comparison for each ORM table
        # ---------------------------------------------------------------
        print("\n" + "=" * 80)
        print("SECTION 2: COLUMN-LEVEL COMPARISON (ORM vs DB)")
        print("=" * 80)

        total_missing_cols = 0
        total_extra_cols = 0

        for tbl_name in sorted(ORM_TABLES.keys()):
            orm_cols = set(ORM_TABLES[tbl_name])
            if tbl_name not in db_tables:
                print(f"\n  [{tbl_name}] SKIPPED — table does not exist in DB")
                continue

            actual_cols = db_columns[tbl_name]
            missing_from_db = orm_cols - actual_cols
            extra_in_db = actual_cols - orm_cols

            if not missing_from_db and not extra_in_db:
                print(f"\n  [{tbl_name}] OK — {len(orm_cols)} columns match")
                continue

            print(f"\n  [{tbl_name}] DRIFT DETECTED:")
            if missing_from_db:
                total_missing_cols += len(missing_from_db)
                for c in sorted(missing_from_db):
                    print(f"    [MISSING IN DB] {c}  — ORM defines it but DB lacks it")
            if extra_in_db:
                total_extra_cols += len(extra_in_db)
                for c in sorted(extra_in_db):
                    print(f"    [EXTRA IN DB]   {c}  — DB has it but ORM does not define it")

        # ---------------------------------------------------------------
        # 5. Specific known-issue checks
        # ---------------------------------------------------------------
        print("\n" + "=" * 80)
        print("SECTION 3: SPECIFIC KNOWN-ISSUE CHECKS")
        print("=" * 80)

        for check in SPECIFIC_CHECKS:
            tbl = check["table"]
            col = check["column"]
            ref = check["referenced_by"]
            sev = check["severity"]

            if col is None:
                # Checking entire table existence
                exists = tbl in db_tables
                status = "EXISTS" if exists else "MISSING"
                icon = "[OK]" if exists else "[!!]"
                print(f"\n  {icon} Table '{tbl}': {status}")
                print(f"      Referenced by: {ref}")
                print(f"      Severity: {sev}")
            else:
                # Checking specific column
                if tbl not in db_tables:
                    print(f"\n  [!!] {tbl}.{col}: TABLE MISSING (so column missing too)")
                    print(f"      Referenced by: {ref}")
                    print(f"      Severity: {sev}")
                elif col in db_columns.get(tbl, set()):
                    print(f"\n  [OK] {tbl}.{col}: EXISTS in DB")
                    print(f"      Note: {ref}")
                    print(f"      Severity: {sev}")
                else:
                    print(f"\n  [!!] {tbl}.{col}: MISSING from DB")
                    print(f"      Referenced by: {ref}")
                    print(f"      Severity: {sev}")

        # ---------------------------------------------------------------
        # 6. Summary
        # ---------------------------------------------------------------
        print("\n" + "=" * 80)
        print("SUMMARY")
        print("=" * 80)
        print(f"  ORM tables:              {len(orm_table_names)}")
        print(f"  Actual DB tables:        {len(db_tables)}")
        print(f"  ORM tables missing:      {len(orm_missing)}")
        print(f"  Unknown DB tables:       {len(unknown_db)}")
        print(f"  Columns missing from DB: {total_missing_cols}")
        print(f"  Extra columns in DB:     {total_extra_cols}")

        critical_issues = []
        if orm_missing:
            critical_issues.append(f"{len(orm_missing)} ORM tables missing from DB")
        if total_missing_cols:
            critical_issues.append(f"{total_missing_cols} ORM columns missing from DB")
        if "risk_state" not in db_tables:
            critical_issues.append("risk_state table missing (run migration 004)")
        if "decision_events" not in db_tables:
            critical_issues.append("decision_events table missing (run migration 020)")

        if critical_issues:
            print(f"\n  CRITICAL ISSUES ({len(critical_issues)}):")
            for i, issue in enumerate(critical_issues, 1):
                print(f"    {i}. {issue}")
        else:
            print("\n  No critical issues found.")

        print()

    await db.close()


if __name__ == "__main__":
    asyncio.run(main())
