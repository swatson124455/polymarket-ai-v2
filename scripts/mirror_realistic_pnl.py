#!/usr/bin/env python3
"""
MirrorBot Retroactive Realistic-Fill P&L Adjustment.

Reads all MirrorBot ENTRY events from trade_events and simulates what
the P&L would have been if PAPER_REALISTIC_FILLS had been enabled from
the start. Applies the same _fill_probability() model used in production.

Does NOT modify any data — read-only analysis.

Usage:
    python scripts/mirror_realistic_pnl.py           # All-time
    python scripts/mirror_realistic_pnl.py 168        # Last 168 hours
"""
import asyncio
import random
import sys
from base_engine.data.database import Database
from base_engine.execution.paper_trading import _fill_probability
from dotenv import load_dotenv
load_dotenv()

# Seed for reproducibility — same adjustment every run
random.seed(42)

# Default spread when bid/ask unavailable (matches config)
DEFAULT_SPREAD = 0.04


async def run(hours: int = 0):
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        # Fetch all MirrorBot ENTRY events
        where_time = ""
        params = {"bot": "MirrorBot"}
        if hours > 0:
            where_time = "AND event_time > NOW() - INTERVAL '1 hour' * :hours"
            params["hours"] = hours

        entries_q = await s.execute(text(f"""
            SELECT market_id, side, size, price, fees, event_time,
                   correlation_id,
                   COALESCE((event_data->>'volume')::float, 0) AS volume,
                   COALESCE((event_data->>'bid')::float, 0) AS bid,
                   COALESCE((event_data->>'ask')::float, 0) AS ask,
                   COALESCE((event_data->>'latency_ms')::float, 0) AS latency_ms
            FROM trade_events
            WHERE bot_name = :bot AND event_type = 'ENTRY' {where_time}
            ORDER BY event_time ASC
        """), params)
        entries = entries_q.fetchall()

        # Fetch all EXIT + RESOLUTION events for realized P&L
        exits_q = await s.execute(text(f"""
            SELECT market_id, side, size, price, fees, realized_pnl,
                   event_type, event_time
            FROM trade_events
            WHERE bot_name = :bot AND event_type IN ('EXIT', 'RESOLUTION') {where_time}
            ORDER BY event_time ASC
        """), params)
        exits = exits_q.fetchall()

    await db.close()

    # Build market→resolution map from EXIT/RESOLUTION events
    # Each entry's outcome is determined by whether its market was exited or resolved
    market_exit_pnl: dict = {}  # market_id -> list of (event_type, realized_pnl, size)
    for ex in exits:
        mid = ex[0]
        market_exit_pnl.setdefault(mid, []).append({
            "type": ex[6],
            "pnl": float(ex[5] or 0),
            "size": float(ex[2] or 0),
            "price": float(ex[3] or 0),
        })

    # Simulate realistic fills
    print(f"=== MirrorBot Retroactive Realistic-Fill P&L Adjustment ===")
    print(f"    Entries analyzed: {len(entries)}")
    print(f"    Exit/Resolution events: {len(exits)}")
    time_label = f"last {hours}h" if hours > 0 else "all-time"
    print(f"    Window: {time_label}")
    print()

    total_fantasy_cost = 0.0
    total_realistic_cost = 0.0
    total_fantasy_pnl = 0.0
    total_realistic_pnl = 0.0
    total_no_fill = 0
    total_partial_fill = 0
    total_full_fill = 0
    total_entries = len(entries)
    fill_probs = []

    # Per-market tracking for realistic fill adjustment
    realistic_entries: dict = {}  # market_id -> adjusted_size

    for e in entries:
        mid = e[0]
        side = e[1]
        size = float(e[2] or 0)
        price = float(e[3] or 0)
        fees = float(e[4] or 0)
        volume = float(e[7] or 0)
        bid = float(e[8] or 0)
        ask = float(e[9] or 0)
        latency_ms = float(e[10] or 0)

        order_usd = size * price
        spread = (ask - bid) if (bid > 0 and ask > 0) else DEFAULT_SPREAD

        # Calculate fill probability
        fill_prob = _fill_probability(price, order_usd, spread, volume)
        fill_probs.append(fill_prob)

        # Fantasy: 100% fill (what actually happened)
        fantasy_cost = order_usd
        total_fantasy_cost += fantasy_cost

        # Realistic: simulate fill/partial/no-fill
        roll = random.random()
        if roll > fill_prob:
            # No fill
            total_no_fill += 1
            realistic_size = 0.0
        else:
            # Partial fill: fill_frac = min(1.0, fill_prob + random()*0.5)
            fill_frac = min(1.0, fill_prob + random.random() * 0.5)
            if fill_frac >= 0.99:
                total_full_fill += 1
                realistic_size = size
            else:
                total_partial_fill += 1
                realistic_size = size * fill_frac

        realistic_cost = realistic_size * price
        total_realistic_cost += realistic_cost
        realistic_entries.setdefault(mid, []).append({
            "original_size": size,
            "realistic_size": realistic_size,
            "price": price,
        })

    # Now compute adjusted P&L for EXIT/RESOLUTION events
    # For each market, scale the realized P&L by the ratio of realistic/fantasy entry size
    for ex in exits:
        mid = ex[0]
        fantasy_pnl = float(ex[5] or 0)
        total_fantasy_pnl += fantasy_pnl

        # Find size ratio for this market
        if mid in realistic_entries:
            orig_total = sum(e["original_size"] for e in realistic_entries[mid])
            real_total = sum(e["realistic_size"] for e in realistic_entries[mid])
            ratio = real_total / orig_total if orig_total > 0 else 0.0
        else:
            ratio = 1.0  # No entry data found, assume full fill

        realistic_pnl = fantasy_pnl * ratio
        total_realistic_pnl += realistic_pnl

    # Summary
    avg_fill_prob = sum(fill_probs) / len(fill_probs) if fill_probs else 0.0
    fill_rate = (total_full_fill + total_partial_fill) / total_entries if total_entries > 0 else 0.0

    print(f"FILL SIMULATION RESULTS:")
    print(f"  No-fills:      {total_no_fill:>5}  ({total_no_fill/total_entries*100:.1f}%)")
    print(f"  Partial fills: {total_partial_fill:>5}  ({total_partial_fill/total_entries*100:.1f}%)")
    print(f"  Full fills:    {total_full_fill:>5}  ({total_full_fill/total_entries*100:.1f}%)")
    print(f"  Avg fill prob: {avg_fill_prob:.3f}")
    print(f"  Fill rate:     {fill_rate:.1%}")
    print()

    print(f"COST BASIS:")
    print(f"  Fantasy (100% fill):  ${total_fantasy_cost:>10.2f}")
    print(f"  Realistic:            ${total_realistic_cost:>10.2f}")
    print(f"  Reduction:            ${total_fantasy_cost - total_realistic_cost:>10.2f}  ({(1 - total_realistic_cost/total_fantasy_cost)*100:.1f}%)")
    print()

    print(f"REALIZED P&L:")
    print(f"  Fantasy (100% fill):  ${total_fantasy_pnl:>+10.2f}")
    print(f"  Realistic (adjusted): ${total_realistic_pnl:>+10.2f}")
    discount_pct = (1 - total_realistic_pnl / total_fantasy_pnl) * 100 if total_fantasy_pnl != 0 else 0
    print(f"  Discount:             {discount_pct:.1f}%")
    print()

    print(f"{'='*50}")
    print(f"BOTTOM LINE:")
    print(f"  Fantasy P&L:   ${total_fantasy_pnl:>+10.2f}")
    print(f"  Realistic P&L: ${total_realistic_pnl:>+10.2f}")
    print(f"  Haircut:       ${total_fantasy_pnl - total_realistic_pnl:>10.2f}  ({discount_pct:.1f}%)")
    print(f"{'='*50}")

    # Distribution of fill probabilities
    if fill_probs:
        buckets = [0]*5
        for fp in fill_probs:
            idx = min(4, int(fp * 5))
            buckets[idx] += 1
        print(f"\nFILL PROBABILITY DISTRIBUTION:")
        labels = ["0-20%", "20-40%", "40-60%", "60-80%", "80-100%"]
        for i, (label, count) in enumerate(zip(labels, buckets)):
            bar = "#" * (count * 40 // max(max(buckets), 1))
            print(f"  {label}: {count:>5}  {bar}")


if __name__ == "__main__":
    hrs = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    asyncio.run(run(hrs))
