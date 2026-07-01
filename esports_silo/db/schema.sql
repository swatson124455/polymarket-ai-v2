-- esports_silo schema — clean, from zero, its own database.
-- No shared tables, no 024-vs-072 collision, no dependency on the 15-bot system.
--
-- Design commandments (see README):
--   * odds_raw is APPEND-ONLY (INSERT only; never UPDATE/DELETE)
--   * every fact row carries event_time AND ingest_time (look-ahead defense)
--   * odds are stored RAW — NO de-vig, ever
--   * game membership is explicit (no reliance on a 'category' tag)
--   * no P&L column feeds a model

-- ---------------------------------------------------------------------------
-- matches — results ground truth (labels for backtest + resolution)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS matches (
    match_id     TEXT PRIMARY KEY,
    game         TEXT        NOT NULL,          -- cs2 | lol | dota2 | valorant
    event_tier   TEXT,                          -- s_tier|a_tier|b_tier|c_tier (competition weighting)
    team_a       TEXT        NOT NULL,
    team_b       TEXT        NOT NULL,
    winner       TEXT,                          -- 'team_a' | 'team_b' | NULL until settled
    score_a      INTEGER,                       -- series score (e.g. 2 in a 2-1 BO3)
    score_b      INTEGER,
    best_of      INTEGER,
    start_time   TIMESTAMPTZ NOT NULL,          -- EVENT time (match scheduled start)
    patch        TEXT,
    source       TEXT        NOT NULL,          -- pandascore | oracle | jsonl | ...
    raw_data     JSONB       NOT NULL DEFAULT '{}',  -- source payload catch-all (nothing lost)
    ingest_time  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_matches_game_start ON matches (game, start_time);

-- ---------------------------------------------------------------------------
-- odds_raw — APPEND-ONLY sharp-book lines. RAW decimal odds (no de-vig).
-- One row per (match, book, observation). Never updated: a new observation
-- of the same match/book is a NEW row, so line movement is preserved.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS odds_raw (
    id           BIGSERIAL PRIMARY KEY,
    match_id     TEXT        NOT NULL REFERENCES matches (match_id),
    book         TEXT        NOT NULL,          -- pinnacle | circa | <asian book>
    aggregator   TEXT        NOT NULL,          -- which aggregator returned it
    team_a_odds  DOUBLE PRECISION,              -- decimal odds, RAW (vig NOT removed)
    team_b_odds  DOUBLE PRECISION,              -- decimal odds, RAW (vig NOT removed)
    is_closing   BOOLEAN     NOT NULL DEFAULT FALSE,
    line_time    TIMESTAMPTZ NOT NULL,          -- when the BOOK displayed this line
    ingest_time  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Query index only. NO unique constraint that would tempt an upsert —
-- this table is append-only by design.
CREATE INDEX IF NOT EXISTS idx_odds_match_book_time ON odds_raw (match_id, book, line_time);

-- ---------------------------------------------------------------------------
-- polymarket_snapshots — live CLOB observations. Price/volume from the LIVE
-- API only; the prior bot's stored liquidity/volume columns read $0 on liquid
-- markets and are banned as a source.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS polymarket_snapshots (
    id             BIGSERIAL PRIMARY KEY,
    market_id      TEXT        NOT NULL,
    question       TEXT,
    game           TEXT,
    team_a         TEXT,
    team_b         TEXT,
    yes_price      DOUBLE PRECISION,            -- price of the team_a = YES token
    volume_24h     DOUBLE PRECISION,            -- from LIVE CLOB only
    snapshot_time  TIMESTAMPTZ NOT NULL,
    ingest_time    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pm_market_time ON polymarket_snapshots (market_id, snapshot_time);

-- ---------------------------------------------------------------------------
-- predictions — the bot's forecasts + decisions. `edge` is a DIAGNOSTIC only;
-- the decision rule must defer to price (the old "large edge => bet" rule
-- selected for the model's worst errors — do not repeat it).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS predictions (
    id             BIGSERIAL PRIMARY KEY,
    match_id       TEXT REFERENCES matches (match_id),
    market_id      TEXT,
    model_version  TEXT        NOT NULL,
    signal_source  TEXT        NOT NULL,        -- e.g. 'sharp_consensus_v1'
    p_model        DOUBLE PRECISION NOT NULL,   -- P(team_a wins), calibrated
    market_price   DOUBLE PRECISION,            -- team_a = YES price at decision
    edge           DOUBLE PRECISION,            -- p_model - market_price (DIAGNOSTIC ONLY)
    decision       TEXT        NOT NULL,        -- 'bet_a' | 'bet_b' | 'no_bet'
    event_time     TIMESTAMPTZ NOT NULL,        -- match start — look-ahead anchor
    ingest_time    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_pred_match ON predictions (match_id);
CREATE INDEX IF NOT EXISTS idx_pred_event_time ON predictions (event_time);

-- ---------------------------------------------------------------------------
-- team_aliases — the resolver (the hardest piece to rebuild). Carry the
-- 1,777 rows from the prior bot. alias -> canonical, scoped by game.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS team_aliases (
    alias      TEXT NOT NULL,
    canonical  TEXT NOT NULL,
    game       TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (alias, game)
);
