# Polymarket AI V2 — Agent Handoff (2026-02-18, updated 2026-02-20)

**Read this first.** Single doc carrying vision, architecture, key files, critical concepts, known issues, and what to work on next.

---

## 1. What This Is

Python async trading system for Polymarket prediction markets. 4 active bots scan markets, generate ML predictions, check risk, and place paper trades. Currently in paper trading mode (no real money).

**Pipeline**: WebSocket/API price data → Bot scan → ML prediction (10-model ensemble) → Confidence filter → Risk check → DrawdownController → KillSwitch → OrderGateway → PaperTradingEngine → DB persist

**Current state**: Paper trading is **working** — first trades executed 2026-02-18. System runs 4 bots, 10 ML models, WebSocket streaming ~344 token prices, 321 unit tests passing (0 warnings).

---

## 2. CRITICAL CONCEPT: Polymarket Side Semantics

**This was a systemic bug across the entire codebase. Every new agent MUST understand this.**

- **YES and NO are both BUY operations** — you're buying that token
- **SELL only means closing a position** (selling tokens you hold)
- Both YES tokens and NO tokens have prices between 0 and 1
- P&L is ALWAYS `(current_price - entry_price) / entry_price` regardless of YES or NO
- Fading a cascade into YES means buying NO tokens, NOT selling
- Signal alignment uses YES/NO (which token), not BUY/SELL (direction)

**Gateway normalization**: `order_gateway.py` converts all non-"SELL" sides to "BUY" for paper trading, but passes `original_side` (YES/NO) for position tracking.

---

## 3. Environment

- **Python 3.13.3** on Windows 10, system-installed (no venv)
- **PowerShell**: `powershell.exe -Command "cd 'C:\lockes-picks\polymarket-ai-v2'; ..."`
- **VPN required**: Surfshark VPN must be ON (US/UK IPs get 403 from Polymarket API)
- **DB**: Supabase Pro ($25/mo), PostgreSQL via session pooler port 5432 (NOT 6543)
- **Run**: `python main.py` from terminal (not double-click)

---

## 4. Architecture

### Bots (all inherit `BaseBot` → `bots/base_bot.py`)
| Bot | File | Status | What It Does |
|-----|------|--------|-------------|
| EnsembleBot | `bots/ensemble_bot.py` | **Enabled** | ML-driven: 10-model prediction → confidence threshold → trade |
| ArbitrageBot | `bots/arbitrage_bot.py` | **Enabled** | Binary arb (YES+NO<1) + cross-market arb |
| MomentumBot | `bots/momentum_bot.py` | **Enabled** | Z-score mean reversion, cascade/persuasion fading |
| MirrorBot | `bots/mirror_bot.py` | **Enabled** | Copy elite trader consensus |
| CrossPlatformArbBot | `bots/cross_platform_arb_bot.py` | Disabled | Multi-exchange arb |
| OracleBot | `bots/oracle_bot.py` | Disabled | UMA oracle disputes |
| SportsBot | `bots/sports_bot.py` | Disabled | Sports market specialist |
| LLMForecasterBot | `bots/llm_forecaster_bot.py` | Disabled | LLM-based predictions |

### ML Ensemble (10 Models)
All toggleable via `MODEL_ENABLE_*` env vars. Training: walk-forward 80/20 temporal split + purge/embargo + Brier rollback gate.

| Model | Key | Notes |
|-------|-----|-------|
| RandomForest | `random_forest` | class_weight="balanced" |
| XGBoost | `xgboost` | scale_pos_weight from data |
| GradientBoosting | `gradient_boosting` | Sequential |
| LogisticRegression | `logistic_regression` | Has `coef_` not `feature_importances_` |
| ExtraTrees | `extra_trees` | Random split thresholds |
| HistGradientBoosting | `hist_gradient_boosting` | class_weight="balanced" |
| LightGBM | `lightgbm` | is_unbalance=True |
| CatBoost | `catboost` | Skips CalibratedClassifierCV (incompatible) |
| RidgeClassifier | `ridge` | Wrapped in CalibratedClassifierCV |
| KNeighborsClassifier | `knn` | Distance-weighted, k=7 |

**Training validation**: Mandatory DummyClassifier gate — if zero models beat majority-class baseline, training is rejected.

**Class balancing**: Applied at sample weight level (sklearn-style w_pos/w_neg) on top of log-volume×elite weights. All tree models also have native class balancing.

### Key Components
| Component | File | Purpose |
|-----------|------|---------|
| BaseEngine | `base_engine/base_engine.py` | 50+ component orchestrator (~1500 lines) |
| Database | `base_engine/data/database.py` | 23 SQLAlchemy ORM tables (~3400 lines) |
| PredictionEngine | `base_engine/prediction/prediction_engine.py` | ML ensemble (~1600 lines) |
| OrderGateway | `base_engine/execution/order_gateway.py` | Risk→DrawdownController→KillSwitch→paper/CLOB |
| PaperTradingEngine | `base_engine/execution/paper_trading.py` | Simulated fills with realistic slippage |
| RiskManager | `base_engine/risk/risk_manager.py` | Position limits, edge filter, CVaR gate |
| DrawdownController | `base_engine/risk/drawdown_controller.py` | Graduated: caution→restricted→halted |
| MultiLayerKillSwitch | `base_engine/risk/multi_kill_switch.py` | Emergency halt in scan loop + order pipeline |
| WebSocketManager | `base_engine/data/websocket_manager.py` | Real-time price/trade streaming |
| Settings | `config/settings.py` | All env-driven config (~610 lines, Pydantic) |
| Dashboard | `ui/dashboard.py` | Streamlit (~3500 lines) |

---

## 5. Key Files Quick Reference

```
main.py                                    — Entry point (260 lines)
config/settings.py                         — All settings (~610 lines)
base_engine/base_engine.py                 — Core orchestrator (~1500 lines)
base_engine/data/database.py               — DB schema + ORM (~3400 lines)
base_engine/prediction/prediction_engine.py — ML ensemble (~1600 lines)
base_engine/execution/order_gateway.py     — Order pipeline (risk→paper/CLOB)
base_engine/execution/paper_trading.py     — Paper trade simulation
base_engine/risk/risk_manager.py           — Position/edge/CVaR risk checks
base_engine/data/websocket_manager.py      — Real-time price/trade streaming
base_engine/data/polymarket_client.py      — API client (REST + CLOB)
bots/base_bot.py                           — Base class for all bots (430 lines)
bots/ensemble_bot.py                       — Primary ML-driven bot (529 lines)
bots/momentum_bot.py                       — Z-score + game theory fading
bots/arbitrage_bot.py                      — Binary + cross-market arb
bots/mirror_bot.py                         — Elite trader mirroring
ui/dashboard.py                            — Streamlit dashboard (~3500 lines)
tests/unit/                                — 321 tests across 24 files
.env                                       — Database URL, API keys, mode flags
data/model_cache.pkl                       — Trained model cache (delete to retrain)
```

---

## 6. Commands

```powershell
# Run the system (VPN must be ON first)
python main.py

# Run all 321 unit tests
python -m pytest tests/unit/ -v --no-cov --tb=short

# Run verification tests (after editing data/prediction code)
python -m pytest tests/unit/test_poly_data_fixes.py tests/unit/test_prediction_price_fallback.py tests/unit/test_ingestion_historical_price_flow.py -v --no-cov

# Force model retrain (delete cache, restart)
del data\model_cache.pkl
python main.py

# Run migrations
python scripts/run_migrations.py

# Dashboard
streamlit run ui/dashboard.py
```

---

## 7. Config Levers (.env)

| Setting | Default | Purpose |
|---------|---------|---------|
| SIMULATION_MODE | true | Paper trading (no real CLOB orders) |
| LIVE_TRADING | false | Must be true for real money |
| ENSEMBLE_MIN_CONFIDENCE | 0.55 | Min confidence to trade |
| MAX_POSITION_SIZE_PCT | 0.01 | Max single position as % of capital |
| RISK_MAX_POSITION_SIZE_USD | 1000 | Hard $ limit per position |
| RISK_MAX_TOTAL_EXPOSURE_USD | 10000 | Hard $ limit total exposure |
| RISK_MAX_DAILY_LOSS_USD | 2000 | Stop trading if daily loss exceeds |
| TOTAL_CAPITAL | 100000 | Paper trading starting capital |
| ENSEMBLE_SCAN_INTERVAL | 10 | Seconds between scans |
| ARB_SCAN_INTERVAL | 5 | |
| MOMENTUM_SCAN_INTERVAL | 10 | |
| MIRROR_SCAN_INTERVAL | 15 | |
| ENSEMBLE_WS_PRICE_CHANGE_THRESHOLD | 0.005 | WS reactive threshold |
| MODEL_ENABLE_* | true | Toggle individual ML models |
| SELF_TUNE_MODEL_WEIGHTS | true | Auto-learn model weights |
| SELF_TUNE_ENSEMBLE_BLEND | true | Auto-learn blend ratio |

---

## 8. Database

- **Supabase Pro** ($25/mo), session pooler port 5432
- Auto-detect: `"pooler.supabase.com" in url` → `statement_cache_size=0`
- 579 MB / 8 GB, ~1.09M market_prices, ~137k trades, ~2.7k markets
- Model cache stored locally (`data/model_cache.pkl`), NOT in DB (avoids pooler hangs)
- All resolved markets have `resolved_at = NULL` — temporal guard uses `end_date_iso` fallback
- **Migration planned**: Supabase → self-hosted VPS PostgreSQL (AWS Lightsail Dublin)

---

## 9. Training Data Pipeline

1. **Primary**: ALL trades on resolved markets (JOIN on `m.id` UNION ALL `m.condition_id`)
2. **Fallback**: Price history from `market_prices` table
3. **Temporal guard**: Excludes trades within 6 hours of resolution (uses `end_date_iso` fallback when `resolved_at` is NULL)
4. **Labels**: Binary — 1=correct prediction, 0=wrong (based on market resolution vs trade side)
5. **Sample weights**: log-volume × elite multiplier × class-balance weights
6. **Validation**: Walk-forward 80/20 temporal split, purge/embargo, Brier rollback gate, mandatory DummyClassifier baseline
7. **Cache**: `data/model_cache.pkl` — includes `feature_columns` and `best_feature_names`. Delete to force retrain.

---

## 10. Known Issues & Gotchas

### Side semantics (MOST IMPORTANT)
- YES/NO = which token to buy. SELL = close position. See Section 2.
- Signals return YES/NO direction. Flow analysis returns bullish/bearish. Map accordingly.

### API
- CLOB `/book` endpoint uses `token_id` param (NOT `token`)
- API returns strings for numeric fields — always `float(value or 0)` with try/except
- `condition_id` (hex hash like `0x339d...`) differs from DB integer `market_id`

### Models
- CatBoost lacks `__sklearn_tags__` — skip CalibratedClassifierCV wrapper
- `get_feature_scores()` divides by `n_contributors` (models with `feature_importances_`), not total models
- Feature columns MUST be in model cache — cache without them forces retrain

### Mocking (for tests)
- `db.get_session()` is sync returning async context manager — use `MagicMock(return_value=MockSessionCtx())`
- `db._verify_database()` is async — use `AsyncMock()`
- `LearningEngine.save_patterns_to_db()` — mock it out entirely

### Paper Trading
- Liquidity check is skipped in simulation mode (paper trades don't hit real CLOB)
- `original_side` parameter preserves YES/NO through BUY/SELL normalization
- Daily P&L auto-resets at UTC midnight (feeds DrawdownController)

---

## 11. Fixes Applied This Session (2026-02-18)

### Pipeline Unblock (paper trades were blocked)
1. CLOB `/book` API param: `token` → `token_id` (polymarket_client.py)
2. OrderBook tracker: empty `market_id=""` → threads `condition_id` through
3. Liquidity check: skip in SIMULATION_MODE
4. **Side mapping**: NO→SELL was wrong. Both YES/NO are BUY. (order_gateway.py)

### Comprehensive Logic Audit (17 issues found, all fixed)
- **1A**: MomentumBot P&L inverted for NO positions → unified formula
- **1B**: Advanced orders exit side → all exits are SELL
- **1C**: Cascade fade YES/NO→BUY/SELL conflation → fixed to use YES/NO tokens
- **1C ext**: Signal alignment in base_bot.py → uses YES/NO not BUY/SELL
- **2A**: Paper trading position side → `original_side` parameter added
- **3B**: Realized PnL null guard → defensive `avg_price or 0.0`
- **Model accuracy**: class_weight="balanced", scale_pos_weight, mandatory DummyClassifier gate
- **Temporal guard**: end_date_iso fallback for NULL resolved_at

### Prior Session Fixes (2026-02-17)
- DrawdownController wired into OrderGateway (graduated halt)
- MultiLayerKillSwitch in scan loop + order pipeline
- DummyClassifier baseline in training validation
- MirrorBot elite fetches parallelized (7s→1-2s)
- CorrelationRiskManager CVaR gate ($200 cap)
- Ridge + KNN models added (8→10 ensemble)
- `realized_pnl_today` tracking for DrawdownController
- Feature columns saved in model cache (was causing zero predictions)

### Session Fixes (2026-02-20) — Millisecond Scan Architecture (9 silent bugs fixed)
1. **Dual-key market index**: `_market_index_by_cid` added — WS reactive trades now fire (condition_id lookup was always failing)
2. **WS cache key**: `update_cached_price()` now uses numeric id (was using condition_id → permanent cache miss)
3. **Feature cache stale price**: `predict()` fast path now persists price-patched vector back to cache
4. **Scan limits raised**: `SCAN_MARKET_LIMIT` 300→800 (was hiding 63% of markets), `ENSEMBLE_SCAN_CONCURRENCY` 3→10
5. **Cold start guard**: `_feature_cache_warmed` flag + model-ready wait + first-scan sync warm (was DB-storming on startup)
6. **Position guards**: ArbitrageBot + MomentumBot WS reactive paths now check `has_open_position()` before firing
7. **Scan timing**: `sleep(max(0, interval - elapsed))` not `sleep(interval)` — was 68s cycle instead of 60s
8. **PaperTrading idempotency**: `_positions_seeded` flag prevents double cash deduction on double-seed
9. **StreamingPersister**: Price market_ids resolved to numeric before insert (was storing mixed condition_id/numeric)

### Session Fixes (2026-02-20) — 17 Test Warnings Eliminated (0 warnings now)
- Removed `StackInfoRenderer` from structlog processor chain in `main.py` (redundant with ConsoleRenderer)
- Removed `exc_info=True` from `ErrorTracker.capture_exception()` (structured fields carry the info)
- Raised `max_iter` 100/200→500 + added `early_stopping=True` in `test_mlp_model.py` (undertrained models in tests)
- `pytest.ini` filterwarnings: websockets.legacy (third-party), LightGBM feature names (already suppressed in production)
- `tests/conftest.py`: structlog.configure() to match production config

### Session Fixes (2026-02-20) — Step 0 + Tier 1 (from master 47-item audit)
- **Step 0** (5 items): vaderSentiment, prediction_timestamp, AlertingSystem wiring, maker/taker fee sim, migration 018
- **Tier 1** (9 items): Longshot bias filter, alpha decay, DDM+EDDM drift detection, ADWIN formalization, maker-taker fee preference, adverse selection gate, sqrt market impact, Anthropic prompt caching, auto-alerts

---

## 12. What Needs Work Next (Priority Order)

### Immediate
1. **Delete model cache + retrain**: `del data\model_cache.pkl` → restart. Picks up all model improvements.
2. **Run resolution backfill**: `python scripts/backfill_market_resolution.py` → better training labels
3. **Monitor paper trades**: Run system for 24h+, check trade profitability

### Next: Tier 2 (12 items — see HANDOFF_2026_02_20.md for full detail)
| # | Item | Blocker |
|---|------|---------|
| 12 | `can_exit()` NegRisk pre-check | Needs migration 017 (neg_risk column) |
| 13 | Correlation IDs (scan→predict→order) | None |
| 14 | VADER sentiment scoring | None (vaderSentiment already installed) |
| 15 | Wikipedia Pageviews signal | None |
| 16 | LLM resolution clarity scoring | None |
| 17 | Disposition effect exploitation | None |
| 18 | VPIN toxicity detection | None |
| 19 | Bot classification via wallet clustering | None |
| 20 | Order flow fingerprinting | None |
| 21 | PSI for feature drift | None |
| 22 | Capital-based canary deployment | None |
| 23 | Latency path instrumentation | None |

### Blockers
- **Migrations 017 + 018**: Blocked by Supabase pooler. Apply directly on VPS after migration (or via psql direct connection bypassing pooler).

### Long-term
- **VPS deploy**: AWS Lightsail Dublin (~2-5ms to London CLOB). Dashboard auth, health monitoring, PG partitioning.
- **PerformanceTracker**: Wire `record_trade_outcome()` into resolution backfill
- **Shared FeatureComputer** (GAP 7): Single service with `as_of` timestamp for backtest=live parity
- **Regime detection** (GAP 8/10): Richer market regime features

---

## 13. Redundancies & Confusion Points

- **"Autonomy" vs "elevation"**: Autonomy = self-learning weights/blend/features (in code). Elevation = broader backlog in old docs (some implemented, some not).
- **"Paper trading" = "SIMULATION_MODE"**: Same thing, different naming in docs vs config.
- **NEW_AGENT_SUMMARY.md vs MEMORY.md**: This file is the repo-level handoff. MEMORY.md is the Claude agent persistent memory (at `~/.claude/projects/.../memory/MEMORY.md`). Keep both in sync.
- **IMPLEMENTATION_PLAN_STATUS.md**: Historical only (dated 2025-02-06). Don't trust it for current status.
- **DEEP_CLEAN_AND_ELEVATION_ADVISORY.md**: Old elevation roadmap. Many items now implemented. Check MEMORY.md "Already Implemented" section before working on anything listed there.
- **`docs/` folder**: Contains useful but potentially stale docs. Always verify against actual code.

---

## 14. Test Commands

```powershell
# Full suite (321 tests, ~40s, 0 warnings)
powershell.exe -Command "cd 'C:\lockes-picks\polymarket-ai-v2'; python -m pytest tests/unit/ -v --no-cov --tb=short"

# Verification tests (must pass after data/prediction edits)
python -m pytest tests/unit/test_poly_data_fixes.py tests/unit/test_prediction_price_fallback.py tests/unit/test_ingestion_historical_price_flow.py -v --no-cov

# Model diversity tests
python -m pytest tests/unit/test_model_diversity.py -v --no-cov
```
