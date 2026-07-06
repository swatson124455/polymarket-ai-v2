#!/usr/bin/env python3
"""
WB Resolution-Source Alignment — Phase-2 check for WB_REBUILD_PLAN.md.

A perfect forecast still loses if it is aimed at the wrong target. This checks
whether the temperature the MARKET settled on matches the temperature our truth
source (ERA5 at the bot's station) reports for the same city+date.

Method: take resolved exact-degree markets ("...be 31C on June 30?") that paid YES
-> that integer IS the market's official high. Fetch ERA5 actual high at the bot's
station coords for that city+date. Compare.

  - alignment rate = fraction where round(ERA5) == market's winning degree
  - mean/std OFFSET = ERA5_high - market_high  (systematic offset = a real bug:
    wrong station / source / rounding / timezone; the bot would be confidently
    wrong even with a good forecast)

READ-ONLY. Run on VPS. Usage:
    PYTHONPATH=/opt/polymarket-ai-v2 python scripts/wb_resolution_alignment.py [days] [limit]
"""
import asyncio
import json
import math
import os
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bots.weather.engine.base_engine.weather.market_mapper import WeatherMarketMapper
from bots.weather.engine.base_engine.weather.station_registry import lookup_station

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
_CTX = ssl.create_default_context()
_ERA5_CACHE = {}


def _era5_high(lat, lon, date_str, unit):
    key = (round(lat, 2), round(lon, 2), date_str, unit)
    if key in _ERA5_CACHE:
        return _ERA5_CACHE[key]
    u = "fahrenheit" if unit == "F" else "celsius"
    url = (f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
           f"&daily=temperature_2m_max&start_date={date_str}&end_date={date_str}"
           f"&temperature_unit={u}&timezone=UTC")
    try:
        d = json.loads(urllib.request.urlopen(
            urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=30, context=_CTX).read())
        vals = d.get("daily", {}).get("temperature_2m_max", [])
        out = vals[0] if vals and vals[0] is not None else None
    except Exception:
        out = None
    _ERA5_CACHE[key] = out
    return out


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 45
    limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 400

    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database()
    await db.init()
    try:
        async with db.get_session() as session:
            r = await session.execute(text(f"""
                SELECT id, question
                FROM markets
                WHERE question ILIKE '%%highest temperature%%'
                  AND resolution = 'YES'
                  AND resolved_at > NOW() - INTERVAL '{days} days'
                ORDER BY resolved_at DESC
                LIMIT {limit}
            """))
            rows = r.fetchall()
    finally:
        try:
            await db.close()
        except Exception:
            pass

    mapper = WeatherMarketMapper()
    offsets, hits, n_used = [], 0, 0
    no_station = set()
    examples = []
    for mid, q in rows:
        bucket = mapper.parse_market({"id": str(mid), "question": q})
        if not bucket or bucket.bucket_type != "exact":
            continue
        city, tdate = mapper._extract_city_and_date(q)
        if not city or not tdate:
            continue
        st = lookup_station(city)
        if not st:
            no_station.add(city)
            continue
        era5 = _era5_high(st.latitude, st.longitude, tdate.isoformat(), bucket.temp_unit)
        if era5 is None:
            continue
        market_high = bucket.low_bound  # exact-degree winner
        offset = era5 - market_high
        offsets.append(offset)
        if abs(offset) < 0.5:
            hits += 1
        n_used += 1
        if len(examples) < 12:
            examples.append((city, str(tdate), market_high, round(era5, 1), round(offset, 1), bucket.temp_unit))

    print("=" * 64)
    print(f"WB RESOLUTION-SOURCE ALIGNMENT — exact-degree YES markets, last {days}d")
    print("=" * 64)
    if not offsets:
        print("  no exact-degree resolved markets matched a station. "
              f"(unmatched cities: {sorted(no_station)[:10]})")
        return
    n = len(offsets)
    mean = sum(offsets) / n
    var = sum((o - mean) ** 2 for o in offsets) / n
    sd = math.sqrt(var)
    align = hits / n
    print(f"  markets compared: {n}   (ERA5 truth vs market's winning degree)")
    print(f"  ALIGNMENT (|ERA5 - market_high| < 0.5): {align:.1%}")
    print(f"  OFFSET (ERA5 - market_high):  mean={mean:+.2f}  std={sd:.2f}")
    print()
    print("  samples:  city / date / market_high / ERA5 / offset")
    for city, dt, mh, e, off, u in examples:
        print(f"    {city:<16} {dt}  mkt={mh:>5.0f}{u}  era5={e:>5.1f}  off={off:+.1f}")
    print()
    print("  READ:")
    if align >= 0.7 and abs(mean) < 0.5:
        print(f"  * ALIGNED ({align:.0%}, offset {mean:+.1f}). Market settles ~where our truth says.")
        print("    -> the historical 'confidently wrong' was NOT a source mismatch (points to")
        print("       the contaminated-pairing explanation instead).")
    elif abs(mean) >= 0.7:
        print(f"  * SYSTEMATIC OFFSET {mean:+.1f}{'(warmer)' if mean>0 else '(cooler)'} — REAL BUG.")
        print("    The market settles on a source/station/rounding that differs from ours;")
        print("    correct a bias of ~{:+.1f} or re-point the station, and 'confidently wrong'".format(-mean))
        print("    bets stop. This is directly fixable.")
    else:
        print(f"  * Low alignment ({align:.0%}) but no clean offset (std {sd:.1f}) -> noisy mapping")
        print("    (wrong station for some cities, or scattered rounding). Worth a per-city look.")


if __name__ == "__main__":
    asyncio.run(main())
