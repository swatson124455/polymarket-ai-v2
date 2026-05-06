-- Migration 076: shadow_fills — intended-size + rejection telemetry columns
-- Supports P0.2 (pre-cap intended_size from BotBankrollManager),
-- P0.3 (twin book-walk at intended size with isolation),
-- P0.5 (rejection coverage), and P0.6 (counterfactual P&L script).
--
-- Counterfactual formula: intended_size × fill_frac_at_intended × vwap_at_intended
-- NULL-safe: P0.3 exception → vwap_at_intended/slippage_at_intended/fill_frac_at_intended=NULL
--            + intended_walk_error populated.
-- Staged deploy: all 7 columns nullable — old writers unaffected.

ALTER TABLE shadow_fills
    ADD COLUMN IF NOT EXISTS intended_size_shares   NUMERIC(18,8),
    ADD COLUMN IF NOT EXISTS intended_size_usd      NUMERIC(18,4),
    ADD COLUMN IF NOT EXISTS vwap_at_intended        NUMERIC(18,8),
    ADD COLUMN IF NOT EXISTS slippage_at_intended    NUMERIC(18,8),
    ADD COLUMN IF NOT EXISTS fill_frac_at_intended   NUMERIC(6,4),
    ADD COLUMN IF NOT EXISTS intended_walk_error     TEXT,
    ADD COLUMN IF NOT EXISTS rejection_type          TEXT;
