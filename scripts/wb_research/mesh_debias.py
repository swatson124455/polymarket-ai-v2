#!/usr/bin/env python3
"""S232 Phase-2 — per-PWS debias table producer (VPS cron, daily, ubuntu user).

Computes, for every city the pws_mesh cron collects, each PWS's trailing
offset vs the airport METAR PRINT series (the world resolution grades — see
spec §S232 REP_BIAS RECOMPUTE), and writes a bot-readable table:

    /opt/pa2-weather-feeds/mesh_debias.json        (atomic tmp+rename)
    ~/wb_research/mesh_debias_YYYYMMDD.json        (dated research copy)

Table shape per city sid:
    pws:   { pws_id: { n, scalar, blocks: {morning|afternoon|evening:
             [offset_f, n]} } }   — blocks are LOCAL-hour 6-11/12-17/18-23;
             a block is published only at n>=8; a PWS only at n>=5.
    residual_sd: sd of (ob - scalar_offset - print) over all matches —
             the post-debias noise floor. dropped: residual_sd > 1.5F
             (per-city drop rule; Shenzhen is the motivating case).
    n_matched, generated from trail_days of history (default 5).

Scope rules (deliberate):
  - CURRENT registry sids only. Rows logged under pre-S231-fix legacy ids
    (KDEN/KDFW/KIAH/RKSS/RCTP/LTBA/LIML) measured the WRONG airport — they
    are excluded, so renamed cities cold-start their tables from 07-17.
  - GLOBAL per the WB-ALWAYS-GLOBAL directive: every city in the mesh files
    is processed; cities IEM has no METARs for (lowercase pseudo-sids) just
    yield no matches and no table row.
  - Hour-block offsets are computed for ALL cities; the consumer prefers a
    block offset when populated and falls back to the scalar — the
    "diurnal city list" is emergent from data, never hardcoded.

Also prunes pws_mesh_*.jsonl older than 7 days from the FEED dir only (the
research copies in ~/wb_research are never touched).

Usage: mesh_debias.py [trail_days=5]   (cron: 15 9 * * *)
"""
import glob
import io
import csv
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median, pstdev
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/polymarket-ai-v2-weather")
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY  # noqa

SID = {s.station_id: s for s in STATION_REGISTRY.values()}
MESH_DIR = "/home/ubuntu/wb_research"
FEED_DIR = "/opt/pa2-weather-feeds"
OUT_LIVE = os.path.join(FEED_DIR, "mesh_debias.json")
TRAIL_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 5
MIN_PWS_N = 5
MIN_BLOCK_N = 8
DROP_SD_F = 1.5
BLOCKS = (("morning", 6, 11), ("afternoon", 12, 17), ("evening", 18, 23))


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


def iem_station(sid):
    return sid[1:] if sid.startswith("K") else sid


def load_mesh_window(day0, day1):
    """{sid: [(epoch, temp_f, pws)]} qc==1, from files named day0..day1+1 UTC."""
    out = defaultdict(list)
    d = day0
    while d <= day1 + timedelta(days=1):
        for path in glob.glob(os.path.join(MESH_DIR, f"pws_mesh_{d:%Y%m%d}.jsonl")):
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if r.get("qc") != 1 or r.get("temp_f") is None:
                        continue
                    out[r["sid"]].append((int(r["epoch"]), float(r["temp_f"]), r["pws"]))
        d += timedelta(days=1)
    for k in out:
        out[k].sort()
    return out


def iem_prints(sid3, d0, d1):
    u = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
         f"station={sid3}&data=tmpf&year1={d0.year}&month1={d0.month}&day1={d0.day}"
         f"&year2={d1.year}&month2={d1.month}&day2={d1.day}"
         "&tz=Etc/UTC&format=onlycomma&missing=M&trace=T"
         "&report_type=3&report_type=4")
    raw = fetch(u)
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


def block_of(local_hour):
    for name, lo, hi in BLOCKS:
        if lo <= local_hour <= hi:
            return name
    return None


def main():
    now = datetime.now(timezone.utc)
    d1 = now.date()
    d0 = d1 - timedelta(days=TRAIL_DAYS)
    mesh = load_mesh_window(datetime.combine(d0, datetime.min.time()),
                            datetime.combine(d1, datetime.min.time()))
    cities = {}
    for sid, series in sorted(mesh.items()):
        st = SID.get(sid)
        if st is None:
            continue                      # legacy/pseudo sid — no table row
        tz = ZoneInfo(st.timezone)
        prints = iem_prints(iem_station(sid), d0, d1)
        time.sleep(0.2)
        if not prints:
            continue
        # per-PWS matches: latest ob per PWS within [print-15min, print+1min]
        per_pws = defaultdict(list)       # pws -> [(offset_f, local_hour)]
        for t, v in prints:
            ep = int(t.timestamp())
            latest = {}
            for oep, otf, opws in series:
                if ep - 900 <= oep <= ep + 60:
                    latest[opws] = otf
            lh = t.astimezone(tz).hour
            for opws, otf in latest.items():
                per_pws[opws].append((otf - v, lh))
        pws_table = {}
        residuals = []
        for pws, offs in per_pws.items():
            if len(offs) < MIN_PWS_N:
                continue
            vals = [o for o, _ in offs]
            scalar = round(median(vals), 2)
            blocks = {}
            for name, lo, hi in BLOCKS:
                bv = [o for o, h in offs if lo <= h <= hi]
                if len(bv) >= MIN_BLOCK_N:
                    blocks[name] = [round(median(bv), 2), len(bv)]
            pws_table[pws] = {"n": len(offs), "scalar": scalar, "blocks": blocks}
            residuals += [o - scalar for o in vals]
        if not pws_table:
            continue
        rsd = round(pstdev(residuals), 2) if len(residuals) > 1 else None
        cities[sid] = {
            "pws": pws_table,
            "n_matched": len(residuals),
            "residual_sd": rsd,
            "dropped": bool(rsd is not None and rsd > DROP_SD_F),
        }
    doc = {
        "generated_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trail_days": TRAIL_DAYS,
        "drop_sd_f": DROP_SD_F,
        "cities": cities,
    }
    os.makedirs(FEED_DIR, exist_ok=True)
    tmp = OUT_LIVE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f, separators=(",", ":"))
    os.replace(tmp, OUT_LIVE)             # atomic — bot never sees a partial
    dated = os.path.join(MESH_DIR, f"mesh_debias_{now:%Y%m%d}.json")
    with open(dated, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1)
    # prune FEED-dir mesh day-files >7d (research copies untouched)
    cutoff = (now - timedelta(days=7)).strftime("%Y%m%d")
    for p in glob.glob(os.path.join(FEED_DIR, "pws_mesh_*.jsonl")):
        tag = os.path.basename(p)[9:17]
        if tag.isdigit() and tag < cutoff:
            try:
                os.remove(p)
            except OSError:
                pass
    dropped = [s for s, c in cities.items() if c["dropped"]]
    print(f"{now:%Y-%m-%dT%H:%M:%SZ} mesh_debias cities={len(cities)} "
          f"dropped={dropped} out={OUT_LIVE}")


if __name__ == "__main__":
    main()
