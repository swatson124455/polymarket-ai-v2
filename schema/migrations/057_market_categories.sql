-- 057: market_categories — lightweight category + resolution lookup for markets
-- outside the top-1000 ingested into the `markets` table.
-- Populated by MirrorBot CLOB API fallback and one-time backfill script.
-- Used by elite_reliability tracker query (LEFT JOIN) to provide per-whale
-- per-category win rates for Factor 1 of the multi-factor confidence formula.

CREATE TABLE IF NOT EXISTS market_categories (
    condition_id    TEXT PRIMARY KEY,
    category        TEXT NOT NULL DEFAULT 'unknown',
    question        TEXT,
    yes_token_id    TEXT,
    no_token_id     TEXT,
    resolved        BOOLEAN DEFAULT FALSE,
    resolution      TEXT,              -- 'YES' or 'NO'
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_market_categories_category
    ON market_categories (category);

CREATE INDEX IF NOT EXISTS idx_market_categories_resolved
    ON market_categories (resolved) WHERE resolved = TRUE;
