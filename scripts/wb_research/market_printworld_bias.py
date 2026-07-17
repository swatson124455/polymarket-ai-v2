#!/usr/bin/env python3
"""S231-late STUDY A (pre-registered in WB_NOWCAST_CAPTURE_SPEC.md BEFORE this
ran; commit c005592) — does the MARKET carry the print-world bias?

rep_bias_test proved settlement lives in the hourly-print world ~0.9F below
the continuous-max world public forecasts describe. If the crowd anchors on
those forecasts, buckets safely BELOW the forecast max are underpriced
market-wide (the market-side twin of our bot's cheap-NO tail).

FROZEN RULE: resolved US-F families 03-01..07-12 (question-date keyed);
fc_max = archived previous-runs day-1 forecast max (uniform, no lookahead);
select buckets with hi_bound <= fc_max - 1.0F (range + at_or_below);
CLOB minute price at T = local-midnight-EOD - {24h, 14h}; 0.01 < p < 0.60;
EV/$1 = outcome - price (buy the bucket); FAMILY-DAY-CLUSTERED SEs.
GATE (per lead): meanEV >= +0.05 with 2*cSE excluding 0 -> trade-candidate;
>=2 sigma but < +0.05 -> BIAS-CONFIRMED-NOT-TRADEABLE; else DEAD.
Distance bins (1-3F / 3-5F / >5F below fc) are informational only.

Usage: python3 market_printworld_bias.py [days_back=133] [max_family_days=2000]
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
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 133
MAX_FAM = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
LEADS_H = (24.0, 14.0)
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


def fetch_archived_fmax(st, d0, d1):
    per_model = defaultdict(dict)
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
        if not st or st.temp_unit != "F" or not st.station_id.startswith("K"):
            continue
        ld = datetime(2026, mon, day).date()
        if not (d0 <= ld <= d1):
            continue
        fams[(city, mon, day)].append(
            dict(lo=lo, hi=hi, token=r["token"], won=r["resolution"] == "YES"))

    stations = {CITY[c].station_id: CITY[c] for (c, _, _) in fams}
    arch = {}
    for sid, st in sorted(stations.items()):
        got = fetch_archived_fmax(st, d0, d1)
        for day, v in got.items():
            arch[(sid, day)] = v
        print(f"  archived fmax: {sid} {len(got)} days", flush=True)
    print(f"family-days {d0}..{d1}: {len(fams)} | archived fmax days: {len(arch)}",
          flush=True)

    bets = defaultdict(list)      # lead -> [(ev, fam)]
    bins = defaultdict(list)      # (lead, binlabel) -> [(ev, fam)]
    skip = defaultdict(int)
    pcache = {}
    fam_used = set()
    for (city, mon, day), buckets in sorted(fams.items()):
        if len(fam_used) >= MAX_FAM:
            break
        st = CITY[city]
        tz = ZoneInfo(st.timezone)
        ld = datetime(2026, mon, day).date()
        fc = arch.get((st.station_id, ld.isoformat()))
        if fc is None:
            skip["no_arch_forecast"] += 1
            continue
        midnight = datetime(2026, mon, day, tzinfo=tz) + timedelta(days=1)
        below = [b for b in buckets if b["hi"] < 1e8 and b["hi"] <= fc - 1.0]
        if not below:
            skip["no_below_buckets"] += 1
            continue
        for hlead in LEADS_H:
            T = int((midnight - timedelta(hours=hlead)).timestamp())
            for b in below:
                pc = px_at(b["token"], T, pcache)
                if pc is None or not (0.01 < pc < 0.60):
                    continue
                y = 1.0 if b["won"] else 0.0
                fam = (city, mon, day)
                bets[hlead].append((y - pc, fam))
                dist = fc - b["hi"]
                lab = "1-3F" if dist <= 3 else ("3-5F" if dist <= 5 else ">5F")
                bins[(hlead, lab)].append((y - pc, fam))
                fam_used.add(fam)

    print(f"\nfamily-days contributing: {len(fam_used)} | skips: {dict(skip)}")
    print("\nSTUDY A — buy every bucket with hi <= fc_max - 1.0F (frozen rule):")
    print("  lead | n | meanEV | clustered SE | ~sigma | fams | WR | GATE")
    for hlead in LEADS_H:
        n, ev, se, nf, wr = cluster_stats(bets[hlead])
        sig = ev / se if se > 0 else float("nan")
        if n == 0:
            print(f"  {hlead:4.0f}h | no bets")
            continue
        gate = ("TRADE-CANDIDATE" if ev >= 0.05 and ev - 2 * se > 0 else
                "BIAS-CONFIRMED-NOT-TRADEABLE" if se > 0 and ev - 2 * se > 0 else
                "DEAD")
        print(f"  {hlead:4.0f}h | {n:4d} | {ev:+.3f} | {se:.3f} | {sig:5.1f} "
              f"| {nf:4d} | {wr:.0%} | {gate}")
        for lab in ("1-3F", "3-5F", ">5F"):
            bn, bev, bse, bnf, bwr = cluster_stats(bins[(hlead, lab)])
            if bn:
                print(f"      below-fc {lab:>4}: n={bn:4d} meanEV {bev:+.3f} "
                      f"cSE {bse:.3f} fams={bnf} WR {bwr:.0%}")
    print("\nConventions: mid/last-trade minute mark (NOT executable); archived")
    print("day-1 forecast (uniform, no lookahead); bins informational only.")


main()
