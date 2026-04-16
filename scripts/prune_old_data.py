#!/usr/bin/env python3
"""
2B: Prune old trade_events, reconciliation_breaks, and paper_trades rows.

Follows the same batch ctid-delete pattern as prune_market_prices.py.
Dry-run by default — pass --execute to actually delete.

Safety: trade_events rows are excluded if their market_id has:
  (a) an open or reserving position, OR
  (b) a position opened within the retention window (even if closed —
      bot_pnl.py needs the ENTRY record for P&L of recently-closed trades).

Usage:
    python scripts/prune_old_data.py                               # dry-run all tables
    python scripts/prune_old_data.py --table trade_events --execute # prune trade_events
    python scripts/prune_old_data.py --table all --days 90 --execute
    python scripts/prune_old_data.py --table reconciliation_breaks --days 60 --execute
"""
import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

try:
    from dotenv import load_dotenv
    load_dotenv(_project_root / ".env")
except ImportError:
    pass


# Default retention days per table
DEFAULT_RETENTION = {
    "trade_events": 90,
    "reconciliation_breaks": 60,
    "paper_trades": 90,
}

# Delete queries per table.  trade_events has a safety guard; others are simple date-based.
DELETE_QUERIES = {
    "trade_events": """
        DELETE FROM trade_events
        WHERE ctid IN (
            SELECT te.ctid FROM trade_events te
            WHERE te.recorded_at < NOW() - make_interval(days => :days)
              AND te.market_id NOT IN (
                  SELECT market_id FROM positions
                  WHERE status IN ('open', 'reserving')
                     OR opened_at > NOW() - make_interval(days => :days)
              )
            LIMIT :batch
        )
    """,
    "reconciliation_breaks": """
        DELETE FROM reconciliation_breaks
        WHERE ctid IN (
            SELECT ctid FROM reconciliation_breaks
            WHERE recon_date < CURRENT_DATE - :days
            LIMIT :batch
        )
    """,
    "paper_trades": """
        DELETE FROM paper_trades
        WHERE ctid IN (
            SELECT ctid FROM paper_trades
            WHERE created_at < NOW() - make_interval(days => :days)
            LIMIT :batch
        )
    """,
}

# Existence-check queries for dry-run
EXISTS_QUERIES = {
    "trade_events": """
        SELECT COUNT(*) FROM (
            SELECT 1 FROM trade_events
            WHERE recorded_at < NOW() - make_interval(days => :days)
            LIMIT 1
        ) sub
    """,
    "reconciliation_breaks": """
        SELECT COUNT(*) FROM (
            SELECT 1 FROM reconciliation_breaks
            WHERE recon_date < CURRENT_DATE - :days
            LIMIT 1
        ) sub
    """,
    "paper_trades": """
        SELECT COUNT(*) FROM (
            SELECT 1 FROM paper_trades
            WHERE created_at < NOW() - make_interval(days => :days)
            LIMIT 1
        ) sub
    """,
}


async def prune_table(db, table: str, days: int, batch_size: int, execute: bool) -> dict:
    """Prune a single table. Returns summary dict."""
    from sqlalchemy import text

    print(f"\n--- {table} (retention: {days} days) ---")

    if not execute:
        try:
            async with db.get_raw_session() as session:
                r = await session.execute(text(EXISTS_QUERIES[table]), {"days": days})
                has_old = r.scalar() or 0
        except Exception as e:
            print(f"  DRY RUN check failed: {e}")
            return {"table": table, "error": str(e)}
        print(f"  Old rows exist: {'YES' if has_old else 'NO'}")
        return {"table": table, "dry_run": True, "has_old_data": bool(has_old)}

    deleted_total = 0
    batch_num = 0
    t0 = time.monotonic()

    while True:
        batch_num += 1
        try:
            async with db.get_raw_session() as session:
                await session.execute(text("SET statement_timeout = '300s'"))
                r = await session.execute(
                    text(DELETE_QUERIES[table]),
                    {"days": days, "batch": batch_size},
                )
                deleted = r.rowcount
                await session.commit()
        except Exception as e:
            print(f"  Batch {batch_num} error: {e}")
            await asyncio.sleep(1.0)
            continue

        deleted_total += deleted
        elapsed = time.monotonic() - t0
        rate = deleted_total / max(elapsed, 0.1)

        print(f"  Batch {batch_num}: deleted {deleted:,} (total: {deleted_total:,}, "
              f"{rate:.0f} rows/s, elapsed: {elapsed:.0f}s)")

        if deleted < batch_size:
            break

        await asyncio.sleep(0.1)

    elapsed = time.monotonic() - t0
    print(f"  Done: deleted {deleted_total:,} rows in {elapsed:.1f}s")
    return {"table": table, "deleted": deleted_total, "elapsed_s": round(elapsed, 1)}


async def run(tables: list, days_override: int | None, batch_size: int, execute: bool):
    from base_engine.data.database import Database

    os.environ["DB_STATEMENT_TIMEOUT_MS"] = "120000"
    os.environ["DB_IDLE_IN_TXN_TIMEOUT_MS"] = "120000"

    db = Database()
    await db.init()
    if not db.session_factory:
        print("ERROR: Database not initialized (check DATABASE_URL)")
        return [{"error": "no_db"}]

    print(f"Batch size: {batch_size:,}")
    print(f"Mode: {'EXECUTE' if execute else 'DRY RUN'}")

    results = []
    for table in tables:
        days = days_override if days_override is not None else DEFAULT_RETENTION[table]
        result = await prune_table(db, table, days, batch_size, execute)
        results.append(result)

    await db.close()
    return results


def main():
    parser = argparse.ArgumentParser(description="Prune old trade data rows")
    parser.add_argument(
        "--table",
        choices=["trade_events", "reconciliation_breaks", "paper_trades", "all"],
        default="all",
        help="Which table(s) to prune (default: all)",
    )
    parser.add_argument("--days", type=int, default=None, help="Override retention days (default: per-table)")
    parser.add_argument("--batch", type=int, default=50000, help="Batch size for DELETE (default: 50000)")
    parser.add_argument("--execute", action="store_true", help="Actually delete (default: dry-run)")
    args = parser.parse_args()

    if args.table == "all":
        tables = list(DEFAULT_RETENTION.keys())
    else:
        tables = [args.table]

    results = asyncio.run(run(tables, args.days, args.batch, args.execute))
    print(f"\nResults: {results}")
    return 0 if all("error" not in r for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
