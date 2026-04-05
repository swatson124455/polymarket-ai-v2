-- Rollback S157 resolution check tracking
ALTER TABLE traded_markets DROP COLUMN IF EXISTS check_fail_count;
ALTER TABLE traded_markets DROP COLUMN IF EXISTS resolution_status;
