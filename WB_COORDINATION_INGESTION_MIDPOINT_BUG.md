# WB COORDINATION — vendored data_ingestion has the strategy-2 midpoint bug

**From:** orderbook sort-order audit, 2026-07-14 (MB-priority shared-module session)
**Status:** master copy FIXED (see `base_engine/data/data_ingestion.py::_best_bid_ask`,
branch `claude/mb-orderbook-collector-fix`). WB's vendored copy NOT touched — WB owns it
(RULE FOUR / eb-splinter precedent applies to WB's splinter equally).

## The bug (in your copy)

`bots/weather/engine/base_engine/data/data_ingestion.py:3035-3060` ("Strategy 2" of
`ingest_historical_prices`): computes `midpoint_price = (bids[0] + asks[0]) / 2` from the
RAW CLOB `/book` response. The raw feed sorts **bids ASCENDING and asks DESCENDING**
(verified live 2026-07-14: gamma `bestBid/bestAsk` == `bids[-1]/asks[-1]`), so `[0]` is the
WORST level and the "midpoint" is ~0.5 regardless of the real price. The `0 <= mid <= 1`
guard passes it, and `save_market_price()` writes it into `market_prices`.

## Why it's probably dormant for WB

The strategy-2 fallback only fires when CLOB price-history returns nothing for a market,
and it runs inside `ingest_historical_prices` — which the shared `polymarket-ingestion`
service executes using the MASTER base_engine copy (fixed). The WB vendored copy only
matters if the WB process itself calls `ingest_historical_prices`. Master-side journal
check 2026-07-14: zero strategy-2 log lines ("Got current price from orderbook") in the
~2-week retention window.

## Suggested WB action (your call, your copy)

Port the master fix: module-level `_best_bid_ask(bids, asks)` (max valid bid / min valid
ask, price-only validation in (0,1), None unless both sides valid) + call-site change at
the midpoint computation. Tests: `tests/unit/test_data_ingestion_best_bid_ask.py` on the
fix branch ports directly. Or delete the fallback from your copy if WB provably never
calls this path — operator sign-off required either way.
