#!/usr/bin/env python3
"""S231-late STUDY C (pre-registered BEFORE running; commit 6d496fc) —
SIBLING-BUCKET REPRICING LATENCY ("sell the dead lane"). GLOBAL.

At the winner's reveal everyone buys the winner; does anyone still BUY the
bucket the running max just LEFT? If yes, a resting ask on the dead sibling
collects premium from stale buyers. Study B's boundary-risk finding (~9%)
means "dead" is graded vs actual resolution — the win-rate haircut is part
of the EV, never a filter.

FROZEN RULE: GLOBAL resolved families 03->07 (native-unit bucket matching;
IEM station = ICAO minus leading K for US, full ICAO otherwise; IEM tmpf is
degF -> convert to native for C stations). Reveal = first hourly print
entering the WINNER bucket (+6 min). Dead sibling = bucket the running max
occupied immediately before the crossing. q0 = last YES-frame print of the
sibling before t_reveal - 2min; require q0 >= 0.10. Resting ASK at
q in {q0, q0-0.01, q0-0.02}; fills = YES-frame taker-BUY prints >= q in
[t_reveal, +45min] (UPPER bounds); control window at t_reveal - 3.5h.
EV/share at fill level q0-0.02 = q - y (y = 1 if the sibling won).
GATE: >=30% of windows with >=20 shares filled at q0-0.02 post-reveal AND
meanEV >= +0.05 with 2x family-clustered SE excluding 0 -> exploitable;
fills without EV -> BIAS-CONFIRMED-NOT-TRADEABLE; else DEAD.

Usage: python3 sibling_repricing.py [days_back=136] [max_windows=2000]
"""
import csv
import io
import json
import re
import subprocess
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean, median, pstdev
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/polymarket-ai-v2-weather")
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY  # noqa

PUB_DELAY_MIN = 6
LAG_GUARD_DAYS = 3
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 136
MAX_WIN = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
DELTAS = (0.00, 0.01, 0.02)
WIN_POST_S = 45 * 60

MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"])}
CITY = {s.city_name.lower(): s for s in STATION_REGISTRY.values()}


def fetch(url, tries=3, timeout=40):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wb-research/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode()
        except Exception:
            if i + 1 == tries:
                return None
            time.sleep(1.2 * (i + 1))


def psql(q):
    return subprocess.run(["sudo", "-u", "postgres", "psql", "polymarket", "-At", "-c", q],
                          capture_output=True, text=True, timeout=180).stdout.strip()


def parse_q(q):
    m = re.match(r"Will the highest temperature in (.+?) be (.+?) on (\w+) (\d+)\?", q or "")
    if not m:
        return None
    city, spec, mon, day = m.groups()
    if mon not in MONTHS:
        return None
    s = spec.replace("°", "")
    mm = re.match(r"between (-?\d+)-(-?\d+)", s)
    if mm:
        lo, hi = float(mm.group(1)), float(mm.group(2))
    else:
        mm = re.match(r"(-?\d+)\D*or (?:above|higher)", s)
        if mm:
            lo, hi = float(mm.group(1)), 1e9
        else:
            mm = re.match(r"(-?\d+)\D*or (?:below|lower)", s)
            if mm:
                lo, hi = -1e9, float(mm.group(1))
            else:
                return None
    return city.lower(), MONTHS[mon], int(day), lo, hi


def iem_station(sid):
    return sid[1:] if sid.startswith("K") else sid


def get_hourly(sid, d0, d1):
    u = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
         f"station={iem_station(sid)}&data=tmpf"
         f"&year1={d0.year}&month1={d0.month}&day1={d0.day}"
         f"&year2={d1.year}&month2={d1.month}&day2={d1.day}"
         "&tz=Etc/UTC&format=onlycomma&missing=M&trace=T"
         "&report_type=3&report_type=4")
    raw = fetch(u, timeout=120)
    out = []
    if not raw:
        return out
    for row in csv.DictReader(io.StringIO(raw)):
        v = (row.get("tmpf") or "").strip()
        if v in ("", "M"):
            continue
        try:
            t = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            out.append((t, float(v)))
        except Exception:
            continue
    out.sort()
    return out


def get_prints(mid):
    seen, out, off = set(), [], 0
    while off <= 5000:
        raw = fetch(f"https://data-api.polymarket.com/trades?market={mid}"
                    f"&limit=500&offset={off}")
        try:
            page = json.loads(raw) if raw else []
        except Exception:
            page = []
        if not isinstance(page, list) or not page:
            break
        for t in page:
            key = (t.get("transactionHash"), t.get("asset"), t.get("timestamp"),
                   t.get("price"), t.get("size"))
            if key in seen:
                continue
            seen.add(key)
            try:
                px, sz, ts = float(t["price"]), float(t["size"]), int(t["timestamp"])
            except Exception:
                continue
            is_yes = (t.get("outcome") or "").lower() == "yes"
            side = (t.get("side") or "").upper()
            yes_px = px if is_yes else 1.0 - px
            # unified contract (S231 review deferred #5): 3rd tuple slot is
            # yes_sell EVERYWHERE (maker_fill_study/postlock_drift identical);
            # this script wants taker BUYs -> consumers test `not sell`.
            yes_sell = (side == "SELL") if is_yes else (side == "BUY")
            out.append((ts, yes_px, yes_sell, sz))
        if len(page) < 500:
            break
        off += 500
        time.sleep(0.08)
    out.sort()
    time.sleep(0.08)
    return out


def to_native(tf, unit):
    return tf if unit == "F" else (tf - 32.0) * 5.0 / 9.0


def in_bucket(x, lo, hi):
    return lo - 0.5 <= x < hi + 0.5


def main():
    now = datetime.now(timezone.utc)
    d1 = (now - timedelta(days=LAG_GUARD_DAYS)).date()
    d0 = (now - timedelta(days=LAG_GUARD_DAYS + DAYS_BACK)).date()

    rows = json.loads(psql("""SELECT json_agg(row_to_json(t)) FROM (
      SELECT id, question, resolution FROM markets
      WHERE category='weather' AND resolution IN ('YES','NO')
        AND question LIKE 'Will the highest temperature%%'
        AND (end_date_iso IS NULL OR end_date_iso >= '2026-01-01')) t""") or "[]")
    fams = defaultdict(list)
    for r in rows or []:
        p = parse_q(r["question"])
        if not p:
            continue
        city, mon, day, lo, hi = p
        st = CITY.get(city)
        if not st:
            continue
        ld = datetime(2026, mon, day).date()
        if not (d0 <= ld <= d1):
            continue
        fams[(city, mon, day)].append(
            dict(mid=r["id"], lo=lo, hi=hi, won=r["resolution"] == "YES"))

    hourly = {}
    for sid in sorted({CITY[c].station_id for (c, _, _) in fams}):
        hourly[sid] = get_hourly(sid, d0, d1)
        time.sleep(0.25)
    cov = {s: len(v) for s, v in hourly.items()}
    print(f"family-days {d0}..{d1}: {len(fams)} | stations {len(cov)} "
          f"| no-METAR stations: {[s for s, n in cov.items() if n < 100]}", flush=True)

    skip = defaultdict(int)
    results = []   # dict(fam, q0, ev@d2, fills{d}, ctrl{d}, region)
    done = 0
    for (city, mon, day), buckets in sorted(fams.items()):
        if done >= MAX_WIN:
            break
        st = CITY[city]
        tz = ZoneInfo(st.timezone)
        ld = datetime(2026, mon, day).date()
        obs = [(t, to_native(v, st.temp_unit)) for t, v in hourly[st.station_id]
               if t.astimezone(tz).date() == ld]
        if len(obs) < 8:
            skip["no_metar_day"] += 1
            continue
        winner = next((b for b in buckets if b["won"]), None)
        if not winner or winner["lo"] < -1e8:
            skip["no_winner_or_below_winner"] += 1
            continue
        rm, prev_bucket, t_ob = None, None, None
        for t, v in obs:
            if rm is None or v > rm:
                rm = v
                cur = next((b for b in buckets
                            if in_bucket(rm, b["lo"], b["hi"])), None)
                if cur is winner:
                    t_ob = t
                    break
                prev_bucket = cur or prev_bucket
        if t_ob is None or prev_bucket is None or prev_bucket is winner:
            skip["no_crossing_or_no_sibling"] += 1
            continue
        t_rev = int(t_ob.timestamp()) + PUB_DELAY_MIN * 60
        prints = get_prints(prev_bucket["mid"])
        if not prints:
            skip["no_prints"] += 1
            continue
        pre = [(ts, px) for ts, px, _, _ in prints if ts < t_rev - 120]
        if not pre or (t_rev - 120 - pre[-1][0]) > 12 * 3600:
            skip["no_fresh_pre_price"] += 1
            continue
        q0 = pre[-1][1]
        if q0 < 0.10:
            skip["q0_below_0.10"] += 1
            continue
        fills, ctrl = {}, {}
        c1 = t_rev - int(3.5 * 3600)
        for dta in DELTAS:
            lvl = q0 - dta
            fills[dta] = sum(sz for ts, px, sell, sz in prints
                             if not sell and t_rev <= ts <= t_rev + WIN_POST_S
                             and px >= lvl - 1e-9)
            ctrl[dta] = sum(sz for ts, px, sell, sz in prints
                            if not sell and c1 <= ts <= c1 + WIN_POST_S
                            and px >= lvl - 1e-9)
        y = 1.0 if prev_bucket["won"] else 0.0
        results.append(dict(fam=(city, mon, day), q0=q0,
                            ev=(q0 - 0.02) - y, fills=fills, ctrl=ctrl,
                            us=st.station_id.startswith("K")))
        done += 1
        if done % 100 == 0:
            print(f"  ...{done} windows", flush=True)

    print(f"\nreveal windows analyzed: {len(results)} | skips: {dict(skip)}")
    if not results:
        return
    n = len(results)
    print(f"median q0 (dead-sibling pre-reveal price): {median(r['q0'] for r in results):.2f}"
          f" | sibling actually WON (boundary risk): "
          f"{sum(1 for r in results if r['ev'] < 0)}/{n}")
    print("\nRESTING-ASK FILLS ON THE DEAD SIBLING [t_reveal, +45m] — UPPER BOUNDS")
    print("  level    | any-fill | med shares(>0) | >=20sh | control any")
    for dta in DELTAS:
        f = [r["fills"][dta] for r in results]
        c = [r["ctrl"][dta] for r in results]
        nz = [x for x in f if x > 0]
        print(f"  q0-{dta:.2f}  |  {len(nz)/n:5.0%}  |    {median(nz) if nz else 0:8.1f}    "
              f"| {sum(1 for x in f if x >= 20)/n:5.0%}  |  {sum(1 for x in c if x > 0)/n:5.0%}")
    filled = [r for r in results if r["fills"][0.02] >= 20]
    by = defaultdict(list)
    for r in results:
        by[r["fam"]].append(r["ev"])
    fm = [mean(v) for v in by.values()]
    cse = (pstdev(fm) / len(fm) ** 0.5) if len(fm) > 1 else 0.0
    ev = mean(r["ev"] for r in results)
    ge20 = len(filled) / n
    print(f"\nEV/share selling at q0-0.02 (graded vs resolution): mean {ev:+.3f} "
          f"cSE {cse:.3f} (~{(ev/cse if cse else float('nan')):.1f} sigma, "
          f"{len(fm)} family-days)")
    us = [r["ev"] for r in results if r["us"]]
    nus = [r["ev"] for r in results if not r["us"]]
    print(f"  cuts: US n={len(us)} mean {mean(us) if us else float('nan'):+.3f} | "
          f"non-US n={len(nus)} mean {mean(nus) if nus else float('nan'):+.3f}")
    gate = ("EXPLOITABLE" if ge20 >= 0.30 and ev >= 0.05 and cse > 0 and ev - 2 * cse > 0
            else "BIAS-CONFIRMED-NOT-TRADEABLE" if cse > 0 and ev - 2 * cse > 0
            else "DEAD")
    print(f"GATE: >=20sh@q0-0.02 in {ge20:.0%} of windows (bar 30%) AND "
          f"meanEV {ev:+.3f} (bar +0.05 with 2sigma) -> {gate}")
    print("\nCaveats: UPPER bounds (queue/wash); winner-reveal-conditioned;")
    print("EV includes the ~9% boundary-risk haircut by construction (graded).")


main()
