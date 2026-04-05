#!/usr/bin/env python3
"""
Prune market_prices table — batch delete rows older than retention window.

Usage:
    python scripts/prune_market_prices.py                  # dry-run (default)
    python scripts/prune_market_prices.py --execute        # actually delete
    python scripts/prune_market_prices.py --days 7         # override retention (default 30)
    python scripts/prune_market_prices.py --batch 100000   # override batch size

Designed to run hourly via cron/systemd timer. Batch deletion keeps lock duration
short and avoids WAL bloat that a single unbounded DELETE would cause.

After the first run on a large table, run VACUUM (VERBOSE) market_prices manually
to reclaim dead tuple space. For actual disk reclamation, schedule VACUUM FULL
during a maintenance window (exclusive table lock).

Safety: max lookback across all hot-path queries is 30 days (prediction_engine
feature engineering + market regime). The 180-day Brier training lookback is
acceptable with reduced data — system has <30 days of real trading data anyway.
"""
import argparse
import asyncio
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


async def prune(days: int, batch_size: int, execute: bool, vacuum: bool) -> dict:
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()
    if not db.session_factory:
        print("ERROR: Database not initialized (check DATABASE_URL)")
        return {"error": "no_db"}

    # Count rows to delete
    async with db.get_session() as session:
        r = await session.execute(text(
            "SELECT COUNT(*) FROM market_prices WHERE timestamp < NOW() - make_interval(days => :days)"
        ), {"days": days})
        total_to_delete = r.scalar() or 0

    r2_result = None
    async with db.get_session() as session:
        r2 = await session.execute(text("SELECT COUNT(*) FROM market_prices"))
        r2_result = r2.scalar() or 0

    print(f"Retention: {days} days")
    print(f"Total rows: {r2_result:,}")
    print(f"Rows to delete: {total_to_delete:,}")
    print(f"Rows to keep: {r2_result - total_to_delete:,}")
    print(f"Batch size: {batch_size:,}")
    print(f"Mode: {'EXECUTE' if execute else 'DRY RUN'}")
    print()

    if not execute:
        print("DRY RUN — pass --execute to actually delete")
        await db.close()
        return {"dry_run": True, "to_delete": total_to_delete, "total": r2_result}

    if total_to_delete == 0:
        print("Nothing to delete.")
        await db.close()
        return {"deleted": 0}

    deleted_total = 0
    batch_num = 0
    t0 = time.monotonic()

    while True:
        batch_num += 1
        async with db.get_raw_session() as session:
            # Delete in batches using ctid subquery (avoids index scan for delete target)
            r = await session.execute(text("""
                DELETE FROM market_prices
                WHERE ctid IN (
                    SELECT ctid FROM market_prices
                    WHERE timestamp < NOW() - make_interval(days => :days)
                    LIMIT :batch
                )
            """), {"days": days, "batch": batch_size})
            deleted = r.rowcount
            await session.commit()

        deleted_total += deleted
        elapsed = time.monotonic() - t0
        rate = deleted_total / max(elapsed, 0.1)
        remaining = total_to_delete - deleted_total
        eta = remaining / max(rate, 1)

        print(f"  Batch {batch_num}: deleted {deleted:,} (total: {deleted_total:,}/{total_to_delete:,}, "
              f"{rate:.0f} rows/s, ETA: {eta:.0f}s)")

        if deleted < batch_size:
            break  # No more rows to delete

        # Brief sleep between batches to let other queries through
        await asyncio.sleep(0.1)

    elapsed = time.monotonic() - t0
    print(f"\nDone: deleted {deleted_total:,} rows in {elapsed:.1f}s ({deleted_total/max(elapsed,0.1):.0f} rows/s)")

    if vacuum and deleted_total > 0:
        print("\nRunning VACUUM (VERBOSE) market_prices...")
        print("(This may take several minutes on a large table)")
        try:
            # VACUUM can't run inside a transaction — use raw connection
            async with db.engine.connect() as conn:
                raw = await conn.get_raw_connection()
                await raw.dbapi_connection.execute("VACUUM (VERBOSE) market_prices")
            print("VACUUM complete.")
        except Exception as e:
            print(f"VACUUM failed (run manually): {e}")

    await db.close()
    return {"deleted": deleted_total, "elapsed_s": round(elapsed, 1)}


def main():
    parser = argparse.ArgumentParser(description="Prune old market_prices rows")
    parser.add_argument("--days", type=int, default=30, help="Retention window in days (default: 30)")
    parser.add_argument("--batch", type=int, default=50000, help="Batch size for DELETE (default: 50000)")
    parser.add_argument("--execute", action="store_true", help="Actually delete (default: dry-run)")
    parser.add_argument("--vacuum", action="store_true", help="Run VACUUM after delete")
    args = parser.parse_args()

    result = asyncio.run(prune(args.days, args.batch, args.execute, args.vacuum))
    print(f"\nResult: {result}")
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
