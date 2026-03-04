-- Migration 005: Bot improvements (mirror_performance, execution_quality, indexes)
-- Optional: Run after 004. Some indexes require learning_patterns (from supabase_schema).

-- Mirror performance tracking
CREATE TABLE IF NOT EXISTS mirror_performance (
    id BIGSERIAL PRIMARY KEY,
    trader_address VARCHAR NOT NULL,
    market_id VARCHAR NOT NULL,
    category VARCHAR,
    side VARCHAR,
    mirrored_at TIMESTAMP NOT NULL,
    execution_delay_seconds FLOAT,
    outcome BOOLEAN,
    pnl FLOAT,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_mirror_perf_trader ON mirror_performance(trader_address);
CREATE INDEX IF NOT EXISTS idx_mirror_perf_outcome ON mirror_performance(trader_address, outcome) WHERE outcome IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_mirror_perf_market ON mirror_performance(market_id) WHERE outcome IS NULL;

-- Execution quality tracking
CREATE TABLE IF NOT EXISTS execution_quality (
    id BIGSERIAL PRIMARY KEY,
    bot_name VARCHAR NOT NULL,
    market_id VARCHAR NOT NULL,
    expected_price FLOAT NOT NULL,
    actual_price FLOAT NOT NULL,
    slippage FLOAT NOT NULL,
    size FLOAT NOT NULL,
    executed_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_exec_quality_bot ON execution_quality(bot_name, executed_at DESC);
CREATE INDEX IF NOT EXISTS idx_exec_quality_market ON execution_quality(market_id);

-- Signal quality tracking
CREATE TABLE IF NOT EXISTS signal_quality (
    id BIGSERIAL PRIMARY KEY,
    signal_source VARCHAR NOT NULL,
    market_id VARCHAR NOT NULL,
    predicted_outcome VARCHAR NOT NULL,
    actual_outcome VARCHAR,
    confidence FLOAT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    resolved_at TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_signal_quality_source ON signal_quality(signal_source, resolved_at) WHERE actual_outcome IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_signal_quality_market ON signal_quality(market_id) WHERE actual_outcome IS NULL;

-- Momentum false signals
CREATE TABLE IF NOT EXISTS momentum_false_signals (
    id BIGSERIAL PRIMARY KEY,
    market_id VARCHAR NOT NULL,
    signal_time TIMESTAMP NOT NULL,
    momentum_value FLOAT NOT NULL,
    was_false BOOLEAN NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_momentum_false_market ON momentum_false_signals(market_id, signal_time DESC);

-- Confidence calibration
CREATE TABLE IF NOT EXISTS confidence_calibration (
    id BIGSERIAL PRIMARY KEY,
    bot_name VARCHAR NOT NULL,
    confidence_bucket FLOAT NOT NULL,
    predicted_confidence FLOAT NOT NULL,
    actual_outcome BOOLEAN NOT NULL,
    market_id VARCHAR,
    created_at TIMESTAMP DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_conf_calib_bot_bucket ON confidence_calibration(bot_name, confidence_bucket);
