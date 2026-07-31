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


MISSING_FIELD_ROWS = [0]     # audit probe 2026-07-30: positions rows with BOTH pnl/fee fields
                             # absent in the last build() — venue-rename tripwire for the feed
                             # that ranks capital. Module counter so build() stays pure-return.


def _iso(s):
    return dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))


def build(positions, fills, now=None):
    """Pure aggregation: positions rows + fills rows -> {ticker: {...}}. Testable, no I/O."""
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
    return markets


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from maker_kalshi_client import KalshiOrderClient, API_ROOT
    c = KalshiOrderClient()
    # NO count_filter here (unlike get_positions): settled rows are exactly the receipts we want.
    positions = c._get_paginated(f"{API_ROOT}/portfolio/positions",
                                 "market_positions")["market_positions"]
    fills = c._get_paginated(f"{API_ROOT}/portfolio/fills", "fills")["fills"]
    markets = build(positions, fills)
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
