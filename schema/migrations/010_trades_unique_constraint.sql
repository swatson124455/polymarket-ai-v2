-- PD3: Unique constraint on trades to prevent duplicates.
-- Uses DO block so dedup + index creation happen atomically.
DO $$
DECLARE
    deleted_count INTEGER;
BEGIN
    -- Step 1: Remove duplicates using ctid (guaranteed unique per physical row)
    DELETE FROM trades
    WHERE ctid NOT IN (
        SELECT MIN(ctid)
        FROM trades
        WHERE market_id IS NOT NULL AND user_address IS NOT NULL
              AND token_id IS NOT NULL AND "timestamp" IS NOT NULL
        GROUP BY market_id, user_address, token_id, "timestamp"
    )
    AND market_id IS NOT NULL AND user_address IS NOT NULL
    AND token_id IS NOT NULL AND "timestamp" IS NOT NULL;
    GET DIAGNOSTICS deleted_count = ROW_COUNT;
    RAISE NOTICE 'Deduplication complete: removed % rows', deleted_count;

    -- Step 2: Drop index if partially created from prior failed attempt
    DROP INDEX IF EXISTS uq_trades_market_user_token_ts;

    -- Step 3: Create unique index
    CREATE UNIQUE INDEX uq_trades_market_user_token_ts
    ON trades (market_id, user_address, token_id, "timestamp")
    WHERE market_id IS NOT NULL AND user_address IS NOT NULL AND token_id IS NOT NULL AND "timestamp" IS NOT NULL;
    RAISE NOTICE 'Unique index created';
END $$
