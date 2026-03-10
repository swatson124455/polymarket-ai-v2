-- Migration 035: Add regime column to weather_calibration
-- Required by _save_forecast_to_db() which inserts regime (ENSO tag)
-- Without this column, every INSERT fails silently → calibration never accumulates
-- Deploy BEFORE restarting the service.

ALTER TABLE weather_calibration
  ADD COLUMN IF NOT EXISTS regime VARCHAR(20) DEFAULT NULL;

CREATE INDEX IF NOT EXISTS idx_wc_regime
  ON weather_calibration (regime, station_id, lead_time_hours);
