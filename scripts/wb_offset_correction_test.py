#!/usr/bin/env python3
"""
WB Per-City Offset Correction — hardcode + OUT-OF-SAMPLE test, Phase-2.

Resolution-alignment check found our truth (ERA5 @ station) lands on a different
degree than the market settled on ~75% of the time (~1.3C scatter, mild cool bias).
Question: is that a FIXABLE per-city bias (airport-vs-source, gridded-vs-station)
or irreducible day-to-day noise?

Test: per city, learn offset = mean(ERA5 - market_high) on OLDER half (train),
apply to NEWER half (test), and see if alignment jumps vs uncorrected. Train/test
split = no fitting-and-grading on the same data (no look-ahead). The learned table
is printed as a copy-pasteable dict (the "hardcode"). Residual std after correction
= the irreducible noise that must go into the spread floor.

READ-ONLY. Run on VPS. Usage:
    PYTHONPATH=/opt/polymarket-ai-v2 python scripts/wb_offset_correction_test.py [days] [limit]
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
    limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 700

    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from sqlalchemy import text
    db = Database(); await db.init()
    try:
        async with db.get_session() as s:
            r = await s.execute(text(f"""
                SELECT id, question, resolved_at
                FROM markets
                WHERE question ILIKE '%%highest temperature%%'
                  AND resolution='YES' AND resolved_at > NOW() - INTERVAL '{days} days'
                ORDER BY resolved_at ASC
                LIMIT {limit}
            """))
            rows = r.fetchall()
    finally:
        try: await db.close()
        except Exception: pass

    mapper = WeatherMarketMapper()
    # per city: ordered list of (era5, market_high)
    by_city = {}
    for mid, q, _ts in rows:
        b = mapper.parse_market({"id": str(mid), "question": q})
        if not b or b.bucket_type != "exact":
            continue
        city, td = mapper._extract_city_and_date(q)
        if not city or not td:
            continue
        st = lookup_station(city)
        if not st:
            continue
        e = _era5(st.latitude, st.longitude, td.isoformat(), b.temp_unit)
        if e is None:
            continue
        by_city.setdefault(city, []).append((e, float(b.low_bound), b.temp_unit))

    # global offset (fallback) from all train data
    offsets_table = {}
    train_all = []
    for city, recs in by_city.items():
        cut = int(len(recs) * 0.6)
        train = recs[:cut]
        if len(train) >= 4:
            off = sum(e - m for e, m, _u in train) / len(train)
            offsets_table[city] = (round(off, 2), len(train))
        train_all.extend(train)
    global_off = (sum(e - m for e, m, _u in train_all) / len(train_all)) if train_all else 0.0

    # out-of-sample test on newer 40%
    unc_hit = cor_hit = n_test = 0
    resid = []
    for city, recs in by_city.items():
        cut = int(len(recs) * 0.6)
        test = recs[cut:]
        off = offsets_table.get(city, (round(global_off, 2), 0))[0]
        for e, m, _u in test:
            n_test += 1
            if abs(round(e) - m) < 0.5:
                unc_hit += 1
            corrected = e - off
            if abs(round(corrected) - m) < 0.5:
                cor_hit += 1
            resid.append(corrected - m)

    print("=" * 66)
    print(f"WB PER-CITY OFFSET CORRECTION — last {days}d, exact-degree YES")
    print("=" * 66)
    print(f"  cities with learnable offset: {len(offsets_table)}   "
          f"global fallback offset = {global_off:+.2f}")
    print(f"  test markets (newer 40%, out-of-sample): {n_test}")
    if n_test:
        print(f"  ALIGNMENT  uncorrected: {unc_hit/n_test:.1%}   "
              f"corrected (per-city): {cor_hit/n_test:.1%}")
        if resid:
            mr = sum(resid) / len(resid)
            sr = math.sqrt(sum((x - mr) ** 2 for x in resid) / len(resid))
            print(f"  RESIDUAL after correction: mean={mr:+.2f}  std={sr:.2f}  "
                  f"(<- spread floor must cover this)")
    print()
    print("  HARDCODED OFFSET TABLE (city -> ERA5-minus-settle, n_train):")
    print("  WB_CITY_RESOLUTION_OFFSET = {")
    for city, (off, ntr) in sorted(offsets_table.items(), key=lambda kv: -abs(kv[1][0])):
        print(f"      {city!r:<22}: {off:+.2f},   # n={ntr}")
    print("  }")
    print()
    print("  READ:")
    if n_test and cor_hit > unc_hit * 1.4:
        print("  * Per-city correction MATERIALLY improves out-of-sample alignment -> the gap")
        print("    is largely a FIXABLE structural per-city bias. Hardcode the table + correct.")
    elif n_test and cor_hit > unc_hit * 1.1:
        print("  * Modest improvement -> partly structural (correct it), partly noise (spread).")
    else:
        print("  * Correction barely helps -> the gap is mostly irreducible day-to-day noise;")
        print("    the fix is the SPREAD FLOOR (absorb it), not a per-city offset.")


if __name__ == "__main__":
    asyncio.run(main())
