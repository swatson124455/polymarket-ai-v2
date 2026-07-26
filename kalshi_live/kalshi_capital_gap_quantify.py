#!/usr/bin/env python3
"""CAPITAL GAP QUANTIFY -- NEW FILE, READ-ONLY (GET-only). Imports the DEPLOYED module.

Lays FOUR numbers side by side on the live book and reconciles them to the venue's
own balance accounting:
  a. guard 'committed'  = sum(resting price*count) + GROSS held_cost   (the bug, quoter :1254-1259)
  b. GROSS held cost alone                                             (_held_cost total)
  c. NET held cost after ladder_pairing()                             (naked_held_cost)
  d. VENUE actual reservation = account equity - free cash            (from /portfolio/balance)

Units (canon M7f): balance/portfolio_value/*_exposure = integer CENTS; *_dollars = dollar strings.
Only GET calls are issued. No writes, no order ops.
"""
import json, os, sys

DEPLOY = "/opt/pa2-maker-kalshi-live"
sys.path.insert(0, DEPLOY)

from maker_kalshi_quoter import (          # noqa: E402  (deployed module, read-only import)
    _held_cost, naked_held_cost, ladder_pairing, _live_standing, MAX_TOTAL_CAPITAL,
)
from maker_kalshi_client import KalshiOrderClient  # noqa: E402


def D(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def main():
    client = KalshiOrderClient()   # mode/creds/LIVE_ARMED from injected env
    print(f"# mode={client.mode}  MAX_TOTAL_CAPITAL=${MAX_TOTAL_CAPITAL:,.2f}")

    # ---- raw balance (dump every field so we can SEE what the venue exposes) ----
    bal = client.get_balance()
    print("\n=== RAW /portfolio/balance ===")
    print(json.dumps(bal, indent=2, sort_keys=True))
    free_cash = bal.get("balance", 0) / 100.0
    pv = bal.get("portfolio_value", 0) / 100.0

    # ---- held inventory via the DEPLOYED _held_cost (same code the guard uses) ----
    gross_held, held_by, cost_by = _held_cost(client)

    # ---- resting orders via the DEPLOYED _live_standing (same parse the guard uses) ----
    standing, nraw = _live_standing(client)
    resting_reserve = sum(o["price_dollars"] * o["count"]
                          for ol in standing.values() for o in ol)

    # ---- raw positions + event_positions for causal breakdown / reconciliation ----
    posraw = client.get_positions()
    mps = [p for p in (posraw.get("market_positions") or [])
           if float(p.get("position_fp") or 0) != 0]
    # event_positions comes from the same endpoint payload on Kalshi; refetch raw for it
    import urllib.request
    # get_positions() strips event_positions; hit the endpoint directly for the full payload
    full = client._request("GET", "/trade-api/v2/portfolio/positions?count_filter=position&limit=1000")
    eps = full.get("event_positions") or []

    sum_mkt_exp = sum(D(p.get("market_exposure_dollars")) for p in mps)

    # ---- the FOUR numbers ----
    a = resting_reserve + gross_held
    b = gross_held
    naked_by = ladder_pairing(dict(held_by))
    c = naked_held_cost(held_by, cost_by)
    # venue reservation d: account equity minus free cash.
    # equity candidates depend on whether `balance` already nets resting-order reserve.
    equity_bal_plus_pv = free_cash + pv

    print("\n=== FOUR NUMBERS (dollars) ===")
    print(f"  a) guard 'committed' = resting_reserve + gross_held = "
          f"${resting_reserve:,.2f} + ${gross_held:,.2f} = ${a:,.2f}")
    print(f"  b) GROSS held cost                                   = ${b:,.2f}")
    print(f"  c) NET held cost (after ladder_pairing)              = ${c:,.2f}")
    print(f"  d) VENUE reservation (see reconciliation below)")

    print("\n=== BALANCE RECONCILIATION (what did the venue actually lock?) ===")
    print(f"  free_cash (balance)            = ${free_cash:,.2f}")
    print(f"  portfolio_value (mark)         = ${pv:,.2f}")
    print(f"  free_cash + portfolio_value    = ${equity_bal_plus_pv:,.2f}")
    print(f"  sum market_exposure (cost)     = ${sum_mkt_exp:,.2f}")
    print(f"  resting BUY reserve (a-part)   = ${resting_reserve:,.2f}")
    print(f"  gross held cost (b)            = ${gross_held:,.2f}")

    print("\n=== EVENT POSITIONS (venue's own event aggregation) ===")
    for e in eps:
        print(f"  {e.get('event_ticker'):28s} "
              f"exposure_$={D(e.get('event_exposure_dollars')):8.2f} "
              f"total_cost_$={D(e.get('total_cost_dollars')):8.2f} "
              f"realized_$={D(e.get('realized_pnl_dollars')):8.2f} "
              f"resting_orders_cost_$={D(e.get('resting_orders_count')):8}")

    print("\n=== MARKET POSITIONS (signed, exposure) ===")
    for p in sorted(mps, key=lambda x: x.get("ticker", "")):
        t = p.get("ticker"); n = float(p.get("position_fp") or 0)
        me = D(p.get("market_exposure_dollars"))
        nk = naked_by.get(t, 0.0)
        print(f"  {t:34s} pos={n:+9.2f} mkt_exp=${me:7.2f} naked={nk:+9.2f}")

    print("\n=== CAUSAL BREAKDOWN of gross held vs net held ===")
    paired_portion = gross_held - c
    print(f"  gross_held (b)                 = ${gross_held:,.2f}")
    print(f"  net/naked_held (c)             = ${c:,.2f}")
    print(f"  paired ladder legs counted gross = ${paired_portion:,.2f}")

    # dump event_position raw keys once so we know the exact field names available
    if eps:
        print("\n=== raw event_position[0] keys ===")
        print(json.dumps(eps[0], indent=2, sort_keys=True))
    if mps:
        print("\n=== raw market_position[0] keys ===")
        print(json.dumps(mps[0], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
