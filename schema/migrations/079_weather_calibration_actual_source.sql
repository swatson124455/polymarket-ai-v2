-- Migration 079: weather_calibration.actual_source — ground-truth provenance (S224 WS-1)
--
-- Fallacy-audit V4/V10: pre-2026-07-01, ~96% of weather_calibration.actual_temp
-- values were silent ERA5/Open-Meteo fallback (WU scraper dead), and NOTHING
-- recorded which source a row came from — contaminated rows are permanently
-- indistinguishable from clean ones. This column fixes that going forward.
--
-- Values written by the bot (bots/weather_bot.py):
--   'wu'             — Weather Underground scrape (the Polymarket resolution source)
--   'open_meteo'     — Open-Meteo archive fallback (WU scrape failed)
--   'era5_bootstrap' — cold-start bootstrap pairs (pure ERA5, definitionally not WU)
--   NULL             — pre-instrumentation row: provenance UNKNOWN, treat as dirty
--                      (operator decision 2026-07-08: option A — no retroactive stamping)
--
-- The bot code detects this column at runtime (information_schema check) and
-- falls back to the legacy statements with a warning until this is applied —
-- safe to deploy code before running this, but run it promptly.

ALTER TABLE weather_calibration
    ADD COLUMN IF NOT EXISTS actual_source TEXT;

COMMENT ON COLUMN weather_calibration.actual_source IS
    'Ground-truth provenance: wu | open_meteo | era5_bootstrap | NULL=pre-instrumentation (dirty). S224 WS-1.';
