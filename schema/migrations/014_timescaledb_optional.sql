-- TimescaleDB hypertable conversion for market_prices (P4-05).
-- OPTIONAL: Only runs if TimescaleDB extension is available.
-- Supabase does not support TimescaleDB natively — this is for
-- self-hosted PostgreSQL with TimescaleDB extension installed.
-- If extension is not available, this migration is a no-op.

DO $$
BEGIN
    -- Check if TimescaleDB extension exists
    IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        -- Convert market_prices to hypertable if not already one
        IF NOT EXISTS (
            SELECT 1 FROM timescaledb_information.hypertables
            WHERE hypertable_name = 'market_prices'
        ) THEN
            PERFORM create_hypertable(
                'market_prices',
                'timestamp',
                migrate_data => true,
                chunk_time_interval => INTERVAL '7 days'
            );
            RAISE NOTICE 'market_prices converted to TimescaleDB hypertable';
        END IF;
    ELSE
        RAISE NOTICE 'TimescaleDB not available — skipping hypertable conversion (no-op)';
    END IF;
END $$;
