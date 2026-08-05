#!/usr/bin/env python3
"""W3 evidence pass — sweep credit-feedback ranking parameters over the W11 harness.

Grid: bonus (multiplier for series credited to date) x never_paid_mult (multiplier for
series with fills but no credit to date). Zero-fill series are untouched by construction
(the D2 proof criterion). Reports, per parameter pair, the two rank metrics the proof
criteria are stated in:
    median rank of never-paid series   (want: LOW in the list = pushed DOWN numerically UP)
    median rank of payer series        (want: unchanged / near the recorded policy's)
Selection-only replay; fills/queue/payout not backtestable (W11 honest limit).
"""
import json
import os
import statistics as st
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import w11_replay as H


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--caprank", nargs="+", required=True)
    ap.add_argument("--outdir", default="/tmp/w11")
    a = ap.parse_args()
    raw = json.load(open(a.snapshot))

    grid = [(b, m) for b in (1.5, 2.0, 4.0) for m in (0.25, 0.5, 1.0)]
    H.POLICIES = {"recorded": H.policy_recorded}
    for b, m in grid:
        H.POLICIES[f"fb_b{b}_m{m}"] = H.make_policy_credit_feedback(
            bonus=b, never_paid_mult=m)

    stats, never_paid, zero_fill = H.replay(a.caprank, raw["credits"], raw["orders"])
    doc = {"schema": 1, "read_ts": raw["read_ts"], "grid": grid, "days": {}}
    print(f"never_paid n={len(never_paid)}  zero_fill n={len(zero_fill)}")
    print(f"{'policy':18s} {'day':8s} {'np_medrank':>10s} {'payer_medrank':>13s}")
    for name, days in stats.items():
        for day, d in sorted(days.items()):
            npr = st.median(d["np_ranks"]) if d["np_ranks"] else None
            pr = st.median(d["payer_ranks"]) if d["payer_ranks"] else None
            print(f"{name:18s} {day:8s} {str(npr):>10s} {str(pr):>13s}")
            doc["days"].setdefault(day, {})[name] = {
                "np_medrank": npr, "payer_medrank": pr}
    out = os.path.join(a.outdir, "w3_policy_sweep.json")
    json.dump(doc, open(out, "w"), indent=1, sort_keys=True)
    print("wrote", out)


if __name__ == "__main__":
    main()
