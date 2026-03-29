-- Migration 062: Unified audit system
-- Creates audit_runs table and extends reconciliation_breaks with
-- audit_run_id linkage, ACKNOWLEDGED status, and violation_hash discriminator.

CREATE TABLE IF NOT EXISTS audit_runs (
    run_id        BIGSERIAL PRIMARY KEY,
    run_type      TEXT NOT NULL,
    started_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMP,
    status        TEXT NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'completed', 'failed')),
    checks_run    INTEGER,
    checks_passed INTEGER,
    checks_failed INTEGER,
    checks_warned INTEGER,
    total_breaks  INTEGER,
    summary       JSONB DEFAULT '{}',
    error_message TEXT,
    triggered_by  TEXT NOT NULL DEFAULT 'scheduler'
                  CHECK (triggered_by IN
                      ('scheduler', 'cli', 'health_check', 'post_resolution', 'manual'))
);

CREATE INDEX IF NOT EXISTS idx_audit_runs_started ON audit_runs (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_runs_status  ON audit_runs (status);

-- Link breaks back to their audit run
ALTER TABLE reconciliation_breaks
    ADD COLUMN IF NOT EXISTS audit_run_id BIGINT;

-- Add ACKNOWLEDGED status
ALTER TABLE reconciliation_breaks
    DROP CONSTRAINT IF EXISTS reconciliation_breaks_status_check;
ALTER TABLE reconciliation_breaks
    ADD CONSTRAINT reconciliation_breaks_status_check
    CHECK (status IN ('OPEN', 'RESOLVED', 'ACKNOWLEDGED'));

-- Per-violation discriminator: dedup on (recon_date, violation_hash) not type+market
ALTER TABLE reconciliation_breaks
    ADD COLUMN IF NOT EXISTS violation_hash TEXT;
CREATE INDEX IF NOT EXISTS idx_recon_breaks_hash
    ON reconciliation_breaks (recon_date, violation_hash)
    WHERE violation_hash IS NOT NULL;
