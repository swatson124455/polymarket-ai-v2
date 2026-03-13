-- 050: Monthly range partitioning on trade_events and position_snapshots.
-- trade_events: partitioned by event_time (append-only, natural time correlation).
-- position_snapshots: partitioned by snapshot_date (daily inserts, natural correlation).
--
-- Strategy: rename existing table → create partitioned parent → migrate data → drop old.
-- Partitions created for 2026-01 through 2026-12, plus a default catchall.
-- Future partitions should be created by a monthly cron or watchdog task.

-- ============================================================
-- 1. trade_events → partitioned by event_time
-- ============================================================

-- Rename existing heap table
ALTER TABLE IF EXISTS trade_events RENAME TO trade_events_old;

-- Drop indexes on old table (they'll be recreated on partitioned table)
DROP INDEX IF EXISTS idx_trade_events_time_brin;
DROP INDEX IF EXISTS idx_trade_events_bot_time;
DROP INDEX IF EXISTS idx_trade_events_market;
DROP INDEX IF EXISTS idx_trade_events_type;
DROP INDEX IF EXISTS idx_trade_events_correlation;
DROP INDEX IF EXISTS idx_trade_events_mode;

-- Drop the immutability trigger (will be re-applied)
DROP TRIGGER IF EXISTS trg_trade_events_immutable ON trade_events_old;

-- Create partitioned parent
CREATE TABLE trade_events (
    sequence_num        BIGSERIAL,
    event_type          TEXT NOT NULL CHECK (event_type IN (
                            'ENTRY', 'EXIT', 'RESOLUTION', 'CORRECTION',
                            'POSITION_REBUILD', 'MANUAL_ADJUSTMENT'
                        )),
    execution_mode      TEXT NOT NULL DEFAULT 'paper' CHECK (execution_mode IN (
                            'paper', 'live', 'backtest'
                        )),
    event_time          TIMESTAMP NOT NULL,
    knowledge_time      TIMESTAMP NOT NULL DEFAULT NOW(),
    recorded_at         TIMESTAMP NOT NULL DEFAULT NOW(),
    bot_name            TEXT NOT NULL,
    market_id           TEXT NOT NULL,
    token_id            TEXT,
    correlation_id      TEXT,
    order_id            TEXT,
    side                TEXT CHECK (side IN ('YES', 'NO', 'SELL')),
    size                NUMERIC(18,8),
    price               NUMERIC(18,8),
    fees                NUMERIC(18,8) DEFAULT 0,
    realized_pnl        NUMERIC(18,4),
    confidence          NUMERIC(6,4),
    predicted_probability NUMERIC(6,4),
    model_version       INTEGER,
    model_name          TEXT,
    idempotency_key     TEXT,
    event_data          JSONB DEFAULT '{}',
    PRIMARY KEY (sequence_num, event_time)
) PARTITION BY RANGE (event_time);

-- Monthly partitions for 2026
CREATE TABLE trade_events_2026_01 PARTITION OF trade_events
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE trade_events_2026_02 PARTITION OF trade_events
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE trade_events_2026_03 PARTITION OF trade_events
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE trade_events_2026_04 PARTITION OF trade_events
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE trade_events_2026_05 PARTITION OF trade_events
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE trade_events_2026_06 PARTITION OF trade_events
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE trade_events_2026_07 PARTITION OF trade_events
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE trade_events_2026_08 PARTITION OF trade_events
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE trade_events_2026_09 PARTITION OF trade_events
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE trade_events_2026_10 PARTITION OF trade_events
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE trade_events_2026_11 PARTITION OF trade_events
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE trade_events_2026_12 PARTITION OF trade_events
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE trade_events_default PARTITION OF trade_events DEFAULT;

-- Migrate data from old table
INSERT INTO trade_events SELECT * FROM trade_events_old;

-- Re-create indexes on partitioned table (auto-propagated to children)
CREATE INDEX idx_trade_events_time_brin ON trade_events USING BRIN (event_time)
    WITH (pages_per_range = 32);
CREATE INDEX idx_trade_events_bot_time ON trade_events (bot_name, event_time DESC);
CREATE INDEX idx_trade_events_market ON trade_events (market_id);
CREATE INDEX idx_trade_events_type ON trade_events (event_type);
CREATE INDEX idx_trade_events_correlation ON trade_events (correlation_id)
    WHERE correlation_id IS NOT NULL;
CREATE INDEX idx_trade_events_mode ON trade_events (execution_mode);

-- Unique constraint on idempotency_key (partition-local)
CREATE UNIQUE INDEX idx_trade_events_idempotency ON trade_events (idempotency_key)
    WHERE idempotency_key IS NOT NULL;

-- Re-apply immutability trigger (automatically propagated to partitions)
CREATE TRIGGER trg_trade_events_immutable
    BEFORE UPDATE OR DELETE ON trade_events
    FOR EACH ROW EXECUTE FUNCTION prevent_trade_event_mutation();

-- Drop old table after successful migration
DROP TABLE trade_events_old;


-- ============================================================
-- 2. position_snapshots → partitioned by snapshot_date
-- ============================================================

ALTER TABLE IF EXISTS position_snapshots RENAME TO position_snapshots_old;

DROP INDEX IF EXISTS idx_position_snapshots_date_brin;

CREATE TABLE position_snapshots (
    id                  BIGSERIAL,
    snapshot_date       DATE NOT NULL,
    bot_name            TEXT NOT NULL,
    market_id           TEXT NOT NULL,
    side                TEXT NOT NULL,
    quantity            NUMERIC(18,8) NOT NULL,
    entry_price         NUMERIC(18,8) NOT NULL,
    mark_price          NUMERIC(18,8),
    unrealized_pnl      NUMERIC(18,4),
    realized_pnl        NUMERIC(18,4) DEFAULT 0,
    last_event_seq      BIGINT,
    PRIMARY KEY (id, snapshot_date),
    UNIQUE (snapshot_date, bot_name, market_id, side)
) PARTITION BY RANGE (snapshot_date);

-- Monthly partitions for 2026
CREATE TABLE position_snapshots_2026_01 PARTITION OF position_snapshots
    FOR VALUES FROM ('2026-01-01') TO ('2026-02-01');
CREATE TABLE position_snapshots_2026_02 PARTITION OF position_snapshots
    FOR VALUES FROM ('2026-02-01') TO ('2026-03-01');
CREATE TABLE position_snapshots_2026_03 PARTITION OF position_snapshots
    FOR VALUES FROM ('2026-03-01') TO ('2026-04-01');
CREATE TABLE position_snapshots_2026_04 PARTITION OF position_snapshots
    FOR VALUES FROM ('2026-04-01') TO ('2026-05-01');
CREATE TABLE position_snapshots_2026_05 PARTITION OF position_snapshots
    FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE position_snapshots_2026_06 PARTITION OF position_snapshots
    FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE position_snapshots_2026_07 PARTITION OF position_snapshots
    FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE position_snapshots_2026_08 PARTITION OF position_snapshots
    FOR VALUES FROM ('2026-08-01') TO ('2026-09-01');
CREATE TABLE position_snapshots_2026_09 PARTITION OF position_snapshots
    FOR VALUES FROM ('2026-09-01') TO ('2026-10-01');
CREATE TABLE position_snapshots_2026_10 PARTITION OF position_snapshots
    FOR VALUES FROM ('2026-10-01') TO ('2026-11-01');
CREATE TABLE position_snapshots_2026_11 PARTITION OF position_snapshots
    FOR VALUES FROM ('2026-11-01') TO ('2026-12-01');
CREATE TABLE position_snapshots_2026_12 PARTITION OF position_snapshots
    FOR VALUES FROM ('2026-12-01') TO ('2027-01-01');
CREATE TABLE position_snapshots_default PARTITION OF position_snapshots DEFAULT;

-- Migrate data
INSERT INTO position_snapshots SELECT * FROM position_snapshots_old;

-- Re-create indexes
CREATE INDEX idx_position_snapshots_date_brin ON position_snapshots
    USING BRIN (snapshot_date);

-- Drop old table
DROP TABLE position_snapshots_old;
