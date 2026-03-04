-- Migration 025: Latency Instrumentation + Index Maintenance (Session 43)
--
-- 1. Adds latency_ms column to paper_trades for order execution latency tracking
-- 2. Adds composite partial index on prediction_log to eliminate 37M sequential reads
-- 3. Drops verified-unused indexes (6 on market_prices, 1 on trades)
--
-- All statements are idempotent (IF NOT EXISTS / IF EXISTS).

-- 1. Add latency_ms column to paper_trades (nullable — historical rows unaffected)
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS latency_ms DOUBLE PRECISION;

-- 2. Composite partial index on prediction_log for resolved+labeled queries
--    Most common pattern: WHERE resolution IN ('YES','NO') AND was_correct IS NOT NULL
--    This eliminates sequential scans (37M tuples) in Platt scaling, Brier score, performance tracking.
CREATE INDEX IF NOT EXISTS idx_prediction_log_resolved_accuracy
    ON prediction_log (resolution, was_correct, resolved_at DESC)
    WHERE resolution IN ('YES', 'NO') AND was_correct IS NOT NULL;

-- 3. Drop verified-unused indexes (confirmed 0 scans via pg_stat_user_indexes, Session 43 audit)
--    market_prices: 5 redundant/unused indexes consuming disk + slowing writes
DROP INDEX IF EXISTS idx_prices_market_timestamp;
DROP INDEX IF EXISTS idx_prices_partition;
DROP INDEX IF EXISTS ix_market_prices_partition_month;
DROP INDEX IF EXISTS idx_prices_side;
DROP INDEX IF EXISTS ix_market_prices_token_id;
--    trades: composite that is never used (queries use separate market_id + timestamp)
DROP INDEX IF EXISTS idx_trades_market_timestamp;
--    decision_events: kept for now (event sourcing may use them later)
