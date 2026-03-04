-- Migration 023: Sports Betting Infrastructure
-- Applied: 2026-02-25 (renumbered from 022 to resolve collision with 022_weather_tables.sql)
-- Adds 7 tables: sports_players, sports_teams, sports_games, sports_injury_events,
--   sports_market_map, sports_calibration, sports_live_events

-- =====================================================================
-- TABLE: sports_players
-- Player master registry. name_variants JSONB allows fast fuzzy matching.
-- =====================================================================
CREATE TABLE IF NOT EXISTS sports_players (
    id              BIGSERIAL PRIMARY KEY,
    external_id     VARCHAR(100) NOT NULL,               -- SportsDataIO / ESPN player ID
    name            VARCHAR(200) NOT NULL,               -- canonical full name
    name_variants   JSONB        NOT NULL DEFAULT '[]',  -- ["LeBron", "King James", "LBJ"]
    team_id         VARCHAR(100),                        -- soft FK → sports_teams.external_id
    position        VARCHAR(20),                         -- NBA: PG/SG/SF/PF/C | NFL: QB/WR/RB/TE/K/DEF
    sport           VARCHAR(20)  NOT NULL,               -- nba, nfl, mlb, nhl, soccer, tennis
    status          VARCHAR(50)  DEFAULT 'active',       -- active, injured, retired, free_agent
    created_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sports_player_ext
    ON sports_players (external_id, sport);
CREATE INDEX IF NOT EXISTS idx_sports_player_sport_status
    ON sports_players (sport, status);
CREATE INDEX IF NOT EXISTS idx_sports_player_team
    ON sports_players (team_id);
CREATE INDEX IF NOT EXISTS idx_sports_player_name_gin
    ON sports_players USING GIN (name_variants jsonb_path_ops);
COMMENT ON TABLE sports_players IS 'Sports player master registry with name variants for NLP resolution';

-- =====================================================================
-- TABLE: sports_teams
-- =====================================================================
CREATE TABLE IF NOT EXISTS sports_teams (
    id              BIGSERIAL PRIMARY KEY,
    external_id     VARCHAR(100) NOT NULL,
    name            VARCHAR(200) NOT NULL,
    abbreviation    VARCHAR(10),
    sport           VARCHAR(20)  NOT NULL,
    conference      VARCHAR(100),
    created_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sports_team_ext
    ON sports_teams (external_id, sport);
COMMENT ON TABLE sports_teams IS 'Sports team master registry';

-- =====================================================================
-- TABLE: sports_games
-- Holds schedule, live state, and weather (critical for NFL/MLB/Soccer).
-- =====================================================================
CREATE TABLE IF NOT EXISTS sports_games (
    id              BIGSERIAL PRIMARY KEY,
    external_id     VARCHAR(100) NOT NULL,
    home_team_id    VARCHAR(100),                        -- matches sports_teams.external_id
    away_team_id    VARCHAR(100),
    sport           VARCHAR(20)  NOT NULL,
    start_time      TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    status          VARCHAR(30)  NOT NULL DEFAULT 'scheduled', -- scheduled/live/final/postponed/cancelled
    score_home      INTEGER,
    score_away      INTEGER,
    venue           VARCHAR(200),
    weather_summary JSONB,                               -- {wind_mph: 0, temp_f: 0, precip_pct: 0}
    created_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at      TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sports_game_ext
    ON sports_games (external_id, sport);
CREATE INDEX IF NOT EXISTS idx_sports_games_start_status
    ON sports_games (start_time, status);
CREATE INDEX IF NOT EXISTS idx_sports_games_sport_status
    ON sports_games (sport, status);
COMMENT ON TABLE sports_games IS 'Sports game schedule, live state, and environmental data';

-- =====================================================================
-- TABLE: sports_injury_events
-- Every injury/roster signal detected from any source is stored here.
-- Dedup via: same player_id + same source within 60 minutes is skipped.
-- =====================================================================
CREATE TABLE IF NOT EXISTS sports_injury_events (
    id              BIGSERIAL PRIMARY KEY,
    player_id       BIGINT,                              -- NULL if player resolution failed
    game_id         BIGINT,                              -- NULL if no game found (offseason)
    source          VARCHAR(50)  NOT NULL,               -- twitter/rss/reddit/discord/telegram/manual
    source_url      TEXT,
    raw_text        TEXT         NOT NULL,
    player_raw      VARCHAR(200),                        -- raw name before resolution
    detected_status VARCHAR(50),                         -- out/doubtful/questionable/day-to-day/free_agent_move
    severity        VARCHAR(30),                         -- season_ending/multi_week/day-to-day/offseason_move
    confidence      FLOAT        NOT NULL DEFAULT 0.0,
    nlp_tier        VARCHAR(20),                         -- regex/spacy/llm
    detected_at     TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    bet_triggered   BOOLEAN      DEFAULT FALSE,
    bet_market_id   VARCHAR(200)                         -- platform:market_id if bet was placed
);
CREATE INDEX IF NOT EXISTS idx_injury_player_game
    ON sports_injury_events (player_id, game_id);
CREATE INDEX IF NOT EXISTS idx_injury_detected_at
    ON sports_injury_events (detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_injury_source_player
    ON sports_injury_events (source, player_id);
CREATE INDEX IF NOT EXISTS idx_injury_player_detected
    ON sports_injury_events (player_id, detected_at DESC);
COMMENT ON TABLE sports_injury_events IS 'All injury/roster change events from all news sources';

-- =====================================================================
-- TABLE: sports_market_map
-- Maps sports games to their prediction market IDs on each platform.
-- UNIQUE(platform, market_id) prevents duplicate mappings.
-- =====================================================================
CREATE TABLE IF NOT EXISTS sports_market_map (
    id              BIGSERIAL PRIMARY KEY,
    game_id         BIGINT,
    platform        VARCHAR(30)  NOT NULL,               -- polymarket/kalshi/azuro/sx
    market_id       VARCHAR(200) NOT NULL,               -- platform-specific ID
    market_type     VARCHAR(50),                         -- outcome/spread/total/prop/live/draft/free_agency
    sport           VARCHAR(20),
    yes_token_id    VARCHAR(200),                        -- Polymarket YES token
    no_token_id     VARCHAR(200),                        -- Polymarket NO token
    current_price   FLOAT,                              -- last known YES price
    mapped_at       TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sports_market_map
    ON sports_market_map (platform, market_id);
CREATE INDEX IF NOT EXISTS idx_market_map_game
    ON sports_market_map (game_id);
CREATE INDEX IF NOT EXISTS idx_market_map_sport_platform
    ON sports_market_map (sport, platform);
COMMENT ON TABLE sports_market_map IS 'Maps sports games to prediction market IDs across platforms';

-- =====================================================================
-- TABLE: sports_calibration
-- Per-(sport, market_type) accuracy tracking for adaptive Kelly sizing.
-- =====================================================================
CREATE TABLE IF NOT EXISTS sports_calibration (
    id              BIGSERIAL PRIMARY KEY,
    sport           VARCHAR(20)  NOT NULL,
    market_type     VARCHAR(50)  NOT NULL,               -- outcome/prop/live/arb
    bet_count       INTEGER      NOT NULL DEFAULT 0,
    correct_count   INTEGER      NOT NULL DEFAULT 0,
    brier_score     FLOAT,                               -- rolling Brier score (lower = better)
    kelly_fraction  FLOAT        NOT NULL DEFAULT 0.25,  -- adaptive Kelly multiplier
    last_updated    TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'UTC')
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_sports_calibration
    ON sports_calibration (sport, market_type);
COMMENT ON TABLE sports_calibration IS 'Per-sport per-market-type accuracy for adaptive Kelly fraction';

-- =====================================================================
-- TABLE: sports_live_events
-- In-game events detected during live games for live betting triggers.
-- =====================================================================
CREATE TABLE IF NOT EXISTS sports_live_events (
    id              BIGSERIAL PRIMARY KEY,
    game_id         BIGINT,
    sport           VARCHAR(20),
    event_type      VARCHAR(50)  NOT NULL,               -- blowout/player_hot/momentum/weather/injury_in_game
    description     TEXT,
    elapsed_pct     FLOAT,                               -- 0.0-1.0 percentage of game elapsed
    score_diff      INTEGER,                             -- abs(home - away) at time of detection
    detected_at     TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    bet_triggered   BOOLEAN      DEFAULT FALSE,
    bet_market_id   VARCHAR(200)
);
CREATE INDEX IF NOT EXISTS idx_live_events_game
    ON sports_live_events (game_id, detected_at DESC);
CREATE INDEX IF NOT EXISTS idx_live_events_type
    ON sports_live_events (event_type, detected_at DESC);
COMMENT ON TABLE sports_live_events IS 'In-game events for live betting (blowout, momentum, weather)';
