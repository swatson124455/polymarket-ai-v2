# Core Concepts

## BOT_ID vs bot_name

- **BOT_ID** (env/config): Process/instance identity for coordination. One per process. Used by TradeCoordinator and KillSwitch. Default `"default"`.
- **bot_name** (argument to `place_order`): Strategy or bot label for logging and analytics (e.g. `"ArbitrageBot"`). Can differ per strategy in the same process.

Positions and coordination are keyed by **BOT_ID**. Strategy-level metrics can use **bot_name**; process-level coordination and safety use **BOT_ID**.

## Coordination and database (multi-bot / production)

For multi-bot or production, **DATABASE_URL must be set** and the DB must be up so KillSwitch and TradeCoordinator are created. If DB init fails, coordination is skipped and orders can still be placed. Operators enforce this via deployment and monitoring.

## Cycle

A **cycle** is one iteration of a bot's main loop (e.g. one `scan_and_trade()` in `BaseBot._scan_loop`), or one `place_order` call. The kill switch is checked immediately before execute (inside `BaseEngine.place_order`). Optionally, bots can check at cycle start in the scan loop so a full scan is skipped when the kill switch is engaged.

## Graceful shutdown

Graceful shutdown runs when `BaseEngine.stop()` is invoked. The process runner (e.g. dashboard, main script, or supervisor) must call `engine.stop()` on SIGTERM/SIGINT if signal-aware shutdown is required.
