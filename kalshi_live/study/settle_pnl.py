#!/usr/bin/env python3
"""Real settlement P&L per contract, computed from fields that actually carry data.

DO NOT use `revenue` / `value` from /portfolio/settlements: both return literal 0 on
every row (the documented Kalshi plain-vs-_dollars/_fp trap -- a script reading them
fabricates zeros). Everything below is derived from:
    market_result, yes_count_fp, no_count_fp,
    yes_total_cost_dollars, no_total_cost_dollars, fee_cost

    payout = (winning side's count) x $1.00
    net    = payout - (yes_cost + no_cost) - fee_cost

This is the ALL-IN realized cost of holding to settlement, which is exactly the term the
30-minute markout in the frozen study cannot see.
"""
import os, sys, json, collections, datetime
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
for ln in open("/opt/pa2-maker-kalshi-live/live.env"):
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1)
        os.environ[k] = v
import maker_kalshi_client as MK
from maker_kalshi_client import KalshiOrderClient

c = KalshiOrderClient(mode="live")
ROOT = MK.API_ROOT
print("READ_AT_UTC", datetime.datetime.now(datetime.timezone.utc).isoformat())

setts = c._get_paginated(f"{ROOT}/portfolio/settlements", "settlements")["settlements"]
print("N_SETTLEMENTS", len(setts))

tot_net = tot_ct = tot_cost = 0.0
by_day = collections.defaultdict(lambda: [0.0, 0.0, 0])   # day -> [net, contracts, n]
paired = unpaired = 0
rows = []
for s in setts:
    yc = float(s.get("yes_count_fp") or 0)
    nc = float(s.get("no_count_fp") or 0)
    ycost = float(s.get("yes_total_cost_dollars") or 0)
    ncost = float(s.get("no_total_cost_dollars") or 0)
    fee = float(s.get("fee_cost") or 0)
    res = s.get("market_result")
    payout = (yc if res == "yes" else nc if res == "no" else 0.0) * 1.0
    net = payout - (ycost + ncost) - fee
    ct = yc + nc
    tot_net += net
    tot_ct += ct
    tot_cost += ycost + ncost
    d = (s.get("settled_time") or "")[:10]
    by_day[d][0] += net
    by_day[d][1] += ct
    by_day[d][2] += 1
    if yc > 0 and nc > 0:
        paired += 1
    else:
        unpaired += 1
    rows.append((d, s.get("ticker"), res, yc, nc, round(ycost + ncost, 2), round(net, 4)))

print("TOTAL_SETTLED_NET_USD", round(tot_net, 4))
print("TOTAL_SETTLED_CONTRACTS", round(tot_ct, 1))
print("TOTAL_SETTLED_COST_USD", round(tot_cost, 4))
print("NET_PER_CONTRACT_USD", round(tot_net / tot_ct, 5) if tot_ct else None)
print("PAIRED_SETTLEMENTS (both sides held)", paired, "UNPAIRED (naked)", unpaired)

print("\nBY SETTLEMENT DAY  (net, contracts, n_markets, net/contract)")
for d in sorted(by_day):
    net, ct, n = by_day[d]
    print(f"  {d}  net ${net:>9.4f}  ct {ct:>7.1f}  n {n:>3}  "
          f"per_ct ${net/ct if ct else 0:>8.5f}")

# split paired vs naked -- the naked tail is the operator's known defect class
pn = pc = nn = nc2 = 0.0
for s in setts:
    yc = float(s.get("yes_count_fp") or 0)
    nc = float(s.get("no_count_fp") or 0)
    ycost = float(s.get("yes_total_cost_dollars") or 0)
    ncost = float(s.get("no_total_cost_dollars") or 0)
    fee = float(s.get("fee_cost") or 0)
    res = s.get("market_result")
    payout = (yc if res == "yes" else nc if res == "no" else 0.0)
    net = payout - (ycost + ncost) - fee
    if yc > 0 and nc > 0:
        pn += net
        pc += yc + nc
    else:
        nn += net
        nc2 += yc + nc
print(f"\nPAIRED  (hedged, both sides): net ${pn:>9.4f} over {pc:>7.1f} ct "
      f"-> ${pn/pc if pc else 0:>8.5f}/ct")
print(f"NAKED   (one side only)     : net ${nn:>9.4f} over {nc2:>7.1f} ct "
      f"-> ${nn/nc2 if nc2 else 0:>8.5f}/ct")

print("\nWORST 8 SETTLEMENTS BY NET")
for r in sorted(rows, key=lambda x: x[6])[:8]:
    print("  ", r)
