#!/usr/bin/env python3
"""Show win rates for last N closed trades per bot."""
import asyncio
import os
import sys

os.environ.setdefault("POLYMARKET_AI_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

async def main():
    import asyncpg
    from dotenv import load_dotenv
    load_dotenv()
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])

    n_trades = int(sys.argv[1]) if len(sys.argv) > 1 else 30

    for bot in ["MirrorBot", "WeatherBot", "EsportsBot"]:
        rows = await conn.fetch(
            "SELECT event_type, side, ROUND(size::numeric, 2) as sz, "
            "ROUND(price::numeric, 4) as px, "
            "ROUND(COALESCE(realized_pnl, 0)::numeric, 2) as pnl, "
            "event_time "
            "FROM trade_events "
            "WHERE bot_name = $1 AND event_type IN ('EXIT', 'RESOLUTION') "
            "ORDER BY event_time DESC LIMIT $2",
            bot, n_trades
        )
        wins = sum(1 for r in rows if float(r["pnl"]) > 0)
        losses = sum(1 for r in rows if float(r["pnl"]) < 0)
        flat = sum(1 for r in rows if float(r["pnl"]) == 0)
        total_pnl = sum(float(r["pnl"]) for r in rows)
        n = len(rows)
        wr = (wins / n * 100) if n else 0

        win_sum = sum(float(r["pnl"]) for r in rows if float(r["pnl"]) > 0)
        loss_sum = sum(float(r["pnl"]) for r in rows if float(r["pnl"]) < 0)
        avg_win = win_sum / wins if wins else 0
        avg_loss = loss_sum / losses if losses else 0

        print(f"=== {bot} - Last {n} closed trades ===")
        print(f"  Win rate: {wins}W / {losses}L / {flat}F = {wr:.1f}%")
        print(f"  Total P&L: ${total_pnl:+.2f}  Avg win: ${avg_win:+.2f}  Avg loss: ${avg_loss:+.2f}")
        if losses and loss_sum != 0:
            print(f"  Profit factor: {abs(win_sum / loss_sum):.2f}x")
        print()
        for r in rows:
            pnl = float(r["pnl"])
            tag = "W" if pnl > 0 else ("L" if pnl < 0 else "F")
            ts = r["event_time"].strftime("%m-%d %H:%M")
            side = r["side"] or "--"
            print(f"    {ts} {r['event_type']:10s} {side:4s} sz={float(r['sz']):7.2f} px={float(r['px']):.4f} pnl=${pnl:+8.2f} [{tag}]")
        print()

    await conn.close()

if __name__ == "__main__":
    asyncio.run(main())
