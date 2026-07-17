#!/usr/bin/env python3
"""S230 Phase-0a-ii peak-model backtest (read-only, VPS) — THE critical gate.

The loser-leg replay (nowcast_entry_ev.py) showed buy-every-crossing is
EV-zero: only ~33% of crossings are final. This tests whether two real-time-
knowable features separate final crossings from intermediate ones:

  E_rem  = forecast daily-max (median of stored ensemble members, latest
           forecast issued BEFORE t_cross — no lookahead) − current runmax
  hour   = local hour of the crossing

Output 1: EV per (E_rem bin × hour band) cell — a priori bins, no fitting.
Output 2: the pre-registered RULE "enter iff E_rem <= 1.0 AND hour >= 12",
          evaluated on a DATE-SPLIT (train days report only context; the
          verdict is the TEST half). Gate bar (spec): test meanEV >= +0.05
          at mid with ~2SE excluding 0.

S231 (deep-backtest task 1): archived Open-Meteo forecasts wired in via the
PREVIOUS-RUNS API (`temperature_2m_previous_day1` — values for day D come
from runs issued on D-1, so they are ALWAYS before any day-D crossing; the
historical-forecast API is a shortest-lead mosaic = lookahead, NOT used).
Archived forecast daily-max = median across models (gfs_seamless,
ecmwf_ifs025) of max over local-day hourlies. PRIMARY E_rem = DB forecast
when present (unchanged semantics), archived fills the holes; DB-only and
archived-only cuts reported for robustness. Family window now keys on the
QUESTION date (end_date_iso NULLs no longer drop families). Rule FROZEN.

Usage: python3 nowcast_peak_model.py [days_back=12] [max_family_days=150]
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
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 12
MAX_FAM = int(sys.argv[2]) if len(sys.argv) > 2 else 150
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


def fetch_archived_fmax(st, d0, d1):
    """Archived day-1-lead forecast daily max per local date (previous-runs API).

    temperature_2m_previous_day1 values for day D come from model runs issued
    on D-1 -> issued before any day-D crossing (no lookahead). Daily max =
    max over the local-day hourlies; median across ARCH_MODELS.
    """
    per_model = defaultdict(dict)   # model -> 'YYYY-MM-DD' -> running max
    cur = d0
    while cur <= d1:
        end = min(cur + timedelta(days=34), d1)
        u = ("https://previous-runs-api.open-meteo.com/v1/forecast?"
             f"latitude={st.latitude}&longitude={st.longitude}"
             "&hourly=temperature_2m_previous_day1"
             f"&models={','.join(ARCH_MODELS)}"
             f"&start_date={cur}&end_date={end}"
             "&temperature_unit=fahrenheit&timezone=auto")
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
            vals = h.get(f"temperature_2m_previous_day1_{m}", [])
            d = per_model[m]
            for tstr, v in zip(times, vals):
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


def main():
    now = datetime.now(timezone.utc)
    d1 = (now - timedelta(days=LAG_GUARD_DAYS)).date()
    d0 = (now - timedelta(days=LAG_GUARD_DAYS + DAYS_BACK)).date()
    split_day = d0 + timedelta(days=DAYS_BACK // 2)

    rows = json.loads(psql("""SELECT json_agg(row_to_json(t)) FROM (
      SELECT question, yes_token_id AS token, resolution FROM markets
      WHERE category='weather' AND resolution IN ('YES','NO')
        AND question LIKE 'Will the highest temperature%%'
        AND (end_date_iso IS NULL OR end_date_iso >= '2026-01-01')
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
        print(f"  archived previous_day1 fmax: {sid} {len(got)} days", flush=True)

    print(f"family-days {d0}..{d1}: {len(fams)} (question-date keyed) | DB forecast rows: "
          f"{sum(len(v) for v in fmap.values())} | archived fmax days: {len(arch)} "
          f"| split at {split_day}")

    entries = []
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
        k1 = (sid3, "1min", u0)
        if k1 not in scache:
            scache[k1] = get_series("1min", sid3, u0, u1)
            time.sleep(0.25)
        one = [(t, v) for t, v in scache[k1] if t.astimezone(tz).date() == ld]
        if len(one) < 300:
            continue
        # forecasts stored in C for C-unit stations? registry says F stations;
        # members for F stations are deg-F daily-max values (duel convention)
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
                lh = t.astimezone(tz).hour
                if not (9 <= lh < 23):
                    continue
                prior = [fm for ft, fm in flist if ft <= t]
                fc_db = prior[-1] if prior else None
                if fc_db is None and fc_arch is None:
                    continue
                fc_pri = fc_db if fc_db is not None else fc_arch
                pc = px_at(b["token"], int(t.timestamp()), pcache)
                if pc is None or not (0.001 < pc < 0.999):
                    continue
                y = 1.0 if b["won"] else 0.0
                entries.append(dict(
                    ev=y - pc, hour=lh, price=pc, won=b["won"],
                    e_rem=fc_pri - rm,
                    e_rem_db=(fc_db - rm) if fc_db is not None else None,
                    e_rem_arch=(fc_arch - rm) if fc_arch is not None else None,
                    src="db" if fc_db is not None else "arch",
                    fam=(city, mon, day), month=mon,
                    is_test=ld >= split_day))
                got_any = True
        if got_any:
            fam_used += 1

    print(f"priced crossing entries with a prior forecast: {len(entries)} "
          f"({fam_used} family-days)")
    n_db = sum(1 for e in entries if e["src"] == "db")
    n_both = sum(1 for e in entries if e["e_rem_db"] is not None
                 and e["e_rem_arch"] is not None)
    print(f"  source: db {n_db} | arch-fill {len(entries) - n_db} | both-known {n_both} "
          f"| march {sum(1 for e in entries if e['month'] == 3)}")
    if n_both >= 5:
        diffs = [e["e_rem_db"] - e["e_rem_arch"] for e in entries
                 if e["e_rem_db"] is not None and e["e_rem_arch"] is not None]
        print(f"  DB-vs-archived fmax offset (db - arch): mean {mean(diffs):+.2f}F "
              f"median {median(diffs):+.2f}F sd {pstdev(diffs):.2f}F (n={len(diffs)})")

    def cell(evs):
        n = len(evs)
        if n == 0:
            return "    -    "
        return f"{mean(e['ev'] for e in evs):+.3f}({n:3d})"

    print("\nEV by (E_rem = forecast_max - runmax) x (hour band) — all days, PRIMARY:")
    print("  E_rem bin   | h<12       | h12-13     | h>=14")
    for lab, lof, hif in (("<= 0.5F", -99, 0.5), ("0.5-2F", 0.5, 2.0),
                          ("> 2F", 2.0, 99)):
        row = [e for e in entries if lof < e["e_rem"] <= hif]
        b1 = [e for e in row if e["hour"] < 12]
        b2 = [e for e in row if 12 <= e["hour"] < 14]
        b3 = [e for e in row if e["hour"] >= 14]
        print(f"  {lab:<11} | {cell(b1)} | {cell(b2)} | {cell(b3)}")

    print("\nPRE-REGISTERED RULE: enter iff E_rem <= 1.0F AND hour >= 12")
    variants = (("PRIMARY (db, arch fills holes)", "e_rem"),
                ("DB-only", "e_rem_db"),
                ("ARCH-only", "e_rem_arch"))
    for vlab, key in variants:
        print(f"  [{vlab}]")
        pool = [e for e in entries if e[key] is not None]
        for tag, flag in (("TRAIN half", False), ("TEST half", True)):
            sel = [e for e in pool if e["is_test"] == flag
                   and e[key] <= 1.0 and e["hour"] >= 12]
            if not sel:
                print(f"    {tag}: no entries")
                continue
            n = len(sel)
            se = 0.45 / n ** 0.5
            ev = mean(e["ev"] for e in sel)
            wr = sum(1 for e in sel if e["won"]) / n
            line = (f"    {tag}: n={n} meanEV {ev:+.3f} (SE ~{se:.3f}) win% {wr:.0%} "
                    f"med price {median(e['price'] for e in sel):.2f}")
            if flag and key == "e_rem":
                # S231 review deferred #2: the gate must ALSO clear on the
                # family-clustered SE (same-family entries share one weather
                # outcome; per-entry SE alone is anti-conservative). The
                # recorded S231 PASS cleared both; future runs must too.
                by_fam = defaultdict(list)
                for e in sel:
                    by_fam[e["fam"]].append(e["ev"])
                fmeans = [mean(v) for v in by_fam.values()]
                cse = (pstdev(fmeans) / len(fmeans) ** 0.5) if len(fmeans) > 1 else 0.0
                dm = mean(fmeans)
                clustered_ok = len(fmeans) > 1 and dm - 2 * cse > 0
                line += ("  <-- GATE: " + ("PASS" if ev >= 0.05 and ev - 2 * se > 0
                                           and clustered_ok
                                           else "FAIL/INSUFFICIENT"))
                line += (f"\n      (family-clustered: {len(fmeans)} family-days, "
                         f"day-mean EV {dm:+.3f}, clustered SE ~{cse:.3f}, "
                         f"clustered-2sigma {'OK' if clustered_ok else 'NOT MET'})")
            print(line)
        rej = [e for e in pool if not (e[key] <= 1.0 and e["hour"] >= 12)]
        if rej:
            print(f"    rejected-by-rule (all days): n={len(rej)} "
                  f"meanEV {mean(e['ev'] for e in rej):+.3f} "
                  f"win% {sum(1 for e in rej if e['won'])/len(rej):.0%}")


main()
