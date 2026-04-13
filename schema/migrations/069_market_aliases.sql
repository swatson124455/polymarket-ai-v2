-- S172 Phase 1E-a: market_aliases table.
-- Centralizes the market_id ↔ condition_id mapping that's currently duplicated
-- as OR clauses in 10+ queries across the codebase.
--
-- Schema only — no code changes in this commit. 1E-b wires the gateway.

CREATE TABLE IF NOT EXISTS market_aliases (
    -- The canonical market_id (markets.id cast to text)
    canonical_id TEXT NOT NULL,
    -- An alias that also refers to this market (e.g. condition_id)
    alias_id TEXT NOT NULL,
    -- What kind of alias (for debugging/audit)
    alias_type TEXT NOT NULL DEFAULT 'condition_id',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT NOW(),
    PRIMARY KEY (canonical_id, alias_id)
);

-- Fast lookup by alias (the common query direction)
CREATE INDEX IF NOT EXISTS idx_market_aliases_alias
    ON market_aliases (alias_id);

-- Populate from existing markets table (condition_id → id mapping)
INSERT INTO market_aliases (canonical_id, alias_id, alias_type)
SELECT CAST(id AS TEXT), condition_id, 'condition_id'
FROM markets
WHERE condition_id IS NOT NULL
  AND condition_id != ''
  AND CAST(id AS TEXT) != condition_id
ON CONFLICT DO NOTHING;

COMMENT ON TABLE market_aliases IS 'S172 1E-a: Canonical market_id ↔ alias mapping (condition_id, slug, etc.)';
