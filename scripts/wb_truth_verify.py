#!/usr/bin/env python3
"""
WB Truth Verification — Phase-2 rebuild, certifies items #1 and #6 vs REAL station truth.

The bot stores, per (station, date, lead), BOTH its own forecast_temp AND the observed
station daily-max (actual_temp) in weather_calibration. That is the bot's real target —
the station reading the market settles on — not the ERA5 grid proxy that muddied earlier
numbers. This certifies two rebuild items straight from clean DB data (no external fetch):

  A) ITEM #1 forecast skill vs REAL truth:  forecast_temp vs actual_temp
     -> MAE / bias / RMSE per lead (converted to C), corr(forecast, actual).
        (This replaces the optimistic ERA5/past_days number.)

  B) ITEM #6 right-target check:  actual_temp (our station truth) vs the market's settled
     degree (exact-YES markets). If these align FAR better than ERA5's 24.5%, the "wrong
     target" was ERA5's blur and our real target IS forecastable -> rebuild premise holds.

READ-ONLY. Run on VPS. Usage:
    PYTHONPATH=/opt/polymarket-ai-v2 python scripts/wb_truth_verify.py [days]
"""
import asyncio
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _f2c_err(e, unit):
    return e * (5.0 / 9.0) if unit == "F" else e


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 90

    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from sqlalchemy import text
    from bots.weather.engine.base_engine.weather.market_mapper import WeatherMarketMapper
    from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY, lookup_station

    unit_of = {s.station_id: (s.temp_unit or "C").upper() for s in STATION_REGISTRY.values()}

    db = Database(); await db.init()
    try:
        async with db.get_session() as s:
            cal = (await s.execute(text(f"""
                SELECT station_id, target_date::date AS d, forecast_temp, actual_temp, lead_time_hours
                FROM weather_calibration
                WHERE actual_temp IS NOT NULL
                  AND target_date > NOW() - INTERVAL '{days} days'
            """))).fetchall()
            mkts = (await s.execute(text(f"""
                SELECT id, question
                FROM markets
                WHERE question ILIKE '%%highest temperature%%'
                  AND resolution='YES' AND resolved_at > NOW() - INTERVAL '{days} days' AND resolved_at <= NOW()
                ORDER BY resolved_at DESC LIMIT 800
            """))).fetchall()
    finally:
        try: await db.close()
        except Exception: pass

    # ---- A) forecast skill vs real truth ----
    lead_err = {}          # bucket -> list of (err_C)
    all_err, preds, acts = [], [], []
    actual_by_key = {}     # (station_id, date) -> actual_temp (native unit)
    for st_id, d, fc, ac, lead in cal:
        if fc is None or ac is None:
            continue
        u = unit_of.get(st_id, "C")
        err = _f2c_err(fc - ac, u)
        all_err.append(err)
        # correlation in C-space
        preds.append(_f2c_err(fc, u) if u == "F" else fc)
        acts.append(_f2c_err(ac, u) if u == "F" else ac)
        b = ("<24h" if lead is not None and lead < 24 else
             "24-48h" if lead is not None and lead < 48 else
             "48-72h" if lead is not None and lead < 72 else "72h+")
        lead_err.setdefault(b, []).append(err)
        actual_by_key[(st_id, d.isoformat())] = ac

    def _stats(errs):
        n = len(errs)
        if not n: return (0, 0, 0, 0)
        bias = sum(errs) / n
        mae = sum(abs(e) for e in errs) / n
        rmse = math.sqrt(sum(e * e for e in errs) / n)
        return (n, bias, mae, rmse)

    print("=" * 68)
    print(f"WB TRUTH VERIFICATION (last {days}d) — bot forecast vs REAL station truth")
    print("=" * 68)
    print("\nA) ITEM #1 — FORECAST SKILL vs actual station high (weather_calibration, C):")
    n, bias, mae, rmse = _stats(all_err)
    if n:
        mp = sum(preds)/len(preds); ma = sum(acts)/len(acts)
        cov = sum((p-mp)*(a-ma) for p, a in zip(preds, acts))/len(preds)
        sp = math.sqrt(sum((p-mp)**2 for p in preds)/len(preds))
        sa = math.sqrt(sum((a-ma)**2 for a in acts)/len(acts))
        corr = cov/(sp*sa) if sp > 0 and sa > 0 else float("nan")
        print(f"   n={n}  MAE={mae:.2f}C  bias={bias:+.2f}C  RMSE={rmse:.2f}C  corr={corr:.3f}")
        print("   per lead:  " + "   ".join(
            f"{b}: MAE={_stats(e)[2]:.2f} RMSE={_stats(e)[3]:.2f} (n={_stats(e)[0]})"
            for b, e in sorted(lead_err.items())))
    else:
        print("   no calibration rows with actual_temp — cannot certify #1 this way.")

    # ---- B) target alignment: our station truth vs market settle ----
    mapper = WeatherMarketMapper()
    offs, hits, n_b = [], 0, 0
    matched = 0
    for mid, q in mkts:
        b = mapper.parse_market({"id": str(mid), "question": q})
        if not b or b.bucket_type != "exact":
            continue
        city, td = mapper._extract_city_and_date(q)
        if not city or not td:
            continue
        st = lookup_station(city)
        if not st:
            continue
        ac = actual_by_key.get((st.station_id, td.isoformat()))
        if ac is None:
            continue
        matched += 1
        off = ac - b.low_bound     # station truth minus market's settled degree
        offs.append(off)
        if abs(off) < 0.5:
            hits += 1
        n_b += 1

    print("\nB) ITEM #6 — RIGHT TARGET: our station truth vs market's settled degree:")
    if n_b:
        mo = sum(offs)/n_b
        so = math.sqrt(sum((o-mo)**2 for o in offs)/n_b)
        print(f"   exact-YES markets matched to a stored actual: {n_b}")
        print(f"   ALIGNMENT (|actual - market_high| < 0.5): {hits/n_b:.1%}  "
              f"(ERA5 baseline was 24.5%)")
        print(f"   OFFSET (our_actual - market_high): mean={mo:+.2f}  std={so:.2f}")
        print()
        if hits/n_b >= 0.7 and abs(mo) < 0.5:
            print("   => ALIGNED. Our station truth ~= the market's source. The 'wrong target'")
            print("      was ERA5's blur. Our real target IS forecastable -> REBUILD PREMISE HOLDS.")
            print("      Spread floor only needs to cover forecast error (A), not a target gap.")
        elif so <= 0.8 and abs(mo) >= 0.7:
            print(f"   => CLEAN OFFSET {mo:+.2f} with low scatter -> a FIXABLE systematic bias")
            print("      (source/rounding). Correct it per-station and alignment jumps.")
        else:
            print(f"   => Still scattered (std {so:.1f}). Target gap is real even vs our own truth")
            print("      -> the spread floor must absorb it; edge lives in wide buckets.")
    else:
        print("   no exact-YES markets matched a stored actual (station/date coverage gap).")


if __name__ == "__main__":
    asyncio.run(main())
