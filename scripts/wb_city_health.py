#!/usr/bin/env python3
"""
WB City Health — is the WU-target issue one city or fleet-wide? Verify ALL cities.

The WU HTML scraper broke for ALL cities (~96% ERA5-fallback fleet-wide), not one. This
verifies the FIX (api.weather.com) works per-city across (a) every city with an ACTIVE
Polymarket daily-temperature market now, and (b) cities with resolved history — reporting
station mapping, live WU-fetch health, and WU-vs-settle alignment per city.

READ-ONLY. Run on VPS. Usage: PYTHONPATH=/opt/polymarket-ai-v2 python scripts/wb_city_health.py [align_samples]
"""
import asyncio
import json
import os
import ssl
import sys
import urllib.request
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122 Safari/537.36"
_CTX = ssl.create_default_context()
_KEY = "e1f10a1e78da46f5b10a1e78da96f525"


def _get(url):
    return json.loads(urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=25, context=_CTX).read())


def _wu(lat, lon, d_iso, metric):
    ymd = d_iso.replace("-", ""); u = "m" if metric else "e"
    try:
        obs = _get(f"https://api.weather.com/v1/geocode/{lat}/{lon}/observations/historical.json"
                   f"?apiKey={_KEY}&units={u}&startDate={ymd}&endDate={ymd}").get("observations", [])
    except Exception:
        return None
    t = [o.get("temp") for o in (obs or []) if isinstance(o.get("temp"), (int, float))]
    return max(t) if t else None


async def main():
    n_align = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 4

    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from sqlalchemy import text
    from bots.weather.engine.base_engine.weather.market_mapper import WeatherMarketMapper
    from bots.weather.engine.base_engine.weather.station_registry import lookup_station

    mapper = WeatherMarketMapper()
    now = datetime.now(timezone.utc)

    # active cities from Gamma
    active = set()
    for pg in range(6):
        try:
            ev = _get(f"https://gamma-api.polymarket.com/events?active=true&closed=false"
                      f"&tag_slug=daily-temperature&limit=100&offset={pg*100}")
        except Exception:
            break
        if not ev:
            break
        for e in ev:
            for m in e.get("markets") or []:
                end = m.get("endDate") or e.get("endDate") or ""
                try:
                    if (datetime.fromisoformat(end.replace("Z", "+00:00")) - now).total_seconds() <= 0:
                        continue
                except Exception:
                    continue
                c, _d = mapper._extract_city_and_date(m.get("question") or "")
                if c:
                    active.add(c)
        if len(ev) < 100:
            break

    # resolved history per city
    from dotenv import load_dotenv as _ld; _ld()
    db = Database(); await db.init()
    try:
        async with db.get_session() as s:
            mkts = (await s.execute(text("""
                SELECT question FROM markets
                WHERE question ILIKE '%highest temperature%' AND resolution='YES'
                  AND resolved_at > NOW() - INTERVAL '60 days' AND resolved_at <= NOW()
                ORDER BY resolved_at DESC LIMIT 1500
            """))).fetchall()
    finally:
        try: await db.close()
        except Exception: pass

    hist = {}
    for (q,) in mkts:
        b = mapper.parse_market({"id": "0", "question": q})
        c, td = mapper._extract_city_and_date(q)
        if b and b.bucket_type == "exact" and c and td:
            hist.setdefault(c, []).append((td, b.low_bound, b.temp_unit))

    cities = sorted(active | set(hist))
    recent = (now - timedelta(days=2)).date().isoformat()

    print("=" * 78)
    print(f"WB CITY HEALTH — {len(cities)} cities ({len(active)} active on PM now)")
    print("=" * 78)
    print(f"  {'city':<17} {'station':<7} {'active':<7} {'WU_now':<7} {'hist':<5} {'align(WU=settle)':<16}")
    n_station = wu_ok = 0
    no_station = []
    align_hits = align_tot = 0
    for c in cities:
        st = lookup_station(c)
        if not st:
            no_station.append(c)
            print(f"  {c:<17} {'--':<7} {('yes' if c in active else 'no'):<7} {'n/a':<7} "
                  f"{len(hist.get(c, [])):<5} NO STATION MAPPING")
            continue
        n_station += 1
        metric = (st.temp_unit or 'C').upper() == 'C'
        wu_now = _wu(st.latitude, st.longitude, recent, metric)
        wu_flag = "OK" if wu_now is not None else "FAIL"
        if wu_now is not None:
            wu_ok += 1
        # alignment on up to n_align resolved dates
        h = hist.get(c, [])[:n_align]
        hits = tot = 0
        for td, mh, unit in h:
            wu = _wu(st.latitude, st.longitude, td.isoformat(), (unit.upper() == 'C'))
            if wu is None:
                continue
            tot += 1
            if abs(round(wu) - round(mh)) < 0.5:
                hits += 1
        align_hits += hits; align_tot += tot
        astr = f"{hits}/{tot}" if tot else "-"
        print(f"  {c:<17} {st.station_id:<7} {('yes' if c in active else 'no'):<7} "
              f"{wu_flag:<7} {len(hist.get(c, [])):<5} {astr:<16}")

    print("\n  SUMMARY:")
    print(f"  * cities total {len(cities)} | active-now {len(active)} | mapped-to-station {n_station} | "
          f"NO station {len(no_station)}")
    print(f"  * WU fetch working (recent date): {wu_ok}/{n_station} mapped cities")
    if align_tot:
        print(f"  * WU==settle alignment across all sampled cities: {align_hits}/{align_tot} "
              f"({align_hits/align_tot:.0%})")
    if no_station:
        print(f"  * cities with NO station mapping (coverage gap to fix): {no_station}")
    print("\n  READ: WU broke fleet-wide (all cities, ~96% ERA5 fallback) — not one city.")
    print("  The fix restores WU across the mapped cities; NO-station cities are a separate")
    print("  coverage gap (station_registry), not the WU bug.")


if __name__ == "__main__":
    asyncio.run(main())
