-- Migration 003: Signal outcome learning columns
-- Enables tracking whether signals predicted correctly when markets resolve.
-- Run: python -m scripts.apply_migration_003

ALTER TABLE signals ADD COLUMN IF NOT EXISTS outcome_correct BOOLEAN;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS resolution_at TIMESTAMP;
ALTER TABLE signals ADD COLUMN IF NOT EXISTS market_resolution TEXT;
CREATE INDEX IF NOT EXISTS idx_signals_outcome ON signals(outcome_correct);
