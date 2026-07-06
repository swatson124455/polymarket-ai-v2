#!/usr/bin/env python3
"""
WB Root-Cause + Only-Issue Verification — Phase-2 rebuild.

Code shows weather_calibration.actual_temp = Wunderground (the market's resolution source)
WHEN the scrape works, else it silently falls back to Open-Meteo/ERA5 (gridded, ~1.4C off
station highs). So the "1.4C wrong-target" gap should be an ARTIFACT of the ERA5-fallback
rows, not a real forecast/target problem.

Test: classify each stored actual by inferred source —
  ERA5-fallback if |actual - ERA5| < 0.3   (actual literally == the om_temp fallback)
  WU-sourced    otherwise                   (actual differs from ERA5 -> came from WU)
then split alignment-to-market by class.

  Root-cause CONFIRMED + ONLY issue  if: WU-sourced rows align ~perfectly with the market
    AND fallback rows carry the whole gap. (If WU-sourced ALSO misalign -> a second issue:
    wrong station or day-window.)

READ-ONLY. Run on VPS. Usage:
    PYTHONPATH=/opt/polymarket-ai-v2 python scripts/wb_rootcause.py [days]
"""
import asyncio
import json
import math
import os
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
_CTX = ssl.create_default_context()
_CACHE = {}


def _era5(lat, lon, d, unit):
    k = (round(lat, 2), round(lon, 2), d, unit)
    if k in _CACHE:
        return _CACHE[k]
    u = "fahrenheit" if unit == "F" else "celsius"
    url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
           f"&daily=temperature_2m_max&start_date={d}&end_date={d}&temperature_unit={u}&timezone=UTC")
    try:
        j = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=30, context=_CTX).read())
        v = j.get("daily", {}).get("temperature_2m_max", [])
        out = v[0] if v and v[0] is not None else None
    except Exception:
        out = None
    _CACHE[k] = out
    return out


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 90

    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from sqlalchemy import text
    from bots.weather.engine.base_engine.weather.market_mapper import WeatherMarketMapper
    from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY, lookup_station

    st_of = {s.station_id: s for s in STATION_REGISTRY.values()}
    unit_of = {sid: (s.temp_unit or "C").upper() for sid, s in st_of.items()}

    db = Database(); await db.init()
    try:
        async with db.get_session() as s:
            cal = (await s.execute(text(f"""
                SELECT station_id, target_date::date AS d, actual_temp
                FROM weather_calibration
                WHERE actual_temp IS NOT NULL AND target_date > NOW() - INTERVAL '{days} days'
            """))).fetchall()
            mkts = (await s.execute(text(f"""
                SELECT id, question FROM markets
                WHERE question ILIKE '%%highest temperature%%'
                  AND resolution='YES' AND resolved_at > NOW() - INTERVAL '{days} days' AND resolved_at <= NOW()
                ORDER BY resolved_at DESC LIMIT 800
            """))).fetchall()
    finally:
        try: await db.close()
        except Exception: pass

    bot_actual = {(r[0], r[1].isoformat()): r[2] for r in cal}
    mapper = WeatherMarketMapper()

    wu = {"n": 0, "hit": 0, "off": []}
    fb = {"n": 0, "hit": 0, "off": []}
    unclass = 0
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
        key = (st.station_id, td.isoformat())
        ac = bot_actual.get(key)
        if ac is None:
            continue
        unit = unit_of.get(st.station_id, b.temp_unit)
        e5 = _era5(st.latitude, st.longitude, td.isoformat(), unit)
        if e5 is None:
            unclass += 1
            continue
        mh = b.low_bound
        is_fallback = abs(ac - e5) < 0.3
        bucket = fb if is_fallback else wu
        bucket["n"] += 1
        off = ac - mh
        bucket["off"].append(off)
        if abs(round(ac) - round(mh)) < 0.5:
            bucket["hit"] += 1

    def _rep(name, g):
        if not g["n"]:
            print(f"  {name:<28} n=0"); return
        m = sum(g["off"]) / g["n"]
        sd = math.sqrt(sum((o - m) ** 2 for o in g["off"]) / g["n"])
        print(f"  {name:<28} n={g['n']:<4} align={g['hit']/g['n']:.0%}  "
              f"offset mean={m:+.2f} std={sd:.2f}")

    total = wu["n"] + fb["n"]
    print("=" * 64)
    print(f"WB ROOT-CAUSE — actual_temp source split (last {days}d, exact-YES)")
    print("=" * 64)
    print(f"  classified: {total}   (WU-sourced {wu['n']}, ERA5-fallback {fb['n']}, "
          f"unclassified {unclass})")
    if total:
        print(f"  ERA5-fallback share: {fb['n']/total:.0%}\n")
    _rep("WU-sourced (real target)", wu)
    _rep("ERA5-fallback (the bug)", fb)
    print()
    print("  VERDICT:")
    if wu["n"] >= 20 and fb["n"] >= 20:
        wu_al = wu["hit"] / wu["n"]; fb_al = fb["hit"] / fb["n"]
        if wu_al >= 0.7 and wu_al > fb_al * 1.5:
            print(f"  * CONFIRMED: WU-sourced rows align {wu_al:.0%} vs fallback {fb_al:.0%}.")
            print("    The gap is the ERA5-fallback bug — NOT a forecast/target problem.")
            print("    ROOT = weather_calibration falls back to gridded ERA5 when WU scrape fails.")
            wu_sd = math.sqrt(sum((o - sum(wu['off'])/wu['n'])**2 for o in wu['off'])/wu['n'])
            if wu_sd < 0.6:
                print(f"    ONLY ISSUE: WU-sourced residual std {wu_sd:.2f} ~ 0 -> no second confound")
                print("    (station + day-window are fine). Fix = reliable WU, never ERA5 for labels.")
            else:
                print(f"    BUT WU-sourced std {wu_sd:.2f} -> a SECOND smaller issue remains "
                      "(station/day-window).")
        else:
            print(f"  * NOT confirmed: WU-sourced align {wu_al:.0%}, fallback {fb_al:.0%} — the gap")
            print("    is not explained by the ERA5 fallback alone. Another issue dominates.")
    else:
        print("  * insufficient rows in one class to split cleanly; widen the window.")


if __name__ == "__main__":
    asyncio.run(main())
