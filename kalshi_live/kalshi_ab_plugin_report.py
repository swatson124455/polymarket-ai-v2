#!/usr/bin/env python3
"""A/B READOUT for the reduce-only two-sided plug-in (KALSHI_REDUCE_ONLY_KEEP_BOTH).

READ-ONLY: reads the quoter's own plan rows + the A/B marker written when each arm started.
Never touches the venue, never trades.

Design (operator 2026-07-23): run the plug-in ON for ~3h, then OFF, and compare. The marker
file records both switchover times so the split survives across sessions and restarts.

WHAT THIS CAN AND CANNOT SHOW — read before quoting any number:
  CAN: two-sided coverage (the mechanism — does the plug-in actually keep markets qualifying),
       time spent in reduce-only, risk carried, and quoting footprint. These are MEASURED from
       the bot's own plan rows.
  CANNOT: rewards per arm. Rewards are only observable as balance deltas in QUIET intervals,
       and reduce-only periods are exactly when the bot is filling — so a clean per-arm reward
       figure will usually not exist. Treat "earning-eligible minutes" as the proxy: it is the
       thing the CFTC two-sided rule actually gates, and it is directly measured.
This asymmetry is deliberate and stated because this lane has repeatedly had to retract numbers
that outran their evidence.

Run: python kalshi_ab_plugin_report.py
"""
import glob
import json
import os
import sys
from datetime import datetime

DIR = os.path.dirname(os.path.abspath(__file__))
MARKER = os.path.join(DIR, "ab_plugin_marker.json")


def _f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def load_rows():
    rows = []
    for p in sorted(glob.glob(os.path.join(DIR, "plans-*.jsonl"))):
        for line in open(p):
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if r.get("two_sided_markets") is not None:   # only rows with the new telemetry
                rows.append(r)
    return rows


def summarize(rows, label):
    if not rows:
        print(f"  {label:14} (no cycles yet)")
        return
    n = len(rows)
    ro = sum(1 for r in rows if r.get("breaker_reduce_only"))
    two = sum(int(r.get("two_sided_markets") or 0) for r in rows)
    one = sum(int(r.get("one_sided_markets") or 0) for r in rows)
    quoted = two + one
    # earning-eligible = market-cycles that are two-sided (the CFTC rule gates on exactly this)
    elig = 100.0 * two / quoted if quoted else 0.0
    ro_two = sum(int(r.get("two_sided_markets") or 0) for r in rows if r.get("breaker_reduce_only"))
    ro_one = sum(int(r.get("one_sided_markets") or 0) for r in rows if r.get("breaker_reduce_only"))
    ro_q = ro_two + ro_one
    print(f"  {label:14} cycles={n:4}  reduce-only={100*ro/n:5.1f}%  "
          f"market-cycles={quoted:5}  TWO-SIDED={elig:5.1f}%")
    print(f"  {'':14} during reduce-only: {ro_q:4} market-cycles, "
          f"{(100*ro_two/ro_q if ro_q else 0):5.1f}% two-sided (this is what the plug-in changes)")
    print(f"  {'':14} mean naked ${sum(_f(r.get('naked_held_usd')) for r in rows)/n:6.2f}  "
          f"mean committed ${sum(_f(r.get('committed_usd')) for r in rows)/n:6.2f}  "
          f"ladder_violations={sum(int(r.get('ladder_violation') or 0) for r in rows)}")


def main():
    try:
        mk = json.load(open(MARKER))
    except Exception:
        print("no A/B marker found — was the test window started?")
        return 1
    on_start = mk.get("arm_on_start")
    off_start = mk.get("arm_off_start")
    rows = load_rows()
    on_rows = [r for r in rows if on_start and r["ts"] >= on_start
               and (not off_start or r["ts"] < off_start)]
    off_rows = [r for r in rows if off_start and r["ts"] >= off_start]
    print(f"A/B: reduce-only two-sided plug-in")
    print(f"  ON  window from {on_start}")
    print(f"  OFF window from {off_start or '(not flipped yet)'}")
    print()
    summarize(on_rows, "PLUG-IN ON")
    print()
    summarize(off_rows, "PLUG-IN OFF")
    print()
    print("MEASURED: two-sided coverage / reduce-only time / risk / footprint.")
    print("NOT MEASURED: rewards per arm — reduce-only periods are when the bot is filling, so")
    print("clean quiet intervals (the only window the rewards method can read) usually do not")
    print("exist there. 'TWO-SIDED %' is the honest proxy: it is exactly what the CFTC rule gates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
