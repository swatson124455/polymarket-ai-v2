-- prediction_log: Drift detection, live performance tracking (GAP 1)
-- Enables: "What did the model predict? Was it correct? Did we trade? P&L?"
-- Also unblocks Bayesian Elements 2 & 3: once resolution is backfilled, model precision
-- (by training sample count) and market precision (by volume/trader count) are computable from this table.
-- Run after 006_data_contracts.sql

CREATE TABLE IF NOT EXISTS prediction_log (
    id BIGSERIAL PRIMARY KEY,
    market_id TEXT NOT NULL,
    token_id TEXT,
    model_name TEXT NOT NULL,              -- 'ensemble' or model name
    predicted_prob DOUBLE PRECISION NOT NULL,
    market_price DOUBLE PRECISION NOT NULL,
    edge DOUBLE PRECISION NOT NULL,         -- predicted_prob - market_price
    prediction_time TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    fallback_level INT,                    -- 1=elite, 2=all, 3=price
    confidence DOUBLE PRECISION,

    -- Filled after resolution
    resolution TEXT,                       -- 'YES' / 'NO' / NULL (pending)
    resolved_at TIMESTAMPTZ,
    was_correct BOOLEAN,
    realized_edge DOUBLE PRECISION,

    -- Filled after trade execution
    trade_executed BOOLEAN DEFAULT FALSE,
    trade_side TEXT,
    trade_size DOUBLE PRECISION,
    trade_price DOUBLE PRECISION,
    trade_pnl DOUBLE PRECISION,

    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_prediction_log_market ON prediction_log(market_id);
CREATE INDEX IF NOT EXISTS idx_prediction_log_pending ON prediction_log(resolution) WHERE resolution IS NULL;
CREATE INDEX IF NOT EXISTS idx_prediction_log_time ON prediction_log(prediction_time DESC);

-- Backfill resolution on prediction logs (run periodically)
-- UPDATE prediction_log pl SET resolution = m.resolution, resolved_at = m.resolved_at, ...
-- FROM markets m WHERE pl.market_id = m.id AND m.resolved = true AND pl.resolution IS NULL;
