#!/usr/bin/env python3
"""DEEP DIVE on the horizon-ratio survivors — READ-ONLY, PUBLIC API, NO KEYS, NEVER TRADES.

`kalshi_horizon_census.py` screens all 162 series at n=5 contracts / ONE instant. That is a
screen, not evidence: an n=5 two-sided rate at a single moment has a +/-20pp granularity and
cannot see the overnight liquidity drought (canon §M6, where allowlist two-sidedness fell to
20.5%).

This re-measures the shortlist properly:
  * FULL CENSUS of every contract in the series (no sampling bias at all)
  * SEVERAL INSTANTS spaced apart, so the two-sided rate is a rate and not a snapshot
  * the same R1/R2/R3/R4 scoring the census uses (imported, not re-implemented)
  * per-contract horizon ratio over the full census

Still cannot see: fill rate, queue position, adverse selection. Reward side only.

Run:  python kalshi_horizon_deepdive.py [instants] [gap_seconds]
"""
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

import kalshi_horizon_census as C

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "horizon_deepdive.json")
CENSUS = os.path.join(HERE, "horizon_census.json")
RATIO_CUT = float(os.environ.get("DEEP_RATIO_CUT", 2.0))


def shortlist():
    d = json.load(open(CENSUS))
    ss = [r["series"] for r in d["rows"]
          if r["median_ratio"] is not None and r["median_ratio"] <= RATIO_CUT]
    return ss


def main(instants=3, gap=240):
    series = shortlist()
    progs = C.fetch_programs()
    by_series = defaultdict(list)
    for p in progs:
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        if not C.days_of(p) or not p.get("market_ticker"):
            continue
        by_series[p["market_ticker"].split("-")[0]].append(p)

    sel = {s: by_series.get(s, []) for s in series}
    tickers = [p["market_ticker"] for ps in sel.values() for p in ps]
    print(f"deep dive: {len(sel)} series / {len(tickers)} contracts "
          f"/ {instants} instants @ {gap}s apart")
    meta = C.fetch_markets_batch(tickers)
    print(f"metadata {len(meta)}/{len(tickers)}")

    acc = {s: {"two": 0, "n": 0, "cap": [], "empty_side": 0, "thin_side": 0}
           for s in sel}
    ratios = defaultdict(list)
    now0 = datetime.now(timezone.utc)
    for p_ in [p for ps in sel.values() for p in ps]:
        m = meta.get(p_["market_ticker"])
        if not m:
            continue
        try:
            num = (C.parse_iso(m["close_time"]) - now0).total_seconds()
            den = (C.parse_iso(p_["end_date"]) - now0).total_seconds()
            if den > 0 and num > 0:
                ratios[p_["market_ticker"].split("-")[0]].append(num / den)
        except Exception:
            pass

    for k in range(instants):
        t0 = time.time()
        print(f"\n--- instant {k+1}/{instants} @ "
              f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
        for s, ps in sel.items():
            for p in ps:
                t = p["market_ticker"]
                try:
                    ob = C.get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
                except Exception:
                    continue
                yl = C.levels(ob.get("yes_dollars"))
                nl = C.levels(ob.get("no_dollars"))
                tgt = float(p.get("target_size_fp") or 0)
                df = float(p.get("discount_factor_bps") or 0) / 10000.0
                if tgt <= 0 or df <= 0:
                    continue
                pool = (p.get("period_reward") or 0) / 10000.0
                cd, sh, two = C.score(yl, nl, tgt, df, pool, C.days_of(p))
                a = acc[s]
                a["n"] += 1
                a["two"] += 1 if two else 0
                a["cap"].append(cd)
                if not yl or not nl:
                    a["empty_side"] += 1
                elif not two:
                    a["thin_side"] += 1
        el = time.time() - t0
        print(f"    instant took {el:.0f}s")
        if k < instants - 1 and gap > el:
            time.sleep(gap - el)

    rows = []
    cen = {r["series"]: r for r in json.load(open(CENSUS))["rows"]}
    for s, a in acc.items():
        if not a["n"]:
            continue
        rr = ratios.get(s, [])
        c = cen.get(s, {})
        rows.append({
            "series": s,
            "contracts": len(sel[s]),
            "obs": a["n"], "instants": instants,
            "two_sided_pct": 100.0 * a["two"] / a["n"],
            "empty_side_pct": 100.0 * a["empty_side"] / a["n"],
            "thin_side_pct": 100.0 * a["thin_side"] / a["n"],
            "cap_day_per_instant": sum(a["cap"]) / instants,
            "median_ratio_census_full": statistics.median(rr) if rr else None,
            "ratio_n": len(rr),
            "ratio_min": min(rr) if rr else None,
            "ratio_max": max(rr) if rr else None,
            "pool_day": c.get("pool_day"),
            "maker_fee": c.get("maker_fee"),
            "structure": c.get("structure"),
            "mutually_exclusive": c.get("mutually_exclusive"),
            "category": c.get("category"),
            "hours_utc": c.get("hours_utc"),
            "ours": c.get("ours"),
        })
    rows.sort(key=lambda r: (-(r["two_sided_pct"]), -(r["pool_day"] or 0)))
    json.dump({"generated_utc": now0.isoformat(), "instants": instants,
               "gap_s": gap, "ratio_cut": RATIO_CUT, "rows": rows},
              open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")
    print(f"\n{'series':28s} {'ctr':>4} {'ratio':>6} {'2sided%':>8} {'empty%':>7} "
          f"{'$pool/d':>9} {'$cap/d':>8}  fee     structure")
    for r in rows:
        mr = f"{r['median_ratio_census_full']:6.2f}" if r["median_ratio_census_full"] else "   n/a"
        print(f"{r['series']:28s} {r['contracts']:>4} {mr} {r['two_sided_pct']:>7.1f}% "
              f"{r['empty_side_pct']:>6.1f}% {r['pool_day'] or 0:>9,.0f} "
              f"{r['cap_day_per_instant']:>8.2f}  {r['maker_fee']:7s} {r['structure']}")
    return rows


if __name__ == "__main__":
    a = [x for x in sys.argv[1:] if x.isdigit()]
    main(int(a[0]) if a else 3, int(a[1]) if len(a) > 1 else 240)
