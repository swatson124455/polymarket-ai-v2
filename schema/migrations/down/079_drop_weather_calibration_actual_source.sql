-- Down-migration for 079: drop the ground-truth provenance column.
-- The bot's runtime column check makes this safe — writers revert to the
-- legacy statements automatically on the next check cycle (restart to clear
-- the cached positive check).

ALTER TABLE weather_calibration
    DROP COLUMN IF EXISTS actual_source;
