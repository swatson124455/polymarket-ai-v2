"""MB acceptance-gate backtest harness (MB_REBUILD_PLAN.md §2).

"The algo proposes, the backtest disposes": candidate entry rules are replayed
against historical signals with honest fill models; a rule is gate-eligible
ONLY if it clears the PRECISE fill model (operator decision 2026-07-02).

Dual fill models (corrected data reality, salvage v1.1.0):
  precise : VWAP walk of the real per-level ladder stored in
            shadow_fills.book_snapshot (~13.8k MB signal moments; narrow, real)
  coarse  : pessimistic interpolation from orderbook_snapshots aggregate
            buckets (broad, approximate; lower-bounds the edge by design)

Shadow-only: nothing here imports an order path or mutates any table.
"""
