#!/usr/bin/env python3
"""
WeatherBot P&L Breakdown — by city, bucket type, lead time, and side.

Usage (on VPS):
    source /opt/pa2-shared/venv/bin/activate && cd /opt/polymarket-ai-v2
    PYTHONPATH=. python3 scripts/weather_pnl_breakdown.py          # all-time
    PYTHONPATH=. python3 scripts/weather_pnl_breakdown.py 168      # last 7 days

P&L math (uniform for YES and NO — prices are token-specific):
  cost = entry_price * size
  realized_pnl = (resolution_value - entry_price) * size - fees
"""
import asyncio
import sys
from collections import defaultdict

from dotenv import load_dotenv

load_dotenv()

from base_engine.data.database import Database  # noqa: E402


async def weather_pnl_breakdown(hours: int = 0):
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        time_filter = ""
        params: dict = {"bot": "WeatherBot"}
        if hours > 0:
            time_filter = "AND te.event_time > NOW() - INTERVAL '1 hour' * :hours"
            params["hours"] = hours

        # ── 1. Realized P&L from trade_events (EXIT + RESOLUTION) ────────
        query = text(f"""
            SELECT te.event_type, te.market_id, te.side, te.size, te.price,
                   te.realized_pnl, te.event_time, te.event_data
            FROM trade_events te
            WHERE te.bot_name = :bot
              AND te.event_type IN ('ENTRY', 'EXIT', 'RESOLUTION')
              {time_filter}
            ORDER BY te.event_time
        """)
        rows = (await s.execute(query, params)).fetchall()

        # Build entry map: market_id -> first ENTRY event_data (has city, lead_time, etc.)
        entry_meta: dict = {}  # market_id -> {city, lead_time_hours, side, entry_price}
        for r in rows:
            etype, mid, side, size, price, rpnl, etime, edata = r
            if etype == "ENTRY" and mid not in entry_meta:
                ed = edata if isinstance(edata, dict) else {}
                city = ed.get("city", "unknown")
                lead_time = ed.get("lead_time_hours", -1)
                entry_meta[mid] = {
                    "city": city,
                    "lead_time_hours": lead_time,
                    "side": side,
                    "entry_price": float(price or 0),
                }

        # Aggregate P&L by city
        city_pnl: dict = defaultdict(lambda: {"realized": 0.0, "entries": 0, "exits": 0, "resolutions": 0, "wins": 0, "losses": 0})
        side_pnl: dict = defaultdict(lambda: {"realized": 0.0, "count": 0, "wins": 0})
        lead_pnl: dict = defaultdict(lambda: {"realized": 0.0, "count": 0, "wins": 0})

        for r in rows:
            etype, mid, side, size, price, rpnl, etime, edata = r
            meta = entry_meta.get(mid, {"city": "unknown", "lead_time_hours": -1, "side": side})
            city = meta["city"]
            lead_h = meta.get("lead_time_hours", -1)

            if etype == "ENTRY":
                city_pnl[city]["entries"] += 1
            elif etype in ("EXIT", "RESOLUTION"):
                pnl = float(rpnl or 0)
                city_pnl[city]["realized"] += pnl
                city_pnl[city][etype.lower() + "s"] += 1
                if pnl > 0:
                    city_pnl[city]["wins"] += 1
                elif pnl < 0:
                    city_pnl[city]["losses"] += 1

                # By side
                trade_side = meta["side"]
                side_pnl[trade_side]["realized"] += pnl
                side_pnl[trade_side]["count"] += 1
                if pnl > 0:
                    side_pnl[trade_side]["wins"] += 1

                # By lead time bucket
                if lead_h < 0:
                    bucket = "unknown"
                elif lead_h < 6:
                    bucket = "<6h"
                elif lead_h < 24:
                    bucket = "6-24h"
                elif lead_h < 48:
                    bucket = "24-48h"
                elif lead_h < 72:
                    bucket = "48-72h"
                elif lead_h < 120:
                    bucket = "72-120h"
                else:
                    bucket = "120h+"
                lead_pnl[bucket]["realized"] += pnl
                lead_pnl[bucket]["count"] += 1
                if pnl > 0:
                    lead_pnl[bucket]["wins"] += 1

        # ── 2. Open positions (unrealized) ────────────────────────────────
        r2 = await s.execute(text("""
            SELECT p.market_id, p.side, p.size, p.entry_price, p.current_price,
                   p.unrealized_pnl
            FROM positions p
            WHERE (p.bot_id = :bot OR p.source_bot = :bot)
              AND p.status = 'open'
        """), {"bot": "WeatherBot"})
        open_pos = r2.fetchall()

        total_upnl = sum(float(p[5] or 0) for p in open_pos)
        total_cost = sum(float(p[3] or 0) * float(p[2] or 0) for p in open_pos)

        # ── PRINT REPORT ──────────────────────────────────────────────────
        label = f"last {hours}h" if hours > 0 else "all-time"
        print(f"\n{'='*72}")
        print(f"  WeatherBot P&L Breakdown ({label})")
        print(f"{'='*72}")

        # Summary
        total_realized = sum(c["realized"] for c in city_pnl.values())
        total_entries = sum(c["entries"] for c in city_pnl.values())
        total_closed = sum(c["exits"] + c["resolutions"] for c in city_pnl.values())
        total_wins = sum(c["wins"] for c in city_pnl.values())
        total_losses = sum(c["losses"] for c in city_pnl.values())
        win_rate = total_wins / max(total_closed, 1) * 100

        print(f"\n  Realized P&L:   ${total_realized:>+10.2f}")
        print(f"  Unrealized:     ${total_upnl:>+10.2f}")
        print(f"  Open positions: {len(open_pos):>10} (cost: ${total_cost:,.0f})")
        print(f"  Entries:        {total_entries:>10}")
        print(f"  Closed:         {total_closed:>10} ({total_wins}W / {total_losses}L = {win_rate:.0f}%)")

        # By city (sorted by P&L)
        print(f"\n{'─'*72}")
        print(f"  BY CITY")
        print(f"{'─'*72}")
        print(f"  {'City':<20} {'P&L':>10} {'Entries':>8} {'Closed':>8} {'W/L':>10} {'Win%':>6}")
        print(f"  {'-'*62}")
        for city, data in sorted(city_pnl.items(), key=lambda x: x[1]["realized"], reverse=True):
            closed = data["exits"] + data["resolutions"]
            w = data["wins"]
            l = data["losses"]
            wr = w / max(closed, 1) * 100
            print(f"  {city:<20} ${data['realized']:>+9.2f} {data['entries']:>8} {closed:>8} {w:>4}W/{l:<4}L {wr:>5.0f}%")

        # By side
        print(f"\n{'─'*72}")
        print(f"  BY SIDE")
        print(f"{'─'*72}")
        print(f"  {'Side':<10} {'P&L':>10} {'Closed':>8} {'Wins':>6} {'Win%':>6}")
        print(f"  {'-'*40}")
        for side, data in sorted(side_pnl.items(), key=lambda x: x[1]["realized"], reverse=True):
            wr = data["wins"] / max(data["count"], 1) * 100
            print(f"  {side:<10} ${data['realized']:>+9.2f} {data['count']:>8} {data['wins']:>6} {wr:>5.0f}%")

        # By lead time
        print(f"\n{'─'*72}")
        print(f"  BY LEAD TIME")
        print(f"{'─'*72}")
        print(f"  {'Bucket':<12} {'P&L':>10} {'Closed':>8} {'Wins':>6} {'Win%':>6}")
        print(f"  {'-'*42}")
        bucket_order = ["<6h", "6-24h", "24-48h", "48-72h", "72-120h", "120h+", "unknown"]
        for bucket in bucket_order:
            if bucket in lead_pnl:
                data = lead_pnl[bucket]
                wr = data["wins"] / max(data["count"], 1) * 100
                print(f"  {bucket:<12} ${data['realized']:>+9.2f} {data['count']:>8} {data['wins']:>6} {wr:>5.0f}%")

        print(f"\n{'='*72}\n")

    await db.close()


if __name__ == "__main__":
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    asyncio.run(weather_pnl_breakdown(hours))
