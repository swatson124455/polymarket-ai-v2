-- Migration 028: Bot heartbeats for silent bot detection (Session 51)
-- Each bot upserts its row after every scan cycle. Watchdog queries for stale entries.

CREATE TABLE IF NOT EXISTS bot_heartbeats (
    bot_name VARCHAR(64) PRIMARY KEY,
    last_scan_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    scan_duration_ms DOUBLE PRECISION,
    markets_scanned INT DEFAULT 0,
    opportunities_found INT DEFAULT 0,
    trades_executed INT DEFAULT 0,
    consecutive_errors INT DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
