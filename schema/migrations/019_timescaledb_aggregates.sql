-- TimescaleDB Continuous Aggregates + Compression — Tier 4 #40-41
-- Apply on VPS PostgreSQL with TimescaleDB extension installed.
-- Prerequisites: migration 014 (hypertable conversion) must be applied first.

DO $$
BEGIN
    -- Only run if TimescaleDB is available
    IF NOT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
        RAISE NOTICE 'TimescaleDB not available — skipping aggregates/compression (no-op)';
        RETURN;
    END IF;

    -- Only run if market_prices is a hypertable
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.hypertables
        WHERE hypertable_name = 'market_prices'
    ) THEN
        RAISE NOTICE 'market_prices is not a hypertable — run migration 014 first';
        RETURN;
    END IF;

    -- ── Continuous Aggregate: hourly price OHLCV ────────────────────
    -- Pre-computed hourly rollups for dashboard and analysis
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.continuous_aggregates
        WHERE view_name = 'market_prices_hourly'
    ) THEN
        CREATE MATERIALIZED VIEW market_prices_hourly
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket('1 hour', "timestamp") AS bucket,
            market_id,
            token_id,
            first(price, "timestamp") AS open,
            max(price) AS high,
            min(price) AS low,
            last(price, "timestamp") AS close,
            count(*) AS num_samples
        FROM market_prices
        GROUP BY bucket, market_id, token_id
        WITH NO DATA;

        -- Refresh policy: update hourly data every 30 minutes
        SELECT add_continuous_aggregate_policy('market_prices_hourly',
            start_offset => INTERVAL '3 hours',
            end_offset => INTERVAL '30 minutes',
            schedule_interval => INTERVAL '30 minutes'
        );

        RAISE NOTICE 'Created market_prices_hourly continuous aggregate';
    END IF;

    -- ── Continuous Aggregate: daily PnL rollup ──────────────────────
    IF NOT EXISTS (
        SELECT 1 FROM timescaledb_information.continuous_aggregates
        WHERE view_name = 'market_prices_daily'
    ) THEN
        CREATE MATERIALIZED VIEW market_prices_daily
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket('1 day', "timestamp") AS bucket,
            market_id,
            token_id,
            first(price, "timestamp") AS open,
            max(price) AS high,
            min(price) AS low,
            last(price, "timestamp") AS close,
            count(*) AS num_samples
        FROM market_prices
        GROUP BY bucket, market_id, token_id
        WITH NO DATA;

        -- Refresh daily data every hour
        SELECT add_continuous_aggregate_policy('market_prices_daily',
            start_offset => INTERVAL '3 days',
            end_offset => INTERVAL '1 hour',
            schedule_interval => INTERVAL '1 hour'
        );

        RAISE NOTICE 'Created market_prices_daily continuous aggregate';
    END IF;

    -- ── Compression Policy ──────────────────────────────────────────
    -- Compress chunks older than 7 days (reduces storage ~90%)
    BEGIN
        ALTER TABLE market_prices SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'market_id, token_id',
            timescaledb.compress_orderby = '"timestamp" DESC'
        );

        -- Auto-compress chunks older than 7 days
        SELECT add_compression_policy('market_prices', INTERVAL '7 days');

        RAISE NOTICE 'Compression enabled: chunks > 7 days auto-compressed';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'Compression policy already exists or not supported: %', SQLERRM;
    END;

    -- ── Retention Policy ────────────────────────────────────────────
    -- Drop raw data older than 2 years (aggregates preserved)
    BEGIN
        SELECT add_retention_policy('market_prices', INTERVAL '2 years');
        RAISE NOTICE 'Retention policy: raw data drops after 2 years';
    EXCEPTION WHEN OTHERS THEN
        RAISE NOTICE 'Retention policy already exists: %', SQLERRM;
    END;

END $$;
