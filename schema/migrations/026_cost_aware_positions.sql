-- Migration 026: Cost-aware positions for intelligent exit engine (Session 45)
-- Adds entry_cost and breakeven_price columns to positions table.
-- entry_cost: total entry cost in USD (slippage + taker fee at entry time)
-- breakeven_price: price at which an active SELL would net-positive after round-trip costs

ALTER TABLE positions ADD COLUMN IF NOT EXISTS entry_cost DOUBLE PRECISION;
ALTER TABLE positions ADD COLUMN IF NOT EXISTS breakeven_price DOUBLE PRECISION;

-- Backfill existing open/reserving positions with estimated costs
-- Uses 2% cost rate (0.5% slippage + 1.5% taker fee) and 4% round-trip for breakeven
UPDATE positions
SET entry_cost = size * entry_price * 0.02,
    breakeven_price = entry_price * 1.04
WHERE status IN ('open', 'reserving')
  AND entry_cost IS NULL
  AND entry_price > 0
  AND size > 0;
