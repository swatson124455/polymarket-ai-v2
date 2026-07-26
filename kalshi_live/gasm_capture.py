#!/usr/bin/env python3
"""KXAAAGASM capture measurement — READ-ONLY, public API, no keys, never trades.

Same scoring as kalshi_series_scan.py:114 (which is the canon-conformant path),
but over EVERY program in the series, not a 4-market sample, and WITHOUT the
kalshi_concentration_study.py:133 empty-book pre-filter (canon §M6b selection
bias) — an empty side is counted as a two-sided FAILURE, not dropped.

R1 $/day normalisation · R2 $1 per Time Period threshold · R3 two-sided FIRST · R4 scoring.
"""
import importlib.util
import json
import os
import sys
import time
import urllib.request
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
JOIN_SIZE = float(os.environ.get("SCAN_JOIN_SIZE", 20))
MAX_MARKET = float(os.environ.get("SCAN_MAX_MARKET", 15))
MIN_PAYOUT = 1.00
TICK = 0.01
SPACING_S = 0.35
_last = [0.0]

spec = importlib.util.spec_from_file_location("_rec", os.path.join(HERE, "..", "scripts", "maker_kalshi_recorder.py"))
REC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REC)


def get(p):
    w = SPACING_S - (time.time() - _last[0])
    if w > 0:
        time.sleep(w)
    r = urllib.request.Request(PUB + p, headers={"User-Agent": "gasm-diligence/1.0 (read-only)"})
    d = json.loads(urllib.request.urlopen(r, timeout=25).read())
    _last[0] = time.time()
    return d


def levels(raw):
    out = []
    for row in raw or []:
        try:
            p, s = float(row[0]), float(row[1])
        except (TypeError, ValueError, IndexError):
            continue
        if s > 0:
            out.append((p, s))
    return out


def days_of(p):
    a = datetime.fromisoformat(p["start_date"].replace("Z", "+00:00"))
    b = datetime.fromisoformat(p["end_date"].replace("Z", "+00:00"))
    d = (b - a).total_seconds() / 86400.0
    return d if d > 0 else None


def score(yl, nl, target, df, pool, days):
    if not yl or not nl:
        return 0.0, 0.0, False, "EMPTY_SIDE"
    by = max(p for p, _ in yl)
    bn = max(p for p, _ in nl)
    if by <= 0 or bn <= 0:
        return 0.0, 0.0, False, "ZERO_BID"
    half = MAX_MARKET / 2.0
    cy = min(JOIN_SIZE, half / by)
    cn = min(JOIN_SIZE, half / bn)
    ys = REC.side_share(yl, [(by, cy)], target, df, TICK)[0]
    ns = REC.side_share(nl, [(bn, cn)], target, df, TICK)[0]
    two = (REC.qualifying_walk(yl, target)[0] is not None
           and REC.qualifying_walk(nl, target)[0] is not None)
    if not two:
        return 0.0, ys + ns, False, "DEPTH<TARGET"
    pay = pool * (ys + ns) / 2.0
    if pay < MIN_PAYOUT:
        return 0.0, ys + ns, True, "BELOW_$1"
    return pay / days, ys + ns, True, "OK"


def main(series):
    progs, cur = [], ""
    for _ in range(10):
        d = get("/incentive_programs?status=active&limit=1000" + (f"&cursor={cur}" if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur:
            break
    ps = [p for p in progs if (p.get("market_ticker") or "").startswith(series + "-") and days_of(p)]
    print(f"{series}: {len(ps)} active programs  (venue total programs {len(progs)})")
    rows = []
    for p in ps:
        t = p["market_ticker"]
        try:
            ob = get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
        except Exception as e:
            print(f"  {t} ob err {e}")
            continue
        yl, nl = levels(ob.get("yes_dollars")), levels(ob.get("no_dollars"))
        d = days_of(p)
        pool = p["period_reward"] / 10000.0
        tgt = float(p.get("target_size_fp") or 0)
        df = float(p.get("discount_factor_bps") or 0) / 10000.0
        cd, sh, two, why = score(yl, nl, tgt, df, pool, d)
        ydep = sum(s for _, s in yl)
        ndep = sum(s for _, s in nl)
        rows.append({"t": t, "pool": pool, "days": d, "pool_day": pool / d,
                     "cap_day": cd, "share": sh, "two": two, "why": why,
                     "y_levels": len(yl), "n_levels": len(nl),
                     "y_depth": ydep, "n_depth": ndep, "target": tgt})
        print(f"  {t:28s} pool/day ${pool/d:6.2f} win {d*24:7.2f}h  ydep {ydep:8.1f} ndep {ndep:8.1f}  "
              f"share {sh:.5f}  cap ${cd:6.3f}/d  {why}")
    n = len(rows)
    two = sum(1 for r in rows if r["two"])
    tot = sum(r["cap_day"] for r in rows)
    pool_day = sum(r["pool_day"] for r in rows)
    print(f"\n== {series}  n={n}  two-sided {two}/{n} = {100.0*two/max(n,1):.1f}%")
    print(f"   pool  ${pool_day:.2f}/day   modelled capture ${tot:.3f}/day "
          f"({100.0*tot/max(pool_day,1e-9):.2f}% of pool)   per-contract ${tot/max(n,1):.4f}/day")
    from collections import Counter
    print("   reason:", dict(Counter(r["why"] for r in rows)))
    json.dump(rows, open(os.path.join(HERE, f"gasm_capture_{series}.json"), "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "KXAAAGASM")
