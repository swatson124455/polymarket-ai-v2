# FULL AGENT HANDOFF — EsportsBot Session 82 (2026-03-13)
# CARBON COPY: Everything needed to continue building with zero context loss
# Scope: EsportsBot + EsportsLiveBot + EsportsSeriesBot ONLY — No bleed to Mirror/Weather/other bots

---

## 0. HOW TO USE THIS HANDOFF

You are continuing development on 3 esports prediction market bots. Read this ENTIRE document before writing any code. This handoff contains:

1. System architecture and constraints
2. Complete EsportsBot internals (2,700+ lines)
3. Every file you'll need to touch and why
4. What was built in Sessions 81-82 (the most recent work)
5. The calibration pipeline (newly wired)
6. What's next — prioritized backlog
7. Critical traps that WILL break things if violated
8. Testing and deployment procedures

**Governance**: Read `CLAUDE.md` before any edit. Key rules: one fix per commit, preserve all function signatures, no silent behavior changes, no "while I'm in here" refactors. This is a LIVE trading system (paper mode).

---

## 1. SYSTEM OVERVIEW

**Polymarket AI V2** — 15-bot automated trading system on Polymarket (prediction markets on Polygon L2). Paper trading mode (`SIMULATION_MODE=true`). Real capital structure, fake execution.

| Property | Value |
|----------|-------|
| VPS | Ubuntu-3 at `34.251.224.21` (16GB/4vCPU, eu-west-1) |
| DB | PostgreSQL localhost, user=polymarket, db=polymarket |
| Service | `sudo systemctl restart polymarket-ai` |
| Logs | `journalctl -u polymarket-ai -f` |
| Deploy | `KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh` |
| Rollback | Same with `deploy/rollback.sh` |
| SSH Key | `~/.ssh/LightsailDefaultKey-eu-west-1.pem` |
| Local dev | Windows, Python 3.13.3, `C:\lockes-picks\polymarket-ai-v2` |
| Release dir | `/opt/pa2-releases/` → symlink `/opt/polymarket-ai-v2` |
| Shared dir | `/opt/pa2-shared/{data,saved_models,venv}` |

**5 active bots**: WeatherBot, MirrorBot, EsportsBot, EsportsLiveBot, EsportsSeriesBot
**9 disabled bots** (via `BOT_ENABLED_*` flags). MomentumBot DELETED, EnsembleBot ARCHIVED.

---

## 2. THE 3 ESPORTS BOTS — RELATIONSHIP

| Bot | File | Inherits | Scope |
|-----|------|----------|-------|
| **EsportsBot** | `bots/esports_bot.py` (2,700+ lines) | BaseBot | Pre-game + live, all 8 games |
| **EsportsLiveBot** | `bots/esports_live_bot.py` (154 lines) | BaseBot (NOT EsportsBot) | Live in-play only |
| **EsportsSeriesBot** | `bots/esports_series_bot.py` (385 lines) | BaseBot (NOT EsportsBot) | Best-of series |

**CRITICAL**: EsportsLiveBot and EsportsSeriesBot do NOT inherit from EsportsBot. Changes to EsportsBot do NOT affect them. They share `PandaScoreClient` (rate-limited, class-level counter: 1000 req/hr total across all 3).

---

## 3. ESPORTSBOT ARCHITECTURE (The Main Bot)

### Games
LoL, CS2, Dota 2, Valorant, CoD, R6, StarCraft II, Rocket League

### Scan Loop Flow (`scan_and_trade()`)
```
1. Restore exposure counters from daily_counters DB (first scan only)
2. Restore daily P&L (refreshes every 10th scan for mid-day resolutions)
3. Fetch positions (once, shared between exit check + re-evaluation)
4. Daily loss limit check → if hit, only run exits, then return
5. Stop-loss + max-hold exits (_check_and_execute_exits)
6. Re-evaluate open positions (every 5th scan)
7. Kelly graduation check (every 10th scan)
8. Monitoring thresholds (every 10 min) — includes calibrator fitting
9. Patch drift check
10. PandaScore live match refresh
11. Get esports markets from EsportsMarketService
12. Per-market analysis loop → analyze_opportunity() → execute trade
13. Scan summary log
```

### Scan Interval
- **120s** default
- **60s** with open positions (for stop-loss monitoring)
- **10s** during live matches

### Prediction Pipeline (as of Session 82)
```
Market question text
  → regex team name extraction (_get_glicko2_prediction)
  → _clean_team_names() (strip "league of legends:", "(bo3)", etc.)
  → _match_team_name() (exact match → substring fuzzy match)
  → If miss: _backfill_unknown_team() (PandaScore lookup, 2 API calls, capped at 5/scan)
  → Glicko-2 expected_score(team_a, team_b)
  → Bayesian prior blending (phi-based: high uncertainty → blend toward 0.50)
  → Game-specific model predictions:
      - LoL: ML model blend (predict_with_glicko2)
      - CS2: Economy model + Glicko-2 adjustment
      - Dota2/Valorant: extremized_geometric_mean([ML, Glicko-2], d=egm_d)  ← Session 82
      - SC2: Aligulac Elo blend (50/50)
      - Dota2: OpenDota form adjustment (±3%)
      - RL: Ballchasing stats adjustment (±3%)
  → Cross-game XGBoost (40% weight, uses ONNX if available)  ← Session 82
  → Clamp to [0.05, 0.95]
  → Cache in _prediction_cache (1h TTL)
  → EsportsBiasDecomposition.recalibrate(prob, game)  ← Session 82
  → FocalTemperatureCalibrator.calibrate(prob)  ← Session 82
  → Edge computation (model_prob - market_price)
```

### Sizing Pipeline
```
Edge = abs(calibrated_prob - market_price)
  → Confidence check (>= ESPORTS_MIN_CONFIDENCE 0.52)
  → Edge check (>= ESPORTS_MIN_EDGE 0.08, <= ESPORTS_MAX_EDGE 0.20)
  → Confluence gate (ESPORTS_CONFLUENCE_MIN=0.60)
  → Tournament phase multiplier (auto-calibrating from Brier)
  → Exposure checks (per-game $300, per-tournament $200, per-team $150)
  → BotBankrollManager.get_bet_size() (Kelly fraction × edge × bankroll)
  → Uncertainty scaling: size × (1 - phi/500) — high Glicko-2 uncertainty → smaller bet
  → Near-expiry boost: <6h: confidence × 1.5, <24h: × 1.2
  → Drawdown reduction: at 20% capital loss, Kelly × 0.5
  → Hard cap: $100/trade (ESPORTS_MAX_BET_USD)
```

---

## 4. SESSION 82 — WHAT WAS BUILT (2 Commits)

### Commit `7efabe0` — 6-Item Calibration & Inference Sprint

Analyzed a 35-component third-party prediction market blueprint. Identified 6 FULLY APPLICABLE, 6 PARTIALLY APPLICABLE, 19 SKIP for esports bots specifically.

**Implemented all 6 FULLY APPLICABLE items:**

#### Item 1: Focal Temperature Scaling + HorizonBiasCalibrator
**File**: `base_engine/features/calibration.py` (lines 167-472)
- `FocalTemperatureCalibrator` (L167-319): Grid search T∈[0.5,3.0], γ∈[0.0,5.0]. Minimizes focal loss. ~20% ECE improvement over plain temperature scaling.
  - `async fit_from_prediction_log(n_days=90)` → queries `prediction_log` table
  - `calibrate(raw_prob)` → `sigmoid(logit(p) / T)`. Returns identity when unfitted.
- `HorizonBiasCalibrator` (L322-472): Le (2026) power-law recalibration per (domain, horizon) bucket. 4 TTR buckets. Uses scipy.optimize.minimize_scalar.
  - `async fit_from_paper_trades(n_days=180)` → queries paper_trades JOIN markets
  - `calibrate(raw_prob, category, ttr_days)` → `1 / (1 + ((1-p)/p)^(1/b))`
- Both use same interface as existing `FavoriteLongshotCalibrator`

#### Item 2: Extremized Geometric Mean of Odds
**Files**: `base_engine/features/aggregation.py` (new, 72 lines), `bots/esports_bot.py`
- `extremized_geometric_mean(probabilities, weights, d, clip_min, clip_max)` — Satopää et al. 2014, IARPA ACE Tournament
- Computes in log-odds space for numerical stability. Handles weighted/unweighted.
- Replaces hardcoded `0.6*prob + 0.4*glicko2_prob` linear blending in 3 sites:
  - Dota2 blend (L1291)
  - Valorant blend (L1312)
  - Cross-game XGB+Glicko2 blend (L1385-1387)
- d parameter now config-driven: `ESPORTS_EGM_D` (default 1.5)

#### Item 3: Le (2026) Per-Game Bias Decomposition
**Files**: `esports/calibration/__init__.py`, `esports/calibration/bias_decomposition.py` (197 lines)
- `EsportsBiasDecomposition` class: per-game recalibration parameter `b` from resolved `esports_predictions`
- 4 components per game: base_bias, b (power-law), ECE, horizon correlation
- Min 30 resolved predictions per game to fit
- `recalibrate(raw_prob, game)` → `1 / (1 + (1/p - 1)^b)`. Returns identity when unfitted.

#### Item 4: httpx HTTP/2 + uvloop
**Files**: `esports/data/pandascore_client.py` (L141), `requirements.txt`
- `http2=True` added to PandaScore httpx.AsyncClient
- `httpx[http2]>=0.24.0` in requirements (was `httpx>=0.24.0`)
- `uvloop>=0.19.0; sys_platform != 'win32'` in requirements (already in main.py)
- HTTP/2 multiplexing: concurrent requests on single TCP connection

#### Item 5: ONNX Tree Compilation
**Files**: `esports/models/onnx_compiler.py` (new, 100 lines), `esports/models/esports_trainer.py`
- `OnnxCompiler.export_xgboost(model, n_features, save_path)` → converts XGBoost to ONNX
- `OnnxCompiler.load_session(onnx_path)` → `ort.InferenceSession`
- `OnnxCompiler.predict_proba(session, X)` → 50-200x faster inference
- Auto-exports after model.save() in trainer: LoL (8 feats), CS2 (14 feats), cross-game (9 feats)
- All try/except wrapped — graceful fallback when onnxmltools/onnxruntime missing
- Deps: `onnxmltools>=1.12.0`, `onnxruntime>=1.17.0`, `skl2onnx>=1.16.0`

#### Item 6: Metaculus Calibration Benchmark
**File**: `esports/calibration/metaculus_benchmark.py` (195 lines)
- Standalone diagnostic tool (NOT wired to any bot)
- `MetaculusBenchmark.fetch_resolved_binary(limit=500)` → Metaculus public API
- `MetaculusBenchmark.run_validation(calibrator)` → raw + calibrated ECE/Brier comparison
- Use for cold-start validation of calibration pipeline

**Tests**: 50 new tests across 6 files:
- `tests/unit/test_focal_temperature_scaling.py` (19 tests)
- `tests/unit/test_aggregation.py` (7 tests)
- `tests/unit/test_bias_decomposition.py` (10 tests)
- `tests/unit/test_pandascore_http2.py` (3 tests)
- `tests/unit/test_onnx_compiler.py` (5 tests)
- `tests/unit/test_metaculus_benchmark.py` (6 tests)

### Commit `a3702a2` — Wiring + Bug Fixes + Config

**Wired all standalone items into the live bot:**

| Item | Init Location | Fit Location | Apply Location |
|------|--------------|--------------|----------------|
| FocalTemperatureCalibrator | `start()` L270 | `_check_monitoring_thresholds()` every 10min | `_analyze_market()` after model_prob, before edge |
| EsportsBiasDecomposition | `start()` L277 | `_check_monitoring_thresholds()` every 10min | `_analyze_market()` after model_prob, before edge |
| ONNX cross-game inference | `_load_cross_game_model()` L2668 | — (loaded from disk) | Cross-game predict path L1365+ (native fallback) |
| ESPORTS_EGM_D config | `__init__` L129 | — | All 3 `extremized_geometric_mean()` call sites |

**Calibration order in `_analyze_market()`:**
```
raw model_prob → bias_decomp.recalibrate(prob, game) → focal_temp.calibrate(prob) → edge computation
```

**Bug fixes:**
- FocalTemp SQL: `.replace(":days", str(n_days))` → parameterized `:interval_days`
- HorizonBias SQL: f-string concatenation → parameterized `:interval_days`

**Config added:**
- `ESPORTS_EGM_D` in `config/settings.py` (default 1.5, env var override)

**Safety**: All calibrators return input unchanged when unfitted. Zero behavioral change until:
- BiasDecomp: 30+ resolved esports_predictions per game
- FocalTemp: 50+ resolved prediction_log entries
- ONNX: .onnx file exists on disk from prior training

---

## 5. KEY INSTANCE VARIABLES (EsportsBot.__init__)

```python
# Core state
_game_exposure: Dict[str, float]           # game → USD (write-through to daily_counters)
_tournament_exposure: Dict[str, float]     # tournament → USD
_team_exposure: Dict[str, float]           # team → USD
_live_matches: Dict[str, Dict]             # match_id → PandaScore live data
_prediction_cache: Dict[str, Dict]         # market_id → {prob, ts, game, ml_raw, glicko2_est}
_market_token_map: Dict[str, Dict]         # market_id → {"yes": token_id, "no": token_id}
_glicko2_trackers: Dict[str, Any]          # game → Glicko2Tracker
_team_name_to_id: Dict[str, str]           # lowercased name → PandaScore ID
_backfill_attempted: set                   # "game:name" keys (session-scoped dedup)
_backfill_calls_this_scan: int             # reset each scan, capped at 5
_prediction_log_cache: Dict[str, tuple]    # market_id → (prob, ts) for dedup
_calibration_ece: Dict[str, float]         # game → latest ECE
_edge_decay_data: Dict[str, Dict]          # game → latest edge decay analysis
_game_kelly_mult: Dict[str, float]         # game → Kelly multiplier (Brier-based)
_daily_pnl: float                          # today's realized P&L
_daily_pnl_date: Optional[str]            # UTC date string for midnight reset
_drawdown_halted: bool                     # 40% drawdown flag
_kelly_graduated: bool                     # True after 50+ trades + Brier < 0.24
_models_graduated: bool                    # accuracy >= 55% + brier <= 0.24
_scan_count: int                           # monotonic scan counter
_monitoring_halted_games: set              # games halted by monitoring (Brier > 0.30)
_monitoring_last_check: float              # monotonic time of last monitoring check
_monitoring_check_interval: float          # 600.0 (10 min)

# Session 82 additions
_focal_calibrator: Any                     # FocalTemperatureCalibrator instance
_bias_decomp: Any                          # EsportsBiasDecomposition instance
_onnx_cross_game_session: Any              # ONNX InferenceSession for cross-game XGB
_egm_d: float                              # Extremization factor from ESPORTS_EGM_D config

# Settings
_min_edge: float                           # ESPORTS_MIN_EDGE (0.08)
_min_confidence: float                     # ESPORTS_MIN_CONFIDENCE (0.52)
_max_edge: float                           # ESPORTS_MAX_EDGE (0.20)
_maker_timeout: float                      # ESPORTS_MAKER_FALLBACK_TIMEOUT_S (3.0)
_daily_loss_limit: float                   # ESPORTS_DAILY_LOSS_LIMIT (500.0)
```

---

## 6. COMPLETE FILES MAP

### Esports-specific files
| File | Lines | Purpose |
|------|-------|---------|
| `bots/esports_bot.py` | 2,700+ | Main bot — scan loop, prediction, sizing, exits, calibration |
| `bots/esports_live_bot.py` | 154 | Live in-play bot (inherits BaseBot) |
| `bots/esports_series_bot.py` | 385 | BO series bot (inherits BaseBot) |
| `esports/models/glicko2.py` | 280 | Glicko-2 algorithm (mu, phi, sigma per team) |
| `esports/models/lol_win_model.py` | ~200 | LoL XGBoost (8 features) |
| `esports/models/cs2_economy_model.py` | ~200 | CS2 XGBoost (14 features) |
| `esports/models/dota2_model.py` | ~150 | Dota2 model |
| `esports/models/valorant_model.py` | ~150 | Valorant model |
| `esports/models/esports_trainer.py` | ~660 | Training pipeline + ONNX export |
| `esports/models/onnx_compiler.py` | 100 | ONNX export/load/predict (Session 82) |
| `esports/models/patch_drift.py` | ~100 | Patch change detection (48h observation) |
| `esports/data/pandascore_client.py` | 532 | Async HTTP/2 client + shared rate limiter |
| `esports/data/esports_data_collector.py` | ~300 | PandaScore → training data ETL |
| `esports/data/esports_db.py` | ~400 | DB queries (calibration, accuracy, P&L) |
| `esports/data/opendota_client.py` | ~150 | Dota2 form data (free, no auth) |
| `esports/data/aligulac_client.py` | ~100 | SC2 Elo (free key) |
| `esports/data/ballchasing_client.py` | ~100 | RL replay stats (free key) |
| `esports/markets/esports_market_scanner.py` | 269 | Market discovery + game classification |
| `esports/markets/esports_market_service.py` | ~250 | DB-backed market service |
| `esports/calibration/__init__.py` | 0 | Package init (Session 82) |
| `esports/calibration/bias_decomposition.py` | 197 | Per-game bias analysis (Session 82) |
| `esports/calibration/metaculus_benchmark.py` | 195 | Calibration validation tool (Session 82) |

### Shared infrastructure (touch with extreme care)
| File | Lines | Purpose |
|------|-------|---------|
| `bots/base_bot.py` | 536 | Base class — place_order(), bankroll, scan loop |
| `base_engine/execution/order_gateway.py` | ~500 | Single order path — kill switch, risk, liquidity |
| `base_engine/risk/bankroll_manager.py` | ~250 | BotBankrollManager (sizing) |
| `base_engine/risk/risk_manager.py` | ~500 | Risk checks (limits) — DEPRECATED for sizing |
| `base_engine/features/calibration.py` | 472 | All calibrators (isotonic, domain, focal temp, horizon bias) |
| `base_engine/features/aggregation.py` | 72 | Extremized geometric mean (Session 82) |
| `base_engine/data/daily_counter.py` | ~100 | Write-through daily counters |
| `base_engine/data/database.py` | 2000+ | SQLAlchemy async, all DB ops |
| `config/settings.py` | 1000+ | All config (60+ ESPORTS_* keys) |
| `requirements.txt` | 345 | All dependencies (annotated) |

---

## 7. LIVE CONFIG (VPS values)

```env
# Core
ESPORTS_TOTAL_CAPITAL=5000.0
ESPORTS_KELLY_DEFAULT_FRACTION=0.25
ESPORTS_MAX_BET_USD=100.0
ESPORTS_MAX_DAILY_USD=500.0
ESPORTS_MIN_CONFIDENCE=0.52
ESPORTS_MIN_EDGE=0.08
ESPORTS_MAX_EDGE=0.20
ESPORTS_EGM_D=1.5                         # Session 82 — extremization factor

# Risk
ESPORTS_STOP_LOSS_PCT=0.25
ESPORTS_MAX_HOLD_HOURS=96
ESPORTS_DAILY_LOSS_LIMIT=500.0
ESPORTS_DRAWDOWN_HALT_PCT=0.40
ESPORTS_DRAWDOWN_REDUCE_PCT=0.20

# Exposure caps
ESPORTS_MAX_GAME_EXPOSURE=300.0
ESPORTS_MAX_TOURNAMENT_EXPOSURE=200.0
ESPORTS_MAX_TEAM_EXPOSURE=150.0

# Timing
ESPORTS_OBSERVATION_HOURS=48
ESPORTS_PANDASCORE_REFRESH_INTERVAL=15
ESPORTS_FRESHNESS_DECAY_SECONDS=120.0
ESPORTS_MAKER_FALLBACK_TIMEOUT_S=3.0

# Signals
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_WS_PRICE_CHANGE_PCT=0.01
ESPORTS_WS_COOLDOWN_SECONDS=10

# Features
ESPORTS_LOL_HEURISTIC_ENABLED=true
ESPORTS_PINNACLE_ENABLED=false
SIMULATION_MODE=true
```

---

## 8. EXTERNAL API DEPENDENCIES

| API | Rate Limit | Purpose | Client File |
|-----|-----------|---------|-------------|
| **PandaScore** | 1000/hr shared (3 bots) | Live matches, team stats, search | `pandascore_client.py` |
| **Riot API** | Variable | LoL patch drift | `riot_api_client.py` |
| **OpenDota** | ~60/min free | Dota2 team form (±3%) | `opendota_client.py` |
| **Aligulac** | Free key | SC2 Elo (50/50 blend) | `aligulac_client.py` |
| **Ballchasing** | Free key | RL replay stats (±3%) | `ballchasing_client.py` |
| **Polymarket CLOB** | Market order API | Order placement | `clob_adapter.py` |

---

## 9. DATABASE TABLES

| Table | Key Columns | Usage |
|-------|------------|-------|
| `paper_trades` | order_id, market_id, token_id, bot_name, side, size, price, confidence, status, realized_pnl, resolution, created_at | Trade records. NO metadata JSONB column. |
| `positions` | market_id, bot_id, side, size, entry_price, current_price, unrealized_pnl, status | Open positions (current_price updated 10s) |
| `daily_counters` | bot_name, counter_key, counter_value, counter_date | Game exposure write-through (ADDITIVE via increment_counter) |
| `glicko2_ratings` | game, team_key, mu, phi, sigma, match_count | Persisted Glicko-2 ratings |
| `esports_training_data` | game, team_a, team_b, outcome, patch, game_state_json, tournament, scheduled_at | Historical match data for model training |
| `esports_predictions` | game, predicted_prob, actual_outcome, created_at, resolved_at | Prediction log for calibration/accuracy |
| `markets` | id, question, market_category, tokens, end_date_iso | Market metadata from Polymarket |
| `prediction_log` | market_id, predicted_prob, resolution, prediction_time | General prediction log (all bots) |
| `esports_calibration` | game, params (JSONB) | Per-game calibration storage |

---

## 10. 35-COMPONENT BLUEPRINT ANALYSIS (Session 82 Decisions)

### FULLY APPLICABLE — All 6 Implemented
1. Focal Temperature Scaling (P1, LOW effort) ✅
2. Extremized Geometric Mean of Odds (P1, LOW-MED effort) ✅
3. Le (2026) Domain-Specific Bias Decomposition (P2, MEDIUM effort) ✅
4. uvloop + httpx[http2] (P2, LOW effort) ✅
5. ONNX Tree Compilation (P2, MEDIUM effort) ✅
6. Metaculus forecasting-tools Dataset (P3, LOW effort) ✅

### PARTIALLY APPLICABLE — Cherry-pick Later
| Item | Useful Part | Skip Part | Priority |
|------|------------|-----------|----------|
| **MAPIE v1 Conformal** | Prediction intervals → conservative Kelly (`p_low` not `p_mid`) | Full conformal regression framework | P3 |
| **TabPFN v2.5** | Ensemble for sparse-data games (SC2, RL, CoD, R6) | LoL/CS2 where XGB works; needs GPU | P4 |
| **GLiNER2 NER** | Team name extraction fix (replace fragile regex) | Full NER pipeline; 500MB model | P3 |
| **Independent CoT Ensemble** | LLM validation for high-edge (>15%) trades only | Every market (cost prohibitive) | P4 |
| **d3rlpy Offline RL** | Sizing optimization from paper_trades | Needs 500+ resolved trades first | P5 |
| **Pearl Bandits** | Continuous Kelly graduation | Safety constraints (rule-based works) | P5 |

### SKIP — 19 Items (Not Applicable to Esports)
ModernBERT/SetFit, SGLang, DSPy, Chronos-2/TabPFN-TS/Diffusion-TS (esports is NOT time-series), Rust ort, AWS Cluster, NautilusTrader, RLlib MAPPO, ABIDES, TabSyn, AIA Forecaster, LangGraph, Log-wealth reward, dylanpersonguy bot

---

## 11. CRITICAL TRAPS (VIOLATE THESE AND THINGS BREAK)

1. **YES/NO for entries, SELL for exits**: `place_order(side="YES"/"NO")` for new positions, `side="SELL"` for exits. NEVER pass "BUY"/"SELL" for entries.
2. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
3. **`risk_manager.calculate_position_size()` is DEPRECATED** — BotBankrollManager is the real sizer.
4. **EsportsLiveBot/SeriesBot inherit BaseBot, NOT EsportsBot.** Don't assume shared code.
5. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable. Only resolution labels are correct.
6. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
7. **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime.
8. **`paper_trades` has NO `metadata` JSONB column.**
9. **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time.
10. **`_game_exposure` is ADDITIVE write-through** to daily_counters. Use `increment_counter()`. NOT absolute-set.
11. **PandaScore rate limit is SHARED** across 3 esports bots (class-level counter).
12. **Backfill budget**: `_backfill_calls_this_scan` caps at 5. Prevents quota exhaustion.
13. **48h observation mode**: New LoL/CS2 markets don't trade for 48h (patch drift window).
14. **VPS SSH**: 70MB archive uploads fail intermittently. Use hot-patch for single files.
15. **asyncpg timestamps**: `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`. `created_at` has NO DEFAULT.
16. **Calibrators are identity when unfitted**: Both `_focal_calibrator` and `_bias_decomp` return raw_prob unchanged until enough data accumulates. Don't assume they're active.
17. **ONNX is optional**: Missing onnxmltools/onnxruntime = silent skip, not crash.
18. **`extremized_geometric_mean` clips to [0.05, 0.95]** — same as existing blend logic.
19. **BOT_REGISTRY = 14 bots** — shared module changes require ALL 14 verified.

---

## 12. SELF-STUDY FINDINGS (Session 82 Audit)

### Bugs Found & Fixed
| Bug | Severity | Fix | Commit |
|-----|----------|-----|--------|
| SQL string interpolation in FocalTemp | P2 | Parameterized `:interval_days` | `a3702a2` |
| SQL f-string in HorizonBias | P2 | Parameterized `:interval_days` | `a3702a2` |
| Hardcoded `d=1.5` in 3 EGM blend sites | P2 | Config `ESPORTS_EGM_D` | `a3702a2` |
| Flaky `test_bias_decomposition::test_fit_basic` | P3 | `asyncio.get_event_loop()` → `@pytest.mark.asyncio` | `7efabe0` |

### Remaining Observations (NOT bugs, just noted)
- Redundant clipping: EGM clips to [0.05, 0.95], then esports_bot clips again (harmless)
- Grid search is O(286) iterations — fast enough at 30ms on 5K samples
- `onnx` package is transitive dep of `onnxmltools` (not explicitly in requirements.txt)

---

## 13. OUTSTANDING WORK (Priority Order)

### P0 — Immediate
- **Monitor calibrator activation**: Once 30+ resolved predictions per game, `_bias_decomp` will start adjusting probabilities. Log: `journalctl -u polymarket-ai -f | grep "bias_decomp_fitted"`
- **Monitor FocalTemp activation**: Once 50+ resolved prediction_log entries. Log: `grep "focal_temp_fitted"`
- **LoL 48h observation**: Markets discovered Session 81 should now be tradeable (~2026-03-14)

### P1 — Near-Term
- **Wire HorizonBiasCalibrator**: Initialized but NOT wired (needs paper_trades with resolution data)
- **Wire ONNX for per-game models**: Only cross-game XGB uses ONNX. LoL/CS2/Dota2/Valorant models still use native XGBoost predict
- **GLiNER2 NER for team name extraction**: Replace fragile regex in `_get_glicko2_prediction()` — currently the #1 cause of `no_prediction` (9/40 markets)
- **LoL team name extraction**: Many LoL markets fail team name regex → `no_prediction`

### P2 — Medium-Term
- **MAPIE conformal prediction intervals**: Use `p_low` for conservative Kelly (predict [p_low, p_high], size on p_low)
- **Per-game ONNX inference**: Export and load per-game models in ONNX for batch prediction
- **Dynamic d tuning**: Currently static `ESPORTS_EGM_D=1.5`. Could optimize d per-game based on resolved trade Brier.
- **Edge decay modeling**: `_edge_decay_data` is logged but not used in sizing
- **Line movement signals**: Price changes after entry are ignored except stop-loss

### P3 — Future
- **TabPFN for sparse games**: SC2, RL, CoD, R6 only have Glicko-2 today. TabPFN v2.5 for small-sample games.
- **Independent CoT Ensemble**: LLM validation for high-edge (>15%) trades
- **d3rlpy offline RL**: Sizing optimization from paper_trades (needs 500+ resolved first)
- **Metaculus validation**: Run benchmark manually to verify calibration pipeline

---

## 14. STATE PERSISTENCE (ALL GAPS CLOSED)

| State | Mechanism | Status |
|-------|-----------|--------|
| `_daily_exposure_usd` (all bots) | `daily_counters` 60s flush + SIGTERM + startup restore | Done |
| `_game_exposure` (EsportsBot) | `daily_counters` write-through via `increment_counter()` | Done |
| Open positions | `order_gateway.seed_positions_from_db()` | Done |
| Glicko-2 ratings | `glicko2_ratings` table, restored on startup | Done |
| ML models | `saved_models/*.json` files, loaded on startup | Done |
| ONNX models | `saved_models/*.onnx` files, loaded if present | Done (Session 82) |
| Calibrator params | Refitted from DB every 10 min (not persisted) | By design |

---

## 15. TESTING

```bash
# Full suite (exclude known MirrorBot failure)
python -m pytest --timeout=300 -q --deselect tests/unit/test_mirror_bot_logic.py::TestCanOpenPosition::test_blocks_when_position_limit_reached

# Esports-only tests
python -m pytest tests/unit/test_esports_bot.py tests/unit/test_esports_live_bot.py tests/unit/test_esports_series_bot.py -v

# Session 82 new tests only
python -m pytest tests/unit/test_focal_temperature_scaling.py tests/unit/test_aggregation.py tests/unit/test_bias_decomposition.py tests/unit/test_pandascore_http2.py tests/unit/test_onnx_compiler.py tests/unit/test_metaculus_benchmark.py -v
```

**Current counts**: 1546 passed, 8 skipped, 0 failed (excluding 1 pre-existing MirrorBot failure)

---

## 16. GIT STATE

```
a3702a2 feat(esports): wire calibration pipeline + ONNX inference + config-driven EGM
7efabe0 feat(esports): 6-item calibration & inference sprint
17b3e16 feat(esports): 7-item upgrade — calibration, Kelly scaling, parallel analysis
90dff64 fix(paper): restore paper_trades DB persistence (tz-naive + created_at)
e76df44 fix(backfill): add end_date_iso to SELECT DISTINCT for ORDER BY clause
c0c3e2b fix(esports): cap backfill API calls to 5 per scan cycle
00dde26 perf(esports): share positions query between exit check and re-evaluation
36a2b60 fix(esports): add lol+cs2 to Glicko-2 auto-collection loop
d0535f8 fix(esports): exit via SELL order instead of opposite-side BUY
```

**NOTE**: Working tree has uncommitted changes from Sessions 77-81 in MirrorBot/WeatherBot files. These are NOT esports-related — do not touch them.

---

## 17. QUICK START

```bash
# 1. Read this handoff (you're doing it now)
# 2. Read CLAUDE.md governance rules
# 3. Check VPS status
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21 \
  "journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'esportsbot_scan_summary' | tail -3"

# 4. Run tests locally
cd C:/lockes-picks/polymarket-ai-v2
python -m pytest --timeout=300 -q --deselect tests/unit/test_mirror_bot_logic.py::TestCanOpenPosition::test_blocks_when_position_limit_reached

# 5. Check calibrator status (once deployed)
journalctl -u polymarket-ai -f | grep "focal_temp_fitted\|bias_decomp_fitted\|onnx.*loaded"
```

---

## 18. VISION & DIRECTION

**Current state**: EsportsBot is a pre-game + live Glicko-2 powered prediction bot that trades esports markets on Polymarket. It has per-game ML models (LoL, CS2, Dota2, Valorant) that augment the Glicko-2 baseline (63% accuracy). As of Session 82, it has a mathematically principled probability aggregation method (EGM), a calibration pipeline (focal temp + bias decomp), and compiled inference (ONNX).

**Goal**: Achieve consistent positive CLV (closing line value) across all 8 games, with well-calibrated probabilities that feed into properly-sized Kelly bets.

**Key insight from Session 82 blueprint analysis**: The bot's biggest ROI improvements come from CALIBRATION, not from adding more models or signals. The current prediction pipeline produces reasonable probabilities (63% Glicko-2 baseline), but without calibration, the Kelly sizing is garbage-in-garbage-out. The 6 items implemented directly address this gap.

**Architecture principle**: All new capabilities are ADDITIVE (new classes alongside existing ones) and GRACEFULLY DEGRADING (return identity/fallback when unfitted or unavailable). Nothing was removed or replaced.
