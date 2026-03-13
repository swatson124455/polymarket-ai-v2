-- 049: WAL/autovacuum tuning for high-write append-only tables.
-- Absolute thresholds replace scale-factor-based defaults.
-- At 500 trades/day + thousands of price updates, the default
-- scale_factor=0.2 delays vacuum too long.

ALTER TABLE market_prices SET (
    autovacuum_vacuum_scale_factor = 0.0,
    autovacuum_vacuum_threshold = 10000,
    autovacuum_analyze_scale_factor = 0.0,
    autovacuum_analyze_threshold = 5000
);

ALTER TABLE prediction_log SET (
    autovacuum_vacuum_scale_factor = 0.0,
    autovacuum_vacuum_threshold = 5000,
    autovacuum_analyze_scale_factor = 0.0,
    autovacuum_analyze_threshold = 2500
);

ALTER TABLE decision_events SET (
    autovacuum_vacuum_scale_factor = 0.0,
    autovacuum_vacuum_threshold = 5000,
    autovacuum_analyze_scale_factor = 0.0,
    autovacuum_analyze_threshold = 2500
);

ALTER TABLE trade_events SET (
    autovacuum_vacuum_scale_factor = 0.0,
    autovacuum_vacuum_threshold = 5000,
    autovacuum_analyze_scale_factor = 0.0,
    autovacuum_analyze_threshold = 2500
);

-- paper_trades: lower threshold since it's smaller but actively queried
ALTER TABLE paper_trades SET (
    autovacuum_vacuum_scale_factor = 0.0,
    autovacuum_vacuum_threshold = 1000,
    autovacuum_analyze_scale_factor = 0.0,
    autovacuum_analyze_threshold = 500
);
