-- P4: Track empty price-fetch responses so we can skip/deprioritize those markets.
-- Reduces API waste and bias from re-fetching the same failing markets every cycle.

ALTER TABLE markets
ADD COLUMN IF NOT EXISTS price_fetch_attempts INTEGER DEFAULT 0,
ADD COLUMN IF NOT EXISTS last_price_fetch_empty TIMESTAMP WITH TIME ZONE;

COMMENT ON COLUMN markets.price_fetch_attempts IS 'P4: Incremented when price history API returns empty for this market. Reset on success.';
COMMENT ON COLUMN markets.last_price_fetch_empty IS 'P4: Last time we got an empty price response for this market.';

CREATE INDEX IF NOT EXISTS idx_markets_price_fetch_empty
ON markets (last_price_fetch_empty)
WHERE last_price_fetch_empty IS NOT NULL;
