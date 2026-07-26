#!/usr/bin/env python3
"""LIVE UNWIND MARKETABILITY -- corrected orderbook parse (top key is `orderbook_fp`).

Kalshi books are BID-ONLY on each outcome:
    best YES bid = max(yes_dollars) ; best NO bid = max(no_dollars)
    YES ask = 1 - best NO bid       ; NO ask   = 1 - best YES bid

Holding long YES, the bot reduces by resting a NO bid (_unwind_price(best_no, cost)):
    cap = floor((1 - cost + MAX_UNWIND_LOSS)*100)/100 ; order rests at min(best_no, cap)
If cap < best_no the order rests BELOW the market -> it cannot fill -> the position
rides to settlement. Symmetric for a short (long NO) position.
"""
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
ROOT = MK.API_ROOT
print("READ_AT_UTC", datetime.datetime.now(datetime.timezone.utc).isoformat())
print("MAX_UNWIND_LOSS live =", os.environ.get("KALSHI_MAX_UNWIND_LOSS"))

pos = c._get_paginated(f"{ROOT}/portfolio/positions", "market_positions",
                       {"count_filter": "position"})["market_positions"]

print(f"\n{'ticker':<34} {'pos':>6} {'cost/ct':>7} {'exitbid':>7} {'mark':>6} "
      f"{'uPnL$':>8} {'cap.02':>6} {'fill?':>5} {'cap.10':>6} {'fill?':>5} {'cap.55':>6} {'fill?':>5}")
tot_upnl = 0.0
stuck = {0.02: 0, 0.10: 0, 0.55: 0}
stuck_usd = {0.02: 0.0, 0.10: 0.0, 0.55: 0.0}
for p in sorted(pos, key=lambda z: z["ticker"]):
    q = float(p.get("position_fp") or 0)
    if not q:
        continue
    t = p["ticker"]
    expo = float(p.get("market_exposure_dollars") or 0)
    cost_ct = expo / abs(q)
    ob = (c.get_orderbook(t).get("orderbook_fp") or {})
    yl = [(float(a), float(b)) for a, b in (ob.get("yes_dollars") or [])]
    nl = [(float(a), float(b)) for a, b in (ob.get("no_dollars") or [])]
    best_yes = max([a for a, _ in yl], default=0.0)
    best_no = max([a for a, _ in nl], default=0.0)
    # exit side: long YES -> rest a NO bid ; long NO -> rest a YES bid
    exit_bid = best_no if q > 0 else best_yes
    # mark of what we hold, on the bid we could hit
    mark = best_yes if q > 0 else best_no
    upnl = (mark - cost_ct) * abs(q)
    tot_upnl += upnl
    row = f"{t:<34} {q:>6.1f} {cost_ct:>7.4f} {exit_bid:>7.2f} {mark:>6.2f} {upnl:>8.2f}"
    for mul in (0.02, 0.10, 0.55):
        cap = math.floor((1.0 - cost_ct + mul) * 100.0) / 100.0
        ok = cap >= exit_bid - 1e-9 and exit_bid > 0
        row += f" {cap:>6.2f} {'YES' if ok else 'NO':>5}"
        if not ok:
            stuck[mul] += 1
            stuck_usd[mul] += expo
    print(row)

print(f"\nTOTAL unrealized (mark-to-opposing-bid) = ${tot_upnl:.2f}")
for mul in (0.02, 0.10, 0.55):
    print(f"  MAX_UNWIND_LOSS={mul:<5} -> {stuck[mul]} of 8 positions have an UNFILLABLE exit, "
          f"${stuck_usd[mul]:.2f} of cost basis stranded")
