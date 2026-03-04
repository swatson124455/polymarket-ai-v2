# Paper Trading Mode (PAPER_TRADING_MODE / SIMULATION_MODE)

## Problem

Without this fix, Python bots were in limbo:

- **No CLOB keys** → execution_engine API call fails → order rejected or exception (silently swallowed or logged).
- **CLOB keys set, kill_switch on** → order blocked at OrderGateway.
- **CLOB keys set, kill_switch off** → real money trades.

There was no intermediate "paper trade" mode that records what would have been done without submitting to the CLOB. So the system either did nothing or traded real money, with no way to validate performance in between.

## What Exists Now (after fix)

A proper **paper trading mode** when `SIMULATION_MODE=true` (default):

1. **Full pipeline** – Bots run: scan → predict → signal → size → **risk check** (OrderGateway) → then record order instead of calling CLOB.
2. **Persist orders** – Each paper order is written to the `paper_trades` table (market_id, token_id, bot_name, side, size, price, confidence, etc.).
3. **No CLOB call** – When `SIMULATION_MODE=true`, OrderGateway uses PaperTradingEngine instead of ExecutionEngine; execution_engine is never called.
4. **Hypothetical P&L** – When markets resolve, `backfill_paper_trades_resolution()` updates `paper_trades` with `resolution`, `resolved_at`, and `realized_pnl`. Same data as real trading for backtesting and tuning.
5. **Documented** – `SIMULATION_MODE` and `PAPER_TRADING_CAPITAL` are documented in `config/settings.py`. Default: `SIMULATION_MODE=true` (paper); set `SIMULATION_MODE=false` when ready for real wallet.

## Config

| Env | Default | Meaning |
|-----|--------|--------|
| `SIMULATION_MODE` | `true` | When true: orders go through risk/coordinator then PaperTradingEngine (persisted to `paper_trades`). When false: orders go to Polymarket CLOB (real money). |
| `PAPER_TRADING_CAPITAL` | `10000` | Virtual capital for paper trading (position sizing and balance). |

## Flow

- **BaseEngine.place_order**: When `SIMULATION_MODE=true` and paper_trading enabled, we still run the **full path** through OrderGateway (kill switch, risk, liquidity, coordinator). OrderGateway then calls `paper_trading.place_order()` instead of `execution_engine.place_order()` when in simulation mode.
- **PaperTradingEngine**: In-memory cash/positions plus **persistence**: each trade is written to `paper_trades` when Database is provided.
- **Resolution**: Resolution backfill (or periodic job) runs `backfill_paper_trades_resolution()` so resolved markets update `paper_trades.resolution`, `resolved_at`, and `realized_pnl`.

## Integration with autonomy plan

Paper trading is the missing link between "models generate predictions" and "we trust them enough to trade real money." It makes the self-learning/autonomy plan testable: you can validate that learned weights improve P&L using hypothetical P&L from paper_trades.
