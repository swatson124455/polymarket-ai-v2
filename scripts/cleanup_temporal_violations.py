#!/usr/bin/env python3
"""
S142: Delete prediction_log rows where resolved_at < prediction_time.

These are predictions made on markets that had already closed.  The temporal
ordering guard in backfill_prediction_log_resolution() correctly excludes them
from labeling, but they cause a warning to fire every ~0.5s.  Deleting them
removes the spam at its source.

Run: python scripts/cleanup_temporal_violations.py [--dry-run]
"""
import asyncio, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()

async def go():
    dry_run = "--dry-run" in sys.argv
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        # Count violations
        r = await s.execute(text("""
            SELECT COUNT(*) FROM prediction_log pl
            JOIN markets m ON pl.market_id = CAST(m.id AS TEXT)
            WHERE m.resolution IN ('YES', 'NO')
            AND m.resolved_at IS NOT NULL
            AND pl.prediction_time IS NOT NULL
            AND m.resolved_at < pl.prediction_time
        """))
        count = r.scalar_one_or_none() or 0
        print(f"Found {count} prediction_log rows with resolved_at < prediction_time")

        if count == 0:
            print("Nothing to clean up.")
            return

        if dry_run:
            # Show sample rows
            r2 = await s.execute(text("""
                SELECT pl.market_id, pl.prediction_time, m.resolved_at, pl.bot_name,
                       pl.model_name, pl.predicted_prob
                FROM prediction_log pl
                JOIN markets m ON pl.market_id = CAST(m.id AS TEXT)
                WHERE m.resolution IN ('YES', 'NO')
                AND m.resolved_at IS NOT NULL
                AND pl.prediction_time IS NOT NULL
                AND m.resolved_at < pl.prediction_time
                ORDER BY pl.prediction_time DESC
                LIMIT 5
            """))
            print("\nSample rows (newest first):")
            for row in r2.fetchall():
                print(f"  market={row[0][:20]}  predicted={row[1]}  resolved={row[2]}  bot={row[3]}")
            print(f"\n--dry-run: would delete {count} rows. Run without --dry-run to execute.")
            return

        # Delete
        r3 = await s.execute(text("""
            DELETE FROM prediction_log pl
            USING markets m
            WHERE pl.market_id = CAST(m.id AS TEXT)
            AND m.resolution IN ('YES', 'NO')
            AND m.resolved_at IS NOT NULL
            AND pl.prediction_time IS NOT NULL
            AND m.resolved_at < pl.prediction_time
        """))
        deleted = r3.rowcount or 0
        await s.commit()
        print(f"Deleted {deleted} temporal-violation rows from prediction_log.")

if __name__ == "__main__":
    asyncio.run(go())
