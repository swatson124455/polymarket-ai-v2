# MASTER HANDOFF — Polymarket AI Trading System V2
**Date: 2026-02-22 | Author: Claude Sonnet 4.6 | Status: COMPLETE carbon copy for new agent**

> **New agent: read this entire file top to bottom before touching anything.
> It contains every decision, bug fix, vision, gotcha, and next step.**

---

## TABLE OF CONTENTS
1. [Vision & Goals](#1-vision--goals)
2. [Current System State (RIGHT NOW)](#2-current-system-state-right-now)
3. [Critical Concepts (NEVER GET WRONG)](#3-critical-concepts-never-get-wrong)
4. [Architecture](#4-architecture)
5. [Key File Map](#5-key-file-map)
6. [Environment & Commands](#6-environment--commands)
7. [Current .env Settings](#7-current-env-settings)
8. [Database](#8-database)
9. [ML Ensemble & Training Pipeline](#9-ml-ensemble--training-pipeline)
10. [Complete Fix History (All Sessions)](#10-complete-fix-history-all-sessions)
11. [Active Issues & Immediate Fixes Needed](#11-active-issues--immediate-fixes-needed)
12. [Tier 2 Backlog (7 items remaining)](#12-tier-2-backlog-7-items-remaining)
13. [Long-Term Roadmap](#13-long-term-roadmap)
14. [Test Suite](#14-test-suite)
15. [Known Gotchas & Quirks](#15-known-gotchas--quirks)
16. [Mock Patterns for Tests](#16-mock-patterns-for-tests)
17. [Documentation Map](#17-documentation-map)

---

## 1. Vision & Goals

**What we're building**: Python async trading system that places profitable bets on Polymarket prediction markets. Currently in paper trading (simulation) mode. Goal: validate strategy via paper trading → apply remaining features → deploy to VPS → live trading with real capital.

**Strategy**: ML ensemble predicts whether a market will resolve YES or NO. 4 bots run concurrently, each with a different alpha source:
- **EnsembleBot**: 10-model ML prediction → confidence filter → trade
- **ArbitrageBot**: Binary price arb (YES + NO < 1.0) + cross-market
- **MomentumBot**: Z-score mean reversion, cascade fading
- **MirrorBot**: Copy elite trader consensus

**Pipeline (canonical)**:
```
Polymarket CLOB/WS
  └─ WebSocketManager → market_prices DB
       └─ EventBus → on_price_update()
            ├─ EnsembleBot: market_index[cid] → predict() → analyze_opportunity()
            ├─ ArbitrageBot: WS price cache → arb screen → analyze_opportunity()
            └─ MomentumBot: z-score → signal → analyze_opportunity()

Bot scan_and_trade() [every 10-60s]:
  └─ get_all_tradeable_markets() [LIMIT 50 on Supabase, 1500 on VPS]
       └─ filter open positions [O(1) in-memory set]
            └─ prefetch_markets() [batch DB load]
                 └─ parallel analyze_opportunity() [concurrency=3 on Supabase, 10 on VPS]
                      └─ predict() [feature cache → 10 models → ensemble → <5ms]
                           └─ confidence filter [ENSEMBLE_MIN_CONFIDENCE=0.45]
                                └─ RiskManager [position limits, CVaR, edge filter]
                                     └─ DrawdownController [caution→restricted→halted]
                                          └─ MultiLayerKillSwitch [emergency halt]
                                               └─ OrderGateway [adverse selection gate]
                                                    └─ PaperTradingEngine [fee sim, fill sim]
                                                         └─ DB persist [paper_trades, positions]
```

**End goal timeline**:
```
Paper trading (now) → strategy validation → VPS deploy (Dublin, ~2ms to London CLOB)
→ Tier 2 features → live trading with real capital
```

---

## 2. Current System State (RIGHT NOW)

**As of 2026-02-22 18:34 UTC:**

| Property | Value |
|----------|-------|
| Mode | Paper trading (SIMULATION_MODE=true, LIVE_TRADING=false) |
| Capital | $100,000 simulated |
| Active bots | 4 (Ensemble, Arbitrage, Momentum, Mirror) |
| ML models | 10 (RF, XGB, GB, LR, ET, HGB, LightGBM, CatBoost, Ridge, KNN) |
| Unit tests | **321 passing, 0 warnings** |
| WS tokens | ~752 subscribed |
| DB | Supabase Pro, 579 MB/8 GB, session pooler port 5432 |
| Process | PID 34124 running (background via `python run_paper.py`) |
| Log | `data/paper_trading.log` |

**Live log summary (last 5 hours)**:
- `18:22:54` — BaseEngine started
- `18:23:06` — WebSocket subscribed to 752 tokens
- `18:23:41` — IngestionScheduler: starting run (3,000 markets — SLOW, see §11)
- `18:24:10` — System running, 4 bots active
- `18:26:51` — Whale trade detected: $15,500 (market working, API live)
- `18:31:42-06` — **ALL 4 bots timed out** (300s cold-start scan) — expected on first run
- `18:33:10` — WebSocket reconnected after brief disconnect
- `18:34:57` — "Tradeable markets from DB: 50" — system resuming, bots scanning again

**Post-timeout behavior**: After 300s timeouts, bots restart their scan loop. All 4 bots are running, DB is connected (12 active connections), WS is streaming. The 300s timeout on first scan is a known cold-start issue (see §11).

---

## 3. Critical Concepts (NEVER GET WRONG)

### 3A. Polymarket Side Semantics — THE MOST IMPORTANT THING
**This was a systemic bug. Every agent MUST understand this.**

- **YES and NO are both BUY operations** — you are buying that outcome token
- **SELL only means closing a position** (selling tokens you already hold)
- Both YES tokens and NO tokens have prices between 0 and 1
- P&L is ALWAYS `(current_price - entry_price) / entry_price` regardless of YES or NO
- Fading into a YES cascade means **buying NO tokens** (not selling)
- Signals return YES/NO (which token to buy). Gateway normalizes to BUY/SELL for routing.

**Gateway normalization** (`order_gateway.py`): converts non-"SELL" → "BUY" for order routing, but passes `original_side` (YES/NO) for position tracking and P&L.

### 3B. Market ID Formats
Two incompatible ID formats exist in the same system:
- **Numeric ID** (`628113`): `markets.id` (DB primary key), used in `market_prices` table, feature cache keys `fv:{id}:{token_id}`
- **Condition ID** (`0x339d...`): Polymarket WebSocket `"market"` field, also stored in `markets.condition_id`, trades join on this

**Always JOIN with**: `(mp.market_id = m.id OR mp.market_id = m.condition_id)`

This was a critical bug fixed in the last session. Before the fix, JOINs used only one format → missed 80%+ of price data (112 → 735 markets with price data after fix).

### 3C. Model Cache
- File: `data/model_cache.pkl`
- **MUST contain**: `feature_columns` and `best_feature_names` — cache without them is rejected on load (forces retrain)
- Delete to force retrain: `del data\model_cache.pkl` → next `python main.py` retrains (~2-3 min)
- Also saves `best_feature_names` and PSI baselines

### 3D. DB Connection Rules
- **Port 5432** = Supabase session pooler (correct)
- **Port 6543** = Supabase transaction pooler — DO NOT USE (drops SSL mid-query)
- Auto-detected: `"pooler.supabase.com" in url` → `statement_cache_size=0`
- `DATABASE_URL` lives in `.env` only — NEVER in code or memory files
- Semaphore = `pool_size` (12) — advisory lock sessions use `get_raw_session()` to bypass

### 3E. Logging Levels
- Bot **scan cycles log at DEBUG** (invisible at INFO level by design)
- Only slow scans (>5s), errors, and trade executions appear at WARNING/INFO
- **Ingestion progress logs at INFO every 60s** — if you see no progress logs, ingestion is stuck
- Feature pre-compute "waiting for models" is **DEBUG** — invisible if models aren't ready

---

## 4. Architecture

### 4A. Bots
| Bot | File | Status | Alpha Source |
|-----|------|--------|-------------|
| EnsembleBot | `bots/ensemble_bot.py` | **Enabled** | 10-model ML ensemble |
| ArbitrageBot | `bots/arbitrage_bot.py` | **Enabled** | YES+NO<1 price arb |
| MomentumBot | `bots/momentum_bot.py` | **Enabled** | Z-score, cascade fading |
| MirrorBot | `bots/mirror_bot.py` | **Enabled** | Elite trader consensus |
| CrossPlatformArbBot | `bots/cross_platform_arb_bot.py` | Disabled | Multi-exchange arb |
| OracleBot | `bots/oracle_bot.py` | Disabled | UMA oracle disputes |
| SportsBot | `bots/sports_bot.py` | Disabled | Sports specialist |
| LLMForecasterBot | `bots/llm_forecaster_bot.py` | Disabled | LLM predictions |
| WeatherBot | `bots/weather_bot.py` | Disabled | Weather markets |

All inherit `BaseBot` (`bots/base_bot.py`).

### 4B. ML Ensemble (10 Models)
All toggleable via `MODEL_ENABLE_*` env vars (default: all true).

| Model | Key | Notes |
|-------|-----|-------|
| RandomForest | `random_forest` | class_weight="balanced", MDI feature importance |
| XGBoost | `xgboost` | scale_pos_weight from class ratio |
| GradientBoosting | `gradient_boosting` | Sequential, no n_jobs |
| LogisticRegression | `logistic_regression` | Has `coef_` not `feature_importances_` |
| ExtraTrees | `extra_trees` | Random split thresholds, class_weight="balanced" |
| HistGradientBoosting | `hist_gradient_boosting` | class_weight="balanced" |
| LightGBM | `lightgbm` | is_unbalance=True, try/except ImportError |
| CatBoost | `catboost` | Skips CalibratedClassifierCV (no `__sklearn_tags__`) |
| RidgeClassifier | `ridge` | Wrapped in CalibratedClassifierCV for predict_proba |
| KNeighborsClassifier | `knn` | Distance-weighted, k=7 |

**Training validation**: Mandatory DummyClassifier gate — if zero models beat majority-class baseline, entire training is rejected and previous cache kept.

**Meta-learning**: MetaLearner auto-weights models based on Brier score. CatBoost gets downweighted (no calibration). Walk-forward 80/20 temporal split + purge/embargo + Brier rollback.

### 4C. Core Components
| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| BaseEngine | `base_engine/base_engine.py` | ~1500 | 50+ component orchestrator |
| Database | `base_engine/data/database.py` | ~3400 | 23 SQLAlchemy ORM tables |
| PredictionEngine | `base_engine/prediction/prediction_engine.py` | ~1600 | ML ensemble |
| OrderGateway | `base_engine/execution/order_gateway.py` | — | Risk→paper/CLOB |
| PaperTradingEngine | `base_engine/execution/paper_trading.py` | — | Simulated fills |
| PositionManager | `base_engine/execution/position_manager.py` | — | Position lifecycle |
| RiskManager | `base_engine/risk/risk_manager.py` | — | Limits, CVaR, edge filter |
| DrawdownController | `base_engine/risk/drawdown_controller.py` | — | Graduated: caution→halted |
| MultiLayerKillSwitch | `base_engine/risk/multi_kill_switch.py` | — | Emergency halt |
| CorrelationRiskManager | `base_engine/risk/correlation_risk.py` | — | CVaR tail-risk gate |
| WebSocketManager | `base_engine/data/websocket_manager.py` | — | Real-time streaming |
| PolymarketClient | `base_engine/data/polymarket_client.py` | — | REST + CLOB API |
| DatabaseLock | `base_engine/data/database_lock.py` | — | PG advisory locks |
| IngestionScheduler | `base_engine/data/ingestion_scheduler.py` | — | Periodic data ingestion |
| LearningScheduler | `base_engine/learning/scheduler.py` | — | Retrain scheduler |
| CalibrationTracker | `base_engine/learning/calibration_tracker.py` | — | DDM+EDDM drift |
| MarketImpact | `base_engine/features/market_impact.py` | — | Sqrt impact model |
| LLMProbability | `base_engine/features/llm_probability.py` | — | Anthropic predictions |
| AlertingSystem | `base_engine/monitoring/alerting.py` | — | Auto-alerts |
| PipelineGate | `base_engine/monitoring/pipeline_gate.py` | — | Training quality gate |
| Settings | `config/settings.py` | ~610 | All env-driven config |
| Dashboard | `ui/dashboard.py` | ~3500 | Streamlit UI |

---

## 5. Key File Map

```
main.py                                              Entry point (391 lines)
run_paper.py                                         Background process launcher
config/settings.py                                   All settings (Pydantic, ~610 lines)
base_engine/
  base_engine.py                                     Core orchestrator (~1500 lines)
  data/
    database.py                                      ORM + 23 tables (~3400 lines)
    database_lock.py                                 PG advisory locks (uses get_raw_session)
    polymarket_client.py                             REST/CLOB API client
    websocket_manager.py                             Real-time streaming
    ingestion_scheduler.py                           Data ingestion loop
    ingestion.py                                     Market + price ingestion logic
    streaming_persister.py                           WS price writes to DB
  prediction/
    prediction_engine.py                             10-model ML ensemble (~1600 lines)
  execution/
    order_gateway.py                                 Risk → paper/CLOB pipeline
    paper_trading.py                                 Simulated fills + fees
    position_manager.py                              Position lifecycle
  risk/
    risk_manager.py                                  Position limits, CVaR, edge
    drawdown_controller.py                           Graduated halt logic
    multi_kill_switch.py                             Emergency halt
    correlation_risk.py                              CVaR tail-risk ($200 cap gate)
  learning/
    scheduler.py                                     Retrain scheduler (6h intervals)
    learning_engine.py                               Pattern learning
    calibration_tracker.py                           DDM+EDDM+ADWIN drift detection
  features/
    market_impact.py                                 Sqrt market impact model
    llm_probability.py                               Anthropic prompt caching
    sentiment_analyzer.py                            VADER sentiment scoring
    wikipedia_pageviews.py                           Wiki pageviews signal
  monitoring/
    alerting.py                                      Auto-alerts (Brier/Sharpe/drift)
    pipeline_gate.py                                 Training quality gate
bots/
  base_bot.py                                        Base class (430 lines, LatencyTracker)
  ensemble_bot.py                                    Primary ML bot (529 lines)
  arbitrage_bot.py                                   Binary + cross-market arb
  momentum_bot.py                                    Z-score + cascade fading
  mirror_bot.py                                      Elite trader mirroring
ui/
  dashboard.py                                       Streamlit (~3500 lines)
tests/unit/                                          321 tests, 24 files, 0 warnings
schema/migrations/                                   001-018 SQL migrations
scripts/                                             ~40 operational scripts
data/
  model_cache.pkl                                    Trained model cache (delete to retrain)
  paper_trading.log                                  Live log (TeeLogger writes here)
  rl_qtable.pkl                                      RL agent Q-table (disabled by default)
```

---

## 6. Environment & Commands

### Prerequisites
- **Python 3.13.3** system-installed (no venv)
- **Windows 10**, PowerShell
- **VPN: Surfshark must be ON** — US/UK IPs get 403 from Polymarket API. Auto-detected via OS routing (WireGuard).
- **Redis**: Optional. Running locally on default port 6379 (confirmed by live network connections despite pre-flight warning).

### Commands
```powershell
# Run paper trading in foreground (recommended for debugging)
python main.py

# Run paper trading as background process (log to data/paper_trading.log)
python run_paper.py

# Monitor live log
powershell -Command "Get-Content 'data\paper_trading.log' -Tail 30 -Wait"

# Kill background process
powershell -Command "Stop-Process -Id 34124 -Force"  # Replace PID

# Run all 321 unit tests
powershell.exe -Command "cd 'C:\lockes-picks\polymarket-ai-v2'; python -m pytest tests/unit/ -v --no-cov --tb=short"

# Run verification tests (REQUIRED after editing data/prediction code)
python -m pytest tests/unit/test_poly_data_fixes.py tests/unit/test_prediction_price_fallback.py tests/unit/test_ingestion_historical_price_flow.py -v --no-cov

# Force model retrain (delete cache, restart)
del data\model_cache.pkl
python main.py

# Run migrations (001-016 are safe; 017-018 blocked by Supabase pooler)
python scripts/run_migrations.py

# Dashboard
streamlit run ui/dashboard.py

# Check DB directly (bypass pooler — use direct connection string from Supabase dashboard)
psql "direct-connection-string"
```

---

## 7. Current .env Settings

**IMPORTANT**: `DATABASE_URL` contains real credentials — never display or log.

```ini
# === DB ===
DATABASE_URL=<in .env file — session pooler port 5432>
DB_POOL_SIZE=12
DB_MAX_OVERFLOW=3
# Comment in file: set Supabase dashboard pool_size to 40+

# === Trading Mode ===
SIMULATION_MODE=true
LIVE_TRADING=false
TOTAL_CAPITAL=100000
PAPER_TRADING_CAPITAL=100000

# === Risk (Learning Mode) ===
ENSEMBLE_MIN_CONFIDENCE=0.45      # Lowered from 0.65 for faster feedback
RISK_MIN_EDGE_PCT=1
RISK_MAX_POSITION_SIZE_USD=100    # Small = more distinct markets
RISK_MAX_POSITIONS_COUNT=100
RISK_MAX_TOTAL_EXPOSURE_USD=10000
RISK_MAX_DAILY_LOSS_USD=2000

# === Scan Config (Supabase-safe settings) ===
SCAN_MARKET_LIMIT=50              # CRITICAL: 50 for Supabase (~400ms/query). → 1500 on VPS
ENSEMBLE_SCAN_CONCURRENCY=3       # 3 parallel per bot × 4 bots = 12 sessions max
BOT_SCAN_TIMEOUT_SECONDS=300      # 5 min to allow cold-start

# === Scan Intervals ===
SCAN_INTERVAL_ARBITRAGE=15
SCAN_INTERVAL_ENSEMBLE=30
SCAN_INTERVAL_MOMENTUM=30
SCAN_INTERVAL_MIRROR=60

# === WebSocket Reactive ===
ENSEMBLE_WS_PRICE_CHANGE_PCT=0.005
ENSEMBLE_WS_COOLDOWN_SECONDS=10
ARB_WS_PRICE_CHANGE_PCT=0.008
ARB_WS_COOLDOWN_SECONDS=5

# === Ingestion (PROBLEM: 3000 is too large for Supabase) ===
DAILY_INGESTION_MARKETS_COUNT=3000   # ← REDUCE TO 200-500 (see §11)
DAILY_INGESTION_PRICES_MARKETS=3000  # ← REDUCE TO 200-500

# === Model Enables (all true = 10 models) ===
MODEL_ENABLE_MLP=true
RL_TRADE_TIMING_ENABLED=false        # RL agent disabled (paper trading phase)

# === ML Tuning ===
ALPHA_DECAY_LAMBDA=0.5               # Signal freshness decay rate

# === Pooler ===
POLYMARKET_GAMMA_API=https://gamma-api.polymarket.com
POLYMARKET_CLOB_API=https://clob.polymarket.com
POLYMARKET_DATA_API=https://data-api.polymarket.com
POLYMARKET_WS=wss://ws-subscriptions-clob.polymarket.com

# === Redis (optional) ===
REDIS_ENABLED=true
REDIS_HOST=localhost
REDIS_PORT=6379
```

---

## 8. Database

### Connection
- **Supabase Pro** ($25/mo)
- **Session pooler port 5432** (NOT 6543 — transaction pooler)
- Auto-detect: `"pooler.supabase.com" in url` → `statement_cache_size=0`, `statement_cache_maxsize=0`
- Pool: `pool_size=12, max_overflow=3` (15 total connections)
- Semaphore: `asyncio.Semaphore(12)` — queues requests gracefully
- `get_raw_session()`: bypasses semaphore — ONLY for advisory lock sessions (prevents deadlock)

### DB Stats (as of 2026-02-15)
- 579 MB / 8 GB (Pro limit)
- ~1.09M `market_prices` (after 267k dupes removed, unique constraint applied)
- ~137k total trades
- ~2.7k markets, 1,769 users
- 1,004 resolved YES/NO markets (989 with volume ≥ 500)
- 35,311 trades JOIN on `m.id`, 20,579 JOIN on `m.condition_id`
- `resolved_at` = NULL for ALL resolved markets (resolved via `end_date_iso` fallback)
- 37 elite users exist

### Key ORM Tables
```
markets              — All Polymarket markets
market_prices        — Price history (dual market_id format, unique constraint)
trades               — Trade history (condition_id format)
paper_trades         — Paper trading simulation records
paper_positions      — Current paper positions
prediction_log       — ML prediction history (with feature_snapshot JSONB)
users                — Trader profiles
elite_users          — Elite trader list (37 users)
sync_log             — Ingestion run tracking
model_versions       — ML model performance history
```

### Known Schema Blockers
- **Migration 017** (neg_risk, outcome_count columns): BLOCKED by Supabase pooler lock timeout. Apply via direct psql connection (bypassing pooler) with:
  ```sql
  ALTER TABLE markets ADD COLUMN IF NOT EXISTS neg_risk BOOLEAN DEFAULT false;
  ALTER TABLE markets ADD COLUMN IF NOT EXISTS outcome_count INTEGER DEFAULT 2;
  CREATE INDEX IF NOT EXISTS idx_markets_neg_risk ON markets (neg_risk) WHERE neg_risk = true;
  ```
  Then uncomment ORM columns in `database.py` (search `neg_risk`).
- **Migration 018** (feature_snapshot JSONB): ORM done, SQL not applied (same pooler issue).

### Critical DB Fix (2026-02-18)
`market_prices.market_id` format mismatch — historical ingestion stores numeric ID (`628113`), WebSocket streaming stores condition_id (`0x339d...`). ALL JOINs must use:
```sql
(mp.market_id = m.id OR mp.market_id = m.condition_id)
```
Files with this fix: `base_engine.py`, `prediction_engine.py`, `database.py` (3 queries).

---

## 9. ML Ensemble & Training Pipeline

### Training Data Sources
1. **Primary**: ALL trades on resolved markets (UNION ALL join on `m.id` and `m.condition_id`)
2. **Fallback**: Price history from `market_prices` (MAX timestamp per market)
3. **Paper trade rows**: from `paper_trades` table (weight=0.5, max 5,000 rows)
4. **Prediction log rows**: from `prediction_log` table (weight=0.3, max 10,000 rows)

### Temporal Guard
- `resolved_at` NOT NULL → 6h cutoff before resolution
- `resolved_at` NULL → use `end_date_iso` as fallback (CASE WHEN in SQL)
- This bypasses the 6h cutoff for all current markets (all have `resolved_at=NULL`)

### Labels
Binary (1=correct prediction, 0=wrong) based on:
- `resolution` (YES/NO) vs `side` (YES/NO) or `token_id` (for older trades)

### Sample Weights
- Base: log-scaled volume
- Elite multiplier: 1.35x base, 1.55x high-vol elite
- Class balance: `w_pos/w_neg` ratio applied after volume weights
- "Why wrong" boost: wrong high-confidence high-edge predictions get 1.0-2.0x correction boost

### Validation
- Walk-forward 80/20 temporal split + purge/embargo gap
- Brier rollback gate: if new model Brier > old model Brier, reject new model, keep old
- Mandatory DummyClassifier baseline gate: zero models beating majority class → full rejection
- PSI drift detection: checks feature distribution shift every PSI_CHECK_INTERVAL hours

### Retrain Trigger
- Scheduled: every `RETRAIN_INTERVAL_HOURS=6` via LearningScheduler
- Triggered early: if new paper trade/prediction feedback arrives since last cycle
- Skip: if not enough training data (pipeline gate check with paper trade fallback)

### Model Cache Key
`data/model_cache.pkl` contains:
- `models`: dict of trained sklearn/xgb/lgb/catboost models
- `feature_columns`: list of feature names (CRITICAL — cache rejected without this)
- `best_feature_names`: top selected features
- `scaler`: fitted StandardScaler
- `meta_weights`: per-model Brier-based weights
- `psi_baselines`: feature distribution baselines for PSI drift detection
- `version`, `trained_at`, `brier_score`

---

## 10. Complete Fix History (All Sessions)

### Session 2026-02-17 (Audit Phase 1)
- **C3**: Scan intervals reduced (Arb=5s, Ensemble=10s) + WS thresholds lowered
- **C1**: DrawdownController wired into OrderGateway (graduated: caution→restricted→halted)
- **H1**: MultiLayerKillSwitch wired into scan loop (base_bot.py) + order pipeline (order_gateway.py)
- **H3**: Label distribution logging + DummyClassifier baseline to training validation
- **M1**: MirrorBot elite fetches parallelized with asyncio.gather + Semaphore(5) — 7s→1-2s

### Session 2026-02-17 (Audit Phase 2)
- **BUG-1**: Fixed multi_kill_switch=None in OrderGateway (was created 13 lines after being passed)
- **BUG-2**: Fixed `get_position_multiplier()` sync→async (was returning coroutine object)
- **BUG-3**: Added `realized_pnl_today` tracking to PaperTradingEngine (daily auto-reset)
- **BUG-4**: Fixed SQLite `strftime()` → PostgreSQL `to_char()` in database_partitioning.py
- **M3**: Fixed `weekly_drawdown = daily_drawdown` — accepts separate `realized_pnl_week`
- **DEAD-1**: Wired CorrelationRiskManager — CVaR tail-risk gate ($200 cap)
- **GAP-4**: Added Ridge + KNN models (8→10 model ensemble)
- Feature columns now saved in model cache (was causing zero predictions after restart)

### Session 2026-02-18 (Audit Phase 3 — Paper Trade Pipeline)
- **CLOB /book API**: param `token` → `token_id` (was 400 Bad Request)
- **OrderBook tracker**: empty `market_id=""` → threads condition_id through
- **Liquidity check**: skipped in SIMULATION_MODE
- **CRITICAL side mapping**: NO→SELL was WRONG. Both YES/NO are BUY. (order_gateway.py)
- **1A**: MomentumBot P&L was inverted for NO positions → unified `(current-entry)/entry`
- **1B**: Advanced orders exit side → all exits are SELL
- **1C**: Cascade fade: `fade_side` changed from BUY/SELL to YES/NO (buy opposite token)
- **1C ext**: Signal alignment in base_bot.py → uses YES/NO not BUY/SELL
- **2A**: Paper trading `original_side` parameter added — preserves YES/NO
- **3B**: Paper trading realized PnL — defensive `avg_price or 0.0` guard
- **Model accuracy**: class_weight="balanced" (RF, HGB), scale_pos_weight (XGB), is_unbalance (LGBM)
- **Market ID JOIN fix**: ALL JOINs now use dual-format `(m.id OR m.condition_id)` (112→735 markets)
- **First paper trade executed**: market 620335, NO side, 74.5% confidence

### Session 2026-02-19 (Training Feedback Loop)
- **Training feedback loop**: ML models now learn from paper trade outcomes + prediction log
  - `_get_paper_trade_training_rows()` added to prediction_engine.py
  - `_get_prediction_log_training_rows()` added to prediction_engine.py
  - `_prepare_training_data()` appends both sources with `_source` column
  - LearningScheduler triggers retrain on new feedback, not just on timer
  - PipelineGate falls back to paper trade counts if external trades gate fails
  - `_on_resolution()` in base_engine.py triggers immediate backfill on market resolution
- **Position management**: 10-min grace period, min_edge_to_hold=-0.05, ghost position auto-close
- **Migration 017**: BLOCKED (Supabase pooler lock timeout — see §8)

### Session 2026-02-20 (Master Audit — Step 0 + Tier 1 + Millisecond Architecture)

**Step 0 Prerequisites (5/5)**:
1. vaderSentiment added to requirements.txt
2. `prediction_timestamp` added to `predict()` return dict
3. AlertingSystem wired into bot scan loop (3+ consecutive failure alerts)
4. Maker/taker fee simulation in PaperTradingEngine (TAKER=1.5%, MAKER=0%)
5. Migration 018: `feature_snapshot JSONB` column ORM + SQL

**Tier 1 (9/9)**:
1. Longshot bias filter (`ensemble_bot.py:529-541`) — boost NO conf when price<0.20
2. Alpha decay (`ensemble_bot.py:468-479`) — `confidence *= exp(-lambda * hours)`
3. DDM + EDDM drift detection (`calibration_tracker.py:15-106`)
4. ADWIN formalization (`prediction_engine.py:76-93`)
5. Maker-taker fee preference (order_type threaded through gateway→paper trading)
6. Adverse selection gate (`order_gateway.py:358-372`) — reject if spread < 2x cost
7. Sqrt market impact (`market_impact.py:80-139`)
8. Anthropic prompt caching (`llm_probability.py:138-155`)
9. Auto-alerts for Brier/Sharpe/drift (`alerting.py:387-432`)

**Millisecond Scan Architecture (9 silent bugs fixed)**:
1. Dual-key market index: `_market_index_by_cid` added — WS reactive trades now work
2. WS cache key: `update_cached_price()` now uses numeric id (was permanent cache miss)
3. Feature cache stale price: `predict()` fast path persists price-patched vector back to cache
4. SCAN_MARKET_LIMIT 300→800 (was hiding 63% of markets), concurrency 3→10
5. Cold start guard: `_feature_cache_warmed` flag + model-ready wait + first-scan sync warm
6. Position guards: ArbitrageBot + MomentumBot WS reactive paths check `has_open_position()`
7. Scan timing: `sleep(max(0, interval - elapsed))` not `sleep(interval)` — was 68s cycle
8. PaperTrading idempotency: `_positions_seeded` flag prevents double cash deduction
9. StreamingPersister: prices normalized to numeric market_id before insert

**Test suite**: 17 warnings eliminated → 0 warnings. 321 tests.

**Tier 2 (5/12 implemented)**:
- Correlation IDs (scan→predict→order)
- VADER sentiment (was pre-existing)
- Wikipedia Pageviews signal (was pre-existing)
- PSI feature drift detection
- Capital canary auto-transition
- Latency path instrumentation (`_LatencyTracker` in base_bot.py)

### Session 2026-02-21 (Bug Fixes + Learning Mode)
- **Bug A**: Closed-market guard in EnsembleBot — skips markets with `active=False`, `closed=True`, or `end_date_iso` in past
- **Bug B**: Token-level order dedup (`_pending_orders` set) — prevents duplicate orders from parallel scans
- **Bug C**: SELL size=0 guard in order_gateway.py — returns early instead of phantom record
- **Learning mode**: `ENSEMBLE_MIN_CONFIDENCE` 0.65→0.45, `RISK_MAX_POSITION_SIZE_USD` 1000→100, `SCAN_MARKET_LIMIT` 800→1500

### Session 2026-02-22 (DB Pool Exhaustion Fix — THIS SESSION)
**Root causes fixed**:
- Supabase session pooler overwhelmed (59+ MaxClientsInSessionMode errors)
- Advisory lock deadlock: `database_lock.py` held semaphore slot for up to 300s while waiting for PG advisory lock → starved other services
- Startup thundering herd: all 15+ services hitting DB simultaneously on boot

**Fixes applied**:
1. **`database.py` semaphore**: Changed from `max(pool_size-2, 5)` → `max(pool_size, 3)` (matches pool exactly)
2. **`database.py` `get_raw_session()`**: New method bypassing semaphore — ONLY for advisory locks
3. **`database_lock.py`**: Advisory lock now uses `get_raw_session()` to prevent deadlock
4. **`base_engine.py`**: 2s stagger delays between service group starts (prevent thundering herd)
5. **`main.py`**: 2s stagger delay between each bot start
6. **`main.py`**: structlog.configure() moved BEFORE application imports (TeeLogger must be configured before any module-level `get_logger()` calls cache the logger)
7. **`main.py`**: `_TeeLogger` writes to both stdout (flush=True) AND `data/paper_trading.log` directly (line-buffered file handle), bypassing stdout buffering issues in subprocess
8. **`prediction_engine.py`**: `_prepare_training_data()` closes primary session before sub-queries open their own sessions
9. **`prediction_engine.py`**: `await session.rollback()` after each failed query in inference path — prevents InFailedSQLTransactionError cascade
10. **`run_paper.py`**: Created launcher script (stdout/stderr→DEVNULL, TeeLogger handles file)
11. **`.env`**: `DB_POOL_SIZE=12, DB_MAX_OVERFLOW=3` (15 total), `SCAN_MARKET_LIMIT=50`, `ENSEMBLE_SCAN_CONCURRENCY=3`

**Result**: ZERO MaxClientsInSessionMode, ZERO QueuePool timeout, ZERO InFailedSQLTransactionError. Whale trades detected. 50 tradeable markets fetched. System operational.

**Critical bug fixed (main.py recovery)**: Write tool accidentally truncated main.py from 391→100 lines (lost `_preflight_check`, `_watchdog`, `main`, `__main__` block). Recovered from `__pycache__/main.cpython-313.pyc` via marshal/dis bytecode decompilation. All 321 tests passed post-recovery.

---

## 11. Active Issues & Immediate Fixes Needed

### ISSUE 1: Ingestion Too Large (CRITICAL — fix before next restart)
**Symptom**: IngestionScheduler fires at startup (`DAILY_INGESTION_MARKETS_COUNT=3000`). At ~400ms/query Supabase latency, ingesting 3,000 markets takes **hours**. This:
- Holds DB semaphore slots for hours
- Starves feature pre-compute (can't warm model cache)
- Causes first bot scans to timeout (DB slots unavailable)

**Fix**: In `.env`, change:
```ini
DAILY_INGESTION_MARKETS_COUNT=200
DAILY_INGESTION_PRICES_MARKETS=200
```
This completes in ~5-10 minutes, freeing DB slots for normal operation.

**Longer term**: Increase to 1500 after VPS migration (direct PG, <1ms/query).

### ISSUE 2: First-Scan Timeouts (KNOWN, expected)
**Symptom**: All 4 bots hit 300s timeout on first scan (cold cache). This is expected behavior: the feature vector cache is empty on startup, so every market needs a full DB feature query.

**Status**: After the timeout, bots restart scan loop. Second and subsequent scans are fast (cache hit). NOT a blocking issue — just ugly logging.

**Fix options (optional)**:
- Reduce `SCAN_MARKET_LIMIT=20` until feature cache warms, then increase dynamically
- Or increase `BOT_SCAN_TIMEOUT_SECONDS=600` for first scan only
- Or add a "wait for feature cache warm" gate before starting bot scan loops

### ISSUE 3: Models Predict ~0.92 for Everything
**Symptom**: All 11 models output ~0.92 confidence regardless of market. Only 1 of 30 high-confidence predictions had positive edge in initial runs.

**Diagnosis**: Training data is dominated by correct predictions from professional traders → models learned "always predict correct" shortcut. Class-balanced weights partially help.

**Fix in progress**: Training feedback loop (from session 2026-02-19) will correct this over time as markets resolve and models learn from their mistakes.

**Action**: Let paper trading run, wait for markets to resolve, retrain. Check `PREDICTION_LOG_TRAINING_MAX_ROWS` and `PAPER_TRADE_TRAINING_WEIGHT` settings.

### ISSUE 4: Migration 017 Blocked
- `neg_risk`, `outcome_count` columns missing from `markets` table
- Blocks: `can_exit()` NegRisk pre-check (Tier 2 item #12)
- Fix: Run via direct psql connection (bypass pooler) — see §8 for SQL

### ISSUE 5: SCAN_MARKET_LIMIT=50
- Remote Supabase latency means only 50 markets/scan is feasible
- Will increase to 1500 after VPS migration
- Current coverage: ~7% of available markets per scan cycle

---

## 12. Tier 2 Backlog (7 items remaining)

Items 13, 14, 15, 21, 22, 23 from Tier 2 are COMPLETE (see §10). Remaining:

| # | Item | Effort | Key File | Blocker |
|---|------|--------|----------|---------|
| 12 | `can_exit()` NegRisk pre-check | ~40 lines | `order_gateway.py` | Migration 017 |
| 16 | LLM resolution clarity scoring | ~50 lines | `base_engine/features/resolution_risk.py` | None |
| 17 | Disposition effect exploitation | ~60 lines | `bots/momentum_bot.py` | None |
| 18 | VPIN toxicity detection | ~100 lines | `base_engine/features/trade_flow_analyzer.py` | None |
| 19 | Bot classification via wallet clustering | ~80 lines | `base_engine/data/whale_tracker.py` | None |
| 20 | Order flow fingerprinting | ~60 lines | `base_engine/data/whale_tracker.py` | None |

**Recommended next**: Item 16 → 17 → 18 (no blockers, high signal value).

### Item 16: LLM Resolution Clarity Scoring
Call LLM to score whether a market's question is ambiguous (high clarity = lower resolution risk). Score goes into EnsembleBot confidence adjustment. File: `resolution_risk.py`.

### Item 17: Disposition Effect Exploitation (MomentumBot)
Traders hold losers too long and sell winners too early. When price moves against them, predict reversal (mean reversion signal from disposition bias). Add to MomentumBot's signal set.

### Item 18: VPIN Toxicity Detection
Volume-synchronized Probability of Informed Trading. High VPIN → likely informed trader → fade signal. Goes into `trade_flow_analyzer.py` and is used by EnsembleBot/ArbitrageBot to reject toxic flow.

---

## 13. Long-Term Roadmap

### VPS Deploy (AWS Lightsail Dublin)
- **Why Dublin**: Polymarket CLOB is in AWS eu-west-2 (London). UK is RESTRICTED. Dublin (eu-west-1) is closest allowed location, ~2-5ms to CLOB.
- **Impact**: Direct PostgreSQL (<1ms/query), SCAN_MARKET_LIMIT → 1500, ENSEMBLE_SCAN_CONCURRENCY → 10, full ingestion in minutes not hours
- **Setup needed**: PostgreSQL 16+, PG partitioning, healthcheck loop, dashboard auth (nginx + htpasswd)

### Latency Target: WS → Decision < 15ms
- **Current**: WS event → trade decision ~100ms-25s (DB-bound)
- **Architecture**: In-memory market index (done ✅), in-memory position tracker (done ✅)
- **Remaining**: Fire-and-forget prediction log writes (async write queue), remove DB from hot path entirely

### Live Trading
- Set `SIMULATION_MODE=false`, `LIVE_TRADING=true` in `.env`
- Requires: Polygon wallet private key (`PRIVATE_KEY`), wallet address (`WALLET_ADDRESS`)
- Risk: Start with small capital ($1,000), use `CANARY_AUTO_ADVANCE` for staged rollout

### Tier 3-5 (Not Started)
From original 47-item audit, only Tier 2 is next. Tiers 3-5 not started — reconstruct from HANDOFF_2026_02_20.md if needed.

---

## 14. Test Suite

**State**: 321 tests passing, 0 warnings across 24 test files.

```powershell
# Full suite
powershell.exe -Command "cd 'C:\lockes-picks\polymarket-ai-v2'; python -m pytest tests/unit/ -v --no-cov --tb=short"

# Verification tests (MUST run after editing data/ or prediction/ code)
python -m pytest tests/unit/test_poly_data_fixes.py tests/unit/test_prediction_price_fallback.py tests/unit/test_ingestion_historical_price_flow.py -v --no-cov

# Model tests
python -m pytest tests/unit/test_model_diversity.py -v --no-cov
```

**ALWAYS run full suite before committing any changes.**

---

## 15. Known Gotchas & Quirks

1. **VPN MUST be ON** — Surfshark, any country. US/UK IPs get 403.
2. **Don't double-click main.py** — console closes on exit. Run from terminal.
3. **CLOB /book endpoint uses `token_id` param** (NOT `token`) — was a 400 error before fix.
4. **API returns strings for numbers** — always `float(value or 0)` with try/except.
5. **CatBoost + CalibratedClassifierCV**: incompatible (`__sklearn_tags__` missing). Skip `_wrap_model()` for CatBoost.
6. **`get_feature_scores()`**: divides by `n_contributors` (models with `feature_importances_`), not total models. LogisticRegression has `coef_` not `feature_importances_`.
7. **Semaphore is `pool_size=12`**: Advisory lock MUST use `get_raw_session()`. Other sessions use `get_session()`.
8. **`db.get_session()`** is a sync method returning an async context manager.
9. **Scan cycle is invisible** at INFO log level (all DEBUG). Only slow scans, errors, and trades appear.
10. **Ingestion progress IS at INFO** every 60s. If no progress logs, ingestion is stuck.
11. **`resolved_at` is NULL** for ALL markets in DB — always use `end_date_iso` fallback.
12. **`condition_id` vs numeric `id`**: Always join both. `market_prices` has mixed formats.
13. **structlog must be configured BEFORE imports** — `cache_logger_on_first_use=True` caches loggers at first call.
14. **AST parse after every code edit** — historical corrupted newlines found in multiple files.
15. **PaperTradingEngine `_positions_seeded`** flag prevents double cash deduction on double-seed (idempotent).
16. **Model cache without `feature_columns`** is rejected on load (forced retrain).
17. **Migrations 017-018 blocked** by Supabase pooler — apply via direct psql.
18. **`SCAN_MARKET_LIMIT=50`** on remote Supabase (400ms/query). DO NOT increase until VPS.
19. **First scan timeouts** are expected on cold start. Bots restart automatically.
20. **`DAILY_INGESTION_MARKETS_COUNT=3000`** is too large for Supabase — reduce to 200-500.

---

## 16. Mock Patterns for Tests

```python
# db.get_session() — sync method returning async context manager
class MockSessionCtx:
    async def __aenter__(self): return self.session
    async def __aexit__(self, *args): pass
    def __init__(self): self.session = AsyncMock()

db.get_session = MagicMock(return_value=MockSessionCtx())  # NOT async def

# db._verify_database() — async
db._verify_database = AsyncMock()

# DataIngestionService.ingest_all_markets() — calls get_events first, then get_markets
client.get_events = AsyncMock(return_value=[])
client.get_markets = AsyncMock(return_value=[...])

# LearningEngine.save_patterns_to_db() — mock entirely (deep DB interactions)
learning_engine.save_patterns_to_db = AsyncMock()

# structlog in tests — configure in tests/conftest.py to match production
structlog.configure(
    processors=[structlog.processors.KeyValueRenderer()],
    wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
    logger_factory=structlog.PrintLoggerFactory(),
)
```

---

## 17. Documentation Map

| File | Trust | Purpose |
|------|-------|---------|
| **MASTER_HANDOFF_2026_02_22.md** (this file) | ✅ LATEST | Complete state as of 2026-02-22 |
| **PROJECT_STATUS.md** | ✅ Current (2026-02-21) | Feature completion status |
| **NEW_AGENT_SUMMARY.md** | ✅ Current (2026-02-20) | Architecture + critical concepts |
| **HANDOFF_2026_02_20.md** | ✅ Historical | Step 0 + Tier 1 + Tier 2 detail |
| **HANDOFF_2026_02_19.md** | ✅ Historical | Training feedback loop + migration 017 |
| **MEMORY.md** (`~/.claude/projects/.../memory/`) | ✅ Current | Claude agent memory — mock patterns, DB facts |
| **HANDOFF_INDEX.md** | ✅ Current | Navigation index |
| `archive/` | 📦 Archived | 60+ stale docs, legacy scripts |
| `IMPLEMENTATION_PLAN_STATUS.md` | ❌ STALE | Dated 2025-02-06, DO NOT USE |

---

## QUICK START FOR NEW AGENT

```
1. READ: This file (MASTER_HANDOFF_2026_02_22.md) top to bottom
2. READ: PROJECT_STATUS.md (feature completion status)
3. UNDERSTAND: §3 Critical Concepts (side semantics, market IDs, logging levels)
4. CHECK: Is VPN on? Is PID 34124 still running?
5. CHECK log: powershell -Command "Get-Content data\paper_trading.log -Tail 30"
6. FIX FIRST: Reduce DAILY_INGESTION_MARKETS_COUNT=200 in .env (§11 Issue 1)
7. NEXT FEATURE: Tier 2 Item 16 (LLM resolution clarity) or Item 17 (Disposition effect)
8. TEST always: python -m pytest tests/unit/ -v --no-cov --tb=short (must be 321 passed, 0 warnings)
```
