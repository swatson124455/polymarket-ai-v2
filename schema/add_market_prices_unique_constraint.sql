-- Migration: Add unique constraint on market_prices (market_id, token_id, timestamp)
-- for idempotent bulk insert (ON CONFLICT DO NOTHING).
-- Run once. Safe to re-run: dedupe keeps one row per key; constraint creation is IF NOT EXISTS.

-- Step 1: Remove duplicates (keep row with smallest id per market_id, token_id, timestamp)
DELETE FROM market_prices a
USING market_prices b
WHERE a.market_id = b.market_id
  AND a.token_id = b.token_id
  AND a.timestamp = b.timestamp
  AND a.id > b.id;

-- Step 2: Add unique constraint (PostgreSQL 15+ can use ADD CONSTRAINT ... UNIQUE ... NOT VALID then VALIDATE)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'uq_market_prices_market_token_timestamp'
  ) THEN
    ALTER TABLE market_prices
    ADD CONSTRAINT uq_market_prices_market_token_timestamp
    UNIQUE (market_id, token_id, timestamp);
  END IF;
END $$;
