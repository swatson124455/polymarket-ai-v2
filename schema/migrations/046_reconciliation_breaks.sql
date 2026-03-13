-- 046: reconciliation_breaks — Automated integrity checks.
-- Detects position/PnL mismatches across positions, paper_trades, traded_markets.
-- Run every 6h by watchdog loop.

CREATE TABLE IF NOT EXISTS reconciliation_breaks (
    break_id            BIGSERIAL PRIMARY KEY,
    recon_date          DATE NOT NULL,
    recon_type          TEXT NOT NULL CHECK (recon_type IN (
                            'POSITION', 'PNL', 'EXPOSURE', 'TRADE_COUNT',
                            'RESOLUTION_MISMATCH', 'STALE_POSITION'
                        )),
    bot_name            TEXT NOT NULL,
    market_id           TEXT,
    internal_value      NUMERIC(18,8),
    external_value      NUMERIC(18,8),
    difference          NUMERIC(18,8),
    severity            TEXT DEFAULT 'WARNING' CHECK (severity IN (
                            'INFO', 'WARNING', 'CRITICAL'
                        )),
    status              TEXT DEFAULT 'OPEN' CHECK (status IN (
                            'OPEN', 'ACKNOWLEDGED', 'RESOLVED', 'FALSE_POSITIVE'
                        )),
    details             JSONB DEFAULT '{}',
    detected_at         TIMESTAMP DEFAULT NOW(),
    resolved_at         TIMESTAMP,
    resolution_note     TEXT
);

CREATE INDEX idx_recon_breaks_open ON reconciliation_breaks (status, severity)
    WHERE status = 'OPEN';
CREATE INDEX idx_recon_breaks_bot ON reconciliation_breaks (bot_name, detected_at DESC);
