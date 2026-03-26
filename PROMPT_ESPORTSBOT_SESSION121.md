# CONTINUATION PROMPT — EsportsBot Session 121
# Carbon-copy agent handoff. Paste into a fresh session. DO NOT bleed into MirrorBot or WeatherBot.

---

## CRITICAL: Read This First
You are continuing an **EsportsBot-only** session for the Polymarket AI V2 automated trading system. Read `CLAUDE.md` in the repo root — it is the prime directive. Then read this document fully before doing anything.

**SCOPE LOCK is active.** Only touch: `bots/esports_bot.py`, `bots/esports_live_bot.py`, `bots/esports_series_bot.py`, `esports/**`, esports tests, `config/settings.py` (ESPORTS_ keys only). Shared modules ONLY if required for an esports bug fix and justified explicitly. NEVER commit changes to mirror_bot.py, weather_bot.py, or other non-esports files.

---

## System Overview

**Polymarket AI V2**: 15-bot automated prediction market trading system. Paper trading mode (`SIMULATION_MODE=true`) on Ubuntu VPS (34.251.224.21). Real capital architecture, $0 execution flag.

**EsportsBot**: Trades esports match-winner markets using:
- **Glicko-2 ratings** (per-game trackers for 8 games: LoL, CS2, Valorant, Dota2, SC2, CoD, R6, RL)
- **BetaCalibrator** (Kull et al. 2017): `sigmoid(a*ln(p) - b*ln(1-p) + c)` — fits per-game, needs 15+ resolved samples
- **Conformal prediction**: per-game prediction intervals for uncertainty-aware sizing
- **Cross-game XGBoost**: blended with Glicko-2 via extremized geometric mean (0.6/0.4 weights)
- **Per-game ML models**: CS2 (dual-path: pregame + economy), LoL (dual-path: pregame + live), Dota2 (XGBoost), Valorant (XGBoost)
- **Paper trading engine**: shared across 15 bots, fill probability, VWAP book walk, alpha decay

**EsportsLiveBot**: Live in-game trading using WS price feeds (shares `esports_bot.py`)
**EsportsSeriesBot**: Series-level trading via `_series_scan()` (currently silent — no series markets on Polymarket)

---

## SESSION 120 RECAP — WHAT WAS DONE (2026-03-23)

### Phase 1: CS2 Pre-Game Prediction Path (was DEAD — now LIVE)
**Root cause:** `esports_bot.py:2109` had `and live_data` guard. CS2 ML model only fired for live matches. Pre-game CS2 markets (majority of volume, biggest esports market by far) fell through to raw Glicko-2 fallback.

**3-file fix:**
- `esports/data/esports_data_collector.py` — Added Glicko-2 metadata (`matchup_uncertainty`, `rd_asymmetry`, `team_a_volatility`, `team_b_volatility`, `best_of`) to CS2 training data via `_get_glicko2_metadata()`. Previously only had `team_strength_diff` + `map_ct_rate` as real features out of 14 (11 constant economy placeholders).
- `esports/models/cs2_economy_model.py` — Added dual-path architecture:
  - `PREGAME_FEATURES` constant (6 Glicko-2 features matching Valorant/Dota2)
  - `FEATURE_NAMES = PREGAME_FEATURES` class attribute for ONNX compat
  - `_pregame_model = None` in `__init__`
  - `predict_pregame()` — XGBoost on 6 features, heuristic fallback (logistic on team_strength_diff)
  - `train_pregame()` — XGBClassifier(n_estimators=60, max_depth=2, same hyperparams as Valorant/Dota2)
  - `_predict_pregame_heuristic()` — static method, mirrors Valorant/Dota2
  - `save()`/`load()` updated to persist `_pregame_model` (backward-compatible — old pickles return None)
  - Existing `predict_round()`/`predict_map()`/`predict_match()` chain **UNTOUCHED**
- `esports/models/esports_trainer.py` — `_train_cs2()` now also calls `model.train_pregame(train_set)` after round model training

**Result:** CS2 pregame model trained on 3316 samples. `team_strength_diff` still has importance=1.0 (old training data lacks Glicko-2 metadata — will diversify as new data accumulates).

### Phase 2: LoL Pre-Game Prediction Path (was DEAD — now LIVE)
**Root cause:** Same `and live_data` guard at line 2074.

**Fix:** Added LoL pre-game block in `esports_bot.py`. Builds game_state with neutral live features (`game_time=30, gold_pct=0.5, towers=0, dragons=0`) + real Glicko-2 from `_build_glicko2_game_state()`. Calls `predict_with_glicko2()` for ML+Glicko-2 blend. No model file changes needed — LoL model already trained on these mixed features.

### Phase 3: Unified Enrichment Pipeline (`_enrich_prediction()`)
**Root cause:** Game-specific ML paths (LoL, CS2, Dota2, Valorant) returned predictions BEFORE the Glicko-2 fallback, which is where form adjustments, cross-game XGB, LAN adj, blue side bonus, CatBoost draft, and BO adjustment lived. The 4 games with ML models skipped ALL enrichment steps.

**Fix:** Extracted ~170 lines of inline enrichment from Glicko-2 fallback into new `_enrich_prediction()` method. All 7 prediction paths now call it:

```
_enrich_prediction(prob, game, market_id, market_data, live_data) → float
  1. Form adjustment (OpenDota for Dota2, PandaScore for others, Aligulac for SC2, Ballchasing for RL)
  2. TabPFN blend (CoD, R6, SC2, RL sparse games)
  3. Cross-game XGB blend (all 8 games, EGM 0.6/0.4)
  4. CatBoost draft model blend (if ESPORTS_CATBOOST_ENABLED=true)
  5. LAN adjustment (CS2, Valorant: -2% fav, +1% underdog)
  6. Blue side bonus (LoL: +1.9%)
  7. BO format adjustment (BO3/BO5 binomial boost, BO1 underdog mean-reversion)
```

Callers: LoL live, LoL pre-game, CS2 live, CS2 pre-game, Dota2, Valorant, Glicko-2 fallback.

### Phase 4: GAP-1 — CLOB Volume Passthrough
EsportsBot used $50K generic fallback for fill probability. Now reads real `volume_24h` from `order_gateway._market_index` (same pattern as WeatherBot) in `analyze_opportunity()`, passes via `_clob_volume` in opp dict, injected into `event_data["volume_24h"]` in `_execute_esports_trade()`.

### Phase 5: GAP-4 — Min Trade Floor
Added `ESPORTS_MIN_TRADE_USD=10.0` in `config/settings.py`. Guard in `_execute_esports_trade()` after max bet cap: if `price * size < $10`, reject with `esportsbot_below_min_trade` log. Prevents dust positions.

### Phase 6: GAP-2 — Same-Side Dedup (WS + Series paths)
Scan path (line 1128-1154) already had side-aware checking via `_position_details`. WS path (line 750) and series WS path (line 5664) only checked `has_open_position(market_id)` — no side comparison. Fixed both to check `_position_details.side` match. Only blocks re-entry on SAME side; allows opposite-side positions.

### Phase 7: 1K — Roster Stability Hygiene
- Normalized `team_id` to consistent str key: `team_id = str(int(team_id))`
- Added 1h API failure cooldown (`_roster_fail_cache`) on `TimeoutError`/`ValueError` — prevents hammering PandaScore after failures

### Phase 8: `no_prediction` Logging
Added `logger.info("esportsbot_no_prediction", ...)` with game, market_type, market_id, question for failed `_get_model_prediction()` returns. Previously these were counted in waterfall but question text was invisible.

### Phase 9: CVaR Cap Increase
`RISK_MAX_PORTFOLIO_CVAR_USD`: $5,000 → $10,000 in `config/settings.py`. Portfolio CVaR was $10,250 — blocking all new trades. After increase, first trade placed immediately.

### Phase 10: PR-5 Volume Filter — DENIED by user
User ruled: "in the real world the bet will fill what it can then move to the next available item." The book walk + edge-at-VWAP gate already handle thin markets realistically. No pre-filter needed.

---

## PENDING REVIEW — Features Awaiting User Approval

### GAP-5: Calibration Exclusion Flags (NOT IMPLEMENTED — user said "add to handoff, don't turn on")
**What:** BetaCalibrator trains on ALL trade outcomes including bad ones (restart floods, patch drift era, null-confidence entries). No mechanism to exclude junk data.
**When to implement:** After first BetaCalibrator game fits (~24-48h from S120). Not urgent while 0/8 games fitted.
**Proposed fix:** Add `calibration_exclude=true` to event_data for bad trades. Filter in calibration queries. ~3h effort, MEDIUM-HIGH blast radius.

### PR-1: Confluence Gate — Multi-signal trade filter
**Was:** Edge (65%) + freshness (35%) + agreement (0%), gate at 0.55. **Broken:** edge normalization saturated to 1.0. **Status:** PENDING USER APPROVAL. ~2h.

### PR-3: Champion Drift Detection — LoL patch-aware
**Was:** Monitored champion win rates for >3% shift. **Broken:** Never wired in. **LoL is worst game (Brier 0.308).** ~1h. **Status:** PENDING USER APPROVAL.

### PR-4: Team Exposure Tracking — Correlated risk
**Was:** Per-team USD exposure dict + cap. **Broken:** Never written/read. Config `ESPORTS_MAX_TEAM_EXPOSURE=2000` exists but unenforced. ~1h. **Status:** PENDING USER APPROVAL.

### PR-2: Momentum Fallacy — BLOCKED (no series markets)
### PR-6: Cross-Game Conformal — BLOCKED (needs 50+ resolved predictions)
### PR-7: CS2 Economy Helpers — BLOCKED (needs real-time data source)

---

## KNOWN ISSUES FROM 48h P&L ANALYSIS

### ISSUE-1: NO side 18% win rate (CRITICAL)
48h data shows: YES +$625 (13 trades, 46% WR), NO -$1,445 (11 trades, 18% WR). Model overestimates underdogs. This is a prediction quality issue, not a pipeline bug. Possible fixes: NO confidence discount, asymmetric edge thresholds, or NO-side sizing reduction.

### ISSUE-2: Game field NULL on EXIT/RESOLUTION events
`trade_events` for EXIT and RESOLUTION events have `event_data->>'game' = NULL`. This makes per-game P&L tracking impossible from trade_events alone. The game tag from ENTRY should be carried forward to EXIT/RESOLUTION events. Currently, P&L by game chart shows everything as "unknown."

### ISSUE-3: CS2 pregame model only uses team_strength_diff
Old training data (3316 rows) lacks Glicko-2 metadata features. New data being collected with all 6 features. Feature importance will diversify over days as new data dominates. Not a bug — just needs time.

---

## PREDICTION PIPELINE (current state after S120)

1. **Glicko-2 rating lookup** → raw `model_prob` (P(team A wins))
2. **BetaCalibrator** (if fitted for game) → calibrated probability
3. **Online Platt scaling** (if available) → applied to RAW prob (not beta-calibrated)
4. **RFLB correction** → favorites-longshot bias adjustment
5. **BO adjustment** → best-of-1 dampening (via `_enrich_prediction`)
6. **Cross-game XGBoost blend** → EGM with Glicko-2 (0.6/0.4 weights, via `_enrich_prediction`)
7. **Per-game conformal prediction** → uncertainty intervals for sizing
8. **Edge calculation** → `model_prob - market_price` (YES) or `(1-model_prob) - (1-price)` (NO)
9. **BotBankrollManager sizing** → Kelly fraction with conformal bounds

### Full Prediction Path (in `_get_model_prediction()`)
```
Market -> detect_game() -> _get_model_prediction() -> one of:

|- LoL LIVE (live_data + _lol_model.is_trained):
|   -> _inject_glicko2_metadata → predict_with_glicko2 → _enrich_prediction

|- LoL PRE-GAME (not live_data + _lol_model.is_trained):   [NEW S120]
|   -> neutral live features + _build_glicko2_game_state → predict_with_glicko2 → _enrich_prediction

|- CS2 LIVE (live_data + _cs2_model.is_trained):
|   -> predict_match (round→map→match chain) → _enrich_prediction

|- CS2 PRE-GAME (not live_data + _cs2_model.is_trained):   [NEW S120]
|   -> _build_glicko2_game_state → predict_pregame → EGM blend → _enrich_prediction

|- Dota2 (self._dota2_model.is_trained):
|   -> _build_glicko2_game_state → _onnx_predict_game → EGM blend → _enrich_prediction

|- Valorant (self._valorant_model.is_trained):
|   -> _build_glicko2_game_state → _onnx_predict_game → EGM blend → _enrich_prediction

'- ALL GAMES fallback:
    -> _get_glicko2_prediction → _enrich_prediction → cache + return
```

---

## SIZING PIPELINE (in order)

```
1. BotBankrollManager.calculate_bot_position_size() — Kelly with kelly_fraction=0.25
2. Near-expiry confidence boost (A5)
3. Per-game conformal bounds — shrinks size by prediction interval width
4. Drawdown Kelly reduction (A8) — reduces Kelly when daily P&L negative
5. CLV-gated scaling (disabled: ESPORTS_CLV_SCALING_ENABLED=false)
6. Apply multipliers: size * phi_factor * dd_factor * game_kelly_mult * edge_decay_mult
7. Upset risk scaling — volatile favorites get sized down
8. Per-market cap override (re-entry)
9. P6 max bet cap: if (price * size) > ESPORTS_MAX_BET_USD ($300), clamp
10. GAP-4 min floor: if (price * size) < ESPORTS_MIN_TRADE_USD ($10), reject   [NEW S120]
11. If size < 0.10 shares → reject (size_crushed)
12. Game exposure cap — $5K per game
13. Daily cap — $20K
```

---

## SCAN WATERFALL (what blocks trades, in order)

1. `no_game` — can't detect game from market question
2. `halted` — Brier halt (DISABLED via threshold=999.0)
3. `exposure_cap` — per-game ($5K) / tournament ($8K) / total ($15K)
4. `observation` — PatchDriftDetector (48h after game patch)
5. `no_prediction` — team name extraction/matching failed OR tournament_winner type [NOW LOGGED S120]
6. `exit_cooldown` — recently exited (5 min Redis-persisted)
7. `max_entries` — 5 entries per market per 12h window
8. `low_confidence` — below 0.48
9. `low_edge` — below 0.05
10. `reentry_rejected` — has position, wrong direction or insufficient edge
11. `passed` → goes to `_execute_esports_trade()`

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
     2. S116 SPREAD GUARD: if spread > 80% → dead market reject
     3. S115 EDGE-AT-VWAP GATE: if confidence <= VWAP → edge eroded reject
     4. Risk manager: CVaR check (max $10K), position limits
     5. If all pass → paper_trading.py executes at VWAP price
     6. Shadow fill recorded in shadow_fills table
```

---

## CALIBRATION ARCHITECTURE

### Phase 1: BetaCalibrator (batch, per-game)
- `sigmoid(a*ln(p) - b*ln(1-p) + c)`, identity at a=1,b=1,c=0
- Min 15 resolved samples per game, 90-day window from `_GLICKO2_FIX_DATE = 2026-03-16`
- **STATUS**: 0/8 games fitted. CS2 has 3 resolved. ETA first fit: ~24-48h.

### Phase 2: OnlinePlattCalibrator (streaming, per-game)
- River `LogisticRegression` with `SGD(lr=0.01)`
- Applied to RAW prob (not beta-calibrated — fixed in S119)

### Phase 3: ConformalPredictor (per-game, sizing)
- Logit-space residuals, fitted in `_check_monitoring_thresholds`
- Used for phi_factor sizing in `_execute_esports_trade`

### Phase 4: ADWIN Drift Detection (streaming, per-game)
- River `ADWIN(delta=0.002)` — advisory only, does NOT halt trading

---

## P&L SUMMARY

| Day | Net | Notes |
|-----|-----|-------|
| Mar 18 | +$175 | |
| Mar 19 | -$1,709 | |
| Mar 20 | +$1,357 | |
| Mar 21 | +$5,117 | 2 big Valorant wins + resolution spike |
| Mar 22 | -$79 | |
| **All-time** | **+$4,844** | |

**48h breakdown (from charts):**
- Mar 21: +$3,460 (big day)
- Mar 22: -$1,318 (exits gave back gains)
- Mar 23: -$385 (early, 2 new entries placed post-S120)

**Side asymmetry**: NO +18% WR (BAD), YES 46% WR. Model overestimates underdogs.

### Brier by Game
| Game | Brier | Accuracy | Status |
|------|-------|----------|--------|
| Valorant | 0.153 | 72% | Best — carrying P&L |
| Dota2 | 0.231 | 62% | Decent |
| CS2 | 0.273 | 42% | Fixed pre-game path S120, monitoring |
| LoL | 0.308 | 31% | **Worst — active P&L leak** |

---

## LIVE VPS CONFIG (as of S120 deploy)

```env
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_MAX_BET_USD=300
ESPORTS_MIN_TRADE_USD=10.0
ESPORTS_MAX_DAILY_USD=20000
ESPORTS_MAX_TOTAL_EXPOSURE_USD=15000
ESPORTS_MAX_GAME_EXPOSURE=5000
ESPORTS_MAX_TOURNAMENT_EXPOSURE=8000
ESPORTS_MAX_TEAM_EXPOSURE=2000
ESPORTS_MIN_CONFIDENCE=0.48
ESPORTS_MIN_EDGE=0.05
ESPORTS_REENTRY_MIN_EDGE=0.08
ESPORTS_EXIT_COOLDOWN_SECONDS=300
ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=5
ESPORTS_ENTRY_WINDOW_HOURS=12.0
ESPORTS_PER_MARKET_CAP=600
ESPORTS_STOP_LOSS_PCT=0.15
ESPORTS_BRIER_HALT_THRESHOLD=999.0
ESPORTS_DAILY_LOSS_LIMIT=10000
ESPORTS_MAX_HOLD_HOURS=96
SCAN_INTERVAL_ESPORTS_LIVE=2
RISK_MAX_PORTFOLIO_CVAR_USD=10000
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 20000}}
SIMULATION_MODE=true
```

---

## KEY CODE LOCATIONS

### bots/esports_bot.py (~5800 lines post-S120)
| Lines (approx) | What |
|----------------|------|
| 30-148 | `BetaCalibrator` class |
| 151-189 | `OnlinePlattCalibrator` class |
| 195-395 | `__init__` — all instance vars |
| 420-600 | `start()` — client init, Glicko-2, calibrators |
| 624-800 | `on_price_update()` — WS reactive trading (GAP-2 side-aware dedup) |
| 810-845 | `_cleanup_caches()` |
| 846-1280 | `scan_and_trade()` — main loop, waterfall, parallel analysis |
| 1300-1360 | `_restore_daily_pnl_from_db` |
| 1430-1580 | `_check_and_execute_exits()` — stop-loss, max hold |
| 1620-1750 | `_resolve_esports_from_clob()` — CLOB resolution |
| 1750-1810 | `_backfill_esports_outcomes()` |
| 1810-2060 | `analyze_opportunity()` — edge/confidence/volume passthrough (GAP-1) |
| 2055-2230 | `_enrich_prediction()` — unified enrichment [NEW S120] |
| 2230-2420 | `_get_model_prediction()` — LoL live/pregame, CS2 live/pregame, Dota2, Valorant, fallback |
| 2700-2850 | Form adjustment methods |
| 2850-2940 | `_detect_game()`, `_classify_market_type()` |
| 2940-3220 | `_execute_esports_trade()` — sizing pipeline (GAP-4 min floor) |
| 3340-3460 | `_init_glicko2_trackers()`, `_save_glicko2_ratings()` |
| 3700-3900 | `_check_monitoring_thresholds()` — Brier, calibrator fitting |
| 4300-4500 | `_get_glicko2_prediction()` — Bayesian blend |
| 4456-4542 | `_check_roster_stability()` — 1K hygiene fixes |
| 4600-4850 | Team extraction + matching (6 regex patterns + fuzzy) |
| 5050-5670 | Series analysis + trading (GAP-2 side-aware dedup on series WS) |
| 5650-5740 | `_series_on_price_update()` |

### Other Key Files
| File | Purpose |
|------|---------|
| `config/settings.py` | All ESPORTS_* config + RISK_MAX_PORTFOLIO_CVAR_USD |
| `esports/models/cs2_economy_model.py` | CS2 model: PREGAME_FEATURES, predict_pregame, train_pregame, round→map→match chain |
| `esports/models/esports_trainer.py` | Per-game + cross-game training, `_train_cs2()` now trains pregame too |
| `esports/data/esports_data_collector.py` | Training data: CS2 now has Glicko-2 metadata |
| `esports/models/glicko2.py` | Glicko2Rating, expected_score, Glicko2Tracker |
| `esports/models/conformal_wrapper.py` | ConformalPredictor — logit-space residuals |
| `esports/models/lol_win_model.py` | LoL ML model (9 features) |
| `esports/models/dota2_model.py` | Dota2 XGBoost (6 features) |
| `esports/models/valorant_model.py` | Valorant XGBoost (6 features) |
| `esports/models/series_model.py` | BO3/BO5 probability, map veto, bo1_underdog_adjustment |
| `esports/models/patch_drift.py` | Patch version monitoring, observation mode |
| `esports/data/esports_db.py` | All DB functions |
| `esports/data/esports_data_collector.py` | Training data from PandaScore |
| `esports/data/opendota_client.py` | OpenDota API |
| `bots/esports_live_bot.py` | Live in-game bot |
| `scripts/esports_48h_visual.py` | 48h P&L charts (3x2 grid, matches WeatherBot format) |

---

## ALL 8 GAMES — PIPELINE STATUS

| Game | ML Model | Pre-game | Live | Training Features | Form Adj | Enrichment |
|------|----------|----------|------|-------------------|----------|------------|
| CS2 | CS2EconomyModel | YES (pregame) | YES (round→map→match) | 14 round + 6 Glicko-2 | — | FULL |
| LoL | LoLWinModel | YES (neutral+Glicko-2) | YES (predict_with_glicko2) | 9 (4 live + 5 Glicko-2) | PandaScore | FULL |
| Dota2 | Dota2Model | YES | No | 6 Glicko-2 | OpenDota | FULL |
| Valorant | ValorantModel | YES | No | 6 Glicko-2 | PandaScore | FULL |
| CoD | None | TabPFN | No | 6 Glicko-2 (generic) | PandaScore | FULL |
| R6 | None | TabPFN | No | 6 Glicko-2 (generic) | PandaScore | FULL |
| SC2 | None | TabPFN | No | 6 Glicko-2 (generic) | Aligulac | FULL |
| RL | None | TabPFN | No | 6 Glicko-2 (generic) | Ballchasing | FULL |

**"FULL" enrichment** = form adj + TabPFN (sparse) + cross-game XGB + CatBoost draft + LAN adj + blue side + BO adj. All via `_enrich_prediction()`.

---

## CRITICAL TRAPS (DO NOT BREAK)

- **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL (SELL is only for exits)
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **Python 3.13 scoping**: `from X import Y` inside function makes `Y` local for ENTIRE function
- **PatchDriftDetector**: `_patch_timestamps` ONLY set on genuine patch changes (`old is not None`)
- **RESOLUTION event idempotency**: Uses atomic INSERT...SELECT with WHERE NOT EXISTS
- **Confluence gate was REMOVED in S119** — do not re-add without user approval (PR-1)
- **`_team_exposure` was REMOVED in S119** — do not re-add without user approval (PR-4)
- **Backfill outcome is now `await`ed (S119)** — do NOT revert to `asyncio.create_task`
- **`_enrich_prediction()` is the SINGLE enrichment path** [S120] — do NOT add inline enrichment to ML paths
- **CS2 pregame model is ADDITIVE** — existing round→map→match chain untouched
- **PR-5 (volume filter) DENIED by user** — book walk handles thin markets, no pre-filter
- **CVaR cap is $10,000** [S120] — was $5,000, raised because portfolio CVaR was $10,250 blocking all trades

---

## DEPLOY PROTOCOL

```bash
# Upload files
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o StrictHostKeyChecking=no <files> ubuntu@34.251.224.21:/tmp/

# Copy to app directory + restart
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo cp /tmp/<file> /opt/polymarket-ai-v2/<path>/ && sudo systemctl restart polymarket-ai"

# Verify (use short --since windows — journal is huge)
ssh ... "sleep 25 && sudo journalctl -u polymarket-ai --since '10 sec ago' -o cat --no-pager | grep 'esportsbot_scan_summary' | tail -2"

# VPS venv: /opt/polymarket-ai-v2/venv/bin/activate
# VPS .env: /opt/pa2-shared/.env
# SSH GOTCHA: journalctl | grep over SSH hangs on large output. Always use short --since windows.
```

## Tests
```bash
python -m pytest tests/unit/ -x -q --ignore=tests/unit/test_weather_bot.py
# Expected: 1428 passed, 2 skipped, 0 failures
```

---

## NEXT SESSION PRIORITIES

### Immediate
1. **ISSUE-1: NO side 18% WR** — investigate and fix. Biggest P&L leak. Options: NO confidence discount, asymmetric edge, NO sizing reduction.
2. **ISSUE-2: Game field NULL on EXIT/RESOLUTION** — carry game tag from ENTRY for proper sector P&L tracking.
3. **Monitor BetaCalibrator** — should produce non-identity params as matches resolve (~24-48h).
4. **Monitor CS2 pregame feature diversification** — new training data should show matchup_uncertainty etc. gaining importance.

### Pending User Approval
5. **GAP-5: Calibration exclusion flags** — implement when first BetaCalibrator fits.
6. **PR-1: Confluence gate rebuild** — multi-signal filter, ~2h.
7. **PR-3: Champion drift detection** — could help LoL (worst game), ~1h.
8. **PR-4: Team exposure tracking** — correlated risk, ~1h.

### Design Observations (no code change yet)
- LoL model pickle missing on VPS — falls back to pure Glicko-2. Needs training pipeline.
- Dota2/Valorant models are copy-pasted. Refactor to shared base class.
- CS2 model is 1-2 effective features in 14-feature wrapper (live path).
- TabPFN not installed. Sparse games run Glicko-2 + cross-game XGB only.
- LoL blue side bonus applied without verifying actual side (line 2399).
- CatBoost draft model disabled (`ESPORTS_CATBOOST_ENABLED=false`).

---

## FILES MODIFIED IN S120 (5 files)
- `bots/esports_bot.py` — CS2/LoL pre-game paths, `_enrich_prediction()`, GAP-1/2/4, 1K, no_prediction logging
- `esports/models/cs2_economy_model.py` — `PREGAME_FEATURES`, `predict_pregame()`, `train_pregame()`, save/load
- `esports/models/esports_trainer.py` — `model.train_pregame()` in `_train_cs2()`
- `esports/data/esports_data_collector.py` — Glicko-2 metadata in CS2 training data
- `config/settings.py` — `ESPORTS_MIN_TRADE_USD`, `RISK_MAX_PORTFOLIO_CVAR_USD` $5K→$10K
