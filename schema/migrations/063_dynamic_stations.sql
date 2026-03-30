-- Migration 063: dynamic_stations table for WeatherBot auto-discovery
-- Cities detected at runtime that don't exist in the static station registry
-- are geocoded via Open-Meteo and stored here; lookup_station() checks this
-- table as a fallback after a static registry miss.

CREATE TABLE IF NOT EXISTS dynamic_stations (
    station_key   TEXT        NOT NULL,
    city_name     TEXT        NOT NULL,
    latitude      DOUBLE PRECISION NOT NULL,
    longitude     DOUBLE PRECISION NOT NULL,
    timezone      TEXT        NOT NULL,
    temp_unit     CHAR(1)     NOT NULL CHECK (temp_unit IN ('C', 'F')),
    aliases       TEXT[]      NOT NULL DEFAULT '{}',
    icao          TEXT,
    confidence    DOUBLE PRECISION NOT NULL,
    source        TEXT        NOT NULL DEFAULT 'open-meteo-geocoding',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (station_key)
);

CREATE INDEX IF NOT EXISTS idx_dynamic_stations_city
    ON dynamic_stations (lower(city_name));
