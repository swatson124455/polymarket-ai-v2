-- Migration 033: Create weather_tail_calibration table
-- Stores (model_prob, actual_outcome) pairs for isotonic tail calibration.
-- Used by WeatherBot._load_calibration() to correct systematic probability
-- errors at the tails of the distribution (over/under-confidence at extremes).
-- Without this table, WeatherBot falls back to a static 0.85 tail discount.

CREATE TABLE IF NOT EXISTS weather_tail_calibration (
    id               BIGSERIAL PRIMARY KEY,
    bucket_type      VARCHAR(20)  NOT NULL,          -- range, at_or_below, at_or_higher
    lead_time_bucket INTEGER      NOT NULL,           -- 0=<6h, 1=6-24h, 2=24-72h, 3=72h+
    model_prob       FLOAT        NOT NULL,
    actual_outcome   INTEGER      NOT NULL,           -- 0 or 1
    station_id       VARCHAR(20),
    created_at       TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tail_cal_bucket
    ON weather_tail_calibration (bucket_type, lead_time_bucket);
