#!/usr/bin/env python3
"""Why does the STOP flatten still say 'reducing side unpriceable'? Evaluate the ACTUAL
deployed predicate against the ACTUAL live books, printing every input."""
import os, sys, importlib.util, datetime
for ln in open("/opt/pa2-maker-kalshi-live/live.env"):
    ln = ln.strip()
    if ln and not ln.startswith("#") and "=" in ln:
        k, v = ln.split("=", 1)
        os.environ[k] = v
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")
spec = importlib.util.spec_from_file_location(
    "q", "/opt/pa2-maker-kalshi-live/maker_kalshi_quoter.py")
q = importlib.util.module_from_spec(spec); sys.modules["q"] = q; spec.loader.exec_module(q)

print("READ_AT_UTC", datetime.datetime.now(datetime.timezone.utc).isoformat())
print("HAS _ok_exit_price:", hasattr(q, "_ok_exit_price"))
print("EXIT bounds:", getattr(q, "EXIT_MIN_PRICE_DOLLARS", None),
      getattr(q, "EXIT_MAX_PRICE_DOLLARS", None))
print("ENTRY band :", q.MIN_PRICE_DOLLARS, q.MAX_PRICE_DOLLARS)
print("MAX_UNWIND_LOSS:", q.MAX_UNWIND_LOSS, " INV_TOLERANCE:", q.INV_TOLERANCE)
print()

from maker_kalshi_client import KalshiOrderClient
import maker_kalshi_client as MK
c = KalshiOrderClient(mode="live")
pos = c._get_paginated(f"{MK.API_ROOT}/portfolio/positions", "market_positions",
                       {"count_filter": "position"})["market_positions"]
held = {p["ticker"]: float(p["position_fp"]) for p in pos if float(p.get("position_fp") or 0)}
costs = {p["ticker"]: (float(p.get("market_exposure_dollars") or 0) / abs(float(p["position_fp"])))
         for p in pos if float(p.get("position_fp") or 0)}
naked = q.ladder_pairing(held)
print("HELD  ", held)
print("NAKED ", naked)
print()
for t, pos_ct in held.items():
    ob = q.public_get(f"/trade-api/v2/markets/{t}/orderbook").get("orderbook_fp") or {}
    by = max((p for p, _ in q._levels(ob.get("yes_dollars") or [])[0]), default=None)
    bn = max((p for p, _ in q._levels(ob.get("no_dollars") or [])[0]), default=None)
    nk = naked.get(t, 0.0)
    print(f"{t}")
    print(f"   pos={pos_ct:+.1f} naked={nk:+.2f} cost/ct={costs[t]:.4f} by={by} bn={bn}")
    if abs(nk) < q.INV_TOLERANCE:
        print(f"   -> SKIPPED: |naked| {abs(nk):.2f} < INV_TOLERANCE {q.INV_TOLERANCE} "
              f"(treated as a FLOORED PAIR, left to settle)")
        continue
    if nk > 0:
        ok = q._ok_exit_price(bn)
        print(f"   -> long yes: needs NO bid, _ok_exit_price({bn}) = {ok}")
        price = bn
    else:
        ok = q._ok_exit_price(by)
        print(f"   -> long no : needs YES bid, _ok_exit_price({by}) = {ok}")
        price = by
    if ok:
        capped = q._unwind_price(price, costs[t])
        print(f"      _unwind_price({price}, {costs[t]:.4f}) = {capped}  "
              f"then _ok_exit_price -> {q._ok_exit_price(capped)}")
