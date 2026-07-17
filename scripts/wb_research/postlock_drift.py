#!/usr/bin/env python3
"""S231-late STUDY B (pre-registered in WB_NOWCAST_CAPTURE_SPEC.md BEFORE this
ran; commit c005592) — POST-LOCK DRIFT CAPTURE (execution-only edge).

Winners drift 0.85->1.00 for hours after the outcome is physically locked.
If sellers still print below fair AFTER lock, that is latency/attention money
with zero forecast risk (only settlement/void risk remains).

FROZEN RULES, evaluated PROSPECTIVELY on EVERY bucket (false-lock rate is
part of the verdict, not a hindsight filter):
  L1: after the day's LAST hourly print (local >= 23:30) with the running max
      inside the bucket.
  L2 (earlier): local hour >= 21 AND running max inside bucket AND the latest
      print <= runmax - 2.0F.
Measure per fired lock: YES-frame taker-SELL prints (data-api full history;
outcome=No BUY at q == YES sell at 1-q via merged-book mint) at price
<= {0.97, 0.95, 0.90} in [t_lock, t_lock+12h]; shares; EV/share = 1 - price.
GATE: false-lock rate < 1% for the rule to be usable; viability = >=50% of
locked family-days show >=20 shares buyable at <=0.97. UPPER BOUNDS.

Usage: python3 postlock_drift.py [days_back=133] [max_locks=3000]
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
from statistics import mean, median
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/polymarket-ai-v2-weather")
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY  # noqa

LAG_GUARD_DAYS = 3
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 133
MAX_LOCKS = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
LEVELS = (0.97, 0.95, 0.90)
WINDOW_S = 12 * 3600

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


def get_hourly(sid3, d0, d1):
    u = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
         f"station={sid3}&data=tmpf&year1={d0.year}&month1={d0.month}&day1={d0.day}"
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
            yes_sell = (side == "SELL") if is_yes else (side == "BUY")
            out.append((ts, yes_px, yes_sell, sz))
        if len(page) < 500:
            break
        off += 500
        time.sleep(0.08)
    out.sort()
    time.sleep(0.08)
    return out


def in_bucket(rm, lo, hi):
    return lo - 0.5 <= rm < hi + 0.5


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
        if not st or st.temp_unit != "F" or not st.station_id.startswith("K"):
            continue
        ld = datetime(2026, mon, day).date()
        if not (d0 <= ld <= d1):
            continue
        fams[(city, mon, day)].append(
            dict(mid=r["id"], lo=lo, hi=hi, won=r["resolution"] == "YES"))

    hourly = {}
    for sid3 in sorted({CITY[c].station_id[1:] for (c, _, _) in fams}):
        hourly[sid3] = get_hourly(sid3, d0, d1)
        print(f"  hourly METAR {sid3}: {len(hourly[sid3])} obs", flush=True)
        time.sleep(0.3)
    print(f"family-days {d0}..{d1}: {len(fams)}", flush=True)

    # PASS 1 — evaluate lock rules prospectively on every bucket (no prints).
    locks = {"L1": [], "L2": []}   # dict(mid, won, t_lock, fam)
    for (city, mon, day), buckets in sorted(fams.items()):
        st = CITY[city]
        tz = ZoneInfo(st.timezone)
        ld = datetime(2026, mon, day).date()
        obs = [(t, v) for t, v in hourly[st.station_id[1:]]
               if t.astimezone(tz).date() == ld]
        if len(obs) < 8:
            continue
        fam = (city, mon, day)
        # L2: first qualifying moment per bucket
        rm = None
        fired2 = set()
        for t, v in obs:
            rm = v if rm is None else max(rm, v)
            lh = t.astimezone(tz)
            if lh.hour >= 21 and v <= rm - 2.0:
                for i, b in enumerate(buckets):
                    if i not in fired2 and in_bucket(rm, b["lo"], b["hi"]):
                        fired2.add(i)
                        locks["L2"].append(dict(mid=b["mid"], won=b["won"],
                                                t=int(t.timestamp()), fam=fam))
        # L1: last print of the day, local >= 23:30
        t_last, v_last = obs[-1]
        lt = t_last.astimezone(tz)
        if lt.hour == 23 and lt.minute >= 30:
            rm_all = max(v for _, v in obs)
            for b in buckets:
                if in_bucket(rm_all, b["lo"], b["hi"]):
                    locks["L1"].append(dict(mid=b["mid"], won=b["won"],
                                            t=int(t_last.timestamp()), fam=fam))

    for rule in ("L1", "L2"):
        fired = locks[rule]
        false_n = sum(1 for x in fired if not x["won"])
        print(f"\n{rule}: fired {len(fired)} | false-locks {false_n} "
              f"({(false_n/len(fired) if fired else 0):.2%}) "
              f"| GATE(<1%): {'PASS' if fired and false_n/len(fired) < 0.01 else 'FAIL'}",
              flush=True)

    # PASS 2 — prints for TRUE locks of whichever rules pass the gate.
    pcache = {}
    for rule in ("L1", "L2"):
        fired = locks[rule]
        if not fired or sum(1 for x in fired if not x["won"]) / len(fired) >= 0.01:
            print(f"\n{rule}: skipped print analysis (gate fail or empty)")
            continue
        true_locks = [x for x in fired if x["won"]][:MAX_LOCKS]
        per_day = defaultdict(lambda: defaultdict(float))
        evs = defaultdict(list)
        done = 0
        for x in true_locks:
            if x["mid"] not in pcache:
                pcache[x["mid"]] = get_prints(x["mid"])
            prints = pcache[x["mid"]]
            for lvl in LEVELS:
                sh = sum(sz for ts, px, sell, sz in prints
                         if sell and x["t"] <= ts <= x["t"] + WINDOW_S
                         and px <= lvl + 1e-9)
                per_day[(x["fam"], lvl)][x["mid"]] = sh
                if sh > 0:
                    ws = [(1 - px, sz) for ts, px, sell, sz in prints
                          if sell and x["t"] <= ts <= x["t"] + WINDOW_S
                          and px <= lvl + 1e-9]
                    evs[lvl].append(sum(e * s for e, s in ws) / sum(s for _, s in ws))
            done += 1
            if done % 100 == 0:
                print(f"  {rule}: ...{done}/{len(true_locks)} locks", flush=True)
        print(f"\n{rule} POST-LOCK FILLS (true locks n={len(true_locks)}) — UPPER BOUNDS:")
        print("  level | %locks any-fill | med shares(>0) | %locks >=20sh | mean EV/share(filled)")
        for lvl in LEVELS:
            tots = [sum(d.values()) for (fam, L), d in per_day.items() if L == lvl]
            nz = [x for x in tots if x > 0]
            ge20 = sum(1 for x in tots if x >= 20)
            n = len(tots)
            print(f"  <={lvl:.2f} | {len(nz)/n if n else 0:6.0%} | "
                  f"{median(nz) if nz else 0:8.1f} | {ge20/n if n else 0:6.0%} | "
                  f"{mean(evs[lvl]) if evs[lvl] else float('nan'):+.3f}")
        viable = sum(1 for x in ( [sum(d.values()) for (fam, L), d in per_day.items() if L == 0.97] ) if x >= 20)
        nn = sum(1 for (fam, L) in per_day if L == 0.97)
        print(f"  VIABILITY GATE (>=50% locked days with >=20sh at <=0.97): "
              f"{viable}/{nn} = {(viable/nn if nn else 0):.0%} -> "
              f"{'PASS' if nn and viable/nn >= 0.5 else 'FAIL'}")
    print("\nCaveats: prints ARE fills but queue/wash unknowable -> UPPER bounds;")
    print("residual risk on 'true lock' = settlement/void only (rule graded above).")


main()
