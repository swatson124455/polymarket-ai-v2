-- Migration 024: Esports Trading Infrastructure
-- Applied: 2026-03-01
-- Adds 8 tables: esports_teams, esports_players, esports_matches, esports_match_maps,
--   esports_market_map, esports_calibration, esports_live_events, esports_patch_history

-- =====================================================================
-- TABLE: esports_teams
-- Team registry for LoL, CS2, Dota 2, Valorant.
-- =====================================================================
CREATE TABLE IF NOT EXISTS esports_teams (
    id              BIGSERIAL PRIMARY KEY,
    external_id     VARCHAR(100) NOT NULL,               -- PandaScore team ID
    name            VARCHAR(200) NOT NULL,
    abbreviation    VARCHAR(20),
    game            VARCHAR(20)  NOT NULL,               -- lol / cs2 / dota2 / valorant
    region          VARCHAR(50),                         -- NA / EU / KR / CN / SEA / etc
    rating          FLOAT,                               -- HLTV rating or equivalent
    created_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_esports_team_ext
    ON esports_teams (external_id, game);
CREATE INDEX IF NOT EXISTS idx_esports_team_game
    ON esports_teams (game);
COMMENT ON TABLE esports_teams IS 'Esports team registry (PandaScore + HLTV data)';

-- =====================================================================
-- TABLE: esports_players
-- Player registry with role information.
-- =====================================================================
CREATE TABLE IF NOT EXISTS esports_players (
    id              BIGSERIAL PRIMARY KEY,
    external_id     VARCHAR(100) NOT NULL,               -- PandaScore player ID
    name            VARCHAR(200) NOT NULL,               -- In-game name (IGN)
    real_name       VARCHAR(200),
    team_id         VARCHAR(100),                        -- soft FK -> esports_teams.external_id
    game            VARCHAR(20)  NOT NULL,
    role            VARCHAR(30),                         -- top/jungle/mid/adc/support (LoL), entry/awp/igl/lurk/support (CS2)
    created_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_esports_player_ext
    ON esports_players (external_id, game);
CREATE INDEX IF NOT EXISTS idx_esports_player_team
    ON esports_players (team_id);
COMMENT ON TABLE esports_players IS 'Esports player registry with role/position data';

-- =====================================================================
-- TABLE: esports_matches
-- Match schedule, live state, and series information.
-- =====================================================================
CREATE TABLE IF NOT EXISTS esports_matches (
    id              BIGSERIAL PRIMARY KEY,
    external_id     VARCHAR(100) NOT NULL,               -- PandaScore match ID
    team_a_id       VARCHAR(100),                        -- -> esports_teams.external_id
    team_b_id       VARCHAR(100),
    game            VARCHAR(20)  NOT NULL,
    tournament      VARCHAR(200),
    best_of         INTEGER      NOT NULL DEFAULT 1,     -- BO1/BO3/BO5
    score_a         INTEGER      DEFAULT 0,              -- maps won by team A
    score_b         INTEGER      DEFAULT 0,              -- maps won by team B
    status          VARCHAR(30)  NOT NULL DEFAULT 'not_started',  -- not_started/running/finished/canceled
    start_time      TIMESTAMP WITHOUT TIME ZONE,
    patch_version   VARCHAR(30),                         -- game patch at time of match
    created_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_esports_match_ext
    ON esports_matches (external_id, game);
CREATE INDEX IF NOT EXISTS idx_esports_match_status
    ON esports_matches (status, start_time);
CREATE INDEX IF NOT EXISTS idx_esports_match_game_status
    ON esports_matches (game, status);
CREATE INDEX IF NOT EXISTS idx_esports_match_teams
    ON esports_matches (team_a_id, team_b_id);
COMMENT ON TABLE esports_matches IS 'Esports match schedule + series state';

-- =====================================================================
-- TABLE: esports_match_maps
-- Per-map state within a BO3/BO5 series.
-- =====================================================================
CREATE TABLE IF NOT EXISTS esports_match_maps (
    id              BIGSERIAL PRIMARY KEY,
    match_id        VARCHAR(100) NOT NULL,               -- -> esports_matches.external_id
    map_number      INTEGER      NOT NULL,               -- 1, 2, 3, 4, 5
    map_name        VARCHAR(50),                         -- Dust2 / Nuke / Inferno (CS2), Summoner's Rift (LoL)
    winner_team_id  VARCHAR(100),                        -- NULL if in progress
    score_a         INTEGER      DEFAULT 0,              -- LoL: N/A, CS2: rounds won by team A
    score_b         INTEGER      DEFAULT 0,
    game_state      JSONB,                               -- live in-game data (gold diff, round economy, etc.)
    started_at      TIMESTAMP WITHOUT TIME ZONE,
    finished_at     TIMESTAMP WITHOUT TIME ZONE
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_esports_match_map
    ON esports_match_maps (match_id, map_number);
CREATE INDEX IF NOT EXISTS idx_esports_match_maps_match
    ON esports_match_maps (match_id);
COMMENT ON TABLE esports_match_maps IS 'Per-map state within BO3/BO5 series';

-- =====================================================================
-- TABLE: esports_market_map
-- Maps esports matches to their Polymarket market IDs.
-- =====================================================================
CREATE TABLE IF NOT EXISTS esports_market_map (
    id              BIGSERIAL PRIMARY KEY,
    match_id        VARCHAR(100),                        -- -> esports_matches.external_id
    platform        VARCHAR(30)  NOT NULL DEFAULT 'polymarket',
    market_id       VARCHAR(200) NOT NULL,               -- Polymarket condition_id
    market_type     VARCHAR(50),                         -- match_winner/map_winner/tournament_winner/total_maps/props
    game            VARCHAR(20),
    yes_token_id    VARCHAR(200),                        -- Polymarket YES token
    no_token_id     VARCHAR(200),                        -- Polymarket NO token
    current_price   FLOAT,                               -- last known YES price
    mapped_at       TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_esports_market_map
    ON esports_market_map (platform, market_id);
CREATE INDEX IF NOT EXISTS idx_esports_market_map_match
    ON esports_market_map (match_id);
CREATE INDEX IF NOT EXISTS idx_esports_market_map_game
    ON esports_market_map (game, platform);
COMMENT ON TABLE esports_market_map IS 'Maps esports matches to Polymarket market IDs';

-- =====================================================================
-- TABLE: esports_calibration
-- Per-(game, market_type) accuracy tracking for adaptive Kelly sizing.
-- =====================================================================
CREATE TABLE IF NOT EXISTS esports_calibration (
    id              BIGSERIAL PRIMARY KEY,
    game            VARCHAR(20)  NOT NULL,               -- lol / cs2 / dota2 / valorant
    market_type     VARCHAR(50)  NOT NULL,               -- match_winner/map_winner/live_event/series
    bet_count       INTEGER      NOT NULL DEFAULT 0,
    correct_count   INTEGER      NOT NULL DEFAULT 0,
    brier_score     FLOAT,                               -- rolling Brier score (lower = better)
    kelly_fraction  FLOAT        NOT NULL DEFAULT 0.25,  -- adaptive Kelly multiplier
    last_updated    TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_esports_calibration
    ON esports_calibration (game, market_type);
COMMENT ON TABLE esports_calibration IS 'Per-game per-market-type accuracy for adaptive Kelly fraction';

-- =====================================================================
-- TABLE: esports_live_events
-- In-game events detected during live matches.
-- =====================================================================
CREATE TABLE IF NOT EXISTS esports_live_events (
    id              BIGSERIAL PRIMARY KEY,
    match_id        VARCHAR(100),
    game            VARCHAR(20),
    event_type      VARCHAR(50)  NOT NULL,               -- baron_take/elder_dragon/economy_break/round_streak/map_clinch/blowout
    description     TEXT,
    confidence      FLOAT,
    map_number      INTEGER,
    edge_estimate   FLOAT,
    market_side     VARCHAR(5),                          -- YES / NO
    detected_at     TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    bet_triggered   BOOLEAN      DEFAULT FALSE,
    bet_market_id   VARCHAR(200)
);
CREATE INDEX IF NOT EXISTS idx_esports_live_events_match
    ON esports_live_events (match_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_esports_live_events_type
    ON esports_live_events (event_type, detected_at DESC);
COMMENT ON TABLE esports_live_events IS 'In-game events for live esports betting';

-- =====================================================================
-- TABLE: esports_patch_history
-- Tracks game patch versions and observation mode timestamps.
-- =====================================================================
CREATE TABLE IF NOT EXISTS esports_patch_history (
    id              BIGSERIAL PRIMARY KEY,
    game            VARCHAR(20)  NOT NULL,
    patch_version   VARCHAR(30)  NOT NULL,
    detected_at     TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    observation_end TIMESTAMP WITHOUT TIME ZONE,         -- NULL if still in observation
    retrain_needed  BOOLEAN      DEFAULT TRUE,
    retrained_at    TIMESTAMP WITHOUT TIME ZONE
);
CREATE INDEX IF NOT EXISTS idx_esports_patch_game
    ON esports_patch_history (game, detected_at DESC);
COMMENT ON TABLE esports_patch_history IS 'Game patch version history and observation mode tracking';
