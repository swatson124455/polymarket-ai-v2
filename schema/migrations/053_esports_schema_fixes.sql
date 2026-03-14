-- 053: Add missing columns to esports_prediction_log
-- closing_price: referenced by 4 functions (CLV tracking, edge decay, Pinnacle backfill)
-- tournament_phase: formalize runtime ALTER TABLE hack from esports_db.py

ALTER TABLE esports_prediction_log ADD COLUMN IF NOT EXISTS closing_price DOUBLE PRECISION;
ALTER TABLE esports_prediction_log ADD COLUMN IF NOT EXISTS tournament_phase VARCHAR(50) DEFAULT '';
