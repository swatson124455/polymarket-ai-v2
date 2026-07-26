#!/usr/bin/env python3
"""RESTING ORDER COMPOSITION -- NEW FILE, READ-ONLY (GET-only).
Splits the resting book into BUY (reserves cash) vs SELL (frees cash on fill / reduces),
so we know how much of the guard's resting_reserve is real venue cash-lock vs over-count.
"""
import json, sys
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
from maker_kalshi_client import KalshiOrderClient


def D(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


c = KalshiOrderClient()
orders = c.get_orders("resting").get("orders") or []
pos = c.get_positions()
held = {p["ticker"]: float(p.get("position_fp") or 0)
        for p in (pos.get("market_positions") or []) if float(p.get("position_fp") or 0)}

buy_reserve = sell_notional = 0.0
buy_n = sell_n = 0
rows = []
for o in orders:
    side = o.get("outcome_side") or o.get("side")
    action = o.get("action")
    rc = D(o.get("remaining_count_fp") or o.get("remaining_count") or o.get("count"))
    price = D(o.get(f"{side}_price_dollars"))
    notional = price * rc
    if action == "buy":
        buy_reserve += notional; buy_n += 1
    else:
        sell_notional += notional; sell_n += 1
    rows.append((o.get("ticker"), side, action, price, rc, notional, held.get(o.get("ticker"), 0)))

print(f"resting orders: {len(orders)}  (BUY={buy_n} SELL={sell_n})")
print(f"BUY reserve (price*count)  = ${buy_reserve:.2f}   <- venue actually locks this")
print(f"SELL notional (price*count)= ${sell_notional:.2f}   <- frees cash on fill, venue locks $0")
print(f"guard resting term (ALL)   = ${buy_reserve+sell_notional:.2f}   <- what _live_standing sums")
print(f"\n=== first raw resting order (field names) ===")
if orders:
    print(json.dumps(orders[0], indent=2, sort_keys=True))
print("\n=== rows (ticker side action price x count = notional | held) ===")
for t, s, a, p, rc, n, h in sorted(rows):
    print(f"  {t:34s} {str(s):3s} {str(a):4s} @${p:.2f} x{rc:6.1f} = ${n:7.2f} | held={h:+.1f}")
