#!/usr/bin/env python3
"""Scaled resolution-day race study (read-only, public data).

For every resolved US 'highest temperature' family in the last week:
  - IEM METAR obs -> running max by local hour
  - CLOB price history for every bucket token
  - Strategy backtest: at local hour H, buy the bucket containing the current
    running max (the 'leader'); hold to resolution. Includes losers (overshoot)
    -> honest EV per hour, no survivorship.
  - Winner reaction curve: price at first entry of winning bucket, +15/30/60m.
"""
import json, csv, io, re, time, urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import sys
INPUT = sys.argv[1] if len(sys.argv) > 1 else "race_input.json"
MONTHS = {m: i + 1 for i, m in enumerate(
    ["January","February","March","April","May","June","July","August",
     "September","October","November","December"])}

def fetch(url, tries=2):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wb-research/1.0"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.read()
        except Exception:
            if i + 1 == tries: return None
            time.sleep(0.6)

def parse_q(q):
    m = re.match(r"Will the highest temperature in (.+?) be (.+?) on (\w+) (\d+)\?", q)
    if not m: return None
    city, spec, mon, day = m.groups()
    if mon not in MONTHS: return None
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
                else: return None
    return city.lower(), MONTHS[mon], int(day), lo, hi

lines = io.open(INPUT, encoding="utf-8").read().strip().splitlines()
citymap = json.loads(lines[0])["citymap"]
markets = json.loads(lines[1])

fams = defaultdict(list)
for r in markets:
    p = parse_q(r["question"])
    if not p: continue
    city, mon, day, lo, hi = p
    st = citymap.get(city)
    if not st or st["unit"] != "F" or not st["sid"].startswith("K"): continue
    year = int(r["end_date"][:4]) if r["end_date"] else 2026
    fams[(city, year, mon, day)].append(
        {"lo": lo, "hi": hi, "token": r["token"], "won": r["resolution"] == "YES"})

print(f"US families: {len(fams)}")
HOURS = [12, 13, 14, 15, 16, 17]
buy_leader = {h: [] for h in HOURS}   # returns per $1
reaction = []                          # (p_at_entry, p15, p30, p60)
fam_done = 0
for (city, year, mon, day), buckets in sorted(fams.items()):
    if not any(b["won"] for b in buckets): continue  # winner outside listed set
    st = citymap[city]; tz = ZoneInfo(st["tz"]); sid4 = st["sid"]; sid = sid4[1:]
    # --- METAR obs (local day) ---
    u = (f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station={sid}"
         f"&data=tmpf&year1={year}&month1={mon}&day1={day}&year2={year}&month2={mon}&day2={day+1 if day<28 else day}"
         f"&tz={st['tz']}&format=onlycomma&missing=M&trace=T&report_type=3")
    raw = fetch(u)
    if not raw: continue
    obs = []
    for row in csv.DictReader(io.StringIO(raw.decode())):
        if row.get("tmpf") in (None, "M", ""): continue
        d = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
        if d.day == day:
            obs.append((d, float(row["tmpf"])))
    if len(obs) < 10: continue
    obs.sort()
    # --- price history per bucket ---
    day0 = datetime(year, mon, day, 0, 0, tzinfo=tz)
    t_start = int((day0 - timedelta(hours=12)).timestamp())
    t_end = int((day0 + timedelta(hours=40)).timestamp())
    hist = {}
    ok = True
    for b in buckets:
        raw2 = fetch(f"https://clob.polymarket.com/prices-history?market={b['token']}"
                     f"&startTs={t_start}&endTs={t_end}&fidelity=10")
        time.sleep(0.08)
        if not raw2: ok = False; break
        h = json.loads(raw2).get("history", [])
        if not h: ok = False; break
        hist[b["token"]] = h
    if not ok: continue
    def px(token, ts):
        c = [p for p in hist[token] if p["t"] <= ts + 300]
        return c[-1]["p"] if c else None
    def runmax_at(dt):
        v = [t for d, t in obs if d <= dt]
        return max(v) if v else None
    def leader(dt):
        rm = runmax_at(dt)
        if rm is None: return None
        for b in buckets:
            if b["lo"] - 0.5 <= rm < b["hi"] + 0.5: return b
        return None
    for H in HOURS:
        dt = day0.replace(hour=H)
        b = leader(dt)
        if b is None: continue
        p = px(b["token"], int(dt.timestamp()))
        if p is None or not (0.02 < p < 0.98): continue
        buy_leader[H].append((1.0 if b["won"] else 0.0) - p)
    # winner reaction: first obs time the winning bucket became leader
    wb = next(b for b in buckets if b["won"])
    t_enter = None
    for d, t in obs:
        rm = runmax_at(d)
        if rm is not None and wb["lo"] - 0.5 <= rm < wb["hi"] + 0.5:
            t_enter = d; break
    if t_enter is not None:
        ts = int(t_enter.timestamp())
        vals = [px(wb["token"], ts + m * 60) for m in (0, 15, 30, 60)]
        if all(v is not None for v in vals):
            reaction.append(vals)
    fam_done += 1

print(f"families replayed: {fam_done}")
print("\nBUY-THE-LEADER at local hour H, hold to resolution (per $1, mid prices, no fees):")
for H in HOURS:
    r = buy_leader[H]
    if len(r) >= 5:
        mean = sum(r) / len(r)
        wins = sum(1 for x in r if x > 0)
        print(f"  H={H:02d}:00  n={len(r):3d}  meanEV={mean:+.3f}  win%={wins/len(r):.0%}")
if reaction:
    import statistics as st_
    cols = list(zip(*reaction))
    print(f"\nWINNER REACTION (n={len(reaction)}): avg price at first-entry of winning bucket,"
          f" then +15/+30/+60min:")
    print("  " + "  ".join(f"{st_.mean(c):.3f}" for c in cols))
