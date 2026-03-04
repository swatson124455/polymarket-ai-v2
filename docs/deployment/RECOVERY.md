# Disaster Recovery

Primary recovery path is **pg_dump + pg_restore**. See [BACKUP.md](BACKUP.md) for backup procedures and restore steps.

## Primary: PostgreSQL restore

1. Stop the application.
2. Restore with pg_restore:
   ```bash
   pg_restore -d "$DATABASE_URL" -c /path/to/polymarket_YYYYMMDD_HHMMSS.dump
   ```
3. Verify: `psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM markets;"`
4. Restart the application.

**Supabase:** Use Dashboard → Database → Backups (Pro: point-in-time recovery).

## Optional: Table-level restore from JSON exports

If you export tables to JSON (e.g. for portability or partial restore), use the restore script:

```bash
# List available backups (directories containing *.json.gz)
python scripts/disaster_recovery.py --list

# Dry run (show what would be restored)
python scripts/disaster_recovery.py --date YYYYMMDD --dry-run

# Restore specific table
python scripts/disaster_recovery.py --date YYYYMMDD --table markets

# Restore all supported tables (markets, trades, market_prices)
python scripts/disaster_recovery.py --date YYYYMMDD
```

Backup directory layout expected: `backups/YYYYMMDD/markets.json.gz`, `trades.json.gz`, `market_prices.json.gz`.  
JSON files should be arrays of row dicts matching the schema (e.g. `id`, `market_id`, `question` for markets).

**Warning:** Restore **replaces** existing data for the selected table(s). Confirm with `RESTORE` when prompted.
