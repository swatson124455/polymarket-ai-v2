-- ============================================================
-- Migration 017: Add NegRisk defense columns to markets table
-- ============================================================
-- MUST be run via DIRECT psql connection (not through Supabase pooler).
-- The session pooler enforces ~10s statement_timeout which blocks ALTER TABLE
-- because Supabase internal processes hold AccessShareLock on markets.
--
-- How to run:
--   1. Go to Supabase Dashboard → Project Settings → Database → Connection String (Direct)
--   2. Copy the "URI" connection string
--   3. Run: psql "postgresql://postgres:PASSWORD@db.PROJECT.supabase.co:5432/postgres"
--   4. Paste this file contents and run
--
-- Alternatively from Windows:
--   psql "direct-connection-string" -f scripts/apply_migration_017_direct.sql
-- ============================================================

-- Disable statement timeout for this session (DDL needs it)
SET statement_timeout = '0';
SET lock_timeout = '30s';

BEGIN;

ALTER TABLE markets ADD COLUMN IF NOT EXISTS neg_risk BOOLEAN DEFAULT false;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS outcome_count INTEGER DEFAULT 2;

-- Index for quick filtering of neg_risk markets in scan queries
CREATE INDEX IF NOT EXISTS idx_markets_neg_risk ON markets (neg_risk) WHERE neg_risk = true;

COMMIT;

-- Verify
SELECT column_name, data_type, column_default
FROM information_schema.columns
WHERE table_name = 'markets' AND column_name IN ('neg_risk', 'outcome_count');
