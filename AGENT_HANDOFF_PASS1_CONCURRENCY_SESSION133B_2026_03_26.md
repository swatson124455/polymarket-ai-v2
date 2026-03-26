# Agent Handoff — Pass 1 Concurrency Audit Fixes (Session 133B, 2026-03-26)

## Summary
3 multi-bot concurrency race conditions fixed. 1711 tests passing.

## Fix 1: Daily exposure tracked INSIDE _trade_lock (MEDIUM-HIGH)
**Issue:** `order_gateway._track_position_open()` was called AFTER paper engine released `_trade_lock`. Between lock release and exposure update, another bot's `BotBankrollManager.get_bet_size()` could read stale daily exposure. Could overshoot daily cap by up to 1 `max_bet_usd` per scan cycle.
**Root cause:** Exposure tracking was outside the critical section.
**Fix:** Added `on_buy_fill` callback parameter to `paper_trading.place_order()` and `_place_order_locked()`. Callback fires INSIDE the lock after BUY position creation. Removed duplicate post-return `_track_position_open()` calls.
**Files:** `base_engine/execution/paper_trading.py`, `base_engine/execution/order_gateway.py`
**Blast radius:** ALL 15 BOTS — shared paper engine + order gateway
**Rollback:** `git revert <sha>`

## Fix 2: Atomic day boundary reset (LOW)
**Issue:** `get_daily_exposure_usd()` and `_track_position_open()` both had non-atomic day-boundary checks. At midnight UTC, two coroutines could double-clear the dict, losing the first bot's daily exposure increment.
**Root cause:** `clear()` happened before `date = today` — second coroutine read stale date and cleared again.
**Fix:** Extracted `_maybe_reset_daily()` — sets date BEFORE clearing, making it idempotent on second call.
**Files:** `base_engine/execution/order_gateway.py`
**Blast radius:** ALL 15 BOTS
**Rollback:** `git revert <sha>`

## Fix 3: Redis exit cooldown DB backup (LOW)
**Issue:** Exit cooldowns in Redis with TTL. If key expired during restart, cooldown lost → bot re-enters a market it just exited.
**Fix:** `_save_exit_to_redis()` / `_save_exit_cooldown_to_redis()` now also write `expire_at` to `daily_counters`. Restore functions fall back to DB when Redis key is missing.
**Files:** `bots/weather_bot.py`, `bots/esports_bot.py`
**Blast radius:** WeatherBot + EsportsBot only
**Rollback:** `git revert <sha>`

## Cross-bot market overlap — DISMISSED
User confirmed bots trade different sectors. No overlap risk.

## What's NOT a bug (confirmed safe)
- **Cash overdraft from 3 bots** — `_trade_lock` serializes ALL cash mutations
- **`seed_positions_from_db()` unprotected** — runs once during startup before bots start
- **`daily_counters` PostgreSQL race** — `INSERT ... ON CONFLICT` is atomic
- **Per-bot BotBankrollManager locks** — independent locks for independent state
