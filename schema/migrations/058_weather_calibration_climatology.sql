-- Migration 058: Add climatology columns to weather_calibration for SAMOS
-- (Standardized Anomaly Model Output Statistics) — S115
--
-- Stores ERA5 climatological mean/std per (station, target_date) so that
-- forecast and actual temperatures can be normalized before EMOS fitting.
-- Global EMOS pooled across stations benefits most (eliminates station-specific
-- effects: Chengdu 30°C and Helsinki 5°C become comparable anomalies).
--
-- Nullable: existing rows keep NULL (raw EMOS fallback). New rows populated
-- by bootstrap pipeline and daily calibration writer.

ALTER TABLE weather_calibration
    ADD COLUMN IF NOT EXISTS clim_mean FLOAT,
    ADD COLUMN IF NOT EXISTS clim_std  FLOAT;

COMMENT ON COLUMN weather_calibration.clim_mean IS 'ERA5 climatological mean for station+day_of_year (°F or °C matching temp_unit)';
COMMENT ON COLUMN weather_calibration.clim_std  IS 'ERA5 climatological std for station+day_of_year';
