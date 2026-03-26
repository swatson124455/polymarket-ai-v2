#!/usr/bin/env python3
"""S131: One-time cleanup of 15 inflated RESOLUTION events in WeatherBot.

Root cause: Resolution backfill Phase 4b used paper_trades.size (which gets
overwritten by position accumulation via UPSERT) instead of the original ENTRY
event size. This inflated RESOLUTION event sizes by 10-278x, creating -$2,697
in phantom P&L.

Steps:
1. Disable immutability trigger on trade_events_2026_03
2. DELETE the 15 inflated RESOLUTION events (identified by size ratio > 5x vs ENTRY)
3. Re-enable immutability trigger
4. Let the backfill scheduler re-emit correct RESOLUTION events on next run

Run on VPS:  python scripts/cleanup_phantom_resolutions.py
Dry-run:     python scripts/cleanup_phantom_resolutions.py --dry-run
"""
import asyncio
import argparse
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main(dry_run: bool = False):
    from base_engine.data.database import Database
    from config import settings

    db = Database(settings.DATABASE_URL)
    await db.initialize()

    try:
        async with db.get_session() as session:
            from sqlalchemy import text as sa_text

            # Step 1: Find inflated RESOLUTION events
            # Compare RESOLUTION size to max ENTRY size for same (market_id, bot_name)
            result = await session.execute(sa_text("""
                WITH res AS (
                    SELECT id, market_id, bot_name, size, realized_pnl,
                           event_data, recorded_at
                    FROM trade_events_2026_03
                    WHERE bot_name = 'WeatherBot'
                      AND event_type = 'RESOLUTION'
                ),
                entry_max AS (
                    SELECT market_id, bot_name, MAX(size) as max_entry_size
                    FROM trade_events_2026_03
                    WHERE bot_name = 'WeatherBot'
                      AND event_type = 'ENTRY'
                    GROUP BY market_id, bot_name
                )
                SELECT r.id, r.market_id, r.size as res_size,
                       COALESCE(e.max_entry_size, 0) as entry_size,
                       r.realized_pnl,
                       CASE WHEN e.max_entry_size > 0
                            THEN r.size / e.max_entry_size
                            ELSE 999 END as size_ratio
                FROM res r
                LEFT JOIN entry_max e ON r.market_id = e.market_id
                                      AND r.bot_name = e.bot_name
                WHERE CASE WHEN e.max_entry_size > 0
                           THEN r.size / e.max_entry_size
                           ELSE 999 END > 5.0
                ORDER BY size_ratio DESC
            """))

            rows = result.fetchall()

            if not rows:
                print("No inflated RESOLUTION events found. Nothing to clean.")
                return

            total_phantom_pnl = sum(float(r[4] or 0) for r in rows)
            print(f"\nFound {len(rows)} inflated RESOLUTION events:")
            print(f"{'ID':<10} {'Market':<20} {'Res Size':>10} {'Entry Size':>10} {'Ratio':>8} {'P&L':>10}")
            print("-" * 75)
            for r in rows:
                print(f"{r[0]:<10} {str(r[1])[:20]:<20} {r[2]:>10.1f} {r[3]:>10.1f} {r[5]:>8.1f}x {float(r[4] or 0):>10.2f}")
            print(f"\nTotal phantom P&L: ${total_phantom_pnl:.2f}")

            if dry_run:
                print("\n[DRY RUN] No changes made.")
                return

            ids_to_delete = [r[0] for r in rows]

            # Step 2: Disable immutability trigger
            print("\nDisabling immutability trigger...")
            await session.execute(sa_text(
                "ALTER TABLE trade_events_2026_03 DISABLE TRIGGER trg_trade_events_immutable"
            ))

            # Step 3: Delete inflated events
            print(f"Deleting {len(ids_to_delete)} inflated RESOLUTION events...")
            await session.execute(sa_text(
                "DELETE FROM trade_events_2026_03 WHERE id = ANY(:ids)"
            ), {"ids": ids_to_delete})

            # Step 4: Re-enable trigger
            print("Re-enabling immutability trigger...")
            await session.execute(sa_text(
                "ALTER TABLE trade_events_2026_03 ENABLE TRIGGER trg_trade_events_immutable"
            ))

            await session.commit()
            print(f"\n✓ Deleted {len(ids_to_delete)} inflated RESOLUTION events.")
            print(f"✓ Phantom P&L removed: ${total_phantom_pnl:.2f}")
            print(f"✓ Backfill will re-emit correct RESOLUTION events on next run.")

    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Cleanup inflated WeatherBot RESOLUTION events")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without changing anything")
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
