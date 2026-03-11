-- Migration 038: Feature flags table
-- Provides runtime kill-switches and per-bot feature toggles without restart.
-- Bots can check flags via db.get_flag(flag_name) on each scan cycle.
-- Flag changes propagate within one scan cycle (no restart needed).
--
-- Kill switch usage example:
--   UPDATE feature_flags SET enabled = false, updated_at = NOW()
--     WHERE flag_name = 'mirrorbot_buy_enabled';

CREATE TABLE IF NOT EXISTS feature_flags (
    flag_name   TEXT PRIMARY KEY,
    bot_name    TEXT DEFAULT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT true,
    description TEXT DEFAULT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO feature_flags (flag_name, bot_name, enabled, description)
VALUES
    ('mirrorbot_buy_enabled',   'MirrorBot',   true, 'Allow MirrorBot to open new BUY positions'),
    ('mirrorbot_sell_enabled',  'MirrorBot',   true, 'Allow MirrorBot to execute SELL exits'),
    ('weatherbot_buy_enabled',  'WeatherBot',  true, 'Allow WeatherBot to open new positions'),
    ('esportsbot_buy_enabled',  'EsportsBot',  true, 'Allow EsportsBot to open new positions')
ON CONFLICT (flag_name) DO NOTHING;
