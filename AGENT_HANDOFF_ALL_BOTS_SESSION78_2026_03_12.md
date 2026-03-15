# AGENT HANDOFF — ALL BOTS SESSION 78 (2026-03-12)
## Carbon-Copy Continuation Document

**Date:** 2026-03-12 00:30 UTC
**Session type:** ALL BOTS — WeatherBot deep-dive + cross-bot pattern transfer
**Previous sessions:** 72-77 (see MEMORY.md for full history)
**Branch:** `master` (PR target: `main`)

---

## WHAT THIS SESSION DID (Session 78)

### Part 1: Cross-Bot Pattern Transfer (MirrorBot + EsportsBot → WeatherBot)

Deep-scanned MirrorBot (987 lines, 20+ methods) and EsportsBot (1800+ lines) for transferable patterns. Identified 10 gaps, eliminated 3 as unnecessary, implemented 6 transfers + 1 critical bug fix. **~334 new lines in `bots/weather_bot.py`**.

#### 6 Cross-Bot Features Implemented:

**1. Prediction Logging** (from EsportsBot pattern)
- `_log_weather_prediction()` — dedup via cache (skip if same market_id changed < 0.01 within 600s)
- Calls `self.base_engine.db.insert_prediction_log(market_id, predicted_prob, market_price, model_name=f"weather_{market_type}", bot_name="WeatherBot", confidence=confidence)`
- Called from 4 analysis methods: `_analyze_group()`, `_analyze_precipitation_group()`, `_analyze_snowfall_group()`, `_analyze_wind_group()`
- Added `self._prediction_log_cache: Dict[str, Tuple[float, float]] = {}` to `__init__`

**2. Outcome Backfill** (from EsportsBot pattern)
- `_backfill_weather_outcomes()` — runs `db.backfill_prediction_log_resolution()` every 10 scans
- Also feeds consecutive loss tracker (`_record_weather_outcome()`)
- Added `self._scan_count: int = 0` to `__init__`, incremented at top of `scan_and_trade()`

**3. Consecutive Loss Tracking + Kelly Compression** (from EsportsBot `esports_bankroll_manager.py:40`)
- `_DRAWDOWN_SCHEDULE = [(8, 0.25), (5, 0.50), (3, 0.75)]`
- `_compute_weather_drawdown_factor(market_type)` — returns float multiplier per market type
- `_record_weather_outcome(market_type, won)` — resets on win, increments on loss
- Added `self._consecutive_losses: Dict[str, int] = {}` to `__init__`
- Applied in `_execute_weather_trade()` on `combined_boost`

**4. Per-Market-Type Adaptive Parameters** (from MirrorBot `bot_category_params` pattern)
- `_load_category_params()` — queries `bot_category_params` table (migration 034)
- `_get_min_edge(market_type)` — falls back to `self._min_edge` when no DB override
- Replaced `self._min_edge` with `self._get_min_edge(type)` at 4 filtering sites
- Added `self._category_params: Dict[str, Dict[str, float]] = {}` and `_category_params_loaded` flag

**5. Per-Station Reliability-Weighted Sizing** (from MirrorBot EliteReliabilityTracker)
- `_get_station_reliability_factor(station_id)` — queries `weather_calibration` AVG MSE over 14 days
  - MSE < 4: 1.2x, MSE 4-9: 1.0x, MSE 9-16: 0.8x, MSE > 16: 0.5x
- 1hr TTL cache: `self._station_mse_cache: Dict[str, Tuple[float, float]] = {}`
- Applied in `_execute_weather_trade()` on `combined_boost`

**6. EMOS Drift Detection** (from `base_engine/learning/calibration_tracker.py`)
- `_check_emos_drift()` — feeds binary errors into DDM/EDDM `DriftDetector` per station
- Advisory only — alerts but does NOT change trading behavior
- Added `self._drift_detectors: Dict[str, Any] = {}` to `__init__`

#### All 6 features wired in `scan_and_trade()`:
```python
# Phase 4b: every 10 scans
if self._scan_count % 10 == 0:
    await self._backfill_weather_outcomes()
    await self._check_emos_drift()
    await self._close_stale_positions()
```

### Part 2: Zombie Position Fix (P0 CRITICAL)

**Problem:** 269 positions stuck at `status='open'` in DB. Weather markets resolve in 24h but nothing closes positions in paper trading mode (no on-chain SELL, no `end_date_iso`). The `_open_position_markets` in-memory set blocked 71% of tradeable markets. Raw edges existed (Atlanta 0.97, Seoul 0.30) but `groups_with_edge=0` for 7+ hours.

**Root cause:** Weather markets have `end_date_iso = NULL` in the `markets` table. The existing `_close_expired_positions()` in `position_manager.py` checks `end_date_iso` — never fires for weather. Positions accumulate indefinitely.

**Fix (v1 — deployed):** `_close_stale_positions()` with 48h threshold. Ran on startup + every 10 scans. Closed 72 positions on first run. Bot immediately resumed trading (2 trades on next scan).

**Fix (v2 — deployed this session):** Lowered from 48h → 20h AND added resolved paper_trade check:
```python
"AND ("
"  opened_at < NOW() - INTERVAL '20 hours' "
"  OR market_id IN ("
"    SELECT pt.market_id FROM paper_trades pt "
"    WHERE pt.realized_pnl IS NOT NULL"
"  )"
") "
```
On startup: closed 75 positions. First scan: **20 trades, 6 groups_with_edge, best_edge=-0.3855**.

### Part 3: "Timing Gap" Investigation — False Alarm

**User reported:** 5h+ gaps between WeatherBot scans in the log file.

**Finding:** There were NO timing gaps. The bot scanned every 1-5 minutes continuously throughout. The "gaps" were an artifact of the log file only containing scans where `trades > 0`. During the 7h "gap" (15:11-22:30), the bot ran 100+ scans — all with `groups_with_edge=0, trades=0` due to zombie positions blocking all markets.

**Service restarts at 15:04 and 15:10:** These were from our deploy script (`deploy.sh`), not crashes. Process transitions (1915916 → 1959584 → 1964411) were all clean systemd stop/start cycles.

---

## CURRENT STATE (as of 2026-03-12 00:27 UTC)

### Bot Health
| Bot | Status | Latest Scan | Key Metric |
|-----|--------|-------------|------------|
| WeatherBot | ✅ Active, trading | 00:27 | 20 trades, 6 groups w/ edge |
| MirrorBot | ✅ Active | scanning | waterfall: ~600 parsed, ~400 rel_pass |
| EsportsBot | ✅ Active | scanning | 182 markets, 3 opportunities |
| EsportsLiveBot | ✅ Active | scanning | — |
| EsportsSeriesBot | ✅ Active | scanning | — |

### WeatherBot P&L
- **Resolved:** ~140 trades, ~+$461.74 net
- **Pending:** ~200 trades (markets settling March 10-13)
- **Open positions:** ~130 (after cleanup of 75 stale)
- **Win rate:** 44%, avg win $11.38, avg loss $3.13

### VPS Deployment
- **Latest release:** `/opt/pa2-releases/20260311_202259`
- **Process:** 2008508 (started 00:24 UTC)
- **All 5 bots scanning, no errors**
- **Tests:** 1299 passed, 0 failed

### Uncommitted Local Changes
```
M  base_engine/data/database.py          (+1 line)
M  base_engine/data/ingestion_error_capture.txt
M  base_engine/execution/paper_trading.py
M  base_engine/risk/bankroll_manager.py   (MirrorBot max_daily $1500→$10000)
M  bots/esports_bot.py                   (+75 lines)
M  bots/mirror_bot.py                    (+15 lines)
M  bots/weather_bot.py                   (+53 lines — stale position fix v2)
M  deploy/env.vps
M  esports/markets/esports_market_service.py (+45 lines)
```

---

## COMPLETE FILE MAP — WeatherBot

### Primary file: `bots/weather_bot.py` (~3580 lines)

#### __init__ state variables:
```python
self._prediction_log_cache: Dict[str, Tuple[float, float]] = {}
self._scan_count: int = 0
self._consecutive_losses: Dict[str, int] = {}
self._category_params: Dict[str, Dict[str, float]] = {}
self._category_params_loaded: bool = False
self._station_mse_cache: Dict[str, Tuple[float, float]] = {}
self._drift_detectors: Dict[str, Any] = {}
```

#### Key methods added this session:
| Method | Line | Purpose |
|--------|------|---------|
| `_close_stale_positions()` | ~382 | Close positions >20h old OR with resolved paper_trade |
| `_log_weather_prediction()` | ~after 3400 | Prediction logging with dedup cache |
| `_backfill_weather_outcomes()` | ~after 3415 | Outcome backfill + loss tracker feeding |
| `_compute_weather_drawdown_factor()` | ~after 3440 | Kelly compression per market type |
| `_record_weather_outcome()` | ~after 3450 | Consecutive loss tracking |
| `_load_category_params()` | ~after 3460 | Load per-type params from DB |
| `_get_min_edge()` | ~after 3480 | Per-type min_edge with global fallback |
| `_get_station_reliability_factor()` | ~after 3490 | MSE-based sizing factor (1hr cache) |
| `_check_emos_drift()` | ~after 3520 | DDM/EDDM drift detection per station |

#### Key existing methods (DO NOT BREAK):
| Method | Purpose |
|--------|---------|
| `scan_and_trade()` | Main scan loop — discovery → analysis → trades → precip/snow/wind |
| `_analyze_group()` | Temperature group analysis (multi-model ensemble) |
| `_execute_weather_trade()` | Single trade execution with all sizing factors |
| `_execute_group_trades()` | Multi-bucket S-T Kelly sizing |
| `_smoczynski_tomkins_allocate()` | Pro-rata Kelly allocation |
| `_restore_exposure_from_db()` | Restore group/city exposure from paper_trades |
| `_save_exit_to_redis()` / `_restore_exits_from_redis()` | Exit cooldown persistence |
| `_maybe_reload_calibration()` | EMOS calibration reload (per-station logging) |
| `_check_monitoring_thresholds()` | Dynamic Kelly graduation (MSE-based) |
| `_get_scan_interval_seconds()` | Adaptive interval (NWP windows: 30-120s, default: 300s) |

### Supporting files:
| File | Purpose |
|------|---------|
| `base_engine/data/database.py` | `insert_prediction_log()` (line 2731), `backfill_prediction_log_resolution()` (line 2833) |
| `base_engine/execution/order_gateway.py` | `_open_position_markets` (line 58), `reconcile_exposure_from_db()` (line 265) |
| `base_engine/execution/position_manager.py` | `_close_expired_positions()` (line 266) — doesn't work for weather (no end_date_iso) |
| `base_engine/execution/paper_trading.py` | Paper trade execution, slippage model |
| `base_engine/learning/calibration_tracker.py` | `DriftDetector` class (line 15) — DDM + EDDM |
| `esports/kelly/esports_bankroll_manager.py` | `_DRAWDOWN_SCHEDULE` at line 40 — pattern source |
| `config/settings.py` | `SCAN_INTERVAL_WEATHER=300`, `BOT_SCAN_TIMEOUT_SECONDS=300`, `BOT_MAX_CONSECUTIVE_ERRORS=10` |

---

## COMPLETE SYSTEM ARCHITECTURE

### 15 Bots in BOT_REGISTRY
- **5 ACTIVE:** WeatherBot, MirrorBot, EsportsBot, EsportsLiveBot, EsportsSeriesBot
- **9 DISABLED:** ArbitrageBot, CrossPlatformArbBot, OracleBot, SportsBot, LLMForecasterBot, SportsInjuryBot, SportsLiveBot, SportsArbBot, LogicalArbBot
- **1 DELETED:** MomentumBot
- **1 ARCHIVED:** EnsembleBot (−$5.6k)

### Order Pipeline
```
Bot.place_order() → BaseEngine → OrderGateway:
1. Kill Switch check
2. Risk Manager (limits, drawdown, loss caps, price bounds)
3. Cascade Detection
4. Liquidity Guardian (slippage)
5. TradeCoordinator.reserve_position()
6. PaperTradingEngine (SIMULATION_MODE=true) or ExecutionEngine (CLOB)
7. TradeCoordinator.confirm_position()
```

### Base Engine (50+ components, 11 dependency levels)
- L1: PolymarketClient, Database, RedisCache
- L2: DataIngestionService, UnifiedMarketService
- L3: LearningEngine, PredictionEngine, RiskManager, ExecutionEngine
- L4+: Signals, whale tracking, WebSocket, position mgmt, calibration, elite detector, paper trading, monitoring

### Scan Loop Architecture (`base_bot.py`)
- Exponential backoff on errors: `min(60, 2^min(failures, 6))` seconds
- Auto-stop after `BOT_MAX_CONSECUTIVE_ERRORS` (default 10) consecutive failures
- Scan timeout: `BOT_SCAN_TIMEOUT_SECONDS` (default 300s) — timeout counts as failure
- Watchdog in `main.py`: checks every 30s, restart backoff doubles (30→60→120→240→600s cap)

---

## STATE PERSISTENCE — ALL GAPS CLOSED

| State | Mechanism | Status |
|-------|-----------|--------|
| `_daily_exposure_usd` (all bots) | `daily_counters` — 60s flush + SIGTERM + startup restore | ✅ |
| `_game_exposure` (EsportsBot) | `daily_counters` write-through via `increment_counter()` | ✅ |
| `_group_exposure` + `_city_exposure` (WeatherBot) | `_restore_exposure_from_db()` from paper_trades | ✅ |
| `_daily_exposure` (MirrorBot) | `_restore_state_on_startup()` from paper_trades SUM | ✅ |
| Exit cooldowns (WeatherBot) | Redis TTL via `_save/_restore_exits_from_redis()` | ✅ |
| Open positions (all bots) | `order_gateway.seed_positions_from_db()` | ✅ |
| Stale positions (WeatherBot) | `_close_stale_positions()` — 20h threshold + resolved check | ✅ |

---

## CRITICAL TRAPS — DO NOT BREAK

1. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL.
2. **Deploy via `deploy.sh`**: `KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh`
3. **Rollback**: `bash deploy/rollback.sh` (same KEY/VPS vars)
4. **`paper_trades` has NO `metadata` column** — must JOIN with prediction_log for game info
5. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
6. **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer.
7. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
8. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
9. **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python string
10. **Weather markets have `end_date_iso = NULL`** — auto-close-on-expiry never fires. Use `_close_stale_positions()` instead.
11. **`_open_position_markets` in-memory set** blocks trade entry. Must be evicted when positions are closed in DB.
12. **`_market_meta_cache` in MirrorBot**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
13. **BOT_REGISTRY=14 bots** — shared module change requires all 14 verified.
14. **`websockets.exceptions` must be imported explicitly** (v15 lazy-loads)
15. **Position `current_price` auto-updated every 10s** by `position_manager._update_current_prices()`

---

## LIVE CONFIGURATION (VPS `/opt/pa2-shared/.env`)

```env
# WeatherBot
WEATHER_BOT_CAPITAL=5000
WEATHER_KELLY_FRACTION=0.25
WEATHER_MAX_BET_USD=500
WEATHER_MAX_DAILY_USD=2000
WEATHER_MAX_POSITIONS=500
WEATHER_MIN_EDGE=0.08

# MirrorBot
MIRROR_BOT_CAPITAL=3000
MIRROR_KELLY_FRACTION=0.30
MIRROR_MAX_BET_USD=250
MIRROR_MAX_DAILY_USD=10000
MIRROR_MAX_POSITIONS=200
MIRROR_MAX_PER_MARKET=400

# EsportsBot (shared across 3 esports bots)
ESPORTS_BOT_CAPITAL=5000
ESPORTS_KELLY_FRACTION=0.25
ESPORTS_MAX_BET_USD=100
ESPORTS_MAX_DAILY_USD=500
ESPORTS_MIN_CONFIDENCE=0.52
ESPORTS_MIN_EDGE=0.08
ESPORTS_FRESHNESS_DECAY_SECONDS=30.0
ESPORTS_SERIES_HEDGE_ENABLED=true
BOT_ENABLED_ESPORTS_SERIES=true

# System
SIMULATION_MODE=true
WS_SIGNAL_LATENCY_ALERT_MS=2500
PHASE_MAX_BET_USD=1000
```

---

## INFRASTRUCTURE

- **VPS:** Ubuntu-3, 34.251.224.21, 16GB RAM / 4 vCPU, eu-west-1
- **SSH key:** `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **DB:** PostgreSQL, host=localhost, user=polymarket, db=polymarket, pw=polymarket_s46
- **Redis:** pw=78psiRhepTgrmWSoy3cgNEIr
- **Service:** `sudo systemctl restart polymarket-ai`
- **Logs:** `sudo journalctl -u polymarket-ai -f`
- **VPS structure:** `/opt/polymarket-ai-v2` → symlink to `/opt/pa2-releases/YYYYMMDD_HHMMSS`
- **Shared:** `/opt/pa2-shared/{data,saved_models,venv}`

---

## RECENT GIT HISTORY (last 20 commits)

```
af25abf config(mirror): raise max_daily_usd $3k→$10k
339d8d0 feat(cross-bot): WeatherBot prediction logging + EsportsBot debug logging
259b3f4 fix(mirror): P1-P7 — phantom trade dedup, exposure logging, daily cap $3k
900fcbd fix(weather): 7 silent bugs — monitoring, wind trades, exposure, logging
799b5ac fix(mirror): stop-loss exits use SELL to bypass risk price bounds
46a0f70 fix(weather): position dedup in trade execution, 30-min resolution backfill, reconciler accuracy
71c3ff8 perf(weather_bot): parallelize precip/snow/wind scans + NWS alerts + add phase timing
4516455 fix(engine): suppress RPC 401 noise, stale sync_log, task lifecycle
a311fec fix(esports): bounded cache cleanup prevents unbounded memory growth
aed0ba3 fix(weather): await _save_exit_to_redis — no more fire-and-forget
4af9b5d fix(esports): match.match_id instead of match.get("id") on dataclass
e8f23b2 fix(pandascore): use class-level counter in 429 rate-limit log
5c3c451 fix(reconciler): use ANY(:ids) parameterized binding — no SQL injection
f905fbe feat(reconciler): H2 — schedule position reconcile every 30 min
2b85073 feat(paper): H1 — correlation_id idempotency guard prevents double-fills
8c25779 feat(reconciliation): paper trading position reconciler
862db1a feat(paper): order state machine PENDING→SUBMITTED→FILLED + migration 039
5604f61 feat(ws): REST resync callback after WebSocket reconnect
da2b214 feat(kill_switch): B1 — mark open positions halted on kill switch engage
adfdae4 feat(alerting): daily PnL summary alert via Slack/Discord
```

---

## PLAN STATUS — Cross-Bot Pattern Transfer

### Original Plan (optimized-tumbling-lollipop.md):
| Commit | Feature | Status |
|--------|---------|--------|
| 1 | Prediction Logging | ✅ DEPLOYED |
| 2 | Outcome Backfill | ✅ DEPLOYED |
| 3 | Consecutive Loss Tracking + Kelly Compression | ✅ DEPLOYED |
| 4 | Per-Market-Type Adaptive Parameters | ✅ DEPLOYED |
| 5 | Per-Station Reliability-Weighted Sizing | ✅ DEPLOYED |
| 6 | EMOS Drift Detection | ✅ DEPLOYED |
| 7 | Zombie Position Fix (P0) | ✅ DEPLOYED (v2: 20h + resolved check) |

### Gaps Eliminated (not needed):
- Daily counter write-through — WeatherBot uses paper_trades SUM on startup
- Confluence gate — multi-model ensemble IS the confluence
- Stop-loss exit — `position_manager` already handles via `ExitStrategy`

---

## KNOWN ISSUES / NEXT STEPS

### WeatherBot:
1. **EMOS stations not ready:** NZWN needs +3 rows, RJTT needs +19 rows (~2026-03-15 target)
2. **`bot_category_params` table needs seeding:** Per-type thresholds not yet INSERTed (Tier 1 config)
3. **Precipitation trade volume:** `precip_trades=0` since 429 cooldown. Will recover when cooldowns expire.
4. **P&L bulk resolution:** Check ~2026-03-13 when March 11-12 markets settle
5. **Position lifecycle:** Long-term fix should set `end_date_iso` on weather positions at creation time (in order_gateway or paper_trading) so `_close_expired_positions()` works natively

### EsportsBot:
1. **LoL 0 opportunities:** 143 LoL markets scanned → 0 opportunities consistently. Likely team name extraction failing for LCK/LEC/LPL tournaments (Glicko-2 returns low confidence). NOT yet investigated.
2. **47 YES/NO trades pending resolution** — first real accuracy data when markets settle

### MirrorBot:
1. **Test coverage:** 55% (740 tests added in `65e4946`)
2. **max_daily_usd raised:** $1500 → $10000 (uncommitted in bankroll_manager.py)

### System-wide:
1. **H2 deferred:** PositionReconciler CLOB API reconciliation — meaningless while SIMULATION_MODE=true
2. **H3 deferred:** Redis shared rate limiter — needed before Stage 1 (5% live capital)
3. **CANARY_STAGE gate:** LIVE_READINESS.md created with Stage 0→4 criteria
4. **WebSocket instability:** WS reconnect every ~60-90s with 2s backoff. Informational only.

---

## DEEP-DIVE IMPLEMENTATION HISTORY (Sessions 63-64)

### Tier 1 (COMPLETED):
- E1: Glicko-2 bootstrap from historical data
- W1: NWP model-run timing scans (GFS/ECMWF/HRRR windows)
- W4/E4: Structured monitoring thresholds (MSE/Brier + drawdown alerts)
- E2: Patch-conditioned training (current-patch-only when ≥30 LoL samples)

### Tier 2 (COMPLETED):
- W3+W5: Temperature laddering + Smoczynski-Tomkins multi-bucket Kelly
- E5: Bayesian prior from historical win rates (Glicko-2 phi-based blending)
- W7: Baker-McHale uncertainty-scaled Kelly
- E7: Cross-game XGBoost model (pools all 8 games)

### Tier 3 (COMPLETED):
- W6: Dynamic Kelly graduation (MSE-based auto-scaling)
- W8: CRPS scoring (Ferro 2014 fair CRPS)
- Edge decay analysis in `esports_db.py`

### Tier 4 (BLOCKED/DEFERRED):
- W9: Market-making bot — significant investment, separate project
- W10: Cross-platform weather arb — blocked on Kalshi credentials
- E8: Premium PandaScore — business decision

---

## HOW TO VERIFY THE SYSTEM IS HEALTHY

```bash
# SSH into VPS
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21

# Check all bots scanning
sudo journalctl -u polymarket-ai --since '5 min ago' | grep -E 'scan_done|waterfall'

# WeatherBot specifically
sudo journalctl -u polymarket-ai -f | grep 'weatherbot_scan_done'
# Expect: groups_with_edge > 0, trades > 0

# Check stale position cleanup
sudo journalctl -u polymarket-ai -f | grep 'weatherbot_stale_positions_closed'

# Check open positions
sudo -u postgres psql -d polymarket -c "SELECT COUNT(*), status FROM positions WHERE source_bot='WeatherBot' GROUP BY status;"

# Check P&L
sudo -u postgres psql -d polymarket -c "SELECT source_bot, COUNT(*), SUM(realized_pnl) FROM paper_trades WHERE realized_pnl IS NOT NULL GROUP BY source_bot;"

# Check prediction logging
sudo -u postgres psql -d polymarket -c "SELECT COUNT(*), model_name FROM prediction_log WHERE bot_name='WeatherBot' GROUP BY model_name;"

# Check daily exposure
sudo journalctl -u polymarket-ai -f | grep 'daily_exposure'

# Deploy new changes
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh

# Run tests before deploying
python -m pytest tests/unit/ -q --no-cov  # 1299 tests must pass
```

---

## EXISTING HANDOFF DOCUMENTS (for deep context)

| Document | Scope | Location |
|----------|-------|----------|
| `WEATHERBOT_FULL_AGENT_HANDOFF.md` | Sessions 53-69, complete architecture | repo root |
| `AGENT_HANDOFF_WEATHERBOT_SESSION69_2026_03_09.md` | Boot blocker, schema gaps | repo root |
| `AGENT_HANDOFF_ALL_BOTS_SESSION75_2026_03_10.md` | Session 75 all-bots | repo root |
| `memory/MEMORY.md` | Sessions 72-77 cumulative | memory/ |
| `memory/architecture.md` | System architecture reference | memory/ |
| `memory/deep_dive_implementation.md` | Tier 1-3 implementation details | memory/ |
| `CLAUDE.md` | Development rules (MUST follow) | repo root |
| `LIVE_READINESS.md` | Canary stage gates | repo root |

---

## KEY LESSONS LEARNED THIS SESSION

1. **"Timing gaps" in logs can be misleading** — always check ALL scans, not just ones with trades
2. **Zombie positions are the #1 WeatherBot bottleneck** — positions accumulate because weather markets lack `end_date_iso`
3. **48h stale threshold was too conservative** — weather markets resolve in <24h, so 20h is correct
4. **The in-memory `_open_position_markets` set must be evicted** when positions are closed in DB, otherwise trades remain blocked until next `reconcile_exposure_from_db()` cycle
5. **Cross-bot pattern transfer works** — EsportsBot's drawdown schedule and prediction logging infrastructure mapped cleanly onto WeatherBot with minimal adaptation
6. **Most plan items were already implemented** — always check existing code before writing new code

---

## WHAT THE NEXT AGENT SHOULD DO

1. **Monitor WeatherBot P&L** — check March 13 for bulk resolution of March 11-12 weather markets
2. **Seed `bot_category_params`** — INSERT per-type thresholds for temperature/precipitation/snowfall/wind
3. **Investigate LoL 0 opportunities** — 143 markets scanned, 0 opps. Team name extraction likely failing.
4. **Commit uncommitted changes** — 7 modified files including bankroll_manager.py, esports improvements
5. **Consider reducing stale threshold further** — 20h works but a date-aware approach (parse target date from market question) would be ideal
6. **Watch for EMOS readiness** — NZWN and RJTT stations approaching enough data (~March 15)
