-- Migration 057: Deduplicate esports_prediction_log
-- Root cause: bare INSERT with no unique constraint logs ~97 rows per market
-- (every 10 min from in-memory dedup, resets hourly + on restart).
-- Fix: keep ONE row per (market_id, bot_name), upsert on conflict.

-- Step 1: Delete duplicates, keeping the most recent row per (market_id, bot_name)
DELETE FROM esports_prediction_log
WHERE id NOT IN (
    SELECT DISTINCT ON (market_id, bot_name) id
    FROM esports_prediction_log
    ORDER BY market_id, bot_name, created_at DESC
);

-- Step 2: Add unique constraint
CREATE UNIQUE INDEX IF NOT EXISTS idx_esports_pred_log_market_bot
    ON esports_prediction_log (market_id, bot_name);
