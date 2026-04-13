-- Revert S172 1C vacuum tuning — reset to defaults
ALTER TABLE positions RESET (
    autovacuum_vacuum_scale_factor,
    autovacuum_vacuum_threshold,
    autovacuum_analyze_scale_factor,
    autovacuum_analyze_threshold
);

ALTER TABLE markets RESET (
    autovacuum_vacuum_scale_factor,
    autovacuum_analyze_scale_factor
);

ALTER TABLE users RESET (
    autovacuum_vacuum_scale_factor,
    autovacuum_analyze_scale_factor
);

ALTER TABLE traded_markets RESET (
    autovacuum_vacuum_scale_factor,
    autovacuum_vacuum_threshold,
    autovacuum_analyze_scale_factor,
    autovacuum_analyze_threshold
);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class WHERE relname = 'market_prices_latest') THEN
        EXECUTE 'ALTER TABLE market_prices_latest RESET (
            autovacuum_vacuum_scale_factor,
            autovacuum_analyze_scale_factor
        )';
    END IF;
END$$;
