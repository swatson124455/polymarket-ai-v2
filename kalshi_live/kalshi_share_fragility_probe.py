#!/usr/bin/env python3
"""SHARE-FRAGILITY PROBE — READ-ONLY, PUBLIC API, NO KEYS, NEVER TRADES.

survivor_qualify.json's $/day is `pool * (yes_share + no_share) / 2`, with our 20 ct dropped
into a STATIC book. Canon §M7d attributes most of the model's 2-6x over-prediction to one
mechanism it cannot see: "competitors requote and dilute our share continuously."

That mechanism is not scale-free. With DF = 0.50 the reference level dominates the score, so
dilution sensitivity is set by HOW MUCH SIZE IS ALREADY AT THE REFERENCE. If we are 16 ct of a
235 ct reference (gas), one competitor barely moves us. If we are 15 ct of a 6 ct reference
(challenger), one competitor erases us.

This measures, per contract, at the deployed shape:
  ref_size   size resting at the reference price, EXCLUDING ours
  base       modelled payout fraction (what survivor_qualify scores)
  +100@ref   fraction after ONE competitor posts 100 ct at the same reference price
  +100@ref+1 fraction after ONE competitor improves by a single tick with 100 ct
  keep%      the worse of the two, as a percent of base   <- the fragility number

No orders, no auth, no live config. Public /markets/{t}/orderbook only.
"""
import json
import os
import sys

import kalshi_horizon_census as C

REC = C.REC
HERE = os.path.dirname(os.path.abspath(__file__))
QUAL = os.path.join(HERE, "survivor_qualify.json")
OUT = os.path.join(HERE, "share_fragility.json")
TARGET, DF, TICK = 1000.0, 0.5, 0.01
JOIN, HALF = 20.0, 7.5
COMP = 100.0


def frac(yl, nl):
    by = max(p for p, _ in yl)
    bn = max(p for p, _ in nl)
    cy, cn = min(JOIN, HALF / by), min(JOIN, HALF / bn)
    ys = REC.side_share(yl, [(by, cy)], TARGET, DF, TICK)[0]
    ns = REC.side_share(nl, [(bn, cn)], TARGET, DF, TICK)[0]
    return (ys + ns) / 2.0


def main():
    q = json.load(open(QUAL))
    rows = []
    print(f"{'ticker':36s} {'refY':>7} {'refN':>7} {'base':>6} {'+100@ref':>8} "
          f"{'+100@r+1':>8} {'keep%':>6}")
    for r in sorted(q["rows"], key=lambda x: -x["cap_day_admitted_per_instant"]):
        if not r["top_contracts"]:
            continue
        for tc in r["top_contracts"][:2]:
            t = tc["ticker"]
            try:
                ob = C.get(f"/markets/{t}/orderbook").get("orderbook_fp") or {}
            except Exception:
                continue
            yl, nl = C.levels(ob.get("yes_dollars")), C.levels(ob.get("no_dollars"))
            if not yl or not nl:
                continue
            by, bn = max(p for p, _ in yl), max(p for p, _ in nl)
            ry = sum(s for p, s in yl if abs(p - by) < 1e-9)
            rn = sum(s for p, s in nl if abs(p - bn) < 1e-9)
            base = frac(yl, nl)
            at_ref = frac(yl + [(by, COMP)], nl + [(bn, COMP)])
            # a competitor improving by one tick moves the reference against us
            iy, inn = min(by + TICK, 0.99), min(bn + TICK, 0.99)
            improved = frac(yl + [(iy, COMP)], nl + [(inn, COMP)]) if (iy + inn) < 1.0 \
                else at_ref
            worst = min(at_ref, improved)
            keep = 100.0 * worst / base if base > 0 else 0.0
            rows.append({"series": r["series"], "ticker": t, "ref_yes": ry, "ref_no": rn,
                         "base": base, "at_ref": at_ref, "improved": improved,
                         "keep_pct": keep, "ours": r["ours"]})
            print(f"{t:36s} {ry:7.1f} {rn:7.1f} {base:6.4f} {at_ref:8.4f} "
                  f"{improved:8.4f} {keep:5.1f}%")
    json.dump({"rows": rows}, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    sys.exit(main())
