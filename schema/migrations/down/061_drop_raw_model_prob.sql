-- Rollback migration 061: Remove raw_model_prob column from esports_prediction_log
ALTER TABLE esports_prediction_log DROP COLUMN IF EXISTS raw_model_prob;
