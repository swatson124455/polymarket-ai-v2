-- S100: Whale trade log — persists every trade from the top-500 elite watchlist.
-- Used for: book walk calibration, whale P&L tracking, copy-trading signal analysis.
-- ~270K rows/day at current watchlist activity. 30-day retention recommended.

CREATE TABLE IF NOT EXISTS whale_trades (
    id              BIGSERIAL PRIMARY KEY,
    event_time      TIMESTAMP NOT NULL DEFAULT NOW(),
    trader_address  TEXT NOT NULL,
    market_id       TEXT NOT NULL,          -- condition_id (0x hash)
    token_id        TEXT NOT NULL,
    side            TEXT NOT NULL,           -- BUY / SELL
    outcome         TEXT,                    -- Yes / No / team name
    price           NUMERIC(10,6) NOT NULL,
    size            NUMERIC(16,4) NOT NULL,  -- shares
    size_usd        NUMERIC(16,4),           -- price * size
    tx_hash         TEXT,                    -- transaction hash if available
    copied          BOOLEAN DEFAULT FALSE,   -- did we attempt to copy this trade?
    slug            TEXT                     -- market slug for human readability
);

-- Indexes for common queries
CREATE INDEX IF NOT EXISTS idx_whale_trades_event_time ON whale_trades (event_time);
CREATE INDEX IF NOT EXISTS idx_whale_trades_trader ON whale_trades (trader_address, event_time);
CREATE INDEX IF NOT EXISTS idx_whale_trades_market ON whale_trades (market_id, event_time);
