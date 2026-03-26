# Session 94 — EsportsBot Latency Optimization (HANDOFF)

**Date**: 2026-03-15
**Bot scope**: EsportsBot ONLY — no other bot modifications
**Branch**: `master` (working tree has uncommitted Phase 2C/3 changes)
**Operator**: Sam Watson
**Prior sessions**: S89 (features + audit), S88 (observation mode fix), S87 (resolution dedup), S85 (data overhaul)

---

## SESSION OBJECTIVE

Reduce EsportsBot trade latency from ~6-9s (scan loop) to ~50-200ms (WebSocket reactive). Three-part plan:

1. **Part 1**: Scan cycle optimization — batch DB queries, time-based guards (DEPLOYED)
2. **Part 2**: PandaScore latency — remove API from scan hot path via background prefetch (DEPLOYED)
3. **Part 3**: Event-driven WS trading — promote WS to primary trader, scan becomes cache-warmer (IN PROGRESS)

---

## PART 1: Scan Latency Optimization (COMMITTED & DEPLOYED)

**Commit**: `d77c253` — `feat(esports): S94 scan latency optimization — 4-6x faster live detection`
**Deploy**: `20260315_203031`
**Files**: `bots/esports_bot.py`, `esports/data/esports_db.py`, `tests/unit/test_esports_bot.py`

### Changes:
1. **Time-based guards** — Converted scan-count modulo guards to monotonic time-based guards. With scan interval at 0s during live matches, modulo guards fired too frequently. Now uses `_monitoring_last_check`, `_last_outcome_backfill`, etc. with proper intervals.

2. **Scan interval = 0s for live matches** — `get_scan_interval()` returns 0 when `self._live_matches` is non-empty (was 10s). Immediate rescan for maximum liveness.

3. **Parallel positions fetch** — `asyncio.gather()` for positions fetch + monitoring thresholds check (were sequential).

4. **Batch rolling accuracy** — `get_rolling_accuracy_batch()` in `esports_db.py` — single query using `ROW_NUMBER() OVER (PARTITION BY game)` window function replaces 18 sequential per-game queries.

5. **Rolling accuracy cache** — `_get_cached_rolling_accuracy(db, last_n=50)` with 5-min TTL. Cache keyed by `last_n` as `Dict[int, Dict[str, Dict]]` to support both `last_n=50` (monitoring) and `last_n=20` (de-graduation).

6. **All 5 callers updated**:
   - `_check_monitoring_thresholds()` — uses `_get_cached_rolling_accuracy(db)` (default last_n=50)
   - `_update_kelly_multipliers()` — uses batch cache
   - `_check_degraduation()` — uses `_get_cached_rolling_accuracy(db, last_n=20)`
   - `_get_game_stats()` — uses batch cache
   - `analyze_opportunity()` → `_compute_conformal_interval()` — uses batch cache

7. **Test updates** — 4 Kelly graduation tests updated to mock `get_rolling_accuracy_batch` with `{game: acc_dict for game in all_8_games}` batch format.

### Result:
- Scan cycle ~7-10s on VPS (confirmed)
- Rolling accuracy: 18 sequential queries → 1-2 cached batch queries per 5min

---

## PART 2: PandaScore Latency Optimization (COMMITTED & DEPLOYED)

**Commit**: `c243f56` — `feat(esports): S94 PandaScore latency optimization — remove API from scan hot path`
**Deploy**: `20260315_225400`
**Files**: `bots/esports_bot.py`, `esports/data/pandascore_client.py`, `config/settings.py`

### Changes:

1. **Form cache TTL bump** — `_team_form_ttl` from 1800s (30min) to 5400s (90min). Form changes once per match (~2-4h). Rationale: reduces PandaScore API calls in scan loop.

2. **Startup warmup** — `_warm_form_cache()` method. Pre-fetches form for up to 50 rated teams during `start()`. Runs after `_init_glicko2_trackers()`. 0.5s between calls, stops if budget < 700. Confirmed `teams_warmed=50` on VPS.

3. **Background form prefetch** — `_background_form_prefetch()` method. Continuous `asyncio.Task` started in `start()`, cancelled in `stop()`. Priority: live match teams first, then all rated teams. Rate: ~1 req/18s (~200/hr). Pauses if budget < 500. 20-min target cycle time. Confirmed `form_prefetch_started` on VPS.

4. **Configurable PandaScore rate limits** — `pandascore_client.py`:
   - `_get_rate_config()` reads `PANDASCORE_RATE_LIMIT_PER_HOUR` (default 1000) and `PANDASCORE_CIRCUIT_BREAKER_BUFFER` (default 50) from settings
   - `_RATE_LIMIT`, `_CB_BUFFER`, `_HARD_LIMIT` are module-level constants (no per-request overhead)
   - `get_remaining_budget()` uses `_HARD_LIMIT` instead of hardcoded 950
   - `_get()` rate check uses `_HARD_LIMIT` and `_RATE_LIMIT`
   - Milestone logging uses percentage-based thresholds: `{int(_RATE_LIMIT * p) for p in (0.50, 0.75, 0.90, 0.95, 0.99)}`

5. **WebSocket upgrade path** — Documentation block at end of `pandascore_client.py` + `PANDASCORE_USE_WEBSOCKET` flag in settings. Stub only — no WS client implemented.

6. **`config/settings.py` additions**:
   ```python
   PANDASCORE_RATE_LIMIT_PER_HOUR = int(os.getenv("PANDASCORE_RATE_LIMIT_PER_HOUR", "1000"))
   PANDASCORE_CIRCUIT_BREAKER_BUFFER = int(os.getenv("PANDASCORE_CIRCUIT_BREAKER_BUFFER", "50"))
   PANDASCORE_USE_WEBSOCKET = os.getenv("PANDASCORE_USE_WEBSOCKET", "false").lower() == "true"
   ```

### Result:
- Scan-loop PandaScore calls drop from 0-50 to ~0 (always pre-cached)
- Background prefetch handles all form data refresh outside scan loop

---

## PART 3: Event-Driven WS Trading (IN PROGRESS — NOT COMMITTED)

**Plan file**: `C:\Users\samwa\.claude\plans\typed-fluttering-hummingbird.md`

### Architecture:
```
BEFORE:  scan_and_trade() [6-9s] → analyze + trade all 32 markets
         on_price_update() [1ms]  → trade if cached (partial guards)

AFTER:   scan_and_trade() [1-2s] → analyze + refresh prediction cache ONLY
         on_price_update() [50-200ms] → trade with FULL guards (primary trader)
         Fallback: if no WS events for 30s → scan resumes trading
```

### Phase 1: WS Guards + Lock Fix — CODE COMPLETE (uncommitted)

**5 missing WS guards** added in `on_price_update()` after `game = cached.get("game", "")` (line ~533):
```python
# S94: WS guards matching scan path (were missing — race/safety fix)
if self._check_daily_loss_limit():                          # scan: L674
    return
if game in self._monitoring_halted_games:                   # scan: L1249
    return
if self._patch_drift and self._patch_drift.is_observation_mode(game):  # scan: L1267
    return
if self._patch_drift and self._patch_drift.is_halted(game): # scan: L1277
    return
_max_game_exp = float(getattr(settings, "ESPORTS_MAX_GAME_EXPOSURE", 300.0))
if self._game_exposure.get(game, 0.0) >= _max_game_exp:     # scan: L1254
    return
```

**`_trade_lock` race fix** — WS path now uses `async with self._trade_lock:` around `_execute_esports_trade(opp)` (line ~610). Previously called WITHOUT lock — both WS and scan paths mutate `_game_exposure`.

### Phase 2: Scan → Cache-Warmer — CODE COMPLETE (uncommitted)

**New instance vars** in `__init__()` (line ~170):
```python
self._ws_trading_active: bool = True
self._last_ws_price_ts: float = 0.0  # monotonic time of last WS price event
```

**WS liveness tracking** — `self._last_ws_price_ts = time.monotonic()` added in `on_price_update()` after `await super().on_price_update(event)` (line ~483).

**WS health check** added before `_analyze_one()` definition (line ~870):
```python
_ws_threshold = float(getattr(settings, "ESPORTS_WS_STALE_THRESHOLD_S", 30.0))
_ws_healthy = (self._last_ws_price_ts > 0
               and (time.monotonic() - self._last_ws_price_ts) < _ws_threshold)
if _ws_healthy != self._ws_trading_active:
    self._ws_trading_active = _ws_healthy
    if _ws_healthy:
        logger.info("esportsbot_ws_trading_resumed")
    else:
        logger.warning("esportsbot_ws_trading_fallback",
                       last_ws_age_s=round(time.monotonic() - self._last_ws_price_ts, 1))
```

**Modified `_analyze_one()`** (line ~883):
```python
async def _analyze_one(m: Dict) -> tuple:
    async with self._analysis_semaphore:
        mid = str(m.get("id", ""))
        if og and mid and og.has_open_position(self.bot_name, mid):
            return (0, 0, 1)
        opp = await self.analyze_opportunity(m)
        if opp and not self._ws_trading_active:
            # Fallback: scan trades when WS is stale
            async with self._trade_lock:
                await self._execute_esports_trade(opp)
            return (1, 1, 0)
        elif opp:
            return (1, 0, 0)  # Opportunity found; WS will trade it
        return (0, 0, 0)
```

**Key behavior**: `analyze_opportunity()` ALWAYS runs (refreshes `_prediction_cache` and `_market_token_map`). Trading only happens in scan loop when WS is stale (>30s without WS price events). This preserves all existing function.

### Phase 2 TODO — NOT YET DONE:
- Add `ws_trading=self._ws_trading_active` to scan summary log (line ~929, after `backfills_this_scan`)

### Phase 3: Smart Prediction Invalidation — NOT STARTED

The plan is approved but no code has been written. Here's what needs to happen:

**3A. New instance var** in `__init__()`:
```python
self._prediction_refresh_needed: Set[str] = set()  # market_ids that need re-prediction
```

**3B. New method** `_should_repredict()`:
```python
def _should_repredict(self, market_id: str) -> bool:
    """Skip re-prediction if all inputs unchanged and cache is fresh (<5min)."""
    if market_id in self._prediction_refresh_needed:
        self._prediction_refresh_needed.discard(market_id)
        return True
    cached = self._prediction_cache.get(market_id)
    if not cached:
        return True  # Never predicted
    age = time.monotonic() - cached.get("ts", 0)
    if age > 300.0:  # 5-min max staleness
        return True
    if market_id in self._live_matches:
        return True  # Live matches have rapidly changing state
    return False
```

**3C. Invalidation triggers** — Mark markets for re-prediction when inputs change:
- After `_refresh_live_matches()` updates `_live_matches`: add all affected market_ids to `_prediction_refresh_needed`
- After Glicko-2 rating updates (match resolution): add all markets for that game
- After form cache refresh (background prefetch updates a team): add markets involving that team

**3D. Modify `_analyze_one()`** to skip unchanged markets:
```python
async def _analyze_one(m: Dict) -> tuple:
    async with self._analysis_semaphore:
        mid = str(m.get("id", ""))
        if og and mid and og.has_open_position(self.bot_name, mid):
            return (0, 0, 1)
        if self._ws_trading_active and not self._should_repredict(mid):
            return (0, 0, 0)  # Cache still valid, WS using it
        opp = await self.analyze_opportunity(m)
        if opp and not self._ws_trading_active:
            async with self._trade_lock:
                await self._execute_esports_trade(opp)
            return (1, 1, 0)
        elif opp:
            return (1, 0, 0)
        return (0, 0, 0)
```

**Expected result**: Scan evaluates ~2-5 markets (those with changed inputs) instead of all 32. Scan time drops from ~6-9s to ~1-2s.

---

## UNCOMMITTED NON-ESPORTS CHANGES IN WORKING TREE

The working tree has changes from other sessions that are NOT part of S94. Do NOT commit these with S94:

| File | Source | Description |
|------|--------|-------------|
| `base_engine/data/database.py` | S93 (MirrorBot) | Fix uPnL formula — uniform `(current-entry)*size` for both YES/NO |
| `base_engine/risk/risk_manager.py` | S94 shared latency | CVaR cache 30→120s TTL, `cvar_after` cache (5s TTL), sims 10k→2k |
| `base_engine/risk/correlation_risk.py` | S94 shared latency | `n_simulations` default 10000→2000 |
| `base_engine/weather/forecast_client.py` | S92 (WeatherBot) | P1 model-run jump detection + `forecast_delta` |
| `base_engine/weather/probability_engine.py` | S92 (WeatherBot) | P2 NBM benchmark `compute_nbm_benchmark()` |
| `tests/unit/test_mirror_bot_logic.py` | S93 (MirrorBot) | Update stop-loss PnL tests for uniform formula |
| `bots/esports_bot.py` | **S94 Part 3** | **THIS SESSION** — WS guards, lock fix, WS-primary mode |
| `run_ui.py` (DELETED) | UI overhaul | Old run_ui replaced by `ui/app.py` |
| `ui/async_worker.py` (DELETED) | UI overhaul | Old async worker removed |
| `ui/dashboard.py` (DELETED) | UI overhaul | Old dashboard removed |

**To commit S94 Part 3 esports changes only**: `git add bots/esports_bot.py && git commit`

---

## S94 ALSO COMMITTED (non-esports, not in this bot scope)

These were committed during S94 but affect shared modules:

| Commit | Description | Files |
|--------|-------------|-------|
| `9a1b5f9` | Lock-free DB writes + parallel persist + coordinator skip | `base_bot.py`, `paper_trading.py` |
| `654dd74` | WeatherBot scan cycle latency optimization — 4 fixes | `bots/weather_bot.py` |
| `1a120a7` | RTDS fast-path — skip risk/drawdown/fill model for copy trades | `bots/mirror_bot.py` |

---

## KEY FILES & THEIR ROLES

| File | Purpose | Modified in S94? |
|------|---------|-----------------|
| `bots/esports_bot.py` | Main bot — 1400+ lines, scan loop + WS reactive trading | Yes (all 3 parts) |
| `esports/data/esports_db.py` | DB queries — `get_rolling_accuracy_batch()` added | Yes (Part 1) |
| `esports/data/pandascore_client.py` | PandaScore API client — rate limits, budget tracking | Yes (Part 2) |
| `config/settings.py` | All config vars — PandaScore rate limit settings added | Yes (Part 2) |
| `tests/unit/test_esports_bot.py` | Unit tests — Kelly graduation tests updated | Yes (Part 1) |

---

## CRITICAL KNOWLEDGE FOR CONTINUATION

### WS Reactive Trading Architecture (existing, pre-S94)
- `on_price_update()` (line ~450) receives ALL 26K+ market price updates via WebSocket
- Early exit: only processes markets in `_prediction_cache` (populated by scan loop's `analyze_opportunity()`)
- `_market_token_map` maps market_id → {yes: token_id, no: token_id} — needed to convert WS token prices to YES-equivalent
- `_ws_prev_prices` keyed by token_id (not market_id) to avoid YES/NO cross-contamination
- `_ws_cooldowns` prevent re-triggering same market within cooldown period (10s default, extended to 120s after trade)
- `_ws_pending_trades` set prevents concurrent WS trades on same market

### Prediction Cache
- `_prediction_cache: Dict[str, Dict]` — market_id → {prob, ts, game, ...}
- Populated by `analyze_opportunity()` during scan loop
- Used by `on_price_update()` for WS reactive trading
- Has 1h TTL (checked in `on_price_update()`)

### Scan Loop Flow (scan_and_trade)
1. `_step_patch_drift()` — check for game patches
2. `_refresh_live_matches()` — PandaScore live match data
3. `_step_get_markets()` — get tradeable esports markets from CLOB/index
4. `_analyze_one()` per market (parallel, semaphore-bounded to 10)
5. `_analyze_one()` calls `analyze_opportunity()` which:
   - Detects game, gets team names, checks form/ratings
   - Runs prediction pipeline (Glicko-2 + calibration + cross-game model)
   - Populates `_prediction_cache` and `_market_token_map`
   - Returns opportunity dict if edge > min_edge
6. If opportunity found, `_execute_esports_trade()` handles sizing + order placement
7. Series scan for BO3/BO5 markets
8. Periodic: outcome backfill, Glicko-2 retraining, monitoring thresholds

### Key Config (live VPS)
```
ESPORTS_MIN_CONFIDENCE=0.52
ESPORTS_MIN_EDGE=0.08
ESPORTS_MAX_GAME_EXPOSURE=300.0
ESPORTS_FRESHNESS_DECAY_SECONDS=30.0
ESPORTS_ANALYSIS_CONCURRENCY=10
ESPORTS_WS_PRICE_CHANGE_PCT=0.01
ESPORTS_WS_COOLDOWN_SECONDS=10
ESPORTS_WS_STALE_THRESHOLD_S=30.0 (new, default)
PANDASCORE_RATE_LIMIT_PER_HOUR=1000
PANDASCORE_CIRCUIT_BREAKER_BUFFER=50
```

### Deploy Process
```bash
# From local machine:
cd /path/to/polymarket-ai-v2
git add bots/esports_bot.py
git commit -m "feat(esports): ..."
git push origin master

# On VPS (34.251.224.21):
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
cd /opt/polymarket-ai
sudo ./deploy.sh   # atomic symlink swap
sudo systemctl restart polymarket-ai

# Verify:
journalctl -u polymarket-ai -f | grep "EsportsBot"
```

---

## REMAINING WORK (PRIORITY ORDER)

### 1. Add `ws_trading` to scan summary log (2 min)
In `bots/esports_bot.py` line ~929, add to the scan summary log:
```python
ws_trading=self._ws_trading_active,
```

### 2. Implement Phase 3: Smart Prediction Invalidation (~30 min)
See Phase 3 section above. Four sub-steps:
- 3A: Add `_prediction_refresh_needed: Set[str]` instance var
- 3B: Add `_should_repredict()` method
- 3C: Wire invalidation triggers (live match refresh, Glicko updates, form prefetch)
- 3D: Modify `_analyze_one()` to skip unchanged markets

### 3. Run Tests
```bash
pytest tests/ -k esports -x -q --tb=short
```

### 4. Commit S94 Part 3
```bash
git add bots/esports_bot.py
git commit -m "feat(esports): S94 event-driven WS trading — WS-primary with scan fallback"
```
Do NOT add non-esports files.

### 5. Deploy to VPS + Verify
```bash
# Deploy
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
# Run deploy.sh, restart, verify:
journalctl -u polymarket-ai -f | grep -E "ws_trading|ws_trading_fallback|WS reactive"
# Should see: ws_trading=True in scan summary, WS reactive trades firing
```

### 6. Write Session 94 Final Handoff
Update this document with deploy timestamp, test results, and any issues found.

---

## LATENCY TARGETS

| Metric | Before S94 | After Part 1+2 | After Part 3 (target) |
|--------|-----------|----------------|----------------------|
| Price change → trade | ~6-9s (next scan) | ~6-9s (scan) + 1ms (WS partial) | ~50-200ms (WS primary) |
| Scan cycle time | ~7-10s | ~7-10s | ~1-2s (smart invalidation) |
| PandaScore calls/scan | 0-50 | ~0 (pre-cached) | ~0 (pre-cached) |
| Rolling accuracy queries | 18 sequential | 1-2 batch (cached 5min) | 1-2 batch (cached 5min) |

---

## SAFETY INVARIANTS (MUST NOT BREAK)

1. **Scan loop ALWAYS runs** — even with WS primary, scan loop refreshes `_prediction_cache` and `_market_token_map`. If WS stale >30s, scan resumes trading.
2. **`_trade_lock`** — both WS and scan paths must use `async with self._trade_lock:` around `_execute_esports_trade()`. Both mutate `_game_exposure`.
3. **5 WS guards** — WS path must check: daily loss limit, monitoring halt, observation mode, patch halt, game exposure cap. All 5 now present.
4. **`analyze_opportunity()` always runs in scan** — it populates caches (prediction, token map). Phase 3's `_should_repredict()` skips it only for markets with fresh, unchanged predictions.
5. **Fallback activation** — `_ws_trading_active` flips to False when `_last_ws_price_ts` is >30s stale. Scan resumes trading. Flips back on next WS event.

---

## OPERATOR PREFERENCES (from memory)

- **Show plans in conversation** — User wants to see plans displayed in chat, not just written to plan files
- **Scope lock** — NEVER add unsolicited features. Fix only what's requested.
- **Bot-scoped sessions** — Each session modifies ONE bot. No bleed to other bots.
- **P&L math** — NEVER invert for NO positions. `cost = entry * size`, `uPnL = (current - entry) * size` for ALL sides.
- **YES/NO mandate** — `place_order()` requires `side="YES"/"NO"`. Never BUY/SELL.
- **Paper trading IS production** — No shortcuts because "we're only paper trading."

---

## GIT STATE SUMMARY

```
Committed S94 esports:
  d77c253 — Part 1: batch rolling accuracy, time-based guards, parallel positions
  c243f56 — Part 2: form cache TTL, startup warmup, background prefetch, configurable rates

Uncommitted (bots/esports_bot.py only — S94 Part 3):
  +39 lines, -3 lines
  - __init__: _ws_trading_active, _last_ws_price_ts instance vars
  - on_price_update: _last_ws_price_ts tracking + 5 WS guards + _trade_lock fix
  - scan_and_trade: WS health check + modified _analyze_one (skip trading when WS healthy)

Other uncommitted files: NOT PART OF S94. Do not commit with esports changes.
```
