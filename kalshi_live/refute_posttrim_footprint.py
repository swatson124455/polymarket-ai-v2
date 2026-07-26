#!/usr/bin/env python3
"""ADVERSARIAL REFUTATION (read-only, no network): the trim does NOT produce the
'pure top-40' footprint the proposal measured dilution against. It produces a
round-robin over the 6 REMAINING series. Compute THAT footprint in the SAME R3 model
and compare earnable to the current 9-series deployed footprint.

Reads allowlist_density_census.json only (two_detail carries per-contract two_sided).
Replicates allowlist_footprint_replicate.py's algorithm EXACTLY. Never trades."""
import json
import os
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
FOOTPRINT_TOP = 40
PER_SERIES_CAP = 100

d = json.load(open(os.path.join(HERE, "allowlist_density_census.json")))

# rebuild per-contract (series, ticker, usd_day, two_sided) from census two_detail
all_contracts = []
best_ud = {}
for r in d["rows"]:
    s = r["series"]
    for c in r.get("two_detail", []):
        all_contracts.append((s, c["ticker"], c["usd_day"], c["two_sided"]))
        best_ud[s] = max(best_ud.get(s, 0.0), c["usd_day"])


def round_robin(contracts):
    by_series = defaultdict(list)
    for s, t, ud, tw in contracts:
        by_series[s].append((ud, t, tw))
    for s in by_series:
        by_series[s].sort(key=lambda x: (-x[0], x[1]))  # -usd_day, ticker  (deployed)
    order = sorted(by_series, key=lambda s: (-best_ud[s], s))
    picked, ptr, taken = [], {s: 0 for s in by_series}, {s: 0 for s in by_series}
    while len(picked) < FOOTPRINT_TOP:
        progressed = False
        for s in order:
            if len(picked) >= FOOTPRINT_TOP:
                break
            if taken[s] >= PER_SERIES_CAP or ptr[s] >= len(by_series[s]):
                continue
            ud, t, tw = by_series[s][ptr[s]]
            picked.append((s, t, ud, tw))
            ptr[s] += 1
            taken[s] += 1
            progressed = True
        if not progressed:
            break
    return picked


def earnable(sel):
    return sum(ud for s, t, ud, tw in sel if tw)


def summarize(sel, label):
    comp = defaultdict(lambda: [0, 0.0, 0, 0.0])  # slots, nom, two_ct, two_usd
    for s, t, ud, tw in sel:
        comp[s][0] += 1
        comp[s][1] += ud
        if tw:
            comp[s][2] += 1
            comp[s][3] += ud
    nom = sum(v[1] for v in comp.values())
    earn = sum(v[3] for v in comp.values())
    print(f"\n=== {label}: {len(sel)} slots | nominal ${nom:.1f} | EARNABLE ${earn:.1f} ===")
    print(f"  {'series':24s}{'slots':>6}{'earn$/d':>10}{'2s/slots':>10}")
    for s in sorted(comp, key=lambda s: -comp[s][3]):
        c = comp[s]
        print(f"  {s:24s}{c[0]:>6}{c[3]:>10.1f}{str(c[2])+'/'+str(c[0]):>10}")
    return earn


# --- CURRENT: deployed round-robin over all 9 live series ---
cur = round_robin(all_contracts)
cur_earn = summarize(cur, "CURRENT deployed RR (9 live series)")

# --- POST-TRIM: cut TRUMP, B200, H100 -> RR over remaining 6 series ---
CUT = {"KXTRUMPENDORSEMENTS", "KXB200MON", "KXH100MON"}
trimmed_contracts = [(s, t, ud, tw) for (s, t, ud, tw) in all_contracts if s not in CUT]
post = round_robin(trimmed_contracts)
post_earn = summarize(post, "POST-TRIM RR (6 remaining series)")

# --- also the optional 4th cut (CHIP) -> 5 series ---
CUT4 = CUT | {"KXCHIPBURRITO"}
post4 = round_robin([(s, t, ud, tw) for (s, t, ud, tw) in all_contracts if s not in CUT4])
post4_earn = summarize(post4, "POST-TRIM+CHIP RR (5 remaining series)")

print("\n" + "=" * 62)
print(f"CURRENT (9-series) earnable    = ${cur_earn:8.1f}/day  (R3 model, upper bound)")
print(f"POST-TRIM (6-series) earnable  = ${post_earn:8.1f}/day")
print(f"POST-TRIM+CHIP (5) earnable    = ${post4_earn:8.1f}/day")
print(f"\nDELTA of the trim (6-series - current) = ${post_earn - cur_earn:+.1f}/day  (replication-faithful)")
print(f"DELTA of trim+CHIP (5-series - current) = ${post4_earn - cur_earn:+.1f}/day")

# total available contracts
tot = len(all_contracts)
tot_cut = len([1 for s, t, ud, tw in all_contracts if s in CUT])
print(f"\ntotal live contracts across 9 series = {tot}; removed by 3-cut = {tot_cut}; "
      f"remaining = {tot - tot_cut} (vs FOOTPRINT_TOP={FOOTPRINT_TOP})")
