-- Migration 027: Add bot_name to prediction_log for per-bot model training and calibration.
-- Session 47: Per-bot independence architecture — each bot needs its own prediction history
-- to enable per-bot model training, calibration scoring, and performance tracking.

ALTER TABLE prediction_log ADD COLUMN IF NOT EXISTS bot_name VARCHAR(64);
CREATE INDEX IF NOT EXISTS idx_prediction_log_bot_name ON prediction_log (bot_name);

-- Backfill existing rows: assume EnsembleBot for all historical predictions
-- (it's the only bot that has been making predictions)
UPDATE prediction_log SET bot_name = 'EnsembleBot' WHERE bot_name IS NULL;
