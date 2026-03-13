-- 043: trade_events — Immutable append-only event store.
-- Single source of truth for every trade action. paper_trades UPSERT
-- overwrites history; this table preserves it.
--
-- Bi-temporal: event_time (when it happened) + knowledge_time (when system learned).
-- ML attribution: model_version, predicted_probability on the event.
-- Idempotency: idempotency_key UNIQUE prevents duplicate events on retries.
-- Immutability: trigger prevents UPDATE/DELETE with GUC bypass for retention cleanup.

CREATE TABLE IF NOT EXISTS trade_events (
    sequence_num        BIGSERIAL PRIMARY KEY,
    event_type          TEXT NOT NULL CHECK (event_type IN (
                            'ENTRY', 'EXIT', 'RESOLUTION', 'CORRECTION',
                            'POSITION_REBUILD', 'MANUAL_ADJUSTMENT'
                        )),
    execution_mode      TEXT NOT NULL DEFAULT 'paper' CHECK (execution_mode IN (
                            'paper', 'live', 'backtest'
                        )),
    event_time          TIMESTAMP NOT NULL,
    knowledge_time      TIMESTAMP NOT NULL DEFAULT NOW(),
    recorded_at         TIMESTAMP NOT NULL DEFAULT NOW(),

    -- Identity
    bot_name            TEXT NOT NULL,
    market_id           TEXT NOT NULL,
    token_id            TEXT,
    correlation_id      TEXT,
    order_id            TEXT,

    -- Trade data
    side                TEXT CHECK (side IN ('YES', 'NO', 'SELL')),
    size                NUMERIC(18,8),
    price               NUMERIC(18,8),
    fees                NUMERIC(18,8) DEFAULT 0,
    realized_pnl        NUMERIC(18,4),

    -- ML attribution
    confidence          NUMERIC(6,4),
    predicted_probability NUMERIC(6,4),
    model_version       INTEGER,
    model_name          TEXT,

    -- Dedup + metadata
    idempotency_key     TEXT UNIQUE,
    event_data          JSONB DEFAULT '{}'
);

-- Immutability enforcement with GUC bypass for retention cleanup
CREATE OR REPLACE FUNCTION prevent_trade_event_mutation() RETURNS TRIGGER AS $$
BEGIN
    IF current_setting('app.allow_retention_cleanup', true) = 'true' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'Cannot % rows in append-only table %', TG_OP, TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_trade_events_immutable
    BEFORE UPDATE OR DELETE ON trade_events
    FOR EACH ROW EXECUTE FUNCTION prevent_trade_event_mutation();

-- Also protect decision_events (existing table from migration 020)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_trigger WHERE tgname = 'trg_decision_events_immutable'
    ) THEN
        CREATE TRIGGER trg_decision_events_immutable
            BEFORE UPDATE OR DELETE ON decision_events
            FOR EACH ROW EXECUTE FUNCTION prevent_trade_event_mutation();
    END IF;
END $$;

-- Indexes
CREATE INDEX idx_trade_events_time_brin ON trade_events USING BRIN (event_time) WITH (pages_per_range = 32);
CREATE INDEX idx_trade_events_bot_time ON trade_events (bot_name, event_time DESC);
CREATE INDEX idx_trade_events_market ON trade_events (market_id);
CREATE INDEX idx_trade_events_type ON trade_events (event_type);
CREATE INDEX idx_trade_events_correlation ON trade_events (correlation_id) WHERE correlation_id IS NOT NULL;
CREATE INDEX idx_trade_events_mode ON trade_events (execution_mode);
