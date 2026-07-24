#!/usr/bin/env python3
"""KALSHI MARKET SCORECARD -- READ-ONLY. Per-market: are we IN it, and are we EARNING or BLEEDING?

Answers the two questions the bot could never see:
  IN?    -- are we resting BOTH sides, and is the book two-sided at Target Size (R3)?
  EARN?  -- our R4 qualifying SHARE of the reward (DF^N-weighted, normalized) -> est reward $/day.
  BLEED? -- realized fill P&L per market (venue realized_pnl_dollars = the adverse-selection cost).

The est-reward column is a MODEL (over-predicts 2-6x per M7) until calibrated against actual
period-close credits -- that is the second tool. This one gives the LIVE picture. GETs only.
"""
import sys, datetime as dt
from collections import defaultdict
import kalshi_attribution_ledger as L

TICK = 0.01


def _f(x):
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def qualifying_share(bids, our_price, our_size, target, df):
    """R4: reference = highest bid (<1.0); walk down accumulating size to Target; score = DF^N*size,
    N = ticks from reference; return (our normalized share, side_qualifies)."""
    bids = sorted(((p, s) for p, s in bids if s > 0), key=lambda x: -x[0])
    if not bids or bids[0][0] >= 1.0:
        return 0.0, False
    ref = bids[0][0]
    cum = total = 0.0
    lowest_q = ref
    for price, size in bids:
        n = round((ref - price) / TICK)
        total += (df ** n) * size
        cum += size
        lowest_q = price
        if cum >= target:
            break
    if cum < target:
        return 0.0, False                       # book can't reach Target on this side -> $0 for everyone
    our = 0.0
    if our_price is not None and our_price >= lowest_q - 1e-9:  # our order is inside the qualifying set
        our = (df ** round((ref - our_price) / TICK)) * our_size
    return (our / total if total > 0 else 0.0), True


def main():
    now = dt.datetime.now(dt.timezone.utc)
    # our resting orders
    orders = L.get(L.P + "/portfolio/orders?status=resting").get("orders") or []
    ours = defaultdict(lambda: {"yes": None, "no": None})   # ticker -> side -> (price$, size)
    for o in orders:
        t = o.get("ticker")
        if not t:
            continue
        side = o.get("outcome_side")            # "yes" / "no" (the side we quote)
        px = _f(o.get("yes_price_dollars") if side == "yes" else o.get("no_price_dollars"))  # dollars, no /100
        sz = _f(o.get("remaining_count_fp"))
        cur = ours[t][side]
        ours[t][side] = (px, sz + (cur[1] if cur else 0.0)) if px > 0 else cur
    # programs (pool $/day, target, df) for the markets we're in
    progs = L.get(L.P + "/incentive_programs?status=active&limit=10000").get("incentive_programs") or []
    prog = {}
    for p in progs:
        t = p.get("market_ticker")
        if not t:
            continue
        try:
            st = dt.datetime.fromisoformat(p["start_date"].replace("Z", "+00:00"))
            en = dt.datetime.fromisoformat(p["end_date"].replace("Z", "+00:00"))
            days = max((en - st).total_seconds() / 86400, 1 / 24)
        except Exception:
            days = 1.0
        prog[t] = {"usd_day": (_f(p.get("period_reward")) / 10000) / days,
                   "target": _f(p.get("target_size_fp")) or 1000.0,
                   "df": (_f(p.get("discount_factor_bps")) / 10000) or 0.5}
    # realized fill P&L per market (the cost)
    real = {}
    for pos in (L.get(L.P + "/portfolio/positions").get("market_positions") or []):
        real[pos.get("ticker")] = _f(pos.get("realized_pnl_dollars"))

    mkts = sorted(ours)
    print(f"MARKET SCORECARD @ {now.strftime('%Y-%m-%dT%H:%MZ')}  (markets we rest in: {len(mkts)})")
    print(f"{'market':<30}{'rest':>6}{'book2s':>7}{'ourShr':>7}{'estRwd$/d':>10}{'fillP&L$':>9}  signal")
    tot_est = tot_pnl = 0.0
    by_series = defaultdict(lambda: [0.0, 0.0, 0])
    for t in mkts:
        pr = prog.get(t)
        ob = L.get(L.P + f"/markets/{t}/orderbook").get("orderbook_fp", {}) or {}
        yb = [(_f(p), _f(s)) for p, s in (ob.get("yes_dollars") or [])]
        nb = [(_f(p), _f(s)) for p, s in (ob.get("no_dollars") or [])]
        tgt = pr["target"] if pr else 1000.0
        df = pr["df"] if pr else 0.5
        oy, on = ours[t]["yes"], ours[t]["no"]
        ys, yq = qualifying_share(yb, oy[0] if oy else None, oy[1] if oy else 0.0, tgt, df)
        ns, nq = qualifying_share(nb, on[0] if on else None, on[1] if on else 0.0, tgt, df)
        book_2s = yq and nq
        our_snap = (ys + ns) / 2 if book_2s else 0.0            # R4: excluded if not two-sided
        est = (pr["usd_day"] * our_snap) if pr else 0.0
        pnl = real.get(t, 0.0)
        rest = ("Y" if oy else "-") + ("Y" if on else "-")
        sig = "EARNING" if (book_2s and est > 0) else ("$0-not-2sided" if not book_2s else "in-not-qual")
        tot_est += est
        tot_pnl += pnl
        s = t.split("-")[0]
        by_series[s][0] += est
        by_series[s][1] += pnl
        by_series[s][2] += 1
        print(f"{t[-30:]:<30}{rest:>6}{('Y' if book_2s else 'N'):>7}{our_snap*100:6.2f}%{est:10.2f}{pnl:9.2f}  {sig}")
    print(f"{'TOTAL':<30}{'':>6}{'':>7}{'':>7}{tot_est:10.2f}{tot_pnl:9.2f}")
    print("\n=== by series: est reward $/day (model) vs realized fill P&L (measured) ===")
    for s, v in sorted(by_series.items(), key=lambda x: -x[1][0]):
        net = v[0] + v[1]
        print(f"  {s:<26} est_reward ${v[0]:6.2f}/d  fill_pnl ${v[1]:+7.2f}  ~net ${net:+7.2f}  ({v[2]} mkts)")
    print("\n[estRwd = R1 pool x our R4 share; MODEL, over-predicts 2-6x until calibrated vs actual "
          "period-close credits. fillP&L = venue realized_pnl_dollars = the adverse-selection cost, MEASURED. "
          "'~net' is model-reward minus measured-cost -- a SIGNAL, not a receipt.]")


if __name__ == "__main__":
    sys.exit(main())
