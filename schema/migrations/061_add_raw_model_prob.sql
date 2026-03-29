-- S138: Add raw_model_prob to esports_prediction_log for calibrator feedback loop fix.
-- Stores pre-calibration probability so calibrators can train on raw model output
-- instead of their own post-calibration output.
ALTER TABLE esports_prediction_log ADD COLUMN IF NOT EXISTS raw_model_prob DOUBLE PRECISION;
