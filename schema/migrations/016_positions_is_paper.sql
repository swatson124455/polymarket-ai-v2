-- Migration 016: Add is_paper flag to positions table
-- Paper positions (SIMULATION_MODE=true) are excluded from real metrics (CLV, win rate, Total P&L).
-- Existing rows get is_paper=FALSE (real positions).

ALTER TABLE positions ADD COLUMN IF NOT EXISTS is_paper BOOLEAN DEFAULT FALSE;

-- Partial index: only index rows where is_paper=TRUE (most rows are real)
CREATE INDEX IF NOT EXISTS idx_positions_is_paper ON positions(is_paper) WHERE is_paper = TRUE;

-- Backfill: mark any position whose bot_id appears in paper_trades as paper
-- (best-effort heuristic for pre-existing data - future inserts set is_paper correctly)
UPDATE positions p
SET is_paper = TRUE
WHERE EXISTS (
    SELECT 1 FROM paper_trades pt
    WHERE pt.market_id = p.market_id
      AND pt.bot_name = p.bot_id
      AND pt.side = p.side
)
AND p.is_paper = FALSE;
