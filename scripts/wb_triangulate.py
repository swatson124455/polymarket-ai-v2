#!/usr/bin/env python3
"""
WB Data Triangulation — Phase-2 rebuild, verifies our data vs an INDEPENDENT 3rd party.

The #6 "1.4C wrong target" finding compared two of OUR OWN unverified numbers (the bot's
scraped station high vs our DB copy of the market resolution). This introduces a trusted
third leg — Meteostat (keyless bulk archive aggregating METAR/GHCN/synop) — so we can tell
which link is actually broken:

  bot_actual vs Meteostat   -> is our OWN truth-collection correct?
  Meteostat vs market_high  -> is the market forecastable from real truth?
  bot_actual vs market_high -> the 1.4C gap we saw

Also pulls a few markets' resolution rules from Polymarket Gamma (what source they name).

READ-ONLY. Run on VPS. Usage:
    PYTHONPATH=/opt/polymarket-ai-v2 python scripts/wb_triangulate.py [days] [n_stations]
"""
import asyncio
import gzip
import io
import json
import math
import os
import ssl
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120 Safari/537.36"
_CTX = ssl.create_default_context()


def _get_bytes(url):
    return urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": _UA}), timeout=90, context=_CTX).read()


def _get_json(url):
    return json.loads(_get_bytes(url).decode())


def _icao_to_meteostat():
    """Map ICAO -> Meteostat station id from the keyless bulk stations dump."""
    raw = _get_bytes("https://bulk.meteostat.net/v2/stations/full.json.gz")
    data = json.loads(gzip.decompress(raw).decode())
    m = {}
    for st in data:
        icao = (st.get("identifiers") or {}).get("icao")
        if icao:
            m[icao.upper()] = st["id"]
    return m


def _meteostat_daily_tmax(mid):
    """{date_str: tmax_C} from Meteostat daily bulk CSV (date,tavg,tmin,tmax,...)."""
    try:
        raw = _get_bytes(f"https://bulk.meteostat.net/v2/daily/{mid}.csv.gz")
        txt = gzip.decompress(raw).decode()
    except Exception:
        return {}
    out = {}
    for line in txt.splitlines():
        f = line.split(",")
        if len(f) > 3 and f[0] and f[3]:
            try:
                out[f[0]] = float(f[3])
            except ValueError:
                pass
    return out


def _c(v, unit):
    return (v - 32.0) * 5.0 / 9.0 if unit == "F" else v


async def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 90
    n_stations = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 20

    from dotenv import load_dotenv
    load_dotenv()
    from base_engine.data.database import Database
    from sqlalchemy import text
    from bots.weather.engine.base_engine.weather.market_mapper import WeatherMarketMapper
    from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY, lookup_station

    unit_of = {s.station_id: (s.temp_unit or "C").upper() for s in STATION_REGISTRY.values()}
    icao_of = {s.station_id: s.station_id for s in STATION_REGISTRY.values()}  # station_id IS the ICAO

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
                  AND resolution='YES' AND resolved_at > NOW() - INTERVAL '{days} days'
                ORDER BY resolved_at DESC LIMIT 800
            """))).fetchall()
    finally:
        try: await db.close()
        except Exception: pass

    bot_actual = {(r[0], r[1].isoformat()): r[2] for r in cal}

    # market exact-YES -> (station_id, date, market_high_C)
    mapper = WeatherMarketMapper()
    triples = []  # (station_id, date, market_high_C)
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
        triples.append((st.station_id, td.isoformat(), _c(b.low_bound, b.temp_unit)))

    # pick the busiest stations that also have bot_actual
    from collections import Counter
    freq = Counter(sid for sid, d, _mh in triples if (sid, d) in bot_actual)
    top = [sid for sid, _n in freq.most_common(n_stations)]
    print(f"Mapping {len(top)} busiest stations to Meteostat + fetching archive...")
    try:
        icao_map = _icao_to_meteostat()
    except Exception as e:
        print(f"  meteostat stations dump failed: {e}"); return
    ms_cache = {}
    for sid in top:
        msid = icao_map.get(sid.upper())
        ms_cache[sid] = _meteostat_daily_tmax(msid) if msid else {}

    # triangulate
    ba_ms, ms_mk, ba_mk = [], [], []   # (diff in C)
    ba_ms_hit = ms_mk_hit = ba_mk_hit = 0
    n = 0
    for sid, d, mh_c in triples:
        if sid not in top:
            continue
        ba = bot_actual.get((sid, d))
        ms = ms_cache.get(sid, {}).get(d)
        if ba is None or ms is None:
            continue
        ba_c = _c(ba, unit_of.get(sid, "C"))
        n += 1
        ba_ms.append(ba_c - ms)
        ms_mk.append(ms - mh_c)
        ba_mk.append(ba_c - mh_c)
        if abs(ba_c - ms) < 0.7: ba_ms_hit += 1
        if abs(round(ms) - round(mh_c)) < 0.5: ms_mk_hit += 1
        if abs(round(ba_c) - round(mh_c)) < 0.5: ba_mk_hit += 1

    def _st(xs):
        k = len(xs)
        if not k: return (0, 0.0, 0.0)
        m = sum(xs) / k
        return (k, m, math.sqrt(sum((x - m) ** 2 for x in xs) / k))

    print("\n" + "=" * 66)
    print(f"WB TRIANGULATION — 3-way, {n} (station,date) with all three sources (C)")
    print("=" * 66)
    if not n:
        print("  no overlapping triples (Meteostat mapping/coverage gap).");
    else:
        for lbl, diffs, hit in [
            ("bot_actual vs METEOSTAT ", ba_ms, ba_ms_hit),
            ("METEOSTAT vs market_high", ms_mk, ms_mk_hit),
            ("bot_actual vs market_high", ba_mk, ba_mk_hit),
        ]:
            k, mean, sd = _st(diffs)
            print(f"  {lbl}:  n={k}  mean={mean:+.2f}  std={sd:.2f}  agree(<0.7)={hit/k:.0%}")
        print()
        _, ba_ms_m, ba_ms_s = _st(ba_ms)
        _, ms_mk_m, ms_mk_s = _st(ms_mk)
        print("  READ:")
        if ba_ms_s < 0.7 and abs(ba_ms_m) < 0.5:
            print("  * Our bot_actual MATCHES the independent archive -> our truth-collection is")
            print("    fine. So the gap to the market is REAL -> the market settles on a different")
            print("    source/day-window. Crack the resolution rule (below).")
        else:
            print(f"  * Our bot_actual DIVERGES from the archive (std {ba_ms_s:.1f}) -> OUR truth-")
            print("    collection is (partly) buggy. Fix that first; the '1.4C gap' is partly ours.")
        if ms_mk_s < 0.9:
            print(f"  * Archive vs market std {ms_mk_s:.1f} — if tighter than bot-vs-market, the market")
            print("    aligns better with a clean archive than with our scrape.")

    # resolution rules
    print("\n  RESOLUTION RULES (sample of 3 live markets):")
    try:
        ev = _get_json("https://gamma-api.polymarket.com/events?active=true&closed=false"
                       "&tag_slug=daily-temperature&limit=3")
        for e in ev[:3]:
            for m in (e.get("markets") or [])[:1]:
                desc = (m.get("description") or "")[:300].replace("\n", " ")
                print(f"    - {(m.get('question') or '')[:55]}")
                print(f"      {desc}")
    except Exception as e:
        print(f"    (rule fetch failed: {e})")


if __name__ == "__main__":
    asyncio.run(main())
