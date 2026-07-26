#!/usr/bin/env python3
"""TRUE VENUE RESERVATION -- NEW FILE, READ-ONLY (GET-only).

Kalshi collateral model (fully collateralised):
  position_fp signed in YES-equivalents: + = long YES, - = long NO (short YES).
  A resting order is a BID on one outcome:
    - buy yes  @ y  -> reserves y * count  UNLESS it reduces a long-NO position (covered -> $0)
    - sell yes @ y == NO bid @ (1-y) -> reserves (1-y)*count UNLESS it reduces a long-YES (covered)
  Covered (reducing) legs free cash on fill: venue reserves $0 for them.
  Coverage is shared across multiple orders on the same ticker -> allocate greedily.

Outputs the venue's ACTUAL resting cash-reservation, and reconciles:
    account_value = free_cash + resting_reservation + portfolio_value(mark)
"""
import json, sys
from collections import defaultdict
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
from maker_kalshi_client import KalshiOrderClient


def D(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


c = KalshiOrderClient()
bal = c.get_balance()
free_cash = bal.get("balance", 0) / 100.0
pv = bal.get("portfolio_value", 0) / 100.0

pos = c.get_positions()
held = {p["ticker"]: float(p.get("position_fp") or 0)
        for p in (pos.get("market_positions") or []) if float(p.get("position_fp") or 0)}

orders = c.get_orders("resting").get("orders") or []

# remaining coverage per ticker: long_yes can cover no-bids; long_no can cover yes-bids
cover_long_yes = {t: max(0.0, n) for t, n in held.items()}
cover_long_no = {t: max(0.0, -n) for t, n in held.items()}

reservation = 0.0
covered_total = 0.0
rows = []
# process so coverage is deterministic; sort by ticker
for o in sorted(orders, key=lambda x: x.get("ticker", "")):
    t = o["ticker"]
    outcome = o.get("outcome_side")          # 'yes' | 'no'  (the side being BID)
    cnt = D(o.get("remaining_count_fp") or o.get("remaining_count") or o.get("count"))
    price = D(o.get(f"{outcome}_price_dollars"))   # that outcome's own bid price
    if outcome == "yes":
        cov = min(cnt, cover_long_no.get(t, 0.0))   # buying yes reduces a long-NO
        cover_long_no[t] = cover_long_no.get(t, 0.0) - cov
    else:  # no bid
        cov = min(cnt, cover_long_yes.get(t, 0.0))  # buying no (sell yes) reduces a long-YES
        cover_long_yes[t] = cover_long_yes.get(t, 0.0) - cov
    uncovered = cnt - cov
    r = price * uncovered
    reservation += r
    covered_total += price * cov
    rows.append((t, outcome, price, cnt, cov, uncovered, r))

guard_resting = sum(D(o.get(f"{o.get('outcome_side')}_price_dollars")) *
                    D(o.get("remaining_count_fp") or o.get("remaining_count") or o.get("count"))
                    for o in orders)

account_value = free_cash + reservation + pv

print(f"free_cash (balance, net of reserve) = ${free_cash:.2f}")
print(f"portfolio_value (mark)              = ${pv:.2f}")
print(f"TRUE resting reservation (uncovered bids) = ${reservation:.2f}")
print(f"  covered/reducing notional (venue reserves $0) = ${covered_total:.2f}")
print(f"guard resting term (ALL outcome_price*count)    = ${guard_resting:.2f}")
print(f"  guard OVER-counts resting by                  = ${guard_resting - reservation:.2f}")
print(f"\nRECONCILE identity: account_value = free_cash + resting_reservation + pv")
print(f"  = ${free_cash:.2f} + ${reservation:.2f} + ${pv:.2f} = ${account_value:.2f}")
print(f"venue reservation d = account_value - free_cash = ${account_value - free_cash:.2f}")
print(f"                    = resting_reservation + pv  = ${reservation + pv:.2f}")
print("\n=== per-order coverage (ticker outcome @price xcount cov uncov reserve) ===")
for t, oc, p, cnt, cov, unc, r in rows:
    print(f"  {t:34s} {oc:3s} @${p:.2f} x{cnt:6.1f} cov={cov:5.1f} unc={unc:5.1f} res=${r:6.2f}")
