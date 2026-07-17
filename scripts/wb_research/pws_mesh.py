#!/usr/bin/env python3
"""S231 Phase-1 PWS-MESH COLLECTOR (READ-ONLY research cron) — operator-approved
build 2026-07-15 after the peak-model gate PASSED (nowcast_peak_133d.out).

Purpose: the live substitute for the IEM 1-min curve (which lags ~42h). Every
tick, for EVERY active-market resolution city (GLOBAL — operator hard
directive 2026-07-16; any country/unit) whose LOCAL time is 09:00-21:59,
poll current observations from up to STATIONS_PER_CITY nearby Weather
Underground personal weather stations and append NEW obs (per-PWS epoch
cursor) to ~/wb_research/pws_mesh_YYYYMMDD.jsonl. Cron: every 5 minutes.
temp_f is always °F (units=e); convert to station-native units at analysis.

Next-session validation: reconstruct the running-max curve from this mesh and
compare against IEM 1-min once it catches up (~42h) — does the mesh reproduce
the 58-min median print lead (nowcast_skill.py)?

DEPENDENCY CAVEAT (recorded in WEATHER_STATUS): api.weather.com is queried
with the public web key the wunderground.com site itself embeds — unofficial.
Durable path = operator obtains a personal WU key (free for PWS owners) or a
Synoptic Data token; swap WEBKEY/env then. Cadence is deliberately gentle
(5-min cron, <=4 obs calls/city, 0.15s pacing, jittered by cron seconds).

Rollback: remove the crontab line. State: ~/wb_research/.pws_mesh_state.json
(per-city station roster resolved 1x/day via v3/location/near + per-PWS
last-epoch cursors; dead stations rotate out on repeated 204s).
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/polymarket-ai-v2-weather")
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY  # noqa

WEBKEY = os.environ.get("WU_WEBKEY", "e1f10a1e78da46f5b10a1e78da96f525")
OUT = ("/home/ubuntu/wb_research/pws_mesh_%s.jsonl"
       % datetime.now(timezone.utc).strftime("%Y%m%d"))
STATE = "/home/ubuntu/wb_research/.pws_mesh_state.json"
STATIONS_PER_CITY = 4
CANDIDATES_PER_CITY = 10
DEAD_AFTER = 5          # consecutive empty polls -> rotate to next candidate
LOCAL_H0, LOCAL_H1 = 9, 21


def fetch_json(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "wb-research/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            return json.loads(r.read().decode())
    except Exception:
        return None


def resolve_roster(st):
    d = fetch_json("https://api.weather.com/v3/location/near?"
                   f"geocode={st.latitude},{st.longitude}&product=pws"
                   f"&format=json&apiKey={WEBKEY}")
    try:
        loc = d["location"]
        ids = loc["stationId"][:CANDIDATES_PER_CITY]
        km = loc["distanceKm"][:CANDIDATES_PER_CITY]
        return [{"pws": i, "km": k, "misses": 0} for i, k in zip(ids, km) if i]
    except Exception:
        return []


def active_cities():
    """US F-station cities with an ACTIVE temp family (same SQL family as
    trade_prints.py) — bounds the call budget to markets that can trade."""
    raw = subprocess.run(
        ["sudo", "-u", "postgres", "psql", "polymarket", "-At", "-c",
         """SELECT DISTINCT question FROM markets
            WHERE category='weather' AND resolved IS NOT TRUE
              AND question LIKE 'Will the highest temperature%'
              AND end_date_iso BETWEEN NOW() - INTERVAL '1 day'
                                   AND NOW() + INTERVAL '2 days'"""],
        capture_output=True, text=True, timeout=60).stdout
    cities = set()
    for q in raw.splitlines():
        m = re.match(r"Will the highest temperature in (.+?) be ", q)
        if m:
            cities.add(m.group(1).lower())
    return cities


def main():
    try:
        state = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        state = {}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    live = active_cities()
    new_count = 0
    with open(OUT, "a") as out:
        for st in STATION_REGISTRY.values():
            # GLOBAL (operator hard directive 2026-07-16): every active-market
            # city, any country/unit. temp_f stays °F (units=e) — analysis
            # converts to native units where needed.
            if st.city_name.lower() not in live:
                continue
            lh = datetime.now(ZoneInfo(st.timezone)).hour
            if not (LOCAL_H0 <= lh <= LOCAL_H1):
                continue
            cs = state.setdefault(st.station_id, {})
            if cs.get("resolved") != today or not cs.get("roster"):
                roster = resolve_roster(st)
                time.sleep(0.15)
                if roster:
                    # keep miss counts of surviving ids across re-resolution
                    old = {r["pws"]: r.get("misses", 0)
                           for r in cs.get("roster", [])}
                    for r in roster:
                        r["misses"] = old.get(r["pws"], 0)
                    cs["roster"] = roster
                    cs["resolved"] = today
                if not cs.get("roster"):
                    continue
            cursors = cs.setdefault("last_epoch", {})
            alive = [r for r in cs["roster"] if r["misses"] < DEAD_AFTER]
            for r in alive[:STATIONS_PER_CITY]:
                d = fetch_json("https://api.weather.com/v2/pws/observations/"
                               f"current?stationId={r['pws']}&format=json"
                               f"&units=e&apiKey={WEBKEY}&numericPrecision=decimal")
                time.sleep(0.15)
                obs = (d or {}).get("observations") or []
                if not obs:
                    r["misses"] = r.get("misses", 0) + 1
                    continue
                r["misses"] = 0
                o = obs[0]
                epoch = o.get("epoch") or 0
                if epoch <= cursors.get(r["pws"], 0):
                    continue
                cursors[r["pws"]] = epoch
                imp = o.get("imperial") or {}
                out.write(json.dumps({
                    "sid": st.station_id, "pws": r["pws"],
                    "km": r["km"], "epoch": epoch,
                    "obs_utc": o.get("obsTimeUtc"),
                    "temp_f": imp.get("temp"),
                    "qc": o.get("qcStatus"),
                    "lat": o.get("lat"), "lon": o.get("lon"),
                    "fetched_at": fetched_at,
                }) + "\n")
                new_count += 1

    # prune stale city entries (roster not re-resolved for 3+ days)
    cutoff = (datetime.now(timezone.utc).toordinal() - 3)
    state = {k: v for k, v in state.items()
             if v.get("resolved") and
             datetime.strptime(v["resolved"], "%Y-%m-%d").toordinal() >= cutoff}
    json.dump(state, open(STATE, "w"))
    print("%s pws_mesh cities=%d new_obs=%d" % (fetched_at, len(live), new_count))


main()
