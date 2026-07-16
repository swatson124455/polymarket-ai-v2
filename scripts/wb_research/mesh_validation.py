#!/usr/bin/env python3
"""S231/S232 — PWS-MESH VALIDATION (read-only, VPS). Phase-1 acceptance test.

Two modes (both read pws_mesh_*.jsonl written by the pws_mesh.py cron):

  --bias  (runnable IMMEDIATELY): per city, compare the mesh consensus temp
          (median of qc==1 PWS obs in a time bin) against the airport's own
          METAR prints (IEM asos.py serves today's METARs near-real-time;
          only the 1-min product lags). Reports per-city mesh-vs-METAR offset
          at print times + scatter — the systematic correction Phase 2 would
          have to learn. Print-time matching: nearest mesh bin within 15 min.

  --lead  (runnable once IEM 1-min catches up, ~42h): reconstruct the mesh
          running-max curve per city-day, detect rounded-degF increments of
          the running max (nowcast_skill.py convention), and measure (a) how
          early the MESH sees each increment vs the public print (+6 min pub
          delay), (b) false-crossing rate: mesh-only increments the print
          world never confirms (the never-print risk), using IEM 1-min as
          arbiter of what the real curve did. Verdict: does the mesh
          reproduce a usable share of the 58-min median 1-min lead?

Usage:
  python3 mesh_validation.py --bias [date_utc=YYYYMMDD]
  python3 mesh_validation.py --lead [date_utc=YYYYMMDD]
"""
import csv
import glob
import io
import json
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
BIN_S = 300           # mesh consensus bin
SID = {s.station_id: s for s in STATION_REGISTRY.values()}


def fetch(url, tries=3, timeout=60):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "wb-research/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode()
        except Exception:
            if i + 1 == tries:
                return None
            time.sleep(1.2 * (i + 1))


def load_mesh(date_tag):
    """-> {sid: [(epoch, temp_f)]} qc==1 only, per-PWS-debiased NOT applied."""
    out = defaultdict(list)
    for path in sorted(glob.glob(f"/home/ubuntu/wb_research/pws_mesh_{date_tag}.jsonl")):
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("qc") != 1 or r.get("temp_f") is None:
                    continue
                out[r["sid"]].append((int(r["epoch"]), float(r["temp_f"]), r["pws"]))
    for k in out:
        out[k].sort()
    return out


def consensus(series, t0, t1):
    """Median over the LATEST ob per PWS in [t0,t1]."""
    last = {}
    for ep, tf, pws in series:
        if t0 <= ep <= t1:
            last[pws] = tf
    return (median(last.values()), len(last)) if last else (None, 0)


def iem_series(sid3, d0, d1, kind):
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
    out = []
    if not raw:
        return out
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


def mode_bias(date_tag):
    mesh = load_mesh(date_tag)
    if not mesh:
        print(f"no mesh data for {date_tag}")
        return
    d = datetime.strptime(date_tag, "%Y%m%d").date()
    print(f"MESH-vs-METAR BIAS — {date_tag}, {len(mesh)} cities, bin {BIN_S}s")
    print("  sid  | prints matched | mean(mesh-metar) | median | sd | med PWS/bin")
    alldiff = []
    for sid, series in sorted(mesh.items()):
        obs = iem_series(sid[1:], d, d + timedelta(days=1), "hourly")
        time.sleep(0.2)
        diffs, ns = [], []
        for t, v in obs:
            ep = int(t.timestamp())
            c, n = consensus(series, ep - 900, ep + 60)
            if c is None:
                continue
            diffs.append(c - v)
            ns.append(n)
        if not diffs:
            continue
        alldiff += diffs
        print(f"  {sid} | {len(diffs):3d} | {mean(diffs):+6.2f}F | "
              f"{median(diffs):+6.2f}F | {pstdev(diffs) if len(diffs)>1 else 0:.2f} "
              f"| {median(ns):.0f}")
    if alldiff:
        print(f"  ALL  | {len(alldiff):3d} | {mean(alldiff):+6.2f}F | "
              f"{median(alldiff):+6.2f}F | {pstdev(alldiff):.2f}")
    print("NOTE: raw consensus, no per-PWS debiasing yet — Phase 2 must learn")
    print("per-station offsets; sd here bounds how noisy that correction is.")


def mode_lead(date_tag):
    mesh = load_mesh(date_tag)
    if not mesh:
        print(f"no mesh data for {date_tag}")
        return
    d = datetime.strptime(date_tag, "%Y%m%d").date()
    print(f"MESH LEAD vs PUBLIC PRINT — {date_tag} (IEM 1-min as arbiter)")
    print("  sid  | events | mesh-led | med lead min | false-crossings | 1min-events missed")
    tot_ev, tot_led, tot_false = 0, 0, 0
    for sid, series in sorted(mesh.items()):
        st = SID[sid]
        tz = ZoneInfo(st.timezone)
        one = [(t, v) for t, v in iem_series(sid[1:], d, d + timedelta(days=1), "1min")
               if t.astimezone(tz).date() == d and 9 <= t.astimezone(tz).hour < 23]
        prints = [(t, v) for t, v in iem_series(sid[1:], d, d + timedelta(days=1), "hourly")
                  if t.astimezone(tz).date() == d]
        time.sleep(0.3)
        if len(one) < 300 or not prints:
            continue
        # truth events: each rounded-F increment of the 1-min running max
        truth = {}
        rm = None
        for t, v in one:
            if rm is None or v > rm:
                rm = v
                truth.setdefault(round(rm), t)
        # print reveal time per level
        reveal = {}
        rmp = None
        for t, v in prints:
            if rmp is None or v > rmp:
                rmp = v
                for lvl in list(truth):
                    if round(rmp) >= lvl and lvl not in reveal:
                        reveal[lvl] = t + timedelta(minutes=PUB_DELAY_MIN)
        # mesh detection time per level (consensus running max)
        mesh_rm, mesh_seen = None, {}
        eps = sorted({ep for ep, _, _ in series})
        for ep in eps:
            c, n = consensus(series, ep - BIN_S, ep)
            if c is None or n < 2:
                continue
            if mesh_rm is None or c > mesh_rm:
                mesh_rm = c
                mesh_seen.setdefault(round(mesh_rm), ep)
        ev = led = 0
        leads = []
        false_cross = sum(1 for lvl in mesh_seen if lvl > max(truth) if truth)
        for lvl, t_true in truth.items():
            if lvl not in reveal:
                continue        # never printed — not gradeable vs print
            ev += 1
            t_mesh = mesh_seen.get(lvl)
            if t_mesh is not None and t_mesh < reveal[lvl].timestamp():
                led += 1
                leads.append((reveal[lvl].timestamp() - t_mesh) / 60)
        missed = sum(1 for lvl in truth if lvl not in mesh_seen)
        tot_ev += ev
        tot_led += led
        tot_false += false_cross
        print(f"  {sid} | {ev:3d} | {led:3d} | "
              f"{median(leads) if leads else float('nan'):6.1f} | {false_cross:2d} | {missed}")
    print(f"  TOTAL events {tot_ev} mesh-led {tot_led} "
          f"({(tot_led/tot_ev if tot_ev else 0):.0%}) false-crossings {tot_false}")
    print("Compare against nowcast_skill.py: 1-min curve median lead 58 min.")


if __name__ == "__main__":
    args = sys.argv[1:]
    tag = next((a for a in args if a.isdigit()), None) or \
        datetime.now(timezone.utc).strftime("%Y%m%d")
    if "--lead" in args:
        mode_lead(tag)
    else:
        mode_bias(tag)
