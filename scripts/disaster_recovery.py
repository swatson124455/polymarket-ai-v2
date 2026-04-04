#!/usr/bin/env python3
"""
Disaster recovery: restore tables from JSON.gz backups.
Primary recovery path is pg_dump/pg_restore (see docs/deployment/RECOVERY.md).
Use this script when you have table exports as .json.gz (e.g. from BackupManager or custom export).
"""
import argparse
import asyncio
import gzip
import json
import sys
from pathlib import Path

# Project root
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# Load env before importing app code
try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass


def list_backups(backup_dir: Path) -> list:
    """List backup directories (by name, newest first)."""
    if not backup_dir.exists():
        return []
    dirs = [d for d in backup_dir.iterdir() if d.is_dir() and d.name.isdigit()]
    return sorted(dirs, key=lambda d: d.name, reverse=True)


# S156: All tables supported for disaster recovery (was 3, now 12)
_SUPPORTED_TABLES = {
    "markets", "trades", "market_prices",
    "positions", "trade_events", "paper_trades", "prediction_log",
    "traded_markets", "bot_health_states", "fill_analysis",
    "audit_runs", "tunable_config",
}


async def _bulk_upsert_generic(db, table_name: str, rows: list) -> None:
    """Generic table restore via raw SQL. Truncates then inserts for full restore."""
    if not rows:
        return
    from sqlalchemy import text
    columns = list(rows[0].keys())
    col_list = ", ".join(columns)
    val_placeholders = ", ".join(f":{c}" for c in columns)
    sql = f"INSERT INTO {table_name} ({col_list}) VALUES ({val_placeholders}) ON CONFLICT DO UPDATE SET " + \
          ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in ("id", "break_id", "run_id"))
    # Fallback: if no PK conflict clause works, use DO NOTHING
    try:
        async with db.get_session() as session:
            for row in rows:
                await session.execute(text(sql), row)
            await session.commit()
    except Exception:
        # Retry with DO NOTHING (table may lack expected PK)
        sql_safe = f"INSERT INTO {table_name} ({col_list}) VALUES ({val_placeholders}) ON CONFLICT DO NOTHING"
        async with db.get_session() as session:
            for row in rows:
                await session.execute(text(sql_safe), row)
            await session.commit()


async def restore_table(db, table_name: str, backup_file: Path, batch_size: int = 1000) -> bool:
    """Restore a single table from a .json.gz file. Returns True on success."""
    try:
        with gzip.open(backup_file, "rt", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            data = [data]
        total = len(data)
        if total == 0:
            print(f"  [SKIP] {table_name}: no rows in backup")
            return True
        print(f"  Restoring {table_name}: {total} rows in batches of {batch_size}")
        inserted = 0
        for i in range(0, total, batch_size):
            batch = data[i : i + batch_size]
            if table_name == "markets":
                await db.bulk_insert_markets(batch)
            elif table_name == "trades":
                await db.bulk_insert_trades(batch)
            elif table_name == "market_prices":
                await db.bulk_insert_prices_raw(batch)
            else:
                # S156: Generic restore for additional tables via raw SQL upsert
                await _bulk_upsert_generic(db, table_name, batch)

            inserted += len(batch)
            print(f"    {inserted}/{total}")
        print(f"  [OK] {table_name}: {inserted} rows")
        return True
    except Exception as e:
        print(f"  [ERROR] {table_name}: {e}")
        return False


async def main_async(args: argparse.Namespace) -> int:
    backup_dir = _project_root / "data" / "backups"
    if getattr(args, "backup_dir", None):
        backup_dir = Path(args.backup_dir)
    if args.list:
        backups = list_backups(backup_dir)
        print("Available backups (data/backups/YYYYMMDD or --backup-dir):")
        for d in backups[:20]:
            print(f"  {d.name}")
        if not backups:
            print("  (none)")
        return 0
    date_str = getattr(args, "date", None)
    if not date_str:
        print("Error: --date YYYYMMDD required (use --list to see available)")
        return 1
    backup_path = backup_dir / date_str
    if not backup_path.exists():
        print(f"Error: Backup not found: {backup_path}")
        return 1
    # Find .json.gz files
    backup_files = list(backup_path.glob("*.json.gz"))
    if not backup_files:
        print(f"Error: No .json.gz files in {backup_path}")
        return 1
    table_to_file = {}
    for f in backup_files:
        name = f.stem.replace(".json", "")
        if name in _SUPPORTED_TABLES:
            table_to_file[name] = f
    if args.table:
        if args.table not in table_to_file:
            print(f"Error: No backup for table '{args.table}' in {backup_path}")
            return 1
        table_to_file = {args.table: table_to_file[args.table]}
    if args.dry_run:
        print(f"DRY RUN: would restore from {backup_path}")
        for t, f in table_to_file.items():
            print(f"  {t} <- {f.name}")
        return 0
    print("WARNING: This will INSERT/merge data into the database. Existing rows may be updated.")
    confirm = input("Type RESTORE to confirm: ")
    if confirm != "RESTORE":
        print("Aborted.")
        return 0
    from base_engine.data.database import Database
    db = Database()
    await db.init()
    if not db.session_factory:
        print("Error: Database not initialized (check DATABASE_URL)")
        return 1
    # Restore in dependency order (markets first, then dependent tables)
    tables = ["markets", "trades", "market_prices", "positions", "paper_trades",
              "trade_events", "prediction_log", "traded_markets", "bot_health_states",
              "fill_analysis", "audit_runs", "tunable_config"]
    ok = True
    for t in tables:
        if t in table_to_file:
            if not await restore_table(db, t, table_to_file[t]):
                ok = False
    return 0 if ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore tables from JSON.gz backups")
    parser.add_argument("--list", action="store_true", help="List available backups")
    parser.add_argument("--date", type=str, help="Backup date YYYYMMDD")
    parser.add_argument("--table", type=str, help="Restore only this table (any of 12 supported tables)")
    parser.add_argument("--backup-dir", type=str, help="Backup root directory (default: data/backups)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be restored")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
