-- 072_esports_v2.sql
-- Phase 5v2-A2: EsportsBot v2 schema — 6 new tables for Rating Trinity + meta-model pipeline.
-- All new tables. No modifications to existing tables.
-- Apply as postgres user due to ownership issue (see CLAUDE.md).

-- 1. esports_matches: Historical + live match results (CS2 + LoL)
CREATE TABLE IF NOT EXISTS esports_matches (
    match_id TEXT PRIMARY KEY,
    game TEXT NOT NULL,                      -- 'cs2' or 'lol'
    event_name TEXT,                         -- e.g. 'IEM Katowice 2025'
    event_tier TEXT,                         -- 's_tier', 'a_tier', 'b_tier', etc.
    team_a TEXT NOT NULL,
    team_b TEXT NOT NULL,
    winner TEXT,                             -- team name or NULL if unresolved
    score_a INTEGER,
    score_b INTEGER,
    best_of INTEGER,                         -- 1, 3, 5
    map TEXT,                                -- per-map results (CS2)
    patch TEXT,                              -- game patch version
    match_date TIMESTAMPTZ NOT NULL,
    is_lan BOOLEAN DEFAULT FALSE,
    source TEXT NOT NULL,                    -- 'grid', 'hltv', 'oracle'
    raw_data JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. esports_players: Player roster tracking for OpenSkill
CREATE TABLE IF NOT EXISTS esports_players (
    player_id TEXT PRIMARY KEY,
    game TEXT NOT NULL,
    ign TEXT NOT NULL,                       -- in-game name
    team TEXT,                               -- current team (NULL if free agent)
    role TEXT,                               -- 'awper', 'igl', 'rifler', 'adc', 'mid', etc.
    active BOOLEAN DEFAULT TRUE,
    first_seen TIMESTAMPTZ,
    last_seen TIMESTAMPTZ,
    metadata JSONB DEFAULT '{}'
);

-- 3. esports_ratings: Snapshots of all 3 rating systems after each match
CREATE TABLE IF NOT EXISTS esports_ratings (
    id BIGSERIAL PRIMARY KEY,
    entity_type TEXT NOT NULL,               -- 'team' or 'player'
    entity_id TEXT NOT NULL,                 -- team name or player_id
    game TEXT NOT NULL,                      -- 'cs2' or 'lol'
    system TEXT NOT NULL,                    -- 'elo', 'glicko2', 'openskill'
    rating DOUBLE PRECISION NOT NULL,
    deviation DOUBLE PRECISION,              -- Glicko-2 RD / OpenSkill sigma
    volatility DOUBLE PRECISION,             -- Glicko-2 volatility
    matches_played INTEGER DEFAULT 0,
    snapshot_time TIMESTAMPTZ NOT NULL,
    match_id TEXT REFERENCES esports_matches(match_id),
    UNIQUE(entity_id, game, system, match_id)
);

-- Partial unique index for initial ratings (match_id IS NULL)
CREATE UNIQUE INDEX IF NOT EXISTS idx_esports_ratings_initial
    ON esports_ratings(entity_id, game, system) WHERE match_id IS NULL;

-- 4. esports_features: Pre-computed feature vectors per match for XGBoost
CREATE TABLE IF NOT EXISTS esports_features (
    match_id TEXT PRIMARY KEY REFERENCES esports_matches(match_id),
    p_elo DOUBLE PRECISION,                  -- Elo predicted prob for team_a
    p_glicko DOUBLE PRECISION,               -- Glicko-2 predicted prob for team_a
    p_openskill DOUBLE PRECISION,            -- OpenSkill predicted prob for team_a
    trinity_spread DOUBLE PRECISION,         -- max(p) - min(p)
    trinity_mean DOUBLE PRECISION,           -- mean of 3 probabilities
    features JSONB NOT NULL,                 -- full feature dict for XGBoost
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 5. esports_predictions: Model outputs (backtest, shadow, paper, live)
CREATE TABLE IF NOT EXISTS esports_predictions (
    id BIGSERIAL PRIMARY KEY,
    match_id TEXT REFERENCES esports_matches(match_id),
    game TEXT NOT NULL,
    predicted_winner TEXT,
    p_model DOUBLE PRECISION NOT NULL,       -- calibrated model probability
    p_raw DOUBLE PRECISION,                  -- raw XGBoost output
    conformal_set TEXT[],                    -- e.g. {'team_a'} or {'team_a','team_b'}
    is_singleton BOOLEAN,                    -- TRUE = confident enough to bet
    market_price DOUBLE PRECISION,           -- Polymarket price at prediction time
    pinnacle_odds DOUBLE PRECISION,          -- Pinnacle closing odds (CLV benchmark)
    edge DOUBLE PRECISION,                   -- p_model - market_price
    kelly_fraction DOUBLE PRECISION,         -- recommended Kelly bet fraction
    actual_winner TEXT,                       -- filled after resolution
    correct BOOLEAN,                         -- filled after resolution
    mode TEXT NOT NULL,                      -- 'backtest', 'shadow', 'paper', 'live'
    model_version TEXT,                      -- e.g. 'v2-trinity-1.0'
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 6. esports_odds: External odds for CLV tracking
CREATE TABLE IF NOT EXISTS esports_odds (
    id BIGSERIAL PRIMARY KEY,
    match_id TEXT REFERENCES esports_matches(match_id),
    source TEXT DEFAULT 'pinnacle',
    team_a_odds DOUBLE PRECISION,
    team_b_odds DOUBLE PRECISION,
    captured_at TIMESTAMPTZ NOT NULL,
    is_closing BOOLEAN DEFAULT FALSE
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_esports_matches_game_date
    ON esports_matches(game, match_date DESC);

CREATE INDEX IF NOT EXISTS idx_esports_ratings_entity
    ON esports_ratings(entity_id, game, system);

CREATE INDEX IF NOT EXISTS idx_esports_predictions_mode
    ON esports_predictions(mode, game);

CREATE INDEX IF NOT EXISTS idx_esports_predictions_match
    ON esports_predictions(match_id);

CREATE INDEX IF NOT EXISTS idx_esports_odds_match
    ON esports_odds(match_id);

CREATE INDEX IF NOT EXISTS idx_esports_players_game_team
    ON esports_players(game, team);
