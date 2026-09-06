#!/usr/bin/env python3
"""KALSHI FILL WATCH — read-only alarm on every fill; continuous mode + true-loss STOP.

Safeguard 2 (operator 2026-09-06): the GO-window churn fills landed at 18:03-18:09Z,
~30 min before the 18:35Z halt. A watched bot gets pulled at -$3, not -$20. This tails
the venue fills feed + balance and prints an ALARM line per new fill with running
realized cash delta, so the operator (or a session) can stop early.

Honest capability statement (blind-review N3): this constructs the ORDER-CAPABLE
KalshiOrderClient directly (it needs the authed GET endpoints), but only ever calls its
GET methods — balance, fills, positions. It never places, cancels, amends, or modifies
anything. The ONE write it can make is the STOP sentinel, and only under
--stop-file-on-alarm. Run with live.env sourced (client credentials come from the env).

GAUGES (each printed every poll):
  fills_net    — YES-signed net-of-fee sum over window fills (ACTION-ONLY convention;
                 verified on the 2026-09-01 GO window: -$19.95 = balance recon exact).
                 Mid-position it includes open cost/credit — informational.
  balance_delta— venue balance vs the session baseline. Debits on OPEN fills too
                 (measured 09-01 18:32->18:37Z: -$29.00 on one benign 100ct open).
  realized_est — THE TRUE-LOSS GAUGE (A4i): (balance + open_position_cost) now, minus
                 the same sum at baseline. Open cost basis comes from the venue
                 positions read (market_exposure_dollars — the cash recorder's own
                 field). Deploying capital reads ~0; churn/settlement losses read
                 negative. BLIND SPOT (stated per Rule 12): an OPEN position marked
                 against us reads ~0 here until exit/settle — mark-drawdown is the
                 daemon's own $10 halt's job, not this gauge's.
ALARM banner: any gauge <= -ALARM_USD (informational, prints loudly).
STOP write (--stop-file-on-alarm): ONLY when realized_est <= -ALARM_USD — never on the
raw gauges (they trip on benign open fills; measured, see above). Writes only if no
STOP already exists; never deletes or edits an existing STOP.

Usage (on box, under sudo, env sourced):
  ./venv/bin/python kalshi_fill_watch.py --since <ISO8601> [--hours 2] [--interval 60]
    --since        : only count fills at/after this timestamp
    --since-file F : read the since-timestamp from F; if F is missing, write NOW into it
                     and use that (self-healing for the systemd unit; the file persists
                     across watcher restarts so the window never silently re-bases)
    --baseline-file F : persist {balance, open_cost} baseline in F; loaded if present
                     (crash-restart continuity), created if absent. Remove the file to
                     re-base (kalshi_safe_start.sh removes it at each sanctioned start).
    --hours 0      : run FOREVER (A4ii continuous mode — the systemd unit's setting;
                     the unit is PartOf the daemon so it stops when the daemon stops)
    --once         : single pass then exit (replay/session polling)
    --stop-file-on-alarm : arm the A4i auto-STOP (true-loss gauge only, see above)
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

LIVE = "/opt/pa2-maker-kalshi-live"
ALARM_USD = 5.0   # cumulative realized loss that raises the banner / STOP
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


def _open_cost(c):
    """Open-position cost basis, venue truth — same field the cash recorder sums
    (market_exposure_dollars over nonzero position_fp rows)."""
    try:
        import maker_kalshi_client as MK
        pos = c._get_paginated(f"{MK.API_ROOT}/portfolio/positions", "market_positions",
                               {"count_filter": "position"})["market_positions"]
        return sum(float(p.get("market_exposure_dollars") or 0) for p in pos
                   if float(p.get("position_fp") or 0))
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
    """A4i auto-STOP (armed 2026-09-06, true-loss gauge only). Creates STOP only if absent."""
    if os.path.exists(STOP_FILE):
        print(f"  STOP already present — not rewriting ({STOP_FILE})")
        return
    with open(STOP_FILE, "w") as fh:
        fh.write(f"fill-watch auto-halt {now} realized_est {loss:+.2f} "
                 f"<= -${ALARM_USD:.0f} (kalshi_fill_watch.py --stop-file-on-alarm)")
    print(f"  STOP WRITTEN by fill-watch ({STOP_FILE}) — daemon will flatten/halt")


def _resolve_since(a):
    if a.since_file:
        try:
            s = open(a.since_file).read().strip()
            if s:
                return s
        except OSError:
            pass
        s = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
        try:
            with open(a.since_file, "w") as fh:
                fh.write(s)
        except OSError:
            pass
        return s
    if not a.since:
        sys.exit("need --since or --since-file")
    return a.since


def _resolve_baseline(a, c):
    """(start_bal, start_open) — loaded from --baseline-file if present, else measured
    now (and persisted when the flag is given). Either can be None on read failure."""
    if a.baseline_file and os.path.exists(a.baseline_file):
        try:
            d = json.load(open(a.baseline_file))
            return d.get("balance"), d.get("open_cost")
        except Exception:
            pass
    bal, oc = _balance(c), _open_cost(c)
    if a.baseline_file and bal is not None and oc is not None:
        try:
            with open(a.baseline_file, "w") as fh:
                json.dump({"balance": bal, "open_cost": oc,
                           "ts": datetime.now(timezone.utc).isoformat()}, fh)
        except OSError:
            pass
    return bal, oc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since")
    ap.add_argument("--since-file")
    ap.add_argument("--baseline-file")
    ap.add_argument("--hours", type=float, default=2.0)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--stop-file-on-alarm", action="store_true")
    a = ap.parse_args()

    since = _resolve_since(a)
    c = _client()
    start_bal, start_open = _resolve_baseline(a, c)
    seen = set()
    stop_written = False
    t_end = None if a.hours <= 0 else time.monotonic() + a.hours * 3600
    print(f"FILL WATCH since {since} | window "
          + ("FOREVER" if t_end is None else f"{a.hours}h")
          + " | baseline "
          + (f"bal ${start_bal:.4f} open ${start_open:.4f}"
             if start_bal is not None and start_open is not None else "UNKNOWN")
          + (" | auto-STOP ARMED (realized_est)" if a.stop_file_on_alarm else ""))

    while True:
        now = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
        win, new, realized = poll(c, since, seen)
        bal = _balance(c)
        open_now = _open_cost(c)
        bal_delta = (bal - start_bal) if (bal is not None and start_bal is not None) else None
        realized_est = None
        if (bal is not None and open_now is not None
                and start_bal is not None and start_open is not None):
            realized_est = (bal + open_now) - (start_bal + start_open)
        for f in new:
            print(f"  {now} FILL {f['ticker']} {f['action']} {f['side']} "
                  f"{float(f['count_fp']):.0f}ct @{(float(f.get('yes_price_dollars') or 0) if f['side']=='yes' else float(f.get('no_price_dollars') or 0)):.2f} "
                  f"taker={f.get('is_taker')}")
        print(f"  {now} fills={len(win)} new={len(new)} "
              f"fills_net={realized:+.2f} "
              + (f"balance_delta={bal_delta:+.4f} " if bal_delta is not None else "balance=? ")
              + (f"realized_est={realized_est:+.4f}" if realized_est is not None
                 else "realized_est=?"))
        # ALARM banner on ANY gauge (informational; raw gauges include open-cost noise)
        trips = [x for x in (bal_delta, realized, realized_est)
                 if x is not None and x <= -ALARM_USD]
        if trips:
            print(f"  !!! ALARM {now}: worst gauge {min(trips):+.2f} <= -${ALARM_USD:.0f} — "
                  f"consider STOP (systemctl stop polymarket-maker-kalshi-ws or touch STOP)")
        # STOP write on the TRUE gauge only (A4i): never on raw gauges — a benign 100ct
        # open fill moves balance ~-$29 (measured 09-01) and must not halt the bot.
        if (a.stop_file_on_alarm and not stop_written
                and realized_est is not None and realized_est <= -ALARM_USD):
            _write_stop(realized_est, datetime.now(timezone.utc).isoformat())
            stop_written = True
        if a.once or (t_end is not None and time.monotonic() >= t_end):
            break
        time.sleep(a.interval)
    print("FILL WATCH window closed.")


if __name__ == "__main__":
    main()
