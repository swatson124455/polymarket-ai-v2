-- Migration 017: Add NegRisk defense columns to markets table
-- NegRisk multi-outcome markets have tokens that may be unsellable through normal CLOB orders.
-- The order gateway blocks BUY on these markets until a verified sell path is confirmed.

ALTER TABLE markets ADD COLUMN IF NOT EXISTS neg_risk BOOLEAN DEFAULT false;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS outcome_count INTEGER DEFAULT 2;

-- Index for quick filtering of neg_risk markets in scan queries
CREATE INDEX IF NOT EXISTS idx_markets_neg_risk ON markets (neg_risk) WHERE neg_risk = true;
