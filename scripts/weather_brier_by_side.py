#!/usr/bin/env python3
"""
WeatherBot Brier Score Breakdown by Side × Price Bucket.

Usage:
    python scripts/weather_brier_by_side.py          # All time
    python scripts/weather_brier_by_side.py 168      # Last 168 hours

Computes per-group: Brier score, win rate, count, avg P&L.
Helps diagnose YES win rate (~16%) and identify calibration errors by price range.
"""
import asyncio
import sys
from base_engine.data.database import Database
from dotenv import load_dotenv
load_dotenv()


async def brier_by_side(hours: int = 0):
    db = Database()
    await db.init()
    async with db.get_session() as s:
        from sqlalchemy import text

        time_filter = ""
        if hours > 0:
            time_filter = f"AND e.event_time > NOW() - INTERVAL '{hours} hours' AND e.event_time <= NOW()"

        # Get ENTRY events with their RESOLUTION outcomes
        rows = await s.execute(text(f"""
            WITH entries AS (
                SELECT e.market_id, e.side, e.price AS entry_price,
                       CAST(e.event_data->>'confidence' AS FLOAT) AS confidence,
                       e.event_time AS entry_time
                FROM trade_events e
                WHERE e.bot_name = 'WeatherBot'
                  AND e.event_type = 'ENTRY'
                  {time_filter}
            ),
            resolutions AS (
                SELECT r.market_id, r.realized_pnl,
                       CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END AS won
                FROM trade_events r
                WHERE r.bot_name = 'WeatherBot'
                  AND r.event_type = 'RESOLUTION'
            )
            SELECT e.side,
                   e.entry_price,
                   e.confidence,
                   r.realized_pnl,
                   r.won
            FROM entries e
            JOIN resolutions r ON r.market_id = e.market_id
        """))
        data = rows.fetchall()

    if not data:
        print("No resolved WeatherBot trades found.")
        return

    # Bucket by side × price range
    buckets = {}
    for side, entry_price, confidence, realized_pnl, won in data:
        if entry_price is None or confidence is None:
            continue
        # Price bucket: 0-20, 20-40, 40-60, 60-80, 80-100
        bucket = int(entry_price * 100) // 20 * 20
        bucket_label = f"{bucket}-{bucket+20}¢"
        key = (side, bucket_label)
        if key not in buckets:
            buckets[key] = {"n": 0, "wins": 0, "pnl": 0.0, "brier_sum": 0.0}
        b = buckets[key]
        b["n"] += 1
        b["wins"] += int(won or 0)
        b["pnl"] += float(realized_pnl or 0)
        # Brier score: (forecast - outcome)^2
        outcome = 1.0 if won else 0.0
        b["brier_sum"] += (float(confidence) - outcome) ** 2

    # Print results
    print(f"\n{'Side':<6} {'Price':<10} {'N':>6} {'WR':>7} {'Brier':>8} {'P&L':>10} {'$/trade':>8}")
    print("-" * 60)
    for (side, bucket), b in sorted(buckets.items()):
        wr = b["wins"] / b["n"] * 100 if b["n"] > 0 else 0
        brier = b["brier_sum"] / b["n"] if b["n"] > 0 else 0
        avg_pnl = b["pnl"] / b["n"] if b["n"] > 0 else 0
        print(f"{side:<6} {bucket:<10} {b['n']:>6} {wr:>6.1f}% {brier:>8.4f} ${b['pnl']:>9.2f} ${avg_pnl:>7.2f}")

    # Summary by side
    print(f"\n{'Side':<6} {'N':>6} {'WR':>7} {'Brier':>8} {'P&L':>10}")
    print("-" * 40)
    for side_name in ("YES", "NO"):
        side_data = {k: v for k, v in buckets.items() if k[0] == side_name}
        n = sum(v["n"] for v in side_data.values())
        wins = sum(v["wins"] for v in side_data.values())
        pnl = sum(v["pnl"] for v in side_data.values())
        brier = sum(v["brier_sum"] for v in side_data.values()) / n if n > 0 else 0
        wr = wins / n * 100 if n > 0 else 0
        print(f"{side_name:<6} {n:>6} {wr:>6.1f}% {brier:>8.4f} ${pnl:>9.2f}")


if __name__ == "__main__":
    hours = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    asyncio.run(brier_by_side(hours))
