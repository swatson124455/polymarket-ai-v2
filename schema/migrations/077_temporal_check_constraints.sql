-- Migration 077: defense-in-depth CHECK constraints against forward-dated
-- resolution-observation timestamps.
--
-- Resolution-observation columns must reflect when we OBSERVED the resolution,
-- not when the market was SCHEDULED to close. A 5-minute future-tolerance
-- accommodates VPS-vs-DB clock drift and NTP correction without admitting the
-- bug class (which produces dates months in the future).
--
-- Added NOT VALID — existing corrupt rows do not block the migration, but new
-- INSERTs/UPDATEs are checked immediately. After backfill (see backfill script
-- accompanying the next commit), the constraints may optionally be VALIDATEd
-- separately to enforce on historical rows too:
--   ALTER TABLE <t> VALIDATE CONSTRAINT <c>;
--
-- For partitioned trade_events: the constraint added to the parent propagates
-- to all current and future partitions automatically (declarative partitioning
-- ≥ PG11). No per-partition statement needed.

ALTER TABLE markets
    ADD CONSTRAINT chk_markets_resolved_at_not_future
    CHECK (resolved_at IS NULL OR resolved_at <= now() + INTERVAL '5 minutes')
    NOT VALID;

ALTER TABLE paper_trades
    ADD CONSTRAINT chk_paper_trades_resolved_at_not_future
    CHECK (resolved_at IS NULL OR resolved_at <= now() + INTERVAL '5 minutes')
    NOT VALID;

ALTER TABLE trade_events
    ADD CONSTRAINT chk_trade_events_event_time_not_future
    CHECK (event_time <= now() + INTERVAL '5 minutes')
    NOT VALID;

ALTER TABLE prediction_log
    ADD CONSTRAINT chk_prediction_log_resolved_at_not_future
    CHECK (resolved_at IS NULL OR resolved_at <= now() + INTERVAL '5 minutes')
    NOT VALID;

ALTER TABLE mirror_rejected_signals
    ADD CONSTRAINT chk_mirror_rejected_signals_resolved_at_not_future
    CHECK (resolved_at IS NULL OR resolved_at <= now() + INTERVAL '5 minutes')
    NOT VALID;

ALTER TABLE traded_markets
    ADD CONSTRAINT chk_traded_markets_resolved_at_not_future
    CHECK (resolved_at IS NULL OR resolved_at <= now() + INTERVAL '5 minutes')
    NOT VALID;
