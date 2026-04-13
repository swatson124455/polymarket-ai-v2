-- S172 Phase 1C: Autovacuum tuning for high-dead-tuple tables.
-- positions: 19.8% dead (frequent UPDATE status='closed')
-- markets: 12.5% dead (ingestion updates)
-- users: 9.2% dead (ingestion updates)
-- traded_markets: 13.9% dead (low volume, scale factor too high)
-- market_prices_latest: ingestion hot table, needs aggressive vacuum

-- positions: vacuum at 1000 dead rows (was default 50 + 20% scale = ~1300 on 6.5K rows)
-- Tighten because positions are actively queried by all 3 bots every scan cycle.
ALTER TABLE positions SET (
    autovacuum_vacuum_scale_factor = 0.0,
    autovacuum_vacuum_threshold = 500,
    autovacuum_analyze_scale_factor = 0.0,
    autovacuum_analyze_threshold = 250
);

-- markets: vacuum at 2000 dead rows
-- 110K rows, 12.5% dead = 13.7K. Default threshold is 50 + 0.2*110K = 22K (too high).
ALTER TABLE markets SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_analyze_scale_factor = 0.01
);

-- users: vacuum at 1000 dead rows
-- 54K rows, 9.2% dead. Default threshold is 50 + 0.2*54K = 10.8K (too high for 5K dead).
ALTER TABLE users SET (
    autovacuum_vacuum_scale_factor = 0.02,
    autovacuum_analyze_scale_factor = 0.01
);

-- traded_markets: vacuum at 500 dead rows
-- 14K rows, 13.9% dead. Small table, infrequent vacuum.
ALTER TABLE traded_markets SET (
    autovacuum_vacuum_scale_factor = 0.0,
    autovacuum_vacuum_threshold = 500,
    autovacuum_analyze_scale_factor = 0.0,
    autovacuum_analyze_threshold = 250
);

-- market_prices_latest: hot ingestion table, vacuum aggressively
-- (may not exist on all deployments — IF EXISTS handles that)
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'market_prices_latest') THEN
        EXECUTE 'ALTER TABLE market_prices_latest SET (
            autovacuum_vacuum_scale_factor = 0.01,
            autovacuum_analyze_scale_factor = 0.005
        )';
    END IF;
END$$;
