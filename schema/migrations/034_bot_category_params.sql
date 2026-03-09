-- Migration 034: bot_category_params
-- Fixes MirrorBot R5b schema violation where category strings were stored
-- in bot_market_params.market_id (a Polymarket condition UUID column).
-- New table uses an explicit 'category' column to avoid semantic collision.

CREATE TABLE IF NOT EXISTS bot_category_params (
    id          BIGSERIAL PRIMARY KEY,
    bot_name    VARCHAR(64) NOT NULL,
    category    VARCHAR(64) NOT NULL,
    param_name  VARCHAR(64) NOT NULL,
    param_value FLOAT       NOT NULL,
    sample_n    INTEGER     DEFAULT 0,
    updated_at  TIMESTAMP,
    UNIQUE(bot_name, category, param_name)
);

CREATE INDEX IF NOT EXISTS idx_bot_category_params
    ON bot_category_params(bot_name, category);

-- Backfill: migrate existing MirrorBot consensus_min rows from bot_market_params.
-- In bot_market_params, market_id was (ab)used to store category strings.
INSERT INTO bot_category_params (bot_name, category, param_name, param_value, sample_n, updated_at)
SELECT bot_name, market_id, param_name, param_value, COALESCE(sample_n, 0), updated_at
FROM   bot_market_params
WHERE  bot_name = 'MirrorBot'
  AND  param_name = 'consensus_min'
ON CONFLICT DO NOTHING;
