-- 045: position_snapshots + equity_snapshots — Daily state capture.
-- Position snapshots: per-position state for time-travel analysis.
-- Equity snapshots: portfolio-level with peak/drawdown/Sharpe for ML features.

CREATE TABLE IF NOT EXISTS position_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
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
    UNIQUE (snapshot_date, bot_name, market_id, side)
);

CREATE TABLE IF NOT EXISTS equity_snapshots (
    id                  BIGSERIAL PRIMARY KEY,
    snapshot_date       DATE NOT NULL,
    bot_name            TEXT NOT NULL,
    total_capital       NUMERIC(18,4) NOT NULL,
    deployed_capital    NUMERIC(18,4) NOT NULL,
    realized_pnl        NUMERIC(18,4) NOT NULL,
    unrealized_pnl      NUMERIC(18,4) NOT NULL,
    total_equity        NUMERIC(18,4) NOT NULL,
    open_positions      INTEGER NOT NULL DEFAULT 0,
    daily_trades        INTEGER NOT NULL DEFAULT 0,
    win_count           INTEGER NOT NULL DEFAULT 0,
    loss_count          INTEGER NOT NULL DEFAULT 0,
    peak_equity         NUMERIC(18,4),
    drawdown_pct        NUMERIC(8,6),
    rolling_sharpe      NUMERIC(8,4),
    execution_mode      TEXT NOT NULL DEFAULT 'paper',
    UNIQUE (snapshot_date, bot_name)
);

CREATE INDEX idx_position_snapshots_date_brin ON position_snapshots USING BRIN (snapshot_date);
CREATE INDEX idx_equity_snapshots_date_brin ON equity_snapshots USING BRIN (snapshot_date);
CREATE INDEX idx_equity_snapshots_bot_date ON equity_snapshots (bot_name, snapshot_date DESC);
