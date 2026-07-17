#!/usr/bin/env python3
"""S231-late STUDY D (pre-registered BEFORE running; commit 6d496fc) —
FORECAST-REVISION MOMENTUM, GLOBAL. The S229 duel tested forecast LEVELS
(market won). This tests DELTAS: when consecutive model runs revise the
daily-max forecast, does the market lag the revision?

FROZEN RULE: GLOBAL resolved families 03->07. rev = archived previous_day1
fmax - previous_day2 fmax (previous-runs API, same target date, both issued
before day D — no lookahead; degF). Target bucket = the bucket containing
the FRESH forecast (previous_day1 max, converted to station-native units,
±0.5 native matching). CLOB price at T = local-midnight-EOD - 24h; require
0.03 < p < 0.90. CASES: |rev| >= 1.5F. CONTROL: |rev| < 0.5F.
EV/$1 = outcome - price (buy the fresh-forecast bucket). Family-clustered.
GATE: case meanEV >= +0.05 with 2x cSE excluding 0 AND case > control ->
trade-candidate; significant-but-small -> BIAS-CONFIRMED-NOT-TRADEABLE;
else DEAD.

Usage: python3 revision_momentum.py [days_back=136] [max_family_days=3000]
"""
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

LAG_GUARD_DAYS = 3
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 136
MAX_FAM = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
ARCH_MODELS = ["gfs_seamless", "ecmwf_ifs025"]
REV_CASE = 1.5
REV_CTRL = 0.5

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
    return float(b["p"]) if abs(b["t"] - ts) <= 300 else None


def fetch_archived_days(st, d0, d1):
    """-> {date: (fmax_day1, fmax_day2)} in degF, median across models."""
    per = {1: defaultdict(dict), 2: defaultdict(dict)}
    cur = d0
    while cur <= d1:
        end = min(cur + timedelta(days=34), d1)
        u = ("https://previous-runs-api.open-meteo.com/v1/forecast?"
             f"latitude={st.latitude}&longitude={st.longitude}"
             "&hourly=temperature_2m_previous_day1,temperature_2m_previous_day2"
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
        for lead in (1, 2):
            for m in ARCH_MODELS:
                d = per[lead][m]
                for tstr, v in zip(times,
                                   h.get(f"temperature_2m_previous_day{lead}_{m}", [])):
                    if v is None:
                        continue
                    day = tstr[:10]
                    if day not in d or v > d[day]:
                        d[day] = v
    out = {}
    days = set()
    for lead in (1, 2):
        for m in per[lead]:
            days |= set(per[lead][m])
    for day in days:
        v1 = [per[1][m][day] for m in ARCH_MODELS if day in per[1][m]]
        v2 = [per[2][m][day] for m in ARCH_MODELS if day in per[2][m]]
        if v1 and v2:
            out[day] = (median(v1), median(v2))
    return out


def to_native(tf, unit):
    return tf if unit == "F" else (tf - 32.0) * 5.0 / 9.0


def cluster_stats(bets):
    if not bets:
        return 0, 0.0, 0.0, 0, 0.0
    ev = mean(b[0] for b in bets)
    by = defaultdict(list)
    for e, f in bets:
        by[f].append(e)
    fm = [mean(v) for v in by.values()]
    se = (pstdev(fm) / len(fm) ** 0.5) if len(fm) > 1 else 0.0
    wr = sum(1 for e, _ in bets if e > 0) / len(bets)
    return len(bets), ev, se, len(by), wr


def main():
    now = datetime.now(timezone.utc)
    d1 = (now - timedelta(days=LAG_GUARD_DAYS)).date()
    d0 = (now - timedelta(days=LAG_GUARD_DAYS + DAYS_BACK)).date()

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
        if not st:
            continue
        ld = datetime(2026, mon, day).date()
        if not (d0 <= ld <= d1):
            continue
        fams[(city, mon, day)].append(
            dict(lo=lo, hi=hi, token=r["token"], won=r["resolution"] == "YES"))

    stations = {CITY[c].station_id: CITY[c] for (c, _, _) in fams}
    arch = {}
    for sid, st in sorted(stations.items()):
        got = fetch_archived_days(st, d0, d1)
        for day, v in got.items():
            arch[(sid, day)] = v
        print(f"  archived day1+day2 fmax: {sid} {len(got)} days", flush=True)
    print(f"family-days {d0}..{d1}: {len(fams)} | stations {len(stations)}", flush=True)

    bets = {"case": [], "ctrl": []}
    cuts = defaultdict(list)
    skip = defaultdict(int)
    pcache = {}
    for (city, mon, day), buckets in sorted(fams.items()):
        st = CITY[city]
        tz = ZoneInfo(st.timezone)
        ld = datetime(2026, mon, day).date()
        got = arch.get((st.station_id, ld.isoformat()))
        if not got:
            skip["no_arch"] += 1
            continue
        f1, f2 = got
        rev = f1 - f2
        if abs(rev) >= REV_CASE:
            grp = "case"
        elif abs(rev) < REV_CTRL:
            grp = "ctrl"
        else:
            skip["mid_rev_excluded"] += 1
            continue
        fresh = to_native(f1, st.temp_unit)
        target = next((b for b in buckets
                       if b["lo"] - 0.5 <= fresh < b["hi"] + 0.5), None)
        if target is None:
            skip["no_target_bucket"] += 1
            continue
        midnight = datetime(2026, mon, day, tzinfo=tz) + timedelta(days=1)
        T = int((midnight - timedelta(hours=24)).timestamp())
        pc = px_at(target["token"], T, pcache)
        if pc is None or not (0.03 < pc < 0.90):
            skip["no_price"] += 1
            continue
        y = 1.0 if target["won"] else 0.0
        fam = (city, mon, day)
        bets[grp].append((y - pc, fam))
        cuts[(grp, "US" if st.station_id.startswith("K") else "nonUS")].append(
            (y - pc, fam))
        if len(bets["case"]) % 50 == 0 and grp == "case":
            print(f"  ...cases {len(bets['case'])} ctrls {len(bets['ctrl'])}",
                  flush=True)

    print(f"\nskips: {dict(skip)}")
    print("\nSTUDY D — buy the FRESH-forecast bucket at EOD-24h:")
    out = {}
    for grp, lab in (("case", f"|rev|>={REV_CASE}F"), ("ctrl", f"|rev|<{REV_CTRL}F")):
        n, ev, se, nf, wr = cluster_stats(bets[grp])
        out[grp] = (ev, se)
        sig = ev / se if se > 0 else float("nan")
        print(f"  {lab:<12}: n={n:4d} meanEV {ev:+.3f} cSE {se:.3f} "
              f"(~{sig:.1f} sigma) fams={nf} WR {wr:.0%}")
        for reg in ("US", "nonUS"):
            rn, rev_, rse, rnf, rwr = cluster_stats(cuts[(grp, reg)])
            if rn:
                print(f"      {reg:>5}: n={rn:4d} meanEV {rev_:+.3f} cSE {rse:.3f} "
                      f"fams={rnf} WR {rwr:.0%}")
    (cev, cse), (kev, _) = out["case"], out["ctrl"]
    gate = ("TRADE-CANDIDATE" if cev >= 0.05 and cse > 0 and cev - 2 * cse > 0
            and cev > kev else
            "BIAS-CONFIRMED-NOT-TRADEABLE" if cse > 0 and cev - 2 * cse > 0
            and cev > kev else "DEAD")
    print(f"\nGATE: case {cev:+.3f} (bar +0.05, 2sigma, > control {kev:+.3f}) -> {gate}")
    print("Conventions: archived previous-runs (no lookahead); mid/minute mark")
    print("NOT executable; case direction is |rev| both signs (frozen).")


main()
