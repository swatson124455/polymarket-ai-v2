-- Migration 040: Seed WeatherBot per-market-type parameters
-- Enables per-type min_edge tuning via bot_category_params table.
-- WeatherBot._get_min_edge(market_type) reads these; falls back to
-- WEATHER_MIN_EDGE (0.08) when no row exists.
--
-- Rationale for differentiated thresholds:
--   temperature: 0.08 — primary product, well-calibrated 133-member ensemble
--   precipitation: 0.10 — wider Gamma distribution uncertainty, fewer models
--   snowfall: 0.12 — highest uncertainty, limited ensemble support
--   wind: 0.10 — normal CDF model, moderate uncertainty

INSERT INTO bot_category_params (bot_name, category, param_name, param_value, sample_n, updated_at)
VALUES
    ('WeatherBot', 'temperature',   'min_edge', 0.08, 0, NOW()),
    ('WeatherBot', 'precipitation', 'min_edge', 0.10, 0, NOW()),
    ('WeatherBot', 'snowfall',      'min_edge', 0.12, 0, NOW()),
    ('WeatherBot', 'wind',          'min_edge', 0.10, 0, NOW())
ON CONFLICT (bot_name, category, param_name) DO UPDATE SET
    param_value = EXCLUDED.param_value,
    updated_at = NOW();
