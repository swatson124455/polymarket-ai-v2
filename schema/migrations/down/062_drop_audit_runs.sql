-- Rollback migration 062: Remove audit system
-- IMPORTANT: Must clean ACKNOWLEDGED rows before restoring original constraint

-- Step 1: Convert ACKNOWLEDGED rows to RESOLVED (original schema only had OPEN/RESOLVED)
UPDATE reconciliation_breaks SET status = 'RESOLVED'
WHERE status = 'ACKNOWLEDGED';

-- Step 2: Remove audit columns from reconciliation_breaks
ALTER TABLE reconciliation_breaks DROP COLUMN IF EXISTS audit_run_id;
ALTER TABLE reconciliation_breaks DROP COLUMN IF EXISTS violation_hash;
DROP INDEX IF EXISTS idx_recon_breaks_hash;

-- Step 3: Restore original status constraint (OPEN, RESOLVED only)
ALTER TABLE reconciliation_breaks
    DROP CONSTRAINT IF EXISTS reconciliation_breaks_status_check;
ALTER TABLE reconciliation_breaks
    ADD CONSTRAINT reconciliation_breaks_status_check
    CHECK (status IN ('OPEN', 'RESOLVED'));

-- Step 4: Drop audit_runs table
DROP TABLE IF EXISTS audit_runs;
