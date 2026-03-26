"""Audit cross-bot position contamination in paper trading engine."""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import text


async def audit():
    # Load DB URL
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env')
    url = None
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith('DATABASE_URL='):
                    url = line.strip().split('=', 1)[1].replace('postgresql://', 'postgresql+asyncpg://')
    if not url:
        url = os.environ.get('DATABASE_URL', '').replace('postgresql://', 'postgresql+asyncpg://')

    engine = create_async_engine(url)
    async with AsyncSession(engine) as s:
        # 1. Markets with positions from multiple bots (all-time)
        r1 = await s.execute(text(
            "SELECT market_id, array_agg(DISTINCT bot_id) as bots, COUNT(DISTINCT bot_id) as bot_count "
            "FROM positions "
            "GROUP BY market_id "
            "HAVING COUNT(DISTINCT bot_id) > 1 "
            "ORDER BY bot_count DESC LIMIT 30"
        ))
        overlaps = r1.fetchall()
        print(f"=== MARKETS WITH MULTI-BOT POSITIONS (all-time): {len(overlaps)} ===")
        for row in overlaps[:15]:
            print(f"  {str(row[0])[:40]} bots={row[1]} count={row[2]}")

        # 2. Currently OPEN with multi-bot overlap
        r2 = await s.execute(text(
            "SELECT market_id, array_agg(DISTINCT bot_id) as bots "
            "FROM positions WHERE status = 'open' "
            "GROUP BY market_id HAVING COUNT(DISTINCT bot_id) > 1"
        ))
        open_overlaps = r2.fetchall()
        print(f"\n=== OPEN POSITIONS WITH MULTI-BOT OVERLAP: {len(open_overlaps)} ===")
        for row in open_overlaps:
            print(f"  {str(row[0])[:40]} bots={row[1]}")

        # 3. Markets where multiple bots have ENTRY trade_events
        r3 = await s.execute(text(
            "SELECT market_id, array_agg(DISTINCT bot_name) as bots "
            "FROM trade_events WHERE event_type = 'ENTRY' "
            "GROUP BY market_id HAVING COUNT(DISTINCT bot_name) > 1 "
            "ORDER BY market_id LIMIT 30"
        ))
        entry_overlaps = r3.fetchall()
        print(f"\n=== MARKETS WITH MULTI-BOT ENTRIES: {len(entry_overlaps)} ===")
        for row in entry_overlaps[:15]:
            print(f"  {str(row[0])[:40]} bots={row[1]}")

        # 4. For each multi-bot market that has EXIT events, check P&L accuracy
        if entry_overlaps:
            market_ids = [row[0] for row in entry_overlaps]
            # Get all EXIT events on these markets
            r4 = await s.execute(text(
                "SELECT te.market_id, te.bot_name, te.price as exit_price, te.size, "
                "te.realized_pnl, te.event_time "
                "FROM trade_events te "
                "WHERE te.event_type = 'EXIT' AND te.market_id = ANY(:mids) "
                "ORDER BY te.event_time DESC"
            ), {"mids": market_ids})
            exits_on_overlap = r4.fetchall()
            print(f"\n=== EXIT EVENTS ON MULTI-BOT MARKETS: {len(exits_on_overlap)} ===")

            # For each exit, find the correct ENTRY for that bot
            for ex in exits_on_overlap:
                mid = ex[0]
                bot = ex[1]
                exit_price = float(ex[2]) if ex[2] else 0
                size = float(ex[3]) if ex[3] else 0
                rpnl = float(ex[4]) if ex[4] else 0

                # Get this bot's entry on this market
                r_entry = await s.execute(text(
                    "SELECT price, side FROM trade_events "
                    "WHERE event_type = 'ENTRY' AND market_id = :mid AND bot_name = :bot "
                    "LIMIT 1"
                ), {"mid": mid, "bot": bot})
                entry_row = r_entry.fetchone()

                # Get OTHER bots' entries on this market
                r_other = await s.execute(text(
                    "SELECT bot_name, price, side FROM trade_events "
                    "WHERE event_type = 'ENTRY' AND market_id = :mid AND bot_name != :bot "
                    "LIMIT 1"
                ), {"mid": mid, "bot": bot})
                other_row = r_other.fetchone()

                if entry_row and other_row:
                    own_entry = float(entry_row[0]) if entry_row[0] else 0
                    other_entry = float(other_row[1]) if other_row[1] else 0
                    other_bot = other_row[0]

                    expected_own = (exit_price - own_entry) * size
                    expected_other = (exit_price - other_entry) * size

                    # Which entry price was actually used?
                    diff_own = abs(rpnl - expected_own)
                    diff_other = abs(rpnl - expected_other)

                    if diff_own < 0.50:
                        status = "CORRECT"
                    elif diff_other < 0.50:
                        status = f"CONTAMINATED (used {other_bot} entry)"
                    else:
                        status = f"UNKNOWN (own_diff={diff_own:.2f}, other_diff={diff_other:.2f})"

                    print(f"  {str(mid)[:22]} bot={bot:14} exit={exit_price:.4f} sz={size:.1f} "
                          f"pnl={rpnl:+.2f} own_entry={own_entry:.4f} other_entry={other_entry:.4f} "
                          f"[{status}]")
                elif entry_row:
                    own_entry = float(entry_row[0]) if entry_row[0] else 0
                    expected = (exit_price - own_entry) * size
                    diff = abs(rpnl - expected)
                    status = "CORRECT" if diff < 0.50 else f"OFF_BY_{diff:.2f}"
                    print(f"  {str(mid)[:22]} bot={bot:14} exit={exit_price:.4f} sz={size:.1f} "
                          f"pnl={rpnl:+.2f} own_entry={own_entry:.4f} [{status}]")

        # 5. Summary: per-bot EXIT P&L
        print(f"\n=== ALL BOTS EXIT P&L SUMMARY ===")
        r5 = await s.execute(text(
            "SELECT bot_name, COUNT(*) as exits, SUM(realized_pnl) as total_pnl "
            "FROM trade_events WHERE event_type = 'EXIT' "
            "GROUP BY bot_name ORDER BY total_pnl DESC"
        ))
        for row in r5.fetchall():
            pnl = float(row[2]) if row[2] else 0
            print(f"  {row[0]:20} exits={row[1]:4} total_pnl=${pnl:+.2f}")

        # 6. Timing: how often do two bots enter the same market within 24h?
        print(f"\n=== TIMING: MULTI-BOT ENTRIES WITHIN 24H ===")
        r6 = await s.execute(text(
            "WITH bot_entries AS ( "
            "  SELECT market_id, bot_name, MIN(event_time) as first_entry "
            "  FROM trade_events WHERE event_type = 'ENTRY' "
            "  GROUP BY market_id, bot_name "
            "), "
            "pairs AS ( "
            "  SELECT a.market_id, a.bot_name as bot_a, b.bot_name as bot_b, "
            "         a.first_entry as time_a, b.first_entry as time_b, "
            "         ABS(EXTRACT(EPOCH FROM (a.first_entry - b.first_entry))) as gap_seconds "
            "  FROM bot_entries a JOIN bot_entries b "
            "  ON a.market_id = b.market_id AND a.bot_name < b.bot_name "
            ") "
            "SELECT market_id, bot_a, bot_b, time_a, time_b, gap_seconds "
            "FROM pairs ORDER BY gap_seconds ASC LIMIT 15"
        ))
        for row in r6.fetchall():
            gap = float(row[5]) if row[5] else 0
            gap_str = f"{gap:.0f}s" if gap < 3600 else f"{gap/3600:.1f}h"
            print(f"  {str(row[0])[:22]} {row[1]:14} vs {row[2]:14} gap={gap_str}")

        # 7. How many positions in positions table total (open vs closed)?
        r7 = await s.execute(text(
            "SELECT status, COUNT(*) FROM positions GROUP BY status"
        ))
        print(f"\n=== POSITIONS TABLE STATUS ===")
        for row in r7.fetchall():
            print(f"  {row[0]:10} count={row[1]}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(audit())
