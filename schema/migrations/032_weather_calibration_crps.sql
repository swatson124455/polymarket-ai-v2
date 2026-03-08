-- W8: Add CRPS (Continuous Ranked Probability Score) column to weather_calibration
-- CRPS evaluates the full ensemble distribution, not just binary bucket outcomes.
ALTER TABLE weather_calibration ADD COLUMN IF NOT EXISTS crps FLOAT;
