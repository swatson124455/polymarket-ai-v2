#!/usr/bin/env python3
"""What is the LIVE bot ACTUALLY resting on gas right now? (authed GET-only, read-only)
Settles the proposal's 'live-faithful' haircut claim: does the live quoter skip one-sided
books and rest the two-sided gas strikes (4.125 gas-D, 4.120-4.180 gas-W), or does it rest
the naive footprint's one-sided strikes (3.980-4.060 gas-W, etc.)?
Uses module L (kalshi_attribution_ledger) for authed reads. Places NO orders."""
import kalshi_attribution_ledger as L
from collections import defaultdict

GAS = ("KXAAAGASD", "KXAAAGASW")

orders = L.get_paginated(f"{L.P}/portfolio/orders", "orders", extra="&status=resting")
print(f"total resting orders on account: {len(orders)}")

by_series = defaultdict(list)
for o in orders:
    t = o.get("ticker") or ""
    by_series[t.split("-")[0]].append(o)

print("\nresting orders by series:")
for s in sorted(by_series, key=lambda s: -len(by_series[s])):
    print(f"  {s:<26} {len(by_series[s])} orders")

# Gas detail: which strikes, which side, how much
print("\n=== GAS resting detail (ticker | side | action | price | remaining) ===")
gas_tickers = set()
for s in GAS:
    for o in sorted(by_series.get(s, []), key=lambda x: x.get("ticker", "")):
        side = o.get("outcome_side") or o.get("side")
        action = o.get("action")
        rc = o.get("remaining_count_fp") or o.get("remaining_count") or o.get("count")
        price = o.get(f"{side}_price_dollars") if side else None
        t = o.get("ticker")
        gas_tickers.add(t)
        print(f"  {t:<30} {str(side):>4} {str(action):>4} @{price} rem={rc}")

print(f"\ndistinct gas strikes with resting orders: {len(gas_tickers)}")
for t in sorted(gas_tickers):
    print(f"    {t}")

# also dump one raw order to see fields
if orders:
    import json
    print("\n=== sample raw resting order ===")
    print(json.dumps(orders[0], indent=2, sort_keys=True)[:1200])
