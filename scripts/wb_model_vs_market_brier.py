#!/usr/bin/env python3
"""
WB Model-vs-Market Brier — Phase-2 scorer for WB_REBUILD_PLAN.md (Method 1, fast).

The decisive question: does WeatherBot's forecast predict actual outcomes BETTER
than the market's price does? Both made a probability call on the same bucket
markets; the markets then resolved YES/NO. We grade both against reality.

This is NOT a P&L number and there is no canonical tool for model-vs-market Brier
(weather_brier.py scores the model only, on traded markets). So this is a
purpose-built scorer with built-in sanity checks (printed below) — read those
before trusting the verdict.

Source: prediction_log (bot logs predicted_prob = model P(YES) AND market_price =
market P(YES) at prediction time). The `resolution` column is the AUTHORITATIVE
market outcome, backfilled by backfill_prediction_log_resolution() which EXCLUDES
rows whose resolution predates the prediction (no look-ahead). One row per market
= the LAST prediction before it resolved (same-moment model-vs-market comparison).

READ-ONLY. No writes.

Usage:
    PYTHONPATH=/opt/polymarket-ai-v2 python scripts/wb_model_vs_market_brier.py [days]
    Default: 60 days
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _brier(rows):
    """rows: list of (model_p, market_p, actual_yes). Returns (n, bs_model, bs_market, mean_model, mean_market, base_rate)."""
    n = len(rows)
    if not n:
        return (0, 0.0, 0.0, 0.0, 0.0, 0.0)
    bs_model = sum((m - a) ** 2 for m, _k, a in rows) / n
    bs_market = sum((k - a) ** 2 for _m, k, a in rows) / n
    mean_model = sum(m for m, _k, _a in rows) / n
    mean_market = sum(k for _m, k, _a in rows) / n
    base = sum(a for _m, _k, a in rows) / n
    return (n, bs_model, bs_market, mean_model, mean_market, base)


def _line(label, rows):
    n, bm, bk, mm, mk, base = _brier(rows)
    if not n:
        print(f"  {label:<22} n=0")
        return
    verdict = "MODEL wins" if bm < bk else ("MARKET wins" if bk < bm else "tie")
    delta = bk - bm  # positive = model better
    print(f"  {label:<22} n={n:<5} BS_model={bm:.4f}  BS_market={bk:.4f}  "
          f"Δ={delta:+.4f}  [{verdict}]")


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 60

    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from sqlalchemy import text

    db = Database()
    await db.init()
    try:
        async with db.get_session() as session:
            # One row per market = last prediction before resolution (no look-ahead:
            # backfill already excludes resolved_at < prediction_time).
            result = await session.execute(text(f"""
                SELECT DISTINCT ON (market_id)
                    market_id,
                    predicted_prob,
                    market_price,
                    resolution,
                    model_name
                FROM prediction_log
                WHERE bot_name = 'WeatherBot'
                  AND resolution IN ('YES','NO')
                  AND predicted_prob IS NOT NULL
                  AND market_price IS NOT NULL
                  AND prediction_time > NOW() - INTERVAL '{days} days'
                ORDER BY market_id, prediction_time DESC
            """))
            raw = result.fetchall()
    finally:
        try:
            await db.close()
        except Exception:
            pass

    if not raw:
        print("No resolved prediction_log rows for WeatherBot in window. "
              "(Either backfill hasn't populated `resolution`, or no predictions logged.)")
        return

    all_rows = []          # (model_p, market_p, actual_yes)
    by_model = {}          # model_name -> rows
    for market_id, pp, mp, res, mname in raw:
        actual = 1.0 if res == "YES" else 0.0
        rec = (float(pp), float(mp), actual)
        all_rows.append(rec)
        by_model.setdefault(mname or "?", []).append(rec)

    n, bm, bk, mm, mk, base = _brier(all_rows)
    print("=" * 72)
    print(f"WB MODEL vs MARKET — Brier on resolved markets (last {days}d)")
    print("=" * 72)
    print(f"  markets scored (deduped, 1 last-prediction each): {n}")
    print(f"  base rate P(YES)={base:.3f}   "
          f"mean model P={mm:.3f}   mean market P={mk:.3f}")
    print()
    print("  --- SANITY CHECKS (read before trusting) ---")
    print(f"  * market should be ~calibrated: mean market P ({mk:.3f}) vs base rate ({base:.3f}) "
          f"-> diff {abs(mk-base):.3f} (small = YES-referencing correct)")
    bs_const = base * (1 - base)
    print(f"  * always-base-rate Brier = {bs_const:.4f} (a dumb baseline; both should beat it)")
    print()
    print("  --- VERDICT (lower Brier = better predictor) ---")
    _line("OVERALL", all_rows)
    print("  by model type:")
    for mname, rows in sorted(by_model.items(), key=lambda kv: -len(kv[1])):
        _line(f"    {mname}", rows)
    print()
    if bm < bk:
        print(f"  => MODEL beats market by {bk-bm:+.4f} Brier. Signal worth pursuing — "
              f"validate on the clean forward snapshot before believing it.")
    else:
        print(f"  => MARKET beats (or ties) model ({bk-bm:+.4f}). No edge in the logged "
              f"history; the overconfidence read holds. Confirm on forward snapshot.")


if __name__ == "__main__":
    asyncio.run(main())
