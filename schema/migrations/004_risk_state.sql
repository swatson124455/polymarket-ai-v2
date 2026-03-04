-- Risk State: Persistent PnL tracking for loss limits and kill switch triggers
-- Run after bot_improvements_schema.sql if using that migration

CREATE TABLE IF NOT EXISTS risk_state (
    id INT PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- Singleton row
    daily_pnl FLOAT DEFAULT 0,
    weekly_pnl FLOAT DEFAULT 0,
    peak_balance FLOAT,
    current_balance FLOAT,
    daily_reset_at TIMESTAMP,
    weekly_reset_at TIMESTAMP,
    kill_switch_reason TEXT,
    kill_switch_triggered_at TIMESTAMP,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Insert singleton if not exists
INSERT INTO risk_state (id, daily_pnl, weekly_pnl, peak_balance, current_balance)
SELECT 1, 0, 0, NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM risk_state WHERE id = 1);
