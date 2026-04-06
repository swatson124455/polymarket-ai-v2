#!/usr/bin/env python3
"""
Reconstruct historical stop-loss P&L from trade_events EXIT records.

S155 fixed backfill_positions_resolution() from erasing stop-loss P&L,
but positions closed before the fix already have unrealized_pnl=0.
EXIT events in trade_events retain the correct realized_pnl.

Usage:
    python scripts/reconstruct_exit_pnl.py              # Dry run (default)
    python scripts/reconstruct_exit_pnl.py --apply       # Apply updates
"""
import asyncio
import sys
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()


async def reconstruct(apply: bool = False):
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        # Find closed positions with zeroed P&L that have EXIT events with real P&L.
        # SUM all matching EXIT realized_pnl per position (handles partial exits).
        r = await s.execute(text("""
            SELECT p.id, p.market_id, p.side, p.bot_id, p.source_bot,
                   p.entry_price, p.size, p.unrealized_pnl,
                   ex.exit_count, ex.total_exit_pnl
            FROM positions p
            JOIN (
                SELECT market_id,
                       COALESCE(bot_name, '') AS bot_name,
                       COUNT(*) AS exit_count,
                       SUM(CAST(realized_pnl AS DOUBLE PRECISION)) AS total_exit_pnl
                FROM trade_events
                WHERE event_type = 'EXIT'
                  AND realized_pnl IS NOT NULL
                  AND CAST(realized_pnl AS DOUBLE PRECISION) != 0
                GROUP BY market_id, bot_name
            ) ex ON ex.market_id = p.market_id
                AND ex.bot_name IN (p.bot_id, COALESCE(p.source_bot, ''))
            WHERE p.status = 'closed'
              AND (p.unrealized_pnl = 0 OR p.unrealized_pnl IS NULL)
            ORDER BY ex.total_exit_pnl DESC
        """))
        rows = r.fetchall()

        if not rows:
            print("No positions found with zeroed P&L and matching EXIT events.")
            await db.close()
            return

        # Flag multi-exit positions
        multi_exit = [r for r in rows if r[8] > 1]
        single_exit = [r for r in rows if r[8] == 1]

        print(f"=== P&L Reconstruction {'(DRY RUN)' if not apply else '(APPLYING)'} ===\n")
        print(f"Positions with zeroed P&L + EXIT events: {len(rows)}")
        print(f"  Single-exit positions: {len(single_exit)}")
        print(f"  Multi-exit positions:  {len(multi_exit)}")

        if multi_exit:
            print(f"\n  MULTI-EXIT POSITIONS (verify SUM is correct):")
            print(f"  {'ID':<8} {'Market':<14} {'Side':<4} {'Bot':<12} {'Exits':>5} {'SUM P&L':>10}")
            print(f"  {'-'*55}")
            for r in multi_exit:
                mid = r[1][:12] + ".." if len(r[1]) > 12 else r[1]
                bot = r[4] or r[3]
                print(f"  {r[0]:<8} {mid:<14} {r[2]:<4} {bot:<12} {r[8]:>5} ${float(r[9]):>+9.2f}")

        total_pnl = sum(float(r[9]) for r in rows)
        print(f"\n  Total P&L to restore: ${total_pnl:+.2f}")

        # Sample first 20
        print(f"\n  Sample (first 20):")
        print(f"  {'ID':<8} {'Market':<14} {'Side':<4} {'Bot':<12} {'Entry':>7} {'Size':>8} {'Exit P&L':>10}")
        print(f"  {'-'*65}")
        for r in rows[:20]:
            mid = r[1][:12] + ".." if len(r[1]) > 12 else r[1]
            bot = r[4] or r[3]
            print(f"  {r[0]:<8} {mid:<14} {r[2]:<4} {bot:<12} {float(r[5]):>7.4f} {float(r[6]):>8.1f} ${float(r[9]):>+9.2f}")

        if apply:
            updated = 0
            for r in rows:
                await s.execute(text("""
                    UPDATE positions SET unrealized_pnl = :pnl
                    WHERE id = :pid AND (unrealized_pnl = 0 OR unrealized_pnl IS NULL)
                """), {"pnl": float(r[9]), "pid": r[0]})
                updated += 1
            await s.commit()
            print(f"\n  APPLIED: Updated {updated} positions.")
        else:
            print(f"\n  DRY RUN — no changes made. Run with --apply to update.")

    await db.close()


if __name__ == "__main__":
    apply = "--apply" in sys.argv
    asyncio.run(reconstruct(apply=apply))
