#!/usr/bin/env python3
"""Evaluate the STRAND-UNWIND predicate (maker_kalshi_quoter.py:1999-2001) against the
live positions, exactly as the quoter would:

    sby = best YES bid ; sbn = best NO bid
    if sby is None or sbn is None or sby + sbn >= 1.0:
        continue          # "unpriceable/crossed -- taker handles it"

The comment defers to the taker. TAKER_FLATTEN=0 and PRECLOSE_FLATTEN=0 (absent from
live.env -> default 0), so the taker does NOT handle it. If this predicate skips, the
position has NO exit order from ANY path and rides to settlement by construction.
"""
import os, sys, datetime
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
print("TAKER_FLATTEN =", os.environ.get("KALSHI_TAKER_FLATTEN"),
      "| PRECLOSE_FLATTEN =", os.environ.get("KALSHI_PRECLOSE_FLATTEN", "(absent -> 0 OFF)"))
print()
pos = c._get_paginated(f"{R}/portfolio/positions", "market_positions",
                       {"count_filter": "position"})["market_positions"]
skipped = 0
for p in sorted([x for x in pos if float(x.get("position_fp") or 0)],
                key=lambda z: z["ticker"]):
    t = p["ticker"]
    q = float(p["position_fp"])
    ob = (c.get_orderbook(t).get("orderbook_fp") or {})
    yl = [float(a) for a, _ in (ob.get("yes_dollars") or [])]
    nl = [float(a) for a, _ in (ob.get("no_dollars") or [])]
    sby = max(yl) if yl else None
    sbn = max(nl) if nl else None
    why = []
    if sby is None:
        why.append("YES book EMPTY")
    if sbn is None:
        why.append("NO book EMPTY")
    if sby is not None and sbn is not None and sby + sbn >= 1.0:
        why.append(f"CROSSED (sby+sbn={sby+sbn:.2f}>=1.0)")
    skip = bool(why)
    if skip:
        skipped += 1
    print(f"  {t:<36} pos {q:>7.1f}  sby={str(sby):<6} sbn={str(sbn):<6} "
          f"-> {'SKIP: ' + ', '.join(why) if skip else 'would rest an exit'}")
print(f"\nSTRAND PATH SKIPS {skipped} of {len([x for x in pos if float(x.get('position_fp') or 0)])} positions.")
print("Those have NO exit order from any path (strand skipped, taker off, preclose off).")
