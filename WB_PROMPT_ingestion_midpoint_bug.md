# WB SESSION PROMPT — vendored data_ingestion strategy-2 worst-of-book midpoint bug

**Origin:** EB-session cross-bot data audit, 2026-07-14 (operator-authorized). The master
`base_engine/data/data_ingestion.py` had a worst-of-book midpoint bug (fixed on branch
`claude/mb-orderbook-collector-fix`, commit `d2f5c2f`). **Your vendored copy has the same
bug** and is yours to fix (WB splinter owns its tree; the EB session did NOT cross-fix).

## The bug (in your copy)

`bots/weather/engine/base_engine/data/data_ingestion.py:3035-3060` — "Strategy 2" of
`ingest_historical_prices` computes `midpoint_price = (bids[0] + asks[0]) / 2` from the
RAW CLOB `/book` response. The raw feed sorts **bids ASCENDING, asks DESCENDING**, so
`[0]` is the WORST level (typically 0.001 / 0.999) and the "midpoint" is ~0.5 regardless
of the real price. The `0 <= mid <= 1` guard passes it and `save_market_price()` writes
the phantom into `market_prices` (consumed by position_manager mark-to-market +
prediction_engine). Verified live 2026-07-14: gamma `bestBid/bestAsk` == `bids[-1]/asks[-1]`.

## Likely dormant for WB — but verify

The strategy-2 fallback only fires when CLOB price-history returns nothing for a market,
and the shared `polymarket-ingestion` service runs the MASTER copy of this code, not your
vendored one. Your vendored copy only executes if the WB process itself calls
`ingest_historical_prices`. Master-side journal check 2026-07-14: zero "Got current price
from orderbook" lines in the ~2-week retention window. So this is almost certainly a
latent bug, not an active one — fix it before it can fire, don't panic.

## Fix (port the master fix — it's small and tested)

Master `d2f5c2f` added a module-level helper and changed the one call site:
```python
def _best_bid_ask(bids, asks):
    """Best (highest) bid, best (lowest) ask from raw CLOB /book levels, order-
    independent. Ignores non-numeric prices and prices outside (0,1). Returns
    None unless BOTH sides have a valid level."""
    def _prices(levels):
        out = []
        for lv in levels if isinstance(levels, list) else []:
            try:
                p = float(lv.get("price"))
            except (AttributeError, TypeError, ValueError):
                continue
            if 0.0 < p < 1.0:
                out.append(p)
        return out
    b, a = _prices(bids), _prices(asks)
    if not b or not a:
        return None
    return max(b), min(a)
```
Call site: replace `best_bid = float(bids[0]...); best_ask = float(asks[0]...)` with
`_ba = _best_bid_ask(bids, asks); if _ba is None: raise ValueError(...); best_bid, best_ask = _ba`.
Tests port directly: `tests/unit/test_data_ingestion_best_bid_ask.py` on the fix branch.
Alternatively, if WB provably never calls `ingest_historical_prices`, delete the strategy-2
fallback from your copy instead. Operator sign-off either way per your splinter protocol.

**Do NOT touch the master copy or the shared ingestion service** — that's the MB/shared
lane. This prompt is scoped strictly to your vendored `bots/weather/engine/**` tree.
