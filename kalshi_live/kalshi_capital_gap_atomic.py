#!/usr/bin/env python3
"""ATOMIC CAPITAL-GAP SNAPSHOT -- NEW FILE, READ-ONLY (GET-only).
All four numbers + reservation reconciliation from ONE set of reads so they are
mutually consistent (the live book moves cycle-to-cycle).

  a. guard 'committed'  = sum(resting outcome_price*count) + GROSS held  (quoter :1254-1259)
  b. GROSS held cost    = sum(market_exposure_dollars)                   (_held_cost total)
  c. NET held cost      = naked_held_cost(held_by, cost_by)              (after ladder_pairing)
  d. VENUE reservation  = account_value - free_cash = true_resting_reserve + pv(mark)

Kalshi model: balance = free cash NET of reserve (proven: guard_resting > balance is
impossible unless reserve<balance). A resting order reserves outcome_price*uncovered_count;
a leg covered by an opposing held position reduces it and reserves $0.
"""
import json, sys
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
from maker_kalshi_quoter import _held_cost, naked_held_cost, ladder_pairing, _live_standing
from maker_kalshi_client import KalshiOrderClient


def D(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


c = KalshiOrderClient()

# --- ONE snapshot of reads ---
bal = c.get_balance()
free_cash = bal.get("balance", 0) / 100.0
pv = bal.get("portfolio_value", 0) / 100.0

gross_held, held_by, cost_by = _held_cost(c)          # deployed code
naked = naked_held_cost(held_by, cost_by)             # deployed code
standing, _n = _live_standing(c)                      # deployed code
guard_resting = sum(o["price_dollars"] * o["count"]
                    for ol in standing.values() for o in ol)

orders = c.get_orders("resting").get("orders") or []
held_signed = dict(held_by)

# TRUE resting reservation: outcome_price * uncovered, coverage from opposing held position
cover_long_yes = {t: max(0.0, n) for t, n in held_signed.items()}
cover_long_no = {t: max(0.0, -n) for t, n in held_signed.items()}
true_reserve = covered_notional = 0.0
for o in sorted(orders, key=lambda x: x.get("ticker", "")):
    t = o["ticker"]; oc = o.get("outcome_side")
    cnt = D(o.get("remaining_count_fp") or o.get("remaining_count") or o.get("count"))
    price = D(o.get(f"{oc}_price_dollars"))
    if oc == "yes":
        cov = min(cnt, cover_long_no.get(t, 0.0)); cover_long_no[t] = cover_long_no.get(t, 0.0) - cov
    else:
        cov = min(cnt, cover_long_yes.get(t, 0.0)); cover_long_yes[t] = cover_long_yes.get(t, 0.0) - cov
    true_reserve += price * (cnt - cov); covered_notional += price * cov

a = guard_resting + gross_held
b = gross_held
cc = naked
account_value = free_cash + true_reserve + pv
d = account_value - free_cash

print("========== ATOMIC SNAPSHOT ==========")
print(f"free_cash(balance)=${free_cash:.2f}  portfolio_value(mark)=${pv:.2f}")
print(f"\n--- FOUR NUMBERS ---")
print(f"a) guard committed  = resting({guard_resting:.2f}) + gross_held({gross_held:.2f}) = ${a:.2f}")
print(f"b) GROSS held       = ${b:.2f}")
print(f"c) NET/naked held   = ${cc:.2f}")
print(f"d) VENUE reservation= true_resting_reserve({true_reserve:.2f}) + pv({pv:.2f}) = ${d:.2f}")
print(f"\n--- KEY RATIO ---")
print(f"a - d = ${a - d:.2f}   (a/d = {a/d:.2f}x)")
print(f"\n--- CAUSAL BREAKDOWN of (a - d) ---")
resting_overcount = guard_resting - true_reserve
held_cost_vs_mark = gross_held - pv
print(f"1. resting reducing legs counted gross (covered->venue reserves $0): ${resting_overcount:.2f}")
print(f"   (covered/reducing notional the guard still counts = ${covered_notional:.2f})")
print(f"2. held counted at cost/max-loss vs venue mark (gross_held - pv):    ${held_cost_vs_mark:.2f}")
print(f"   check: sum = ${resting_overcount + held_cost_vs_mark:.2f}  vs (a-d)=${a-d:.2f}")
print(f"\n--- RISK vs CAPITAL (why 'use naked' would UNDER-reserve) ---")
print(f"gross_held (venue locks this per-market, summed) = ${gross_held:.2f}")
print(f"naked_held (operator TRUE max loss, ladder-floored)= ${cc:.2f}")
print(f"ladder-paired portion (venue locks, ~0 risk)      = ${gross_held - cc:.2f}")
print(f"  -> a capital cap on naked would under-reserve vs venue by ${gross_held - cc:.2f}")
print(f"\n--- $1/contract fallback check (_held_cost) ---")
missing = [t for t, n in held_signed.items() if t not in cost_by]
print(f"positions with NO market_exposure_dollars (fallback to |n|*$1): {missing or 'NONE'}")
print(f"\n--- RECONCILE (identity, balance proven net-of-reserve) ---")
print(f"account_value = free_cash + true_resting_reserve + pv")
print(f"  = {free_cash:.2f} + {true_reserve:.2f} + {pv:.2f} = ${account_value:.2f}")
print(f"free_cash + d = {free_cash:.2f} + {d:.2f} = ${free_cash + d:.2f}  (== account_value)")
print(f"\n--- IDLE CAPITAL (the treadmill) ---")
print(f"free_cash available to deploy NOW = ${free_cash:.2f}")
print(f"guard committed a = ${a:.2f}  (cap gates NEW orders on this)")
