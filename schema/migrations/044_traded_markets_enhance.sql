-- 044: Enhance existing traded_markets table (created in 042).
-- Adds columns for richer resolution discovery: share tracking, status enum,
-- execution_mode, question text, P&L.

ALTER TABLE traded_markets ADD COLUMN IF NOT EXISTS question TEXT;
ALTER TABLE traded_markets ADD COLUMN IF NOT EXISTS last_trade_at TIMESTAMP;
ALTER TABLE traded_markets ADD COLUMN IF NOT EXISTS net_yes_shares NUMERIC(18,8) DEFAULT 0;
ALTER TABLE traded_markets ADD COLUMN IF NOT EXISTS net_no_shares NUMERIC(18,8) DEFAULT 0;
ALTER TABLE traded_markets ADD COLUMN IF NOT EXISTS total_invested NUMERIC(18,4) DEFAULT 0;
ALTER TABLE traded_markets ADD COLUMN IF NOT EXISTS trade_count INTEGER DEFAULT 0;
ALTER TABLE traded_markets ADD COLUMN IF NOT EXISTS execution_mode TEXT DEFAULT 'paper';
ALTER TABLE traded_markets ADD COLUMN IF NOT EXISTS resolution_pnl NUMERIC(18,4);
ALTER TABLE traded_markets ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'open';

-- Backfill status from existing resolved boolean
UPDATE traded_markets SET status = 'resolved' WHERE resolved = TRUE AND status = 'open';

CREATE INDEX IF NOT EXISTS idx_traded_markets_status ON traded_markets (status);
CREATE INDEX IF NOT EXISTS idx_traded_markets_mode_status ON traded_markets (execution_mode, status);
