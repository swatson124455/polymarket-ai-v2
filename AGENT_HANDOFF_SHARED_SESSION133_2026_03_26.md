# Agent Handoff — Shared Infrastructure Session 133 (2026-03-26)

## Summary
2 bugs fixed in paper trading engine (shared by all 15 bots). 1717 tests passing.

## Bug #3: Paper trading idempotency DB path returns `success=True` (HIGH)
**Issue:** Two idempotency paths returned contradictory `success` values. In-memory check (L424) returned `success=False` (correct per S107). DB check (L440) returned `success=True`, which could trigger downstream `confirm_position()` on restart and create ghost positions.
**Root cause:** L441 was `"success": True` — leftover from before S107 fix. S107 only fixed the in-memory path.
**Fix:** Changed L441 to `"success": False` to match in-memory path and S107 contract.
**Files:** `base_engine/execution/paper_trading.py:441`
**Lines changed:** 1 modified (+2 comment lines)
**Blast radius:** ALL 15 BOTS — paper engine is shared
**Cross-bot impact:** Ghost positions (size=0) from this bug inflate position counts for all bots, potentially blocking new entries via position caps
**Verification:** 1717 tests passing
**Rollback:** `git revert <sha>`

## Bug #6: DB write failure error message enhancement (MEDIUM)
**Issue:** When post-lock DB writes fail (L372-378), in-memory state diverges from DB. On restart, cash/positions restored from DB would be wrong. The error was logged but didn't indicate the severity.
**Root cause:** Deliberate architectural choice (S94) to release lock before DB writes for latency. The `_persist_buy_entry` already retries 3x internally (L949-1018). The outer error path only fires on total failure.
**Fix:** Enhanced error message at L378 to include `msg="In-memory state diverges from DB — restart may lose this trade"` so operators know to investigate.
**Files:** `base_engine/execution/paper_trading.py:378`
**Lines changed:** 2 modified
**Blast radius:** ALL 15 BOTS
**Cross-bot impact:** With 3 active bots trading concurrently, DB write failure on one bot's trade doesn't affect other bots' trades (separate coroutines).
**Verification:** 1717 tests passing
**Rollback:** `git revert <sha>`

## Pass 1 Fix: Daily exposure tracked inside _trade_lock (MEDIUM-HIGH)
**Issue:** `order_gateway._track_position_open()` was called AFTER paper engine released `_trade_lock`. Between lock release and exposure update, another bot's `BotBankrollManager.get_bet_size()` could read stale daily exposure → daily cap overshoot by up to 1 `max_bet_usd` per scan cycle.
**Root cause:** Exposure tracking happened outside the critical section.
**Fix:** Added `on_buy_fill` callback parameter to `paper_trading.place_order()`. The callback (`order_gateway._track_position_open`) fires INSIDE `_place_order_locked()` while `_trade_lock` is held. Removed duplicate post-return `_track_position_open()` calls in `order_gateway.py`.
**Files:** `base_engine/execution/paper_trading.py:339,366,404,667-674`, `base_engine/execution/order_gateway.py:942-958,1026,1045`
**Blast radius:** ALL 15 BOTS
**Verification:** 1711 tests passing

## Pass 1 Fix: Atomic day boundary reset (LOW)
**Issue:** `get_daily_exposure_usd()` and `_track_position_open()` both had non-atomic day-boundary checks. At midnight UTC, two coroutines could double-clear the dict, losing the first bot's increment.
**Root cause:** `clear()` and date assignment were in wrong order.
**Fix:** Extracted `_maybe_reset_daily()` method that sets date BEFORE clearing — idempotent on second call.
**Files:** `base_engine/execution/order_gateway.py:88-97,134-137`
**Blast radius:** ALL 15 BOTS
**Verification:** 1711 tests passing

## Audit Findings Rejected (NOT bugs)
1. **NO-side P&L sign error** — FALSE. Paper engine uses token-specific prices, formula is correct for both YES and NO.
2. **WeatherBot exposure TOCTTOU** — FALSE. Correct double-checked locking pattern (early check is fast-rejection, real reservation under lock).
3. **Negative exposure clamp** — FALSE. Correct behavior — yesterday's exits shouldn't give extra budget today.
4. **METAR fallback returning original probs** — FALSE. Correct when all buckets outside range.
5. **Timezone-naive daily P&L query** — NOT CONFIRMED. `trade_events.event_time` is `timestamp without time zone`, so naive comparison works.
