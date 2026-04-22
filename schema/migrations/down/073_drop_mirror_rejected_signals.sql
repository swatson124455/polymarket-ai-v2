-- Rollback migration 073: Remove mirror_rejected_signals instrumentation
--
-- Usage: apply manually ONLY if rolling back S172 7B Phase A. Not part of
-- normal release pipeline. The table is an append-only instrumentation log;
-- BACK UP attribution data before running — DROP is destructive.
--
-- BACKUP FIRST:
--   CREATE TABLE mirror_rejected_signals_bak AS SELECT * FROM mirror_rejected_signals;

DROP INDEX IF EXISTS idx_mirror_rej_stage_time;
DROP INDEX IF EXISTS idx_mirror_rej_unresolved;
DROP INDEX IF EXISTS idx_mirror_rej_market_time;
DROP INDEX IF EXISTS idx_mirror_rej_trader_time;

DROP TABLE IF EXISTS mirror_rejected_signals;
