-- Migration 039: paper_trades order state machine columns
-- Adds status, submitted_at, filled_at to track PENDING→SUBMITTED→FILLED lifecycle.
-- Default status='filled' keeps all existing rows valid without back-filling.
-- submitted_at and filled_at default to created_at for legacy rows (best estimate).

ALTER TABLE paper_trades
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'filled',
    ADD COLUMN IF NOT EXISTS submitted_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS filled_at TIMESTAMP;

-- For existing rows, set submitted_at/filled_at = created_at (all were instant fills)
UPDATE paper_trades
SET submitted_at = created_at,
    filled_at    = created_at
WHERE submitted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_paper_trades_status ON paper_trades (status);
