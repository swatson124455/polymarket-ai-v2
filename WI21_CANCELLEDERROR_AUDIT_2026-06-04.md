# WI-21 — `asyncio.wait_for` + asyncpg CancelledError audit (first pass, 2026-06-04)

**Source:** option-a-verify workflow, cancellederror-audit agent (read-only). Scope: repo `*.py` excluding `bots/weather/**` (vendored dup), `tests/**`, `review_package/**`. **No files edited.** Each fix is its own change-sequence + sign-off — this is the inventory, not the work.

## The defect class
`asyncio.wait_for(coro, T)` where `coro` enters `get_session()`/`get_raw_session()`: on timeout, the injected `asyncio.CancelledError` (a **BaseException**) lands mid-asyncpg, leaving the pooled connection in a bad protocol state (`"cannot switch to state N; another operation (N) is in progress"` / `InFailedSQLTransactionError`) on its *next* use. Any `except Exception` fallback is bypassed (CancelledError is not an `Exception`). Canonical statement: the S162 comment at `position_manager.py:356-362`. Established correct pattern (S162/S166/S177): replace client-side `wait_for` with server-side `SET LOCAL statement_timeout` (or `asyncio.timeout` only for non-DB work) so cancellation arrives as asyncpg `QueryCanceledError` through the normal path.

## Secondary gap — `handle_error` doesn't catch this corruption
`database.py:1240-1256` `_on_handle_error` matches only `"connection was closed"` / `ConnectionDoesNotExistError` / `InterfaceError`. It does **NOT** match `"cannot switch to state"` / `"another operation"` / `InFailedSQLTransactionError`, so it never proactively invalidates a cancellation-poisoned connection. The only catch is the reactive S235 `_CORRUPT_SIGS` guard in `_SemaphoreSession.__aenter__` (`database.py:227-251`) — one cycle late, on the next checkout. **Fix direction:** add those signatures to the `handle_error` match set so poisoned connections are evicted proactively. (Directly relevant to the esports engine-leak / pool-corruption family.)

## CORRUPTION-RISK sites (mid-asyncpg wait_for) — ranked
| file:line | note |
|---|---|
| `base_engine/data/ingestion_scheduler.py:337` | `wait_for(run_resolution_backfill, 90s)` — SELECT/INSERT loop. 2nd confirmed instance. |
| `bots/base_bot.py:853` (+`:894` burst) | `wait_for(scan_and_trade(), 60s)` — per-bot asyncpg every cycle. **PRIMARY unresolved corruption source** (EB coordination handoff). Line moved from :811→:853 after the Option A edit. |
| `base_engine/coordination/trade_coordinator.py:552` | `_do_reap` 30s — `DELETE … RETURNING` on a raw session. |
| `base_engine/execution/order_gateway.py:882` | `reserve_position` 5/15s — `INSERT … ON CONFLICT … RETURNING`. **EXECUTION PATH.** |
| `bots/mirror_bot.py:845` | `_reliability_tracker.refresh` 120s — `get_user_resolution_counts_by_category()` scan. |
| `bots/mirror_bot.py:834` | `_update_elite_traders` 30s — DB elite-refresh. |
| `bots/mirror_bot.py:811` | `_reconcile_leader_positions` 60s — DB reads (background task). |
| `base_engine/execution/position_manager.py:288` | `_refresh_exit_learning` 5s — `get_session()` SELECT (same monitor loop S162 protects). |
| `bots/arbitrage_bot.py:347/355/363` | cross-market / bond / neg-risk sub-scans, 30/20/20s — DB correlation lookups. |
| `base_engine/data/ingestion_scheduler.py:239` | `_do_mini_backfill` — same class as :337. |

## DEFECT / conditional
| file:line | note |
|---|---|
| `base_engine/coordination/event_bus.py:68` | `wait_for` on arbitrary handlers; `except Exception` (:76) can't catch the injected CancelledError; a DB-touching handler gets poisoned. Run handlers shielded / forbid blocking-DB handlers. |
| `base_engine/execution/position_manager.py:1034` (`_pe.predict`, 2s) | mostly cache-CPU but can lazy-load via DB — verify cold path. |
| `base_engine/execution/position_manager.py:952` (`compute_exit_params`, 3s) | cached but cold path queries — verify. |

## SAFE (representative)
- `bots/base_bot.py:762` kill-switch check — **SAFE post-Option-A** (raw session + cached fallback; S235 guard discards poisoned conn next checkout). Monitor `kill_switch_cache_fallback=True` as pool-pressure proxy.
- `database.py:183/6367`, `signal_ingestion.py:727` — semaphore acquire only (cancellation-safe Py3.12+, bpo-47789).
- `prediction_engine.py:781` — uses `asyncio.shield` (intentional, correct).
- `execution_engine.py:386/413` — Web3 RPC, not asyncpg.
- All signal/news/LLM/CLOB-HTTP/WebSocket `wait_for` sites — network I/O, cancellation harmless.

## Uniform fix direction (when WI-21 is worked)
Replace client-side `asyncio.wait_for` around any coroutine that enters `get_session()`/`get_raw_session()` with server-side `SET LOCAL statement_timeout` per S162/S166/S177; extend `handle_error` to invalidate on the cancellation-corruption signatures. Each fix = its own one-fix commit + full suite + cross-bot verify + sign-off.
