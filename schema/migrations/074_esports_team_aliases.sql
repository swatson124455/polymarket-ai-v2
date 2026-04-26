-- S195 Phase B: esports_team_aliases + esports_unmatched_predictions.
--
-- Replaces the naive substring matcher in
-- esports/markets/esports_market_scanner.py:130-132 that requires the
-- PandaScore team name to appear verbatim (lowercased) inside the
-- Polymarket question text. Org rebrands, sponsorship suffixes,
-- abbreviations, and language variants silently dropped predictions.
--
-- Two tables:
--   1. esports_team_aliases  — canonical_name ↔ alias mapping. Seeded by
--      a separate script that ingests PandaScore team data and scans
--      active Polymarket esports questions, cross-linked via fuzzy match.
--   2. esports_unmatched_predictions — every shadow prediction that found
--      no Polymarket market gets logged here so we can review top-missing
--      aliases over time and grow the alias table organically.
--
-- Idempotent: CREATE IF NOT EXISTS on tables and indexes. Safe to re-run.
-- Reversibility: see schema/migrations/down/074_drop_esports_team_aliases.sql.

CREATE TABLE IF NOT EXISTS esports_team_aliases (
    id              BIGSERIAL PRIMARY KEY,
    canonical_name  TEXT NOT NULL,
    alias           TEXT NOT NULL,
    -- Where this alias came from: 'pandascore', 'polymarket_question',
    -- 'manual', 'fuzzy_link'. Used by the seed builder for confidence
    -- weighting and for triaging bad aliases when matches go wrong.
    source          TEXT NOT NULL DEFAULT 'manual',
    -- Match quality if the alias was discovered via fuzzy link
    -- (NULL for manual/source=pandascore exact aliases). Range 0.0-1.0.
    confidence      DOUBLE PRECISION,
    -- Game tag (lol/cs2/dota2/valorant/cod/r6/sc2/rl) — restricts matching
    -- so e.g. "Liquid" in League doesn't match a Dota market by accident.
    game            TEXT,
    created_at      TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    UNIQUE (canonical_name, alias, game)
);

-- Plain alias index — matches the ORM's Index("idx_eta_alias_lc", "alias", "game")
-- which the SQLAlchemy Base.metadata.create_all path emits.
CREATE INDEX IF NOT EXISTS idx_eta_alias_lc
    ON esports_team_aliases (alias, game);

-- Functional index on LOWER(alias) — accelerates the matcher's
-- case-insensitive load query (`SELECT LOWER(alias) ... FROM esports_team_aliases`).
-- Replaces the GENERATED `alias_lc` column from the v1 migration draft;
-- ORM-managed schema doesn't expose generated columns cleanly.
CREATE INDEX IF NOT EXISTS idx_eta_alias_lower
    ON esports_team_aliases (LOWER(alias), game);

-- Reverse direction: canonical → all aliases for that team.
-- Used by the matcher to expand a single PandaScore team name into the
-- full set of variants to test against the market question.
CREATE INDEX IF NOT EXISTS idx_eta_canonical
    ON esports_team_aliases (canonical_name, game);


CREATE TABLE IF NOT EXISTS esports_unmatched_predictions (
    id                      BIGSERIAL PRIMARY KEY,
    event_time              TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    match_id                TEXT NOT NULL,
    team_a                  TEXT NOT NULL,
    team_b                  TEXT NOT NULL,
    game                    TEXT NOT NULL,
    -- How many candidate markets the scanner DID return before all of them
    -- failed the team-name filter. 0 = no candidates. >0 = candidates exist
    -- but team names don't appear in any of their questions (alias gap).
    candidate_markets_count INTEGER NOT NULL DEFAULT 0,
    -- Optional snapshot of question text from the closest candidate so
    -- a human reviewing the table can spot the actual mismatch fast
    -- (e.g. "AaB Esport" predicted but question said "Aalborg").
    closest_question        TEXT,
    -- Optional rapidfuzz score against the closest question for triage.
    closest_score           DOUBLE PRECISION
);

-- Per-match dedup: don't log the same unmatched prediction over and over.
-- Matcher should INSERT ... ON CONFLICT DO NOTHING using this unique key.
CREATE UNIQUE INDEX IF NOT EXISTS idx_eup_match_dedup
    ON esports_unmatched_predictions (match_id, team_a, team_b);

-- Time-descending scan for "top missing aliases" reports.
CREATE INDEX IF NOT EXISTS idx_eup_recent
    ON esports_unmatched_predictions (event_time DESC);


COMMENT ON TABLE esports_team_aliases IS 'S195: PandaScore team name to Polymarket question variant mapping. Replaces naive substring matcher; grown by seed script + matcher feedback.';

COMMENT ON TABLE esports_unmatched_predictions IS 'S195: Shadow predictions where no Polymarket market matched. Daily report surfaces top missing aliases for review.';
