#!/usr/bin/env python3
"""Replicate the DEPLOYED round-robin footprint selection (FOOTPRINT_TOP=40,
PER_SERIES_CAP=100) over the SAME post-gate contracts the density census measured, to show
concretely how 40 slots spread across the 9 currently-live series vs a pure-density top-40.
Reads allowlist_density_census.json (no network). Never trades."""
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
FOOTPRINT_TOP = 40
PER_SERIES_CAP = 100

d = json.load(open(os.path.join(HERE, "allowlist_density_census.json")))
# rebuild per-contract (series, usd_day, two_sided) from the census detail
contracts = []
best_ud = {}
for r in d["rows"]:
    s = r["series"]
    for c in r.get("two_detail", []):
        contracts.append((s, c["ticker"], c["usd_day"], c["two_sided"]))
        best_ud[s] = max(best_ud.get(s, 0.0), c["usd_day"])

# ---- deployed round-robin -------------------------------------------------------------
by_series = defaultdict(list)
for s, t, ud, tw in contracts:
    by_series[s].append((ud, t, tw))
for s in by_series:
    by_series[s].sort(key=lambda x: (-x[0], x[1]))          # -usd_day, ticker
series_order = sorted(by_series, key=lambda s: -best_ud[s])  # best-usd_day desc
picked, ptr, taken_ct = [], {s: 0 for s in by_series}, {s: 0 for s in by_series}
while len(picked) < FOOTPRINT_TOP:
    progressed = False
    for s in series_order:
        if len(picked) >= FOOTPRINT_TOP:
            break
        if taken_ct[s] >= PER_SERIES_CAP or ptr[s] >= len(by_series[s]):
            continue
        ud, t, tw = by_series[s][ptr[s]]
        picked.append((s, t, ud, tw))
        ptr[s] += 1
        taken_ct[s] += 1
        progressed = True
    if not progressed:
        break

# ---- pure-density top-40 (counterfactual) --------------------------------------------
pure = sorted(contracts, key=lambda x: -x[2])[:FOOTPRINT_TOP]

def summarize(sel, label):
    comp = defaultdict(lambda: [0, 0.0, 0])   # series -> [count, sum_ud, two_sided_ct]
    for s, t, ud, tw in sel:
        comp[s][0] += 1
        comp[s][1] += ud
        comp[s][2] += 1 if tw else 0
    tot_ud = sum(v[1] for v in comp.values())
    tot_earn = sum(ud for s, t, ud, tw in sel if tw)
    print(f"\n=== {label}: {len(sel)} slots, pool $/day {tot_ud:.1f}, EARNABLE $/day {tot_earn:.1f} ===")
    print(f"  {'series':24s}{'slots':>6}{'pool$/d':>9}{'2s':>5}")
    for s in sorted(comp, key=lambda s: -comp[s][1]):
        c = comp[s]
        print(f"  {s:24s}{c[0]:>6}{c[1]:>9.1f}{c[2]:>4}/{c[0]}")

summarize(picked, "DEPLOYED round-robin footprint (top=40)")
summarize(pure, "PURE-DENSITY top-40 (counterfactual)")

# gas share
gas_rr = sum(1 for s, t, ud, tw in picked if s in ("KXAAAGASD", "KXAAAGASW"))
gas_pure = sum(1 for s, t, ud, tw in pure if s in ("KXAAAGASD", "KXAAAGASW"))
print(f"\ngas slots: round-robin {gas_rr}/40  vs  pure-density {gas_pure}/40")
