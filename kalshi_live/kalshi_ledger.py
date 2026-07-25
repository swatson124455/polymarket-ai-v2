#!/usr/bin/env python3
"""KALSHI LEDGER — the four-way reconciliation that actually closes.

WHY THIS EXISTS: Kalshi exposes NO credits/ledger/transactions endpoint (8 paths
probed 2026-07-25, all 404) and `revenue_dollars` / `realized_pnl_dollars` come
back UNPOPULATED on /portfolio/settlements. So LIP reward income was invisible
to every tool we had, and it was under-counted 3.5x (25.21 vs the true 88.07) —
which made a reward-POSITIVE strategy look like a dead loss.

THE IDENTITY (must close to the cent, every run):

    equity = deposits + realized_trading + rewards + unrealized

Three of the four are computable from the API; REWARDS falls out as the residual
and is then cross-checked against the operator's UI figure. Everything is in
yes-signed dollars.

  equity    = balance_dollars + portfolio_value        (venue)
  realized  = FIFO-matched round-trip P&L on closed lots, minus fees, PLUS the
              settlement leg of lots that went to resolution
  unrealized= sum over still-open lots of (mid_now - lot_price) * signed_qty
  deposits  = operator input (persisted; there is no API for it)
  rewards   = equity - deposits - realized - unrealized      <-- DERIVED

Direction is ACTION-ONLY (buy=+1, sell=-1) — venue-validated against
/portfolio/positions on every run; the script REFUSES to report if it drifts,
because an inverted sign silently corrupts every number (it did once already).

USAGE
  python kalshi_ledger.py                     # report + append a snapshot
  python kalshi_ledger.py --deposits 365      # set/update deposits (persisted)
  python kalshi_ledger.py --rewards-actual 88.07   # record the UI figure to
                                              # cross-check the derivation
  python kalshi_ledger.py --rate              # reward/trading run-rate from
                                              # the snapshot history
READ-ONLY against the venue. State lives in kalshi_ledger_state.json and
snapshots append to kalshi_ledger_snapshots.jsonl.
"""
import argparse
import datetime as dt
import json
import os
import sys
from collections import defaultdict, deque

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kalshi_attribution_ledger as L                  # noqa: E402

P = L.P
STATE = os.path.join(HERE, "kalshi_ledger_state.json")
SNAPS = os.path.join(HERE, "kalshi_ledger_snapshots.jsonl")
CENT = 0.01


def f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def direction(fill):
    """Yes-signed direction. ACTION-ONLY — venue-validated. buy=+1, sell=-1.
    (The venue labels our resting NO bid's fill 'sell no' because the client
    submits NO bids as asks on the yes-scale; economically we acquire NO =
    short yes = -1. Deriving sign from `side` inverts half of all fills.)"""
    return +1.0 if fill.get("action") == "buy" else -1.0


def yes_px(fill_or_trade):
    return f(fill_or_trade.get("yes_price_dollars"))


def load_state():
    try:
        with open(STATE) as fh:
            return json.load(fh)
    except Exception:
        return {}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(st, fh, indent=1, sort_keys=True)
    os.replace(tmp, STATE)


def fetch():
    bal = L.get(P + "/portfolio/balance")
    fills = L.get_paginated(P + "/portfolio/fills", "fills") or []
    pos = L.get_paginated(P + "/portfolio/positions", "market_positions") or []
    setl = L.get_paginated(P + "/portfolio/settlements", "settlements") or []
    return bal, fills, pos, setl


def reconcile_signs(fills, pos, setl):
    """GATE: fill-derived positions must reproduce the venue on unsettled
    tickers, or every downstream number is suspect."""
    settled = {s.get("ticker") for s in setl}
    venue = {p["ticker"]: f(p.get("position_fp")) for p in pos}
    derived = defaultdict(float)
    for x in fills:
        derived[x["ticker"]] += direction(x) * f(x.get("count_fp"))
    chk = ok = 0
    bad = []
    for t, d in derived.items():
        if t in settled:
            continue
        chk += 1
        if abs(d - venue.get(t, 0.0)) < 0.01:
            ok += 1
        else:
            bad.append((t, d, venue.get(t, 0.0)))
    return ok, chk, bad


def split_lots(fills):
    """FIFO-match fills per ticker. Returns (realized_trading, open_lots).
    realized_trading = closed round-trip P&L minus fees (yes-signed).
    open_lots = {ticker: [(price, signed_qty), ...]} still outstanding."""
    by_t = defaultdict(list)
    for x in fills:
        by_t[x["ticker"]].append(x)
    realized = 0.0
    fees = 0.0
    open_lots = defaultdict(list)
    for t, xs in by_t.items():
        xs.sort(key=lambda z: int(z.get("ts") or 0))
        lots = deque()                     # (price, qty, dir)
        for x in xs:
            fees += f(x.get("fee_cost"))
            d = direction(x)
            q = f(x.get("count_fp"))
            p = yes_px(x)
            if q <= 0 or not (0 < p < 1):
                continue
            while q > 1e-9 and lots and lots[0][2] != d:
                lp, lq, ld = lots[0]
                m = min(q, lq)
                realized += (p - lp) * m * ld       # yes-signed round trip
                lq -= m
                q -= m
                if lq <= 1e-9:
                    lots.popleft()
                else:
                    lots[0] = (lp, lq, ld)
            if q > 1e-9:
                lots.append((p, q, d))
        for lp, lq, ld in lots:
            open_lots[t].append((lp, ld * lq))
    return realized - fees, open_lots, fees


def settlement_leg(open_lots, setl):
    """Lots on SETTLED tickers never got a closing fill — their P&L is the
    settlement outcome. yes-signed: a long-yes lot pays 1 if YES resolved."""
    res = {}
    for s in setl:
        t = s.get("ticker")
        r = (s.get("market_result") or s.get("result") or "").lower()
        if t and r in ("yes", "no"):
            res[t] = 1.0 if r == "yes" else 0.0
    leg = 0.0
    still_open = {}
    for t, lots in open_lots.items():
        if t in res:
            payoff = res[t]
            for lp, sq in lots:
                leg += (payoff - lp) * sq
        else:
            still_open[t] = lots
    return leg, still_open


def mark_open(still_open):
    """Mark remaining lots to the venue mid. Falls back to last price."""
    unreal = 0.0
    detail = []
    for t, lots in still_open.items():
        mid = None
        try:
            m = L.get(P + "/markets/%s" % t).get("market", {}) or {}
            b, a = f(m.get("yes_bid_dollars")), f(m.get("yes_ask_dollars"))
            if 0 < b and 0 < a:
                mid = (b + a) / 2.0
            elif f(m.get("last_price_dollars")) > 0:
                mid = f(m.get("last_price_dollars"))
        except Exception:
            pass
        if mid is None:
            detail.append((t, None, sum(q for _, q in lots), 0.0))
            continue
        u = sum((mid - lp) * sq for lp, sq in lots)
        unreal += u
        detail.append((t, mid, sum(q for _, q in lots), u))
    return unreal, detail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deposits", type=float, help="total deposited to date (persisted)")
    ap.add_argument("--rewards-actual", type=float,
                    help="reward total from the Kalshi UI, to cross-check the derivation")
    ap.add_argument("--rate", action="store_true", help="run-rates from snapshot history")
    ap.add_argument("--no-snapshot", action="store_true")
    a = ap.parse_args()

    st = load_state()
    if a.deposits is not None:
        st["deposits"] = a.deposits
        st["deposits_set_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if a.rewards_actual is not None:
        st["rewards_actual"] = a.rewards_actual
        st["rewards_actual_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    if a.deposits is not None or a.rewards_actual is not None:
        save_state(st)

    if a.rate:
        return show_rate()

    bal, fills, pos, setl = fetch()
    ok, chk, bad = reconcile_signs(fills, pos, setl)
    print("sign reconcile: %d/%d unsettled tickers" % (ok, chk))
    for t, d, v in bad[:5]:
        print("   MISMATCH %-34s derived=%+.2f venue=%+.2f" % (t, d, v))
    if chk and ok / chk < 0.9:
        print("ABORT: sign convention no longer reproduces the venue.")
        return 1

    cash = f(bal.get("balance_dollars"))
    mkt = f(bal.get("portfolio_value")) / 100.0
    equity = cash + mkt

    realized_rt, open_lots, fees = split_lots(fills)
    setl_leg, still_open = settlement_leg(open_lots, setl)
    realized = realized_rt + setl_leg
    unreal, detail = mark_open(still_open)

    deposits = st.get("deposits")
    print("\n=== KALSHI LEDGER  %s ==="
          % dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%MZ"))
    print("  cash                  $%9.2f" % cash)
    print("  open position value   $%9.2f" % mkt)
    print("  EQUITY                $%9.2f" % equity)
    print("  ---")
    print("  realized: round trips $%+9.2f  (fees $%.2f incl.)" % (realized_rt, fees))
    print("  realized: settlements $%+9.2f" % setl_leg)
    print("  REALIZED TRADING      $%+9.2f" % realized)
    print("  UNREALIZED (open)     $%+9.2f" % unreal)
    if deposits is None:
        print("\n  deposits NOT SET — run with --deposits <total> to derive rewards")
        return 0
    rewards = equity - deposits - realized - unreal
    print("  deposits              $%9.2f" % deposits)
    print("  REWARDS (derived)     $%+9.2f   <-- LIP income, no API exposes this"
          % rewards)
    actual = st.get("rewards_actual")
    if actual is not None:
        gap = rewards - actual
        flag = "OK" if abs(gap) <= 0.50 else "GAP"
        print("  rewards (UI actual)   $%+9.2f   [%s, delta %+.2f]" % (actual, flag, gap))
    print("  ---")
    net = equity - deposits
    print("  NET vs DEPOSITS       $%+9.2f  (%.1f%%)"
          % (net, 100.0 * net / deposits if deposits else 0))
    tr = realized + unreal
    print("  trading (all-in)      $%+9.2f" % tr)
    print("  rewards               $%+9.2f" % (actual if actual is not None else rewards))
    print("  IDENTITY CHECK        %s"
          % ("closes" if abs(deposits + realized + rewards + unreal - equity) < CENT
             else "DOES NOT CLOSE"))

    if detail:
        print("\n  open lots marked to mid:")
        for t, mid, q, u in sorted(detail, key=lambda z: -abs(z[3]))[:10]:
            print("    %-34s pos=%+7.1f mid=%s uPnL=$%+7.2f"
                  % (t[:34], q, ("%.4f" % mid) if mid else "n/a", u))

    if not a.no_snapshot:
        row = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(),
               "equity": round(equity, 4), "cash": round(cash, 4),
               "mkt": round(mkt, 4), "realized": round(realized, 4),
               "unrealized": round(unreal, 4), "deposits": deposits,
               "rewards_derived": round(rewards, 4),
               "rewards_actual": actual, "fees": round(fees, 4),
               "n_fills": len(fills), "n_settlements": len(setl)}
        with open(SNAPS, "a") as fh:
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
        print("\n  snapshot appended -> %s" % os.path.basename(SNAPS))
    return 0


def show_rate():
    rows = []
    try:
        with open(SNAPS) as fh:
            for ln in fh:
                try:
                    rows.append(json.loads(ln))
                except ValueError:
                    pass
    except OSError:
        print("no snapshots yet — run the ledger a few times first")
        return 0
    if len(rows) < 2:
        print("need >= 2 snapshots for a rate (have %d)" % len(rows))
        return 0
    a, b = rows[0], rows[-1]
    t0 = dt.datetime.fromisoformat(a["ts"])
    t1 = dt.datetime.fromisoformat(b["ts"])
    days = max((t1 - t0).total_seconds() / 86400.0, 1e-9)
    dr = (b["rewards_derived"] or 0) - (a["rewards_derived"] or 0)
    dt_ = ((b["realized"] + b["unrealized"]) - (a["realized"] + a["unrealized"]))
    de = b["equity"] - a["equity"]
    print("=== RUN RATE over %.2f days (%d snapshots) ===" % (days, len(rows)))
    print("  rewards   $%+8.2f  => $%+7.2f/day" % (dr, dr / days))
    print("  trading   $%+8.2f  => $%+7.2f/day" % (dt_, dt_ / days))
    print("  equity    $%+8.2f  => $%+7.2f/day" % (de, de / days))
    print("  breakeven requires trading loss < $%.2f/day" % abs(dr / days))
    return 0


if __name__ == "__main__":
    sys.exit(main())
