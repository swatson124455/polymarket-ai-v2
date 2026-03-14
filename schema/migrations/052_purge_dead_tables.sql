-- Migration 052: Purge 5 dead tables + add missing correlation_id to paper_trades
-- These tables have zero readers (verified by 5 independent audits):
--   position_snapshots: 594 rows, daily watchdog writer, 0 readers
--   trade_model_linkage: 0 rows, paper_trading hook writer, 0 readers
--   model_registry: 0 rows, prediction_engine writer (wrong kwargs), 0 readers
--   model_performance_daily: 0 rows, watchdog writer (buggy), 0 readers
--   feature_sets: 0 rows, no writer, 0 readers

DROP TABLE IF EXISTS position_snapshots CASCADE;
DROP TABLE IF EXISTS trade_model_linkage CASCADE;
DROP TABLE IF EXISTS model_registry CASCADE;
DROP TABLE IF EXISTS model_performance_daily CASCADE;
DROP TABLE IF EXISTS feature_sets CASCADE;

-- F6: correlation_id written by insert_paper_trade() but missing from migrations.
-- Column likely exists in live DB via ORM auto-create. This makes it explicit.
ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS correlation_id TEXT;
CREATE INDEX IF NOT EXISTS idx_paper_trades_correlation
    ON paper_trades(correlation_id) WHERE correlation_id IS NOT NULL;
