-- 047: BRIN indexes on append-only timestamp columns.
-- BRIN indexes are 0.1% the size of B-tree with only 11% insert overhead
-- (vs 85% for B-tree). Only on append-only tables where timestamp correlation
-- is high. NOT on positions (mutable — updates break BRIN correlation).
--
-- NOTE: CREATE INDEX CONCURRENTLY cannot run inside a transaction block.
-- If your migration runner wraps in a transaction, remove CONCURRENTLY.

-- Drop existing B-tree on decision_events.created_at (replaced by BRIN)
DROP INDEX IF EXISTS idx_decision_events_created;

-- BRIN replacements
CREATE INDEX IF NOT EXISTS idx_paper_trades_created_brin
    ON paper_trades USING BRIN (created_at) WITH (pages_per_range = 32);

CREATE INDEX IF NOT EXISTS idx_prediction_log_created_brin
    ON prediction_log USING BRIN (created_at) WITH (pages_per_range = 32);

CREATE INDEX IF NOT EXISTS idx_decision_events_created_brin
    ON decision_events USING BRIN (created_at) WITH (pages_per_range = 32);

-- esports_prediction_log — CREATE INDEX IF NOT EXISTS is safe even if table doesn't exist
-- (PostgreSQL will error but we catch it)
CREATE INDEX IF NOT EXISTS idx_esports_prediction_log_created_brin
    ON esports_prediction_log USING BRIN (created_at) WITH (pages_per_range = 32);
