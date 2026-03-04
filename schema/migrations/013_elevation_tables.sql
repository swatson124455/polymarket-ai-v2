-- Elevation Phase 2A-5: fill_analysis, kill_switch_events, tunable_config tables.
-- Run after 012.

-- P2A-10: Persist adverse selection fill tracking to DB
CREATE TABLE IF NOT EXISTS fill_analysis (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT,
    source_bot TEXT,
    fill_price DOUBLE PRECISION NOT NULL,
    fill_side TEXT NOT NULL,
    fill_time TIMESTAMPTZ NOT NULL,
    price_30s DOUBLE PRECISION,
    price_60s DOUBLE PRECISION,
    price_300s DOUBLE PRECISION,
    adverse_move_30s DOUBLE PRECISION,
    adverse_move_300s DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fill_analysis_market ON fill_analysis(market_id);
CREATE INDEX IF NOT EXISTS idx_fill_analysis_time ON fill_analysis(fill_time DESC);

-- P4-03: Kill switch event audit trail
CREATE TABLE IF NOT EXISTS kill_switch_events (
    id BIGSERIAL PRIMARY KEY,
    trigger_level TEXT NOT NULL,
    trigger_reason TEXT NOT NULL,
    triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    auto_reset_at TIMESTAMPTZ,
    manually_reset_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_ks_events_time ON kill_switch_events(triggered_at DESC);

-- P5-01: Self-tuning parameter store
CREATE TABLE IF NOT EXISTS tunable_config (
    key TEXT PRIMARY KEY,
    value DOUBLE PRECISION NOT NULL,
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    updated_by TEXT
);

-- P6-03: Tax-compliant transaction log
CREATE TABLE IF NOT EXISTS tax_transactions (
    id BIGSERIAL PRIMARY KEY,
    tx_time TIMESTAMPTZ NOT NULL,
    market_id TEXT NOT NULL,
    market_question TEXT,
    side TEXT NOT NULL,
    quantity DOUBLE PRECISION NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    fee DOUBLE PRECISION DEFAULT 0,
    gas_cost DOUBLE PRECISION DEFAULT 0,
    net_proceeds DOUBLE PRECISION,
    cost_basis DOUBLE PRECISION,
    realized_pnl DOUBLE PRECISION,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tax_tx_time ON tax_transactions(tx_time DESC);
