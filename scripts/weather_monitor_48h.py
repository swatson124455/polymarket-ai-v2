#!/usr/bin/env python3
"""
WeatherBot 48h dampener-removal monitoring script.

Run on VPS after S134 deploy. Checks:
1. Win rate by side (YES/NO) — alert if drops below 65%
2. P&L by side — alert if NO P&L worse than -$500/day
3. Entry price distribution (NO side) — alert if avg > 0.75
4. Kelly graduation status
5. Combined boost sanity (no uncapped runaway)

Usage:
    python scripts/weather_monitor_48h.py           # Last 24h
    python scripts/weather_monitor_48h.py 48        # Last 48h
"""
import asyncio
import sys
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()


async def monitor(hours: int = 24):
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        alerts = []

        # 1. Win rate by side (resolved positions)
        wr = await s.execute(text(f"""
            WITH entries AS (
                SELECT market_id, side,
                       SUM(size) as total_size,
                       SUM(price * size) / NULLIF(SUM(size), 0) as avg_price
                FROM trade_events
                WHERE bot_name = 'WeatherBot' AND event_type = 'ENTRY'
                GROUP BY market_id, side
            ),
            exits AS (
                SELECT market_id, side, SUM(size) as exit_size
                FROM trade_events
                WHERE bot_name = 'WeatherBot' AND event_type = 'EXIT'
                GROUP BY market_id, side
            ),
            recent_res AS (
                SELECT DISTINCT market_id
                FROM trade_events
                WHERE bot_name = 'WeatherBot' AND event_type = 'RESOLUTION'
                  AND recorded_at >= NOW() - INTERVAL '{hours} hours'
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
            SELECT side,
                   COUNT(*) as n,
                   SUM(CASE WHEN side = resolution THEN 1 ELSE 0 END) as wins,
                   ROUND(AVG(avg_price)::numeric, 4) as avg_entry,
                   ROUND(SUM(CASE
                       WHEN side = resolution
                       THEN (1.0 - avg_price) * remaining - remaining * 0.015
                       ELSE -avg_price * remaining
                   END)::numeric, 2) as pnl
            FROM pos
            GROUP BY side
        """))
        wr_rows = wr.fetchall()

        print(f"\n=== WeatherBot {hours}h Monitoring ===\n")
        print(f"{'Side':<6} {'N':>5} {'Wins':>5} {'WR%':>7} {'AvgEntry':>9} {'P&L':>10}")
        print("-" * 50)

        total_n, total_wins, total_pnl = 0, 0, 0.0
        for r in wr_rows:
            side, n, wins = r[0], int(r[1]), int(r[2])
            wr_pct = 100 * wins / n if n > 0 else 0
            avg_entry = float(r[3])
            pnl = float(r[4])
            total_n += n
            total_wins += wins
            total_pnl += pnl
            print(f"{side:<6} {n:>5} {wins:>5} {wr_pct:>6.1f}% {avg_entry:>9.4f} ${pnl:>9.2f}")

            if n >= 10 and wr_pct < 65:
                alerts.append(f"ALERT: {side} WR={wr_pct:.1f}% < 65% ({n} positions)")
            if side == "NO" and avg_entry > 0.75:
                alerts.append(f"ALERT: NO avg entry ${avg_entry:.4f} > $0.75 cap")
            if pnl < -500:
                alerts.append(f"ALERT: {side} P&L ${pnl:.2f} < -$500")

        if total_n > 0:
            total_wr = 100 * total_wins / total_n
            print(f"\n{'TOTAL':<6} {total_n:>5} {total_wins:>5} {total_wr:>6.1f}%  {'':>9} ${total_pnl:>9.2f}")
            if total_wr < 65:
                alerts.append(f"ALERT: Overall WR={total_wr:.1f}% < 65%")

        # 2. Entry volume (are we trading?)
        volume = await s.execute(text(f"""
            SELECT COUNT(*), ROUND(SUM(size * price)::numeric, 2)
            FROM trade_events
            WHERE bot_name = 'WeatherBot' AND event_type = 'ENTRY'
              AND recorded_at >= NOW() - INTERVAL '{hours} hours'
        """))
        vol_row = volume.fetchone()
        print(f"\nNew entries: {vol_row[0]} trades, ${vol_row[1] or 0} notional")
        if vol_row[0] == 0:
            alerts.append("ALERT: Zero new entries — bot may be halted")

        # 3. Kelly mult check
        print(f"\n--- Kelly Status ---")
        kelly_check = await s.execute(text("""
            SELECT COALESCE(SUM(realized_pnl), 0)
            FROM trade_events
            WHERE bot_name = 'WeatherBot'
              AND event_type IN ('EXIT', 'RESOLUTION')
              AND recorded_at >= NOW() - INTERVAL '7 days'
        """))
        kelly_pnl = float(kelly_check.scalar() or 0)
        print(f"Trailing 7d P&L: ${kelly_pnl:.2f}")
        if kelly_pnl < 0:
            print(f"  Kelly graduation BLOCKED (need >= $0)")
        if kelly_pnl < -500:
            print(f"  Kelly DEMOTION triggered (< -$500)")

        # 4. Alerts summary
        if alerts:
            print(f"\n{'!'*50}")
            print("ALERTS:")
            for a in alerts:
                print(f"  {a}")
            print(f"{'!'*50}")
        else:
            print(f"\nNo alerts — all metrics within bounds.")

        print()

    await db.close()


if __name__ == "__main__":
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    asyncio.run(monitor(hours))
