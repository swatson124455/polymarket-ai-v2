# SHARED INFRASTRUCTURE AUDIT — Session 128
**Date**: 2026-03-24
**Scope**: Every `.py` file in `base_engine/`, `bots/base_bot.py`, `config/settings.py`, `main.py`, `deploy/`, `esports/` — ~250 files audited line-by-line via 17 parallel agents.

---

## MASTER PRIORITY TABLE

| # | Sev | Module | File:Line | One-liner |
|---|-----|--------|-----------|-----------|
| 1 | **P0** | execution | `order_gateway.py:1231` | `_execute_with_retry` passes unknown `correlation_id` kwarg — every live trade crashes with `TypeError` |
| 2 | **P0** | prediction | `prediction_engine.py:2512` | Local `from datetime import` shadows module-level — `predict()` crashes with `UnboundLocalError` for any market with `end_date_iso` |
| 3 | **P0** | base_engine | `base_engine.py:1451` | `_fetch_tradeable_markets()` called with 0 args (needs 3) — market index pre-population silently fails every startup |
| 4 | **P1** | execution | `order_gateway.py:839` | `_check_price` NameError in edge-eroded logging — crashes trade + leaks coordinator lock |
| 5 | **P1** | execution | `advanced_orders.py:150,184,217` | Stop-loss/TP/trailing hardcode `side="SELL"` — violates YES/NO mandate |
| 6 | **P1** | polymarket_client | `polymarket_client.py:272-402` | Retry loop falls through on 429 exhaustion → returns `None` + doubles request volume |
| 7 | **P1** | kill_switch | `kill_switch.py:59` | First DB failure defaults to "allow trading" — should fail-safe (block) |
| 8 | **P1** | base_engine | `base_engine.py:777` | `os` not imported — mempool monitor crashes in live mode |
| 9 | **P1** | lifecycle | `lifecycle.py:24-51` | Single try/except around all shutdown steps — one failure skips DB close, reservation release, model save |
| 10 | **P1** | signals | `signal_ingestion.py:1165+` | `score=` should be `sentiment_score=` — streaming sentiment completely dead |
| 11 | **P1** | signals | `signal_ingestion.py:1056` | `get_divergences()` called with `List` not `Dict` — sentiment divergence dead |
| 12 | **P1** | signals | `signal_ingestion.py:321` | `fetch_elections()` doesn't exist — should be `fetch_upcoming_elections()` |
| 13 | **P1** | monitoring | `health_scheduler.py:291` | Queries `paper_trades.metadata` which doesn't exist — sports Kelly calibration dead |
| 14 | **P1** | config | `settings.py:720,746` | Duplicate `WEATHER_COMBINED_BOOST_CAP` — S122 fix silently reverted from 1.5→2.0 |
| 15 | **P1** | learning | `learning_engine.py:466-492` | Simulation errors pollute `self.patterns` namespace, eviction deletes structural dicts |
| 16 | **P1** | learning | `scheduler.py:591` | `_date.today()` uses local TZ not UTC — daily alert wrong time during BST |
| 17 | **P1** | main.py | `main.py:554` | Preflight result discarded — bots start even when API+DB both unreachable |
| 18 | **P1** | esports | `esports_trainer.py:509` | Cross-game XGBoost no early stopping — 200 trees overfit, bad probs for all 8 games |
| 19 | **P1** | database | `database.py:3553` | `backfill_positions_resolution` zeroes out unrealized_pnl immediately after computing it |
| 20 | **P1** | resolution | `resolution_backfill.py:470` | Phase 4b partial-exit double-counting — RESOLUTION records P&L on full size |
| 21 | **P1** | polymarket_client | `polymarket_client.py:272-403` | Circuit breaker trips 3x too slow (inner retry hides failures) |

**P0 count: 3 | P1 count: 18 | Total critical: 21**

---

## P0 BUGS (System-breaking, fix immediately)

### P0-1: Live trade `TypeError` — `_execute_with_retry` passes unknown kwarg
**File**: `base_engine/execution/order_gateway.py:1231`
**What**: `_execute_with_retry()` calls `self.execution_engine.place_order(..., correlation_id=correlation_id)` but `ExecutionEngine.place_order()` does NOT accept `correlation_id`.
**Impact**: The instant `SIMULATION_MODE=False`, every live order crashes with `TypeError`. This is a **go-live blocker**.
**Fix**: Add `correlation_id: Optional[str] = None` to `ExecutionEngine.place_order()` signature, or remove the kwarg from the call.

### P0-2: `predict()` crashes on any market with `end_date_iso`
**File**: `base_engine/prediction/prediction_engine.py:2512`
**What**: `from datetime import datetime, timezone` inside a conditional branch makes `datetime` local for the entire `predict()` function. Lines 2488 and 2569 use `datetime` BEFORE line 2512, triggering `UnboundLocalError`.
**Impact**: All 14 bots crash on `predict()` for any market with an end date. Silently swallowed → returns no prediction → bot skips all dated markets.
**Fix**: Delete line 2512. The top-level import on line 10 already provides `datetime` and `timezone`.

### P0-3: Market index pre-population silently fails every startup
**File**: `base_engine/base_engine.py:1451`
**What**: `await self._fetch_tradeable_markets()` called with 0 args but signature requires 3: `(self, min_liquidity, categories, _now)`. Inside try/except → silently swallowed.
**Impact**: WS messages in first 30s have no market metadata → price updates can't resolve condition_id to market.
**Fix**: Call with defaults: `await self._fetch_tradeable_markets(None, None, time.monotonic())` or give params defaults.

---

## P1 BUGS (Financially impactful or safety-critical)

### P1-4: `_check_price` NameError crashes edge gate + leaks coordinator lock
**File**: `order_gateway.py:839`
**Impact**: Edge-eroded gate crashes with `NameError`, trade fails, coordinator reservation never released → market locked.
**Fix**: Replace `_check_price` with `_shadow_best_ask`.

### P1-5: Advanced orders hardcode `side="SELL"`
**File**: `advanced_orders.py:150,184,217`
**Impact**: Stop-loss/TP/trailing pass `"SELL"` to `place_order()` instead of position's actual side. Paper engine and position tracking may handle this wrong.
**Fix**: Use the position's original side (`"YES"` or `"NO"`), not raw `"SELL"`.

### P1-6: API retry returns `None` on 429 exhaustion + doubles requests
**File**: `polymarket_client.py:272-402`
**Impact**: During rate limiting, every API call silently returns `None` causing downstream `TypeError` crashes, AND doubles request volume (feedback loop making rate limiting worse).
**Fix**: Add explicit `raise RuntimeError(...)` after the retry `for` loop.

### P1-7: Kill switch defaults to "allow trading" on first DB failure
**File**: `kill_switch.py:59`
**Impact**: If kill switch is engaged but DB is down at startup, bots trade for 30s before next check. Should fail-safe.
**Fix**: Return `True` (engaged) when `_cache_engaged is None` and DB check fails.

### P1-8: `os` not imported in `base_engine.py`
**File**: `base_engine.py:777`
**Impact**: Mempool monitor crashes in live mode with `NameError`. Dormant in paper trading.
**Fix**: Add `import os` to imports.

### P1-9: Lifecycle shutdown — one failure skips all remaining steps
**File**: `lifecycle.py:24-51`
**Impact**: If scheduler.stop() fails, DB close, reservation release, model save all skipped → connection leak, ghost locks, data loss.
**Fix**: Wrap each shutdown step in its own try/except.

### P1-10/11/12: Three signal subsystems completely dead
**Files**: `signal_ingestion.py:1165`, `:1056`, `:321`
**Impact**: Streaming sentiment, sentiment divergence, and election tracking all crash on every call with wrong kwarg/wrong type/wrong method name. Errors silently swallowed.
**Fix**: (10) `score=` → `sentiment_score=`, (11) pass `Dict[str, float]` not `List[str]`, (12) `fetch_elections()` → `fetch_upcoming_elections()`.

### P1-13: Sports Kelly calibration queries nonexistent column
**File**: `health_scheduler.py:291`
**Impact**: Queries `paper_trades.metadata` which doesn't exist → Kelly fractions never calibrate for sports bots.
**Fix**: Query `trade_events.event_data` instead.

### P1-14: Duplicate config silently reverts WeatherBot sizing cap
**File**: `settings.py:720,746`
**Impact**: S122 lowered `WEATHER_COMBINED_BOOST_CAP` to 1.5 (line 720). Duplicate at line 746 silently overrides to 2.0 → positions 33% larger than intended.
**Fix**: Delete line 746.

### P1-15/16: Learning engine pattern eviction + wrong timezone
**Files**: `learning_engine.py:466-492`, `scheduler.py:591`
**Impact**: (15) Simulation errors pollute `self.patterns` → eviction deletes structural dicts → learning dies. (16) `_date.today()` uses local TZ during BST → daily alert at wrong time.
**Fix**: (15) Move simulation data to separate dict. (16) Use `datetime.now(timezone.utc).date()`.

### P1-17: Preflight result discarded — bots start blind
**File**: `main.py:554`
**Impact**: API and DB both unreachable → bots start anyway → immediate fail loops.
**Fix**: Check preflight result, abort if both API and DB down.

### P1-18: Cross-game XGBoost overfits (no early stopping)
**File**: `esports_trainer.py:509`
**Impact**: 200 trees at depth 4 with no eval set → memorizes noise → bad probability estimates for all 8 games.
**Fix**: Add `eval_set=[(X_val, y_val)], early_stopping_rounds=20`.

### P1-19: Resolution P&L zeroed immediately after computation
**File**: `database.py:3553`
**Impact**: `UPDATE positions SET unrealized_pnl = 0 WHERE status = 'closed'` runs AFTER computing resolution P&L → destroys it every 30-min backfill.
**Fix**: Run zero-out BEFORE resolution update, or exclude resolution-closed positions.

### P1-20: Phase 4b partial-exit double-counting
**File**: `resolution_backfill.py:470`
**Impact**: If position is 50% exited, RESOLUTION still records P&L on FULL size → double-counts the exited portion.
**Fix**: Subtract EXIT sum from `total_size` before emitting RESOLUTION.

### P1-21: Circuit breaker trips 3x too slow
**File**: `polymarket_client.py:272-403`
**Impact**: Inner retry loop burns 3 HTTP requests per circuit breaker "call" → needs 15 actual failures to trip (not 5).
**Fix**: Move retry loop outside `circuit_breaker.call()`, or make each attempt a separate call.

---

## P2 BUGS (52 findings)

### Paper Trading / Base Bot
| File | Issue |
|------|-------|
| `paper_trading.py:231` | `seed_positions_from_db` loads ALL bots' positions into one cash pool — no per-bot isolation |
| `paper_trading.py:278` | Realized P&L restoration sums ALL bots — inflates shared cash |
| `paper_trading.py:695` | `self.trades` list grows unbounded in memory |
| `paper_trading.py:450` | `original_price` captured after bid/ask override — slippage understated |
| `base_bot.py:477-509` | Signal/flow/trends services each called TWICE per market (tracked wrappers re-query) |
| `base_bot.py:91,539` | `_pending_signal_meta` never cleared for non-traded markets — memory leak |

### Risk / Bankroll / Position Manager
| File | Issue |
|------|-------|
| `risk_manager.py:572` | CVaR cache keyed on position COUNT not content — stale on position swaps |
| `risk_manager.py:292` | `check_risk_limits` has no `side` param — NO-side exposure understated up to 9x |
| `risk_manager.py:473-487` | DB fallback missing EsportsBot exposure isolation |
| `position_manager.py:883-922` | `set_position_limits` is a no-op that returns `True` |
| `settings.py:152` | `TOTAL_CAPITAL` defaults $100K vs real $20K — % risk checks 5x too loose |
| `correlation_risk.py:43` | CVaR queries nonexistent `market_prices` table — correlation always identity matrix |

### Execution Module
| File | Issue |
|------|-------|
| `advanced_orders.py:101-109` | Missing `monitoring` attribute before `set_order_gateway()` → `AttributeError` |
| `order_management_system.py:237-252` | Partial fill uses `=` not `+=` — fills never accumulate, orders never complete |
| `smart_order_router.py:46-47,117` | Uses `BUY/SELL` throughout — `YES/NO` callers get wrong book side |
| `smart_order_router.py:148` | Hardcoded `price=0.5` for market orders — never fills at real prices |

### Database / Resolution
| File | Issue |
|------|-------|
| `resolution_backfill.py:549` | Phase 4b-alt fee calc wrong — fee on entry cost, not payout |
| `database.py:3302` | Inconsistent fee calculation across Phase 4 vs Phase 4b-alt |
| `database.py:4893` | Shadow fill resolution ignores position side — wrong P&L for NO entries |

### Analysis Module
| File | Issue |
|------|-------|
| `wash_trading_detector.py:56` | Queries `positions.created_at` — column doesn't exist → detector 100% dead |
| `wash_trading_detector.py:64-70` | Queries `market_prices.high_price/low_price` — columns don't exist |
| `market_regime.py:69` | Python 3.13 local import scoping trap (latent crash) |
| `trade_journal.py:131` | Python 3.13 local import scoping trap (latent crash) |
| `performance_attribution.py:46` | Reads `performance_records` not `trade_events` (wrong P&L source) |
| `trade_journal.py:39` | Reads `trades`/`performance_records` not `trade_events` (wrong P&L source) |

### Kill Switch / Drawdown / Ingestion
| File | Issue |
|------|-------|
| `drawdown_controller.py:76` | Weekly P&L falls back to daily → 15% weekly guard is dead |
| `risk_manager.py:45` | `_consecutive_losses` not persisted — resets on restart |
| `data_ingestion.py:2104-2110` | sync_log cleanup doesn't catch `BaseException` → zombie "running" entry (same class as S90 outage) |

### WebSocket / Polymarket Client
| File | Issue |
|------|-------|
| `polymarket_client.py:245-252` | `_backoff_until` read outside lock → race to `TypeError` |
| `websocket_manager.py:292` | `or` chain treats `best_bid=0` as falsy → wrong price at resolution |

### Data Module
| File | Issue |
|------|-------|
| `data_archival.py:50-52` | Stale cutoff timestamps never refreshed after init |
| `data_archival.py:86+` | SQL injection via f-string table/column interpolation |
| `data_archival.py:224-239` | `restore_archived_data` is a no-op, silently reports success |
| `query_pagination.py:47,52` | SQL injection via f-string `order_by` interpolation |
| `streaming_persister.py:136-141` | Fire-and-forget whale callback + deprecated `get_event_loop()` |

### Cache / Config / Coordination
| File | Issue |
|------|-------|
| `event_bus.py:140` | Fire-and-forget `create_task` for event persistence — audit trail has gaps |
| `event_bus.py:99-105` | `emit_sync` silently drops events when no running loop |
| `arbitrage_coordinator.py:82+` | ArbitrageBot passes `side="BUY"/"SELL"` → ghost positions on exit |

### Features / Prediction
| File | Issue |
|------|-------|
| `calibration.py:137` | SQL injection via `.replace(":category", ...)` |
| `calibration.py:42-45` | INTERVAL with string replacement bypasses bind params |
| `counterparty_classifier.py:70` | Window function inside aggregate = always-failing SQL |

### Monitoring
| File | Issue |
|------|-------|
| `anomaly_detector.py:119` | Missing `asyncio` import — async anomaly callbacks silently fail |
| `alerting.py:448-458` | Daily P&L summary reads `paper_trades` instead of `trade_events` |
| `health_runner.py:561-576` | `asyncio.gather(return_exceptions=True)` silently swallows check failures |
| `pipeline_gate.py:91` | SQL injection via string interpolation |

### Signals
| File | Issue |
|------|-------|
| `whale_tracker.py:306` | Redis `pipe.expire()` only expires LAST category — memory leak |
| `whale_tracker.py:140` | Default `side="BUY"` violates YES/NO mandate |
| `signal_ingestion.py:1058` | Checks `divergence_type` but field is `type` — divergence signals never fire |
| `whale_tracker.py:267` | Missing `_naive_utc()` on Trade.timestamp filter |

### Utils / Exchanges
| File | Issue |
|------|-------|
| `strategy_analytics.py:69` | Python 3.13 local import crash — `UnboundLocalError` on every call |
| `performance_tracker.py:85` | `ExecutionQuality` model doesn't exist — all persistence silently fails |
| `performance_tracker.py:62` | Slippage sign used as trade side — garbage metrics |
| `db_session.py:50` | Exception type wrapping — callers can't catch specific DB errors |

### Base Engine
| File | Issue |
|------|-------|
| `base_engine.py:1093,1126` | `multi_kill_switch` passed as None to OrderGateway, backfilled later |
| `base_engine.py:2293` | Stale `_now` after lock acquisition — serves cache older than TTL |
| `base_engine.py:1597` | `DegradationManager total_bots=8` but system has 14 |

### Deploy / Esports
| File | Issue |
|------|-------|
| `deploy.sh:41` | `compileall` skips `esports/` — syntax errors deploy to VPS |
| `main.py:37` | Log file handle never closed — FD leak on restarts |
| `opendota_client.py:55` | New aiohttp session per request |
| `opendota_client.py:39` | Race on global rate limiter — burst violates rate limit |
| `esports_trainer.py:400-402` | Cross-game training mutates shared row dicts |
| `esports_data_collector.py:505` | ON CONFLICT partial index may not match DB — training data silently lost |

### Learning
| File | Issue |
|------|-------|
| `learning_engine.py:466-475` | Pattern eviction can delete structural dicts |
| `learning_engine.py:164` | `user_performance` save/load operates on empty BoundedDict |
| `scheduler.py:602` | Fire-and-forget task reference can be GC'd |
| `incremental_learning.py:15-16` | Circular import risk |
| `learning_engine.py:470` | Dead `hasattr` guard makes config unreachable |

---

## P3 BUGS (58 findings — abbreviated)

Key themes at P3:
- **8 × new aiohttp session per request** (noaa_data, wikipedia, kalshi, opendota, cot_validator, etc.)
- **6 × wrong P&L formulas** (backtest NO-side inversion, strategy analytics sign flip, VaR on losses-only, etc.)
- **5 × Python 3.13 scoping traps** (redundant local imports in market_parser_v2, bayesian_model, etc.)
- **5 × unbounded cache/dict growth** (ws_price_cache, market_vol_cache, oracle_risk_cache, etc.)
- **4 × SQL against nonexistent columns** (airdrop_tracker `created_at`, chronos `price_history`, etc.)
- **3 × BUY/SELL vs YES/NO convention violations** (liquidity_guardian, elite_reliability, order_flow)
- **3 × rate limiter issues** (double-counted denials, inverted priority, Google Trends thread pool starvation)
- **2 × wrong starting_capital** ($10K default vs $20K real)
- **2 × deprecated `datetime.utcnow()`** and `asyncio.get_event_loop()`

---

## P4 BUGS (12 findings — abbreviated)

Minor issues: dead variables, fragile private API access, deprecated patterns, cosmetic inconsistencies.

---

## SUMMARY COUNTS

| Severity | Count |
|----------|-------|
| P0 | 3 |
| P1 | 18 |
| P2 | 52 |
| P3 | 58 |
| P4 | 12 |
| **Total** | **143** |

### Subsystems Most Affected
1. **Execution module** (order_gateway, advanced_orders, OMS, smart_order_router) — 11 bugs including the P0 live blocker
2. **Signal ingestion** — 3 P1s, entire subsystems dead
3. **Risk/drawdown** — multiple guards silently disabled ($100K denominator, weekly guard dead, consecutive loss resets)
4. **Resolution backfill** — P&L double-counting and zeroing actively corrupt data
5. **Monitoring** — health checks, calibration, anomaly detection all have silent failure paths

### Recommended Fix Order
1. **P0-1** (live trade TypeError) — 1-line fix, go-live blocker
2. **P0-2** (predict() crash) — 1-line deletion
3. **P0-3** (market index startup) — 1-line fix
4. **P1-14** (duplicate config cap) — 1-line deletion
5. **P1-19** (resolution P&L zeroed) — reorder SQL statements
6. **P1-6** (API retry None return) — add `raise` after retry loop
7. **P1-7** (kill switch fail-safe) — change `return False` to `return True`
8. **P1-9** (lifecycle shutdown) — wrap each step in own try/except
9. **P1-17** (preflight ignored) — check return value, abort if both down
10. All remaining P1s, then P2s by financial impact
