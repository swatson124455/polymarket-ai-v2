#!/usr/bin/env python3
"""PLANS CROSS-CHECK — quoting/commit by UTC hour, from the live plan log (READ-ONLY).

Consumes copies of /opt/pa2-maker-kalshi-live/plans-*.jsonl pulled with `ssh sudo cat`.
Never connects to Kalshi, never trades, never writes to the VPS.

THE TEST: the union program calendar (kalshi_coverage_gap.py) predicts WHICH hours a pool
even exists. If the hours the bot actually under-quotes line up with the no-pool hours, the
under-deployment is external (nothing to quote). If they DON'T line up, something in our own
pipeline is throttling us -- and the drop/gate counters in the same row say which stage.

Stage counters, in pipeline order (all per cycle):
  programs_seen           rows returned by /incentive_programs?status=active
  drop_allowlist          series not in KALSHI_SERIES_ALLOW
  drop_late_life          past the late-life entry cutoff (our gate)
  footprint               markets selected to quote
  gated_out / capped_ /
    budget_dropped_       our capital + write-budget gates
  empty_books / unqualifiable / one_sided_markets
                          the BOOK failed, not us (R3 exclusion territory)
  quoted_markets          markets we actually rested a quote on
  committed_usd           capital resting
"""
import argparse
import glob
import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timezone

FIELDS = ["programs_seen", "drop_allowlist", "drop_late_life", "footprint",
          "quoted_markets", "committed_usd", "est_capital_usd", "gated_out",
          "capped_markets", "budget_dropped_markets", "empty_books", "unqualifiable",
          "one_sided_markets", "two_sided_markets", "activate_markets", "creates",
          "naked_held_usd", "held_cost_usd"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+", help="plans-*.jsonl copies")
    ap.add_argument("--since", default=None, help="ISO cutoff, e.g. 2026-07-22T00:00Z")
    a = ap.parse_args()

    rows = []
    for pat in a.paths:
        for p in sorted(glob.glob(pat)):
            with open(p, encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            pass
    def ts(r):
        return datetime.fromisoformat(r["ts"].replace("Z", "+00:00"))
    rows = [r for r in rows if "ts" in r]
    if a.since:
        lo = datetime.fromisoformat(a.since.replace("Z", "+00:00"))
        rows = [r for r in rows if ts(r) >= lo]
    rows.sort(key=ts)
    if not rows:
        print("no rows")
        return 1
    print(f"cycles: {len(rows)}   {ts(rows[0]):%Y-%m-%dT%H:%MZ} -> {ts(rows[-1]):%Y-%m-%dT%H:%MZ}")
    modes = defaultdict(int)
    for r in rows:
        modes[r.get("mode")] += 1
    print(f"modes : {dict(modes)}")

    # ---- by (day, hour) so a day-level outage is not smeared into a diurnal average ----
    print("\n=== BY UTC DAY x HOUR: mean footprint / mean quoted / mean committed_usd ===")
    grid = defaultdict(list)
    for r in rows:
        t = ts(r)
        grid[(t.strftime("%m-%d"), t.hour)].append(r)
    days = sorted({k[0] for k in grid})
    print("hour " + "".join(f"{d:>22}" for d in days))
    print("     " + "".join(f"{'n  fp  qt   $':>22}" for d in days))
    for h in range(24):
        cells = []
        for d in days:
            v = grid.get((d, h))
            if not v:
                cells.append(f"{'-':>22}")
            else:
                fp = st.mean([x.get("footprint", 0) for x in v])
                qt = st.mean([x.get("quoted_markets", 0) for x in v])
                cu = st.mean([x.get("committed_usd", 0) for x in v])
                cells.append(f"{len(v):>4}{fp:>6.1f}{qt:>5.1f}{cu:>7.1f}")
        print(f"{h:02d}Z  " + "".join(cells))

    # ---- pipeline attrition by hour (last day present) ----
    print("\n=== PIPELINE ATTRITION BY UTC HOUR (all cycles in range, mean per cycle) ===")
    byh = defaultdict(list)
    for r in rows:
        byh[ts(r).hour].append(r)
    cols = ["programs_seen", "drop_allowlist", "drop_late_life", "footprint",
            "quoted_markets", "committed_usd", "two_sided_markets", "one_sided_markets",
            "empty_books", "unqualifiable", "gated_out", "capped_markets"]
    print("hr   n " + "".join(f"{c[:9]:>11}" for c in cols))
    for h in range(24):
        v = byh.get(h)
        if not v:
            continue
        print(f"{h:02d}Z{len(v):>4} " + "".join(
            f"{st.mean([x.get(c, 0) or 0 for x in v]):>11.2f}" for c in cols))

    # ---- overall ----
    print("\n=== OVERALL (mean / median / p90 / max per cycle) ===")
    for f in FIELDS:
        v = [x.get(f, 0) or 0 for x in rows if f in x]
        if not v:
            continue
        v2 = sorted(v)
        print(f"  {f:<24} n={len(v):<5} mean={st.mean(v):>9.2f} med={st.median(v):>8.2f} "
              f"p90={v2[int(0.9*(len(v2)-1))]:>9.2f} max={max(v):>9.2f}")

    zero = [r for r in rows if (r.get("footprint", 0) or 0) == 0]
    print(f"\ncycles with footprint==0: {len(zero)} / {len(rows)} = {len(zero)/len(rows):.1%}")
    if zero:
        hrs = defaultdict(int)
        for r in zero:
            hrs[ts(r).strftime("%m-%d %HZ")] += 1
        print("  by day-hour:", dict(sorted(hrs.items())))
    zq = [r for r in rows if (r.get("quoted_markets", 0) or 0) == 0]
    print(f"cycles with quoted_markets==0: {len(zq)} / {len(rows)} = {len(zq)/len(rows):.1%}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
