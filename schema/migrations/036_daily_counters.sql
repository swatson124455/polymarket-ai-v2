-- Migration 036: daily_counters — write-through persistence for daily counters
--
-- Eliminates restart-amnesia for daily state that resets at UTC midnight.
-- Keyed by (bot_id, counter_date, counter_name) — resets naturally at UTC midnight
-- because CURRENT_DATE returns the new date on the next day's queries.
--
-- TWO WRITE PATTERNS — do NOT mix them for the same (bot_id, counter_name) pair:
--
--   1. ADDITIVE (increment_counter in base_engine/data/daily_counter.py):
--      Used by bots that write on every trade: counter_value += amount.
--      Correct for write-through counters where the DB is the source of truth.
--      Currently used by: EsportsBot _game_exposure (counter_name = "game_{game_name}")
--
--   2. ABSOLUTE-SET (OrderGateway._flush_daily_exposure()):
--      Used by OrderGateway which holds an authoritative in-memory total and
--      periodically flushes it whole: counter_value = current_total.
--      Correct for in-memory-first counters that are flushed on schedule (every 60s)
--      and on SIGTERM. Worst-case data loss on SIGKILL = 60 seconds of exposure.
--      Currently used by: OrderGateway _daily_exposure_usd (counter_name = "daily_exposure_usd")
--
-- Never use both patterns for the same (bot_id, counter_name) pair — they
-- produce inconsistent totals. Use distinct counter_name values per consumer.
--
-- Other usage rules:
--   Do NOT use for net counters (up+down) — use paper_trades SUM instead.
--   Do NOT use for multi-day accumulators — those need explicit expiry columns.

CREATE TABLE IF NOT EXISTS daily_counters (
    bot_id        TEXT        NOT NULL,
    counter_date  DATE        NOT NULL DEFAULT CURRENT_DATE,
    counter_name  TEXT        NOT NULL,
    counter_value NUMERIC     NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (bot_id, counter_date, counter_name)
);

CREATE INDEX IF NOT EXISTS idx_daily_counters_bot_date
    ON daily_counters (bot_id, counter_date);
