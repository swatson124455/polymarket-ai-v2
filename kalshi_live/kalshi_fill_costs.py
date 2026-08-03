#!/usr/bin/env python3
"""PER-MARKET FILL-COST FEED — writes kalshi_fill_costs.json for the capital-aware ranking
telemetry (kalshi_capital_rank.py). READ-ONLY against the venue; run ad hoc or on a timer.

SOURCE = the venue's own per-market attribution: /portfolio/positions carries
`realized_pnl_dollars` and `fees_paid_dollars` PER TICKER (probe-read 2026-07-29: both fields
present on live rows). That is a receipt, not a model. /portfolio/fills supplies the first/last
activity timestamps that turn a lifetime cost into a $/day rate.

UNVERIFIED (and exactly what the telemetry-first period exists to validate): whether
`realized_pnl_dollars` is net of fees. Both raw fields are stored per market so the answer is
recoverable from the feed file itself once a settlement receipt can be cross-checked.

cost_usd_day = max(0, -realized_pnl) / max(active_days, 1.0)
  - losses only: a market that MADE money on fills gets cost 0, not a bonus — fill profits are
    not the maker thesis and must not inflate a rank (receipts for rewards do that, on credit).
  - active_days floored at 1: a market touched once for ten minutes must not annualize a blip
    into a monster rate.

Usage (VPS):  cd /opt/pa2-maker-kalshi-live && set -a && . ./live.env && set +a && \
              venv/bin/python kalshi_fill_costs.py [out_path]
"""
import datetime as dt
import json
import os
import sys

SCHEMA = 1          # must match kalshi_capital_rank.SCHEMA (reader fails open on mismatch)


SETTLED_REPLAY_FAILS = [0]   # defect 14: the settled-market fill replay faulted, so those
                             # markets fell back to ABSENT (the pre-fix behaviour) rather than
                             # being priced on a partial tape. Counted, never silent.
MISSING_FIELD_ROWS = [0]     # audit probe 2026-07-30: positions rows with BOTH pnl/fee fields
                             # absent in the last build() — venue-rename tripwire for the feed
                             # that ranks capital. Module counter so build() stays pure-return.


def _iso(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def build(positions, fills, now=None, settlements=None):
    """Pure aggregation: positions rows + fills rows (+ settlements) -> {ticker: {...}}.

    `settlements` is OPTIONAL and additive — the 3-arg signature every existing caller and test
    uses is unchanged, and omitting it reproduces the old output exactly.

    WHY IT EXISTS (defect 14, root-fixed 2026-08-03). This feed used to be built from
    /portfolio/positions alone, and that endpoint NEVER returns a SETTLED market: measured
    read-only 2026-08-03T12:39:55Z against the live account, 0 of 129 settled tickers appear
    under unfiltered, count_filter=total_traded OR count_filter=position. So a cost feed meant
    to price completed round trips was blind to every completed round trip — and it is the feed
    the net-EV calibration is built on.
    A settled market's realized P&L is recoverable without it: settlement revenue plus the
    position-aware cash of that ticker's fills. Both halves are canon —
    `revenue` is CENTS (matches the value/counts payout model 127/127 divided by 100, measured
    2026-08-03T02:30:57Z) and the fill model is kalshi_attribution_ledger.replay_fills, the
    same validated model the cash recorder was moved onto for defect 13.
    NOT used: the settlement row's yes/no_total_cost_dollars. Those are GROSS LIFETIME costs on
    BOTH legs (KXTRUMPENDORSEMENTS-26AUG01-A5 carries $141.15 against 125.00 yes vs 124.89 no —
    almost entirely paired), so subtracting them would manufacture enormous phantom losses."""
    now = now or dt.datetime.now(dt.timezone.utc)
    span = {}                                        # ticker -> [first_ts, last_ts]
    for f in fills:
        t = f.get("ticker")
        ts = f.get("created_time")
        if not t or not ts:
            continue
        try:
            w = _iso(ts)
        except ValueError:
            continue
        lo, hi = span.get(t, (w, w))
        span[t] = (min(lo, w), max(hi, w))
    markets = {}
    missing_fields = 0        # audit probe 2026-07-30: venue rename of the pnl/fee fields would
    for p in positions:       # silently zero the whole cost feed (which ranks capital!)
        t = p.get("ticker")
        if not t:
            continue
        if p.get("realized_pnl_dollars") is None or p.get("fees_paid_dollars") is None:
            missing_fields += 1     # blind-review: EITHER field renamed silently zeroes money
        try:
            realized = float(p.get("realized_pnl_dollars") or 0.0)
            fees = float(p.get("fees_paid_dollars") or 0.0)
        except (TypeError, ValueError):
            continue
        lo, hi = span.get(t, (None, None))
        days = ((hi - lo).total_seconds() / 86400.0) if lo is not None else 0.0
        days = max(days, 1.0)
        markets[t] = {"realized_pnl_usd": round(realized, 4),
                      "fees_usd": round(fees, 4),
                      "active_days": round(days, 3),
                      "cost_usd_day": round(max(0.0, -realized) / days, 4),
                      "ts": now.isoformat()}
    MISSING_FIELD_ROWS[0] = missing_fields
    # ---- SETTLED MARKETS (defect 14). Absent from `positions` under every filter, so their
    # realized P&L is reconstructed from the settlement receipt plus the position-aware cash of
    # their own fills. A live ticker is NEVER overwritten: settlement and presence are mutually
    # exclusive at the venue, and the guard makes that explicit rather than assumed.
    if settlements:
        try:
            from kalshi_attribution_ledger import replay_fills
        except Exception:
            replay_fills = None
        if replay_fills is not None:
            cash_by, fee_by = {}, {}
            try:
                events, _pos = replay_fills(fills)
                for e in events:
                    t = (e["fill"].get("ticker") or e["fill"].get("market_ticker"))
                    if not t:
                        continue
                    cash_by[t] = cash_by.get(t, 0.0) + float(e["cash"])
                    try:
                        fee_by[t] = fee_by.get(t, 0.0) + abs(float(
                            e["fill"].get("fee_cost") or 0.0))
                    except (TypeError, ValueError):
                        pass
            except Exception:
                SETTLED_REPLAY_FAILS[0] += 1
                cash_by, fee_by = {}, {}
            for s in settlements:
                t = s.get("ticker")
                if not t or t in markets:
                    continue                     # never shadow a live row
                try:
                    revenue = float(s.get("revenue") or 0.0) / 100.0   # CENTS, measured 127/127
                except (TypeError, ValueError):
                    continue
                realized = revenue + cash_by.get(t, 0.0)
                lo, hi = span.get(t, (None, None))
                days = ((hi - lo).total_seconds() / 86400.0) if lo is not None else 0.0
                days = max(days, 1.0)
                markets[t] = {"realized_pnl_usd": round(realized, 4),
                              "fees_usd": round(fee_by.get(t, 0.0), 4),
                              "active_days": round(days, 3),
                              "cost_usd_day": round(max(0.0, -realized) / days, 4),
                              "settled": 1,
                              "ts": now.isoformat()}
    return markets


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from maker_kalshi_client import KalshiOrderClient, API_ROOT
    c = KalshiOrderClient()
    # ⚠ REFUTED 2026-08-03. This line used to read "NO count_filter here (unlike
    # get_positions): settled rows are exactly the receipts we want." The premise is FALSE:
    # /portfolio/positions never returns a SETTLED market under ANY filter. Measured read-only
    # 2026-08-03T12:39:55Z against the live account, 129 settled tickers definitive from
    # /portfolio/settlements: unfiltered 40 rows / 0 settled; count_filter=total_traded 40 rows
    # / 0 settled; count_filter=position 19 rows / 0 settled.
    # So omitting count_filter buys NOTHING here, and this feed cannot see the settled markets
    # it most wants — NEW DEFECT 14, reported to the operator, unfixed. It matters because the
    # net-EV calibration (defect 7) is built on this feed; a per-market cost table blind to
    # every settled market is blind to exactly the completed round trips it is meant to price.
    # The same blind spot in the GOVERNOR feed was root-fixed 2026-08-03 (carry-forward plus a
    # settlement top-up from /portfolio/settlements); the equivalent repair here is Phase-C
    # work and is the operator's to name.
    positions = c._get_paginated(f"{API_ROOT}/portfolio/positions",
                                 "market_positions")["market_positions"]
    fills = c._get_paginated(f"{API_ROOT}/portfolio/fills", "fills")["fills"]
    try:
        settlements = c.get_settlements().get("settlements") or []
    except Exception:
        settlements = []
    markets = build(positions, fills, settlements=settlements)
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "kalshi_fill_costs.json")
    tmp = out + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"schema": SCHEMA, "markets": markets}, fh, separators=(",", ":"))
    os.replace(tmp, out)
    if MISSING_FIELD_ROWS[0]:
        print(f"WARNING {MISSING_FIELD_ROWS[0]} positions rows missing BOTH pnl/fee fields — "
              f"venue field rename? Feed may be under-costing.")
    costed = sorted(markets.items(), key=lambda kv: -kv[1]["cost_usd_day"])
    print(f"wrote {out}: {len(markets)} markets, "
          f"{sum(1 for _, v in costed if v['cost_usd_day'] > 0)} with cost > 0")
    for t, v in costed[:10]:
        print(f"  {t:42s} cost ${v['cost_usd_day']:7.4f}/day  "
              f"(realized {v['realized_pnl_usd']:+.4f}, fees {v['fees_usd']:.4f}, "
              f"{v['active_days']:.1f}d)")


if __name__ == "__main__":
    main()
