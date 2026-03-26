#!/usr/bin/env python3
"""S134: Delete inflated/orphan RESOLUTION events from trade_events_2026_03.

Root cause: Resolution backfill Phase 4b used paper_trades.size (which gets
overwritten by position accumulation via UPSERT) instead of the original ENTRY
event size from trade_events. This inflated RESOLUTION event sizes, creating
phantom P&L losses. Additionally, some RESOLUTION events were emitted for
positions that were fully exited or had no ENTRY events at all.

Scope: ALL bots (MirrorBot, WeatherBot, EsportsBot, EnsembleBot).

Deletes:
  1. Orphan RESOLUTIONs — no matching ENTRY or position fully exited before resolution
  2. Inflated RESOLUTIONs — RESOLUTION size > 1.1x the correct remaining size

After cleanup, the backfill scheduler re-emits correct RESOLUTION events using
the fixed Phase 4b query (S134: sources from trade_events ENTRY, not paper_trades).

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
    from dotenv import load_dotenv
    load_dotenv()

    db = Database()
    await db.init()

    try:
        async with db.get_session() as session:
            from sqlalchemy import text

            # Find all bad RESOLUTION events:
            # 1. Orphans (no ENTRY or fully exited)
            # 2. Inflated (size > 1.1x correct remaining)
            result = await session.execute(text("""
                WITH res AS (
                    SELECT sequence_num, market_id, bot_name, side, size,
                           realized_pnl, event_time
                    FROM trade_events_2026_03
                    WHERE event_type = 'RESOLUTION'
                ),
                entry_agg AS (
                    SELECT market_id, bot_name, side,
                           SUM(size) as total_entry_size
                    FROM trade_events_2026_03
                    WHERE event_type = 'ENTRY'
                    GROUP BY market_id, bot_name, side
                ),
                exit_agg AS (
                    SELECT market_id, bot_name,
                           SUM(size) as total_exit_size
                    FROM trade_events_2026_03
                    WHERE event_type = 'EXIT'
                    GROUP BY market_id, bot_name
                ),
                remaining AS (
                    SELECT e.market_id, e.bot_name, e.side,
                           e.total_entry_size - COALESCE(x.total_exit_size, 0)
                             as correct_size
                    FROM entry_agg e
                    LEFT JOIN exit_agg x
                      ON x.market_id = e.market_id AND x.bot_name = e.bot_name
                )
                SELECT r.sequence_num, r.bot_name, r.market_id, r.side,
                       r.size as res_size,
                       COALESCE(rm.correct_size, 0) as correct_size,
                       r.realized_pnl,
                       CASE
                         WHEN rm.correct_size IS NULL OR rm.correct_size <= 0
                           THEN 'orphan'
                         WHEN r.size / rm.correct_size > 1.1
                           THEN 'inflated'
                         ELSE 'ok'
                       END as reason
                FROM res r
                LEFT JOIN remaining rm
                  ON r.market_id = rm.market_id
                 AND r.bot_name = rm.bot_name
                 AND r.side = rm.side
                WHERE rm.correct_size IS NULL
                   OR rm.correct_size <= 0
                   OR r.size / rm.correct_size > 1.1
                ORDER BY r.bot_name, ABS(COALESCE(r.realized_pnl, 0)) DESC
            """))

            rows = result.fetchall()

            if not rows:
                print("No inflated/orphan RESOLUTION events found. Nothing to clean.")
                return

            # Summary by bot and reason
            from collections import defaultdict
            by_bot = defaultdict(lambda: {"orphan": 0, "inflated": 0, "pnl": 0.0})
            seq_nums = []
            for r in rows:
                seq_num, bot, market, side, res_size, correct_size, pnl, reason = r
                by_bot[bot][reason] += 1
                by_bot[bot]["pnl"] += float(pnl or 0)
                seq_nums.append(seq_num)

            total_phantom = sum(v["pnl"] for v in by_bot.values())
            print(f"\nFound {len(rows)} bad RESOLUTION events to delete:")
            print(f"\n{'Bot':<15} {'Orphan':>8} {'Inflated':>10} {'Phantom P&L':>12}")
            print("-" * 50)
            for bot, v in sorted(by_bot.items()):
                print(f"{bot:<15} {v['orphan']:>8} {v['inflated']:>10} ${v['pnl']:>11.2f}")
            print(f"{'TOTAL':<15} {sum(v['orphan'] for v in by_bot.values()):>8} "
                  f"{sum(v['inflated'] for v in by_bot.values()):>10} ${total_phantom:>11.2f}")

            # Show worst 10
            print(f"\nWorst 10 by |P&L|:")
            print(f"{'Bot':<12} {'Market':<14} {'Side':>4} {'ResSize':>9} {'Correct':>9} {'Reason':<9} {'P&L':>10}")
            print("-" * 75)
            worst = sorted(rows, key=lambda r: abs(float(r[6] or 0)), reverse=True)[:10]
            for r in worst:
                mid = str(r[2])[:12] + ".."
                print(f"{r[1]:<12} {mid:<14} {r[3]:>4} {float(r[4]):>9.1f} "
                      f"{float(r[5]):>9.1f} {r[7]:<9} ${float(r[6] or 0):>9.2f}")

            if dry_run:
                print(f"\n[DRY RUN] Would delete {len(seq_nums)} events, "
                      f"removing ${total_phantom:.2f} phantom P&L.")
                return

            # Confirm
            print(f"\nAbout to DELETE {len(seq_nums)} RESOLUTION events.")
            print(f"This removes ${total_phantom:.2f} phantom P&L.")
            print(f"Phase 4b (now fixed) will re-emit correct events on next backfill run.")

            # Disable immutability trigger
            print("\nDisabling immutability trigger...")
            await session.execute(text(
                "ALTER TABLE trade_events_2026_03 "
                "DISABLE TRIGGER trg_trade_events_immutable"
            ))

            # Delete in batches of 500 (asyncpg array limit)
            deleted = 0
            for i in range(0, len(seq_nums), 500):
                batch = seq_nums[i:i+500]
                await session.execute(text(
                    "DELETE FROM trade_events_2026_03 "
                    "WHERE sequence_num = ANY(CAST(:seqs AS bigint[]))"
                ), {"seqs": batch})
                deleted += len(batch)
                print(f"  Deleted batch {i//500 + 1}: {len(batch)} events "
                      f"({deleted}/{len(seq_nums)})")

            # Re-enable trigger
            print("Re-enabling immutability trigger...")
            await session.execute(text(
                "ALTER TABLE trade_events_2026_03 "
                "ENABLE TRIGGER trg_trade_events_immutable"
            ))

            await session.commit()
            print(f"\nDone. Deleted {deleted} inflated/orphan RESOLUTION events.")
            print(f"Phantom P&L removed: ${total_phantom:.2f}")
            print(f"Backfill will re-emit correct RESOLUTION events on next run.")

    finally:
        await db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Cleanup inflated/orphan RESOLUTION events from trade_events"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be deleted without changing anything"
    )
    args = parser.parse_args()
    asyncio.run(main(dry_run=args.dry_run))
