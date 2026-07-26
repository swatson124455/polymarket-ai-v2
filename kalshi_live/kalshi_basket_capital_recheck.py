#!/usr/bin/env python3
"""BASKET CAPITAL RE-CHECK — no API calls, no orders. Pure re-analysis of survivor_qualify.json.

THE CLAIM UNDER TEST (expansion proposal §2a): "capital does NOT bind at the live gate — the
greedy gas-only basket commits $47.96 of $85, so survivors are ADDITIVE, not displacing."

THE DEFECT: kalshi_survivor_basket.py:greedy() packs the $85 using ADMISSION-WEIGHTED capital

    "exp_capital": c["capital"] * c["adm_pct"] / 100.0        (basket:54)
    if used + c["exp_capital"] > MAX_TOTAL_CAPITAL: continue   (basket:69)

Weighting the REWARD by admission probability is defensible (expected earnings over the window).
Weighting the CAPITAL is not: MAX_TOTAL_CAPITAL is an INSTANTANEOUS hard cap enforced per cycle
(maker_kalshi_quoter.py:1274 `if not reducing and committed + cost > MAX_TOTAL_CAPITAL`), and on
the cycles when a contract IS admitted it consumes 100% of its capital, not adm% of it. So the
greedy over-packs by roughly 1/adm.

Two extra reasons the expectation framing does not rescue it:
  * admissions are NOT independent across contracts — the binding gate is MIN_DEPTH_SYM on the
    book, and book thinness is common-mode across a series and across the venue's quiet hours,
    so the "sometimes A is in, sometimes B" smoothing is exactly what does not happen;
  * `committed` in the quoter also includes GROSS held inventory (:1259), which is not
    admission-weighted at all.

This recomputes each basket at UNWEIGHTED capital — what the cap actually sees when every
picked contract is simultaneously admitted — and reports the over-subscription.

DOES NOT COVER: fill rate, queue position, adverse selection, settlement toxicity. Capture is
the reward-side upper bound (canon §M7d, over-predicts ~2-6x); use ratios, not absolute $/day.
"""
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "survivor_qualify.json")
OUT = os.path.join(HERE, "basket_capital_recheck.json")
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
                        "exp_cap_day": c["cap_day"] * w, "exp_capital": c["capital"] * w,
                        "raw_capital": c["capital"], "eff": c["cap_day"] / c["capital"],
                        "adm_pct": c["adm_pct"]})
    return out


def greedy(cs, weighted=True):
    cs = sorted(cs, key=lambda c: -c["eff"])
    used, per_s, picked = 0.0, defaultdict(int), []
    key = "exp_capital" if weighted else "raw_capital"
    for c in cs:
        if per_s[c["series"]] >= PER_SERIES_CAP:
            continue
        if used + c[key] > MAX_TOTAL_CAPITAL:
            continue
        picked.append(c)
        per_s[c["series"]] += 1
        used += c[key]
    return picked, used


def main():
    d = json.load(open(SRC))
    safe = {r["series"] for r in d["rows"]
            if r["structure_verdict"] in ("SAFE-LADDER", "SAFE-ABSTAIN")
            and r["maker_fee"] == "FREE"}
    out = {}
    print(f"source {d['generated_utc']}  instants={d['instants']}  cap=${MAX_TOTAL_CAPITAL:.0f}")
    print(f"\n{'basket':34s} {'K':>3} {'wtdCap$':>8} {'RAWCap$':>8} {'oversub':>8}  {'$/day':>7}")
    for name, allow in (("A. gas only", set(BENCH)),
                        ("B. gas + all structurally-safe", safe),
                        ("C. survivors without gas", safe - set(BENCH))):
        p, u = greedy(contracts(d, allow), weighted=True)
        raw = sum(c["raw_capital"] for c in p)
        tot = sum(c["exp_cap_day"] for c in p)
        print(f"  {name:32s} {len(p):3d} {u:8.2f} {raw:8.2f} {raw/MAX_TOTAL_CAPITAL:7.2f}x "
              f"{tot:7.2f}")
        out[name] = {"K": len(p), "weighted_capital": u, "raw_capital": raw,
                     "oversub_x": raw / MAX_TOTAL_CAPITAL, "cap_day": tot,
                     "picks": [{"t": c["ticker"], "adm": c["adm_pct"],
                                "raw": c["raw_capital"], "wtd": c["exp_capital"]} for c in p]}

    print("\n  gas-only basket, contract by contract (the $47.96-of-$85 claim):")
    p, u = greedy(contracts(d, set(BENCH)), weighted=True)
    for c in p:
        print(f"    {c['ticker']:34s} adm {c['adm_pct']:5.1f}%  raw ${c['raw_capital']:6.2f}  "
              f"weighted ${c['exp_capital']:6.2f}")
    print(f"    {'TOTAL':34s} {'':12s} raw ${sum(c['raw_capital'] for c in p):6.2f}  "
          f"weighted ${u:6.2f}")

    # headroom claim: does NETFLIX fit in the RAW headroom?
    raw_gas = sum(c["raw_capital"] for c in p)
    print(f"\n  RAW headroom after gas = ${MAX_TOTAL_CAPITAL - raw_gas:+.2f}")
    for r in d["rows"]:
        if r["series"] in BENCH or r["series"] not in safe:
            continue
        cs = contracts(d, {r["series"]})
        rawsum = sum(c["raw_capital"] for c in cs)
        print(f"    {r['series']:26s} raw capital of its admitted contracts ${rawsum:7.2f}  "
              f"-> fits raw headroom: {rawsum <= MAX_TOTAL_CAPITAL - raw_gas}")

    json.dump(out, open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
