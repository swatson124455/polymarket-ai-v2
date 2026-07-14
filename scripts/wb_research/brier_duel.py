#!/usr/bin/env python3
"""S229 read-only Brier duel: RAW stored ensemble vs MARKET (CLOB price history)
at matched information time, on CLOB-resolved temperature markets.

Data cleanliness:
- outcomes: markets.resolution (written by resolution backfill FROM CLOB; spot-verified)
- forecast: weather_forecasts.ensemble_members = exactly what the bot saw (pre-correction)
- market prob: CLOB /prices-history for the YES token at the forecast's timestamp
"""
import json, math, re, subprocess, sys, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

MONTHS = {m: i + 1 for i, m in enumerate(
    ["January","February","March","April","May","June","July","August",
     "September","October","November","December"])}

# city -> (station_id, tz) pulled from the release registry via a tiny import
sys.path.insert(0, "/opt/polymarket-ai-v2-weather")
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY  # noqa
CITY = {}
for s in STATION_REGISTRY.values():
    CITY[s.city_name.lower()] = (s.station_id, s.timezone, s.temp_unit)

def parse_q(q):
    m = re.match(r"Will the (highest|lowest) temperature in (.+?) be (.+?) on (\w+) (\d+)\?", q)
    if not m: return None
    kind, city, spec, mon, day = m.groups()
    if kind != "highest": return None  # highs only (lows = different WF variable)
    if mon not in MONTHS: return None
    lo = hi = None
    s = spec.replace("°", "")
    mm = re.match(r"between (-?\d+)-(-?\d+)[FC]", s)
    if mm: lo, hi = float(mm.group(1)), float(mm.group(2))
    else:
        mm = re.match(r"(-?\d+)[FC] or (below|lower)", s)
        if mm: lo, hi = -1e9, float(mm.group(1))
        else:
            mm = re.match(r"(-?\d+)[FC] or (above|higher)", s)
            if mm: lo, hi = float(mm.group(1)), 1e9
            else:
                mm = re.match(r"(-?\d+)[FC]$", s)
                if mm: lo = hi = float(mm.group(1))
    if lo is None: return None
    return city, MONTHS[mon], int(day), lo, hi

def bucket_prob(members, lo, hi):
    # resolution rounds to integer: bucket verifies when rounded max in [lo, hi]
    a, b = lo - 0.5, hi + 0.5
    n = sum(1 for m in members if a <= m < b)
    p = n / len(members)
    return min(max(p, 1.0 / (len(members) + 2)), 1 - 1.0 / (len(members) + 2))

def clob_price_at(token, ts):
    url = (f"https://clob.polymarket.com/prices-history?market={token}"
           f"&startTs={ts-5400}&endTs={ts+5400}&fidelity=30")
    try:
        with urllib.request.urlopen(urllib.request.Request(
                url, headers={"User-Agent": "wb-research/1.0"}), timeout=15) as r:
            h = json.loads(r.read()).get("history", [])
    except Exception:
        return None
    if not h: return None
    best = min(h, key=lambda x: abs(x["t"] - ts))
    return float(best["p"]) if abs(best["t"] - ts) <= 7200 else None

def main():
    payload = json.loads(sys.stdin.read())
    fmap = {}
    for f in payload["forecasts"]:
        fmap.setdefault((f["sid"], f["td"]), []).append(f)
    duel = []
    skipped = {"parse": 0, "city": 0, "fc": 0, "px": 0}
    for r in payload["markets"]:
        pq = parse_q(r["question"])
        if not pq: skipped["parse"] += 1; continue
        city, mon, day, lo, hi = pq
        st = CITY.get(city.lower())
        if not st: skipped["city"] += 1; continue
        sid, tzname, unit = st
        year = int(r["end_date"][:4]) if r["end_date"] else 2026
        td = f"{year:04d}-{mon:02d}-{day:02d}"
        flist = fmap.get((sid, td), [])
        # forecast row nearest to 24h-before-local-noon of target day
        noon = datetime(year, mon, day, 12, 0, tzinfo=ZoneInfo(tzname))
        tgt = (noon - timedelta(hours=24)).astimezone(timezone.utc).replace(tzinfo=None)
        cands = [f for f in flist
                 if abs((datetime.fromisoformat(f["ft"]) - tgt).total_seconds()) < 14 * 3600]
        if not cands: skipped["fc"] += 1; continue
        f = min(cands, key=lambda f: abs((datetime.fromisoformat(f["ft"]) - tgt).total_seconds()))
        members = f["members"]
        if not members or len(members) < 10: skipped["fc"] += 1; continue
        p_ens = bucket_prob(members, lo, hi)
        ts = int(datetime.fromisoformat(f["ft"]).replace(tzinfo=timezone.utc).timestamp())
        p_mkt = clob_price_at(r["token"], ts)
        if p_mkt is None or not (0.001 < p_mkt < 0.999): skipped["px"] += 1; continue
        y = 1.0 if r["resolution"] == "YES" else 0.0
        duel.append((unit, p_ens, p_mkt, y, city, f"{lo}-{hi}"))
    if not duel:
        print("no duel rows", skipped); return
    def brier(pairs): return sum((p - y) ** 2 for p, y in pairs) / len(pairs)
    n = len(duel)
    be = brier([(d[1], d[3]) for d in duel])
    bm = brier([(d[2], d[3]) for d in duel])
    print(f"MATCHED-TIME DUEL  n={n}  skipped={skipped}")
    print(f"  raw-ensemble Brier: {be:.4f}")
    print(f"  market Brier:       {bm:.4f}")
    for u in ("F", "C"):
        sub = [d for d in duel if d[0] == u]
        if len(sub) >= 10:
            print(f"  unit {u} (n={len(sub)}): ens {brier([(d[1],d[3]) for d in sub]):.4f}"
                  f"  mkt {brier([(d[2],d[3]) for d in sub]):.4f}")
    # where do they disagree most, and who wins disagreements?
    dis = [d for d in duel if abs(d[1] - d[2]) > 0.15]
    if dis:
        w = sum(1 for d in dis if abs(d[1] - d[3]) < abs(d[2] - d[3]))
        print(f"  disagreements >15pts: n={len(dis)}, ensemble closer to truth in {w} ({w/len(dis):.0%})")
    ens_wins = sum(1 for d in duel if (d[1]-d[3])**2 < (d[2]-d[3])**2)
    print(f"  per-market head-to-head: ensemble wins {ens_wins}/{n} ({ens_wins/n:.0%})")

main()
