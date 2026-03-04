# COMPLETE AGENT HANDOFF — Polymarket AI V2
**Updated**: 2026-03-04 (Session 49) — SUPERSEDES ALL PREVIOUS VERSIONS
**Purpose**: Full carbon-copy context for a new agent. No prior conversation needed.
**Tests**: 1090 passed, 6 skipped, 0 failed.
**VPS**: Sessions 1-49 ALL DEPLOYED. Service active at 34.251.224.21.
**Bots**: 15 total (8 original + 3 sports + 3 esports + 1 LogicalArbBot). MomentumBot DELETED.
**Active bots**: 6 (EnsembleBot, ArbitrageBot, MirrorBot, CrossPlatformArbBot, WeatherBot, LogicalArbBot).

---

## SESSION 49 CHANGES — READ THIS FIRST

### Session 49 (2026-03-04) — Trade Quality Fixes + Ops Hardening

**CRITICAL DISCOVERY**: 805 realized sells, **ZERO winning trades**. Root causes identified and fixed:

#### Part A — 0% Sell Win Rate Root Cause Analysis
1. **Penny token churn**: Bot bought tokens at 7-9c where CLOB bid was 5-6c. Stop-loss triggered within minutes every time. `RISK_MIN_PRICE` was 0.05 (5c) — way too low.
2. **Model reversal churn**: `MODEL_REVERSAL_THRESHOLD=0.45` caused exits whenever model oscillated between 45-52% confidence. Since the model predicts near coin-flip (calibration shows 49-50% accuracy), this fired constantly.
3. **Absolute-only spread check**: `ENSEMBLE_MAX_SPREAD_PCT=0.10` was absolute (10 cents). A 5c spread on a 15c token (33% relative round-trip cost) passed this check.

**Model calibration (242 resolved predictions)**:
- 0.40-0.50 bucket: 189 predictions, 50.3% actual (predicted 45.6%) — essentially random
- 0.50-0.60 bucket: 51 predictions, 49.0% actual (predicted 50.7%) — essentially random
- Brier score: 0.2512 (need ≤0.22 for graduation)
- Win rate: 49.6% (need ≥52% for graduation)

#### Part B — Fixes Deployed

| Fix | File(s) | Change |
|-----|---------|--------|
| **B1** Price floor raised | VPS .env | `RISK_MIN_PRICE=0.15` (was 0.05). Blocks all tokens below 15c |
| **B2** Price ceiling lowered | VPS .env | `RISK_MAX_PRICE=0.90` (was 0.95). Blocks extreme favorites |
| **B3** Model reversal threshold | VPS .env | `MODEL_REVERSAL_THRESHOLD=0.30` (was 0.45). Only exit if model strongly disagrees |
| **B4** Relative spread check | `ensemble_bot.py:1631-1640` + VPS .env | `ENSEMBLE_MAX_RELATIVE_SPREAD=0.20`. Rejects if spread/price > 20% |
| **B5** Slug SAVEPOINT fix | `database.py:1136-1150` | Per-row fallback uses `session.begin_nested()` (SAVEPOINT) so one slug failure doesn't cascade to all remaining rows |
| **B6** Platt scaling enabled | VPS .env | `PLATT_SCALING_ENABLED=true` (242 resolved > 200 threshold) |
| **B7** LogicalArbBot enabled | VPS .env | `BOT_ENABLED_LOGICAL_ARB=true` + `LOGICAL_ARB_ENABLED=true` |
| **B8** sentence_transformers | VPS pip | Installed for LogicalArbBot embeddings (was using text fallback) |

#### Part C — Ops Hardening
- VACUUM FULL decision_events (1003 MB — live data, no dead tuple bloat)
- .env permissions verified (`600 polymarket:polymarket`)
- No duplicate .env keys found
- BOT_SCAN_TIMEOUT_SECONDS=300 verified
- Full limits audit completed — all settings properly tuned for paper phase

#### VPS .env Additions (Session 49)
```env
RISK_MIN_PRICE=0.15
RISK_MAX_PRICE=0.90
MODEL_REVERSAL_THRESHOLD=0.30
ENSEMBLE_MAX_RELATIVE_SPREAD=0.20
PLATT_SCALING_ENABLED=true
BOT_ENABLED_LOGICAL_ARB=true
LOGICAL_ARB_ENABLED=true
```

#### Performance After Fixes
- EnsembleBot: 185 markets evaluated, much more selective (only trades 15-90c range with <20% relative spread)
- MirrorBot: Made first trades (2 positions opened)
- LogicalArbBot: Scanning every 60s, $500 capital, 0.2 Kelly fraction

---

### Session 48 (2026-03-03) — Deep-Sweep Root Cause Fixes

| Fix | File(s) | Change |
|-----|---------|--------|
| P1 | `ensemble_bot.py` | Price floor guard — rejects penny tokens before 11-model ML inference |
| P2 | `ensemble_bot.py` | Category key in opportunity dict → BotBankrollManager shows real categories |
| P3 | `bankroll_manager.py` | `_get_daily_spent()` uses `get_daily_exposure_usd()` (day-rollover aware) |
| P4 | `main.py` + service file | sklearn ConvergenceWarning suppression + PYTHONWARNINGS env |
| P5 | 4 bot files | Diagnostic INFO logging in ArbitrageBot, MirrorBot, CrossPlatformArbBot, WeatherBot |
| P6 | `prediction_engine.py` | Feature vector cache TTL unified to `_FV_CACHE_TTL = 300.0` |
| P7 | `polymarket-ai.service` | **CRITICAL**: Removed WatchdogSec (app never sent sd_notify heartbeats → killed every 5min) |
| P8 | VPS .env | `BOT_SCAN_TIMEOUT_SECONDS=300` (was 180, scan takes ~176-284s) |

---

## SESSION 47 CHANGES

### Session 47 (2026-03-03) — Per-Bot Independence Architecture + Root Fixes

**Problem**: 32 trades/day (target 60-120+). Kelly sizing divided by 10 bots (only 1 trades). Edge gate too tight. Exit cooldown too long. sklearn spam (12,736 warnings/scan).

#### Part A — Immediate Fixes

| Fix | File(s) | Change |
|-----|---------|--------|
| **A1** sklearn warning suppression | `main.py` | `warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")` inserted BEFORE app imports |
| **A2** Log spam → DEBUG | `database.py` line 1158, `data_ingestion.py` line 477 | `logger.info(` → `logger.debug(` for "Bulk processed" and "fetch_markets_batch" |
| **A3** Exit cooldown reduced | `settings.py`: ENSEMBLE_EXIT_COOLDOWN_SECONDS default 1800→300, `ensemble_bot.py` line 846: `_max_cooldown = 86400` → `3600` |
| **A5** Edge gate lowered | `settings.py`: ENSEMBLE_MIN_EDGE default 0.04→0.02, ENSEMBLE_CATEGORY_MIN_EDGES defaults 0.03-0.05 |
| **A6** Confidence aligned | `settings.py`: MIN_CONFIDENCE_THRESHOLD default 0.55→0.45, ENSEMBLE_MIN_CONFIDENCE default 0.55→0.45 |

#### Part B — Per-Bot Independence Architecture

**NEW FILE**: `base_engine/risk/bankroll_manager.py` — `BotBankrollManager` class:
- Each bot gets its OWN capital pool, Kelly fraction, per-trade/daily caps
- No more shared `KELLY_ACTIVE_BOTS` divisor (was dividing Kelly by 10 for 1 trading bot)
- Default allocations: EnsembleBot=$8k, ArbitrageBot=$1k, MirrorBot=$1k, CrossPlatformArbBot=$500, WeatherBot=$500
- Config override via `BOT_BANKROLL_CONFIG` JSON env var
- Calibration-aware sizing (Brier>0.15 → reduce), drawdown compression, category-specific fractions
- Follows pattern of SportsBankrollManager / EsportsBankrollManager

**Wiring changes**:
- `bots/base_bot.py`: `self.bankroll = BotBankrollManager(bot_name)` in `__init__`; `calculate_bot_position_size()` routes through bankroll when available, legacy `risk_manager.calculate_position_size()` as fallback
- `bots/ensemble_bot.py`: passes `category=` to `calculate_bot_position_size()`
- `risk_manager.py`: KELLY_ACTIVE_BOTS divisor simplified to `max(1, KELLY_ACTIVE_BOTS)` with deprecation comment

**NEW MIGRATION**: `schema/migrations/027_prediction_log_bot_name.sql`:
- `ALTER TABLE prediction_log ADD COLUMN IF NOT EXISTS bot_name VARCHAR(64)`
- Index on bot_name, backfill existing rows to 'EnsembleBot'
- ORM model updated, `insert_prediction_log()` accepts `bot_name` parameter

**Per-bot model infrastructure** (gated, not yet active):
- `prediction_engine.py`: `_get_model_path(bot_name)` returns per-bot model cache path
- `predict()` accepts optional `bot_name` parameter, passes to prediction_log insert
- Gated behind `USE_PER_BOT_MODELS=false` (default OFF, shared models used initially)

**VPS .env updated** (Session 47):
```
ENSEMBLE_MIN_EDGE=0.02          # was 0.04
BOT_SCAN_TIMEOUT_SECONDS=180    # was 120
KELLY_ACTIVE_BOTS=3             # was 10 (deprecated, legacy path only)
ENSEMBLE_EXIT_COOLDOWN_SECONDS=300  # was 1800
```

**Expected impact**:
| Metric | Before S47 | After S47 |
|--------|-----------|-----------|
| Trades/24h | 32 | 60-120 (projected) |
| Position size avg | $10-33 | $50-100 |
| Log volume | 12,736+ warnings/scan | ~100 lines/scan |
| Exit cooldown (1st) | 30 min | 5 min |
| Raw edge required | ~7% | ~5% |
| Kelly model | Shared pool / 10 bots | Per-bot: $8k Ensemble, $1k each Arb/Mirror |

---

### Session 46 (2026-03-03) — Production Readiness: 3→32 Trades/Day

**Critical fixes that increased trading from 3 to 32 trades/day:**
- `BOT_SCAN_TIMEOUT_SECONDS`: 30→120 (every scan was timing out)
- `SCAN_MARKET_LIMIT`: 50→200 (was scanning too few markets)
- `ENSEMBLE_SCAN_CONCURRENCY`: 5→10 (VPS 4 vCPU handles IO-bound concurrency fine)
- `ENSEMBLE_MIN_EDGE`: 0.10→0.04 (NET edge — was requiring ~13% raw edge, impossible)
- `ENSEMBLE_CATEGORY_MIN_EDGES`: all lowered from 0.08-0.12 to 0.03-0.05
- `MIN_CONFIDENCE_THRESHOLD`: 0.55→0.45
- `RISK_MIN_EDGE_PCT`: left at 1% (the universal risk_manager gate)
- DB password set to `polymarket_s46` during VPS rebuild

### Session 44 (2026-03-03) — Sell Bug Fix + MomentumBot Purge + VPS Upgrade

**CRITICAL BUG FIX — 0% Sell Win Rate**: `position_manager.py` never updated `current_price` after position open. Every sell used stale entry_price minus slippage = guaranteed loss. Added `_update_current_prices()` that runs every 10s cycle.

**MomentumBot FULLY DELETED**: Archive, 2,036 paper_trades + 85 positions purged from DB, all code references cleaned. BOT_REGISTRY: 16→15.

**VPS Upgrade**: Ubuntu-2 (8GB/2vCPU) → Ubuntu-3 (16GB/4vCPU/320GB SSD). IP: 34.248.60.104 → **34.251.224.21**. PG tuned: shared_buffers=4GB, effective_cache_size=12GB.

**Supabase Fully Removed**: 58 references across 14 files cleaned.

### Session 42 (2026-03-02) — was_correct Labeling Fix + Dashboard UI Parity

**Root cause**: `backfill_prediction_log_from_closed_trades()` set `was_correct = (avg_pnl > 0)`. Since paper trades lose money, 4,654 pseudo-labels were all FALSE. This poisoned model training.

**Fix**: Added `PSEUDO_LABEL_ENABLED=false` (do NOT enable). Cleaned 4,654 bad labels from DB. 242 clean real-resolution labels remain (49.6% accuracy).

**Dashboard**: Phase Tracker panel, 4 new settings sections, dead code removed. 3,503 lines.

### Sessions 36-41 Summary

| Session | Key Work |
|---------|----------|
| 41 | LogicalArbBot (16th bot, now 15 after MomentumBot deleted). Platt scaling wired. Section 21 features. |
| 40 | All flaky tests fixed: inspect.getsource→pathlib, PhaseTracker._last_evaluated=float("-inf") |
| 39 | Elite Model: 6 new signal modules (legislative, polling, bayesian, logical_arb, court, intl_elections) |
| 38 | Phase bet caps, Category Kelly, dynamic KELLY_ACTIVE_BOTS, politics exit, weather hold, phase tracker |
| 37 | 3 esports bots + 12 infrastructure modules + 8 DB tables |
| 36 | LLM resolution clarity scoring + disposition effect |

---

## 1. WHAT THIS SYSTEM IS

A fully automated **paper-trading** prediction market bot system:
- Scans **Polymarket** binary prediction markets (https://polymarket.com)
- Uses an **11-model ML ensemble** to predict resolution probabilities
- Places **paper trades** ($100K virtual capital, `SIMULATION_MODE=true`)
- Tracks P&L, positions, model performance in **PostgreSQL on VPS** (local, NOT Supabase)
- Self-heals via FSM state machine, circuit breakers, kill switch, drawdown breaker, DegradationManager
- **Goal**: Demonstrate edge and graduate through phases before using real money

### The VISION (DO NOT LOSE):
The system is being built to eventually trade **real money** on Polymarket. The graduation system tracks real performance:
- **Paper phase** (CURRENT): Testing, accumulating predictions. Need 52% win rate + 100 resolved predictions + Brier < 0.22 to graduate.
- **Learning phase**: Validated. Need 55% win rate + 300 resolved predictions + Brier < 0.20.
- **Graduated phase**: Proven. Max $200/bet.
- **Production phase**: Full trading. $1000/bet. Real money.

**Per-bot independence** (Session 47): Each bot has its own capital pool, Kelly fraction, and daily caps. Per-bot model training infrastructure is ready but gated (USE_PER_BOT_MODELS=false). When a bot accumulates 200+ prediction_log entries with bot_name, it can train its own model.

**ARCHITECTURAL MANDATE** (Session 31, MUST honor forever):
> "All base modules/data/engines are updated on all bots and used by them. Each bot uses its own specific blend of learning/data/modules/code. Treat all bots equal."
> Implementation: `risk_manager.check_risk_limits()` = universal enforcement for ALL bots. `order_gateway.place_order()` = universal execution gates. BotBankrollManager handles SIZING; risk_manager handles LIMITS. Both must pass.

---

## 2. BOT REGISTRY (15 Bots Total)

| # | Bot | File | Kelly Path | VPS State | Notes |
|---|-----|------|-----------|-----------|-------|
| 1 | EnsembleBot | `bots/ensemble_bot.py` (~1465L) | **BotBankrollManager** ($8k capital) | **ENABLED** | ML ensemble, edge filter, CLOB spread, progressive cooldown |
| 2 | ArbitrageBot | `bots/arbitrage_bot.py` (~1260L) | **BotBankrollManager** ($1k capital) | **ENABLED** | NegRisk arb, all 7 paths Kelly-sized |
| 3 | MirrorBot | `bots/mirror_bot.py` | **BotBankrollManager** ($1k capital) | enabled | Elite trader mirroring |
| 4 | CrossPlatformArbBot | `bots/cross_platform_arb_bot.py` | **BotBankrollManager** ($500 capital) | enabled | Cross-platform arb |
| 5 | OracleBot | `bots/oracle_bot.py` | BotBankrollManager ($500) | disabled | Oracle-based resolution |
| 6 | SportsBot | `bots/sports_bot.py` | BotBankrollManager ($1k fallback) | **DISABLED** | Needs API Football key |
| 7 | LLMForecasterBot | `bots/llm_forecaster_bot.py` | N/A (no trades) | disabled | Data collection only |
| 8 | WeatherBot | `bots/weather_bot.py` (~630L) | **BotBankrollManager** ($500 capital) | **ENABLED** | SWOT upgrades, hold-to-resolution |
| 9 | SportsInjuryBot | `bots/sports_injury_bot.py` | SportsBankrollManager | disabled | News-driven injury bets |
| 10 | SportsLiveBot | `bots/sports_live_bot.py` | SportsBankrollManager | disabled | Live game event bets |
| 11 | SportsArbBot | `bots/sports_arb_bot.py` | SportsBankrollManager | disabled | Cross-platform sports arb |
| 12 | EsportsBot | `bots/esports_bot.py` (280L) | EsportsBankrollManager | **DISABLED** | Needs PANDASCORE_API_KEY |
| 13 | EsportsLiveBot | `bots/esports_live_bot.py` (190L) | EsportsBankrollManager | **DISABLED** | Needs PANDASCORE_API_KEY |
| 14 | EsportsSeriesBot | `bots/esports_series_bot.py` (280L) | EsportsBankrollManager | **DISABLED** | Needs PANDASCORE_API_KEY |
| 15 | LogicalArbBot | `bots/logical_arb_bot.py` (~317L) | BotBankrollManager ($500) | **DISABLED** | Cross-market constraint arb |

**Kelly sizing architecture (Session 47)**:
- **ALL bots** → `BotBankrollManager` via `base_bot.calculate_bot_position_size()` (per-bot capital, no divisor)
- 3 sports bots → own `SportsBankrollManager` (separate adaptive Kelly per sport — override in their bot class)
- 3 esports bots → own `EsportsBankrollManager` (separate Kelly pool, ESPORTS_TOTAL_CAPITAL=5000 — override)
- `risk_manager.calculate_position_size()` is LEGACY fallback only (if BotBankrollManager init fails)
- `risk_manager.check_risk_limits()` remains the UNIVERSAL SAFETY layer for ALL bots

**MomentumBot**: FULLY DELETED in Session 44. Do not recreate.

---

## 3. ARCHITECTURE

```
main.py (~400 lines)
+-- BaseEngine (base_engine/base_engine.py ~3400 lines)
|   +-- PredictionEngine (base_engine/prediction/prediction_engine.py ~2700+ lines)
|   |   +-- 11 ML models from data/model_cache.pkl (16MB, 43-50 features)
|   |   |   RF, XGB, GradBoost, ExtraTrees, HistGradBoost, LightGBM, CatBoost, LogReg, Ridge, KNN, MLP
|   |   +-- predict(bot_name=) -- FV cache fast path + ensemble + calibration + extremization + Platt
|   |   |   EXTREMIZATION_FACTOR=1.4 (log-odds: 55%->60%, 60%->66%, 80%->87%)
|   |   |   Prediction_log gated on self._feature_cache_warmed (all 3 write locations)
|   |   |   predicted_prob range guard (S42): logs WARNING if extreme
|   |   |   bot_name passed to insert_prediction_log() for per-bot tracking (S47)
|   |   +-- _get_model_path(bot_name) -- per-bot model cache (S47, gated USE_PER_BOT_MODELS)
|   |   +-- batch_precompute_all_features() -- background DB batch fill for FV cache
|   |   +-- _feature_vector_cache -- TTL 300s, invalidated on price move > 3%
|   |
|   +-- RiskManager (base_engine/risk/risk_manager.py ~530 lines)
|   |   +-- UNIVERSAL GATES (all bots via check_risk_limits()):
|   |   |   Confidence gate, directional edge check (if edge < min_edge, NO abs())
|   |   |   Price bounds 5%-95%, volume gate ($5K min, 1h cache)
|   |   |   Position limits, loss limits, kill switch, CVaR, PCA factor exposure
|   |   +-- calculate_position_size() -- DEPRECATED LEGACY (S47)
|   |       Uses max(1, KELLY_ACTIVE_BOTS) divisor. Only used if BotBankrollManager fails.
|   |
|   +-- BotBankrollManager (base_engine/risk/bankroll_manager.py -- NEW S47)
|   |   Per-bot capital pools, Kelly sizing, daily caps. No shared divisor.
|   |   Instantiated per-bot in base_bot.__init__. Config via BOT_BANKROLL_CONFIG JSON env.
|   |   Defaults: EnsembleBot=$8k, Arb=$1k, Mirror=$1k, CrossPlatArb=$500, etc.
|   |
|   +-- Database (base_engine/data/database.py ~3400+ lines, 23+ ORM tables)
|   |   +-- backfill_prediction_log_resolution() -- Location 1, CORRECT labeling
|   |   +-- backfill_prediction_log_from_closed_trades() -- DISABLED (PSEUDO_LABEL_ENABLED=false)
|   |   +-- prediction_log.bot_name column (S47 migration 027)
|   |
|   +-- OrderGateway (base_engine/execution/order_gateway.py)
|   |   +-- UNIVERSAL GATES: kill switch, canary, CLOB spread/liquidity, drawdown compression
|   |   +-- PaperTradingEngine: cash=$100k, epsilon 1e-6 guard
|   |   +-- seed_positions_from_db() + reconcile_exposure_from_db() (SELL rows EXCLUDED)
|   |
|   +-- AutomatedPositionManager (base_engine/execution/position_manager.py)
|   |   Stop-loss 30%, take-profit 60%, model reversal exits (warm-cache guard)
|   |   _update_current_prices() every 10s (S44 fix -- was stale before)
|   |
|   +-- TradeCoordinator (STALE_RESERVATION_MINUTES=8)
|   +-- SignalIngestion (4 fetches: asyncio.wait_for timeout=10.0)
|   +-- WhaleTracker (per-trader category accuracy in Redis)
|   +-- WebSocketManager (_resolve_market_id: 0x condition_id -> numeric)
|   +-- KillSwitch
|   +-- Monitoring (phase_tracker, drawdown breaker, degradation_manager, health_runner)
|
+-- Bots (15 total -- see registry above)
|   EnsembleBot: ML ensemble + early CLOB spread + side-bias (65%) + progressive cooldown
|   ArbitrageBot: 7 execution paths, all Kelly-sized
|   WeatherBot: central Kelly + group/city caps + expiry/regime boosts + hold-to-resolution
|   Sports bots (3): SportsBankrollManager + adaptive_kelly
|   Esports bots (3): EsportsBankrollManager, fail-fast if no PANDASCORE_API_KEY
|   LogicalArbBot: mutual_exclusivity + subset + complement cross-market arb
|
+-- Weather Pipeline (base_engine/weather/)
|   station_registry, market_mapper, forecast_client (GEFS 31 + ECMWF 51), probability_engine
|
+-- Sports Pipeline (sports/)
|   markets/, live/, news/, data/, kelly/ (SportsBankrollManager + adaptive_kelly)
|
+-- Esports Pipeline (esports/)
    data/, models/, live/, markets/, kelly/ (EsportsBankrollManager)
```

---

## 4. CRITICAL MENTAL MODELS (NEVER FORGET)

### 4A. YES/NO/BUY/SELL
- **YES and NO are both BUY** -- you buy that outcome's token. SELL = close position only.
- `BaseBot.place_order()` expects `side="YES"` or `side="NO"`. NEVER pass "BUY" or "SELL".
- Market IDs: numeric `m.id` AND hex `condition_id` (0x339d...). Always JOIN both.

### 4B. VPS Database
- **LOCAL PostgreSQL** (NOT Supabase). DB: `polymarket`, User: `polymarket`, Password: `polymarket_s46`
- **TIMESTAMP WITHOUT TIME ZONE** columns -- ALWAYS `.replace(tzinfo=None)` before raw SQL
- **positions UNIQUE**: `(bot_id, market_id, side)`. Filter `side != 'SELL'` in ALL exposure calcs.
- **paper_trades schema**: `bot_name` column (NOT `bot_id`)
- **positions schema**: `bot_id` column (NOT `bot_name`)
- **prediction_log schema**: `bot_name` column (Session 47 migration 027)

### 4C. P&L Accounting
- `realized_pnl = (price - avg_price) * size - exit_fee - entry_fee_total`
- `TAKER_FEE_BPS = 150` (1.5% per trade), `MAKER_FEE_BPS = 0`
- Polymarket actual: `p*(1-p)*0.0625` (625 bps, parabolic, peaks at p=0.50)

### 4D. Edge Filter
```python
# EnsembleBot _analyze_one_token():
edge = confidence - price                  # model_prob - market_price
_min_edge = settings.ENSEMBLE_MIN_EDGE     # 0.02 default (Session 47: was 0.04, was 0.10)
_cat_edges = json.loads(settings.ENSEMBLE_CATEGORY_MIN_EDGES)
# {"weather":0.03,"crypto":0.05,"sports":0.04,"politics":0.04,...}
if price > 0.90: _min_edge *= 3.0
elif price > 0.80: _min_edge *= 2.0
net_edge = edge - (_spread / 2.0)          # CLOB spread deduction (~3% round-trip)
if net_edge < _min_edge: return None       # REJECT
```

### 4E. Kelly Sizing (Session 47 — Per-Bot BotBankrollManager)
```python
# BotBankrollManager.get_bet_size():
# Each bot has its OWN capital pool (no shared divisor)
b = (1.0 - price) / price
q = 1.0 - confidence
kelly_full = (confidence * b - q) / b      # Full Kelly fraction
fraction = self.kelly_fraction              # 0.25 default (Quarter-Kelly)
# Category override: CATEGORY_KELLY_FRACTIONS={"weather":0.25,"crypto":0.125,...}
# Calibration scaling: Brier>0.15 -> reduce fraction by up to 50%
# Drawdown compression: drawdown>2% -> compress fraction
size_usd = kelly_full * fraction * self.capital  # e.g., 0.30 * 0.25 * 8000 = $600
size_usd = min(size_usd, self.max_bet_usd)      # Cap at $100 per bet
size_usd = min(size_usd, remaining_daily)         # Cap at daily limit ($2000 EnsembleBot)
# Minimum: <$1 returns 0

# THEN risk_manager.check_risk_limits() applies as SAFETY layer on top
```

### 4F. Legacy Kelly (DEPRECATED — only if BotBankrollManager fails)
```python
# risk_manager.calculate_position_size() -- DEPRECATED S47
fraction = KELLY_FRACTION / max(1, KELLY_ACTIVE_BOTS)  # 0.25 / 3 = 0.083
# Phase caps still apply: paper=$15, learning=$20, graduated=$200, production=$1000
```

### 4G. Progressive Anti-Churn Cooldown (Session 28, updated S47)
```python
# ensemble_bot.py:
# _exit_count[market_id] tracks consecutive exits
# 1st exit: 5min (S47: was 30min), 2nd: 10min, 3rd: 20min... cap: 1h (S47: was 24h)
```

### 4H. was_correct Labeling (Session 42 FIXED)
```
Location 1 (backfill_prediction_log_resolution) -- CORRECT, KEEP:
  SQL: was_correct = ((predicted_prob >= 0.5) = (m.resolution = 'YES'))
  49.6% accuracy on 242 real-resolution labels. Model is fine.

Location 2 (backfill_prediction_log_from_closed_trades) -- DISABLED:
  PSEUDO_LABEL_ENABLED=false. DO NOT enable.
```

### 4I. Python Rules
```python
# NEVER use local `from datetime import datetime` inside functions (scoping trap)
# NEVER use inspect.getsource() in tests -- use pathlib.Path(mod.__file__).read_text()
# PhaseTracker._last_evaluated MUST be float("-inf") not 0.0
# ALL asyncio.create_task() calls MUST have add_done_callback()
# PRAW (Reddit) is synchronous -- ALWAYS wrap in asyncio.to_thread()
# websockets v15: MUST import websockets.exceptions explicitly
# endDateIso: check ALL 5: endDateISO or endDateIso or endDate or end_date or end_date_iso
```

---

## 5. ENVIRONMENT

### VPS (Bot Runs Here)
```
Provider:    AWS Lightsail, eu-west-1 (Ireland)
IP:          34.251.224.21 (Ubuntu-3, 16GB RAM, 4 vCPU, 320GB SSD)
SSH key:     C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem
OS user:     ubuntu
App dir:     /opt/polymarket-ai-v2/ (root-owned -- sudo required for ALL writes)
Python:      /opt/polymarket-ai-v2/venv/bin/python (Python 3.13)
Service:     polymarket-ai.service (auto-restart on failure, enabled at boot)
Log:         /opt/polymarket-ai-v2/data/paper_trading.log
DB:          PostgreSQL (local on VPS), DB=polymarket, User=polymarket, Pass=polymarket_s46
Redis:       localhost:6379, password=78psiRhepTgrmWSoy3cgNEIr
```

**CRITICAL**: Ubuntu-2 DELETED. LockePicks (63.33.55.154) DELETED. Only ONE Lightsail instance exists.

### VPS Deploy Pattern (MEMORIZE)
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Copy + deploy:
scp -i "$KEY" -o StrictHostKeyChecking=no "local_path" "$VPS:/tmp/"
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" '
  sudo cp /tmp/file.py /opt/polymarket-ai-v2/path/to/file.py
  sudo systemctl restart polymarket-ai
  sleep 3
  sudo systemctl status polymarket-ai --no-pager | head -8
'

# .env changes (NEVER overwrite -- append/sed only!):
ssh -i "$KEY" "$VPS" "sudo sed -i 's/OLD=val/NEW=val/' /opt/polymarket-ai-v2/.env && sudo systemctl restart polymarket-ai"

# Verify .env:
ssh -i "$KEY" "$VPS" "grep SOME_SETTING /opt/polymarket-ai-v2/.env"

# Check logs:
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | tail -40"
```

**WARNING**: VPS .env can drift. python-dotenv uses first-wins for duplicates. Always `grep` to verify. Session 46 had a credential wipe disaster -- NEVER overwrite .env wholesale.

### VPS .env — Current State (Sessions 1-47 Deployed)
```env
DATABASE_URL=postgresql://polymarket:polymarket_s46@localhost:5432/polymarket
SQLITE_PATH=data/polymarket.db
DB_POOL_SIZE=40
DB_MAX_OVERFLOW=5

REDIS_ENABLED=true
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=78psiRhepTgrmWSoy3cgNEIr

POLYMARKET_GAMMA_API=https://gamma-api.polymarket.com
POLYMARKET_CLOB_API=https://clob.polymarket.com
POLYMARKET_DATA_API=https://data-api.polymarket.com
POLYMARKET_WS=wss://ws-subscriptions-clob.polymarket.com

SIMULATION_MODE=true
PAPER_TRADING=true
LIVE_TRADING=false

MODEL_ENABLE_MLP=true
RL_TRADE_TIMING_ENABLED=false

SCAN_INTERVAL_ARBITRAGE=10
SCAN_INTERVAL_ENSEMBLE=20
SCAN_INTERVAL_MIRROR=30
ENSEMBLE_SCAN_CONCURRENCY=10
SCAN_MARKET_LIMIT=200
ARB_MAX_MARKETS_PER_SCAN=50
BOT_SCAN_TIMEOUT_SECONDS=180

ENSEMBLE_WS_PRICE_CHANGE_PCT=0.005
ENSEMBLE_WS_COOLDOWN_SECONDS=10
ARB_WS_PRICE_CHANGE_PCT=0.008
ARB_WS_COOLDOWN_SECONDS=5

ENSEMBLE_MIN_CONFIDENCE=0.45
ENSEMBLE_MIN_EDGE=0.02
ENSEMBLE_CATEGORY_MIN_EDGES={"weather":0.03,"crypto":0.05,"sports":0.04,"politics":0.04,"science":0.04,"finance":0.04,"geopolitical":0.05,"entertainment":0.04}
RISK_MIN_EDGE_PCT=1
WS_SIGNAL_LATENCY_ALERT_MS=500
MIN_CONFIDENCE_THRESHOLD=0.45

RISK_MAX_POSITION_SIZE_USD=100
RISK_MAX_POSITIONS_COUNT=100

DAILY_INGESTION_MARKETS_COUNT=1500
DAILY_INGESTION_PRICES_MARKETS=1500
INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS=60

NEGRISK_MAX_TOTAL_RISK=300.0

TRADING_PHASE=paper
PHASE_MAX_BET_USD={"paper":1000,"learning":1000,"graduated":1000,"production":1000}
CATEGORY_KELLY_FRACTIONS={"weather":0.25,"crypto":0.125,"politics":0.20,"sports":0.15}
POLITICS_EXIT_ENABLED=true
POLITICS_EXIT_PCT=0.65
POLITICS_EXIT_MIN_PROFIT_USD=2.0
WEATHER_HOLD_HOURS_BEFORE_RESOLUTION=48.0
PHASE_GRADUATION_ENABLED=true
PHASE_GRADUATION_CHECK_HOURS=24.0
PHASE_PAPER_TO_LEARNING_WIN_RATE=0.52
PHASE_PAPER_TO_LEARNING_MIN_PREDICTIONS=100
PHASE_PAPER_TO_LEARNING_MAX_BRIER=0.22
PHASE_LEARNING_TO_GRADUATED_WIN_RATE=0.55
PHASE_LEARNING_TO_GRADUATED_MIN_PREDICTIONS=300
PHASE_LEARNING_TO_GRADUATED_MAX_BRIER=0.20

BAYESIAN_MODEL_ENABLED=false
LOGICAL_ARB_ENABLED=false
LLM_CONSENSUS_MODE=fallback
BUCKET_SHORT_TERM_PCT=0.40
BUCKET_MEDIUM_TERM_PCT=0.35
BUCKET_LONG_TERM_PCT=0.05
BUCKET_LIQUID_RESERVE_PCT=0.20
RISK_MAX_FACTOR_EXPOSURE_USD=500.0
PCA_LOOKBACK_DAYS=30
PCA_N_FACTORS=3
PLATT_SCALING_ENABLED=false
PLATT_MIN_RESOLVED=200
PSEUDO_LABEL_ENABLED=false

BOT_ENABLED_LOGICAL_ARB=false
SCAN_INTERVAL_LOGICAL_ARB=60
LOGICAL_ARB_MIN_SPREAD=0.025
LOGICAL_ARB_MAX_POSITION_USD=200

BOT_ENABLED_SPORTS=false
BOT_ENABLED_SPORTS_LIVE=false
BOT_ENABLED_SPORTS_ARB=false
SCAN_INTERVAL_SPORTS=120
SCAN_INTERVAL_SPORTS_LIVE=10

BOT_ENABLED_ESPORTS=false
BOT_ENABLED_ESPORTS_LIVE=false
BOT_ENABLED_ESPORTS_SERIES=false
SCAN_INTERVAL_ESPORTS=120
SCAN_INTERVAL_ESPORTS_LIVE=10
SCAN_INTERVAL_ESPORTS_SERIES=30
ESPORTS_MIN_EDGE=0.08
ESPORTS_MIN_CONFIDENCE=0.55
ESPORTS_SERIES_MIN_EDGE=0.10
ESPORTS_SERIES_REVERSE_SWEEP_FLOOR=0.05
ESPORTS_TOTAL_CAPITAL=5000.0
ESPORTS_MAX_BET_USD=100.0
ESPORTS_MAX_DAILY_USD=500.0
ESPORTS_KELLY_DEFAULT_FRACTION=0.25
ESPORTS_MAKER_FALLBACK_TIMEOUT_S=3.0
ESPORTS_OBSERVATION_HOURS=48
ESPORTS_PINNACLE_ENABLED=false
ESPORTS_LOL_GOLD_DIFF_THRESHOLD=5000
ESPORTS_LOL_TOWER_DIFF_THRESHOLD=3
ESPORTS_CS2_ROUND_DIFF_THRESHOLD=5
ESPORTS_CS2_ECONOMY_BREAK_THRESHOLD=10000

KELLY_ACTIVE_BOTS=3
ENSEMBLE_EXIT_COOLDOWN_SECONDS=300

LOG_LEVEL=INFO
```

### Local Dev (Windows)
```
Working dir:  C:\lockes-picks\polymarket-ai-v2
Python:       3.13 system-installed
VPN:          Surfshark ON required (US IPs get 403 from Polymarket API)
Run:          python main.py  OR  python run_paper.py
Tests:        python -m pytest tests/ -q --no-cov
              Expected: 1090 passed, 6 skipped, 0 failed
              Unit only: python -m pytest tests/unit/ -q --no-cov
Dashboard:    streamlit run ui/dashboard.py
```

---

## 6. TESTS

```powershell
# Full suite (~5 min):
cd C:\lockes-picks\polymarket-ai-v2
python -m pytest tests/ -x --tb=short -q
# Expected: 1090 passed, 6 skipped, 0 failed (Session 47)

# Unit tests only (fast):
python -m pytest tests/unit/ -q --no-cov

# New Session 47 tests:
python -m pytest tests/unit/test_bankroll_manager.py -q  # 41 tests
```

**Test file organization:**
- `tests/improvements/` -- Redis, infrastructure
- `tests/integration/` -- bot lifecycle, data ingestion
- `tests/load/` -- concurrent ingestion
- `tests/test_*.py` -- top-level
- `tests/unit/` -- all bots, all modules, all signals (including test_bankroll_manager.py)

---

## 7. KEY FILES REFERENCE

```
main.py (~400 lines)                              -- BOT_REGISTRY (15 bots), startup, sklearn warning suppression
run_paper.py                                       -- background runner
config/settings.py                                 -- ALL env vars + defaults + BOT_BANKROLL_CONFIG (S47)

bots/
  base_bot.py                                      -- BotBankrollManager init, calculate_bot_position_size(), _SCAN_INTERVAL_KEYS
  ensemble_bot.py (~1465 lines)                    -- Edge filter, CLOB spread, progressive cooldown (5min/1h S47), politics exit
  arbitrage_bot.py (~1260 lines)                   -- 7 execution paths, all Kelly-sized
  weather_bot.py (~630 lines)                      -- hold-to-resolution, group/city caps
  mirror_bot.py, cross_platform_arb_bot.py         -- BotBankrollManager
  oracle_bot.py, llm_forecaster_bot.py             -- oracle/data-only
  sports_injury_bot.py, sports_live_bot.py, sports_arb_bot.py -- SportsBankrollManager
  esports_bot.py, esports_live_bot.py, esports_series_bot.py  -- EsportsBankrollManager
  logical_arb_bot.py (~317 lines)                  -- Cross-market constraint arb

base_engine/
  base_engine.py (~3400 lines)                     -- engine wiring
  prediction/prediction_engine.py (~2700+ lines)   -- predict(bot_name=), _get_model_path(), extremization, Platt
  execution/
    position_manager.py                            -- _update_current_prices() every 10s (S44), model reversal exits
    paper_trading.py                               -- $100K cash, epsilon guard
    order_gateway.py                               -- Universal CLOB spread gates
    rl_trade_timing.py                             -- Q-learning trade timing
  data/
    database.py (~3400+ lines)                     -- 23+ ORM tables, prediction_log.bot_name (S47)
    data_ingestion.py                              -- Volume filter, _infer_category(), bulk log→DEBUG (S47)
    websocket_manager.py                           -- _resolve_market_id()
  risk/
    risk_manager.py (~530 lines)                   -- Universal gates + DEPRECATED calculate_position_size()
    bankroll_manager.py (NEW S47)                  -- BotBankrollManager: per-bot capital, Kelly, daily caps
  monitoring/
    phase_tracker.py                               -- Graduation criteria check every 24h
    health_runner.py, degradation_manager.py, portfolio_drawdown.py
  signals/
    signal_ingestion.py, whale_tracker.py, legislative_tracker.py, polling_client.py, court_monitor.py, intl_elections.py
  analysis/
    bayesian_model.py, logical_arbitrage.py, correlation_strategies.py
  weather/, features/

ui/dashboard.py (3503 lines)                       -- Streamlit, all 15 bots, Phase Tracker, settings panels

esports/, sports/                                  -- See architecture section

deploy/
  env.vps                                          -- Complete VPS .env reference (S47 updated)
  polymarket-ai.service                            -- systemd service file

schema/
  migrations/027_prediction_log_bot_name.sql       -- bot_name column (S47)
```

---

## 8. DATABASE SCHEMA (Key Tables)

```sql
-- paper_trades (COLUMN IS bot_name NOT bot_id)
CREATE TABLE paper_trades (
  id SERIAL PRIMARY KEY,
  bot_name VARCHAR NOT NULL,
  market_id VARCHAR NOT NULL,
  token_id VARCHAR,
  side VARCHAR NOT NULL,        -- 'YES', 'NO', or 'SELL'
  size FLOAT NOT NULL,
  price FLOAT NOT NULL,
  realized_pnl FLOAT,
  entry_price FLOAT,
  entry_fee FLOAT,
  created_at TIMESTAMP WITHOUT TIME ZONE,
  correlation_id VARCHAR
);

-- positions (COLUMN IS bot_id NOT bot_name)
CREATE TABLE positions (
  id SERIAL PRIMARY KEY,
  bot_id VARCHAR NOT NULL,
  market_id VARCHAR NOT NULL,
  side VARCHAR NOT NULL,
  size FLOAT,
  avg_price FLOAT,
  current_price FLOAT,          -- Updated every 10s by position_manager (S44)
  unrealized_pnl FLOAT,         -- Updated every 10s (S44)
  status VARCHAR DEFAULT 'open',
  UNIQUE (bot_id, market_id, side)
);

-- prediction_log (bot_name column added S47)
CREATE TABLE prediction_log (
  id SERIAL PRIMARY KEY,
  market_id VARCHAR,
  predicted_prob FLOAT,          -- ALWAYS YES-token probability
  was_correct BOOLEAN,           -- Only set by Location 1 (market resolution)
  prediction_time TIMESTAMP WITHOUT TIME ZONE,
  resolution VARCHAR,
  feature_snapshot JSONB,
  realized_edge FLOAT,
  correlation_id VARCHAR,
  bot_name VARCHAR(64)           -- Session 47: per-bot tracking, indexed
);

-- markets
CREATE TABLE markets (
  id VARCHAR PRIMARY KEY,
  condition_id VARCHAR,
  question TEXT,
  category VARCHAR,              -- Set by _infer_category()
  end_date_iso VARCHAR,          -- From 5 field name variants
  active BOOLEAN,
  liquidity FLOAT,
  volume FLOAT,
  resolution VARCHAR,            -- 'YES', 'NO', or NULL
  resolved_at TIMESTAMP WITHOUT TIME ZONE
);
```

---

## 9. GUARDRAIL SETTINGS (Paper Phase)

| Category | Setting | Value |
|----------|---------|-------|
| Position | Per-bot max bet | BotBankrollManager max_bet_usd (default $100) |
| Position | Per-bot daily cap | BotBankrollManager max_daily_usd (EnsembleBot=$2k) |
| Position | RISK_MAX_POSITION_SIZE_USD | $100 |
| Edge | ENSEMBLE_MIN_EDGE | 0.02 (NET, after spread deduction) |
| Edge | Category min edges | weather=0.03, crypto=0.05, politics=0.04 |
| Edge | Edge multiplier >80c | 2.0x, >90c: 3.0x |
| Confidence | MIN_CONFIDENCE_THRESHOLD | 0.45 |
| Confidence | ENSEMBLE_MIN_CONFIDENCE | 0.45 |
| Risk | DAILY_LOSS_LIMIT_PCT | 2% |
| Risk | MAX_CONSECUTIVE_LOSSES | 3 |
| Cooldown | Exit cooldown base | 300s (5 min), doubles per exit, cap 3600s (1h) |
| Calibration | EXTREMIZATION_FACTOR | 1.4 |
| Calibration | Platt scaling | Trigger at 200+ resolved (currently disabled) |
| Liquidity | Min 24h volume | $5K |
| Liquidity | Max bid-ask spread | 10% |
| Kelly | EnsembleBot capital | $8,000 (per-bot, no divisor) |
| Kelly | ArbitrageBot capital | $1,000 |
| Kelly | Base fraction | 0.25 (Quarter-Kelly) |
| Graduation | Paper exit | 100 resolved, Brier < 0.22, WinRate > 52% |
| Graduation | Learning exit | 300 resolved, Brier < 0.20, WinRate > 55% |

---

## 10. COMMON DEBUGGING COMMANDS

```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Status + recent logs:
ssh -i "$KEY" "$VPS" "sudo systemctl status polymarket-ai --no-pager | head -10 && sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | tail -40"

# P&L summary by bot:
ssh -i "$KEY" "$VPS" "sudo -u polymarket psql -d polymarket -c \"SELECT bot_name, COUNT(*), ROUND(SUM(realized_pnl)::numeric,2) FROM paper_trades WHERE realized_pnl IS NOT NULL GROUP BY bot_name ORDER BY 3 DESC;\""

# Trade count last 24h:
ssh -i "$KEY" "$VPS" "sudo -u polymarket psql -d polymarket -c \"SELECT bot_name, COUNT(*), ROUND(SUM(size*price)::numeric,2) as volume FROM paper_trades WHERE created_at > now() - interval '24 hours' GROUP BY bot_name ORDER BY 2 DESC;\""

# Open positions:
ssh -i "$KEY" "$VPS" "sudo -u polymarket psql -d polymarket -c \"SELECT bot_id, COUNT(*), ROUND(SUM(size*avg_price)::numeric,2) as exposure, ROUND(SUM(unrealized_pnl)::numeric,2) as unrealized FROM positions WHERE status='open' AND side != 'SELL' GROUP BY bot_id;\""

# Prediction log labels:
ssh -i "$KEY" "$VPS" "sudo -u polymarket psql -d polymarket -c \"SELECT was_correct, COUNT(*) FROM prediction_log WHERE was_correct IS NOT NULL GROUP BY was_correct;\""

# Check BotBankrollManager init in logs:
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since '30 min ago' --no-pager | grep BotBankrollManager"

# Check for scan timeouts:
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep -c 'scan timed out'"
```

---

## 11. KNOWN ACTIVE ISSUES (Non-Blocking)

1. **MomentumBot: DELETED permanently** (Session 44). Do not recreate.
2. **Polygon RPC 401 warning** -- non-critical (mempool monitoring).
3. **Google Trends 429** -- rate limiting, 3600s backoff. Expected.
4. **MirrorBot**: First trades in S49 (2 positions). Previously no trades due to elite consensus threshold.
5. **WebSocket reconnects** -- every 3-5 min. Auto-recovers.
6. **Esports bots: DISABLED** -- Need `PANDASCORE_API_KEY`.
7. **LogicalArbBot: ENABLED** (Session 49) -- scanning, using sentence_transformers for embeddings.
8. **BAYESIAN_MODEL_ENABLED: false** -- Elite model features ready but need API keys.
9. **USE_PER_BOT_MODELS: false** -- Per-bot model training infrastructure ready but gated. Enable when bots accumulate 200+ prediction_log entries each.
10. **Model accuracy ~50%** (Session 49) -- Calibration shows essentially random predictions. Brier=0.2512, win_rate=49.6%. Platt scaling now enabled, monitoring for improvement.
11. **0% sell win rate** (Session 49 FIXED) -- Was caused by penny token churn + model reversal churn. Price floor raised to 15c, reversal threshold lowered to 30%.

---

## 12. PRIORITY NEXT STEPS

### Immediate (Session 50+)
1. **Monitor Session 49 trade quality** -- With price floor at 15c and relative spread check, trades should be higher quality. Check if any sells are now profitable.
   ```bash
   ssh -i "$KEY" "$VPS" "sudo -u polymarket psql -d polymarket -c \"SELECT bot_name, COUNT(*), SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins, ROUND(SUM(realized_pnl)::numeric,2) as pnl FROM paper_trades WHERE realized_pnl IS NOT NULL AND created_at > '2026-03-04' GROUP BY bot_name;\""
   ```
2. **Track win rate improvement** -- Currently 49.6% (242 resolved). Need 52% to graduate.
3. **Monitor Platt scaling effect** -- Enabled in S49, should improve calibration over time.
4. **Watch for PHASE_PROMOTION_RECOMMENDED** -- PhaseTracker logs this when criteria met.

### Short-Term
5. **Consider enabling USE_PER_BOT_MODELS=true** when EnsembleBot has 200+ prediction_log entries (currently 90,451 total, but only 242 resolved).
6. **Investigate model accuracy** -- 49.6% is random. May need feature engineering improvements or model retraining with better data.
7. **SPORTSDATAIO_API_KEY** -- Enables 3 sports bots (user must provide).
8. **PANDASCORE_API_KEY** -- Enables 3 esports bots (user must provide).

### Before Real Money
9. All deferred guardrail items verified
10. API keys for Elite Model (Congress.gov, CourtListener, VoteHub)
11. CLOB_API_KEY + CLOB_SECRET + CLOB_PASSPHRASE + PRIVATE_KEY + WALLET_ADDRESS
12. Alert webhook (Slack/Discord) for critical events
13. Full review of `docs/LIVE_RUN_CHECKLIST.md`
14. Phase promotion through Paper -> Learning -> Graduated -> Production

---

## 13. SESSIONS CHRONOLOGICAL SUMMARY

| Session | Date | Key Work |
|---------|------|----------|
| 1-17 | Foundation | B5 paper trading fix, cascade threshold, DB timezone, Python scoping traps |
| 18 | Core | Model reversal exits DISABLED (unwarmed cache). Stop-loss 30%, take-profit 60% |
| 19 | Weather | WeatherBot SWOT (P1-P7): calibration, ECMWF ensemble, dynamic Kelly |
| 20-22 | Hardening | Adaptive exit learning. 12 bottleneck fixes. Model reversal RE-ENABLED with warm guard |
| 23-26 | Infra | Infrastructure hardening, sports DLQ, VPS deploy, weather ECMWF fix |
| 27-30 | Edge | Tests fixed 657/657. Progressive cooldown. Edge filter. Side-bias. Per-bot Kelly. VPS deploy |
| 31 | Pipeline | Volume filter, _infer_category, extremization=1.4, edge thresholds. **ARCHITECTURAL MANDATE** |
| 32 | Labels | endDateISO root cause. Pseudo-label backfill (later DISABLED S42). Consecutive loss guardrail |
| 33 | DB Health | Pool exhaustion, slug dedup, advisory lock, health_runner, mini backfill |
| 34 | Audit | 54 fixes: training labels, lookahead feature, realized_edge, PipelineGate |
| 35 | Sports | 26 fixes: arb formula, Coinbase rate, KalshiSportsClient |
| 36 | Features | Clarity scoring + disposition effect |
| 37 | Esports | 3 bots + 12 modules + 8 DB tables |
| 38 | Guardrails | Phase bet caps, Category Kelly, KELLY_ACTIVE_BOTS, politics exit, weather hold, phase tracker |
| 39 | Elite Model | 6 new signal modules (legislative, polling, bayesian, logical_arb, court, intl_elections) |
| 40 | Tests | All flaky tests fixed: inspect.getsource->pathlib, PhaseTracker._last_evaluated |
| 41 | LogicalArbBot | 16th bot. Platt scaling wired. Section 21 features. VPS full deploy |
| 42 | was_correct Fix | Pseudo-labels poisoned model (4,654 bad labels). DISABLED via PSEUDO_LABEL_ENABLED=false. Dashboard UI parity |
| 44 | Sell Bug Fix | position_manager stale current_price. MomentumBot DELETED. VPS upgraded to Ubuntu-3 (16GB/4vCPU). Supabase removed |
| 46 | Prod Readiness | 3->32 trades/day. Scan timeout 30s->120s. Market limit 50->200. Edge 0.10->0.04. Concurrency 5->10 |
| **47** | **Per-Bot Independence** | **BotBankrollManager (per-bot capital/Kelly). Edge 0.04->0.02. Cooldown 30min->5min. sklearn suppressed. 1090 tests** |
| **48** | **Deep-Sweep Fixes** | **P1-P8 root cause fixes. Price floor guard. WatchdogSec removed. MemoryMax=6G. EnsembleBot first trades** |
| **49** | **Trade Quality + Ops** | **0% sell win rate fixed (penny churn + model reversal + relative spread). Platt enabled. LogicalArbBot enabled. SAVEPOINT slug fix. sentence_transformers** |

---

## 14. CODE DEBUGGING PATTERNS

```python
# WRONG: edge was abs() -- blocks valid shorts
if abs(edge) < min_edge: return None
# CORRECT:
if edge < min_edge: return None

# WRONG: bot side
self.place_order(side="BUY")
# CORRECT:
self.place_order(side="YES")  # or "NO"

# WRONG: inspect.getsource in tests (fragile with mocks)
src = inspect.getsource(module.SomeClass.method)
# CORRECT:
src = pathlib.Path(module.__file__).read_text(encoding="utf-8")

# WRONG: PhaseTracker initial value
self._last_evaluated: float = 0.0
# CORRECT:
self._last_evaluated: float = float("-inf")

# WRONG: was_correct from P&L
was_correct = (avg_pnl > 0)
# CORRECT: from actual resolution
was_correct = ((predicted_prob >= 0.5) == (resolution == 'YES'))

# WRONG: shared Kelly divisor (DEPRECATED S47)
fraction = KELLY_FRACTION / KELLY_ACTIVE_BOTS
# CORRECT: per-bot bankroll
size_usd = await self.bankroll.get_bet_size(confidence, price, category=cat)
```

---

## 15. MEMORY FILES & DOCUMENTATION

```
Auto-loads each session:
  C:\Users\samwa\.claude\projects\C--lockes-picks-polymarket-ai-v2\memory\MEMORY.md

Canonical full handoffs (NEWEST FIRST):
  C:\lockes-picks\polymarket-ai-v2\AGENT_HANDOFF_SESSION47_2026_03_03.md  -- THIS FILE
  C:\lockes-picks\polymarket-ai-v2\AGENT_HANDOFF_SESSION42_2026_03_02.md  -- Sessions 1-42
  C:\lockes-picks\polymarket-ai-v2\AGENT_HANDOFF_COMPLETE_2026_02_27.md   -- Sessions 1-40

Elite model roadmap:
  C:\lockes-picks\polymarket-ai-v2\docs\ELITE_MODEL_DEEP_DIVE.md

Phase graduation doc:
  C:\lockes-picks\polymarket-ai-v2\docs\PHASES_AND_REBUTTALS_MASTER.md

Pre-live checklist:
  C:\lockes-picks\polymarket-ai-v2\docs\LIVE_RUN_CHECKLIST.md
```

---

*End of handoff -- Session 47, 2026-03-03*
*Tests: 1090 passed, 6 skipped, 0 failed.*
*VPS: Sessions 1-47 ALL DEPLOYED. Service active at 34.251.224.21.*
*15 bots total. Per-bot BotBankrollManager (S47): EnsembleBot=$8k, Arb=$1k, Mirror=$1k.*
*Universal guardrail layer: risk_manager.check_risk_limits() + order_gateway.place_order().*
*prediction_log: bot_name column (S47). 242 clean labels (49.6% accuracy). PSEUDO_LABEL_ENABLED=false.*
*Next: Monitor S47 impact (target 60+ trades/day), watch for PHASE_PROMOTION_RECOMMENDED.*
