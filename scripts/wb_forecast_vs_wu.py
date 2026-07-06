#!/usr/bin/env python3
"""
WB Forecast-vs-WU Pinpoint — measure the forecast against the REAL target (Wunderground).

Until now forecast skill was scored against the bot's actual_temp, which was ERA5 96% of the
time (wrong source). Now we fetch WU (api.weather.com, the market's real source) and score the
bot's forecast_temp against it, per lead. This pinpoints the systematic bias (the "-1.5") —
EMOS-fixable — vs random scatter (must go into the spread floor).

READ-ONLY. Run on VPS. Usage: PYTHONPATH=/opt/polymarket-ai-v2 python scripts/wb_forecast_vs_wu.py [days] [n]
"""
import asyncio
import json
import math
import os
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122 Safari/537.36"
_CTX = ssl.create_default_context()
_KEY = "e1f10a1e78da46f5b10a1e78da96f525"


def _wu(lat, lon, d, metric):
    ymd = d.replace("-", ""); u = "m" if metric else "e"
    url = (f"https://api.weather.com/v1/geocode/{lat}/{lon}/observations/historical.json"
           f"?apiKey={_KEY}&units={u}&startDate={ymd}&endDate={ymd}")
    try:
        obs = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=25, context=_CTX).read()).get("observations", [])
    except Exception:
        return None
    t = [o.get("temp") for o in (obs or []) if isinstance(o.get("temp"), (int, float))]
    return max(t) if t else None


def _toC(v, unit):
    return (v - 32.0) * 5.0 / 9.0 if unit == "F" else v


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 60
    n_want = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 120

    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from sqlalchemy import text
    from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY
    st_of = {s.station_id: s for s in STATION_REGISTRY.values()}

    db = Database(); await db.init()
    try:
        async with db.get_session() as s:
            rows = (await s.execute(text(f"""
                SELECT station_id, target_date::date AS d, forecast_temp, lead_time_hours
                FROM weather_calibration
                WHERE forecast_temp IS NOT NULL AND target_date > NOW() - INTERVAL '{days} days'
                ORDER BY target_date DESC LIMIT 4000
            """))).fetchall()
    finally:
        try: await db.close()
        except Exception: pass

    lead_err = {}; allc = []
    n = 0; seen = set()
    for st_id, d, fc, lead in rows:
        if n >= n_want:
            break
        st = st_of.get(st_id)
        if not st:
            continue
        key = (st_id, d.isoformat())
        if key in seen:
            continue
        seen.add(key)
        unit = (st.temp_unit or "C").upper()
        wu = _wu(st.latitude, st.longitude, d.isoformat(), unit == "C")
        if wu is None:
            continue
        n += 1
        err = _toC(fc, unit) - _toC(wu, unit)   # forecast minus real target, in C
        allc.append(err)
        b = ("<24h" if lead is not None and lead < 24 else "24-48h" if lead is not None and lead < 48
             else "48-72h" if lead is not None and lead < 72 else "72h+")
        lead_err.setdefault(b, []).append(err)

    def _st(e):
        k = len(e)
        if not k: return (0, 0, 0, 0)
        m = sum(e) / k
        return (k, m, sum(abs(x) for x in e) / k, math.sqrt(sum(x * x for x in e) / k))

    print("=" * 60)
    print(f"WB FORECAST vs REAL TARGET (Wunderground) — {n} station-days (C)")
    print("=" * 60)
    k, bias, mae, rmse = _st(allc)
    print(f"  OVERALL: bias={bias:+.2f}C  MAE={mae:.2f}C  RMSE={rmse:.2f}C")
    print("  per lead:")
    for b in ("<24h", "24-48h", "48-72h", "72h+"):
        if b in lead_err:
            kk, bb, mm, rr = _st(lead_err[b])
            resid = math.sqrt(max(rr * rr - bb * bb, 0.0))
            print(f"    {b:<8} n={kk:<4} bias={bb:+.2f}  RMSE={rr:.2f}  "
                  f"scatter-after-debias={resid:.2f}")
    print()
    print("  READ:")
    print(f"  * SYSTEMATIC BIAS = {bias:+.2f}C -> EMOS fixes this once it learns vs WU (the fix).")
    print(f"  * RANDOM SCATTER (RMSE after de-bias) -> the honest SPREAD FLOOR for #2.")
    if bias < -0.4:
        print(f"  * Forecast runs COOL vs WU by {-bias:.1f}C (WU station highs > gridded model) —")
        print("    this is 'the -1.5' and it's a correctable bias, not lost skill.")


if __name__ == "__main__":
    asyncio.run(main())
