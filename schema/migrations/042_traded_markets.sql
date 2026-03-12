-- 042: traded_markets — small lookup table of markets we actually bet on.
-- Eliminates the expensive EXISTS subquery in resolution backfill (Phase 2a)
-- that scans 37k+ markets when only ~100-200 are ours.

CREATE TABLE IF NOT EXISTS traded_markets (
    market_id       TEXT PRIMARY KEY,
    condition_id    TEXT,
    bot_names       TEXT NOT NULL,
    first_trade_at  TIMESTAMP NOT NULL,
    resolved        BOOLEAN NOT NULL DEFAULT FALSE,
    resolution      TEXT,
    resolved_at     TIMESTAMP,
    last_checked_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_traded_markets_unresolved
    ON traded_markets (resolved) WHERE resolved = FALSE;

-- Seed from existing paper_trades
INSERT INTO traded_markets (market_id, condition_id, bot_names, first_trade_at, resolved, resolution, resolved_at)
SELECT pt.market_id, m.condition_id,
       string_agg(DISTINCT pt.bot_name, ','),
       MIN(pt.created_at),
       COALESCE(m.resolved, FALSE),
       m.resolution,
       m.resolved_at
FROM paper_trades pt
LEFT JOIN markets m ON pt.market_id = m.id::text OR pt.market_id = m.condition_id
WHERE pt.side IN ('YES', 'NO')
GROUP BY pt.market_id, m.condition_id, m.resolved, m.resolution, m.resolved_at
ON CONFLICT (market_id) DO NOTHING;
