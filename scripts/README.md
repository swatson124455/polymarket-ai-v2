# Scripts and Entry Points

## Canonical entry points

Use these as the official ways to run the system:

| Entry point | Purpose |
|-------------|---------|
| `python main.py` | CLI mode (from project root) |
| `python run_ui.py` | Streamlit dashboard; open http://localhost:8501 |
| `python validate.py` | System validation (DB, Redis, engines) |
| `python scripts/run_ingestion_standalone.py` | Standalone historical price ingestion |

### Ingestion script options

```bash
python scripts/run_ingestion_standalone.py --validate-only   # Pre-checks (DB, VPN, API)
python scripts/run_ingestion_standalone.py --clear-stuck    # Clear stuck sync_log entries
python scripts/run_ingestion_standalone.py --pull-all --markets 100 --days 365 --prices 100   # Markets + historical prices
python scripts/run_ingestion_standalone.py --backfill --backfill-days 365 --backfill-markets-batch 50 --backfill-prices-batch 50   # Resumable backfill
python scripts/run_ingestion_standalone.py --historical --max-markets 100   # Historical prices only (uses DB token IDs)
```

**Supabase connection limits**: If you see `MaxClientsInSessionMode`, use `DATABASE_POOLER_URL` (Transaction mode, port 6543) in `.env`. Session mode (port 5432) auto-caps pool to 2.

**Bot inputs**: `BaseEngine.get_markets_with_price_history()` returns clean digest: `[{market, price_history}]`. Token IDs extracted from DB or API format. Bots consume this; learning/strategy logic stays in bots.

### Faster bulk inserts (one-time migration)

Run `python scripts/run_market_prices_constraint.py` to add the unique constraint. Or run `schema/add_market_prices_unique_constraint.sql` in Supabase SQL editor (for large tables). Enables `ON CONFLICT DO NOTHING` bulk insert instead of per-row merge.

### Sync run logging and health checks (in-app)

- **sync_log table**: Ingestion runs (backfill, full, markets) are logged to `sync_log` for monitoring. Schema is in `schema/supabase_schema.sql`; create it if you use a fresh DB.
- **validate.py health checks**: `python validate.py` reports last successful ingestion and latest trade timestamp; warns if last sync > 48h or latest trade > 7 days. Non-fatal; validation still passes.

### Elite users (prediction)

After ingesting users/trades, run **Update elite users** from the dashboard (Data Center → "Update elite users" button) so the prediction engine can use `is_elite=TRUE` users. The scheduler also runs this when automated ingestion is enabled.

### Smoother operation (optional)

- **Already in app**: Sync logging, resumable backfill, bulk-insert migration, concurrent token fetches, validate health checks.
- **Optional downloads**: No extra packages required for sync_log or health checks. For heavier retries on bulk writes you could add `tenacity` and wrap DB writes; existing deadlock retry in `ingest_all_markets` is usually enough.
- **Dashboard**: The UI can show last ingestion status from `sync_log` (query `get_last_sync_run`) if you add a small widget; data is already in the DB.

## Scripts in this folder

- **run_ingestion_standalone.py** – Standalone ingestion; use for cron/scheduled runs.
- **disaster_recovery.py** – Restore tables from JSON.gz backups (`--list`, `--date YYYYMMDD`, `--table`, `--dry-run`). Primary recovery is pg_dump/pg_restore; see docs/deployment/RECOVERY.md.
- **load_test_ingestion.py** – Load test bulk_insert_trades (`--trades N`, `--batch B`); reports throughput and batch timing.
- **generate_docs.py** – Generate API docs from docstrings into docs/api/.
- **validate_environment.py** – Pre-flight checks: Python version, DATABASE_URL, deps, disk space, DB ping. Full validation: `python validate.py`.
- **fix_supabase_hosts.bat** – One-off Supabase host fix (Windows).

## Root-level scripts (ad-hoc)

Root-level `verify_*`, `fix_*`, `force_*`, `reset_*`, `nuclear_*`, and similar `.py` files are ad-hoc utilities. Prefer the canonical entry points above for normal operation. Legacy debug/export artifacts live in `archive/artifacts` and `archive/scripts`.
