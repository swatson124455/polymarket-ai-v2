#!/usr/bin/env python3
"""S230 Phase-0a nowcast backtest (read-only, IEM archives; runs on VPS).

Question: how much LEAD does the official station's own 1-minute curve give
over the public hourly METAR prints? (The 1-min archive lags ~42h so it can't
be traded live — but it is exactly the curve a real-time PWS mesh would see,
minus siting offsets. If the station's OWN curve gives no lead, PWS can't
either; if it does, PWS is the live substitute.)

Per station-day (local 09:00-23:00):
  - EVENT: the rounded (deg-F) running max from the 1-min series increments.
  - REVEAL: first public print (routine METAR or SPECI, instantaneous temp)
    at/after the event whose rounded temp >= the new value, plus PUB_DELAY_MIN.
  - LEAD = reveal_time - event_time. Events with no same-day reveal are
    HIDDEN (public learns only from the 6h max group / end-of-day CLI).
Also: HIDDEN-PEAK days = rounded 1-min daily max > rounded max of ALL
instantaneous prints that day (the deciding value never prints intraday).

Usage: python3 nowcast_skill.py [days_back=21] [max_stations=12]
"""
import csv
import io
import re
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/polymarket-ai-v2-weather")
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY  # noqa

PUB_DELAY_MIN = 6          # ob valid-time -> visible on AWC api (assumption)
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 21
MAX_STATIONS = int(sys.argv[2]) if len(sys.argv) > 2 else 12
LAG_GUARD_DAYS = 3         # IEM 1-min ingest lags ~42h; stay clear of it


def fetch(url, tries=3, timeout=60):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wb-research/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode()
        except Exception:
            if i + 1 == tries:
                return None
            time.sleep(1.5 * (i + 1))


def get_1min(sid3, d0, d1):
    """1-min tmpf series, UTC timestamps."""
    u = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py?"
         f"station={sid3}&sts={d0:%Y-%m-%d}T00:00Z&ets={d1:%Y-%m-%d}T23:59Z"
         "&vars=tmpf&sample=1min&what=download&delim=comma")
    raw = fetch(u)
    if not raw:
        return []
    out = []
    for row in csv.DictReader(io.StringIO(raw)):
        v = (row.get("tmpf") or "").strip()
        if v in ("", "M"):
            continue
        try:
            t = datetime.strptime(row["valid(UTC)"], "%Y-%m-%d %H:%M").replace(
                tzinfo=timezone.utc)
            out.append((t, float(v)))
        except Exception:
            continue
    out.sort()
    return out


def get_prints(sid3, d0, d1):
    """Public instantaneous prints: routine METAR + SPECI (report_type 3,4)."""
    u = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
         f"station={sid3}&data=tmpf&year1={d0.year}&month1={d0.month}&day1={d0.day}"
         f"&year2={d1.year}&month2={d1.month}&day2={d1.day}"
         "&tz=Etc/UTC&format=onlycomma&missing=M&trace=T"
         "&report_type=3&report_type=4")
    raw = fetch(u)
    if not raw:
        return []
    out = []
    for row in csv.DictReader(io.StringIO(raw)):
        v = (row.get("tmpf") or "").strip()
        if v in ("", "M"):
            continue
        try:
            t = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(
                tzinfo=timezone.utc)
            out.append((t, float(v)))
        except Exception:
            continue
    out.sort()
    return out


def main():
    now = datetime.now(timezone.utc)
    d1 = (now - timedelta(days=LAG_GUARD_DAYS)).date()
    d0 = (now - timedelta(days=LAG_GUARD_DAYS + DAYS_BACK)).date()
    stations = [s for s in STATION_REGISTRY.values()
                if s.temp_unit == "F" and s.station_id.startswith("K")][:MAX_STATIONS]
    print(f"window {d0} .. {d1} ({DAYS_BACK}d), stations: "
          f"{[s.station_id for s in stations]}")
    print(f"assumption: print visible {PUB_DELAY_MIN} min after ob valid time")

    leads = []                      # minutes, revealed events
    hidden_events = 0
    total_events = 0
    hidden_peak_days = 0
    days_counted = 0
    per_city = defaultdict(lambda: [0, 0, []])   # city -> [events, hidden, leads]

    for st in stations:
        sid3 = st.station_id[1:]
        tz = ZoneInfo(st.timezone)
        one = get_1min(sid3, d0, d1)
        time.sleep(0.5)
        prints = get_prints(sid3, d0, d1)
        time.sleep(0.5)
        if not one or not prints:
            print(f"  {st.station_id}: NO DATA (1min={len(one)} prints={len(prints)})")
            continue
        by_day_1m = defaultdict(list)
        for t, v in one:
            by_day_1m[t.astimezone(tz).date()].append((t, v))
        by_day_pr = defaultdict(list)
        for t, v in prints:
            by_day_pr[t.astimezone(tz).date()].append((t, v))
        for day, series in sorted(by_day_1m.items()):
            prs = by_day_pr.get(day, [])
            if len(series) < 300 or len(prs) < 12:
                continue  # incomplete day
            days_counted += 1
            # walk the local day, window 09-23 local
            runmax = None
            events = []          # (t_event, rounded_value)
            for t, v in series:
                lt = t.astimezone(tz)
                if runmax is None or v > runmax:
                    if runmax is not None and round(v) > round(runmax) \
                            and 9 <= lt.hour < 23:
                        events.append((t, round(v)))
                    runmax = v if (runmax is None or v > runmax) else runmax
            true_max_r = round(max(v for _, v in series))
            pub_max_r = round(max(v for _, v in prs))
            if true_max_r > pub_max_r:
                hidden_peak_days += 1
            for t_ev, val in events:
                total_events += 1
                per_city[st.city_name][0] += 1
                reveal = next((tp for tp, vp in prs
                               if tp >= t_ev and round(vp) >= val), None)
                if reveal is None:
                    hidden_events += 1
                    per_city[st.city_name][1] += 1
                else:
                    lead = (reveal - t_ev).total_seconds() / 60 + PUB_DELAY_MIN
                    leads.append(lead)
                    per_city[st.city_name][2].append(lead)

    print(f"\nstation-days analyzed: {days_counted}")
    print(f"bucket-boundary events (rounded-max increments, 09-23 local): {total_events}")
    if leads:
        s = sorted(leads)
        print(f"  revealed by a same-day print: {len(leads)} "
              f"({100*len(leads)/max(total_events,1):.0f}%)")
        print(f"  LEAD over public print: median {median(s):.0f} min | "
              f"mean {sum(s)/len(s):.0f} | p25 {s[len(s)//4]:.0f} | "
              f"p75 {s[3*len(s)//4]:.0f}")
        for thr in (10, 20, 30, 45):
            print(f"    lead >= {thr} min: {100*sum(1 for x in s if x >= thr)/len(s):.0f}%")
    print(f"  HIDDEN events (never printed intraday): {hidden_events} "
          f"({100*hidden_events/max(total_events,1):.0f}%)")
    print(f"  HIDDEN-PEAK days (true daily max never in any intraday print): "
          f"{hidden_peak_days}/{days_counted} "
          f"({100*hidden_peak_days/max(days_counted,1):.0f}%)")
    print("\nper city: events / hidden / median lead")
    for c, (ev, hid, ls) in sorted(per_city.items(), key=lambda kv: -kv[1][0]):
        ml = f"{median(ls):.0f}m" if ls else "-"
        print(f"  {c:<16} {ev:4d} / {hid:3d} / {ml}")


main()
