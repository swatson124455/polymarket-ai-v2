#!/usr/bin/env python3
"""S230 representativeness-bias test (read-only, VPS) — root-cause candidate
for the cheap-NO-tail miscalibration (bot 4% -> reality 27%, n=67 @ S222 n=133).

HYPOTHESIS: market resolution grades the CONTINUOUS daily max; the bot's chain
(hourly-resolution model output; METAR/WU-anchored ground truth) predicts the
HOURLY-sampled world, which runs ~1F lower on most days -> every P(max >= X)
is biased LOW.

Per US station-day (IEM window):
  C   = rounded continuous 1-min max        (what resolution should track)
  H   = rounded max of hourly prints (rt3+4) (the hourly world)
  WU  = weather_calibration.actual_temp      (the bot's stored ground truth)
  Fm  = median ensemble members, morning-of forecast (lead 6-18h)
Then, on resolved families: does the WINNING bucket contain C or H?

Verdict logic:
  C - H > 0 systematically            (known: ~78% of days >= 1F)
  winner bucket tracks C, not H       => resolution IS the continuous max
  Fm centered on H (Fm-H ~ 0, Fm-C < 0) => forecast layer lives in the hourly
                                          world -> BIAS CONFIRMED at that layer
  WU - C ~ 0 vs WU - H ~ 0            => which world the TRAINING truth lives in

Usage: python3 rep_bias_test.py [days_back=18]
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
from statistics import mean, median, stdev
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/polymarket-ai-v2-weather")
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY  # noqa

LAG_GUARD_DAYS = 3
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 18
MONTHS = {m: i + 1 for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"])}
CITY = {s.city_name.lower(): s for s in STATION_REGISTRY.values()}


def fetch(url, tries=3, timeout=90):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wb-research/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode()
        except Exception:
            if i + 1 == tries:
                return None
            time.sleep(1.5 * (i + 1))


def psql(q):
    return subprocess.run(["sudo", "-u", "postgres", "psql", "polymarket", "-At", "-c", q],
                          capture_output=True, text=True, timeout=180).stdout.strip()


def series_range(kind, sid3, d0, d1):
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
        return city.lower(), MONTHS[mon], int(day), float(mm.group(1)), float(mm.group(2))
    return None


def main():
    now = datetime.now(timezone.utc)
    d1 = (now - timedelta(days=LAG_GUARD_DAYS)).date()
    d0 = (now - timedelta(days=LAG_GUARD_DAYS + DAYS_BACK)).date()
    stations = [s for s in STATION_REGISTRY.values()
                if s.temp_unit == "F" and s.station_id.startswith("K")][:12]
    print(f"window {d0}..{d1}, stations {[s.station_id for s in stations]}")

    wu = defaultdict(dict)      # sid -> date -> actual_temp
    for line in psql(
        "SELECT DISTINCT ON (station_id, target_date::date) "
        "station_id, target_date::date, actual_temp, COALESCE(actual_source,'?') "
        f"FROM weather_calibration WHERE target_date::date BETWEEN '{d0}' AND '{d1}' "
        "AND actual_temp IS NOT NULL ORDER BY station_id, target_date::date, id DESC"
    ).splitlines():
        sid, dt, at, src = line.split("|")
        wu[sid][dt] = (float(at), src)

    fm = defaultdict(dict)      # sid -> date -> median members (morning-of lead)
    for line in psql(
        "SELECT station_id, target_date::date, ensemble_members FROM weather_forecasts "
        f"WHERE target_date::date BETWEEN '{d0}' AND '{d1}' "
        "AND lead_time_hours BETWEEN 6 AND 18 AND ensemble_members IS NOT NULL"
    ).splitlines():
        try:
            sid, dt, mem = line.split("|", 2)
            mem = json.loads(mem)
            if mem and len(mem) >= 10:
                fm[sid].setdefault(dt, []).append(median(mem))
        except Exception:
            continue

    diffs = defaultdict(list)   # label -> [diff]
    day_index = {}              # (sid, date) -> (C, H)
    for st in stations:
        sid3 = st.station_id[1:]
        tz = ZoneInfo(st.timezone)
        one = series_range("1min", sid3, d0, d1)
        time.sleep(0.5)
        prs = series_range("pr", sid3, d0, d1)
        time.sleep(0.5)
        b1, bp = defaultdict(list), defaultdict(list)
        for t, v in one:
            b1[t.astimezone(tz).date()].append(v)
        for t, v in prs:
            bp[t.astimezone(tz).date()].append(v)
        for day in sorted(b1):
            if len(b1[day]) < 600 or len(bp.get(day, [])) < 12:
                continue
            C = round(max(b1[day]))
            H = round(max(bp[day]))
            day_index[(st.station_id, day.isoformat())] = (C, H)
            diffs["C-H"].append(C - H)
            w = wu.get(st.station_id, {}).get(day.isoformat())
            if w:
                diffs["WU-C"].append(round(w[0]) - C)
                diffs["WU-H"].append(round(w[0]) - H)
            fml = fm.get(st.station_id, {}).get(day.isoformat())
            if fml:
                f = mean(fml)
                diffs["Fm-C"].append(f - C)
                diffs["Fm-H"].append(f - H)

    print(f"\nstation-days matched: {len(diffs['C-H'])}")
    print("layer diff | n | mean | median | SE")
    for k in ("C-H", "WU-C", "WU-H", "Fm-C", "Fm-H"):
        v = diffs.get(k, [])
        if len(v) < 5:
            print(f"  {k:<6} | {len(v):3d} | insufficient")
            continue
        se = (stdev(v) / len(v) ** 0.5) if len(v) > 1 else 0
        print(f"  {k:<6} | {len(v):3d} | {mean(v):+.2f} | {median(v):+.1f} | {se:.2f}")

    # which world does RESOLUTION track?
    rows = json.loads(psql(f"""SELECT json_agg(row_to_json(t)) FROM (
      SELECT question FROM markets
      WHERE category='weather' AND resolution='YES'
        AND question LIKE 'Will the highest temperature%%'
        AND end_date_iso::date BETWEEN '{d0}' AND '{d1}') t""") or "[]")
    agree_c = agree_h = tot = 0
    for r in rows or []:
        p = parse_q(r["question"])
        if not p:
            continue
        city, mon, day, lo, hi = p
        st = CITY.get(city)
        if not st or st.temp_unit != "F" or not st.station_id.startswith("K"):
            continue
        key = (st.station_id, f"2026-{mon:02d}-{day:02d}")
        if key not in day_index:
            continue
        C, H = day_index[key]
        tot += 1
        if lo <= C <= hi:
            agree_c += 1
        if lo <= H <= hi:
            agree_h += 1
    if tot:
        print(f"\nRESOLVED WINNER BUCKET vs observed maxes (n={tot} winner buckets):")
        print(f"  winner bucket contains CONTINUOUS max C: {agree_c}/{tot} ({agree_c/tot:.0%})")
        print(f"  winner bucket contains HOURLY-PRINT max H: {agree_h}/{tot} ({agree_h/tot:.0%})")
    print("\nverdict guide: resolution tracks the column with higher agreement;"
          "\nFm/WU centered on H (not C) = the chain lives in the hourly world -> bias.")


main()
