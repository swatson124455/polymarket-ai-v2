#!/usr/bin/env python3
"""S231-late GLOBAL ROBUSTNESS RUN of the peak model (pre-registered as a
robustness cut, commit 6d496fc — NOT a re-gate; the S231 US gate stands).

GLOBAL MANDATE (operator, 2026-07-16): all cities, all units. Differences vs
nowcast_peak_model.py (whose frozen rule and conventions are otherwise kept):
- No US/F filter. Bucket matching in NATIVE units (±0.5 native).
- Rule threshold native: E_rem <= 1.0F for F stations, <= 0.5556C for C.
- Forecasts: DB ensemble median (native, latest before t_cross) primary;
  archived previous-runs day-1 fmax fills holes (temperature_unit per
  station -> native).
- Crossing detector: US = IEM 1-min (as the gate run); NON-US has no 1-min
  product -> first HOURLY print entering the bucket, entry at print time
  +6 min pub delay (this is also what is live-feasible there). Cuts are
  reported separately; do NOT pool-compare against the US 1-min gate number.

Usage: python3 nowcast_peak_global.py [days_back=136] [max_family_days=3000]
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
from statistics import mean, median, pstdev
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/polymarket-ai-v2-weather")
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY  # noqa

PUB_DELAY_MIN = 6
LAG_GUARD_DAYS = 3
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 136
MAX_FAM = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
ARCH_MODELS = ["gfs_seamless", "ecmwf_ifs025"]

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


def iem_station(sid):
    return sid[1:] if sid.startswith("K") else sid


def get_series(kind, sid, d0, d1):
    if kind == "1min":
        u = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py?"
             f"station={iem_station(sid)}&sts={d0:%Y-%m-%d}T00:00Z&ets={d1:%Y-%m-%d}T23:59Z"
             "&vars=tmpf&sample=1min&what=download&delim=comma")
        tcol, vcol = "valid(UTC)", "tmpf"
    else:
        u = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
             f"station={iem_station(sid)}&data=tmpf"
             f"&year1={d0.year}&month1={d0.month}&day1={d0.day}"
             f"&year2={d1.year}&month2={d1.month}&day2={d1.day}"
             "&tz=Etc/UTC&format=onlycomma&missing=M&trace=T"
             "&report_type=3&report_type=4")
        tcol, vcol = "valid", "tmpf"
    raw = fetch(u, timeout=120)
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
        time.sleep(0.1)
    h = cache[key]
    if not h:
        return None
    b = min(h, key=lambda x: abs(x["t"] - ts))
    return float(b["p"]) if abs(b["t"] - ts) <= 600 else None


def fetch_archived_fmax(st, d0, d1):
    unit = "fahrenheit" if st.temp_unit == "F" else "celsius"
    per_model = defaultdict(dict)
    cur = d0
    while cur <= d1:
        end = min(cur + timedelta(days=34), d1)
        u = ("https://previous-runs-api.open-meteo.com/v1/forecast?"
             f"latitude={st.latitude}&longitude={st.longitude}"
             "&hourly=temperature_2m_previous_day1"
             f"&models={','.join(ARCH_MODELS)}"
             f"&start_date={cur}&end_date={end}"
             f"&temperature_unit={unit}&timezone=auto")
        raw = fetch(u, timeout=60)
        cur = end + timedelta(days=1)
        time.sleep(0.15)
        if not raw:
            continue
        try:
            h = json.loads(raw)["hourly"]
        except Exception:
            continue
        times = h.get("time", [])
        for m in ARCH_MODELS:
            d = per_model[m]
            for tstr, v in zip(times, h.get(f"temperature_2m_previous_day1_{m}", [])):
                if v is None:
                    continue
                day = tstr[:10]
                if day not in d or v > d[day]:
                    d[day] = v
    out = {}
    days = set()
    for m in per_model:
        days |= set(per_model[m])
    for day in days:
        vs = [per_model[m][day] for m in ARCH_MODELS if day in per_model[m]]
        if vs:
            out[day] = median(vs)
    return out


def to_native(tf, unit):
    return tf if unit == "F" else (tf - 32.0) * 5.0 / 9.0


def main():
    now = datetime.now(timezone.utc)
    d1 = (now - timedelta(days=LAG_GUARD_DAYS)).date()
    d0 = (now - timedelta(days=LAG_GUARD_DAYS + DAYS_BACK)).date()
    split_day = d0 + timedelta(days=DAYS_BACK // 2)

    rows = json.loads(psql("""SELECT json_agg(row_to_json(t)) FROM (
      SELECT question, yes_token_id AS token, resolution FROM markets
      WHERE category='weather' AND resolution IN ('YES','NO')
        AND question LIKE 'Will the highest temperature%%'
        AND yes_token_id IS NOT NULL AND yes_token_id <> '') t""") or "[]")
    fams = defaultdict(list)
    for r in rows or []:
        p = parse_q(r["question"])
        if not p:
            continue
        city, mon, day, lo, hi = p
        if CITY.get(city) is None:
            continue
        ld = datetime(2026, mon, day).date()
        if not (d0 <= ld <= d1):
            continue
        fams[(city, mon, day)].append(
            dict(lo=lo, hi=hi, token=r["token"], won=r["resolution"] == "YES"))

    fc_rows = json.loads(psql(f"""SELECT json_agg(row_to_json(t)) FROM (
      SELECT station_id sid, target_date::date::text td, forecast_time ft,
             ensemble_members mem
      FROM weather_forecasts
      WHERE target_date::date BETWEEN '{d0}' AND '{d1}'
        AND ensemble_members IS NOT NULL) t""") or "[]")
    fmap = defaultdict(list)
    for f in fc_rows or []:
        if f["mem"] and len(f["mem"]) >= 10:
            ft = datetime.fromisoformat(f["ft"]).replace(tzinfo=timezone.utc)
            fmap[(f["sid"], f["td"])].append((ft, median(f["mem"])))
    for k in fmap:
        fmap[k].sort()

    stations = {CITY[c].station_id: CITY[c] for (c, _, _) in fams}
    arch = {}
    for sid, st in sorted(stations.items()):
        got = fetch_archived_fmax(st, d0, d1)
        for day, v in got.items():
            arch[(sid, day)] = v
        print(f"  archived fmax (native): {sid} {len(got)} days", flush=True)
    print(f"family-days {d0}..{d1}: {len(fams)} | stations {len(stations)} "
          f"| split {split_day}", flush=True)

    entries = []
    scache, pcache = {}, {}
    fam_used = 0
    for (city, mon, day), buckets in sorted(fams.items()):
        if fam_used >= MAX_FAM:
            break
        st = CITY[city]
        tz = ZoneInfo(st.timezone)
        us = st.station_id.startswith("K") and st.temp_unit == "F"
        thr = 1.0 if st.temp_unit == "F" else 1.0 * 5.0 / 9.0
        ld = datetime(2026, mon, day).date()
        u0, u1 = ld - timedelta(days=1), ld + timedelta(days=1)
        kind = "1min" if us else "hourly"
        k1 = (st.station_id, kind, u0)
        if k1 not in scache:
            scache[k1] = get_series(kind, st.station_id, u0, u1)
            time.sleep(0.25)
        one = [(t, to_native(v, st.temp_unit)) for t, v in scache[k1]
               if t.astimezone(tz).date() == ld]
        if len(one) < (300 if us else 8):
            continue
        flist = fmap.get((st.station_id, ld.isoformat()), [])
        fc_arch = arch.get((st.station_id, ld.isoformat()))
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
                t_entry = t if us else t + timedelta(minutes=PUB_DELAY_MIN)
                lh = t_entry.astimezone(tz).hour
                if not (9 <= lh < 23):
                    continue
                prior = [fm for ft, fm in flist if ft <= t_entry]
                fc_db = prior[-1] if prior else None
                if fc_db is None and fc_arch is None:
                    continue
                fc_pri = fc_db if fc_db is not None else fc_arch
                pc = px_at(b["token"], int(t_entry.timestamp()), pcache)
                if pc is None or not (0.001 < pc < 0.999):
                    continue
                y = 1.0 if b["won"] else 0.0
                e_rem_native = fc_pri - rm
                entries.append(dict(
                    ev=y - pc, hour=lh, price=pc, won=b["won"],
                    picked=(e_rem_native <= thr and lh >= 12),
                    us=us, fam=(city, mon, day),
                    is_test=ld >= split_day))
                got_any = True
        if got_any:
            fam_used += 1
            if fam_used % 100 == 0:
                print(f"  ...{fam_used} family-days", flush=True)

    print(f"priced entries: {len(entries)} ({fam_used} family-days) | "
          f"US {sum(1 for e in entries if e['us'])} / "
          f"non-US {sum(1 for e in entries if not e['us'])}")

    def report(tag, pool):
        print(f"\n  [{tag}] (frozen rule, native threshold)")
        for half, flag in (("TRAIN", False), ("TEST", True)):
            sel = [e for e in pool if e["is_test"] == flag and e["picked"]]
            if not sel:
                print(f"    {half}: no entries")
                continue
            n = len(sel)
            by = defaultdict(list)
            for e in sel:
                by[e["fam"]].append(e["ev"])
            fm = [mean(v) for v in by.values()]
            cse = (pstdev(fm) / len(fm) ** 0.5) if len(fm) > 1 else 0.0
            ev = mean(e["ev"] for e in sel)
            wr = sum(1 for e in sel if e["won"]) / n
            print(f"    {half}: n={n} meanEV {ev:+.3f} cSE {cse:.3f} "
                  f"fams={len(fm)} WR {wr:.0%} med price "
                  f"{median(e['price'] for e in sel):.2f}")
        rej = [e for e in pool if not e["picked"]]
        if rej:
            print(f"    rejected: n={len(rej)} meanEV "
                  f"{mean(e['ev'] for e in rej):+.3f} "
                  f"win% {sum(1 for e in rej if e['won'])/len(rej):.0%}")

    report("GLOBAL pooled (info only — mixed detectors)", entries)
    report("US (1-min detector — comparability check vs the gate)",
           [e for e in entries if e["us"]])
    report("NON-US (print-time detector — the new territory)",
           [e for e in entries if not e["us"]])
    print("\nNOT a re-gate: the S231 US gate stands. Non-US uses print-time")
    print("crossings (no 1-min product exists) — also the live-feasible mode.")


main()
