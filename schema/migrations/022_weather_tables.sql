-- Migration 022: Weather Bot Tables
-- Purpose: Forecast cache and bias calibration for WeatherBot

CREATE TABLE IF NOT EXISTS weather_forecasts (
    id              BIGSERIAL PRIMARY KEY,
    station_id      VARCHAR(20) NOT NULL,
    target_date     TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    forecast_time   TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    lead_time_hours FLOAT NOT NULL,
    ensemble_members JSONB NOT NULL,
    deterministic_high FLOAT,
    model_spread    FLOAT,
    models_used     JSONB,
    created_at      TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS idx_weather_fc_station_date
    ON weather_forecasts (station_id, target_date);
CREATE UNIQUE INDEX IF NOT EXISTS uq_weather_fc_station_date_time
    ON weather_forecasts (station_id, target_date, forecast_time);

CREATE TABLE IF NOT EXISTS weather_calibration (
    id              BIGSERIAL PRIMARY KEY,
    station_id      VARCHAR(20) NOT NULL,
    target_date     TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    forecast_temp   FLOAT NOT NULL,
    actual_temp     FLOAT,
    lead_time_hours FLOAT NOT NULL,
    bias            FLOAT,
    model_name      VARCHAR(50),
    created_at      TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS idx_weather_cal_station_date
    ON weather_calibration (station_id, target_date);
CREATE UNIQUE INDEX IF NOT EXISTS uq_weather_cal_station_date_lt
    ON weather_calibration (station_id, target_date, lead_time_hours);
