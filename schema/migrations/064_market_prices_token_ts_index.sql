-- Migration 064: Add composite index on market_prices(token_id, timestamp DESC)
-- Root cause: position_manager._update_current_prices() runs
--   SELECT DISTINCT ON (token_id) ... ORDER BY token_id, timestamp DESC
-- every 10 seconds. Without this index, PostgreSQL does a full sort on the
-- 1.4GB+ PK index. 74 statement timeouts in 8 hours observed in S153.
-- Note: CONCURRENTLY removed because migration runner wraps in transaction.
-- Table lock is brief (~seconds on 1.4GB table). Run during low-traffic window.

CREATE INDEX IF NOT EXISTS idx_market_prices_token_ts
ON market_prices(token_id, timestamp DESC);
