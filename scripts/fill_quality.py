#!/usr/bin/env python3
"""Fill quality analytics for WeatherBot paper trading.

Analyzes fill simulation data logged to trade_events.event_data since S104.
Answers: slippage distribution, fill probability by city/price, book walk usage,
partial fill rates, and taker-side filter impact.

Usage:
    PYTHONPATH=/opt/polymarket-ai-v2 python scripts/fill_quality.py [days] [bot]
    Default: 3 days, WeatherBot
"""

import asyncio
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone

# Allow running from repo root or scripts/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    bot = sys.argv[2] if len(sys.argv) > 2 else "WeatherBot"

    from base_engine.data.database import Database
    from config.settings import settings

    db = Database(settings.DATABASE_URL)
    await db.initialize()

    try:
        from sqlalchemy import text

        # ── 1. Raw fill data from event_data JSONB ──
        async with db.get_session() as session:
            result = await session.execute(text("""
                SELECT
                    event_data->>'city' AS city,
                    (event_data->>'slippage_bps')::numeric AS slippage_bps,
                    (event_data->>'fill_prob')::numeric AS fill_prob,
                    (event_data->>'fill_frac')::numeric AS fill_frac,
                    (event_data->>'book_walk')::boolean AS book_walk,
                    (event_data->>'alpha_decay_bps')::numeric AS alpha_decay_bps,
                    (event_data->>'kyle_lambda_bps')::numeric AS kyle_lambda_bps,
                    (event_data->>'cross_scan_bps')::numeric AS cross_scan_bps,
                    (event_data->>'res_prox_mult')::numeric AS res_prox_mult,
                    event_data->>'side' AS side,
                    (event_data->>'price')::numeric AS price,
                    event_time
                FROM trade_events
                WHERE bot_name = :bot AND event_type = 'ENTRY'
                  AND event_time > NOW() - make_interval(days => :days)
                  AND event_time <= NOW()
                  AND event_data->>'slippage_bps' IS NOT NULL
                ORDER BY event_time
            """), {"bot": bot, "days": days})
            rows = result.fetchall()

        if not rows:
            print(f"No fill quality data for {bot} in last {days} days.")
            print("Fill quality logging was added in S104. Data may not be available yet.")
            return

        print(f"{'='*60}")
        print(f"FILL QUALITY REPORT — {bot} — last {days} days")
        print(f"{'='*60}")
        print(f"Total entries with fill data: {len(rows)}")
        print()

        # ── 2. Overall stats ──
        slippages = [float(r[1]) for r in rows if r[1] is not None]
        fill_probs = [float(r[2]) for r in rows if r[2] is not None]
        fill_fracs = [float(r[3]) for r in rows if r[3] is not None]
        book_walks = [r[4] for r in rows if r[4] is not None]
        alpha_decays = [float(r[5]) for r in rows if r[5] is not None]
        kyle_lambdas = [float(r[6]) for r in rows if r[6] is not None]

        def _stats(vals, label):
            if not vals:
                return f"  {label}: no data"
            avg = sum(vals) / len(vals)
            vals_sorted = sorted(vals)
            p50 = vals_sorted[len(vals_sorted) // 2]
            p90 = vals_sorted[int(len(vals_sorted) * 0.9)]
            p99 = vals_sorted[int(len(vals_sorted) * 0.99)]
            mn = min(vals)
            mx = max(vals)
            return f"  {label:20s}  avg={avg:7.2f}  p50={p50:7.2f}  p90={p90:7.2f}  p99={p99:7.2f}  min={mn:7.2f}  max={mx:7.2f}  n={len(vals)}"

        print("OVERALL FILL METRICS:")
        print(_stats(slippages, "slippage_bps"))
        print(_stats(fill_probs, "fill_prob"))
        print(_stats(fill_fracs, "fill_frac"))
        print(_stats(alpha_decays, "alpha_decay_bps"))
        print(_stats(kyle_lambdas, "kyle_lambda_bps"))
        bw_count = sum(1 for b in book_walks if b)
        print(f"  {'book_walk_used':20s}  {bw_count}/{len(book_walks)} ({bw_count/max(len(book_walks),1)*100:.1f}%)")
        print()

        # ── 3. By city ──
        city_data = defaultdict(list)
        for r in rows:
            city = r[0] or "unknown"
            city_data[city].append({
                "slippage": float(r[1]) if r[1] is not None else 0,
                "fill_prob": float(r[2]) if r[2] is not None else 1,
                "fill_frac": float(r[3]) if r[3] is not None else 1,
                "book_walk": bool(r[4]) if r[4] is not None else False,
            })

        print("BY CITY:")
        print(f"  {'City':20s} {'n':>5s} {'avg_slip':>9s} {'avg_fill':>9s} {'avg_frac':>9s} {'bw%':>6s}")
        print(f"  {'-'*20} {'-'*5} {'-'*9} {'-'*9} {'-'*9} {'-'*6}")
        for city in sorted(city_data.keys(), key=lambda c: -len(city_data[c])):
            entries = city_data[city]
            n = len(entries)
            avg_slip = sum(e["slippage"] for e in entries) / n
            avg_fill = sum(e["fill_prob"] for e in entries) / n
            avg_frac = sum(e["fill_frac"] for e in entries) / n
            bw_pct = sum(1 for e in entries if e["book_walk"]) / n * 100
            print(f"  {city:20s} {n:5d} {avg_slip:9.2f} {avg_fill:9.3f} {avg_frac:9.3f} {bw_pct:5.1f}%")
        print()

        # ── 4. By price bucket ──
        price_buckets = defaultdict(list)
        for r in rows:
            price = float(r[10]) if r[10] is not None else 0.5
            if price < 0.15:
                bucket = "0.00-0.15"
            elif price < 0.30:
                bucket = "0.15-0.30"
            elif price < 0.50:
                bucket = "0.30-0.50"
            elif price < 0.70:
                bucket = "0.50-0.70"
            elif price < 0.85:
                bucket = "0.70-0.85"
            else:
                bucket = "0.85-1.00"
            price_buckets[bucket].append({
                "fill_prob": float(r[2]) if r[2] is not None else 1,
                "slippage": float(r[1]) if r[1] is not None else 0,
            })

        print("BY PRICE BUCKET:")
        print(f"  {'Bucket':12s} {'n':>5s} {'avg_fill':>9s} {'avg_slip':>9s}")
        print(f"  {'-'*12} {'-'*5} {'-'*9} {'-'*9}")
        for bucket in sorted(price_buckets.keys()):
            entries = price_buckets[bucket]
            n = len(entries)
            avg_fill = sum(e["fill_prob"] for e in entries) / n
            avg_slip = sum(e["slippage"] for e in entries) / n
            print(f"  {bucket:12s} {n:5d} {avg_fill:9.3f} {avg_slip:9.2f}")
        print()

        # ── 5. Partial fill analysis ──
        partial = [f for f in fill_fracs if f < 1.0]
        full = [f for f in fill_fracs if f >= 1.0]
        print("PARTIAL FILL ANALYSIS:")
        print(f"  Full fills (frac=1.0):   {len(full)}")
        print(f"  Partial fills (frac<1):  {len(partial)}")
        if partial:
            avg_p = sum(partial) / len(partial)
            print(f"  Avg partial fill frac:   {avg_p:.3f}")
            print(f"  Min partial fill frac:   {min(partial):.3f}")
        print()

        # ── 6. Fill rejections (entries that WEREN'T logged — estimate from fill_prob) ──
        low_fill = [fp for fp in fill_probs if fp < 0.3]
        med_fill = [fp for fp in fill_probs if 0.3 <= fp < 0.5]
        high_fill = [fp for fp in fill_probs if fp >= 0.5]
        print("FILL PROBABILITY DISTRIBUTION (of successful fills):")
        print(f"  Low  (<0.30):  {len(low_fill):5d}  ({len(low_fill)/max(len(fill_probs),1)*100:.1f}%)")
        print(f"  Med  (0.3-0.5): {len(med_fill):4d}  ({len(med_fill)/max(len(fill_probs),1)*100:.1f}%)")
        print(f"  High (>=0.50): {len(high_fill):5d}  ({len(high_fill)/max(len(fill_probs),1)*100:.1f}%)")
        print()

        # ── 7. Resolution proximity impact ──
        res_mults = [float(r[8]) for r in rows if r[8] is not None and float(r[8]) != 1.0]
        if res_mults:
            print("RESOLUTION PROXIMITY PENALTY (entries where res_prox_mult != 1.0):")
            print(f"  Count: {len(res_mults)}")
            print(f"  Avg multiplier: {sum(res_mults)/len(res_mults):.3f}")
        print()

        # ── 8. Taker-side filter recommendation ──
        avg_fp = sum(fill_probs) / len(fill_probs) if fill_probs else 0
        print("TAKER-SIDE FILTER VERDICT:")
        print(f"  Avg fill_prob: {avg_fp:.3f}")
        if avg_fp < 0.5:
            print(f"  RECOMMENDED: avg < 0.50 → taker-side factor 0.55 warranted")
        else:
            print(f"  NOT NEEDED: avg >= 0.50 → fills are already realistic")

        print()
        print(f"{'='*60}")

    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
