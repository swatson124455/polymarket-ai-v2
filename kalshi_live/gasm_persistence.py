#!/usr/bin/env python3
"""Two-sided PERSISTENCE sweep — READ-ONLY, public API, no keys, never trades.

One instant is not persistence (canon §M6: allowlist two-sidedness fell 79% -> 46%
inside one hour). Re-sweeps every program in a series N times and reports, per
contract, the FRACTION of sweeps whose BOOK reached Target Size on each side (R3),
plus the mean modelled $/day capture across sweeps.

NO empty-book pre-filter (canon §M6b): an empty side is a two-sided FAILURE, counted.
"""
import importlib.util
import json
import os
import sys
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
PUB = "https://api.elections.kalshi.com/trade-api/v2"
JOIN_SIZE, MAX_MARKET, MIN_PAYOUT, TICK = 20.0, 15.0, 1.00, 0.01
_last = [0.0]
spec = importlib.util.spec_from_file_location("_rec", os.path.join(HERE, "..", "scripts", "maker_kalshi_recorder.py"))
REC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(REC)


def get(p):
    w = 0.32 - (time.time() - _last[0])
    if w > 0:
        time.sleep(w)
    r = urllib.request.Request(PUB + p, headers={"User-Agent": "gasm-diligence/1.0 (read-only)"})
    d = json.loads(urllib.request.urlopen(r, timeout=25).read())
    _last[0] = time.time()
    return d


def levels(raw):
    return [(float(a), float(b)) for a, b in (raw or []) if float(b) > 0]


def days_of(p):
    a = datetime.fromisoformat(p["start_date"].replace("Z", "+00:00"))
    b = datetime.fromisoformat(p["end_date"].replace("Z", "+00:00"))
    return (b - a).total_seconds() / 86400.0


def sweep(ps):
    res = {}
    for p in ps:
        t = p["market_ticker"]
        try:
            ob = get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
        except Exception:
            continue
        yl, nl = levels(ob.get("yes_dollars")), levels(ob.get("no_dollars"))
        tgt = float(p["target_size_fp"])
        df = float(p["discount_factor_bps"]) / 10000.0
        pool, d = p["period_reward"] / 10000.0, days_of(p)
        two = bool(yl and nl
                   and REC.qualifying_walk(yl, tgt)[0] is not None
                   and REC.qualifying_walk(nl, tgt)[0] is not None)
        cap = 0.0
        if two:
            by, bn = max(p_ for p_, _ in yl), max(p_ for p_, _ in nl)
            ys = REC.side_share(yl, [(by, min(JOIN_SIZE, 7.5 / by))], tgt, df, TICK)[0]
            ns = REC.side_share(nl, [(bn, min(JOIN_SIZE, 7.5 / bn))], tgt, df, TICK)[0]
            pay = pool * (ys + ns) / 2.0
            cap = pay / d if pay >= MIN_PAYOUT else 0.0
        res[t] = (two, cap, pool / d)
    return res


def main(series, n_sweeps, gap_s):
    progs, cur = [], ""
    for _ in range(10):
        d = get("/incentive_programs?status=active&limit=1000" + (f"&cursor={cur}" if cur else ""))
        progs += d.get("incentive_programs") or []
        cur = d.get("next_cursor") or ""
        if not cur:
            break
    ps = [p for p in progs if (p.get("market_ticker") or "").startswith(series + "-")
          and p.get("target_size_fp") and p.get("discount_factor_bps") and days_of(p) > 0]
    acc = defaultdict(lambda: {"n": 0, "two": 0, "cap": 0.0, "poolday": 0.0})
    stamps = []
    for i in range(n_sweeps):
        t0 = datetime.now(timezone.utc)
        r = sweep(ps)
        stamps.append(t0.strftime("%H:%M:%SZ"))
        tw = sum(1 for v in r.values() if v[0])
        cd = sum(v[1] for v in r.values())
        print(f"  sweep {i+1}/{n_sweeps} {t0.strftime('%H:%M:%SZ')}  n={len(r)}  "
              f"two-sided {tw}/{len(r)} = {100.0*tw/max(len(r),1):.1f}%  cap ${cd:.3f}/day")
        for t, (two, cap, pd) in r.items():
            a = acc[t]
            a["n"] += 1
            a["two"] += 1 if two else 0
            a["cap"] += cap
            a["poolday"] = pd
        if i < n_sweeps - 1:
            time.sleep(max(0, gap_s))
    rows = [{"t": t, "sweeps": a["n"], "two_frac": a["two"] / a["n"],
             "mean_cap_day": a["cap"] / a["n"], "pool_day": a["poolday"]}
            for t, a in acc.items()]
    rows.sort(key=lambda r: -r["mean_cap_day"])
    print(f"\n== {series} PERSISTENCE  sweeps at {stamps}  contracts={len(rows)}")
    always = sum(1 for r in rows if r["two_frac"] == 1.0)
    never = sum(1 for r in rows if r["two_frac"] == 0.0)
    print(f"   ALWAYS two-sided {always}/{len(rows)} = {100.0*always/len(rows):.1f}%   "
          f"NEVER {never}/{len(rows)} = {100.0*never/len(rows):.1f}%   "
          f"mean two-sided rate over all contract-sweeps = "
          f"{100.0*sum(r['two_frac'] for r in rows)/len(rows):.1f}%")
    tot = sum(r["mean_cap_day"] for r in rows)
    print(f"   series mean modelled capture ${tot:.3f}/day of ${sum(r['pool_day'] for r in rows):.2f}/day pool "
          f"({100.0*tot/sum(r['pool_day'] for r in rows):.2f}%)  per-contract ${tot/len(rows):.4f}/day")
    for k in (1, 3, 5, 6, 7, 10):
        s = sum(r["mean_cap_day"] for r in rows[:k])
        print(f"   top-K={k:2d}: ${s:6.3f}/day on ~${15*k:4.0f} capital = {100*s/(15*k):5.2f} $/day per $100")
    print("   top 8:")
    for r in rows[:8]:
        print(f"     {r['t']:28s} 2sided {r['two_frac']*100:5.1f}%  ${r['mean_cap_day']:6.3f}/d")
    json.dump(rows, open(os.path.join(HERE, f"gasm_persistence_{series}.json"), "w"), indent=1)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), float(sys.argv[3]))
