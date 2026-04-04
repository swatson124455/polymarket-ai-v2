-- Migration 065: Create market_prices_latest for O(1) latest-price lookups
-- Root cause: position_manager._update_current_prices() hit 63GB market_prices table
-- every 10 seconds. Even with idx_market_prices_token_ts, 600+ token lookups
-- caused I/O saturation. This table is upserted on every price write and
-- queried instead of the historical table for current prices.

CREATE TABLE IF NOT EXISTS market_prices_latest (
    token_id    TEXT PRIMARY KEY,
    market_id   TEXT,
    price       NUMERIC NOT NULL,
    timestamp   TIMESTAMP WITHOUT TIME ZONE NOT NULL
);

-- Seed is done post-deploy via manual SQL with extended timeout.
-- The application layer upserts on every price write, so the table
-- auto-populates within minutes of deploy as new prices arrive.
-- To seed historical data: run with extended statement_timeout:
--   SET statement_timeout = '300s';
--   INSERT INTO market_prices_latest (token_id, market_id, price, timestamp)
--   SELECT DISTINCT ON (token_id) token_id, market_id, price, timestamp
--   FROM market_prices ORDER BY token_id, timestamp DESC
--   ON CONFLICT (token_id) DO UPDATE SET price = EXCLUDED.price,
--     timestamp = EXCLUDED.timestamp
--   WHERE EXCLUDED.timestamp > market_prices_latest.timestamp;
