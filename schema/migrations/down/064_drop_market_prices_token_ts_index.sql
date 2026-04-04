-- Rollback migration 064: Drop market_prices composite index
DROP INDEX IF EXISTS idx_market_prices_token_ts;
