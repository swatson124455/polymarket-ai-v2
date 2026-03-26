# CONTINUATION PROMPT — EsportsBot Session 130
# Carbon-copy agent handoff. Paste into a fresh session. DO NOT bleed into MirrorBot or WeatherBot.

---

## CRITICAL: Read This First
You are continuing an **EsportsBot-only** session for the Polymarket AI V2 automated trading system. Read `CLAUDE.md` in the repo root — it is the prime directive. Then read this document fully before doing anything.

**SCOPE LOCK is active.** Only touch: `bots/esports_bot.py`, `bots/esports_live_bot.py`, `esports/**`, esports tests, `config/settings.py` (ESPORTS_ keys only). Shared modules ONLY if required for an esports bug fix and justified explicitly. NEVER commit changes to mirror_bot.py, weather_bot.py, or other non-esports files.

---

## SYSTEM OVERVIEW

**Polymarket AI V2**: 15-bot automated prediction market trading system. Paper trading mode (`SIMULATION_MODE=true`) on Ubuntu VPS (34.251.224.21). Real capital architecture, $0 execution flag. Paper trading IS production — only difference is final order submission.

**EsportsBot**: Trades esports match-winner markets using:
- **Glicko-2 ratings** (per-game trackers for 8 games: LoL, CS2, Valorant, Dota2, SC2, CoD, R6, RL)
- **BetaCalibrator** (Kull et al. 2017): `sigmoid(a*ln(p) - b*ln(1-p) + c)` — fits per-game, needs 10+ resolved samples
- **Conformal prediction**: per-game prediction intervals for uncertainty-aware sizing
- **Cross-game XGBoost**: blended with Glicko-2 via extremized geometric mean (0.6/0.4 weights)
- **Per-game ML models**: CS2 (dual-path: pregame + economy), LoL (dual-path: pregame + live), Dota2 (XGBoost), Valorant (XGBoost)
- **Signal quality system** (S127): 5-component composite replaces raw `confidence=model_prob`
- **Paper trading engine**: shared across 15 bots, fill probability, VWAP book walk, alpha decay

**EsportsLiveBot**: Live in-game trading using WS price feeds + game monitor queue (maxsize=500)
**EsportsSeriesBot**: Series-level trading via `_series_scan()` (currently silent — no series markets on Polymarket)

---

## WHAT HAPPENED IN RECENT SESSIONS (S125-S129)

### S128 — 10 Audit Bug Fixes (DEPLOYED 20260325_030337) ← CURRENT DEPLOY
Reviewed 15 audit items from `AUDIT_ESPORTSBOT_S127.md`. 10 confirmed REAL, 5 FALSE ALARMS. All 10 fixed, committed (`2f2e417`), deployed, verified live.

#### 5 FALSE ALARMS (no action needed)
| ID | Audit Claim | Reality |
|----|-------------|---------|
| BUG-8 | Prediction log silent fail | Actually `WARNING` level |
| DATA-3 | Calibrator not persisted | Pickle correctly saves/loads `"calibrator"` key |
| LOG-1 | No PandaScore circuit breaker | Full circuit breaker exists with `_HARD_LIMIT` gate |
| RACE-2 | Private `_available_capital` access | Uses public API |
| INEFF-3 | Module-level locks | All locks are instance-level in `__init__` |

#### 10 Fixes Applied

**FIX 1: BUG-24 — LoL Calibration Shape Mismatch [P0 CRITICAL]**
- `lol_win_model.py`: `CalibratedClassifierCV` replaced with `IsotonicRegression`. Old calibrator received shape (1,1) but expected (1,9) → silently returned 0.5 for EVERY LoL prediction. LoL had ZERO edge.
- Now: `float(self._calibrator.predict([proba])[0])` — LoL confidence 0.14-0.41 (was flat 0.5).

**FIX 2: BUG-28 — CS2 Map Heterogeneity [P1]**
- `cs2_economy_model.py`: Added `_heterogeneous_series_prob()` — recursive per-map probability calculation for BO3/BO5. Replaces naive averaging (80%/30%/55% → 55% is wrong).

**FIX 3: BUG-30 — Team Name Substring Match [P1]**
- `opendota_client.py`: Word-boundary regex replaces substring match. "og" no longer matches "rogue".

**FIX 4: BUG-29 — CoT Validator Fail-Open [P2]**
- `cot_validator.py`: Fail-closed on exception (was fail-open). WARNING log + `approved=False`.

**FIX 5: BUG-27 — Dota2 Patch Detection False Positives [P2]**
- `patch_drift.py`: Tightened filter — requires "gameplay update"/"patch"/version regex. Excludes client/workshop/community/cosmetic/server/maintenance.

**FIX 6: BUG-26 — Stale Match Detection [P2]**
- `esports_game_monitor.py`: Timestamp updates only on score change, not every poll.

**FIX 7: BUG-7 — Token Map Clear Blackout [P3]**
- `esports_bot.py`: Evicts oldest half instead of `.clear()` to avoid WS blackout window.

**FIX 8: BUG-6 — list.pop(0) O(n) [P3]**
- `esports_bot.py`: `collections.deque(maxlen=100)` replaces list for latency samples.

**FIX 9: BUG-25 — Graduation Gate [P3]**
- `esports_trainer.py`: `graduated = (accuracy >= 0.55 and brier < 0.30 and len(data) >= 200)` (was hardcoded True).

**FIX 10: STORE-2 — Queue maxsize [P4]**
- `esports_live_bot.py` + `esports_game_monitor.py`: 200→500. Drop log debug→warning.

### S127 — Signal Quality Rewire + Game Tag Backfill (DEPLOYED 20260324_202302)

**Game Tag Backfill**: 226 EXIT/RESOLUTION events backfilled with game tags via ENTRY join + market text analysis.

**Signal Quality System (THE BIG FIX)**:
- `confidence = model_prob` → `confidence = side_prob * signal_quality`
- `signal_quality` = 5-component composite [0.30, 1.0]
- `ESPORTS_MIN_CONFIDENCE`: 0.52 → 0.35, `ESPORTS_BRIER_HALT_THRESHOLD`: 1.0 → 0.30

### S125 — BetaCalibrator Acceleration + Game Tag Restore + Markets Backfill
- `min_samples` 15→10 (lambda_reg=10 safety net)
- `_restore_market_game_from_db()` — game tags survive restart
- Markets-table backfill fallback for calibration resolution data

### S126 — Deploy S125 + Position Manager Exclusion
- BetaCalibrator CS2 fitted (a=0.9955, n=23)
- `PM_EXCLUDE_BOTS=EsportsBot,MirrorBot,WeatherBot` — no PM exits for esports

### S129 — Context continuation, no changes
- SSH monitoring queries were queued but not executed before context ran out

---

## ALL-TIME P&L BY GAME (as of S128 deploy, ~22h stale)

| Game | Trades | P&L | Win Rate | Notes |
|------|--------|-----|----------|-------|
| **Valorant** | 93 | **+$4,132** | 37.6% | ONLY profitable game. DO NOT TOUCH. |
| SC2 | 1 | +$41 | 100% | 1 trade |
| CoD | 16 | -$1,151 | 18.8% | No ML model. Candidate for disable. |
| Dota2 | 76 | -$1,404 | 46.1% | WR OK but avg loss 2x avg win |
| LoL | 40 | -$1,858 | 17.5% | BUG-24 fix should restore edge. MONITOR. |
| CS2 | 155 | -$2,896 | 37.4% | BUG-28 fix should help series. BetaCalibrator fitted. |
| **TOTAL** | **382** | **-$3,343** | -- | Valorant carrying entire bot |

---

## PRIORITY QUEUE FOR NEXT SESSION

### P0 — Monitor S128 fixes (24-48h check) ← IMMEDIATE
S128 deployed ~46h ago now. Run diagnostic queries to check fix impact:
1. LoL predictions discriminating? (confidence should spread, not cluster at 0.5)
2. CS2 series predictions improved?
3. Dota2 team matching improved? (fewer `no_prediction`?)
4. CoT validator rejecting any trades? (`validation_error` in logs?)
5. Stale match detection triggered? (`stale match — skipping` in logs?)
6. Patch drift observation mode frequency reduced?

**Diagnostic SSH queries are in AGENT_HANDOFF_ESPORTS_SESSION129_2026_03_25.md lines 174-235.**

### P1 — Per-game decisions (NOW ACTIONABLE with signal quality + bug fixes)
- **LoL**: If WR still <25% after 48h of BUG-24 fix → consider game-specific edge floor or disable
- **CS2**: BetaCalibrator fitted. Monitor series market accuracy post-BUG-28
- **CoD**: 16 trades, 18.8% WR, no ML model. Strong candidate for `BOT_ENABLED_ESPORTS_COD=false`
- **Dota2**: Monitor post-BUG-30 fix (team matching)

### P2 — Price floor analysis
- <30c trades lost -$6,981 total across all games
- Signal quality should naturally reduce sizing on these
- Re-evaluate after 48h of signal quality data

### P3 — Resolution backlog
- ~15 stale positions (matches ended days ago)
- NULL `end_date_iso` blocking resolution backfill
- Owner: shared infra fix

### P4 — Shared infrastructure audit (AUDIT_SHARED_INFRASTRUCTURE_S128.md)
- 143 bugs found across shared modules
- EsportsBot-relevant fixes: P0-1 (live trade TypeError — go-live blocker), P1-6 (API retry), P1-19/P1-20 (resolution P&L), P1-7 (kill switch)
- P1-20 (partial-exit double-counting) will change reported P&L for ALL bots

### P5 — Future improvements (pending user approval)
- GAP-5: Calibration exclusion flags (exclude bad trades from BetaCalibrator training)
- PR-1: Confluence gate rebuild (multi-signal filter, ~2h)
- PR-3: Champion drift detection (LoL patch-aware, ~1h)
- PR-4: Team exposure tracking (correlated risk, ~1h)

---

## SIGNAL QUALITY SYSTEM (S127)

### Before (broken):
```
confidence = model_prob          # YES side: 0.78 on 22c token
confidence = 1.0 - model_prob   # NO side: 0.78 on 22c token -> biggest bets on worst trades
```

### After (fixed):
```
side_prob = model_prob if YES else (1.0 - model_prob)     # 0.78
signal_quality = _compute_signal_quality(game, market_id)  # 0.30-1.0
confidence = side_prob * signal_quality                    # 0.78 * 0.45 = 0.35
```

### 5 Components of Signal Quality
| Component | Weight | Source | Logic |
|-----------|--------|--------|-------|
| model_agreement | 0.30 | XGB, CatBoost, Glicko-2 est, final prob | `1 - stdev(probs)/0.20` clamped [0,1] |
| calibration_score | 0.25 | BetaCalibrator + OnlinePlatt fitted status | both=1.0, one=0.7, neither=0.3 |
| uncertainty | 0.20 | `matchup_uncertainty` from Glicko-2 phi | `1 - (phi_a+phi_b)/700` |
| enrichment_depth | 0.15 | Count of enrichment layers that fired | `min(1, count/3)` |
| brier_component | 0.10 | Rolling game-level Brier | `1 - brier/0.25` |

**Computed at**: `_compute_signal_quality(game, market_id)` ~line 3451 in esports_bot.py
**Assigned at (4 sites)**: Main path ~2017, WS reactive ~762, Series ~5550, Series WS ~5884

---

## PREDICTION PIPELINE FLOW

```
scan_and_trade -> analyze_opportunity
  -> _detect_game
  -> _get_model_prediction (game-specific ML)
     -> _enrich_prediction -> returns (prob, _enrich_meta)  # TUPLE!
     -> stores _enrich_meta in prediction_cache event_data
  -> BetaCalibrator.calibrate() (if fitted for this game)
  -> ConformalPredictor
  -> side_prob = model_prob (YES) or 1-model_prob (NO)
  -> signal_quality = _compute_signal_quality(game, market_id)
  -> confidence = side_prob * signal_quality
  -> phase_mult applied
  -> min_confidence gate (0.35 in code, 0.20 in .env override)
  -> edge gate (0.05)
  -> TRADE
```

### Full Prediction Path (in `_get_model_prediction()`)
```
Market -> detect_game() -> _get_model_prediction() -> one of:

|- LoL LIVE (live_data + _lol_model.is_trained):
|   -> _inject_glicko2_metadata -> predict_with_glicko2 -> _enrich_prediction

|- LoL PRE-GAME (not live_data + _lol_model.is_trained):
|   -> neutral live features + _build_glicko2_game_state -> predict_with_glicko2 -> _enrich_prediction

|- CS2 LIVE (live_data + _cs2_model.is_trained):
|   -> predict_match (round->map->match chain) -> _enrich_prediction

|- CS2 PRE-GAME (not live_data + _cs2_model.is_trained):
|   -> _build_glicko2_game_state -> predict_pregame -> EGM blend -> _enrich_prediction

|- Dota2 (self._dota2_model.is_trained):
|   -> _build_glicko2_game_state -> _onnx_predict_game -> EGM blend -> _enrich_prediction

|- Valorant (self._valorant_model.is_trained):
|   -> _build_glicko2_game_state -> _onnx_predict_game -> EGM blend -> _enrich_prediction

'- ALL GAMES fallback:
    -> _get_glicko2_prediction -> _enrich_prediction -> cache + return
```

---

## SIZING PIPELINE

```
_execute_esports_trade:
  -> expiry_boost (confidence *= 1.2-1.5x near expiry)
  -> BotBankrollManager.get_bet_size(confidence, price)
     -> Kelly: kelly_full = (confidence * b - q) / b
     -> if confidence <= price: return 0  (natural filter)
  -> size *= phi_factor * dd_factor * game_kelly_mult * edge_decay_mult
  -> exposure checks (game $5K, tournament $8K, total $15K)
  -> max bet cap $300
  -> min trade floor $10 (GAP-4)
  -> if size < 0.10 shares -> reject (size_crushed)
  -> place_order()
```

---

## SCAN WATERFALL (what blocks trades, in order)

1. `no_game` — can't detect game from market question
2. `halted` — Brier halt (threshold=0.30, S127)
3. `exposure_cap` — per-game ($5K) / tournament ($8K) / total ($15K)
4. `observation` — PatchDriftDetector (48h after game patch, tightened filter S128)
5. `no_prediction` — team name extraction/matching failed OR tournament_winner type
6. `exit_cooldown` — recently exited (300s Redis-persisted)
7. `max_entries` — 5 entries per market per 12h window
8. `low_confidence` — below 0.35 code / 0.20 .env
9. `low_edge` — below 0.05
10. `reentry_rejected` — has position, wrong direction or insufficient edge (0.08)
11. `passed` -> goes to `_execute_esports_trade()`

---

## ANTI-CHURN SYSTEM (S109 + S111 + S118)

```
Stop-loss fires (15% drawdown):
  -> SELL order executed
  -> _recently_exited[market_id] = monotonic_time (300s cooldown)
  -> _save_exit_cooldown_to_redis() (survives restart)
  -> _prediction_cache[market_id] cleared

Re-entry attempt:
  -> _recently_exited: if < 300s ago, reject ("exit_cooldown")
  -> _market_entry_times: if >= 5 in last 12h, reject ("max_entries")
  -> Both gates applied in scan, WS, AND series paths
```

---

## ORDER EXECUTION — TWO-LAYER PROTECTION (S115 + S116)

```
Bot calls place_order(side, price, size, confidence, event_data)
  -> order_gateway.py
     1. Book walk: snapshot L2 orderbook, compute VWAP
     2. SPREAD GUARD: if spread > 80% -> dead market reject
     3. EDGE-AT-VWAP GATE: if confidence <= VWAP -> edge eroded reject
     4. Risk manager: CVaR check (max $10K), position limits
     5. If all pass -> paper_trading.py executes at VWAP price
     6. Shadow fill recorded in shadow_fills table
```

---

## CALIBRATION ARCHITECTURE

### Phase 1: BetaCalibrator (batch, per-game)
- `sigmoid(a*ln(p) - b*ln(1-p) + c)`, identity at a=1,b=1,c=0
- Min 10 resolved samples per game, 90-day window from `_GLICKO2_FIX_DATE = 2026-03-16`
- Lambda_reg=10.0 (strong regularization)
- Bounds: a in [0.1,5.0], b in [0.1,5.0], c in [-2.0,2.0]
- **STATUS**: CS2 fitted (a=0.9955, n=23). Others insufficient data.

### Phase 2: OnlinePlattCalibrator (streaming, per-game)
- River `LogisticRegression` with `SGD(lr=0.01)`, min_samples=30
- Applied to RAW prob (not beta-calibrated)

### Phase 3: ConformalPredictor (per-game, sizing)
- Logit-space residuals, fitted in `_check_monitoring_thresholds`
- Used for phi_factor sizing in `_execute_esports_trade`

### Phase 4: ADWIN Drift Detection (streaming, per-game)
- River `ADWIN(delta=0.002)` — advisory only, does NOT halt trading

---

## STATE PERSISTENCE

| State | Mechanism | Restore Method |
|-------|-----------|----------------|
| `_game_exposure` | daily_counters write-through | `_restore_exposure_from_db()` |
| `_daily_pnl` | Query trade_events SUM | `_restore_daily_pnl_from_db()` |
| `_recently_exited` | Redis TTL (300s) | `_restore_exit_cooldowns_from_redis()` in start() |
| `_market_game` | Restored from ENTRY trade_events | `_restore_market_game_from_db()` in scan_and_trade |
| `_open_positions` | positions table | position_manager |
| Glicko-2 ratings | esports_glicko2_ratings table | `_init_glicko2_trackers()` in start() |
| BetaCalibrator params | Re-fitted from esports_prediction_log every 10 min | `_check_monitoring_thresholds()` |
| `_game_brier_cache` | In-memory, populated from monitoring | Lost on restart (repopulates ~10 min) |
| `_prediction_cache` | In-memory, 1h TTL | Lost on restart (10s re-sync) |

---

## KEY CONFIG (Live VPS .env — confirmed S128)

```
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_MAX_BET_USD=300
ESPORTS_MIN_TRADE_USD=10.0
ESPORTS_MAX_DAILY_USD=20000
ESPORTS_MAX_TOTAL_EXPOSURE_USD=15000
ESPORTS_MAX_GAME_EXPOSURE=5000
ESPORTS_MAX_TOURNAMENT_EXPOSURE=8000
ESPORTS_MAX_TEAM_EXPOSURE=2000
ESPORTS_MIN_CONFIDENCE=0.20          # .env overrides code default 0.35
ESPORTS_MIN_EDGE=0.05
ESPORTS_REENTRY_MIN_EDGE=0.08
ESPORTS_MAX_EDGE=0.35
ESPORTS_EXIT_COOLDOWN_SECONDS=300
ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=5
ESPORTS_ENTRY_WINDOW_HOURS=12.0
ESPORTS_PER_MARKET_CAP=600
ESPORTS_STOP_LOSS_PCT=0.15
ESPORTS_BRIER_HALT_THRESHOLD=0.30
ESPORTS_DAILY_LOSS_LIMIT=10000
ESPORTS_MAX_HOLD_HOURS=96
ESPORTS_KELLY_DEFAULT_FRACTION=0.25
ESPORTS_USE_CONFORMAL=true
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_MODEL_MAX_BRIER=0.248
ESPORTS_RETRAIN_INTERVAL_HOURS=24
ESPORTS_MIN_VOLUME_USD=0
SCAN_INTERVAL_ESPORTS_LIVE=2
RISK_MAX_PORTFOLIO_CVAR_USD=10000
BOT_ENABLED_ESPORTS=true
BOT_ENABLED_ESPORTS_LIVE=true
BOT_ENABLED_ESPORTS_SERIES=true
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 20000}}
PM_EXCLUDE_BOTS=EsportsBot,MirrorBot,WeatherBot
PANDASCORE_API_KEY=<redacted>
SIMULATION_MODE=true
```

---

## ARCHITECTURE — COMPLETE FILE MAP

### Core Bot Files
| File | Lines | Role |
|------|-------|------|
| `bots/esports_bot.py` | ~5,900 | Main bot: scan, predict, trade, exit, calibrate, signal quality |
| `bots/esports_live_bot.py` | ~350 | Live in-game trading wrapper (queue maxsize=500) |

### Code Locations in esports_bot.py
| Lines (approx) | What |
|----------------|------|
| 47-149 | `BetaCalibrator` class |
| 151-192 | `OnlinePlattCalibrator` class |
| 195-395 | `__init__` — all instance vars |
| 420-600 | `start()` — client init, Glicko-2, calibrators |
| 684-800 | `on_price_update()` — WS reactive trading (side-aware dedup) |
| 826-845 | `_cleanup_caches()` |
| 846-1280 | `scan_and_trade()` — main loop, waterfall, parallel analysis |
| 1316-1360 | `_restore_exposure_from_db`, `_restore_market_game_from_db` |
| 1360-1400 | `_restore_daily_pnl_from_db` |
| 1430-1580 | `_check_and_execute_exits()` — stop-loss, max hold |
| 1620-1800 | `_resolve_esports_from_clob()` — CLOB resolution |
| 1789-1843 | `_backfill_esports_outcomes()` — trade_events + markets-table fallback |
| 1845-2130 | `analyze_opportunity()` — edge/confidence/volume passthrough |
| 2135-2260 | `_enrich_prediction()` — unified enrichment (returns TUPLE) |
| 2260-2480 | `_get_model_prediction()` — all 7 game paths |
| 2700-2950 | Form adjustment methods |
| 2960-3080 | `_detect_game()`, `_classify_market_type()` |
| 3083-3260 | `_execute_esports_trade()` — sizing pipeline |
| 3325-3460 | `_apply_expiry_boost()`, `_get_phi_sizing_factor()` |
| ~3451 | `_compute_signal_quality()` — 5-component composite |
| 3700-3950 | `_check_monitoring_thresholds()` — Brier, calibrator fitting |
| 4357-4460 | `_get_glicko2_prediction()` — Bayesian blend |
| 4463-4550 | `_check_roster_stability()` |
| 4600-4850 | Team extraction + matching (6 regex patterns + fuzzy) |
| 5050-5670 | Series analysis + trading |
| 5650-5760 | `_series_on_price_update()` |

### ML Models
| File | Lines | Role |
|------|-------|------|
| `esports/models/lol_win_model.py` | ~450 | LoL XGBoost + IsotonicRegression calibrator (S128) |
| `esports/models/cs2_economy_model.py` | ~630 | CS2 round+map+series with _heterogeneous_series_prob (S128) |
| `esports/models/dota2_model.py` | ~500 | Dota2 XGBoost (6 Glicko-2 features) |
| `esports/models/valorant_model.py` | ~400 | Valorant XGBoost (6 Glicko-2 features) |
| `esports/models/series_model.py` | ~350 | Generic BO3/BO5 probability (map veto adjusted) |
| `esports/models/glicko2.py` | -- | Glicko-2 rating system (63.1% accuracy on CS:GO) |
| `esports/models/esports_trainer.py` | ~600 | Training orchestrator with graduation gate (S128) |
| `esports/models/conformal_wrapper.py` | -- | Conformal prediction intervals |
| `esports/models/patch_drift.py` | ~310 | Patch detection + observation mode (tightened S128) |
| `esports/models/cot_validator.py` | ~150 | Chain-of-thought validation (fail-closed S128) |
| `esports/models/tabpfn_ensemble.py` | -- | TabPFN for sparse games (not installed on VPS) |
| `esports/models/draft_features.py` | -- | Draft phase features |
| `esports/models/catboost_draft_model.py` | -- | CatBoost (disabled: ESPORTS_CATBOOST_ENABLED=false) |
| `esports/models/onnx_compiler.py` | -- | ONNX model compilation |

### Data / API Clients
| File | Lines | Role |
|------|-------|------|
| `esports/data/esports_data_collector.py` | ~510 | PandaScore data collection for training |
| `esports/data/esports_db.py` | ~300 | DB helpers: upsert, calibration, prediction log |
| `esports/data/pandascore_client.py` | -- | PandaScore API (all 8 games, 1000 req/hr, 30s cache, circuit breaker) |
| `esports/data/riot_api_client.py` | -- | LoL-specific API (patch checking) |
| `esports/data/opendota_client.py` | ~225 | Dota2 team/hero data (word-boundary match S128) |
| `esports/data/aligulac_client.py` | -- | StarCraft II rankings |
| `esports/data/hltv_scraper.py` | -- | CS2/CSGO data |
| `esports/data/ballchasing_client.py` | -- | CS2 replay analysis |
| `esports/data/oddspapi_client.py` | -- | 3rd party odds cross-validation |

### Live Game Monitoring
| File | Lines | Role |
|------|-------|------|
| `esports/live/esports_game_monitor.py` | ~400 | PandaScore polling (15s), stale detection (score-change-only S128), game state queue |
| `esports/live/esports_event_detector.py` | -- | In-game event classification for live betting signals |
| `esports/live/esports_live_trigger.py` | -- | Cooldowns + per-match caps |

### Other
| File | Role |
|------|------|
| `esports/kelly/esports_bankroll_manager.py` | Separate Kelly pool (drawdown compression) |
| `esports/markets/esports_market_scanner.py` | Keyword matching, market type classification (120s cache) |
| `esports/markets/esports_market_service.py` | DB-based market discovery (bypasses Gamma API) |
| `esports/calibration/bias_decomposition.py` | Per-game recalibration |
| `esports/calibration/metaculus_benchmark.py` | Cross-platform benchmark |
| `config/settings.py` (1043-1233) | All ESPORTS_* environment variables (80+) |

### Tests
| File | Tests | Status |
|------|-------|--------|
| `tests/unit/test_esports_bot.py` | 115 | All passing |
| `tests/unit/test_esports_series_model.py` | 29 | All passing |
| `tests/unit/test_esports_live_bot.py` | -- | Queue=500 assertion |
| `tests/unit/test_esports_bankroll.py` | -- | All passing |
| `tests/unit/test_patch_drift.py` | -- | All passing |
| `tests/unit/test_dota2_model.py` | -- | All passing |
| `tests/unit/test_valorant_model.py` | -- | All passing |

### Scripts
| File | Purpose |
|------|---------|
| `scripts/bot_pnl.py` | Canonical P&L reporting: `python scripts/bot_pnl.py EsportsBot 48` |
| `scripts/esports_diag.py` | Diagnostic tool (positions, predictions, waterfall) |
| `scripts/esports_48h_charts.py` | P&L visualization |
| `scripts/seed_esports_data.py` | Initial data population |
| `scripts/backfill_esports_resolution_events.py` | RESOLUTION event backfill |

### Migrations
| File | Purpose |
|------|---------|
| `schema/migrations/024_esports_tables.sql` | Core tables |
| `schema/migrations/029_esports_training_data.sql` | Training data schema |
| `schema/migrations/030_esports_prediction_log.sql` | Prediction logging |
| `schema/migrations/053_esports_schema_fixes.sql` | S87-88 corrections |
| `schema/migrations/057_esports_prediction_log_dedup.sql` | Dedup fixes |

---

## ALL 8 GAMES — PIPELINE STATUS

| Game | ML Model | Pre-game | Live | Training Features | BetaCalibrator |
|------|----------|----------|------|-------------------|----------------|
| CS2 | CS2EconomyModel | YES (pregame) | YES (round->map->match) | 14 round + 6 Glicko-2 | FITTED (n=23) |
| LoL | LoLWinModel | YES (neutral+Glicko-2) | YES (predict_with_glicko2) | 9 (4 live + 5 Glicko-2) | Insufficient |
| Dota2 | Dota2Model | YES | No | 6 Glicko-2 | Insufficient |
| Valorant | ValorantModel | YES | No | 6 Glicko-2 | Insufficient |
| CoD | None | TabPFN (not installed) | No | 6 Glicko-2 (generic) | 0 samples |
| R6 | None | TabPFN (not installed) | No | 6 Glicko-2 (generic) | 0 samples |
| SC2 | None | TabPFN (not installed) | No | 6 Glicko-2 (generic) | 0 samples |
| RL | None | TabPFN (not installed) | No | 6 Glicko-2 (generic) | 0 samples |

---

## CRITICAL TRAPS (DO NOT BREAK)

1. **VPS deploy path is `/opt/polymarket-ai-v2/`** — NOT `/opt/polymarket-ai/current/`
2. **`trade_events` immutability trigger**: Must DISABLE then re-enable for data corrections
3. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. Never "BUY"/"SELL"
4. **`_enrich_prediction()` returns a TUPLE**: `(prob, _enrich_meta)`. All call sites must unpack
5. **`_compute_signal_quality()` reads from `_prediction_cache`**: Defaults ~0.45 on miss
6. **Signal quality only in ENTRY event_data** — not EXIT/RESOLUTION. Join by market_id
7. **PSEUDO_LABEL_ENABLED=false**: DO NOT enable
8. **Paper trading IS production**: Only difference is final order submission
9. **`asyncio.create_task()` forbidden for financial write-throughs** — always `await`
10. **One fix per commit. Preserve every function signature. No while-I'm-in-here refactors.**
11. **15 bots in BOT_REGISTRY. Shared module change = all 15 verified**
12. **`ESPORTS_MIN_CONFIDENCE=0.20` in .env** overrides code default 0.35 — intentional
13. **LoL calibrator is now IsotonicRegression** (S128) — old pickles with CalibratedClassifierCV still load but inference changed
14. **CS2 `_heterogeneous_series_prob` is @staticmethod** — no self, takes (map_probs, needs_a, needs_b)
15. **CoT validator is fail-CLOSED** (S128) — API failures REJECT trades. Monitor false rejections
16. **Patch drift filter tightened** (S128) — excludes client/workshop/community/cosmetic/server/maintenance. Real gameplay patches with these words will be missed
17. **Graduation gate** (S128) — requires accuracy>=0.55, brier<0.30, n>=200
18. **Extremization of Glicko-2 was REJECTED** (S125) — cascading tuning debt. BetaCalibrator is the permanent fix. Do NOT revisit
19. **min_samples=10 is intentional** (S125) — lambda_reg=10 provides safety. Do NOT raise to 15
20. **Confluence gate was REMOVED in S119** — do not re-add without user approval
21. **`_team_exposure` was REMOVED in S119** — do not re-add without user approval
22. **Backfill outcome is `await`ed (S119)** — do NOT revert to `asyncio.create_task`
23. **CS2 pregame model is ADDITIVE** — existing round->map->match chain untouched
24. **PR-5 (volume filter) DENIED by user** — book walk handles thin markets
25. **CVaR cap is $10,000** (S120) — raised from $5,000 because portfolio CVaR was blocking all trades
26. **`_market_meta_cache` in MirrorBot**: 3-tuple — NEVER expand (this is MirrorBot but good to know)
27. **`trade_events` JSONB column is `event_data`** — NOT `metadata_json`
28. **RESOLUTION event idempotency**: Uses atomic INSERT...SELECT with WHERE NOT EXISTS (ON CONFLICT broken on partitioned tables)
29. **Python 3.13 scoping**: `from X import Y` inside function makes Y local for ENTIRE function
30. **SSH to VPS**: ICMP blocked (ping fails), TCP:22 open. Avoid `journalctl --no-pager | grep` on large windows (hangs). Use `--since '1 hour ago'` or `timeout 30`

---

## DEPLOY PROTOCOL

### Single-file hot-patch:
```bash
# 1. Test locally
python -m pytest tests/unit/test_esports_bot.py tests/unit/test_esports_series_model.py -x -q

# 2. Upload
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem bots/esports_bot.py ubuntu@34.251.224.21:/tmp/esports_bot.py

# 3. Deploy + restart
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo cp /tmp/esports_bot.py /opt/polymarket-ai-v2/bots/esports_bot.py && sudo systemctl restart polymarket-ai"

# 4. Verify (wait 30s)
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo journalctl -u polymarket-ai --since '30 sec ago' -o cat --no-pager | grep -i 'esports'"

# 5. Check first scan
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sleep 60 && sudo journalctl -u polymarket-ai --since '60 sec ago' -o cat --no-pager | grep 'esportsbot_scan_summary' | tail -3"
```

### Multi-file deploy (S128 pattern):
```bash
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem \
  bots/esports_bot.py bots/esports_live_bot.py \
  esports/models/lol_win_model.py esports/models/cs2_economy_model.py \
  esports/models/esports_trainer.py esports/models/cot_validator.py \
  esports/models/patch_drift.py esports/data/opendota_client.py \
  esports/live/esports_game_monitor.py \
  ubuntu@34.251.224.21:/tmp/

ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo cp /tmp/esports_bot.py /opt/polymarket-ai-v2/bots/ && \
   sudo cp /tmp/esports_live_bot.py /opt/polymarket-ai-v2/bots/ && \
   sudo cp /tmp/lol_win_model.py /opt/polymarket-ai-v2/esports/models/ && \
   sudo cp /tmp/cs2_economy_model.py /opt/polymarket-ai-v2/esports/models/ && \
   sudo cp /tmp/esports_trainer.py /opt/polymarket-ai-v2/esports/models/ && \
   sudo cp /tmp/cot_validator.py /opt/polymarket-ai-v2/esports/models/ && \
   sudo cp /tmp/patch_drift.py /opt/polymarket-ai-v2/esports/models/ && \
   sudo cp /tmp/opendota_client.py /opt/polymarket-ai-v2/esports/data/ && \
   sudo cp /tmp/esports_game_monitor.py /opt/polymarket-ai-v2/esports/live/ && \
   sudo systemctl restart polymarket-ai"
```

### Full deploy (atomic symlink swap):
```bash
bash deploy/deploy.sh
# VPS venv: /opt/polymarket-ai-v2/venv/bin/activate
# VPS .env: /opt/pa2-shared/.env
# Current deploy: 20260325_030337
```

---

## DIAGNOSTIC QUERIES (run on VPS)

```bash
SSH_KEY="~/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# P&L by game
ssh -i $SSH_KEY $VPS "sudo -u postgres psql -d polymarket -t -A -F '|' -c \"
SELECT COALESCE(event_data->>'game', 'UNTAGGED') AS game,
       COUNT(*) AS trades, ROUND(SUM(realized_pnl)::numeric, 2) AS pnl,
       ROUND(AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END)::numeric, 3) AS wr
FROM trade_events WHERE bot_name='EsportsBot' AND event_type IN ('EXIT','RESOLUTION')
GROUP BY 1 ORDER BY 3 DESC;
\""

# Open positions
ssh -i $SSH_KEY $VPS "sudo -u postgres psql -d polymarket -t -A -F '|' -c \"
SELECT market_id, side, ROUND(entry_price::numeric, 4), ROUND(size::numeric, 2),
       ROUND(unrealized_pnl::numeric, 2), opened_at::text
FROM positions WHERE status='open' AND bot_id='EsportsBot'
ORDER BY opened_at DESC LIMIT 20;
\""

# Recent scans
ssh -i $SSH_KEY $VPS "sudo journalctl -u polymarket-ai --since '5 min ago' -o cat --no-pager | grep 'esportsbot_scan_summary' | tail -3"

# Signal quality distribution
ssh -i $SSH_KEY $VPS "sudo -u postgres psql -d polymarket -t -A -F '|' -c \"
SELECT event_data->>'game' AS game,
       ROUND(AVG((event_data->>'signal_quality')::float)::numeric, 3) AS avg_sq,
       COUNT(*)
FROM trade_events WHERE bot_name='EsportsBot' AND event_type='ENTRY'
  AND event_data->>'signal_quality' IS NOT NULL
  AND event_time > '2026-03-25 03:00:00'
GROUP BY 1 ORDER BY 1;
\""

# BetaCalibrator status
ssh -i $SSH_KEY $VPS "sudo journalctl -u polymarket-ai --since '1 hour ago' -o cat --no-pager | grep 'esportsbot_beta_cal' | tail -5"
```

---

## BRIER BY GAME (last known)

| Game | Brier | Accuracy | Status |
|------|-------|----------|--------|
| Valorant | 0.153 | 72% | Best — carrying P&L |
| Dota2 | 0.231 | 62% | Decent |
| CS2 | 0.273 | 42% | BetaCalibrator fitted, monitoring |
| LoL | 0.308 | 31% | Worst — BUG-24 fix should help |

---

## DESIGN OBSERVATIONS (no code change yet — context for future work)

- LoL model pickle missing on VPS — falls back to pure Glicko-2. Needs training pipeline
- Dota2/Valorant models are copy-pasted. Could refactor to shared base class
- CS2 model has 1-2 effective features in 14-feature wrapper (live path)
- TabPFN not installed on VPS. Sparse games (SC2/RL/CoD/R6) run Glicko-2 + cross-game XGB only
- LoL blue side bonus applied without verifying actual side (~line 2399)
- CatBoost draft model disabled (`ESPORTS_CATBOOST_ENABLED=false`)
- `bot_pnl.py` shows 100 data integrity warnings (SELL with no matching ENTRY) — historical, not live bug

---

## SESSION HISTORY (key milestones)

| Session | Date | Key Changes |
|---------|------|-------------|
| S120 | 03-23 | CS2/LoL pregame paths LIVE, unified _enrich_prediction, GAP-1/2/4, CVaR $10K |
| S121 | 03-23 | Game tags on EXIT/RESOLUTION events |
| S125 | 03-23 | BetaCalibrator min_samples 10, game tag restore, markets-table backfill |
| S126 | 03-24 | Deploy S125, PM_EXCLUDE_BOTS, BetaCalibrator CS2 fitted |
| S127 | 03-24 | Signal quality system (5-component), game tag backfill, Brier halt 0.30 |
| S128 | 03-25 | 10 audit bug fixes: LoL calibrator, CS2 series, team matching, CoT fail-closed, graduation gate |
| S129 | 03-25 | Context continuation, no changes (monitoring queries not executed) |

### Earlier sessions (reference — read handoff docs for detail):
- S109: Anti-churn system, exit cooldowns
- S111: Position manager hardening
- S115-S116: Order execution guards (spread, edge-at-VWAP)
- S118: Weather audit verified safe for esports
- S119: Confluence gate removed, team exposure removed, backfill await'd

---

## TESTS

```bash
# Full suite
python -m pytest tests/unit/ -x -q --ignore=tests/unit/test_weather_bot.py
# Expected: 1717+ passed, 0 failures

# Esports-only
python -m pytest tests/unit/test_esports_bot.py tests/unit/test_esports_series_model.py -x -q
# Expected: 144+ passed
```
