-- Elite market-maker flag: users who trade both sides on >60% of markets
-- Used to exclude their trades from directional signal (they're market-making, not directional)

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_likely_market_maker BOOLEAN DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS idx_users_market_maker ON users(is_likely_market_maker) WHERE is_likely_market_maker = TRUE;
