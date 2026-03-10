-- Migration 035: positions.trader_addresses
-- Adds a TEXT[] column to store the elite trader addresses mirrored into
-- each open position. Used by MirrorBot._restore_state_on_startup() to
-- rebuild the in-memory `traders` set after a restart, so exit-mirroring
-- remains active for positions opened before the restart.

ALTER TABLE positions
    ADD COLUMN IF NOT EXISTS trader_addresses TEXT[] DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_positions_trader_addresses
    ON positions USING GIN(trader_addresses);
