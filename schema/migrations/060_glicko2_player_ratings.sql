-- Migration 060: Player-level Glicko-2 ratings for Phase 10B
-- Stores per-player ratings that feed composite team strength via update_roster().

CREATE TABLE IF NOT EXISTS glicko2_player_ratings (
    id              BIGSERIAL PRIMARY KEY,
    game            VARCHAR(20)  NOT NULL,
    player_id       VARCHAR(200) NOT NULL,
    mu              DOUBLE PRECISION NOT NULL DEFAULT 1500.0,
    phi             DOUBLE PRECISION NOT NULL DEFAULT 350.0,
    sigma           DOUBLE PRECISION NOT NULL DEFAULT 0.06,
    match_count     INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_glicko2_player_game_id
    ON glicko2_player_ratings (game, player_id);

CREATE INDEX IF NOT EXISTS idx_glicko2_player_game
    ON glicko2_player_ratings (game);
