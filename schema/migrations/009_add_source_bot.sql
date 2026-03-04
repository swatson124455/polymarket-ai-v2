-- Add source_bot to positions for per-bot P&L attribution.
-- bot_id remains for coordinator identity. source_bot records which bot placed the order (e.g. EnsembleBot, MirrorBot).
ALTER TABLE positions ADD COLUMN IF NOT EXISTS source_bot TEXT;
CREATE INDEX IF NOT EXISTS idx_positions_source_bot ON positions(source_bot) WHERE source_bot IS NOT NULL;
