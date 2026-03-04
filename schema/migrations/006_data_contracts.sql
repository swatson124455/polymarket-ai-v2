-- Migration 006: Data contracts (CHECK constraints for price/size validation)
-- Run after 005. Rejects invalid data at DB level.
-- If migration fails due to existing bad data, clean first:
--   UPDATE market_prices SET price = 0.5 WHERE price IS NOT NULL AND (price < 0 OR price > 1);
--   UPDATE trades SET price = 0.5 WHERE price IS NOT NULL AND (price < 0 OR price > 1);
--   UPDATE trades SET size = 0 WHERE size IS NOT NULL AND size < 0;

-- Price range: 0-1 for binary markets (allow NULL)
ALTER TABLE market_prices ADD CONSTRAINT chk_market_prices_price_range
    CHECK (price IS NULL OR (price >= 0 AND price <= 1));

ALTER TABLE trades ADD CONSTRAINT chk_trades_price_range
    CHECK (price IS NULL OR (price >= 0 AND price <= 1));

-- Trade size must be non-negative when present
ALTER TABLE trades ADD CONSTRAINT chk_trades_size_positive
    CHECK (size IS NULL OR size >= 0);
