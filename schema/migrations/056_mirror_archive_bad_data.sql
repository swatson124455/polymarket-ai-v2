-- Migration 056: Archive MirrorBot bad data (S110)
-- Moves known-false trade_events to archive table, closes pre-gate positions.
-- Fully reversible: INSERT back from archive if needed.

BEGIN;

-- ============================================================
-- STEP 1: Create archive table (same schema, no triggers)
-- ============================================================
CREATE TABLE IF NOT EXISTS trade_events_archive (
    sequence_num          BIGINT,
    event_type            TEXT NOT NULL,
    execution_mode        TEXT NOT NULL DEFAULT 'paper',
    event_time            TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    knowledge_time        TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    recorded_at           TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    bot_name              TEXT NOT NULL,
    market_id             TEXT NOT NULL,
    token_id              TEXT,
    correlation_id        TEXT,
    order_id              TEXT,
    side                  TEXT,
    size                  NUMERIC,
    price                 NUMERIC,
    fees                  NUMERIC DEFAULT 0,
    realized_pnl          NUMERIC,
    confidence            NUMERIC,
    predicted_probability NUMERIC,
    model_version         INTEGER,
    model_name            TEXT,
    idempotency_key       TEXT,
    event_data            JSONB DEFAULT '{}'::jsonb,
    archive_reason        TEXT NOT NULL,
    archived_at           TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_archive_bot_market
    ON trade_events_archive (bot_name, market_id, side);
CREATE INDEX IF NOT EXISTS idx_archive_reason
    ON trade_events_archive (archive_reason);

-- ============================================================
-- STEP 2: Build target sets
-- ============================================================

-- 2A: Below-gate market+side combos (avg confidence < 0.45)
CREATE TEMP TABLE _below_gate AS
SELECT market_id, side
FROM trade_events
WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'
GROUP BY market_id, side
HAVING AVG(confidence) < 0.45;

-- 2B: Below-gate token_ids (for matching EXIT events which use SELL not YES/NO)
CREATE TEMP TABLE _below_gate_tokens AS
SELECT DISTINCT token_id
FROM trade_events e
JOIN _below_gate bg ON bg.market_id = e.market_id AND bg.side = e.side
WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY' AND e.token_id IS NOT NULL;

-- 2C: Duplicate ENTRY rows (keep oldest per market+side)
CREATE TEMP TABLE _dup_entries AS
WITH ranked AS (
    SELECT sequence_num,
           ROW_NUMBER() OVER (PARTITION BY market_id, side ORDER BY event_time, sequence_num) AS rn
    FROM trade_events
    WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'
)
SELECT sequence_num FROM ranked WHERE rn > 1;

-- 2D: Orphan RESOLUTION events (no matching ENTRY)
CREATE TEMP TABLE _orphan_res AS
SELECT r.sequence_num
FROM trade_events r
LEFT JOIN (
    SELECT DISTINCT market_id, side
    FROM trade_events WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'
) e ON e.market_id = r.market_id AND e.side = r.side
WHERE r.bot_name = 'MirrorBot' AND r.event_type = 'RESOLUTION' AND e.market_id IS NULL;

-- 2E: NULL-pnl RESOLUTION events
CREATE TEMP TABLE _null_res AS
SELECT sequence_num
FROM trade_events
WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION' AND realized_pnl IS NULL;

-- 2F: Master list of all sequence_nums to archive
CREATE TEMP TABLE _archive_targets AS
-- Below-gate ENTRY/RESOLUTION (joined on market_id+side)
SELECT te.sequence_num, 'below_gate_045' AS reason
FROM trade_events te
JOIN _below_gate bg ON bg.market_id = te.market_id AND bg.side = te.side
WHERE te.bot_name = 'MirrorBot'
UNION
-- Below-gate EXIT (joined on token_id)
SELECT te.sequence_num, 'below_gate_045_exit'
FROM trade_events te
JOIN _below_gate_tokens bt ON bt.token_id = te.token_id
WHERE te.bot_name = 'MirrorBot' AND te.event_type = 'EXIT'
UNION
-- Duplicate entries (excluding ones already caught by below_gate)
SELECT de.sequence_num, 'duplicate_entry'
FROM _dup_entries de
WHERE de.sequence_num NOT IN (
    SELECT te.sequence_num FROM trade_events te
    JOIN _below_gate bg ON bg.market_id = te.market_id AND bg.side = te.side
    WHERE te.bot_name = 'MirrorBot'
)
UNION
-- Orphan resolutions
SELECT sequence_num, 'orphan_resolution' FROM _orphan_res
UNION
-- NULL-pnl resolutions
SELECT sequence_num, 'null_pnl_resolution' FROM _null_res;

-- ============================================================
-- PRE-FLIGHT: Verify counts
-- ============================================================
-- SELECT reason, COUNT(*) FROM _archive_targets GROUP BY 1 ORDER BY 1;

-- ============================================================
-- STEP 3: Disable immutability triggers
-- ============================================================
ALTER TABLE trade_events_2026_01 DISABLE TRIGGER trg_trade_events_immutable;
ALTER TABLE trade_events_2026_02 DISABLE TRIGGER trg_trade_events_immutable;
ALTER TABLE trade_events_2026_03 DISABLE TRIGGER trg_trade_events_immutable;
ALTER TABLE trade_events_default DISABLE TRIGGER trg_trade_events_immutable;

-- ============================================================
-- STEP 4: Archive (copy to archive table)
-- ============================================================
INSERT INTO trade_events_archive
    (sequence_num, event_type, execution_mode, event_time, knowledge_time,
     recorded_at, bot_name, market_id, token_id, correlation_id, order_id,
     side, size, price, fees, realized_pnl, confidence, predicted_probability,
     model_version, model_name, idempotency_key, event_data, archive_reason)
SELECT
    te.sequence_num, te.event_type, te.execution_mode, te.event_time, te.knowledge_time,
    te.recorded_at, te.bot_name, te.market_id, te.token_id, te.correlation_id, te.order_id,
    te.side, te.size, te.price, te.fees, te.realized_pnl, te.confidence, te.predicted_probability,
    te.model_version, te.model_name, te.idempotency_key, te.event_data,
    at.reason
FROM trade_events te
JOIN _archive_targets at ON at.sequence_num = te.sequence_num;

-- ============================================================
-- STEP 5: Delete archived rows from trade_events
-- ============================================================
DELETE FROM trade_events
WHERE sequence_num IN (SELECT sequence_num FROM _archive_targets);

-- ============================================================
-- STEP 6: Re-enable immutability triggers
-- ============================================================
ALTER TABLE trade_events_2026_01 ENABLE TRIGGER trg_trade_events_immutable;
ALTER TABLE trade_events_2026_02 ENABLE TRIGGER trg_trade_events_immutable;
ALTER TABLE trade_events_2026_03 ENABLE TRIGGER trg_trade_events_immutable;
ALTER TABLE trade_events_default ENABLE TRIGGER trg_trade_events_immutable;

-- ============================================================
-- STEP 7: Close below-gate open positions
-- ============================================================
UPDATE positions
SET status = 'closed', unrealized_pnl = 0
WHERE source_bot = 'MirrorBot'
  AND status = 'open'
  AND (market_id, side) IN (SELECT market_id, side FROM _below_gate);

-- ============================================================
-- STEP 8: Cleanup temp tables
-- ============================================================
DROP TABLE _archive_targets;
DROP TABLE _below_gate_tokens;
DROP TABLE _below_gate;
DROP TABLE _dup_entries;
DROP TABLE _orphan_res;
DROP TABLE _null_res;

COMMIT;
