#!/usr/bin/env python3
"""Investigate positions that should have been stopped out but weren't."""
import asyncio
import os
import sys

async def main():
    import asyncpg
    from dotenv import load_dotenv
    load_dotenv()
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    for bot in ["MirrorBot", "WeatherBot", "EsportsBot"]:
        # Get worst open losers
        rows = await conn.fetch(
            "SELECT market_id, side, ROUND(size::numeric, 2) as sz, "
            "ROUND(entry_price::numeric, 4) as entry, ROUND(current_price::numeric, 4) as curr, "
            "ROUND(COALESCE(unrealized_pnl, 0)::numeric, 2) as upnl, "
            "ROUND((entry_price * size)::numeric, 2) as cost, "
            "opened_at "
            "FROM positions WHERE source_bot = $1 AND status = 'open' "
            "AND unrealized_pnl < 0 "
            "ORDER BY unrealized_pnl ASC LIMIT 10",
            bot
        )
        if not rows:
            print(f"=== {bot}: No open losers ===\n")
            continue

        print(f"=== {bot} WORST OPEN LOSERS ===")
        for r in rows:
            entry = float(r["entry"])
            curr = float(r["curr"])
            loss_pct = ((curr - entry) / entry * 100) if entry > 0 else 0
            opened = r["opened_at"].strftime("%m-%d %H:%M") if r["opened_at"] else "?"
            mid_short = r["market_id"][:16]
            print(f"  {mid_short}.. {r['side']:3s} sz={float(r['sz']):7.2f} "
                  f"entry={entry:.4f} curr={curr:.4f} "
                  f"uPnL=${float(r['upnl']):+8.2f} loss={loss_pct:+.1f}% opened={opened}")

        # Check how many are past stop-loss thresholds
        stop_pcts = {"MirrorBot": 0.15, "WeatherBot": 0.30, "EsportsBot": 0.25}
        stop = stop_pcts.get(bot, 0.30)
        past_stop = [r for r in rows if float(r["entry"]) > 0 and
                     (float(r["curr"]) - float(r["entry"])) / float(r["entry"]) < -stop]
        print(f"\n  Positions past {stop*100:.0f}% stop-loss: {len(past_stop)}")

        # For worst 5, check entry events and any exit attempts
        print(f"\n  --- Trade history for worst 5 ---")
        for r in rows[:5]:
            mid = r["market_id"]
            mid_short = mid[:16]
            events = await conn.fetch(
                "SELECT event_type, event_time, side, "
                "ROUND(COALESCE(size, 0)::numeric, 2) as sz, "
                "ROUND(COALESCE(price, 0)::numeric, 4) as px, "
                "ROUND(COALESCE(realized_pnl, 0)::numeric, 2) as pnl "
                "FROM trade_events WHERE bot_name = $1 AND market_id = $2 "
                "ORDER BY event_time",
                bot, mid
            )
            print(f"\n  {mid_short}.. (opened {r['opened_at'].strftime('%m-%d %H:%M') if r['opened_at'] else '?'}, "
                  f"entry={float(r['entry']):.4f}, curr={float(r['curr']):.4f}, "
                  f"uPnL=${float(r['upnl']):+.2f}):")
            if events:
                for e in events:
                    ts = e["event_time"].strftime("%m-%d %H:%M")
                    print(f"    {ts} {e['event_type']:10s} {e['side'] or '--':4s} "
                          f"sz={float(e['sz']):7.2f} px={float(e['px']):.4f} pnl=${float(e['pnl']):+.2f}")
            else:
                print(f"    NO TRADE EVENTS FOUND")

        # Count all open positions past stop-loss
        all_past = await conn.fetch(
            "SELECT COUNT(*) as cnt, ROUND(SUM(unrealized_pnl)::numeric, 2) as total_loss "
            "FROM positions WHERE source_bot = $1 AND status = 'open' "
            "AND entry_price > 0 AND "
            "((current_price - entry_price) / entry_price) < $2",
            bot, -stop
        )
        if all_past:
            print(f"\n  TOTAL positions past stop-loss: {all_past[0]['cnt']} "
                  f"(total uPnL: ${float(all_past[0]['total_loss'] or 0):+.2f})")
        print()

    await conn.close()

asyncio.run(main())
