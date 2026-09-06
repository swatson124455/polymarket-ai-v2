#!/usr/bin/env python3
"""KALSHI FILL WATCH — read-only alarm on every fill for the first N hours after a start.

Safeguard 2 (operator 2026-09-06): the GO-window churn fills landed at 18:03-18:09Z,
~30 min before the 18:35Z halt. A watched bot gets pulled at -$3, not -$20. This tails
the venue fills feed + balance and prints an ALARM line per new fill with running
realized cash delta, so the operator (or a session) can stop early.

Read-only: no order client is ever constructed, only public/authenticated GET reads via
the cash recorder's own client. Never places/cancels/modifies anything.

Usage (on box, under sudo):
  ./venv/bin/python kalshi_fill_watch.py --since <ISO8601> [--hours 2] [--interval 60]
    --since   : only count fills at/after this timestamp (pass the start time)
    --hours   : watch window length (default 2)
    --interval: seconds between polls (default 60)
    --once    : single pass then exit (for cron/session polling)
Prints, each poll: new fills since last poll (side/ct/price/taker), cumulative realized
cash delta vs the balance at --since, and an ALARM banner if realized <= -ALARM_USD.
"""
import argparse
import inspect
import json
import re
import sys
import time
from datetime import datetime, timezone

LIVE = "/opt/pa2-maker-kalshi-live"
ALARM_USD = 5.0   # cumulative realized loss that raises the banner


def _client():
    sys.path.insert(0, LIVE)
    import kalshi_cash_recorder as kcr
    src = inspect.getsource(kcr)
    m = re.search(r"^\s*c\s*=\s*(.+)$", src, re.M)
    return eval(m.group(1), vars(kcr))


def _fill_cash(f):
    n = float(f["count_fp"])
    yp = float(f.get("yes_price_dollars") or 0)
    np_ = float(f.get("no_price_dollars") or 0)
    px = yp if f["side"] == "yes" else np_
    return (-px * n if f["action"] == "buy" else px * n)


def _balance(c):
    try:
        return float(c.get_balance().get("balance", 0)) / 100.0
    except Exception:
        return None


def poll(c, since, seen):
    fills = c._get_paginated(f"{__import__('kalshi_cash_recorder').R}/portfolio/fills",
                             "fills", {})["fills"]
    win = [f for f in fills if f.get("created_time", "") >= since]
    new = [f for f in win if f.get("fill_id") not in seen]
    for f in new:
        seen.add(f.get("fill_id"))
    realized = sum(_fill_cash(f) for f in win)   # pair-net proxy; balance is truth below
    return win, new, realized


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args()

    c = _client()
    start_bal = _balance(c)
    seen = set()
    t_end = time.monotonic() + a.hours * 3600
    print(f"FILL WATCH since {a.since} | window {a.hours}h | start balance "
          f"${start_bal:.4f}" if start_bal is not None else "start balance UNKNOWN")

    while True:
        now = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
        win, new, realized = poll(c, a.since, seen)
        bal = _balance(c)
        bal_delta = (bal - start_bal) if (bal is not None and start_bal is not None) else None
        for f in new:
            print(f"  {now} FILL {f['ticker']} {f['action']} {f['side']} "
                  f"{float(f['count_fp']):.0f}ct @{(float(f.get('yes_price_dollars') or 0) if f['side']=='yes' else float(f.get('no_price_dollars') or 0)):.2f} "
                  f"taker={f.get('is_taker')}")
        line = (f"  {now} fills={len(win)} new={len(new)} "
                f"fill_cash_proxy={realized:+.2f} "
                + (f"balance_delta={bal_delta:+.4f}" if bal_delta is not None else "balance=?"))
        print(line)
        loss = bal_delta if bal_delta is not None else realized
        if loss is not None and loss <= -ALARM_USD:
            print(f"  !!! ALARM {now}: realized {loss:+.2f} <= -${ALARM_USD:.0f} — "
                  f"consider STOP (systemctl stop polymarket-maker-kalshi-ws or touch STOP)")
        if a.once or time.monotonic() >= t_end:
            break
        time.sleep(a.interval)
    print("FILL WATCH window closed.")


if __name__ == "__main__":
    main()
