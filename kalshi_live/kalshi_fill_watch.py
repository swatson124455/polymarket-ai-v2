#!/usr/bin/env python3
"""KALSHI FILL WATCH — read-only alarm on every fill for the first N hours after a start.

Safeguard 2 (operator 2026-09-06): the GO-window churn fills landed at 18:03-18:09Z,
~30 min before the 18:35Z halt. A watched bot gets pulled at -$3, not -$20. This tails
the venue fills feed + balance and prints an ALARM line per new fill with running
realized cash delta, so the operator (or a session) can stop early.

Honest capability statement (blind-review N3, 2026-09-06): this constructs the
ORDER-CAPABLE KalshiOrderClient directly (it needs the authed GET endpoints), but only
ever calls its GET methods — get_balance / the fills read. It never places, cancels,
amends, or modifies anything. Run with live.env sourced (the client reads its
credentials from the environment).

Accounting (blind-review N2 fix): per-fill cash is YES-SIGNED net of fee —
sign(action) * yes_price * count - fee_cost — the venue's action-only convention
(memory: fill direction ACTION-ONLY yes-signed). Verified on the 2026-09-01 GO window:
sums to -$19.95 and matches the balance delta to the cent. The old side-price proxy
read -$228 on that window and is gone.

Usage (on box, under sudo):
  ./venv/bin/python kalshi_fill_watch.py --since <ISO8601> [--hours 2] [--interval 60]
    --since   : only count fills at/after this timestamp (pass the start time)
    --hours   : watch window length (default 2)
    --interval: seconds between polls (default 60)
    --once    : single pass then exit (for cron/session polling)
    --stop-file-on-alarm : ON ALARM, write the STOP sentinel (halts the bot; the daemon
                honors STOP within its heartbeat). DEFAULT OFF — arming this is an
                explicit operator decision (held item A4i). Writes only if no STOP
                already exists; never deletes or edits an existing STOP.
Prints, each poll: new fills since last poll (side/ct/price/taker), cumulative fills-net
dollars and the balance delta vs the balance at watcher start, and an ALARM banner if
EITHER gauge is <= -ALARM_USD. Note the balance delta also moves on settlements/credits;
the fills-net gauge moves on fills only.
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

LIVE = "/opt/pa2-maker-kalshi-live"
ALARM_USD = 5.0   # cumulative realized loss that raises the banner
STOP_FILE = os.path.join(LIVE, "STOP")


def _client():
    sys.path.insert(0, LIVE)
    from maker_kalshi_client import KalshiOrderClient
    return KalshiOrderClient(mode="live")


def _fill_cash(f):
    """YES-signed, net of fee: buy = -yes_price*ct, sell = +yes_price*ct, minus fee.
    ACTION-ONLY convention — never sign by side (side-derived signs invert ~half)."""
    n = float(f["count_fp"])
    yp = float(f.get("yes_price_dollars") or 0)
    fee = float(f.get("fee_cost") or 0)
    sign = -1.0 if f["action"] == "buy" else 1.0
    return sign * yp * n - fee


def _balance(c):
    try:
        return float(c.get_balance().get("balance", 0)) / 100.0
    except Exception:
        return None


def poll(c, since, seen):
    import maker_kalshi_client as MK
    fills = c._get_paginated(f"{MK.API_ROOT}/portfolio/fills", "fills", {})["fills"]
    win = [f for f in fills if f.get("created_time", "") >= since]
    new = [f for f in win if f.get("fill_id") not in seen]
    for f in new:
        seen.add(f.get("fill_id"))
    realized = sum(_fill_cash(f) for f in win)   # fills-net (yes-signed, fee-inclusive)
    return win, new, realized


def _write_stop(loss, now):
    """A4i mechanism (flag-gated OFF by default). Creates STOP only if absent."""
    if os.path.exists(STOP_FILE):
        print(f"  STOP already present — not rewriting ({STOP_FILE})")
        return
    with open(STOP_FILE, "w") as fh:
        fh.write(f"fill-watch auto-halt {now} cumulative realized {loss:+.2f} "
                 f"<= -${ALARM_USD:.0f} (kalshi_fill_watch.py --stop-file-on-alarm)")
    print(f"  STOP WRITTEN by fill-watch ({STOP_FILE}) — daemon will flatten/halt")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True)
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--stop-file-on-alarm", action="store_true")
    a = ap.parse_args()

    c = _client()
    start_bal = _balance(c)
    seen = set()
    stop_written = False
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
                f"fills_net={realized:+.2f} "
                + (f"balance_delta={bal_delta:+.4f}" if bal_delta is not None else "balance=?"))
        print(line)
        # ALARM on EITHER gauge (blind-review fix: the balance gauge alone cannot see a
        # loss that predates watcher start, e.g. in replays; the fills gauge alone cannot
        # see settlement bleed — either one tripping is actionable).
        trips = [x for x in (bal_delta, realized) if x is not None and x <= -ALARM_USD]
        if trips:
            loss = min(trips)
            print(f"  !!! ALARM {now}: realized {loss:+.2f} <= -${ALARM_USD:.0f} — "
                  f"consider STOP (systemctl stop polymarket-maker-kalshi-ws or touch STOP)")
            if a.stop_file_on_alarm and not stop_written:
                _write_stop(loss, datetime.now(timezone.utc).isoformat())
                stop_written = True
        if a.once or time.monotonic() >= t_end:
            break
        time.sleep(a.interval)
    print("FILL WATCH window closed.")


if __name__ == "__main__":
    main()
