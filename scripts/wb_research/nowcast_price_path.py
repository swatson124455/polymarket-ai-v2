#!/usr/bin/env python3
"""S230 Phase-0b' (read-only, VPS): does the MARKET move at the 1-minute
crossing time (someone already trades real-time obs) or at the METAR print
(the hole is real)?

For each resolved WINNING bucket of a US highest-temp family (recent days,
inside the IEM 1-min availability window): find
  t_cross  = first time the 1-min running max enters the winning bucket
  t_reveal = first public print (METAR/SPECI instantaneous) whose rounded
             temp reaches the bucket + PUB_DELAY_MIN
then fetch the CLOB minute price path and average it aligned on BOTH times.
Flat-at-cross + jump-at-reveal => nobody front-runs the print (hole open).
Rise-at-cross => real-time players already exist (hole shrinking/closed).

Usage: python3 nowcast_price_path.py [days_back=10] [max_events=60]
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
from statistics import mean
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/polymarket-ai-v2-weather")
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY  # noqa

PUB_DELAY_MIN = 6
LAG_GUARD_DAYS = 3
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 10
MAX_EVENTS = int(sys.argv[2]) if len(sys.argv) > 2 else 60
OFFS = [-60, -30, -15, -5, 0, 5, 15, 30, 45, 60, 90]

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
    if not mm:
        return None          # winner tails are rare; ranges only keeps it clean
    return city.lower(), MONTHS[mon], int(day), float(mm.group(1)), float(mm.group(2))


def get_series(kind, sid3, day_utc0, day_utc1):
    if kind == "1min":
        u = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py?"
             f"station={sid3}&sts={day_utc0:%Y-%m-%d}T00:00Z&ets={day_utc1:%Y-%m-%d}T23:59Z"
             "&vars=tmpf&sample=1min&what=download&delim=comma")
        tcol, vcol = "valid(UTC)", "tmpf"
    else:
        u = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
             f"station={sid3}&data=tmpf&year1={day_utc0.year}&month1={day_utc0.month}"
             f"&day1={day_utc0.day}&year2={day_utc1.year}&month2={day_utc1.month}"
             f"&day2={day_utc1.day}&tz=Etc/UTC&format=onlycomma&missing=M&trace=T"
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


def clob_series(token, ts0, ts1):
    u = (f"https://clob.polymarket.com/prices-history?market={token}"
         f"&startTs={ts0}&endTs={ts1}&fidelity=1")
    raw = fetch(u)
    if not raw:
        return []
    try:
        return json.loads(raw).get("history", [])
    except Exception:
        return []


def px_at(hist, ts, tol=240):
    if not hist:
        return None
    b = min(hist, key=lambda x: abs(x["t"] - ts))
    return float(b["p"]) if abs(b["t"] - ts) <= tol else None


def main():
    now = datetime.now(timezone.utc)
    d1 = (now - timedelta(days=LAG_GUARD_DAYS)).date()
    d0 = (now - timedelta(days=LAG_GUARD_DAYS + DAYS_BACK)).date()
    rows = json.loads(psql(f"""SELECT json_agg(row_to_json(t)) FROM (
      SELECT question, yes_token_id AS token FROM markets
      WHERE category='weather' AND resolution='YES'
        AND question LIKE 'Will the highest temperature%%'
        AND end_date_iso::date BETWEEN '{d0}' AND '{d1}'
        AND yes_token_id IS NOT NULL AND yes_token_id <> '') t""") or "[]")
    print(f"winner buckets {d0}..{d1}: {len(rows) if rows else 0}")
    cross_path = defaultdict(list)
    reveal_path = defaultdict(list)
    used = 0
    cache = {}
    for r in rows or []:
        if used >= MAX_EVENTS:
            break
        p = parse_q(r["question"])
        if not p:
            continue
        city, mon, day, lo, hi = p
        st = CITY.get(city)
        if not st or st.temp_unit != "F" or not st.station_id.startswith("K"):
            continue
        tz = ZoneInfo(st.timezone)
        sid3 = st.station_id[1:]
        ld = datetime(2026, mon, day).date()
        u0, u1 = ld - timedelta(days=1), ld + timedelta(days=1)
        k1 = (sid3, "1min", u0)
        k2 = (sid3, "pr", u0)
        if k1 not in cache:
            cache[k1] = get_series("1min", sid3, u0, u1)
            time.sleep(0.3)
        if k2 not in cache:
            cache[k2] = get_series("pr", sid3, u0, u1)
            time.sleep(0.3)
        one = [(t, v) for t, v in cache[k1] if t.astimezone(tz).date() == ld]
        prs = [(t, v) for t, v in cache[k2] if t.astimezone(tz).date() == ld]
        if len(one) < 300 or len(prs) < 12:
            continue
        # first time the 1-min RUNNING MAX enters [lo-0.5, hi+0.5)
        t_cross = None
        rm = None
        for t, v in one:
            rm = v if rm is None or v > rm else rm
            if lo - 0.5 <= rm < hi + 0.5:
                t_cross = t
                break
        if t_cross is None:
            continue
        t_reveal = next((t for t, v in prs if t >= t_cross and round(v) >= lo), None)
        if t_reveal is None:
            continue
        t_reveal += timedelta(minutes=PUB_DELAY_MIN)
        gap_min = (t_reveal - t_cross).total_seconds() / 60
        if gap_min < 8:
            continue   # cross and reveal too close to separate the hypotheses
        ts_c, ts_r = int(t_cross.timestamp()), int(t_reveal.timestamp())
        hist = clob_series(r["token"], ts_c - 4200, ts_r + 6600)
        time.sleep(0.1)
        if not hist:
            continue
        got = False
        for o in OFFS:
            pc = px_at(hist, ts_c + o * 60)
            pr_ = px_at(hist, ts_r + o * 60)
            if pc is not None:
                cross_path[o].append(pc)
                got = True
            if pr_ is not None:
                reveal_path[o].append(pr_)
        if got:
            used += 1
    print(f"events used: {used} (cross->reveal gap >=8min, both times known)")
    print("\navg WINNER price path aligned on t_cross (1-min curve enters bucket):")
    print("  off_min: " + "  ".join(f"{o:+4d}" for o in OFFS))
    print("  price  : " + "  ".join(
        f"{mean(cross_path[o]):.2f}" if cross_path.get(o) else "  - " for o in OFFS))
    print("  n      : " + "  ".join(f"{len(cross_path.get(o, [])):4d}" for o in OFFS))
    print("\navg WINNER price path aligned on t_reveal (public print + delay):")
    print("  off_min: " + "  ".join(f"{o:+4d}" for o in OFFS))
    print("  price  : " + "  ".join(
        f"{mean(reveal_path[o]):.2f}" if reveal_path.get(o) else "  - " for o in OFFS))
    print("  n      : " + "  ".join(f"{len(reveal_path.get(o, [])):4d}" for o in OFFS))
    print("\nread: rise BEFORE 0 on the cross-aligned path = someone trades "
          "real-time obs; flat-then-jump on the reveal-aligned path = hole open.")


main()
