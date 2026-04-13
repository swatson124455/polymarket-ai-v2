-- 071_strategy_lifecycle.sql
-- 1M: Strategy lifecycle schema — 5 tables for capital allocation tracking
-- Schema only, no code dependency. Informs capital allocation from Day 1.
-- Will need manual apply as postgres user due to ownership issue.

-- 1. strategies: Each bot/model combination is a "strategy"
CREATE TABLE IF NOT EXISTS strategies (
    id SERIAL PRIMARY KEY,
    strategy_name TEXT NOT NULL UNIQUE,     -- e.g. 'EsportsBot_glicko2_xgb_v3'
    bot_name TEXT NOT NULL,                 -- e.g. 'EsportsBot'
    model_name TEXT,                        -- e.g. 'glicko2_xgb_v3'
    model_version INTEGER,
    status TEXT NOT NULL DEFAULT 'shadow',  -- shadow | live | suspended | retired
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    promoted_at TIMESTAMP,                 -- when moved to live
    suspended_at TIMESTAMP,
    retired_at TIMESTAMP,
    config JSONB DEFAULT '{}'              -- strategy-specific config snapshot
);

-- 2. strategy_performance: Rolling performance metrics per strategy
CREATE TABLE IF NOT EXISTS strategy_performance (
    id BIGSERIAL PRIMARY KEY,
    strategy_id INTEGER NOT NULL REFERENCES strategies(id),
    period_start TIMESTAMP NOT NULL,
    period_end TIMESTAMP NOT NULL,
    trades_count INTEGER NOT NULL DEFAULT 0,
    wins INTEGER NOT NULL DEFAULT 0,
    losses INTEGER NOT NULL DEFAULT 0,
    realized_pnl NUMERIC(18,4) DEFAULT 0,
    total_stake NUMERIC(18,4) DEFAULT 0,
    brier_score NUMERIC(10,6),
    edge_estimate NUMERIC(10,6),           -- rolling edge
    p_edge_positive NUMERIC(6,4),          -- bootstrap P(edge>0)
    kelly_fraction NUMERIC(10,6),
    max_drawdown_pct NUMERIC(6,4),
    sharpe_ratio NUMERIC(10,6),
    recorded_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_strat_perf_strategy_period
    ON strategy_performance (strategy_id, period_end DESC);

-- 3. capital_allocations: How much capital is allocated to each strategy
CREATE TABLE IF NOT EXISTS capital_allocations (
    id BIGSERIAL PRIMARY KEY,
    strategy_id INTEGER NOT NULL REFERENCES strategies(id),
    allocated_usd NUMERIC(18,4) NOT NULL,
    max_bet_usd NUMERIC(18,4) NOT NULL,
    kelly_multiplier NUMERIC(6,4) DEFAULT 0.5,  -- half-Kelly default
    reason TEXT,                                  -- why this allocation
    effective_from TIMESTAMP NOT NULL DEFAULT NOW(),
    effective_until TIMESTAMP,                    -- NULL = current
    created_by TEXT DEFAULT 'manual'              -- manual | auto | rebalance
);

CREATE INDEX idx_cap_alloc_strategy_active
    ON capital_allocations (strategy_id, effective_from DESC)
    WHERE effective_until IS NULL;

-- 4. strategy_transitions: Audit log of state changes
CREATE TABLE IF NOT EXISTS strategy_transitions (
    id BIGSERIAL PRIMARY KEY,
    strategy_id INTEGER NOT NULL REFERENCES strategies(id),
    from_status TEXT NOT NULL,
    to_status TEXT NOT NULL,
    reason TEXT,                            -- why the transition happened
    evidence JSONB DEFAULT '{}',           -- metrics that triggered the change
    transitioned_at TIMESTAMP NOT NULL DEFAULT NOW(),
    transitioned_by TEXT DEFAULT 'manual'  -- manual | shadow_eval | edge_gate | kill_switch
);

CREATE INDEX idx_strat_trans_strategy_time
    ON strategy_transitions (strategy_id, transitioned_at DESC);

-- 5. strategy_predictions: Links predictions to strategies for shadow evaluation
--    (lightweight join table — prediction_log has the actual data)
CREATE TABLE IF NOT EXISTS strategy_predictions (
    id BIGSERIAL PRIMARY KEY,
    strategy_id INTEGER NOT NULL REFERENCES strategies(id),
    prediction_log_id BIGINT,              -- FK to prediction_log if available
    market_id TEXT NOT NULL,
    predicted_prob NUMERIC(6,4),
    confidence NUMERIC(6,4),
    would_trade BOOLEAN DEFAULT FALSE,     -- would this prediction have triggered a trade?
    hypothetical_pnl NUMERIC(18,4),        -- simulated P&L for shadow strategies
    predicted_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_strat_pred_strategy_time
    ON strategy_predictions (strategy_id, predicted_at DESC);

CREATE INDEX idx_strat_pred_market
    ON strategy_predictions (market_id, predicted_at DESC);
