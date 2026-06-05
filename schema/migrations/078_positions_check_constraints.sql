-- Migration 078: CHECK constraints for positions table (WI-8)
--
-- Adds four CHECK constraints to catch invalid inserts at the DB layer:
--
-- 1. status_valid          — status must be a known lifecycle value
-- 2. is_paper_not_null     — is_paper must always be set (no ambiguous live/paper)
-- 3. entry_price_range     — for open positions, entry_price must be in (0, 1]
-- 4. size_positive         — for open positions, size must be > 0
--
-- Constraints 3 and 4 are conditional on status='open' because:
--   - closed positions may have size=0 (zeroed when closed; 5,537 rows as of S235)
--   - reserving positions start with size=0 and entry_price=0 before fill
--   Pre-validation (S235): 0 open rows violate either constraint.
--
-- The WI-8 balance-probe trigger (blocks INSERT is_paper=false when wallet
-- balance < entry_cost) requires a wallet_balance_log DB table that does not
-- yet exist. That component is deferred to WI-11 (audit-on-write hooks).
--
-- Deployed by: standard run_migrations.py (applied idempotently if run twice
-- because pg_constraint prevents duplicate names and ALTER TABLE ADD CONSTRAINT
-- errors out — scripts/run_migrations.py wraps in DO EXCEPTION WHEN SQLSTATE
-- 42710 pattern; see migration 077 for precedent).

-- 1. Valid status values (includes 'reserving' used during order placement)
DO $$
BEGIN
    ALTER TABLE positions
        ADD CONSTRAINT chk_positions_status_valid
        CHECK (status IN ('open', 'closed', 'resolved', 'reserving'));
EXCEPTION
    WHEN duplicate_object THEN
        RAISE NOTICE 'constraint chk_positions_status_valid already exists, skipping';
END;
$$;

-- 2. is_paper must never be NULL (paper/live ambiguity would corrupt P&L reporting)
DO $$
BEGIN
    ALTER TABLE positions
        ADD CONSTRAINT chk_positions_is_paper_not_null
        CHECK (is_paper IS NOT NULL);
EXCEPTION
    WHEN duplicate_object THEN
        RAISE NOTICE 'constraint chk_positions_is_paper_not_null already exists, skipping';
END;
$$;

-- 3. For open positions: entry_price must be in (0, 1]
--    Closed/reserving positions may have entry_price=0 (historical or pre-fill)
DO $$
BEGIN
    ALTER TABLE positions
        ADD CONSTRAINT chk_positions_entry_price_range
        CHECK (status != 'open' OR (entry_price > 0 AND entry_price <= 1));
EXCEPTION
    WHEN duplicate_object THEN
        RAISE NOTICE 'constraint chk_positions_entry_price_range already exists, skipping';
END;
$$;

-- 4. For open positions: size must be > 0
--    Closed/reserving positions may have size=0 (zeroed on close, or pre-fill)
DO $$
BEGIN
    ALTER TABLE positions
        ADD CONSTRAINT chk_positions_size_positive
        CHECK (status != 'open' OR size > 0);
EXCEPTION
    WHEN duplicate_object THEN
        RAISE NOTICE 'constraint chk_positions_size_positive already exists, skipping';
END;
$$;
