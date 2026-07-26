#!/usr/bin/env python3
"""Fresh state — ~9.5h have passed since the 04:30Z reads. Re-verify, do not reuse."""
import os, sys, json, math, datetime
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
for ln in open("/opt/pa2-maker-kalshi-live/live.env"):
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1)
        os.environ[k] = v
import maker_kalshi_client as MK
from maker_kalshi_client import KalshiOrderClient

c = KalshiOrderClient(mode="live")
R = MK.API_ROOT
print("READ_AT_UTC", datetime.datetime.now(datetime.timezone.utc).isoformat())

b = c.get_balance()
print("CASH_USD", b.get("balance_dollars"), "PORTFOLIO_VALUE_cents", b.get("portfolio_value"))

pos = c._get_paginated(f"{R}/portfolio/positions", "market_positions",
                       {"count_filter": "position"})["market_positions"]
pos = [p for p in pos if float(p.get("position_fp") or 0)]
print("N_POSITIONS", len(pos))
tot_exp = 0.0
for p in sorted(pos, key=lambda z: z["ticker"]):
    q = float(p["position_fp"])
    e = float(p.get("market_exposure_dollars") or 0)
    tot_exp += e
    ob = (c.get_orderbook(p["ticker"]).get("orderbook_fp") or {})
    by = max([float(a) for a, _ in (ob.get("yes_dollars") or [])], default=0.0)
    bn = max([float(a) for a, _ in (ob.get("no_dollars") or [])], default=0.0)
    mark = by if q > 0 else bn
    cost = e / abs(q)
    print(f"  {p['ticker']:<36} pos {q:>7.1f} cost/ct {cost:.4f} mark {mark:.2f} "
          f"uPnL ${(mark-cost)*abs(q):>8.2f}")
print("TOTAL_EXPOSURE_USD", round(tot_exp, 2))

o = c.get_orders(status="resting")["orders"]
print("N_RESTING_ORDERS", len(o))
for x in o:
    print("  ORDER", x.get("ticker"), x.get("action"), x.get("side"),
          "yes_px", x.get("yes_price_dollars"))

# settlements since the 04:48Z read
setts = c._get_paginated(f"{R}/portfolio/settlements", "settlements")["settlements"]
print("N_SETTLEMENTS_TOTAL", len(setts))
tot = ct = 0.0
naked_n = naked_ct = naked_net = 0.0
recent = []
for s in setts:
    yc = float(s.get("yes_count_fp") or 0); nc = float(s.get("no_count_fp") or 0)
    yco = float(s.get("yes_total_cost_dollars") or 0)
    nco = float(s.get("no_total_cost_dollars") or 0)
    fee = float(s.get("fee_cost") or 0); res = s.get("market_result")
    net = (yc if res == "yes" else nc if res == "no" else 0.0) - (yco + nco) - fee
    tot += net; ct += yc + nc
    if not (yc > 0 and nc > 0):
        naked_n += 1; naked_ct += yc + nc; naked_net += net
    if (s.get("settled_time") or "") >= "2026-07-26T04:48":
        recent.append((s.get("settled_time"), s.get("ticker"), round(net, 4), yc + nc))
print("SETTLED_NET_USD", round(tot, 4), "over", round(ct, 1), "contracts",
      "-> per_ct", round(tot / ct, 5) if ct else None)
print("NAKED: n", int(naked_n), "ct", round(naked_ct, 1), "net", round(naked_net, 4),
      "per_ct", round(naked_net / naked_ct, 5) if naked_ct else None)
print("SETTLEMENTS SINCE 04:48Z:", len(recent))
for r in sorted(recent):
    print("   ", r)
