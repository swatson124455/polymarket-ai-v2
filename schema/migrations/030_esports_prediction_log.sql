-- Migration 030: Esports prediction log for accuracy tracking + calibration
-- Logs every prediction from esports bots with model prob, market price, and eventual outcome

CREATE TABLE IF NOT EXISTS esports_prediction_log (
    id              BIGSERIAL PRIMARY KEY,
    match_id        VARCHAR(64) NOT NULL DEFAULT '',
    game            VARCHAR(16) NOT NULL,           -- lol / cs2 / dota2 / valorant
    market_id       VARCHAR(128) NOT NULL,
    bot_name        VARCHAR(64) NOT NULL,
    predicted_prob  DOUBLE PRECISION NOT NULL,
    market_price    DOUBLE PRECISION NOT NULL,
    side            VARCHAR(8) NOT NULL DEFAULT '',  -- YES / NO
    edge            DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    actual_outcome  SMALLINT,                        -- NULL until resolved, 1=YES won, 0=NO won
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Rolling accuracy queries: recent predictions per game
CREATE INDEX IF NOT EXISTS idx_esports_pred_log_game_created
    ON esports_prediction_log (game, created_at DESC);

-- Resolution backfill: find unresolved predictions
CREATE INDEX IF NOT EXISTS idx_esports_pred_log_unresolved
    ON esports_prediction_log (actual_outcome)
    WHERE actual_outcome IS NULL;

-- Per-bot accuracy tracking
CREATE INDEX IF NOT EXISTS idx_esports_pred_log_bot
    ON esports_prediction_log (bot_name, created_at DESC);
