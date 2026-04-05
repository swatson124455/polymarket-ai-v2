-- S157: Track resolution check failures for exponential backoff
-- Replaces always-stamp _touch_checked() bandaid
ALTER TABLE traded_markets ADD COLUMN IF NOT EXISTS check_fail_count INT DEFAULT 0;
ALTER TABLE traded_markets ADD COLUMN IF NOT EXISTS resolution_status TEXT DEFAULT 'pending';
-- resolution_status values: 'pending' (default), 'resolved', 'dead_letter' (permanent 404)
