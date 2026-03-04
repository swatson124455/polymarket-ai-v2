-- Autonomy: prediction_log blend-learning columns; paper_trades table for SIMULATION_MODE.
-- Run after 011. Enables: learn_ensemble_blend() grid over [0.4,0.5,0.6,0.7]; paper trade persistence and hypothetical P&L.

-- prediction_log: store ensemble_pred and learning_conf for per-blend Brier grid search
ALTER TABLE prediction_log ADD COLUMN IF NOT EXISTS ensemble_pred DOUBLE PRECISION;
ALTER TABLE prediction_log ADD COLUMN IF NOT EXISTS learning_conf DOUBLE PRECISION;

-- paper_trades: SIMULATION_MODE orders; resolution backfill sets resolution, resolved_at, realized_pnl
CREATE TABLE IF NOT EXISTS paper_trades (
    id BIGSERIAL PRIMARY KEY,
    order_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    token_id TEXT,
    bot_name TEXT NOT NULL,
    side TEXT NOT NULL,
    size DOUBLE PRECISION NOT NULL,
    price DOUBLE PRECISION NOT NULL,
    confidence DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    resolution TEXT,
    resolved_at TIMESTAMPTZ,
    realized_pnl DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS idx_paper_trades_market ON paper_trades(market_id);
CREATE INDEX IF NOT EXISTS idx_paper_trades_bot ON paper_trades(bot_name);
CREATE INDEX IF NOT EXISTS idx_paper_trades_order ON paper_trades(order_id);
