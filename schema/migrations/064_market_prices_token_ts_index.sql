-- Migration 064: Add composite index on market_prices(token_id, timestamp DESC)
-- Root cause: position_manager._update_current_prices() runs
--   SELECT DISTINCT ON (token_id) ... ORDER BY token_id, timestamp DESC
-- every 10 seconds. Without this index, PostgreSQL does a full sort on the
-- 1.4GB+ PK index. 74 statement timeouts in 8 hours observed in S153.
-- CONCURRENTLY avoids locking the table during index build.

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_market_prices_token_ts
ON market_prices(token_id, timestamp DESC);
