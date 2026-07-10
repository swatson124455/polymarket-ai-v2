-- Migration 080: prediction_log.prob_frame — probability-frame provenance (S226 V23)
--
-- Fallacy-audit V23: prediction_log.predicted_prob carried TWO conventions —
-- P(YES) on temperature rows, P(chosen side) on PSW (precip/snow/wind) NO-side
-- rows — while the single grader (backfill_prediction_log_resolution) computes
--   was_correct = (predicted_prob >= 0.5) = (resolution = 'YES')
-- a YES-frame read. Winning PSW NO calls were therefore stored as MISSES,
-- poisoning calibration_tracker, Brier feeds, and the consecutive-loss compress.
--
-- Code fix (95c732c + follow-ups): every WeatherBot writer now logs P(YES) and
-- stamps this column 'yes'. The graders (both trees) refuse to grade PSW rows
-- whose frame is not 'yes' (was_correct/realized_edge -> NULL).
--
-- Values:
--   'yes' — predicted_prob (and, from the S226 market_price fix, market_price)
--           are YES-frame. Written by post-S226 WeatherBot code.
--   NULL  — pre-instrumentation row: frame UNKNOWN. Temperature rows are
--           YES-frame by construction; PSW rows are AMBIGUOUS (side was never
--           persisted) and must not be graded or used for calibration.
--
-- The graders detect this column at runtime (information_schema check) and
-- fall back to the legacy statement with a warning until this is applied —
-- safe to deploy code before running this, but run it promptly.

ALTER TABLE prediction_log
    ADD COLUMN IF NOT EXISTS prob_frame TEXT;

COMMENT ON COLUMN prediction_log.prob_frame IS
    'Probability frame of predicted_prob: yes = P(YES) (post-S226 WeatherBot rows) | NULL = pre-instrumentation (PSW rows frame-ambiguous, do not grade). S226 V23.';

-- Retro-label the poison: historical PSW rows are frame-ambiguous, so their
-- grades are meaningless. NULL them (idempotent — re-running keeps NULL).
-- Temperature history is YES-frame and keeps its grades.
UPDATE prediction_log
SET was_correct = NULL,
    realized_edge = NULL
WHERE bot_name = 'WeatherBot'
  AND model_name IN ('weather_precipitation', 'weather_snowfall', 'weather_wind')
  AND prob_frame IS NULL
  AND (was_correct IS NOT NULL OR realized_edge IS NOT NULL);
