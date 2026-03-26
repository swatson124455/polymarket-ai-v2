# CONTINUATION PROMPT — EsportsBot Session 131
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

## WHAT HAPPENED IN RECENT SESSIONS (S125-S130)

### S130 — Full Diagnostic + Deploy Uncommitted Changes (DEPLOYED 20260325)
**S130 deployed 6 uncommitted esports files** (dead code removal, CS2 Glicko-2 metadata passthrough, trainer early_stopping_rounds=20, Dota2/Valorant FEATURE_NAMES attribute, series_model detect_momentum_fallacy removal).

**Commit**: `2f2e417` (S128 bug fixes) — S130 files deployed via scp but NOT committed yet.

**Full diagnostic revealed THE CRITICAL FINDING: EsportsBot has stopped trading.**

#### TRADE EXECUTION FAILURE — ROOT CAUSE DIAGNOSED
**Every `esportsbot_trade_attempt` returns `success=False`.** All trades pass the scan waterfall but die at the sizing stage.

**Mechanism**: Signal quality crushes `confidence` below market price → negative edge → Kelly = 0 → `esportsbot_sizing_killed_at_bankroll`.

**Concrete examples from live logs**:
| Market | Game | Raw Prob | Signal Quality | Confidence | Price | Edge | Result |
|--------|------|----------|---------------|-----------|-------|------|--------|
| 0xe241eb | Valorant | 0.577 | 0.390 | 0.225 | 0.425 | -0.200 | KILLED |
| 0x812ba7 | CS2 | 0.747 | 0.482 | 0.360 | 0.505 | -0.145 | KILLED |
| 0xb7e160 | LoL | ~0.55 | 0.399 | 0.147 | 0.640 | -0.230 | KILLED |
| 0x619f40 | CS2 | 0.621 | 0.734 | 0.466 | 0.205 | +0.261 | PASSED sizing but `skipped_has_position` |

**Three signal quality bottlenecks**:
1. **`sq_brier=0.0` everywhere** — Brier cache resets on every restart, 10% weight contributes zero until `_check_monitoring_thresholds()` runs (~10 min). Even then, some games have no Brier data.
2. **`sq_calibration=0.3`** for LoL/Valorant/CoD/R6/SC2/RL — no BetaCalibrator fitted, so 25% weight barely there. Only CS2 (n=43) and Dota2 (n=10) are fitted → get 0.7.
3. **`sq_uncertainty=0.072`** for Valorant (Glicko-2 phi extreme for unranked teams) — 20% weight near zero.

**Last actual ENTRY**: March 25 00:21 UTC (CS2). Trading volume collapsed to 1 entry on March 25 vs 33 entries on March 24.

**This is the #1 priority for S131.**

### S128 — 10 Audit Bug Fixes (DEPLOYED 20260325_030337)
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
- Now: `float(self._calibrator.predict([proba])[0])` — LoL probs spread 0.08-0.73 (was flat 0.50). VERIFIED WORKING.

**FIX 2: BUG-28 — CS2 Map Heterogeneity [P1]**
- `cs2_economy_model.py`: Added `_heterogeneous_series_prob()` — recursive per-map probability for BO3/BO5. Old `_binomial_race()` retained as fallback.

**FIX 3: BUG-30 — Team Name Substring Match [P1]**
- `opendota_client.py`: Word-boundary regex replaces substring match. "og" no longer matches "rogue".

**FIX 4: BUG-29 — CoT Validator Fail-Open [P2]**
- `cot_validator.py`: Fail-closed on exception (was fail-open). WARNING log + `approved=False`.

**FIX 5: BUG-27 — Dota2 Patch Detection False Positives [P2]**
- `patch_drift.py`: Tightened filter — requires "gameplay update"/"patch"/version regex. Excludes client/workshop/community/cosmetic/server/maintenance.

**FIX 6: BUG-26 — Stale Match Detection [P2]**
- `esports_game_monitor.py`: Timestamp updates only on score change, not every poll. VERIFIED WORKING — 58 stale detections/hour.

**FIX 7: BUG-7 — Token Map Clear Blackout [P3]**
- `esports_bot.py`: Evicts oldest half instead of `.clear()`.

**FIX 8: BUG-6 — Latency Samples deque [P3]**
- `esports_bot.py`: `collections.deque(maxlen=100)` replaces list.

**FIX 9: BUG-25 — Graduation Gate [P3]**
- `esports_trainer.py`: `graduated = (accuracy >= 0.55 and brier < 0.30 and len(data) >= 200)` (was hardcoded True). VERIFIED WORKING — CS2 retrain graduated=False (accuracy 0.542 < 0.55).

**FIX 10: STORE-2 — Queue maxsize [P4]**
- `esports_live_bot.py` + `esports_game_monitor.py`: 200→500.

### S127 — Signal Quality Rewire + Game Tag Backfill
**Signal Quality System**: `confidence = model_prob` → `confidence = side_prob * signal_quality`
- `ESPORTS_MIN_CONFIDENCE`: 0.52 → 0.35, `ESPORTS_BRIER_HALT_THRESHOLD`: 1.0 → 0.30

### S125-S126 — BetaCalibrator Acceleration + Deploy
- `min_samples` 15→10 (lambda_reg=10 safety net)
- `PM_EXCLUDE_BOTS=EsportsBot,MirrorBot,WeatherBot`
- CS2 BetaCalibrator fitted (a=0.9955, n=23)

---

## ALL-TIME P&L BY GAME (as of S130 diagnostic)

| Game | EXIT P&L | RES P&L | **Total** | WR | Brier | Notes |
|------|----------|---------|-----------|-----|-------|-------|
| **Valorant** | +$3,911 | +$221 | **+$4,132** | 57.1% | 0.241 | ONLY profitable game. DO NOT TOUCH. |
| SC2 | — | +$41 | **+$41** | 100% | — | 1 trade |
| CS2 | -$2,967 | +$72 | **-$2,896** | 53.7% | 0.310 | Retrain alert active. Model barely above random. |
| CoD | -$847 | -$304 | **-$1,151** | 0% (1 resolved) | 0.064 | No ML model. DISABLE. |
| Dota2 | -$819 | -$585 | **-$1,404** | 75.0% | 0.297 | WR good but avg loss 2x avg win |
| LoL | -$455 | -$1,403 | **-$1,858** | 16.7% | 0.239 | BUG-24 fix working (probs 0.08-0.73). Need more data. |
| **ALL** | **-$4,986** | **-$1,959** | **-$3,136** | — | — | +23 open positions = -$153 uPnL |

**Critical insight: EXIT losses (-$4,986) are 2.5x worse than RESOLUTION losses (-$1,959).** The exit logic (stop-loss/max-hold) is the #1 P&L destroyer, more than model accuracy.

### Daily P&L Trend (March 20-25)
| Day | Entries | Exits | Res | P&L |
|-----|---------|-------|-----|-----|
| Mar 20 | 34 | 35 | 0 | +$670 |
| Mar 21 | 46 | 24 | 14 | +$3,467 |
| Mar 22 | 23 | 7 | 17 | -$1,318 |
| Mar 23 | 25 | 11 | 9 | -$902 |
| Mar 24 | 33 | 24 | 17 | -$2,584 |
| Mar 25 | 1 | 0 | 4 | -$1,714 |

Trading volume collapsed on March 25 (signal quality blocking everything).

---

## OPEN POSITIONS (23 as of S130)

- **23 open positions**, $5,506 exposure, **-$153 uPnL**
- Most positions entered at 0.49-0.58, currently near entry
- Biggest losers: YES@0.61→0.50 (-$54), YES@0.46→0.40 (-$32)
- One anomalous position: NO@0.03, size=10000, current=0.41 (market `0xcc8fc6`)

---

## BETACALIBRATOR STATUS (as of S130)

| Game | Status | Samples | sq_calibration value |
|------|--------|---------|---------------------|
| CS2 | **Fitted** (a=0.992, n=43) | 43 | 0.70 |
| Dota2 | **Fitted** (a=0.991, n=10) | 10 | 0.70 |
| LoL | Insufficient | 6/10 needed | 0.30 |
| Valorant | Insufficient | 5/10 needed | 0.30 |
| CoD | Insufficient | 1/10 needed | 0.30 |
| R6/SC2/RL | No data | 0 | 0.30 |

---

## PRIORITY QUEUE FOR SESSION 131

### P0 — FIX SIGNAL QUALITY BLOCKING ALL TRADES ← IMMEDIATE
The bot has stopped executing trades. Signal quality is too conservative for the current calibration state. Options (discuss with user):

**Option A: Floor `sq_brier` at 0.3 instead of 0.0 on cold start**
- Prevents 10% weight from being dead on restart
- Low risk: Brier floor of 0.3 is still conservative

**Option B: Seed Brier cache from DB on startup**
- Query last 7d prediction accuracy from `esports_prediction_log` during `start()`
- More accurate than a fixed floor

**Option C: Reduce signal quality weight overall**
- Adjust formula so signal quality ranges [0.50, 1.0] instead of [0.30, 1.0]
- Higher floor means more trades pass but less discrimination

**Option D: Accept current behavior and wait for BetaCalibrators to grow**
- LoL needs 4 more resolved predictions, Valorant needs 5
- Could be days/weeks depending on match resolution cadence
- Meanwhile bot is accumulating zero new data → chicken-and-egg problem

**Recommended**: Option B (seed from DB) + Option A (floor 0.3 as fallback). This restores trading on games with enough data while keeping the conservative stance for truly uncertain games.

### P1 — Disable CoD
16 trades, -$1,151, no ML model. Set `BOT_ENABLED_ESPORTS_COD=false` in .env.

### P2 — Investigate EXIT P&L hemorrhage
EXIT losses (-$4,986) are 2.5x worse than RESOLUTION losses (-$1,959). Something in the exit logic is systematically wrong:
- Stop-loss at 15% may be cutting winners that recover
- Max-hold (96h) may be forcing exits at bad times
- Need to analyze: what % of stop-loss exits would have been profitable if held to resolution?

### P3 — CS2 model retraining
CS2 Brier=0.292 (>0.25 threshold), accuracy=52.2%. Auto-retrain fired but model didn't graduate (0.542 < 0.55 threshold). Consider:
- Lowering graduation threshold for CS2 specifically
- Adding more features (map pool, recent form)
- Investigating what CS2 predictions look like (systematic bias direction?)

### P4 — WebSocket trading disabled
`ws_trading=False` in all recent scans. WS reactive path not firing. Investigate why.

### P5 — Resolution backlog
~15 stale positions (matches ended days ago). NULL `end_date_iso` blocking resolution backfill.

### P6 — Shared infrastructure audit
143 bugs found across shared modules (`AUDIT_SHARED_INFRASTRUCTURE_S128.md`).
EsportsBot-relevant: P0-1 (live trade TypeError), P1-19/P1-20 (resolution P&L).

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
- Lambda_reg=10.0 (strong regularization), Bounds: a [0.1,5.0], b [0.1,5.0], c [-2.0,2.0]
- **STATUS**: CS2 fitted (a=0.992, n=43), Dota2 fitted (a=0.991, n=10). Others insufficient.

### Phase 2: OnlinePlattCalibrator (streaming, per-game)
- River `LogisticRegression` with `SGD(lr=0.01)`, min_samples=30

### Phase 3: ConformalPredictor (per-game, sizing)
- Logit-space residuals, phi_factor sizing in `_execute_esports_trade`

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
| `_game_brier_cache` | In-memory, populated from monitoring | **Lost on restart (repopulates ~10 min)** ← THIS IS A PROBLEM |
| `_prediction_cache` | In-memory, 1h TTL | Lost on restart (10s re-sync) |

---

## KEY CONFIG (Live VPS .env — confirmed S130)

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

### Data / API Clients
| File | Lines | Role |
|------|-------|------|
| `esports/data/esports_data_collector.py` | ~510 | PandaScore data collection for training |
| `esports/data/esports_db.py` | ~300 | DB helpers: upsert, calibration, prediction log |
| `esports/data/pandascore_client.py` | -- | PandaScore API (all 8 games, 1000 req/hr, 30s cache, circuit breaker) |
| `esports/data/opendota_client.py` | ~225 | Dota2 team/hero data (word-boundary match S128) |
| `esports/data/riot_api_client.py` | -- | LoL-specific API |
| `esports/data/aligulac_client.py` | -- | SC2 rankings |
| `esports/data/hltv_scraper.py` | -- | CS2 data |
| `esports/data/oddspapi_client.py` | -- | 3rd party odds |

### Live Game Monitoring
| File | Lines | Role |
|------|-------|------|
| `esports/live/esports_game_monitor.py` | ~400 | PandaScore polling, stale detection (score-change-only S128) |
| `esports/live/esports_event_detector.py` | -- | In-game event classification |
| `esports/live/esports_live_trigger.py` | -- | Cooldowns + per-match caps |

### Other
| File | Role |
|------|------|
| `esports/kelly/esports_bankroll_manager.py` | Separate Kelly pool |
| `esports/markets/esports_market_scanner.py` | Keyword matching, market type classification |
| `esports/markets/esports_market_service.py` | DB-based market discovery |
| `config/settings.py` (1043-1233) | All ESPORTS_* environment variables |

### Tests
| File | Tests | Status |
|------|-------|--------|
| `tests/unit/test_esports_bot.py` | 115 | All passing |
| `tests/unit/test_esports_series_model.py` | 29 | All passing |
| All esports tests | ~200+ | All passing |

### Scripts
| File | Purpose |
|------|---------|
| `scripts/bot_pnl.py` | Canonical P&L: `python scripts/bot_pnl.py EsportsBot 48` |
| `scripts/esports_diag.py` | Diagnostic tool |
| `scripts/esports_48h_charts.py` | P&L visualization |

---

## ALL 8 GAMES — PIPELINE STATUS

| Game | ML Model | Pre-game | Live | BetaCalibrator | P&L |
|------|----------|----------|------|----------------|-----|
| CS2 | CS2EconomyModel | YES | YES | FITTED (n=43) | -$2,896 |
| LoL | LoLWinModel | YES | YES | 6/10 | -$1,858 |
| Dota2 | Dota2Model | YES | No | FITTED (n=10) | -$1,404 |
| Valorant | ValorantModel | YES | No | 5/10 | +$4,132 |
| CoD | None | No | No | 1/10 | -$1,151 |
| R6 | None | No | No | 0 | $0 |
| SC2 | None | No | No | 0 | +$41 |
| RL | None | No | No | 0 | $0 |

---

## WARNINGS ACTIVE ON VPS

| Warning | Frequency | Action |
|---------|-----------|--------|
| `esportsbot_sizing_killed_at_bankroll` | Every scan cycle | Expected — signal quality blocking trades |
| CS2 Brier WARNING (0.292 > 0.25) | Every 10 min | Model underperforming |
| CS2 retrain triggered, graduated=False | On retrain | Model not meeting graduation gate |
| `ws_trading=False` | Every scan | WebSocket path not firing |

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
13. **LoL calibrator is now IsotonicRegression** (S128) — not CalibratedClassifierCV
14. **CS2 `_heterogeneous_series_prob` is @staticmethod** — no self, takes (map_probs, needs_a, needs_b)
15. **CoT validator is fail-CLOSED** (S128) — API failures REJECT trades
16. **Patch drift filter tightened** (S128) — excludes client/workshop/community/cosmetic/server/maintenance
17. **Graduation gate** (S128) — requires accuracy>=0.55, brier<0.30, n>=200
18. **Extremization of Glicko-2 was REJECTED** (S125) — BetaCalibrator is the permanent fix
19. **min_samples=10 is intentional** (S125) — lambda_reg=10 provides safety
20. **Confluence gate was REMOVED in S119** — do not re-add without user approval
21. **`_team_exposure` was REMOVED in S119** — do not re-add without user approval
22. **Backfill outcome is `await`ed (S119)** — do NOT revert to `asyncio.create_task`
23. **CS2 pregame model is ADDITIVE** — existing round->map->match chain untouched
24. **PR-5 (volume filter) DENIED by user** — book walk handles thin markets
25. **CVaR cap is $10,000** (S120) — raised from $5,000
26. **`trade_events` JSONB column is `event_data`** — NOT `metadata_json`
27. **RESOLUTION event idempotency**: Uses atomic INSERT...SELECT with WHERE NOT EXISTS
28. **Python 3.13 scoping**: `from X import Y` inside function makes Y local for ENTIRE function
29. **SSH to VPS**: ICMP blocked (ping fails). Avoid `journalctl --no-pager | grep` on large windows. Use `--since '1 hour ago'` or `timeout 30`
30. **`esports_prediction_log` schema**: `game` (varchar), `predicted_prob`, `actual_outcome` (smallint 0/1 NOT text), `market_price`, `edge`. NO `game_tag` column.
31. **`positions` table schema**: `bot_id` (not `bot_name`), NO `size_usd`/`closed_at`/`updated_at`. Use `size * entry_price` for cost.
32. **`trade_events` P&L**: Use `realized_pnl` column, NOT `event_data->>'pnl_usd'` (that field doesn't exist).

---

## DEPLOY PROTOCOL

### Single-file hot-patch:
```bash
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem <file> ubuntu@34.251.224.21:/tmp/
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo cp /tmp/<file> /opt/polymarket-ai-v2/<path>/ && sudo systemctl restart polymarket-ai"
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

### Full deploy:
```bash
bash deploy/deploy.sh
# Current deploy: 20260325_030337
```

---

## DIAGNOSTIC SSH QUERIES

```bash
SSH="ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o ConnectTimeout=10 ubuntu@34.251.224.21"

# Scan health
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'esportsbot_scan_summary' | tail -3"

# Signal quality values
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'esportsbot_signal_quality' | tail -10"

# Sizing kills
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'sizing_killed' | tail -5"

# Trade attempts
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep 'esportsbot_trade_attempt' | tail -10"

# BetaCalibrator status
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '30 min ago' --no-pager | grep 'betacal' | tail -10"

# Errors
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep -i esports | grep -iE 'error|exception|traceback' | head -10"

# Per-game P&L (all-time)
$SSH "timeout 10 sudo -u postgres psql -d polymarket -c \"
SELECT COALESCE(event_data->>'game','?') as game, event_type, COUNT(*) as n, ROUND(SUM(realized_pnl)::numeric,2) as pnl
FROM trade_events WHERE bot_name='EsportsBot' AND event_type IN ('EXIT','RESOLUTION')
GROUP BY 1,2 ORDER BY game, event_type;\""

# Open positions
$SSH "timeout 10 sudo -u postgres psql -d polymarket -c \"
SELECT COUNT(*) as open, ROUND(SUM(unrealized_pnl)::numeric,2) as upnl, ROUND(SUM(size*entry_price)::numeric,2) as exposure
FROM positions WHERE bot_id='EsportsBot' AND status='open';\""

# BetaCalibrator sample counts
$SSH "timeout 10 sudo -u postgres psql -d polymarket -c \"
SELECT game, COUNT(*) as n FROM esports_prediction_log WHERE actual_outcome IS NOT NULL GROUP BY game ORDER BY n DESC;\""

# Daily P&L
$SSH "timeout 10 sudo -u postgres psql -d polymarket -c \"
SELECT DATE(event_time) as day, COUNT(*) FILTER (WHERE event_type='ENTRY') as entries, COUNT(*) FILTER (WHERE event_type='EXIT') as exits,
ROUND(SUM(CASE WHEN event_type IN ('EXIT','RESOLUTION') THEN realized_pnl ELSE 0 END)::numeric,2) as pnl
FROM trade_events WHERE bot_name='EsportsBot' AND event_time > NOW()-INTERVAL '7 days' GROUP BY 1 ORDER BY 1;\""
```

---

## SESSION HISTORY
| Session | Date | Key Changes |
|---------|------|-------------|
| S125 | Mar 24 | BetaCalibrator min_samples 15→10, game tag restore, markets backfill |
| S126 | Mar 24 | Deploy S125, PM_EXCLUDE_BOTS, CS2 BetaCalibrator fitted |
| S127 | Mar 24 | Signal quality system, game tag backfill, min_confidence 0.52→0.35 |
| S128 | Mar 25 | 10 audit bug fixes (BUG-24 LoL calibration P0, graduation gate, stale detection, etc.) |
| S129 | Mar 25 | Context continuation, no changes |
| S130 | Mar 25 | Deploy uncommitted files, full diagnostic. **Found: bot has stopped trading due to signal quality blocking.** |
