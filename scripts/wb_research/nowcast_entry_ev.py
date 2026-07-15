#!/usr/bin/env python3
"""S230 Phase-0 loser-leg replay (read-only, VPS): buy EVERY real-time
bucket-crossing — winners AND overshot losers — and measure net EV at mid.

For each resolved US highest-temp family day (IEM 1-min availability window):
walk the 1-min running max; every time it ENTERS a listed bucket
[lo-0.5, hi+0.5) that is an ENTRY: buy that bucket at the CLOB mid price at
  (a) t_cross  — the real-time crossing (what a PWS-mesh nowcaster does)
  (b) t_reveal — first public print revealing it + 6 min (the reactive crowd)
hold to resolution. EV per share = outcome − price (race_study units).
The (a)−(b) difference on identical events = the value of the information
lead, losers included, no survivorship.

Usage: python3 nowcast_entry_ev.py [days_back=8] [max_family_days=120]
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

PUB_DELAY_MIN = 6
LAG_GUARD_DAYS = 3
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 8
MAX_FAM = int(sys.argv[2]) if len(sys.argv) > 2 else 120

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
                          capture_output=True, text=True, timeout=120).stdout.strip()


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


def get_series(kind, sid3, d0, d1):
    if kind == "1min":
        u = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py?"
             f"station={sid3}&sts={d0:%Y-%m-%d}T00:00Z&ets={d1:%Y-%m-%d}T23:59Z"
             "&vars=tmpf&sample=1min&what=download&delim=comma")
        tcol, vcol = "valid(UTC)", "tmpf"
    else:
        u = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
             f"station={sid3}&data=tmpf&year1={d0.year}&month1={d0.month}&day1={d0.day}"
             f"&year2={d1.year}&month2={d1.month}&day2={d1.day}"
             "&tz=Etc/UTC&format=onlycomma&missing=M&trace=T"
             "&report_type=3&report_type=4")
        tcol, vcol = "valid", "tmpf"
    raw = fetch(u)
    if not raw:
        return []
    out = []
    for row in csv.DictReader(io.StringIO(raw)):
        v = (row.get(vcol) or "").strip()
        if v in ("", "M"):
            continue
        try:
            t = datetime.strptime(row[tcol], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            out.append((t, float(v)))
        except Exception:
            continue
    out.sort()
    return out


def px_at(token, ts, cache):
    key = (token, ts // 1800)
    if key not in cache:
        raw = fetch(f"https://clob.polymarket.com/prices-history?market={token}"
                    f"&startTs={ts-2700}&endTs={ts+2700}&fidelity=1")
        try:
            cache[key] = json.loads(raw).get("history", []) if raw else []
        except Exception:
            cache[key] = []
        time.sleep(0.06)
    h = cache[key]
    if not h:
        return None
    b = min(h, key=lambda x: abs(x["t"] - ts))
    return float(b["p"]) if abs(b["t"] - ts) <= 300 else None


def main():
    now = datetime.now(timezone.utc)
    d1 = (now - timedelta(days=LAG_GUARD_DAYS)).date()
    d0 = (now - timedelta(days=LAG_GUARD_DAYS + DAYS_BACK)).date()
    rows = json.loads(psql(f"""SELECT json_agg(row_to_json(t)) FROM (
      SELECT question, yes_token_id AS token, resolution FROM markets
      WHERE category='weather' AND resolution IN ('YES','NO')
        AND question LIKE 'Will the highest temperature%%'
        AND end_date_iso::date BETWEEN '{d0}' AND '{d1}'
        AND yes_token_id IS NOT NULL AND yes_token_id <> '') t""") or "[]")
    fams = defaultdict(list)
    for r in rows or []:
        p = parse_q(r["question"])
        if not p:
            continue
        city, mon, day, lo, hi = p
        st = CITY.get(city)
        if not st or st.temp_unit != "F" or not st.station_id.startswith("K"):
            continue
        fams[(city, mon, day)].append(
            dict(lo=lo, hi=hi, token=r["token"], won=r["resolution"] == "YES"))
    print(f"resolved US family-days {d0}..{d1}: {len(fams)}")

    ev_cross = []          # (ev, local_hour, price, won)
    ev_reveal = []
    scache, pcache = {}, {}
    fam_used = 0
    for (city, mon, day), buckets in sorted(fams.items()):
        if fam_used >= MAX_FAM:
            break
        st = CITY[city]
        tz = ZoneInfo(st.timezone)
        sid3 = st.station_id[1:]
        ld = datetime(2026, mon, day).date()
        u0, u1 = ld - timedelta(days=1), ld + timedelta(days=1)
        k1, k2 = (sid3, "1min", u0), (sid3, "pr", u0)
        if k1 not in scache:
            scache[k1] = get_series("1min", sid3, u0, u1)
            time.sleep(0.25)
        if k2 not in scache:
            scache[k2] = get_series("pr", sid3, u0, u1)
            time.sleep(0.25)
        one = [(t, v) for t, v in scache[k1] if t.astimezone(tz).date() == ld]
        prs = [(t, v) for t, v in scache[k2] if t.astimezone(tz).date() == ld]
        if len(one) < 300 or len(prs) < 12:
            continue
        got_any = False
        rm = None
        entered = set()
        for t, v in one:
            if rm is not None and v <= rm:
                continue
            rm = v
            for i, b in enumerate(buckets):
                if i in entered or not (b["lo"] - 0.5 <= rm < b["hi"] + 0.5):
                    continue
                entered.add(i)
                lh = t.astimezone(tz).hour
                if not (9 <= lh < 23):
                    continue
                y = 1.0 if b["won"] else 0.0
                pc = px_at(b["token"], int(t.timestamp()), pcache)
                if pc is not None and 0.001 < pc < 0.999:
                    ev_cross.append((y - pc, lh, pc, b["won"]))
                    got_any = True
                rt = next((tp for tp, vp in prs
                           if tp >= t and round(vp) >= max(b["lo"], round(rm))), None)
                if rt is not None:
                    pr_ = px_at(b["token"],
                                int((rt + timedelta(minutes=PUB_DELAY_MIN)).timestamp()),
                                pcache)
                    if pr_ is not None and 0.001 < pr_ < 0.999:
                        ev_reveal.append((y - pr_, lh, pr_, b["won"]))
        if got_any:
            fam_used += 1

    def report(tag, evs):
        if not evs:
            print(f"{tag}: no entries")
            return
        n = len(evs)
        wins = sum(1 for e in evs if e[3])
        print(f"\n{tag}: n={n} entries, winners {wins} ({wins/n:.0%}), "
              f"median entry price {median(e[2] for e in evs):.2f}")
        print(f"  meanEV {mean(e[0] for e in evs):+.3f}/share "
              f"(SE ~{0.45/max(n,1)**0.5:.3f})")
        byh = defaultdict(list)
        for e in evs:
            byh[e[1]].append(e)
        print("  hour | n | win% | med price | meanEV")
        for h in sorted(byh):
            g = byh[h]
            if len(g) < 4:
                continue
            print(f"   {h:02d}  | {len(g):3d} | {sum(1 for e in g if e[3])/len(g):4.0%} "
                  f"| {median(e[2] for e in g):9.2f} | {mean(e[0] for e in g):+.3f}")

    print(f"family-days with priced entries: {fam_used}")
    report("BUY AT t_cross (real-time crossing, mid)", ev_cross)
    report("BUY AT t_reveal (public print + 6min, mid) — reactive benchmark", ev_reveal)


main()
