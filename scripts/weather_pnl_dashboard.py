#!/usr/bin/env python3
"""
WeatherBot P&L Dashboard — Ground truth calculation bypassing corrupted RESOLUTION events.

Usage:
    python scripts/weather_pnl_dashboard.py              # Last 24h
    python scripts/weather_pnl_dashboard.py 48           # Last 48h
    python scripts/weather_pnl_dashboard.py 168          # Last 7d
    python scripts/weather_pnl_dashboard.py all          # All time

Sources ENTRY data from trade_events (immutable), resolution from traded_markets.
Ignores corrupted RESOLUTION events in trade_events entirely.

P&L formula:
  WIN:  (1.0 - avg_entry_price) * remaining_size - remaining_size * fee_rate
  LOSS: -avg_entry_price * remaining_size
"""
import asyncio
import sys
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()

FEE_RATE = 0.015  # 150bps taker fee


async def dashboard(hours: str = "24"):
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        interval_clause = ""
        if hours != "all":
            interval_clause = f"AND r.recorded_at >= NOW() - INTERVAL '{int(hours)} hours'"

        # Ground truth query: ENTRY from trade_events, resolution from traded_markets
        result = await s.execute(text(f"""
            WITH entries AS (
                SELECT market_id, bot_name, side,
                       SUM(size) as total_size,
                       SUM(price * size) / NULLIF(SUM(size), 0) as avg_price
                FROM trade_events
                WHERE bot_name = 'WeatherBot' AND event_type = 'ENTRY'
                GROUP BY market_id, bot_name, side
            ),
            exits AS (
                SELECT market_id, bot_name, side, SUM(size) as exit_size
                FROM trade_events
                WHERE bot_name = 'WeatherBot' AND event_type = 'EXIT'
                GROUP BY market_id, bot_name, side
            ),
            recent_res AS (
                SELECT DISTINCT te.market_id
                FROM trade_events te
                WHERE te.bot_name = 'WeatherBot' AND te.event_type = 'RESOLUTION'
                {interval_clause}
            ),
            pos AS (
                SELECT e.market_id, e.side, e.avg_price,
                       e.total_size - COALESCE(x.exit_size, 0) as remaining,
                       UPPER(tm.resolution) as resolution
                FROM entries e
                JOIN recent_res r ON r.market_id = e.market_id
                LEFT JOIN exits x ON x.market_id = e.market_id AND x.side = e.side
                LEFT JOIN traded_markets tm ON tm.market_id = e.market_id
                WHERE tm.resolution IS NOT NULL
                  AND e.total_size - COALESCE(x.exit_size, 0) > 0
            )
            SELECT side, resolution,
                   COUNT(*) as n,
                   SUM(CASE WHEN side = resolution THEN 1 ELSE 0 END) as wins,
                   ROUND(AVG(avg_price)::numeric, 4) as avg_entry,
                   ROUND(SUM(remaining)::numeric, 2) as total_shares,
                   ROUND(SUM(CASE
                       WHEN side = resolution
                       THEN (1.0 - avg_price) * remaining - remaining * {FEE_RATE}
                       ELSE -avg_price * remaining
                   END)::numeric, 2) as pnl
            FROM pos
            GROUP BY side, resolution
            ORDER BY side, resolution
        """))
        rows = result.fetchall()

        # Summary query
        summary = await s.execute(text(f"""
            WITH entries AS (
                SELECT market_id, bot_name, side,
                       SUM(size) as total_size,
                       SUM(price * size) / NULLIF(SUM(size), 0) as avg_price
                FROM trade_events
                WHERE bot_name = 'WeatherBot' AND event_type = 'ENTRY'
                GROUP BY market_id, bot_name, side
            ),
            exits AS (
                SELECT market_id, bot_name, side, SUM(size) as exit_size
                FROM trade_events
                WHERE bot_name = 'WeatherBot' AND event_type = 'EXIT'
                GROUP BY market_id, bot_name, side
            ),
            recent_res AS (
                SELECT DISTINCT te.market_id
                FROM trade_events te
                WHERE te.bot_name = 'WeatherBot' AND te.event_type = 'RESOLUTION'
                {interval_clause}
            ),
            pos AS (
                SELECT e.market_id, e.side, e.avg_price,
                       e.total_size - COALESCE(x.exit_size, 0) as remaining,
                       UPPER(tm.resolution) as resolution
                FROM entries e
                JOIN recent_res r ON r.market_id = e.market_id
                LEFT JOIN exits x ON x.market_id = e.market_id AND x.side = e.side
                LEFT JOIN traded_markets tm ON tm.market_id = e.market_id
                WHERE tm.resolution IS NOT NULL
                  AND e.total_size - COALESCE(x.exit_size, 0) > 0
            )
            SELECT
                COUNT(*) as positions,
                SUM(CASE WHEN side = resolution THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN side != resolution THEN 1 ELSE 0 END) as losses,
                ROUND((100.0 * SUM(CASE WHEN side = resolution THEN 1 ELSE 0 END) / NULLIF(COUNT(*), 0))::numeric, 1) as wr_pct,
                ROUND(SUM(CASE
                    WHEN side = resolution
                    THEN (1.0 - avg_price) * remaining - remaining * {FEE_RATE}
                    ELSE -avg_price * remaining
                END)::numeric, 2) as clean_pnl
            FROM pos
        """))
        s_row = summary.fetchone()

        # Open positions (unrealized)
        open_pos = await s.execute(text("""
            SELECT p.market_id, p.side, p.size, p.entry_price,
                   p.current_price, p.unrealized_pnl
            FROM positions p
            WHERE (p.bot_id = 'WeatherBot' OR p.source_bot = 'WeatherBot')
              AND p.status = 'open'
            ORDER BY p.unrealized_pnl ASC
        """))
        open_rows = open_pos.fetchall()

        # Exit P&L
        exit_pnl = await s.execute(text(f"""
            SELECT COUNT(*), ROUND(SUM(realized_pnl)::numeric, 2)
            FROM trade_events
            WHERE bot_name = 'WeatherBot' AND event_type = 'EXIT'
            {"AND recorded_at >= NOW() - INTERVAL '" + str(int(hours)) + " hours'" if hours != 'all' else ''}
        """))
        exit_row = exit_pnl.fetchone()

        # Print report
        label = f"last {hours}h" if hours != "all" else "all time"
        print(f"\n{'='*70}")
        print(f"  WeatherBot P&L Dashboard — Ground Truth ({label})")
        print(f"{'='*70}\n")

        print("RESOLVED POSITIONS (by side × outcome):")
        print(f"  {'Side':<6} {'Outcome':<8} {'N':>5} {'Wins':>5} {'AvgEntry':>9} {'Shares':>10} {'P&L':>10}")
        print(f"  {'-'*60}")
        for r in rows:
            print(f"  {r[0]:<6} {r[1]:<8} {r[2]:>5} {r[3]:>5} {float(r[4]):>9.4f} {float(r[5]):>10.2f} {float(r[6]):>10.2f}")

        if s_row and s_row[0]:
            print(f"\n  TOTAL: {s_row[0]} positions | {s_row[1]}W / {s_row[2]}L | {s_row[3]}% WR | ${s_row[4]} P&L")
        else:
            print("\n  No resolved positions in this window.")

        if exit_row and exit_row[0]:
            print(f"\n  EXIT P&L: {exit_row[0]} exits | ${exit_row[1] or 0}")

        if open_rows:
            total_upnl = sum(float(r[5] or 0) for r in open_rows)
            total_cost = sum(float(r[3] or 0) * float(r[2] or 0) for r in open_rows)
            print(f"\n  OPEN: {len(open_rows)} positions | ${total_cost:.2f} deployed | ${total_upnl:.2f} uPnL")
            # Show worst 5
            print(f"\n  WORST 5 OPEN:")
            print(f"  {'Market':<14} {'Side':>4} {'Entry':>7} {'Curr':>7} {'uPnL':>9}")
            for r in open_rows[:5]:
                mid = str(r[0])[:12] + ".."
                print(f"  {mid:<14} {r[1]:>4} {float(r[3] or 0):>7.4f} {float(r[4] or 0):>7.4f} {float(r[5] or 0):>9.2f}")

        print(f"\n{'='*70}")
        print(f"  Formula: WIN = (1-entry)*size - size*{FEE_RATE}")
        print(f"           LOSS = -entry*size")
        print(f"  Source: trade_events ENTRY (immutable) + traded_markets resolution")
        print(f"{'='*70}\n")

    await db.close()


if __name__ == "__main__":
    hours = sys.argv[1] if len(sys.argv) > 1 else "24"
    asyncio.run(dashboard(hours))
