-- Supabase/PostgreSQL schema for Polymarket AI Bot
-- Full schema: run this file in Supabase SQL Editor (or psql) to create all tables.
-- Source: base_engine/data/database.py (SQLAlchemy models). Keep in sync when adding/altering tables.

-- ============================================
-- SYSTEM CONFIG (Kill Switch and key-value store)
-- ============================================
-- Kill Switch: key = 'kill_switch', value 'true'|'false'
CREATE TABLE IF NOT EXISTS system_config (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- ============================================
-- SYNC LOG (ingestion run tracking for monitoring)
-- ============================================
CREATE TABLE IF NOT EXISTS sync_log (
    id BIGSERIAL PRIMARY KEY,
    sync_type TEXT NOT NULL,
    component TEXT NOT NULL,
    started_at TIMESTAMP NOT NULL,
    completed_at TIMESTAMP,
    status TEXT NOT NULL,
    records_processed INTEGER,
    records_inserted INTEGER,
    records_failed INTEGER,
    error_message TEXT,
    metadata JSONB
);
CREATE INDEX IF NOT EXISTS idx_sync_log_started ON sync_log(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_sync_log_status ON sync_log(status);
CREATE INDEX IF NOT EXISTS idx_sync_log_component ON sync_log(component);

-- ============================================
-- SNAPSHOTS (pre-operation stats for rollback verification)
-- ============================================
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    statistics JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_created ON snapshots(created_at DESC);

-- ============================================
-- HEALING LOG (AutoHealer audit trail)
-- ============================================
CREATE TABLE IF NOT EXISTS healing_log (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP NOT NULL,
    issues_detected INTEGER NOT NULL,
    fixes_applied INTEGER NOT NULL,
    details JSONB
);
CREATE INDEX IF NOT EXISTS idx_healing_log_timestamp ON healing_log(timestamp DESC);

-- ============================================
-- POSITIONS (TradeCoordinator)
-- ============================================
-- status IN ('open', 'reserving', 'closed'). UNIQUE(bot_id, market_id, side) prevents double-reserve.
CREATE TABLE IF NOT EXISTS positions (
    id BIGSERIAL PRIMARY KEY,
    bot_id TEXT NOT NULL,
    source_bot TEXT,
    market_id TEXT NOT NULL,
    token_id TEXT,
    side TEXT NOT NULL,
    size NUMERIC DEFAULT 0,
    entry_price NUMERIC DEFAULT 0,
    current_price NUMERIC DEFAULT 0,
    unrealized_pnl NUMERIC DEFAULT 0,
    opened_at TIMESTAMPTZ DEFAULT NOW(),
    status TEXT DEFAULT 'open',
    is_paper BOOLEAN DEFAULT FALSE,
    UNIQUE(bot_id, market_id, side)
);
CREATE INDEX IF NOT EXISTS idx_positions_bot_id ON positions(bot_id);
CREATE INDEX IF NOT EXISTS idx_positions_source_bot ON positions(source_bot) WHERE source_bot IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_positions_market_id ON positions(market_id);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_is_paper ON positions(is_paper) WHERE is_paper = TRUE;

-- ============================================
-- MARKETS (from Polymarket API / ingestion)
-- ============================================
CREATE TABLE IF NOT EXISTS markets (
    id TEXT PRIMARY KEY,
    condition_id TEXT,
    question TEXT,
    description TEXT,
    slug TEXT UNIQUE,
    category TEXT,
    resolution_source TEXT,
    end_date_iso TIMESTAMP,
    image TEXT,
    active BOOLEAN,
    liquidity DOUBLE PRECISION,
    volume DOUBLE PRECISION,
    yes_token_id TEXT,
    no_token_id TEXT,
    yes_price DOUBLE PRECISION,
    no_price DOUBLE PRECISION,
    outcome_prices TEXT,
    resolved BOOLEAN DEFAULT FALSE,
    resolution TEXT,
    resolution_source_method TEXT,
    resolved_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    -- P4: Price fetch empty tracking (migration 011)
    price_fetch_attempts INTEGER DEFAULT 0,
    last_price_fetch_empty TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_markets_condition_id ON markets(condition_id);
CREATE INDEX IF NOT EXISTS idx_markets_slug ON markets(slug);
CREATE INDEX IF NOT EXISTS idx_markets_category ON markets(category);
CREATE INDEX IF NOT EXISTS idx_markets_active ON markets(active);
CREATE INDEX IF NOT EXISTS idx_markets_resolved ON markets(resolved);
CREATE INDEX IF NOT EXISTS idx_markets_yes_token ON markets(yes_token_id);
CREATE INDEX IF NOT EXISTS idx_markets_no_token ON markets(no_token_id);
CREATE INDEX IF NOT EXISTS idx_markets_active_category ON markets(active, category);
CREATE INDEX IF NOT EXISTS idx_markets_liquidity ON markets(liquidity);

-- ============================================
-- MARKET PRICES (historical price ingestion)
-- ============================================
CREATE TABLE IF NOT EXISTS market_prices (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT,
    token_id TEXT,
    price DOUBLE PRECISION,
    side TEXT,
    timestamp TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    partition_month VARCHAR(7)
);
CREATE INDEX IF NOT EXISTS idx_market_prices_market_id ON market_prices(market_id);
CREATE INDEX IF NOT EXISTS idx_market_prices_token_id ON market_prices(token_id);
CREATE INDEX IF NOT EXISTS idx_market_prices_timestamp ON market_prices(timestamp);
CREATE INDEX IF NOT EXISTS idx_market_prices_partition_month ON market_prices(partition_month);
CREATE INDEX IF NOT EXISTS idx_prices_market_timestamp ON market_prices(market_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_prices_side ON market_prices(side);
CREATE INDEX IF NOT EXISTS idx_prices_partition ON market_prices(partition_month, market_id);
-- Unique constraint for idempotent bulk insert (ON CONFLICT DO NOTHING). Run add_market_prices_unique_constraint.sql on existing DBs.
CREATE UNIQUE INDEX IF NOT EXISTS uq_market_prices_market_token_timestamp ON market_prices(market_id, token_id, timestamp);

-- ============================================
-- TRADES
-- ============================================
CREATE TABLE IF NOT EXISTS trades (
    id TEXT PRIMARY KEY,
    market_id TEXT,
    token_id TEXT,
    user_address TEXT,
    bot_id TEXT,
    side TEXT,
    size DOUBLE PRECISION,
    price DOUBLE PRECISION,
    pnl DOUBLE PRECISION,
    entry_time TIMESTAMP,
    exit_time TIMESTAMP,
    timestamp TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    partition_month VARCHAR(7)
);
CREATE INDEX IF NOT EXISTS idx_trades_market_id ON trades(market_id);
CREATE INDEX IF NOT EXISTS idx_trades_token_id ON trades(token_id);
CREATE INDEX IF NOT EXISTS idx_trades_user_address ON trades(user_address);
CREATE INDEX IF NOT EXISTS idx_trades_bot_id ON trades(bot_id);
CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades(entry_time);
CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_partition_month ON trades(partition_month);
CREATE INDEX IF NOT EXISTS idx_trades_user_timestamp ON trades(user_address, timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_market_timestamp ON trades(market_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_trades_partition ON trades(partition_month, market_id);

-- ============================================
-- USERS (trader stats for elite detection)
-- ============================================
CREATE TABLE IF NOT EXISTS users (
    address TEXT PRIMARY KEY,
    total_profit DOUBLE PRECISION DEFAULT 0.0,
    total_volume DOUBLE PRECISION DEFAULT 0.0,
    win_rate DOUBLE PRECISION DEFAULT 0.0,
    total_trades INTEGER DEFAULT 0,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    roi DOUBLE PRECISION DEFAULT 0.0,
    is_elite BOOLEAN DEFAULT FALSE,
    last_updated TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS idx_users_is_elite ON users(is_elite);

-- ============================================
-- PREDICTIONS (prediction engine output)
-- ============================================
CREATE TABLE IF NOT EXISTS predictions (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT,
    token_id TEXT,
    confidence DOUBLE PRECISION,
    model_type TEXT,
    features TEXT,
    timestamp TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS idx_predictions_market_id ON predictions(market_id);
CREATE INDEX IF NOT EXISTS idx_predictions_token_id ON predictions(token_id);
CREATE INDEX IF NOT EXISTS idx_predictions_timestamp ON predictions(timestamp);
CREATE INDEX IF NOT EXISTS idx_predictions_market_timestamp ON predictions(market_id, timestamp);

-- ============================================
-- LEARNING PATTERNS (learning engine persistence)
-- ============================================
CREATE TABLE IF NOT EXISTS learning_patterns (
    id BIGSERIAL PRIMARY KEY,
    pattern_type TEXT NOT NULL,
    pattern_key TEXT NOT NULL,
    wins INTEGER DEFAULT 0,
    losses INTEGER DEFAULT 0,
    total INTEGER DEFAULT 0,
    confidence DOUBLE PRECISION DEFAULT 0.0,
    sample_size INTEGER DEFAULT 0,
    updated_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    UNIQUE(pattern_type, pattern_key)
);
CREATE INDEX IF NOT EXISTS idx_learning_patterns_pattern_type ON learning_patterns(pattern_type);
CREATE INDEX IF NOT EXISTS idx_learning_patterns_pattern_key ON learning_patterns(pattern_key);

-- ============================================
-- ML FEATURES (pre-computed feature store for backtesting/training)
-- ============================================
CREATE TABLE IF NOT EXISTS ml_features (
    market_id TEXT PRIMARY KEY REFERENCES markets(id) ON DELETE CASCADE,
    computed_at TIMESTAMP NOT NULL,
    features JSONB NOT NULL,
    updated_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS idx_ml_features_computed ON ml_features(computed_at DESC);

-- ============================================
-- ML MODELS (prediction engine model storage)
-- ============================================
CREATE TABLE IF NOT EXISTS ml_models (
    id BIGSERIAL PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_type TEXT NOT NULL,
    model_data BYTEA NOT NULL,
    scaler_data BYTEA,
    metrics JSONB,
    version INTEGER DEFAULT 1,
    is_active BOOLEAN DEFAULT TRUE,
    trained_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS idx_ml_models_model_name ON ml_models(model_name);
CREATE INDEX IF NOT EXISTS idx_ml_models_is_active ON ml_models(is_active);

-- ============================================
-- SIGNALS (external signals: news, social, whale)
-- ============================================
CREATE TABLE IF NOT EXISTS signals (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    direction TEXT NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    raw_text TEXT,
    extracted_entities TEXT,
    time_sensitivity TEXT,
    is_breaking BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    expires_at TIMESTAMP,
    acted_on BOOLEAN DEFAULT FALSE,
    priority_score DOUBLE PRECISION DEFAULT 0.0,
    outcome_correct BOOLEAN,
    resolution_at TIMESTAMP,
    market_resolution TEXT
);
CREATE INDEX IF NOT EXISTS idx_signals_market_id ON signals(market_id);
CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at);
CREATE INDEX IF NOT EXISTS idx_signals_expires_at ON signals(expires_at);
CREATE INDEX IF NOT EXISTS idx_signals_acted_on ON signals(acted_on);
CREATE INDEX IF NOT EXISTS idx_signals_market_created ON signals(market_id, created_at);
CREATE INDEX IF NOT EXISTS idx_signals_active ON signals(expires_at, acted_on);
CREATE INDEX IF NOT EXISTS idx_signals_priority ON signals(priority_score, created_at);
CREATE INDEX IF NOT EXISTS idx_signals_outcome ON signals(outcome_correct);

-- ============================================
-- SCHEDULED EVENTS (court dates, earnings, etc.)
-- ============================================
CREATE TABLE IF NOT EXISTS scheduled_events (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT,
    event_type TEXT NOT NULL,
    event_name TEXT NOT NULL,
    scheduled_time TIMESTAMP NOT NULL,
    source_url TEXT,
    description TEXT,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    notified BOOLEAN DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_scheduled_events_market_id ON scheduled_events(market_id);
CREATE INDEX IF NOT EXISTS idx_scheduled_events_scheduled_time ON scheduled_events(scheduled_time);
CREATE INDEX IF NOT EXISTS idx_events_upcoming ON scheduled_events(scheduled_time, notified);
CREATE INDEX IF NOT EXISTS idx_events_market ON scheduled_events(market_id, scheduled_time);

-- ============================================
-- PERFORMANCE RECORDS (pattern analysis)
-- ============================================
CREATE TABLE IF NOT EXISTS performance_records (
    id BIGSERIAL PRIMARY KEY,
    trade_id TEXT,
    bot_name TEXT,
    market_id TEXT,
    market_category TEXT,
    entry_price_range TEXT,
    time_to_resolution_days INTEGER,
    liquidity_level TEXT,
    signal_source TEXT,
    market_regime TEXT,
    day_of_week INTEGER,
    hour_of_day INTEGER,
    profit DOUBLE PRECISION NOT NULL,
    profit_pct DOUBLE PRECISION,
    hold_time_hours DOUBLE PRECISION,
    was_winner BOOLEAN,
    entry_time TIMESTAMP,
    exit_time TIMESTAMP,
    recorded_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS idx_performance_records_trade_id ON performance_records(trade_id);
CREATE INDEX IF NOT EXISTS idx_performance_records_bot_name ON performance_records(bot_name);
CREATE INDEX IF NOT EXISTS idx_performance_records_market_id ON performance_records(market_id);
CREATE INDEX IF NOT EXISTS idx_performance_records_market_category ON performance_records(market_category);
CREATE INDEX IF NOT EXISTS idx_performance_records_was_winner ON performance_records(was_winner);
CREATE INDEX IF NOT EXISTS idx_performance_records_recorded_at ON performance_records(recorded_at);
CREATE INDEX IF NOT EXISTS idx_perf_category ON performance_records(market_category, was_winner);
CREATE INDEX IF NOT EXISTS idx_perf_bot ON performance_records(bot_name, was_winner);
CREATE INDEX IF NOT EXISTS idx_perf_regime ON performance_records(market_regime, was_winner);

-- ============================================
-- WHALE MOVEMENTS (smart money tracking)
-- ============================================
CREATE TABLE IF NOT EXISTS whale_movements (
    id BIGSERIAL PRIMARY KEY,
    trade_id TEXT UNIQUE,
    user_address TEXT,
    market_id TEXT,
    token_id TEXT,
    side TEXT,
    size DOUBLE PRECISION,
    price DOUBLE PRECISION,
    value_usd DOUBLE PRECISION,
    timestamp TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    smart_money_rank DOUBLE PRECISION,
    trader_category_accuracy DOUBLE PRECISION,
    is_clustered BOOLEAN DEFAULT FALSE,
    cluster_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_whale_movements_trade_id ON whale_movements(trade_id);
CREATE INDEX IF NOT EXISTS idx_whale_movements_user_address ON whale_movements(user_address);
CREATE INDEX IF NOT EXISTS idx_whale_movements_market_id ON whale_movements(market_id);
CREATE INDEX IF NOT EXISTS idx_whale_movements_timestamp ON whale_movements(timestamp);
CREATE INDEX IF NOT EXISTS idx_whale_user_time ON whale_movements(user_address, timestamp);
CREATE INDEX IF NOT EXISTS idx_whale_market_time ON whale_movements(market_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_whale_smart_money ON whale_movements(smart_money_rank, timestamp);

-- ============================================
-- DATA QUALITY ISSUES (validation tracking)
-- ============================================
CREATE TABLE IF NOT EXISTS data_quality_issues (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT,
    issue_type TEXT NOT NULL,
    description TEXT,
    detected_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS idx_data_quality_issues_market_id ON data_quality_issues(market_id);
CREATE INDEX IF NOT EXISTS idx_data_quality_issues_issue_type ON data_quality_issues(issue_type);
CREATE INDEX IF NOT EXISTS idx_data_quality_issues_detected_at ON data_quality_issues(detected_at);
CREATE INDEX IF NOT EXISTS idx_quality_market ON data_quality_issues(market_id);
CREATE INDEX IF NOT EXISTS idx_quality_type ON data_quality_issues(issue_type);
CREATE INDEX IF NOT EXISTS idx_quality_detected ON data_quality_issues(detected_at);

-- ============================================
-- MATERIALIZED VIEWS (#30 - 1000x faster dashboards)
-- ============================================
CREATE MATERIALIZED VIEW IF NOT EXISTS market_stats AS
SELECT
    m.id,
    m.question,
    m.volume,
    m.liquidity,
    m.resolved,
    COUNT(DISTINCT t.user_address) AS unique_traders,
    COUNT(t.id) AS trade_count,
    AVG(t.price) AS avg_price,
    MAX(t.timestamp) AS last_trade_time
FROM markets m
LEFT JOIN trades t ON t.market_id = m.id
GROUP BY m.id, m.question, m.volume, m.liquidity, m.resolved;
CREATE UNIQUE INDEX IF NOT EXISTS idx_market_stats_id ON market_stats(id);

-- ============================================
-- WEBHOOK CONFIG (#39 - push events to external URLs)
-- ============================================
CREATE TABLE IF NOT EXISTS webhook_config (
    id SERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    url TEXT NOT NULL,
    secret TEXT,
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS idx_webhook_config_event ON webhook_config(event_type);
CREATE INDEX IF NOT EXISTS idx_webhook_config_active ON webhook_config(active);

-- ============================================
-- AUDIT LOG / CDC (#29 - track data mutations)
-- ============================================
CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    table_name TEXT NOT NULL,
    operation TEXT NOT NULL,
    record_id TEXT,
    old_data JSONB,
    new_data JSONB,
    changed_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC'),
    changed_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_log_table ON audit_log(table_name);
CREATE INDEX IF NOT EXISTS idx_audit_log_changed_at ON audit_log(changed_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_log_record ON audit_log(table_name, record_id);

-- ============================================
-- DATA LINEAGE (#38 - trace source to destination)
-- ============================================
CREATE TABLE IF NOT EXISTS data_lineage (
    id BIGSERIAL PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_id TEXT,
    target_type TEXT NOT NULL,
    target_id TEXT,
    operation TEXT NOT NULL,
    metadata JSONB,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS idx_data_lineage_source ON data_lineage(source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_data_lineage_target ON data_lineage(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_data_lineage_created ON data_lineage(created_at DESC);

-- ============================================
-- DATA QUALITY SLA (#48 - SLA thresholds and alerts)
-- ============================================
CREATE TABLE IF NOT EXISTS data_quality_sla (
    id SERIAL PRIMARY KEY,
    sla_name TEXT NOT NULL UNIQUE,
    metric_name TEXT NOT NULL,
    threshold_min DOUBLE PRECISION,
    threshold_max DOUBLE PRECISION,
    window_minutes INTEGER NOT NULL DEFAULT 60,
    alert_on_violation BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE INDEX IF NOT EXISTS idx_data_quality_sla_name ON data_quality_sla(sla_name);
