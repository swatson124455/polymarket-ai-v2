-- S168 Phase 8: mirror_state table for DB-authoritative state persistence.
-- Replaces in-memory-only cooldowns and circuit breaker that were lost on restart.
-- Generic key/value with TTL — supports future state additions without schema changes.

CREATE TABLE IF NOT EXISTS mirror_state (
    key TEXT PRIMARY KEY,
    value_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at TIMESTAMP WITHOUT TIME ZONE,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW()
);

-- Index for TTL cleanup queries
CREATE INDEX IF NOT EXISTS idx_mirror_state_expires
    ON mirror_state (expires_at)
    WHERE expires_at IS NOT NULL;

COMMENT ON TABLE mirror_state IS 'S168: DB-authoritative state for MirrorBot (cooldowns, circuit breaker)';
