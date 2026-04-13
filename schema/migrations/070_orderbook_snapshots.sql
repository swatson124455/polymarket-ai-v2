-- 070_orderbook_snapshots.sql
-- 1J: Orderbook collection table for slippage analysis and liquidity gating
-- Stores best_bid/best_ask snapshots polled every 60s for active markets

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
    id BIGSERIAL PRIMARY KEY,
    token_id TEXT NOT NULL,
    market_id TEXT NOT NULL,
    best_bid NUMERIC(10,6),
    best_ask NUMERIC(10,6),
    spread NUMERIC(10,6),
    mid_price NUMERIC(10,6),
    bid_depth_1pct NUMERIC(18,4),  -- liquidity within 1% of mid
    ask_depth_1pct NUMERIC(18,4),
    bid_depth_5pct NUMERIC(18,4),  -- liquidity within 5% of mid
    ask_depth_5pct NUMERIC(18,4),
    imbalance NUMERIC(6,4),         -- -1 to +1
    snapshot_time TIMESTAMP NOT NULL DEFAULT NOW()
);

-- BRIN index on time (append-only table, excellent compression)
CREATE INDEX idx_ob_snap_time ON orderbook_snapshots USING BRIN (snapshot_time);

-- Composite for per-token lookups in time range
CREATE INDEX idx_ob_snap_token_time ON orderbook_snapshots (token_id, snapshot_time DESC);

-- Composite for per-market lookups
CREATE INDEX idx_ob_snap_market_time ON orderbook_snapshots (market_id, snapshot_time DESC);

-- Retention: prune_old_data.py handles cleanup (30-day default)
