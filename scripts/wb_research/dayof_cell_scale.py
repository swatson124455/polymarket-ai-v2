#!/usr/bin/env python3
"""S231 deep-backtest task 3 — the 9-12h DAY-OF CELL AT SCALE, bot-independent.

The one cell that survived S230 accrual: day-of, 9-12h-to-resolution,
bet-the-disagreement, +0.118 (SE 0.055) on n=66 clean-window prediction_log
rows. This re-cuts it over ALL resolved 03->07 US F-station families with a
signal that does not touch the bot:

  P_model(bucket) = fraction of raw ensemble members (weather_forecasts,
                    latest forecast issued BEFORE T — no lookahead, <=24h
                    stale) whose implied daily max, FLOORED at the hourly-
                    METAR running max at T (print world = settlement world),
                    lands in the bucket (+-0.5F rounding convention).
  market          = CLOB minute price at matched timestamp T
                    (T = local midnight ending day D, minus h hours).
  bet             = sign(P_model - price) when |P_model - price| >= threshold
                    and 0.03 < price < 0.97; EV per $1 at price.

Hour buckets sampled at midpoints: 3-6h->4.5, 6-9h->7.5, 9-12h->10.5,
12-24h->18. SEs are FAMILY-DAY-CLUSTERED (all bets of a family-day = one
cluster; same-family buckets are one weather outcome). Threshold sensitivity
(0.05/0.10/0.15) reported for the 9-12h cell; primary threshold 0.10.

Usage: python3 dayof_cell_scale.py [days_back=133] [max_family_days=2000]
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

LAG_GUARD_DAYS = 3
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 133
MAX_FAM = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
H_MID = (4.5, 7.5, 10.5, 18.0)
H_LAB = ("3-6h", "6-9h", "9-12h", "12-24h")
THRESH = 0.10

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


def get_hourly(sid3, d0, d1):
    u = ("https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
         f"station={sid3}&data=tmpf&year1={d0.year}&month1={d0.month}&day1={d0.day}"
         f"&year2={d1.year}&month2={d1.month}&day2={d1.day}"
         "&tz=Etc/UTC&format=onlycomma&missing=M&trace=T"
         "&report_type=3&report_type=4")
    raw = fetch(u, timeout=120)
    if not raw:
        return []
    out = []
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


def px_at(token, ts, cache):
    key = (token, ts // 1800)
    if key not in cache:
        raw = fetch(f"https://clob.polymarket.com/prices-history?market={token}"
                    f"&startTs={ts-2700}&endTs={ts+2700}&fidelity=1")
        try:
            cache[key] = json.loads(raw).get("history", []) if raw else []
        except Exception:
            cache[key] = []
        time.sleep(0.15)
    h = cache[key]
    if not h:
        return None
    b = min(h, key=lambda x: abs(x["t"] - ts))
    return float(b["p"]) if abs(b["t"] - ts) <= 600 else None


def cluster_stats(bets):
    """bets: list of (ev, fam). Returns (n, meanEV, clustered SE, n_fams, WR)."""
    if not bets:
        return 0, 0.0, 0.0, 0, 0.0
    ev = mean(b[0] for b in bets)
    by_fam = defaultdict(list)
    for e, f in bets:
        by_fam[f].append(e)
    fmeans = [mean(v) for v in by_fam.values()]
    se = (pstdev(fmeans) / len(fmeans) ** 0.5) if len(fmeans) > 1 else 0.0
    wr = sum(1 for e, _ in bets if e > 0) / len(bets)
    return len(bets), ev, se, len(fmeans), wr


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
            fmap[(f["sid"], f["td"])].append((ft, f["mem"]))
    for k in fmap:
        fmap[k].sort()

    hourly = {}
    for sid3 in sorted({CITY[c].station_id[1:] for (c, _, _) in fams}):
        hourly[sid3] = get_hourly(sid3, d0, d1)
        print(f"  hourly METAR {sid3}: {len(hourly[sid3])} obs", flush=True)
        time.sleep(0.3)

    print(f"family-days {d0}..{d1}: {len(fams)} | DB forecast rows: "
          f"{sum(len(v) for v in fmap.values())}", flush=True)

    bets = defaultdict(list)        # h_label -> [(ev, famkey)]
    sens = defaultdict(list)        # (9-12h, thresh) -> [(ev, famkey)]
    skip = defaultdict(int)
    pcache = {}
    fam_used = 0
    for (city, mon, day), buckets in sorted(fams.items()):
        if fam_used >= MAX_FAM:
            break
        st = CITY[city]
        tz = ZoneInfo(st.timezone)
        ld = datetime(2026, mon, day).date()
        midnight = datetime(2026, mon, day, tzinfo=tz) + timedelta(days=1)
        flist = fmap.get((st.station_id, ld.isoformat()), [])
        if not flist:
            skip["no_db_forecast"] += 1
            continue
        obs_day = [(t, v) for t, v in hourly[st.station_id[1:]]
                   if t.astimezone(tz).date() == ld]
        used_this_fam = False
        for hmid, hlab in zip(H_MID, H_LAB):
            T = midnight - timedelta(hours=hmid)
            Tts = int(T.timestamp())
            prior = [(ft, mem) for ft, mem in flist if ft <= T]
            if not prior or (T - prior[-1][0]) > timedelta(hours=24):
                skip[f"stale_fc_{hlab}"] += 1
                continue
            mem = prior[-1][1]
            past = [v for t, v in obs_day if t <= T]
            runmax = max(past) if past else None
            eff = [max(m, runmax) if runmax is not None else m for m in mem]
            for b in buckets:
                pm = sum(1 for m in eff if b["lo"] - 0.5 <= m < b["hi"] + 0.5) / len(eff)
                pc = px_at(b["token"], Tts, pcache)
                if pc is None or not (0.03 < pc < 0.97):
                    continue
                y = 1.0 if b["won"] else 0.0
                dis = pm - pc
                ev_yes = y - pc
                fam_key = (city, mon, day)
                if abs(dis) >= THRESH:
                    ev = ev_yes if dis > 0 else -ev_yes
                    bets[hlab].append((ev, fam_key))
                    used_this_fam = True
                if hlab == "9-12h":
                    for th in (0.05, 0.10, 0.15):
                        if abs(dis) >= th:
                            sens[th].append((ev_yes if dis > 0 else -ev_yes, fam_key))
        if used_this_fam:
            fam_used += 1
            if fam_used % 50 == 0:
                print(f"  ...{fam_used} family-days with bets", flush=True)

    print(f"\nfamily-days with >=1 bet: {fam_used} | skips: {dict(skip)}")
    print(f"\nBET-THE-DISAGREEMENT (|P_model - price| >= {THRESH:.2f}), "
          "raw-ensemble+runmax floor, EV/$1 at CLOB minute price:")
    print("  h-to-res | n bets | meanEV  | clustered SE | ~sigma | fams | WR")
    for hlab in H_LAB:
        n, ev, se, nf, wr = cluster_stats(bets[hlab])
        sig = (ev / se) if se > 0 else float("nan")
        print(f"  {hlab:<8} | {n:6d} | {ev:+.3f}  |    {se:.3f}     | "
              f"{sig:5.1f}  | {nf:4d} | {wr:.0%}")

    print("\n9-12h cell, threshold sensitivity:")
    for th in (0.05, 0.10, 0.15):
        n, ev, se, nf, wr = cluster_stats(sens[th])
        sig = (ev / se) if se > 0 else float("nan")
        print(f"  |dis|>={th:.2f}: n={n} meanEV {ev:+.3f} cSE {se:.3f} "
              f"(~{sig:.1f} sigma) fams={nf} WR {wr:.0%}")
    print("\nConventions: T = local-midnight-EOD minus h; latest ensemble <=24h old;")
    print("members floored at hourly-METAR runmax(T); mid-NOT-executable caveat applies")
    print("(CLOB minute price is a trade/quote mark, not a fillable ask).")


main()
