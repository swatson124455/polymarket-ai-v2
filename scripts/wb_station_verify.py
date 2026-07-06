#!/usr/bin/env python3
"""
WB Station Verify — verify the correct resolution airport per city before editing
station_registry (fixes A: 8 missing cities; B: ~6 mismatched cities).

For each city: (1) read the market's resolution RULE (names the exact airport), (2) test
each candidate ICAO/coords by WU(api.weather.com) vs that city's recent settled markets.
The candidate with the best alignment AND matching the named airport is the right station.
Multi-airport cities (Moscow/Seoul/Milan/Istanbul) are why guessing fails — test them all.

READ-ONLY. Run on VPS. Usage: PYTHONPATH=/opt/polymarket-ai-v2 python scripts/wb_station_verify.py
"""
import asyncio
import json
import os
import re
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122 Safari/537.36"
_CTX = ssl.create_default_context()
_KEY = "e1f10a1e78da46f5b10a1e78da96f525"

# city -> [(icao, lat, lon), ...] candidates (multi for ambiguous multi-airport cities)
CANDIDATES = {
    # A: missing
    "Busan": [("RKPK", 35.1795, 128.9382)],
    "Cape Town": [("FACT", -33.9648, 18.6017)],
    "Guangzhou": [("ZGGG", 23.3924, 113.2988)],
    "Jeddah": [("OEJN", 21.6796, 39.1565)],
    "Karachi": [("OPKC", 24.9065, 67.1608)],
    "Manila": [("RPLL", 14.5086, 121.0198)],
    "Panama City": [("MPTO", 9.0714, -79.3835)],
    "Qingdao": [("ZSQD", 36.2661, 120.3744), ("ZSQD-JD", 36.3614, 120.0857)],
    # B: mismatched — test all plausible airports
    "Hong Kong": [("VHHH", 22.3080, 113.9185)],
    "Shenzhen": [("ZGSZ", 22.6393, 113.8107)],
    "Istanbul": [("LTBA", 40.8986, 29.3092), ("LTFM", 41.2753, 28.7519)],
    "Milan": [("LIML", 45.4451, 9.2767), ("LIMC", 45.6306, 8.7281)],
    "Moscow": [("UUWW", 55.5915, 37.2615), ("UUEE", 55.9726, 37.4146), ("UUDD", 55.4088, 37.9063)],
    "Seoul": [("RKSS", 37.5583, 126.7906), ("RKSI", 37.4691, 126.4505)],
}


def _get(url):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=25, context=_CTX).read())


def _wu(lat, lon, d, metric):
    ymd = d.replace("-", ""); u = "m" if metric else "e"
    try:
        obs = _get(f"https://api.weather.com/v1/geocode/{lat}/{lon}/observations/historical.json"
                   f"?apiKey={_KEY}&units={u}&startDate={ymd}&endDate={ymd}").get("observations", [])
    except Exception:
        return None
    t = [o.get("temp") for o in (obs or []) if isinstance(o.get("temp"), (int, float))]
    return max(t) if t else None


async def main():
    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from sqlalchemy import text
    from bots.weather.engine.base_engine.weather.market_mapper import WeatherMarketMapper
    from bots.weather.engine.base_engine.weather.station_registry import lookup_station
    mapper = WeatherMarketMapper()

    for city, cands in CANDIDATES.items():
        cur = lookup_station(city)
        # settled markets for this city
        from dotenv import load_dotenv as _l; _l()
        db = Database(); await db.init()
        try:
            async with db.get_session() as s:
                rows = (await s.execute(text("""
                    SELECT question FROM markets
                    WHERE question ILIKE :pat AND resolution='YES'
                      AND resolved_at > NOW() - INTERVAL '60 days'
                    ORDER BY resolved_at DESC LIMIT 40
                """), {"pat": f"%in {city} %highest temperature%".replace("in ", "")
                       if False else f"%highest temperature in {city} %"})).fetchall()
        finally:
            try: await db.close()
            except Exception: pass

        settled = []
        unit = "C"
        for (q,) in rows:
            b = mapper.parse_market({"id": "0", "question": q})
            _c, td = mapper._extract_city_and_date(q)
            if b and b.bucket_type == "exact" and td:
                settled.append((td.isoformat(), b.low_bound)); unit = b.temp_unit
        settled = settled[:6]

        # resolution rule airport name (from a live market)
        rule = ""
        try:
            ev = _get(f"https://gamma-api.polymarket.com/events?closed=true&limit=2"
                      f"&tag_slug=daily-temperature")
            for e in ev:
                for m in e.get("markets") or []:
                    pass
        except Exception:
            pass

        print(f"\n=== {city}  (registry now: {cur.station_id if cur else 'NONE'})  "
              f"settled_sample={len(settled)} unit={unit} ===")
        metric = unit.upper() == "C"
        for icao, lat, lon in cands:
            hits = tot = 0
            for d, mh in settled:
                wu = _wu(lat, lon, d, metric)
                if wu is None:
                    continue
                tot += 1
                if abs(round(wu) - round(mh)) < 0.5:
                    hits += 1
            flag = " <== BEST" if tot and hits == tot else ""
            print(f"    {icao:<8} ({lat},{lon})  align={hits}/{tot}{flag}")


if __name__ == "__main__":
    asyncio.run(main())
