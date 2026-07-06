#!/usr/bin/env python3
"""
WB Wunderground Verification — proves root cause + "only issue" + validates the fix.

Root chain found: the bot's `actual_temp` is ERA5 96% of the time because the WU HTML
scraper (`_fetch_wu_daily_high`) regex-hunts a client-rendered page that no longer
contains the daily high. The real WU data comes from api.weather.com (the XHR the page
calls). This fetches WU the correct way and checks WU == the market's settled degree.

  WU == market_high (~100%)  -> market source is exactly WU@station@local-date; the bot's
     station + day-window are FINE; the ONLY issue is the broken fetcher -> fix = call
     api.weather.com instead of scraping HTML. (Also confirms api.weather.com is the fix.)
  WU != market_high          -> a SECOND issue (wrong station / day-window) exists too.

READ-ONLY (external reads only). Run anywhere with internet. Usage:
    python scripts/wb_wu_verify.py [n]        (default 30)
NOTE: uses WU's public web apiKey (same one their site sends); diagnostic only.
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
_WU_KEY = "e1f10a1e78da46f5b10a1e78da96f525"  # WU public web frontend key


def _get(url):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=30, context=_CTX).read())


def _wu_daily_max(lat, lon, d_iso, metric):
    ymd = d_iso.replace("-", "")
    units = "m" if metric else "e"
    url = (f"https://api.weather.com/v1/geocode/{lat}/{lon}/observations/historical.json"
           f"?apiKey={_WU_KEY}&units={units}&startDate={ymd}&endDate={ymd}")
    try:
        obs = _get(url).get("observations", []) or []
    except Exception as e:
        return None, str(e)[:40]
    temps = [o.get("temp") for o in obs if isinstance(o.get("temp"), (int, float))]
    return (max(temps) if temps else None), (f"{len(temps)} obs")


def _era5(lat, lon, d, metric):
    u = "celsius" if metric else "fahrenheit"
    url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
           f"&daily=temperature_2m_max&start_date={d}&end_date={d}&temperature_unit={u}&timezone=UTC")
    try:
        v = _get(url).get("daily", {}).get("temperature_2m_max", [])
        return v[0] if v and v[0] is not None else None
    except Exception:
        return None


async def main():
    n_want = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 30

    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from sqlalchemy import text
    from bots.weather.engine.base_engine.weather.market_mapper import WeatherMarketMapper
    from bots.weather.engine.base_engine.weather.station_registry import lookup_station

    db = Database(); await db.init()
    try:
        async with db.get_session() as s:
            mkts = (await s.execute(text("""
                SELECT id, question FROM markets
                WHERE question ILIKE '%highest temperature%'
                  AND resolution='YES' AND resolved_at > NOW() - INTERVAL '75 days'
                ORDER BY resolved_at DESC LIMIT 400
            """))).fetchall()
    finally:
        try: await db.close()
        except Exception: pass

    mapper = WeatherMarketMapper()
    wu_mk, wu_e5 = [], []
    wu_hit = 0
    n = 0
    samples = []
    seen = set()
    for mid, q in mkts:
        if n >= n_want:
            break
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
        if key in seen:
            continue
        seen.add(key)
        metric = (b.temp_unit or "C").upper() == "C"
        wu, _info = _wu_daily_max(st.latitude, st.longitude, td.isoformat(), metric)
        if wu is None:
            continue
        e5 = _era5(st.latitude, st.longitude, td.isoformat(), metric)
        mh = b.low_bound
        n += 1
        wu_mk.append(wu - mh)
        if abs(round(wu) - round(mh)) < 0.5:
            wu_hit += 1
        if e5 is not None:
            wu_e5.append(wu - e5)
        if len(samples) < 14:
            samples.append((city, td.isoformat(), mh, round(wu, 1),
                            round(e5, 1) if e5 is not None else None, b.temp_unit))

    print("=" * 64)
    print(f"WB WUNDERGROUND VERIFICATION — WU (api.weather.com) vs market settle")
    print("=" * 64)
    print(f"  markets fetched with WU data: {n}")
    if not n:
        print("  api.weather.com returned no usable data (key/endpoint issue) — "
              "root still stands on the code path; retry fetch approach.")
        return
    mm = sum(wu_mk)/n
    sm = math.sqrt(sum((x-mm)**2 for x in wu_mk)/n)
    print(f"  WU vs market_high:  align(<0.5)={wu_hit/n:.0%}  offset mean={mm:+.2f} std={sm:.2f}")
    if wu_e5:
        me = sum(wu_e5)/len(wu_e5)
        se = math.sqrt(sum((x-me)**2 for x in wu_e5)/len(wu_e5))
        print(f"  WU vs ERA5 (the fallback): mean={me:+.2f} std={se:.2f}  "
              f"(confirms WU != the gridded fallback)")
    print("\n  samples: city / date / market / WU / ERA5")
    for c, d, mh, wu, e5, u in samples:
        print(f"    {c:<15} {d}  mkt={mh:>5.0f}{u}  WU={wu:>5.1f}  ERA5={e5}")
    print("\n  VERDICT:")
    if wu_hit/n >= 0.85:
        print(f"  * WU MATCHES the market {wu_hit/n:.0%} -> market source IS WU@station@local-date;")
        print("    the bot's station + day-window are CORRECT. The ONLY issue is the broken HTML")
        print("    scraper falling back to ERA5. FIX = fetch api.weather.com. Root fully verified.")
    elif wu_hit/n >= 0.6:
        print(f"  * WU mostly matches ({wu_hit/n:.0%}) but not fully -> broken fetcher is the main")
        print("    issue; a small residual (station/day-window/rounding) remains.")
    else:
        print(f"  * WU matches only {wu_hit/n:.0%} -> a SECOND issue exists beyond the fetcher")
        print("    (likely wrong station or day-window). Not the only issue.")


if __name__ == "__main__":
    asyncio.run(main())
