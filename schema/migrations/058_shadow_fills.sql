-- Migration 058: Shadow fills table for empirical execution tracking
-- Records L2 book snapshot + VWAP fill price at every trade signal.
-- Resolution backfill computes retroactive P&L from real book data.

CREATE TABLE IF NOT EXISTS shadow_fills (
    id                  BIGSERIAL PRIMARY KEY,
    created_at          TIMESTAMP NOT NULL DEFAULT now(),
    bot_name            TEXT NOT NULL,
    market_id           TEXT NOT NULL,
    token_id            TEXT,
    side                TEXT NOT NULL,
    order_size_shares   NUMERIC(18,8),
    order_size_usd      NUMERIC(18,4),
    signal_price        NUMERIC(18,8),
    confidence          NUMERIC(6,4),
    edge_at_signal      NUMERIC(6,4),
    latency_ms          NUMERIC(10,2),
    book_snapshot       JSONB,
    best_ask            NUMERIC(18,8),
    best_bid            NUMERIC(18,8),
    spread              NUMERIC(18,8),
    depth_at_best_usd   NUMERIC(18,4),
    total_depth_usd     NUMERIC(18,4),
    vwap_fill_price     NUMERIC(18,8),
    book_walk_slippage  NUMERIC(18,8),
    fill_fraction       NUMERIC(6,4),
    edge_at_vwap        NUMERIC(6,4),
    trade_executed      BOOLEAN NOT NULL DEFAULT true,
    execution_price     NUMERIC(18,8),
    correlation_id      TEXT,
    resolved_at         TIMESTAMP,
    resolution_outcome  TEXT,
    shadow_pnl          NUMERIC(18,4),
    model_name          TEXT,
    event_data          JSONB
);

CREATE INDEX IF NOT EXISTS idx_shadow_fills_bot_created
    ON shadow_fills (bot_name, created_at);

CREATE INDEX IF NOT EXISTS idx_shadow_fills_market
    ON shadow_fills (market_id);

CREATE INDEX IF NOT EXISTS idx_shadow_fills_executed_bot
    ON shadow_fills (trade_executed, bot_name);
