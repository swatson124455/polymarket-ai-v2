-- Migration 031: Persist Glicko-2 ratings across restarts
-- Previously, Glicko-2 ratings for all 8 esports games were rebuilt from
-- esports_training_data on every bot restart (~5-10 min cold start, ratings lost).
-- This table stores computed ratings so they survive restarts.

CREATE TABLE IF NOT EXISTS glicko2_ratings (
    id              BIGSERIAL PRIMARY KEY,
    game            VARCHAR(20)  NOT NULL,   -- lol, cs2, dota2, valorant, cod, r6, sc2, rl
    team_key        VARCHAR(200) NOT NULL,   -- lowercased team name (matches Glicko2Tracker key)
    mu              DOUBLE PRECISION NOT NULL DEFAULT 1500.0,
    phi             DOUBLE PRECISION NOT NULL DEFAULT 350.0,
    sigma           DOUBLE PRECISION NOT NULL DEFAULT 0.06,
    match_count     INTEGER NOT NULL DEFAULT 0,
    updated_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_glicko2_game_team
    ON glicko2_ratings (game, team_key);

CREATE INDEX IF NOT EXISTS idx_glicko2_game
    ON glicko2_ratings (game);
