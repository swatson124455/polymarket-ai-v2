#!/usr/bin/env python3
"""
WB Calibration Backtest — double-blind, Phase-2 for WB_REBUILD_PLAN.md.

Scores the model's OWN logged forecasts against what actually happened. Each
prediction_log row holds predicted_prob (model P(YES)) logged at scan time;
backfill_prediction_log_resolution() stamps the AUTHORITATIVE market outcome and
EXCLUDES any row whose resolution predates the prediction -> no look-ahead. So
predicted_prob is a genuine as-of forecast and `resolution` is the truth revealed
only afterward. That is the definition of a double-blind out-of-sample test.

Robustness note: if the old discovery bug mis-paired a forecast to the wrong
market, that only injects NOISE (pushes Brier toward 0.25) — it cannot fabricate
skill. So a positive result here is trustworthy; a negative one may be partly the
bug. Direction-safe against false edge.

The question this answers: is the model SALVAGEABLE (discriminates but is
miscalibrated/overconfident -> fixable by recalibration) or DEAD (no
discrimination -> wind down)? Read the reliability table + resolution term.

One row per market = last prediction before resolution. READ-ONLY.

Usage:
    PYTHONPATH=/opt/polymarket-ai-v2 python scripts/wb_calibration_backtest.py [days] [temp|all]
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _decompose(rows):
    """rows: list of (p, y). Murphy 1973 Brier decomposition with 10 bins."""
    n = len(rows)
    base = sum(y for _p, y in rows) / n
    brier = sum((p - y) ** 2 for p, y in rows) / n
    bins = {}
    for p, y in rows:
        k = min(9, int(p * 10))
        bins.setdefault(k, []).append((p, y))
    reliability = resolution = 0.0
    table = []
    for k in range(10):
        recs = bins.get(k, [])
        if not recs:
            continue
        nk = len(recs)
        mean_p = sum(p for p, _y in recs) / nk
        hit = sum(y for _p, y in recs) / nk
        reliability += nk * (mean_p - hit) ** 2
        resolution += nk * (hit - base) ** 2
        table.append((f"{k/10:.1f}-{(k+1)/10:.1f}", nk, mean_p, hit))
    reliability /= n
    resolution /= n
    uncertainty = base * (1 - base)
    return n, base, brier, reliability, resolution, uncertainty, table


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 120
    scope = "temp" if (len(sys.argv) > 2 and sys.argv[2] == "temp") else "all"

    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from sqlalchemy import text

    model_filter = "AND model_name LIKE 'weather_temp%'" if scope == "temp" else ""
    db = Database()
    await db.init()
    try:
        async with db.get_session() as session:
            result = await session.execute(text(f"""
                SELECT DISTINCT ON (market_id)
                    market_id, predicted_prob, resolution, model_name
                FROM prediction_log
                WHERE bot_name = 'WeatherBot'
                  AND resolution IN ('YES','NO')
                  AND predicted_prob IS NOT NULL
                  AND prediction_time > NOW() - INTERVAL '{days} days'
                  {model_filter}
                ORDER BY market_id, prediction_time DESC
            """))
            raw = result.fetchall()
    finally:
        try:
            await db.close()
        except Exception:
            pass

    if not raw:
        print("No resolved prediction_log rows in window.")
        return
    rows = [(float(pp), 1.0 if res == "YES" else 0.0) for _mid, pp, res, _mn in raw]

    n, base, brier, rel, reso, unc, table = _decompose(rows)
    print("=" * 70)
    print(f"WB CALIBRATION BACKTEST (double-blind) — last {days}d, scope={scope}")
    print("=" * 70)
    print(f"  markets (1 last-prediction each): {n}")
    print(f"  base rate P(YES)={base:.3f}   model Brier={brier:.4f}")
    print(f"  baseline (always base rate) Brier={unc:.4f}  "
          f"-> model {'BEATS' if brier < unc else 'LOSES TO'} baseline by {unc-brier:+.4f}")
    print(f"  reliability (calibration error, LOWER better)={rel:.4f}")
    print(f"  resolution (discrimination, HIGHER better)   ={reso:.4f}")
    print()
    print("  RELIABILITY TABLE — if model_P >> hit_rate, it is OVERCONFIDENT:")
    print(f"    {'pred bin':<10} {'n':>6} {'mean_pred':>10} {'actual_hit':>11} {'gap':>8}")
    for label, nk, mean_p, hit in table:
        gap = mean_p - hit
        flag = "  <<over" if gap > 0.08 else ("  <<under" if gap < -0.08 else "")
        print(f"    {label:<10} {nk:>6} {mean_p:>10.3f} {hit:>11.3f} {gap:>+8.3f}{flag}")
    print()
    print("  VERDICT:")
    if brier >= unc:
        print("  * Model Brier is WORSE than always guessing the base rate -> no usable skill.")
    if reso < 0.01:
        print("  * Resolution ~0 -> model CANNOT separate winners from losers -> DEAD (wind down).")
    elif rel > reso:
        print("  * Discriminates somewhat but miscalibration > discrimination -> overconfident.")
        print("    Recalibration could help IF resolution holds up — but it's a small base to build on.")
    else:
        print("  * Discrimination exceeds calibration error -> there IS rank-order skill here;")
        print("    recalibration is the lever. Worth a clean forward confirmation.")


if __name__ == "__main__":
    asyncio.run(main())
