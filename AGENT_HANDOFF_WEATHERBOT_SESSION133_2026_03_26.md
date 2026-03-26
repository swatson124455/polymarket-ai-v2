# Agent Handoff — WeatherBot Session 133 (2026-03-26)

## Summary
1 bug fixed from full code audit. 1717 tests passing.

## Bug #5: Exit exposure decrement skipped when question parsing fails (MEDIUM)
**Issue:** When position_manager exited a WeatherBot position and the cache was cold, fallback DB query succeeded but `_extract_city_and_date()` failed to parse the market question string. Exposure was never decremented, but the position WAS closed. Group/city exposure stayed inflated, blocking future trades.
**Root cause:** L1071-1072 — if question parsing returns `(None, None)`, the decrement block (L1072-1080) is skipped. No secondary lookup attempted.
**Fix:** Added fallback to read city/date from `trade_events.event_data` (which stores city and date at entry time). Inserted between L1071 and the existing `if _fb_city and _fb_date:` guard.
**Files:** `bots/weather_bot.py:1070-1091`
**Lines changed:** +10 added
**Blast radius:** WeatherBot only — PM exit fallback path in `scan_and_trade()`
**Verification:** 1717 tests passing
**Rollback:** `git revert <sha>`

## Pass 1 Fix: Redis exit cooldown DB backup (LOW)
**Issue:** Exit cooldowns stored in Redis with TTL. If Redis key expired during restart window, cooldown was lost and bot could re-enter a market it just exited.
**Fix:** `_save_exit_to_redis()` now also writes `expire_at` timestamp to `daily_counters` as DB backup. `_restore_exits_from_redis()` falls back to `daily_counters` for any cooldowns missing from Redis.
**Files:** `bots/weather_bot.py:3257-3293`
**Blast radius:** WeatherBot only
**Verification:** 1711 tests passing
**Rollback:** `git revert <sha>`
