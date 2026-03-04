-- Migration 021: Self-Healing Architecture Tables
-- Applied: 2026-02-23
-- Purpose: Add bot_health_states, config_history, and dead_letter_queue tables
--          for the self-healing architecture (transitions state machine, config audit,
--          failed async operation capture).

-- ============================================================
-- TABLE: bot_health_states
-- Purpose: Persistent snapshot of per-bot state machine status.
--          Updated by BotStateMachine on every state transition.
--          Powers the Streamlit dashboard health indicators.
-- ============================================================
CREATE TABLE IF NOT EXISTS bot_health_states (
    id              BIGSERIAL PRIMARY KEY,
    bot_name        VARCHAR(100) NOT NULL,
    state           VARCHAR(50)  NOT NULL,   -- healthy, degraded, failed, recovering, safe_mode
    failure_count   INTEGER      NOT NULL DEFAULT 0,
    sizing_multiplier FLOAT      NOT NULL DEFAULT 1.0,
    state_entered_at TIMESTAMP WITHOUT TIME ZONE,
    recorded_at     TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    details         JSONB        NULL
);

CREATE INDEX IF NOT EXISTS idx_bot_health_bot_name    ON bot_health_states (bot_name);
CREATE INDEX IF NOT EXISTS idx_bot_health_state        ON bot_health_states (state);
CREATE INDEX IF NOT EXISTS idx_bot_health_recorded_at  ON bot_health_states (recorded_at DESC);

COMMENT ON TABLE bot_health_states IS
    'Per-bot health state machine snapshots. Inserted on each state transition.';

-- ============================================================
-- TABLE: config_history
-- Purpose: Audit trail for all configuration parameter changes.
--          Written by the self-healing auto-patch and canary systems.
--          Supports rollback: before_value stored in JSONB for every patch.
-- ============================================================
CREATE TABLE IF NOT EXISTS config_history (
    id              BIGSERIAL PRIMARY KEY,
    patch_id        UUID         NOT NULL DEFAULT gen_random_uuid(),
    applied_at      TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    trigger_type    VARCHAR(50)  NOT NULL,   -- 'auto_patch', 'manual', 'canary', 'rollback', 'startup'
    component       VARCHAR(100) NOT NULL,   -- e.g., 'signal_ingestion', 'order_gateway', 'scan_limit'
    param_key       VARCHAR(200) NOT NULL,   -- parameter name that was changed
    before_value    JSONB        NULL,       -- state before patch (null on first-time set)
    after_value     JSONB        NULL,       -- state after patch
    action_taken    VARCHAR(100) NULL,       -- 'applied', 'rolled_back', 'pending', 'skipped'
    outcome         VARCHAR(50)  NULL,       -- 'success', 'failure', 'reverted'
    approved_by     VARCHAR(100) NULL,       -- 'auto', 'operator:<name>', 'kill_switch'
    notes           TEXT         NULL
);

CREATE INDEX IF NOT EXISTS idx_config_history_component  ON config_history (component);
CREATE INDEX IF NOT EXISTS idx_config_history_applied_at ON config_history (applied_at DESC);
CREATE INDEX IF NOT EXISTS idx_config_history_patch_id   ON config_history (patch_id);

COMMENT ON TABLE config_history IS
    'Audit trail for configuration parameter changes. Retain ≥7 years per financial regulatory guidance.';

-- ============================================================
-- TABLE: dead_letter_queue
-- Purpose: Capture failed async operations (signal writes, trade persists,
--          price updates) for inspection and replay.
--
--          DLQ uses LOCAL SQLITE as primary store under DB pool exhaustion
--          (writing to Postgres during exhaustion would also fail).
--          This table is a secondary sync target for auditing and replay.
-- ============================================================
CREATE TABLE IF NOT EXISTS dead_letter_queue (
    id              BIGSERIAL PRIMARY KEY,
    event_type      VARCHAR(50)  NOT NULL,   -- 'signal_write', 'trade_persist', 'price_write', 'order_submit'
    payload         JSONB        NOT NULL,   -- original event data for replay
    error_message   TEXT         NULL,
    error_type      VARCHAR(100) NULL,       -- 'DatabaseError', 'TimeoutError', 'APIError', etc.
    retry_count     INTEGER      NOT NULL DEFAULT 0,
    max_retries     INTEGER      NOT NULL DEFAULT 3,
    status          VARCHAR(20)  NOT NULL DEFAULT 'pending',  -- 'pending', 'replayed', 'discarded', 'inspecting'
    created_at      TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    next_retry_at   TIMESTAMP WITHOUT TIME ZONE NULL,
    replayed_at     TIMESTAMP WITHOUT TIME ZONE NULL,
    source_bot      VARCHAR(100) NULL,
    market_id       VARCHAR(200) NULL       -- for filtering by market
);

CREATE INDEX IF NOT EXISTS idx_dlq_status        ON dead_letter_queue (status);
CREATE INDEX IF NOT EXISTS idx_dlq_event_type    ON dead_letter_queue (event_type);
CREATE INDEX IF NOT EXISTS idx_dlq_created_at    ON dead_letter_queue (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_dlq_next_retry_at ON dead_letter_queue (next_retry_at)
    WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS idx_dlq_market_id     ON dead_letter_queue (market_id)
    WHERE market_id IS NOT NULL;

COMMENT ON TABLE dead_letter_queue IS
    'Failed async operations capture for inspection and replay. Synced from local SQLite during recovery.';

-- ============================================================
-- CLEANUP: Purge old bot_health_states (keep 7 days)
-- Run this periodically (e.g., weekly cron or ingestion scheduler)
-- ============================================================
-- DELETE FROM bot_health_states WHERE recorded_at < NOW() - INTERVAL '7 days';

-- ============================================================
-- CLEANUP: Purge resolved DLQ entries older than 30 days
-- ============================================================
-- DELETE FROM dead_letter_queue WHERE status IN ('replayed', 'discarded') AND created_at < NOW() - INTERVAL '30 days';
