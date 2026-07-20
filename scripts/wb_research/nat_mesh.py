#!/usr/bin/env python3
"""S233 NATIONAL-FEED MESH COLLECTOR (nat_mesh.py) — read-only research cron.

Ingests official NATIONAL met-service observations as debias ANCHORS / redundant
obs into the nowcast mesh, alongside the WU PWS mesh (pws_mesh.py). Feeds pinned +
parser-validated S233 (2026-07-20, live from the VPS):
  DWD Open Data 10-min    Berlin  EDDB (station 00427), Munich EDDM (01262)
  JMA amedas 10-min       Tokyo   RJTT (amedas 44166)
  data.gov.sg 1-min       Singapore WSSS (nearest station, dynamic)
  BOM 30-min product      Sydney  YSSY (wmo 94767), Melbourne YMML (94866)
All report CELSIUS -> converted to FAHRENHEIT (the mesh schema is °F). qc is
hardcoded 1 (official source). SMN Argentina was DEFERRED (map_items carries no
trustworthy obs timestamp — every station's `updated`==2022; unsafe to epoch).

STAGING BY DEFAULT: writes ~/wb_research/nat_mesh_YYYYMMDD.jsonl, which NOTHING
consumes — so it has ZERO effect on the live nowcast signal until validated. Set
env NAT_MESH_LIVE=1 to ALSO append rows into the pws_mesh_YYYYMMDD.jsonl files that
mesh_debias.py (~/wb_research) and nowcast_mesh.py (/opt/pa2-weather-feeds) read.
That flip is the OPERATOR-GATED go-live (it injects into a flag-on paper signal).

Row schema (BYTE-COMPATIBLE with pws_mesh.py so mesh_debias consumes it unchanged):
  {"sid":<registry ICAO>, "pws":"nat:<feed>:<sid>", "km":0.0, "epoch":<int>,
   "obs_utc":<ISO>, "temp_f":<float °F>, "qc":1, "lat":..., "lon":...,
   "fetched_at":<ISO>, "src":"nat"}

Local-hour gate 09:00-21:59 (matches pws_mesh — homogeneous debias window). Per-
source epoch cursor dedups (only NEW obs written). Fail-soft per feed: one feed
down never kills the tick (WU-fails-style counter). State:
~/wb_research/.nat_mesh_state.json.

Rollback: remove the crontab line (and unset NAT_MESH_LIVE to stop live injection).
Cron suggestion: every 10 minutes (national feeds are 10-min+ cadence; the epoch
cursor absorbs over-polling of the slower 30-min BOM product).
"""
import csv
import io
import json
import os
import sys
import urllib.request
import zipfile
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/polymarket-ai-v2-weather")
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY  # noqa

SID = {s.station_id: s for s in STATION_REGISTRY.values()}
STATE = "/home/ubuntu/wb_research/.nat_mesh_state.json"
STAGE_OUT = ("/home/ubuntu/wb_research/nat_mesh_%s.jsonl"
             % datetime.now(timezone.utc).strftime("%Y%m%d"))
# LIVE (operator-gated) targets — the SAME files pws_mesh.py writes, which
# mesh_debias.py (MESH_DIR) and nowcast_mesh.py (FEED_DIR) actually consume.
PWS_NAME = "pws_mesh_%s.jsonl" % datetime.now(timezone.utc).strftime("%Y%m%d")
LIVE_TARGETS = [
    os.path.join("/home/ubuntu/wb_research", PWS_NAME),   # -> mesh_debias.py
    os.path.join("/opt/pa2-weather-feeds", PWS_NAME),     # -> nowcast_mesh.py (bot)
]
LIVE = os.environ.get("NAT_MESH_LIVE") == "1"
LOCAL_H0, LOCAL_H1 = 9, 21
_UA = {"User-Agent": "wb-nat-mesh/1.0"}
_BOM_UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120 Safari/537.36")}
FEED_FAILS = 0   # a national-feed outage must be distinguishable from a quiet tick


def c_to_f(celsius):
    """Celsius -> Fahrenheit. The one conversion that, done wrong, silently
    corrupts the whole debias table (all national feeds report °C; the mesh
    schema is °F). Kept as a named, unit-tested function on purpose."""
    return celsius * 9.0 / 5.0 + 32.0


def _haversine(a_lat, a_lon, b_lat, b_lon):
    dlat = radians(b_lat - a_lat)
    dlon = radians(b_lon - a_lon)
    h = sin(dlat / 2) ** 2 + cos(radians(a_lat)) * cos(radians(b_lat)) * sin(dlon / 2) ** 2
    return 2 * 6371.0 * asin(sqrt(h))


def _get(url, headers, timeout=30):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


# ── per-feed fetchers (pinned + parser-validated S233) ────────────────────
# Each returns (epoch:int, temp_f:float, obs_utc:str, lat, lon) or None.

_DWD_BASE = ("https://opendata.dwd.de/climate_environment/CDC/observations_germany/"
             "climate/10_minutes/air_temperature/now")


def dwd_fetch(station5, lat, lon):
    blob = _get("%s/10minutenwerte_TU_%s_now.zip" % (_DWD_BASE, station5), _UA, 40)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    member = next(n for n in zf.namelist() if n.startswith("produkt") and n.endswith(".txt"))
    reader = csv.DictReader(io.StringIO(zf.read(member).decode("latin-1")), delimiter=";")
    last = None
    for row in reader:
        try:
            tt = float(row["TT_10"])
        except (KeyError, ValueError, TypeError):
            continue
        if tt == -999.0:
            continue
        last = (row["MESS_DATUM"].strip(), tt)   # rows chronological; keep final valid
    if last is None:
        return None
    mess, temp_c = last
    dt = datetime.strptime(mess, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    return (int(dt.timestamp()), round(c_to_f(temp_c), 2),
            dt.strftime("%Y-%m-%dT%H:%M:%SZ"), lat, lon)


_JMA_BASE = "https://www.jma.go.jp/bosai/amedas/data"


def jma_fetch(amedas_id, lat, lon):
    lt = _get("%s/latest_time.txt" % _JMA_BASE, _UA, 20).decode("utf-8").strip()
    dt = datetime.fromisoformat(lt)                      # JST tz-aware
    slot = dt.strftime("%Y%m%d%H%M%S")
    dt_utc = dt.astimezone(timezone.utc)
    m = json.loads(_get("%s/map/%s.json" % (_JMA_BASE, slot), _UA, 20))
    rec = m.get(amedas_id)
    if not rec or not rec.get("temp"):
        return None
    temp_c, qc = rec["temp"]                             # [celsius, qcflag]
    if temp_c is None or qc != 0:                        # 0 = normal QC
        return None
    return (int(dt_utc.timestamp()), round(c_to_f(temp_c), 2),
            dt_utc.strftime("%Y-%m-%dT%H:%M:%SZ"), lat, lon)


_SG_URL = "https://api.data.gov.sg/v1/environment/air-temperature"


def sg_fetch(target_lat, target_lon):
    d = json.loads(_get(_SG_URL, _UA, 30).decode("utf-8"))
    meta = {s["id"]: (s["location"]["latitude"], s["location"]["longitude"])
            for s in d["metadata"]["stations"]}
    item = d["items"][0]
    dt = datetime.fromisoformat(item["timestamp"]).astimezone(timezone.utc)
    best = None
    for rd in item["readings"]:
        st = rd["station_id"]
        if st not in meta:
            continue
        la, lo = meta[st]
        dist = _haversine(target_lat, target_lon, la, lo)
        if best is None or dist < best[0]:
            best = (dist, la, lo, rd["value"])
    if best is None:
        return None
    _, la, lo, celsius = best
    return (int(dt.timestamp()), round(c_to_f(celsius), 2),
            dt.strftime("%Y-%m-%dT%H:%M:%SZ"), la, lo)


_BOM_URLS = {
    "YSSY": "https://www.bom.gov.au/fwo/IDN60901/IDN60901.94767.json",
    "YMML": "https://www.bom.gov.au/fwo/IDV60901/IDV60901.94866.json",
}


def bom_fetch(sid):
    doc = json.loads(_get(_BOM_URLS[sid], _BOM_UA, 30))
    rows = (doc.get("observations", {}).get("data", []) or [])
    best = None
    for r in rows:
        t = r.get("air_temp")
        ts = r.get("aifstime_utc")
        if t is None or not ts:
            continue
        if best is None or ts > best["aifstime_utc"]:   # lexicographic == chronological
            best = r
    if best is None:
        return None
    dt = datetime.strptime(best["aifstime_utc"], "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    return (int(dt.timestamp()), round(c_to_f(float(best["air_temp"])), 2),
            dt.strftime("%Y-%m-%dT%H:%M:%SZ"), best.get("lat"), best.get("lon"))


# (feed, sid, human station, thunk) — sid MUST be a current registry ICAO.
FEEDS = [
    ("dwd", "EDDB", "00427", lambda: dwd_fetch("00427", 52.3807, 13.5306)),
    ("dwd", "EDDM", "01262", lambda: dwd_fetch("01262", 48.3477, 11.8134)),
    ("jma", "RJTT", "44166", lambda: jma_fetch("44166", 35.5533, 139.7800)),
    ("sg", "WSSS", "nearest", lambda: sg_fetch(1.36, 103.99)),
    ("bom", "YSSY", "94767", lambda: bom_fetch("YSSY")),
    ("bom", "YMML", "94866", lambda: bom_fetch("YMML")),
]


def _local_hour_ok(sid):
    st = SID.get(sid)
    if st is None:
        return False        # sid not in the live registry — skip (never anchor a ghost)
    lh = datetime.now(ZoneInfo(st.timezone)).hour
    return LOCAL_H0 <= lh <= LOCAL_H1


def build_row(feed, sid, epoch, temp_f, obs_utc, lat, lon, fetched_at):
    return {
        "sid": sid,
        "pws": "nat:%s:%s" % (feed, sid),   # stable per (feed, city) source key
        "km": 0.0,                          # official airport-area anchor
        "epoch": int(epoch),
        "obs_utc": obs_utc,
        "temp_f": temp_f,
        "qc": 1,                            # official source
        "lat": lat,
        "lon": lon,
        "fetched_at": fetched_at,
        "src": "nat",                       # provenance tag (pws_mesh rows have no 'src')
    }


def main():
    global FEED_FAILS
    try:
        state = json.load(open(STATE, encoding="utf-8"))
    except Exception:
        state = {}
    cursors = state.setdefault("last_epoch", {})   # pws-key -> last epoch written
    fetched_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    new_lines = []
    for feed, sid, _station, thunk in FEEDS:
        if not _local_hour_ok(sid):
            continue
        pws = "nat:%s:%s" % (feed, sid)
        try:
            res = thunk()
        except Exception as exc:
            FEED_FAILS += 1
            print("%s nat_mesh feed_error feed=%s sid=%s %s"
                  % (fetched_at, feed, sid, exc))
            continue
        if not res:
            continue                        # no obs / bad QC — not a fail
        epoch, temp_f, obs_utc, lat, lon = res
        if temp_f is None:
            continue
        if epoch <= cursors.get(pws, 0):
            continue                        # already written this obs
        cursors[pws] = epoch
        new_lines.append(json.dumps(
            build_row(feed, sid, epoch, temp_f, obs_utc, lat, lon, fetched_at)) + "\n")

    # STAGING write (always) — a separate file NOTHING consumes.
    if new_lines:
        try:
            with open(STAGE_OUT, "a") as f:
                f.writelines(new_lines)
        except Exception as exc:
            print("%s nat_mesh stage_write_failed %s" % (fetched_at, exc))
        # LIVE write (operator-gated) — inject into the consumed pws_mesh files.
        # Best-effort per target; a feed-dir failure never kills the tick and the
        # staging copy above stays canonical (same doctrine as pws_mesh's mirror).
        if LIVE:
            for tgt in LIVE_TARGETS:
                try:
                    with open(tgt, "a") as f:
                        f.writelines(new_lines)
                except Exception as exc:
                    print("%s nat_mesh live_write_failed tgt=%s %s"
                          % (fetched_at, tgt, exc))

    # persist cursors atomically (a crash mid-dump must not wipe every cursor)
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)

    print("%s nat_mesh feeds=%d new_obs=%d feed_fails=%d live=%s"
          % (fetched_at, len(FEEDS), len(new_lines), FEED_FAILS, int(LIVE)))


if __name__ == "__main__":
    main()
