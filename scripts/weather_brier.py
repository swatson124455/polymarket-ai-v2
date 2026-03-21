#!/usr/bin/env python3
"""WeatherBot Brier Score & Calibration Decomposition.

Computes reliability, resolution, and uncertainty components of the Brier score
from trade_events ENTRY + RESOLUTION pairs. Breaks down by city, lead time,
bucket type, and predicted probability bin.

Usage:
    PYTHONPATH=/opt/polymarket-ai-v2 python scripts/weather_brier.py [days]
    Default: 30 days

Data sources:
  - trade_events (ENTRY → predicted_probability, RESOLUTION → realized_pnl)
  - event_data JSONB for city, lead_time_hours, market_type metadata

Brier decomposition (Murphy 1973):
  BS = reliability - resolution + uncertainty
  reliability = (1/N) Σ nk (fk - ōk)²   [penalty for miscalibration]
  resolution  = (1/N) Σ nk (ōk - ō)²    [reward for separating outcomes]
  uncertainty = ō(1 - ō)                  [irreducible; base rate entropy]
"""

import asyncio
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30

    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from config.settings import settings

    db = Database(settings.DATABASE_URL)
    await db.initialize()

    try:
        from sqlalchemy import text

        # ── 1. Fetch ENTRY + RESOLUTION pairs joined on market_id ──
        async with db.get_session() as session:
            result = await session.execute(text("""
                WITH entries AS (
                    SELECT
                        market_id,
                        predicted_probability,
                        side,
                        event_data->>'city' AS city,
                        (event_data->>'lead_time_hours')::numeric AS lead_time_hours,
                        event_data->>'market_type' AS market_type,
                        event_time AS entry_time
                    FROM trade_events
                    WHERE bot_name = 'WeatherBot'
                      AND event_type = 'ENTRY'
                      AND event_time > NOW() - make_interval(days => :days)
                      AND predicted_probability IS NOT NULL
                ),
                resolutions AS (
                    SELECT DISTINCT ON (market_id)
                        market_id,
                        realized_pnl
                    FROM trade_events
                    WHERE bot_name = 'WeatherBot'
                      AND event_type = 'RESOLUTION'
                      AND event_time > NOW() - make_interval(days => :days)
                    ORDER BY market_id, event_time DESC
                )
                SELECT
                    e.market_id,
                    e.predicted_probability,
                    e.side,
                    e.city,
                    e.lead_time_hours,
                    e.market_type,
                    r.realized_pnl
                FROM entries e
                JOIN resolutions r USING (market_id)
                ORDER BY e.entry_time
            """), {"days": days})
            rows = result.fetchall()

        if not rows:
            print(f"No resolved ENTRY+RESOLUTION pairs found in last {days} days.")
            return

        # ── 2. Build records ──
        records = []
        for row in rows:
            market_id, pred_prob, side, city, lead_h, mkt_type, rpnl = row
            pred_prob = float(pred_prob) if pred_prob is not None else None
            rpnl = float(rpnl) if rpnl is not None else None
            lead_h = float(lead_h) if lead_h is not None else None

            if pred_prob is None or rpnl is None:
                continue

            # Outcome: 1 if position won (realized_pnl > 0), 0 if lost
            outcome = 1.0 if rpnl > 0.0 else 0.0

            records.append({
                "market_id": market_id,
                "pred_prob": pred_prob,
                "outcome": outcome,
                "city": city or "unknown",
                "lead_h": lead_h,
                "market_type": mkt_type or "temperature",
                "side": side,
                "rpnl": rpnl,
            })

        print(f"=== WeatherBot Brier Calibration Report (last {days}d) ===")
        print(f"Total resolved trades: {len(records)}\n")

        # ── 3. Overall Brier score ──
        brier = _brier_score(records)
        _print_brier("OVERALL", brier, len(records))

        # ── 4. By city ──
        by_city = defaultdict(list)
        for r in records:
            by_city[r["city"]].append(r)

        print("\n--- BY CITY ---")
        print(f"{'City':<20} {'N':>5} {'Brier':>7} {'Reliab':>7} {'Resol':>7} {'WinRate':>8}")
        print("-" * 60)
        city_rows = []
        for city, recs in sorted(by_city.items(), key=lambda x: len(x[1]), reverse=True):
            b = _brier_score(recs)
            wr = sum(1 for r in recs if r["outcome"] == 1.0) / len(recs)
            city_rows.append((city, len(recs), b, wr))
            print(f"{city:<20} {len(recs):>5} {b['brier']:>7.4f} {b['reliability']:>7.4f} {b['resolution']:>7.4f} {wr:>7.1%}")

        # ── 5. By lead time bucket ──
        print("\n--- BY LEAD TIME ---")
        print(f"{'Lead Time':<15} {'N':>5} {'Brier':>7} {'Reliab':>7} {'Resol':>7} {'WinRate':>8}")
        print("-" * 55)
        by_lead = defaultdict(list)
        for r in records:
            if r["lead_h"] is None:
                bucket = "unknown"
            elif r["lead_h"] < 24:
                bucket = "0-24h"
            elif r["lead_h"] < 48:
                bucket = "24-48h"
            elif r["lead_h"] < 72:
                bucket = "48-72h"
            else:
                bucket = "72h+"
            by_lead[bucket].append(r)

        for bucket in ["0-24h", "24-48h", "48-72h", "72h+", "unknown"]:
            recs = by_lead.get(bucket, [])
            if not recs:
                continue
            b = _brier_score(recs)
            wr = sum(1 for r in recs if r["outcome"] == 1.0) / len(recs)
            print(f"{bucket:<15} {len(recs):>5} {b['brier']:>7.4f} {b['reliability']:>7.4f} {b['resolution']:>7.4f} {wr:>7.1%}")

        # ── 6. By market type ──
        print("\n--- BY MARKET TYPE ---")
        print(f"{'Type':<15} {'N':>5} {'Brier':>7} {'Reliab':>7} {'Resol':>7} {'WinRate':>8}")
        print("-" * 55)
        by_type = defaultdict(list)
        for r in records:
            by_type[r["market_type"]].append(r)

        for mtype, recs in sorted(by_type.items(), key=lambda x: len(x[1]), reverse=True):
            b = _brier_score(recs)
            wr = sum(1 for r in recs if r["outcome"] == 1.0) / len(recs)
            print(f"{mtype:<15} {len(recs):>5} {b['brier']:>7.4f} {b['reliability']:>7.4f} {b['resolution']:>7.4f} {wr:>7.1%}")

        # ── 7. By side ──
        print("\n--- BY SIDE ---")
        print(f"{'Side':<6} {'N':>5} {'Brier':>7} {'WinRate':>8} {'Avg Pred':>9} {'Avg PnL':>9}")
        print("-" * 50)
        by_side = defaultdict(list)
        for r in records:
            by_side[r["side"]].append(r)

        for side in ["YES", "NO"]:
            recs = by_side.get(side, [])
            if not recs:
                continue
            b = _brier_score(recs)
            wr = sum(1 for r in recs if r["outcome"] == 1.0) / len(recs)
            avg_pred = sum(r["pred_prob"] for r in recs) / len(recs)
            avg_pnl = sum(r["rpnl"] for r in recs) / len(recs)
            print(f"{side:<6} {len(recs):>5} {b['brier']:>7.4f} {wr:>7.1%} {avg_pred:>9.4f} ${avg_pnl:>8.2f}")

        # ── 8. Calibration curve (reliability diagram data) ──
        print("\n--- CALIBRATION CURVE ---")
        print(f"{'Bin':<12} {'N':>5} {'Avg Pred':>9} {'Actual WR':>10} {'Gap':>8}")
        print("-" * 48)
        bins = [(i / 10, (i + 1) / 10) for i in range(10)]
        for lo, hi in bins:
            recs = [r for r in records if lo <= r["pred_prob"] < hi]
            if not recs:
                continue
            avg_pred = sum(r["pred_prob"] for r in recs) / len(recs)
            actual_wr = sum(r["outcome"] for r in recs) / len(recs)
            gap = actual_wr - avg_pred
            bar = "+" * int(abs(gap) * 50) if gap >= 0 else "-" * int(abs(gap) * 50)
            print(f"[{lo:.1f}-{hi:.1f})  {len(recs):>5} {avg_pred:>9.4f} {actual_wr:>9.1%} {gap:>+7.1%} {bar}")

        # ── 9. EMOS residual MSE from weather_calibration ──
        async with db.get_session() as session:
            result = await session.execute(text("""
                SELECT
                    station_id,
                    COUNT(*) AS n_pairs,
                    ROUND(AVG((forecast_temp - actual_temp)^2)::numeric, 2) AS mse,
                    ROUND(AVG(forecast_temp - actual_temp)::numeric, 2) AS mean_bias,
                    ROUND(STDDEV(forecast_temp - actual_temp)::numeric, 2) AS std_bias
                FROM weather_calibration
                WHERE actual_temp IS NOT NULL
                  AND created_at > NOW() - make_interval(days => :days)
                GROUP BY station_id
                HAVING COUNT(*) >= 5
                ORDER BY AVG((forecast_temp - actual_temp)^2) DESC
            """), {"days": days})
            cal_rows = result.fetchall()

        if cal_rows:
            print("\n--- EMOS RESIDUAL MSE (by station) ---")
            print(f"{'Station':<10} {'N':>5} {'MSE':>8} {'Bias':>8} {'Std':>8}")
            print("-" * 42)
            for cr in cal_rows:
                sid, n, mse, bias, std = cr
                print(f"{sid:<10} {n:>5} {float(mse):>8.2f} {float(bias):>+8.2f} {float(std):>8.2f}")

    finally:
        await db.close()


def _brier_score(records):
    """Compute Brier score with Murphy (1973) decomposition.

    BS = (1/N) Σ (fi - oi)²
    reliability = (1/N) Σ nk (f̄k - ōk)²
    resolution  = (1/N) Σ nk (ōk - ō)²
    uncertainty = ō(1 - ō)
    """
    n = len(records)
    if n == 0:
        return {"brier": 0.0, "reliability": 0.0, "resolution": 0.0, "uncertainty": 0.0}

    # Raw Brier score
    brier = sum((r["pred_prob"] - r["outcome"]) ** 2 for r in records) / n

    # Base rate
    o_bar = sum(r["outcome"] for r in records) / n
    uncertainty = o_bar * (1.0 - o_bar)

    # Bin into 10 probability bins for decomposition
    bins = defaultdict(list)
    for r in records:
        b = min(int(r["pred_prob"] * 10), 9)  # 0-9
        bins[b].append(r)

    reliability = 0.0
    resolution = 0.0
    for b, recs in bins.items():
        nk = len(recs)
        f_bar_k = sum(r["pred_prob"] for r in recs) / nk  # avg forecast in bin
        o_bar_k = sum(r["outcome"] for r in recs) / nk     # avg outcome in bin
        reliability += nk * (f_bar_k - o_bar_k) ** 2
        resolution += nk * (o_bar_k - o_bar) ** 2

    reliability /= n
    resolution /= n

    return {
        "brier": round(brier, 6),
        "reliability": round(reliability, 6),
        "resolution": round(resolution, 6),
        "uncertainty": round(uncertainty, 6),
    }


def _print_brier(label, b, n):
    print(f"{label} (N={n}):")
    print(f"  Brier Score:  {b['brier']:.4f}  (lower = better, 0.25 = coin flip)")
    print(f"  Reliability:  {b['reliability']:.4f}  (lower = better calibrated)")
    print(f"  Resolution:   {b['resolution']:.4f}  (higher = better discrimination)")
    print(f"  Uncertainty:  {b['uncertainty']:.4f}  (base rate entropy, irreducible)")
    skill = 1.0 - b["brier"] / max(b["uncertainty"], 0.001)
    print(f"  Brier Skill:  {skill:.4f}  (>0 = better than climatology)")


if __name__ == "__main__":
    asyncio.run(main())
