#!/usr/bin/env python3
"""S231 deep-backtest task 2 — HISTORICAL MAKER-FILL STUDY (read-only, VPS).

Question (spec 0c, answered from history instead of waiting for accrual):
when the deciding METAR print publishes, do hypothetical bids RESTING at
pre-reveal price levels on the WINNER bucket actually get filled?

Method: for each resolved US F-station 'highest temperature' family 03->07,
find t_reveal = publication of the first hourly METAR print whose running
max enters the winning bucket (+PUB_DELAY_MIN). Pull the market's full
public trade-print history (data-api, paginated; prints ARE fills, side =
TAKER side). Convert prints to YES-frame (outcome=No BUY at price q is
downward YES flow at 1-q via merged-book minting). A resting YES bid at
level b counts FILLED if any taker-sell YES-frame print <= b lands in the
reveal window [t_reveal-30m, t_reveal+45m].

REPORTED AS UPPER BOUNDS: queue position is unknowable, our size is not in
the historical book, and self-crossing/wash flow is indistinguishable.
Winner-conditioning is deliberate: the question is capture-when-right; the
edge-when-wrong is the peak-model's problem (task 1). A same-day CONTROL
window (t_reveal-3.5h .. -2.25h) separates reveal repricing from background
churn. 'X or below' winners have no reveal moment and are skipped (counted).

Usage: python3 maker_fill_study.py [days_back=133] [max_markets=2000]
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
from statistics import mean, median
from zoneinfo import ZoneInfo

sys.path.insert(0, "/opt/polymarket-ai-v2-weather")
from bots.weather.engine.base_engine.weather.station_registry import STATION_REGISTRY  # noqa

PUB_DELAY_MIN = 6
LAG_GUARD_DAYS = 3
DAYS_BACK = int(sys.argv[1]) if len(sys.argv) > 1 else 133
MAX_MKTS = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
LEVELS = (0.00, 0.01, 0.02, 0.05)   # bid = p0 - delta
WIN_PRE_S = 30 * 60
WIN_POST_S = 45 * 60

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


def get_prints(mid):
    """Full print history, YES-frame, deduped. Returns [(ts, yes_px, yes_sell, size)]."""
    seen, out, off = set(), [], 0
    while off <= 5000:
        raw = fetch(f"https://data-api.polymarket.com/trades?market={mid}"
                    f"&limit=500&offset={off}")
        try:
            page = json.loads(raw) if raw else []
        except Exception:
            page = []
        if not isinstance(page, list) or not page:
            break
        for t in page:
            key = (t.get("transactionHash"), t.get("asset"), t.get("timestamp"),
                   t.get("price"), t.get("size"))
            if key in seen:
                continue
            seen.add(key)
            try:
                px, sz, ts = float(t["price"]), float(t["size"]), int(t["timestamp"])
            except Exception:
                continue
            is_yes = (t.get("outcome") or "").lower() == "yes"
            side = (t.get("side") or "").upper()
            yes_px = px if is_yes else 1.0 - px
            # taker-sell in YES frame = SELL Yes, or BUY No (mint-matched vs YES bids)
            yes_sell = (side == "SELL") if is_yes else (side == "BUY")
            out.append((ts, yes_px, yes_sell, sz))
        if len(page) < 500:
            break
        off += 500
        time.sleep(0.08)
    out.sort()
    time.sleep(0.08)
    return out


def window_fills(prints, t0, t1, levels):
    """Per level: (any_fill, shares_filled) from taker-sell YES prints <= b in [t0,t1]."""
    res = {}
    for b in levels:
        sh = sum(sz for ts, px, sell, sz in prints
                 if sell and t0 <= ts <= t1 and px <= b + 1e-9)
        res[b] = sh
    return res


def main():
    now = datetime.now(timezone.utc)
    d1 = (now - timedelta(days=LAG_GUARD_DAYS)).date()
    d0 = (now - timedelta(days=LAG_GUARD_DAYS + DAYS_BACK)).date()

    rows = json.loads(psql("""SELECT json_agg(row_to_json(t)) FROM (
      SELECT id, question, resolution FROM markets
      WHERE category='weather' AND resolution IN ('YES','NO')
        AND question LIKE 'Will the highest temperature%%') t""") or "[]")
    winners = []
    for r in rows or []:
        if r["resolution"] != "YES":
            continue
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
        winners.append(dict(mid=r["id"], city=city, ld=ld, lo=lo, hi=hi))
    print(f"resolved US-F winner buckets {d0}..{d1}: {len(winners)}", flush=True)

    hourly = {}
    for sid3 in sorted({CITY[w["city"]].station_id[1:] for w in winners}):
        hourly[sid3] = get_hourly(sid3, d0, d1)
        print(f"  hourly METAR {sid3}: {len(hourly[sid3])} obs", flush=True)
        time.sleep(0.3)

    skipped = defaultdict(int)
    results = []   # dict(month, p0, fills={b: sh}, post={b: sh}, ctrl={b: sh}, jump)
    done = 0
    for w in sorted(winners, key=lambda x: (x["ld"], x["city"])):
        if done >= MAX_MKTS:
            break
        st = CITY[w["city"]]
        tz = ZoneInfo(st.timezone)
        obs = [(t, v) for t, v in hourly[st.station_id[1:]]
               if t.astimezone(tz).date() == w["ld"]]
        if len(obs) < 8:
            skipped["no_metar_day"] += 1
            continue
        if w["lo"] < -1e8:
            skipped["below_winner_no_reveal"] += 1
            continue
        rm, t_ob = None, None
        for t, v in obs:
            if rm is None or v > rm:
                rm = v
                if w["lo"] - 0.5 <= rm < w["hi"] + 0.5:
                    t_ob = t
                    break
        if t_ob is None:
            skipped["winner_never_crossed_in_prints"] += 1
            continue
        t_rev = int(t_ob.timestamp()) + PUB_DELAY_MIN * 60
        prints = get_prints(w["mid"])
        if not prints:
            skipped["no_prints"] += 1
            continue
        pre = [(ts, px) for ts, px, _, _ in prints if ts < t_rev - 120]
        if not pre or (t_rev - 120 - pre[-1][0]) > 12 * 3600:
            skipped["no_fresh_pre_price"] += 1
            continue
        p0 = pre[-1][1]
        if not (0.05 < p0 < 0.90):
            skipped["p0_outside_0.05_0.90"] += 1
            continue
        levels = {d: max(0.01, round(p0 - d, 3)) for d in LEVELS}
        fills = window_fills(prints, t_rev - WIN_PRE_S, t_rev + WIN_POST_S,
                             levels.values())
        post = window_fills(prints, t_rev, t_rev + WIN_POST_S, levels.values())
        c1 = t_rev - int(3.5 * 3600)
        ctrl = window_fills(prints, c1, c1 + WIN_PRE_S + WIN_POST_S, levels.values())
        after = [px for ts, px, _, _ in prints if t_rev + 600 <= ts <= t_rev + 5400]
        results.append(dict(
            month=w["ld"].month, p0=p0,
            fills={d: fills[levels[d]] for d in LEVELS},
            post={d: post[levels[d]] for d in LEVELS},
            ctrl={d: ctrl[levels[d]] for d in LEVELS},
            jump=(median(after) - p0) if after else None))
        done += 1
        if done % 50 == 0:
            print(f"  ...{done} reveal windows processed", flush=True)

    print(f"\nreveal windows analyzed: {len(results)} | skipped: {dict(skipped)}")
    if not results:
        return
    bym = defaultdict(int)
    for r in results:
        bym[r["month"]] += 1
    print(f"by month: {dict(sorted(bym.items()))}")
    print(f"median p0 (last pre-reveal print): {median(r['p0'] for r in results):.2f}")
    jumps = [r["jump"] for r in results if r["jump"] is not None]
    if jumps:
        print(f"post-reveal repricing (median print +10..90min minus p0): "
              f"{median(jumps):+.2f} (n={len(jumps)})")

    print("\nRESTING-BID FILLS IN REVEAL WINDOW [-30m,+45m] — UPPER BOUNDS")
    print("  level      | any-fill | med shares(filled>0) | >=20sh | POST-only any | control any")
    n = len(results)
    for d in LEVELS:
        f = [r["fills"][d] for r in results]
        po = [r["post"][d] for r in results]
        ct = [r["ctrl"][d] for r in results]
        nz = [x for x in f if x > 0]
        print(f"  p0-{d:.2f}    |   {len(nz)/n:5.0%}  |        {median(nz) if nz else 0:7.1f}       "
              f"| {sum(1 for x in f if x >= 20)/n:5.0%}  |     {sum(1 for x in po if x > 0)/n:5.0%}     "
              f"|   {sum(1 for x in ct if x > 0)/n:5.0%}")
    print("\nCaveats: winner-conditioned; queue position unknowable; historical book")
    print("absent; self-cross/wash flow included -> ALL rates are UPPER bounds.")


main()
