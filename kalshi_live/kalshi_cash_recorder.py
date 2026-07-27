#!/usr/bin/env python3
"""KALSHI CASH RECORDER — forward-looking, exact reward attribution from now on.

READ-ONLY against the venue: balance, orders, positions, fills, settlements.
Places nothing, cancels nothing, amends nothing. Safe under STOP / while parked.

WHY IT EXISTS
  No reward line-item has been found on the trade API. ESTABLISHED 2026-07-27: of 13 candidate
  money endpoints probed, only /portfolio/balance, /portfolio/fills and /portfolio/settlements
  exist; the other 10 (transactions, account_history, ledger, rewards, incentive_payouts,
  incentive_rewards, payouts, resting_order_total_value, incentive_programs/payouts,
  incentive_program_payouts) all return 404. That is 13 probed paths, NOT a proof that the
  full API surface has none. On the evidence we have, a reward is observable only as the
  UNEXPLAINED part of a cash move. Operator rule: any unexplained POSITIVE step is a REWARD
  unless the operator states it was a deposit.

THE CONFOUND THIS EXISTS TO RESOLVE  -- OPEN QUESTION, NOT AN ESTABLISHED FACT
  Reconciling on `balance` alone leaves large per-interval errors that mostly cancel over a
  long window, so something moves cash without appearing in fills or settlements.
  HYPOTHESIS (untested): `balance` is net of collateral reserved by our own resting orders,
  so d(balance) mixes trading flow with reservation changes. A first check against the
  ledger's `resting_orders` COUNT did NOT confirm it -- count is a poor proxy, since
  reservation should scale with notional, which nothing recorded.

  So this records `cash` and `resting_reservation` SEPARATELY every cycle and asserts
  neither. Whichever holds, consecutive rows settle it empirically:
      if the hypothesis holds:  d(cash + resting_reservation) - fills - settlement == 0
      if it does not:           d(cash) - fills - settlement == 0
  and the remainder in the correct form is deposit-or-reward. Raw orders are stored too, so
  the reservation model can be revised later WITHOUT re-collecting.

CONVENTIONS (both validated against the venue 2026-07-27)
  fills       ACTION-ONLY, YES-SIGNED; priced on yes_price_dollars. Integrity check:
              settled net-yes == summed fills on 75/75 settled tickers.
  settlements payout on NET position only: net = yes_count_fp - no_count_fp;
              payout = net*v if net>0 else -net*(1-v), where v = value/100.
              The gross "paired pays $1/pair" model is REFUTED (implied a -$1,977 residual).

OUTPUT  cash-YYYYMM.jsonl, one object per run, append-only. Raw rows are kept so every
        derived number stays re-derivable from the record itself.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/opt/pa2-maker-kalshi-live")

ENV_FILE = "/opt/pa2-maker-kalshi-live/live.env"


def _load_env(path=ENV_FILE):
    """Systemd supplies these via EnvironmentFile; a standalone run needs the same values.
    Never OVERRIDES an already-set variable, so an explicit env always wins."""
    try:
        with open(path) as fh:
            for ln in fh:
                ln = ln.strip()
                if not ln or ln.startswith("#") or "=" not in ln:
                    continue
                k, v = ln.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass


_load_env()

import maker_kalshi_client as MK
from maker_kalshi_client import KalshiOrderClient

R = MK.API_ROOT


def _f(x, d=0.0):
    try:
        return float(x)
    except (TypeError, ValueError):
        return d


def order_reservation(o):
    """MODEL of the collateral Kalshi holds for one resting order. NOT a venue-published
    figure, and NOT yet validated against observed cash — validating it is the whole point of
    recording it. The raw order is stored alongside so this can be revised without re-collecting.

    The bot quotes post-only bids, so a resting order is modelled as a BID for `outcome_side`
    at that side's own price, costing at most count x that price. `action` is reported
    YES-signed (observed 2026-07-27: fills carry only ('buy','yes') and ('sell','no'), i.e. a
    NO bid prints as action=sell), so the reserving price is keyed off outcome_side, NOT
    action — the one place in this file where action is the wrong key."""
    ct = _f(o.get("remaining_count_fp"), _f(o.get("initial_count_fp")))
    px = _f(o.get("yes_price_dollars")) if o.get("outcome_side") == "yes" \
        else _f(o.get("no_price_dollars"))
    return ct * px


def fill_cash(f):
    """ACTION-ONLY, YES-SIGNED. buy -> cash out at yes price; sell -> cash in at yes price."""
    sign = 1.0 if f.get("action") == "buy" else -1.0
    return -sign * _f(f.get("count_fp")) * _f(f.get("yes_price_dollars")) - _f(f.get("fee_cost"))


def settlement_payout(s):
    """NET position only. Gross/paired model refuted 2026-07-27."""
    v = _f(s.get("value")) / 100.0
    net = _f(s.get("yes_count_fp")) - _f(s.get("no_count_fp"))
    pay = (net * v) if net > 0 else ((-net) * (1.0 - v))
    return pay - _f(s.get("fee_cost"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="/opt/pa2-maker-kalshi-live")
    ap.add_argument("--print-only", action="store_true")
    a = ap.parse_args()

    now = datetime.datetime.now(datetime.timezone.utc)
    c = KalshiOrderClient(mode="live")

    bal = c.get_balance()
    cash = _f(bal.get("balance_dollars"))

    orders = c._get_paginated(f"{R}/portfolio/orders", "orders", {"status": "resting"})["orders"]
    reservation = sum(order_reservation(o) for o in orders)

    pos = c._get_paginated(f"{R}/portfolio/positions", "market_positions",
                           {"count_filter": "position"})["market_positions"]
    pos = [p for p in pos if _f(p.get("position_fp"))]
    open_cost = sum(_f(p.get("market_exposure_dollars")) for p in pos)

    # cumulative-to-date; differencing between rows is done offline so a missed run
    # never loses money (no incremental cursor state to corrupt).
    fills = c._get_paginated(f"{R}/portfolio/fills", "fills", {})["fills"]
    setts = c._get_paginated(f"{R}/portfolio/settlements", "settlements", {})["settlements"]
    cum_fills = sum(fill_cash(f) for f in fills)
    cum_settle = sum(settlement_payout(s) for s in setts)

    row = {
        "ts": now.isoformat(),
        "cash": round(cash, 6),
        "resting_reservation": round(reservation, 6),
        # candidate reconciling quantity IF the reservation hypothesis holds; `cash` alone is
        # the candidate if it does not. Both are recorded so neither is assumed.
        "funded_cash": round(cash + reservation, 6),
        "n_resting": len(orders),
        "open_position_cost": round(open_cost, 6),
        "n_positions": len(pos),
        "portfolio_value_cents": bal.get("portfolio_value"),
        "cum_fills_cash": round(cum_fills, 6),
        "cum_settle_payout": round(cum_settle, 6),
        "n_fills_todate": len(fills),
        "n_settlements_todate": len(setts),
        # unexplained-to-date under BOTH candidate forms; per-interval deltas taken offline.
        # Neither is asserted — whichever holds constant across rows is the true invariant.
        "unexplained_todate_funded": round(cash + reservation - cum_fills - cum_settle, 6),
        "unexplained_todate_cash": round(cash - cum_fills - cum_settle, 6),
        "resting_raw": [
            {"ticker": o.get("ticker"), "outcome_side": o.get("outcome_side"),
             "action": o.get("action"),
             "yes_price_dollars": o.get("yes_price_dollars"),
             "no_price_dollars": o.get("no_price_dollars"),
             "remaining_count_fp": o.get("remaining_count_fp"),
             "order_id": o.get("order_id"), "created_time": o.get("created_time")}
            for o in orders
        ],
        "positions_raw": [
            {"ticker": p.get("ticker"), "position_fp": p.get("position_fp"),
             "market_exposure_dollars": p.get("market_exposure_dollars")}
            for p in pos
        ],
    }

    line = json.dumps(row, separators=(",", ":"))
    if a.print_only:
        print(json.dumps({k: v for k, v in row.items()
                          if k not in ("resting_raw", "positions_raw")}, indent=1))
        print(f"(resting_raw {len(row['resting_raw'])} rows, "
              f"positions_raw {len(row['positions_raw'])} rows suppressed)")
        return 0

    path = os.path.join(a.out_dir, f"cash-{now.strftime('%Y%m')}.jsonl")
    with open(path, "a") as f:
        f.write(line + "\n")
    print(f"cash-recorder ok {now.isoformat()} cash={cash:.4f} "
          f"reservation={reservation:.4f} funded={cash+reservation:.4f} "
          f"unexplained_todate funded={row['unexplained_todate_funded']:.4f} "
          f"cash={row['unexplained_todate_cash']:.4f} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
