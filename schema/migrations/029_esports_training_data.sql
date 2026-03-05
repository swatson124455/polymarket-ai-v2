-- Migration 029: Esports training data table for model training pipeline
-- Stores historical match snapshots extracted from PandaScore for LoL/CS2 model training

CREATE TABLE IF NOT EXISTS esports_training_data (
    id              BIGSERIAL PRIMARY KEY,
    match_id        VARCHAR(64) NOT NULL,
    game            VARCHAR(16) NOT NULL,          -- lol / cs2 / dota2 / valorant
    team_a          VARCHAR(128) NOT NULL DEFAULT '',
    team_b          VARCHAR(128) NOT NULL DEFAULT '',
    patch           VARCHAR(32) NOT NULL DEFAULT '',
    game_state_json JSONB NOT NULL DEFAULT '{}',   -- feature snapshot (FEATURE_NAMES for LoL, ROUND_FEATURES for CS2)
    outcome         SMALLINT NOT NULL,              -- 1 = team_a/blue won, 0 = team_b/red won
    snapshot_type   VARCHAR(32) NOT NULL DEFAULT 'match',  -- 'match' for LoL game-level, 'round' for CS2 round-level
    tournament      VARCHAR(256) NOT NULL DEFAULT '',
    scheduled_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for training queries: fetch all rows for a game, ordered by recency
CREATE INDEX IF NOT EXISTS idx_esports_training_game_created
    ON esports_training_data (game, created_at DESC);

-- Unique constraint: one snapshot per match+snapshot_type combo (dedup on re-collection)
CREATE UNIQUE INDEX IF NOT EXISTS idx_esports_training_match_snapshot
    ON esports_training_data (match_id, snapshot_type, game)
    WHERE snapshot_type = 'match';

-- For CS2 round-level data: match_id + round number (stored in game_state_json)
-- No unique constraint on rounds since multiple rounds per match are expected
