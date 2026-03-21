-- Migration 059: Dedicated weather climatology table for SAMOS
-- (Standardized Anomaly Model Output Statistics) — S115
--
-- Stores recency-weighted ERA5 climatological (mean, std) per (station, day_of_year).
-- Populated by scripts/backfill_climatology.py from 10-year Open-Meteo ERA5 archive.
-- Used by _maybe_reload_calibration() to build SAMOS pairs for global EMOS fitting.
--
-- Replaces the denormalized clim_mean/clim_std columns on weather_calibration (migration 058)
-- which were populated with a circular bandaid (monthly averages from 90-day bootstrap).

CREATE TABLE IF NOT EXISTS weather_climatology (
    station_id   VARCHAR(20) NOT NULL,
    day_of_year  SMALLINT NOT NULL CHECK (day_of_year BETWEEN 1 AND 366),
    clim_mean    FLOAT NOT NULL,
    clim_std     FLOAT NOT NULL,
    n_years      SMALLINT NOT NULL DEFAULT 0,
    updated_at   TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC'),
    PRIMARY KEY (station_id, day_of_year)
);

COMMENT ON TABLE weather_climatology IS 'ERA5 10-year recency-weighted climatological normals per (station, DOY) for SAMOS EMOS normalization';
COMMENT ON COLUMN weather_climatology.clim_mean IS 'Weighted mean daily max temp (°F for US, °C for intl) — recent 3 years full weight, older years decayed at 0.85/yr';
COMMENT ON COLUMN weather_climatology.clim_std IS 'Weighted std of daily max temp — floor 1.0 to prevent overconfident normalization';
COMMENT ON COLUMN weather_climatology.n_years IS 'Number of years with data for this DOY (max 10)';
