-- Migration 018: Add feature_snapshot JSONB column to prediction_log
-- Stores the full feature vector used for each prediction, enabling offline analysis
-- of feature drift, importance shifts, and debugging incorrect predictions.

ALTER TABLE prediction_log ADD COLUMN IF NOT EXISTS feature_snapshot JSONB;

-- Partial index: only index rows that have a snapshot (most won't initially)
CREATE INDEX IF NOT EXISTS idx_prediction_log_feature_snapshot
    ON prediction_log USING gin (feature_snapshot)
    WHERE feature_snapshot IS NOT NULL;
