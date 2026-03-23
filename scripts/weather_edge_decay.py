#!/usr/bin/env python3
"""
WeatherBot Edge Decay Tracker — weekly edge and profitability trends.

Usage:
    python scripts/weather_edge_decay.py          # All time
    python scripts/weather_edge_decay.py 720      # Last 720 hours (30 days)

Tracks: avg edge, trade count, realized P&L, and $/trade per week.
Used to monitor whether EMOS window fix (S120) and other changes improve edge retention.
"""
import asyncio
import sys
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()


async def edge_decay(hours: int = 0):
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        time_filter = ""
        if hours > 0:
            time_filter = f"AND e.event_time > NOW() - INTERVAL '{hours} hours'"

        # Get ENTRY events with confidence and price (edge = confidence - price)
        rows = await s.execute(text(f"""
            SELECT
                DATE_TRUNC('week', e.event_time) AS week,
                e.price AS entry_price,
                CAST(e.event_data->>'confidence' AS FLOAT) AS confidence,
                e.side
            FROM trade_events e
            WHERE e.bot_name = 'WeatherBot'
              AND e.event_type = 'ENTRY'
              {time_filter}
            ORDER BY week
        """))
        entries = rows.fetchall()

        # Get realized P&L from RESOLUTION events
        res_rows = await s.execute(text(f"""
            SELECT
                DATE_TRUNC('week', r.event_time) AS week,
                SUM(r.realized_pnl) AS total_pnl,
                COUNT(*) AS n_resolved,
                SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) AS n_wins
            FROM trade_events r
            WHERE r.bot_name = 'WeatherBot'
              AND r.event_type = 'RESOLUTION'
              {time_filter}
            GROUP BY DATE_TRUNC('week', r.event_time)
            ORDER BY week
        """))
        resolutions = {str(r[0])[:10]: (float(r[1] or 0), int(r[2]), int(r[3])) for r in res_rows.fetchall()}

    if not entries:
        print("No WeatherBot ENTRY events found.")
        return

    # Group entries by week
    weeks = {}
    for week, entry_price, confidence, side in entries:
        week_key = str(week)[:10]
        if week_key not in weeks:
            weeks[week_key] = {"n": 0, "edges": [], "sides": {"YES": 0, "NO": 0}}
        if confidence is not None and entry_price is not None:
            edge = float(confidence) - float(entry_price)
            weeks[week_key]["edges"].append(edge)
            weeks[week_key]["n"] += 1
            weeks[week_key]["sides"][side] = weeks[week_key]["sides"].get(side, 0) + 1

    # Print results
    print(f"\n{'Week':<12} {'Entries':>8} {'Avg Edge':>10} {'YES/NO':>8} {'Resolved':>9} {'WR':>6} {'P&L':>10} {'$/trade':>8}")
    print("-" * 80)
    for week_key in sorted(weeks.keys()):
        w = weeks[week_key]
        avg_edge = sum(w["edges"]) / len(w["edges"]) if w["edges"] else 0
        yes_no = f"{w['sides'].get('YES', 0)}/{w['sides'].get('NO', 0)}"
        pnl, n_res, n_wins = resolutions.get(week_key, (0.0, 0, 0))
        wr = n_wins / n_res * 100 if n_res > 0 else 0
        avg_pnl = pnl / n_res if n_res > 0 else 0
        print(f"{week_key:<12} {w['n']:>8} {avg_edge:>10.4f} {yes_no:>8} {n_res:>9} {wr:>5.1f}% ${pnl:>9.2f} ${avg_pnl:>7.2f}")

    # Totals
    total_entries = sum(w["n"] for w in weeks.values())
    total_edges = [e for w in weeks.values() for e in w["edges"]]
    total_avg_edge = sum(total_edges) / len(total_edges) if total_edges else 0
    total_pnl = sum(v[0] for v in resolutions.values())
    total_res = sum(v[1] for v in resolutions.values())
    total_wins = sum(v[2] for v in resolutions.values())
    total_wr = total_wins / total_res * 100 if total_res > 0 else 0
    total_avg_pnl = total_pnl / total_res if total_res > 0 else 0
    print("-" * 80)
    print(f"{'TOTAL':<12} {total_entries:>8} {total_avg_edge:>10.4f} {'':>8} {total_res:>9} {total_wr:>5.1f}% ${total_pnl:>9.2f} ${total_avg_pnl:>7.2f}")


if __name__ == "__main__":
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    asyncio.run(edge_decay(hours))
