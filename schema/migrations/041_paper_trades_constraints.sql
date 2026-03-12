-- Migration 041: paper_trades integrity constraints (idempotent)
-- Prevents duplicate entries: same bot re-entering same market on successive scans.
-- Side CHECK allows YES/NO/SELL (SELL = exit trades from paper engine).

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'uq_paper_trades_bot_market_side'
  ) THEN
    ALTER TABLE paper_trades
    ADD CONSTRAINT uq_paper_trades_bot_market_side
    UNIQUE (bot_name, market_id, side);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint WHERE conname = 'chk_paper_trades_side'
  ) THEN
    ALTER TABLE paper_trades
    ADD CONSTRAINT chk_paper_trades_side
    CHECK (side IN ('YES', 'NO', 'SELL'));
  END IF;
END $$;
