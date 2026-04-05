#!/usr/bin/env python3
"""
Prune market_prices table — batch delete rows older than retention window.

Usage:
    python scripts/prune_market_prices.py                  # dry-run (shows estimate)
    python scripts/prune_market_prices.py --execute        # actually delete
    python scripts/prune_market_prices.py --days 7         # override retention (default 30)
    python scripts/prune_market_prices.py --batch 100000   # override batch size

Runs hourly via systemd timer (deploy/polymarket-prune-prices.timer).
Batch deletion with 100ms sleep between batches keeps lock duration short.

After initial bulk purge, run VACUUM FULL during maintenance window.
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


async def prune(days: int, batch_size: int, execute: bool) -> dict:
    from base_engine.data.database import Database
    from sqlalchemy import text

    # Override statement timeout for this process — maintenance needs more time
    os.environ["DB_STATEMENT_TIMEOUT_MS"] = "120000"  # 2 minutes per query
    os.environ["DB_IDLE_IN_TXN_TIMEOUT_MS"] = "120000"

    db = Database()
    await db.init()
    if not db.session_factory:
        print("ERROR: Database not initialized (check DATABASE_URL)")
        return {"error": "no_db"}

    print(f"Retention: {days} days")
    print(f"Batch size: {batch_size:,}")
    print(f"Mode: {'EXECUTE' if execute else 'DRY RUN'}")
    print()

    if not execute:
        # Dry run: just try one batch to see if there's data to delete
        async with db.get_raw_session() as session:
            r = await session.execute(text("""
                SELECT COUNT(*) FROM (
                    SELECT 1 FROM market_prices
                    WHERE timestamp < NOW() - make_interval(days => :days)
                    LIMIT 1
                ) sub
            """), {"days": days})
            has_old = r.scalar() or 0
        print(f"Old rows exist: {'YES' if has_old else 'NO'}")
        print("DRY RUN — pass --execute to actually delete")
        await db.close()
        return {"dry_run": True, "has_old_data": bool(has_old)}

    deleted_total = 0
    batch_num = 0
    t0 = time.monotonic()

    while True:
        batch_num += 1
        try:
            async with db.get_raw_session() as session:
                # Override PgBouncer-inherited 30s timeout for this maintenance query
                await session.execute(text("SET statement_timeout = '300s'"))
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
        except Exception as e:
            print(f"  Batch {batch_num} error: {e}")
            # Brief pause then retry
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
    print(f"\nDone: deleted {deleted_total:,} rows in {elapsed:.1f}s")
    await db.close()
    return {"deleted": deleted_total, "elapsed_s": round(elapsed, 1)}


def main():
    parser = argparse.ArgumentParser(description="Prune old market_prices rows")
    parser.add_argument("--days", type=int, default=30, help="Retention window in days (default: 30)")
    parser.add_argument("--batch", type=int, default=50000, help="Batch size for DELETE (default: 50000)")
    parser.add_argument("--execute", action="store_true", help="Actually delete (default: dry-run)")
    args = parser.parse_args()

    result = asyncio.run(prune(args.days, args.batch, args.execute))
    print(f"\nResult: {result}")
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    sys.exit(main())
