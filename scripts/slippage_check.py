#!/usr/bin/env python3
"""
Slippage Check — Compare paper trade entry prices against market prices.

Usage:
    python scripts/slippage_check.py                  # All bots, default cutoff
    python scripts/slippage_check.py EsportsBot       # Specific bot

Verifies that paper trading fill simulation uses realistic prices.
If fills consistently deviate from market price, models learn from fake edge.

NOTE: market_prices has ~30-day retention. Only works for recent entries.
Uses SET LOCAL statement_timeout = '10000' to fail fast (LATERAL JOIN can be slow).

S169: Data quality verification pipeline.
"""
import asyncio
import sys
from collections import defaultdict

from dotenv import load_dotenv
load_dotenv()

from base_engine.data.database import Database


async def slippage_check(bot_name: str = "", cutoff: str = "2026-04-08T16:01:40Z"):
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        # Set short timeout — LATERAL JOIN on market_prices can be slow
        await s.execute(text("SET LOCAL statement_timeout = '10000'"))

        bot_clause = ""
        params = {"cutoff": cutoff}
        if bot_name:
            bot_clause = "AND te.bot_name = :bot_name"
            params["bot_name"] = bot_name

        try:
            result = await s.execute(text(f"""
                SELECT te.market_id, te.token_id, te.bot_name,
                       te.price AS entry_price, te.event_time,
                       mpl.price AS market_price_at_entry,
                       ABS(te.price - mpl.price) AS slippage,
                       te.size
                FROM trade_events te
                JOIN LATERAL (
                    SELECT price FROM market_prices mp
                    WHERE mp.token_id = te.token_id
                      AND mp.timestamp <= te.event_time
                    ORDER BY mp.timestamp DESC
                    LIMIT 1
                ) mpl ON TRUE
                WHERE te.event_type = 'ENTRY'
                  AND te.event_time > :cutoff
                  {bot_clause}
                ORDER BY slippage DESC
                LIMIT 200
            """), params)
        except Exception as e:
            if "statement timeout" in str(e).lower():
                print("ERROR: Query timed out at 10s. market_prices table may be too large.")
                print("Try narrowing the cutoff window or adding an index on market_prices(token_id, timestamp).")
                return
            raise

        rows = result.fetchall()

        if not rows:
            print("No entries with matching market_prices found.")
            print("Possible causes:")
            print("  - market_prices has 30-day retention; older entries have no price match")
            print("  - No ENTRY events after cutoff date")
            return

        # Aggregate by bot
        by_bot = defaultdict(list)
        for mid, tid, bot, entry_px, ts, mkt_px, slip, sz in rows:
            by_bot[bot or "unknown"].append({
                "market_id": mid,
                "entry_price": float(entry_px),
                "market_price": float(mkt_px),
                "slippage": float(slip),
                "size": float(sz) if sz else 0,
                "event_time": str(ts),
            })

        for bot, entries in sorted(by_bot.items()):
            slips = [e["slippage"] for e in entries]
            slips_sorted = sorted(slips)
            n = len(slips_sorted)

            median = slips_sorted[n // 2]
            p95 = slips_sorted[int(n * 0.95)] if n >= 20 else slips_sorted[-1]
            max_slip = slips_sorted[-1]
            mean = sum(slips) / n

            print(f"\n{'=' * 60}")
            print(f"BOT: {bot} ({n} entries with price match)")
            print(f"{'=' * 60}")
            print(f"  Mean slippage:   {mean:.4f}")
            print(f"  Median slippage: {median:.4f}")
            print(f"  P95 slippage:    {p95:.4f}")
            print(f"  Max slippage:    {max_slip:.4f}")

            # Flag high-slippage entries
            high_slip = [e for e in entries if e["slippage"] > 0.05]
            if high_slip:
                print(f"\n  HIGH SLIPPAGE ENTRIES (>{0.05}):")
                print(f"  {'Market':<16} {'Entry':>7} {'Market':>7} {'Slip':>7} {'Size':>8} {'Time'}")
                print(f"  {'-' * 70}")
                for e in high_slip[:20]:
                    mid = e["market_id"][:14] + ".."
                    print(f"  {mid:<16} {e['entry_price']:>7.3f} {e['market_price']:>7.3f} "
                          f"{e['slippage']:>7.3f} {e['size']:>8.1f} {e['event_time'][:19]}")
            else:
                print(f"\n  No entries with slippage > 0.05")

    await db.close()


if __name__ == "__main__":
    bot = ""
    cutoff = "2026-04-08T16:01:40Z"
    for arg in sys.argv[1:]:
        if arg.startswith("--cutoff"):
            continue
        if arg.startswith("2"):
            cutoff = arg
        elif not arg.startswith("-"):
            bot = arg
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--cutoff" and i < len(sys.argv) - 1:
            cutoff = sys.argv[i + 1]

    asyncio.run(slippage_check(bot, cutoff))
