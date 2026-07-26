#!/usr/bin/env python3
"""DISPLACEMENT TEST — is any survivor worth a SLOT? READ-ONLY, no API calls, no orders.

WHY THIS EXISTS. Every prior ranking treated a new series as ADDITIVE: "series X pays $N/day, so
admitting it adds $N/day." That is false under the live caps. `kalshi_depth_capacity_study.py`
measured MAX_TOTAL_CAPITAL=85 becoming the binding constraint at K~7 concurrent markets. Once
capital binds, a new series cannot ADD dollars — it can only DISPLACE an existing quote. The
decision question is therefore not "does it pay?" but "does it pay MORE PER DOLLAR than the
marginal quote it would evict?"

Reads `survivor_qualify.json` (per-contract, admitted-only economics at the deployed shape) and
runs a greedy $85 allocation under the live caps:
    MAX_TOTAL_CAPITAL = 85     PER_SERIES_CAP = 10     one quote per contract

Contracts are ranked by capital efficiency ($/day per $ committed), and each contract's numbers
are ADMISSION-WEIGHTED: a contract our selection gate only admits 30% of the time earns 30% of
its conditional capture and ties up 30% of its capital, in expectation over the run window.

CAVEATS, all load-bearing:
  * Capture is the reward-side model only. Canon §M7d: it over-predicts ~2-6x. Use RATIOS between
    rows (same bias both sides), never the absolute $/day.
  * Fill rate, queue position and adverse selection are invisible here. A basket that wins on
    this metric can still lose money on the trading leg — that is exactly what KXTEMP* did
    (canon §M8: 91% of reward income, 100% of the loss).
  * Sample window = the qualify run only. It does NOT cover the overnight drought (canon §M6).
  * The greedy solve ignores per-event correlation: several strikes of one ladder are ONE risk
    (canon §T), so a basket that fills up on one event is more concentrated than K suggests.

Run:  python kalshi_survivor_basket.py
"""
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "survivor_qualify.json")
OUT = os.path.join(HERE, "survivor_basket.json")

MAX_TOTAL_CAPITAL = 85.0
PER_SERIES_CAP = 10
BENCH = ("KXAAAGASD", "KXAAAGASW")


def contracts(d, allow=None):
    out = []
    for r in d["rows"]:
        if allow is not None and r["series"] not in allow:
            continue
        for c in r["top_contracts"]:
            w = c["adm_pct"] / 100.0
            if w <= 0 or c["capital"] <= 0:
                continue
            out.append({"series": r["series"], "ticker": c["ticker"],
                        "exp_cap_day": c["cap_day"] * w,
                        "exp_capital": c["capital"] * w,
                        "eff": c["cap_day"] / c["capital"],
                        "adm_pct": c["adm_pct"],
                        "structure": r["structure_verdict"]})
    return out


def greedy(cs):
    cs = sorted(cs, key=lambda c: -c["eff"])
    used, per_s, picked = 0.0, defaultdict(int), []
    for c in cs:
        if per_s[c["series"]] >= PER_SERIES_CAP:
            continue
        if used + c["exp_capital"] > MAX_TOTAL_CAPITAL:
            continue
        picked.append(c)
        per_s[c["series"]] += 1
        used += c["exp_capital"]
    return picked, used


def show(name, picked, used):
    tot = sum(c["exp_cap_day"] for c in picked)
    by = defaultdict(lambda: [0, 0.0, 0.0])
    for c in picked:
        b = by[c["series"]]
        b[0] += 1
        b[1] += c["exp_cap_day"]
        b[2] += c["exp_capital"]
    mix = "  ".join(f"{s}:{v[0]}(${v[1]:.1f})" for s, v in
                    sorted(by.items(), key=lambda kv: -kv[1][1]))
    print(f"{name:34s} K={len(picked):3d}  ${used:6.2f} committed  "
          f"${tot:7.2f}/day  eff {tot/used if used else 0:5.3f}   {mix}")
    return {"name": name, "K": len(picked), "capital": used, "cap_day": tot,
            "eff": (tot / used) if used else None,
            "mix": {s: {"n": v[0], "cap_day": v[1], "capital": v[2]}
                    for s, v in by.items()}}


def main():
    d = json.load(open(SRC))
    print(f"source {d['generated_utc']}  instants={d['instants']} gap={d['gap_s']}s")
    print(f"shape  {d['shape']}\n")

    safe = {r["series"] for r in d["rows"]
            if r["structure_verdict"] in ("SAFE-LADDER", "SAFE-ABSTAIN")
            and r["maker_fee"] == "FREE"}
    results = []
    p, u = greedy(contracts(d, set(BENCH)))
    results.append(show("A. gas only (today's earner)", p, u))
    p, u = greedy(contracts(d, safe))
    results.append(show("B. gas + ALL structurally-safe", p, u))
    p, u = greedy(contracts(d, safe - set(BENCH)))
    results.append(show("C. survivors WITHOUT gas", p, u))

    base = results[0]["cap_day"]
    print("\n  marginal value of adding ONE survivor to gas:")
    per = []
    for r in d["rows"]:
        s = r["series"]
        if s in BENCH or s not in safe:
            continue
        p, u = greedy(contracts(d, set(BENCH) | {s}))
        tot = sum(c["exp_cap_day"] for c in p)
        took = sum(1 for c in p if c["series"] == s)
        per.append({"series": s, "cap_day": tot, "delta": tot - base,
                    "slots_taken": took, "structure": r["structure_verdict"]})
    per.sort(key=lambda x: -x["delta"])
    for x in per:
        print(f"    {x['series']:26s} basket ${x['cap_day']:7.2f}/day  "
              f"delta {x['delta']:+7.2f}  slots {x['slots_taken']:2d}  {x['structure']}")

    json.dump({"source": d["generated_utc"], "baskets": results, "marginal": per},
              open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
