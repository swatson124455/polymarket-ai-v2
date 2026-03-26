# Agent Handoff — MirrorBot Session 133 (2026-03-26)

## Summary
2 bugs fixed from full code audit. Dead code removed. 1717 tests passing.

## Bug #2: Exit decrement uses exit price, not entry price (MEDIUM)
**Issue:** `_daily_exposure` and `_category_exposure` decremented by `exit_size * exit_price` on manual exit, but incremented by `size * entry_price` on entry. Mismatch caused exposure drift.
**Root cause:** L943 used `exit_price` instead of `pos.get("entry_price")`. Resolution reap path (L988) was already correct.
**Fix:** Changed L943 to `exit_size * pos.get("entry_price", exit_price)`.
**Files:** `bots/mirror_bot.py:943`
**Lines changed:** 1 modified
**Blast radius:** MirrorBot only — `_check_and_execute_exits()` exit path
**Verification:** Existing test `TestDailyExposureDecrement::test_exposure_decremented_on_successful_exit` updated and passing (expects entry_price decrement)
**Rollback:** `git revert <sha>`

## Bug #7: New trades not added to `_open_positions` (HIGH)
**Issue:** Newly opened positions had NO exit monitoring (stop-loss, trader-exit, max-hold) until the next restart loaded them from DB. `_track_open_position()` was defined but never called. `_execute_mirror_trade()` L1694 only updated existing entries.
**Root cause:** Dead code — `_track_open_position()` never wired into the call chain. L1694 `if pos_key in self._open_positions` skipped new trades.
**Fix:**
1. Added `else` branch at L1696 to create new position entry with all fields (side, size, entry_price, traders, timestamp, category)
2. Deleted dead `_track_open_position()` method (was lines 1005-1020)
3. Updated stale comment at L782
4. Removed 3 tests that called the deleted method, replaced with minimal sanity test
**Files:** `bots/mirror_bot.py:782,1005-1020,1694-1703`, `tests/unit/test_mirror_bot_logic.py:626-689`
**Lines changed:** +8 added, -19 removed
**Blast radius:** MirrorBot only — position tracking dict
**Verification:** 1717 tests passing
**Rollback:** `git revert <sha>`
