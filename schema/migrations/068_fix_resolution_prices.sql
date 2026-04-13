-- S172 Phase 1D: Fix WeatherBot RESOLUTION events that have entry_price
-- instead of payout price (0.0/1.0).
--
-- Root cause: Pre-S141 resolution backfill used entry_price as the price field.
-- S141 (2026-03-29) fixed the forward path but 2140 historical records remain.
-- The correct price for RESOLUTION events is: 1.0 if won, 0.0 if lost.
-- We determine outcome from realized_pnl (positive = won, zero/negative = lost).
--
-- Also fixes all bots, not just WeatherBot (same bug existed for MB/EB).

-- Must run as superuser (migration runner connects directly to PG).
-- Disable immutability trigger on affected partitions.

DO $$
DECLARE
    _part TEXT;
    _updated INT := 0;
    _total INT := 0;
BEGIN
    -- Disable triggers on all partitions + parent
    FOR _part IN
        SELECT inhrelid::regclass::text
        FROM pg_inherits
        WHERE inhparent = 'trade_events'::regclass
    LOOP
        EXECUTE format('ALTER TABLE %I DISABLE TRIGGER ALL', _part);
    END LOOP;
    ALTER TABLE trade_events DISABLE TRIGGER trg_trade_events_immutable;

    -- Fix: price = 1.0 if won (realized_pnl > 0), 0.0 if lost
    UPDATE trade_events
    SET price = CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END
    WHERE event_type = 'RESOLUTION'
      AND price NOT IN (0.0, 1.0);
    GET DIAGNOSTICS _updated = ROW_COUNT;

    -- Re-enable triggers
    FOR _part IN
        SELECT inhrelid::regclass::text
        FROM pg_inherits
        WHERE inhparent = 'trade_events'::regclass
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE TRIGGER ALL', _part);
    END LOOP;
    ALTER TABLE trade_events ENABLE TRIGGER trg_trade_events_immutable;

    RAISE NOTICE '1D: Fixed % RESOLUTION events with bad prices', _updated;
END$$;
