#!/usr/bin/env python3
"""REWARD-REALITY REFUTATION PASS — READ-ONLY, PUBLIC API, NO KEYS, NEVER TRADES.

Adversarial check on survivor_qualify.json's $/day column. Three questions:

Q1. CAPTURE FRACTION. Per contract, what fraction of that contract's OWN pool does the model
    claim we take? Canon §M7d gives the only receipt-calibrated anchor we have (KXAAAGASD).
    A challenger whose value comes from claiming a materially larger fraction than gas is
    making a strictly stronger claim than the one measurement that has ever been checked
    against money.

Q2. R2 IS A THRESHOLD, NOT A SCALE. §M7d says the model over-predicts 2-6x. Applying that as a
    linear haircut to a $/day column is wrong: the rulebook floors the WHOLE-PERIOD payout at
    $1.00, pays zero below. Re-apply R2 AFTER the haircut and see what survives.

Q3. PER-PERIOD, NOT PER-DAY. Recover each program's Time Period length so Q2 can be done on the
    quantity the rulebook actually thresholds.

Writes reward_reality_refute.json. Creates no orders, touches no live config.
"""
import json
import os
import sys
from collections import defaultdict

import kalshi_horizon_census as C

HERE = os.path.dirname(os.path.abspath(__file__))
QUAL = os.path.join(HERE, "survivor_qualify.json")
OUT = os.path.join(HERE, "reward_reality_refute.json")

# canon §M7d receipts, KXAAAGASD-26JUL23, model-vs-receipt on the SAME four contracts
M7D_PRED = [22.10, 16.81, 9.43, 2.54]
M7D_RECEIPT = [3.75, 1.75, 2.57, 2.02]


def main():
    q = json.load(open(QUAL))
    progs = C.fetch_programs()
    P = {}
    for p in progs:
        if (p.get("incentive_type") or "liquidity") != "liquidity":
            continue
        t = p.get("market_ticker")
        d = C.days_of(p)
        if not t or not d:
            continue
        P[t] = {"days": d, "pool": (p.get("period_reward") or 0) / 10000.0}

    print(f"programs fetched {len(P)}")
    print(f"\n--- §M7d calibration (n=4, KXAAAGASD-26JUL23, the ONLY receipt-checked series)")
    for a, b in zip(M7D_PRED, M7D_RECEIPT):
        print(f"    model ${a:6.2f}/period  receipt ${b:5.2f}  over-pred {a/b:5.2f}x")
    print(f"    model spread hi/lo {max(M7D_PRED)/min(M7D_PRED):.2f}x   "
          f"receipt spread hi/lo {max(M7D_RECEIPT)/min(M7D_RECEIPT):.2f}x")
    print(f"    model total ${sum(M7D_PRED):.2f}  receipt total ${sum(M7D_RECEIPT):.2f}  "
          f"= {sum(M7D_PRED)/sum(M7D_RECEIPT):.2f}x")

    rows = []
    for r in q["rows"]:
        if not r["top_contracts"]:
            continue
        cts = []
        for t in r["top_contracts"]:
            pr = P.get(t["ticker"])
            if not pr:
                cts.append({"ticker": t["ticker"], "missing_program": True})
                continue
            pool_day = pr["pool"] / pr["days"]
            cap_day = t["cap_day"]
            frac = cap_day / pool_day if pool_day > 0 else None
            per_period = cap_day * pr["days"]
            cts.append({
                "ticker": t["ticker"], "days": pr["days"], "pool": pr["pool"],
                "pool_day": pool_day, "cap_day": cap_day,
                "capture_frac": frac, "model_per_period": per_period,
                "adm_pct": t["adm_pct"],
                # R2 re-applied AFTER haircut, per Time Period
                "survives_r2_at_2x": (per_period / 2.0) >= 1.0,
                "survives_r2_at_5x": (per_period / 5.04) >= 1.0,
                "survives_r2_at_6x": (per_period / 6.0) >= 1.0,
                "paid_day_at_5x": (per_period / 5.04) / pr["days"]
                if (per_period / 5.04) >= 1.0 else 0.0,
            })
        good = [c for c in cts if not c.get("missing_program")]
        rows.append({
            "series": r["series"], "ours": r["ours"],
            "model_day": r["cap_day_admitted_per_instant"],
            "n_contracts_sampled": r["programs"],
            "n_admitted_contracts": len(r["top_contracts"]),
            "two_sided_pct": r["two_sided_pct"], "admit_pct": r["admit_pct"],
            "max_capture_frac": max((c["capture_frac"] for c in good
                                     if c["capture_frac"] is not None), default=None),
            "day_after_5x_with_r2": sum(c["paid_day_at_5x"] for c in good),
            "contracts": cts,
        })
    rows.sort(key=lambda x: -x["model_day"])

    print(f"\n{'series':26s} {'mdl$/d':>7} {'top10$/d':>8} {'5x+R2':>7} "
          f"{'maxCapFrac':>10} {'nAdmCt':>6} {'nCt':>4}")
    for r in rows:
        top = sum(c["cap_day"] for c in r["contracts"] if not c.get("missing_program"))
        mf = f"{100*r['max_capture_frac']:9.1f}%" if r["max_capture_frac"] is not None else "        -"
        print(f"{r['series']:26s} {r['model_day']:7.2f} {top:8.2f} "
              f"{r['day_after_5x_with_r2']:7.2f} {mf} "
              f"{r['n_admitted_contracts']:6d} {r['n_contracts_sampled']:4d}")

    json.dump({"m7d_pred": M7D_PRED, "m7d_receipt": M7D_RECEIPT, "rows": rows},
              open(OUT, "w"), indent=1)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    sys.exit(main())
