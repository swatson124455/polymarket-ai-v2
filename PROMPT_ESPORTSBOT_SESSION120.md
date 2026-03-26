# CONTINUATION PROMPT — EsportsBot Session 120
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
- **Paper trading engine**: shared across 15 bots, fill probability, VWAP book walk, alpha decay

**EsportsLiveBot**: Live in-game trading using WS price feeds (shares `esports_bot.py`)
**EsportsSeriesBot**: Series-level trading via `_series_scan()` (currently silent — no series markets on Polymarket)

---

## SESSION 119 RECAP — WHAT WAS DONE (2026-03-23)

### Full Forensic Code Audit
Line-by-line audit of ALL esports code: `esports_bot.py` (5743 lines) + 37 support files (~11,500 lines). 11 dedicated Opus agents, each reading every line. **70 findings total.**

### Phase 1: 17 Root-Cause Bug Fixes (DEPLOYED)
| Fix | File | What |
|-----|------|------|
| 1A | esports_bot.py:1500 | Stale `entry` var in exit cost loop — re-extract per position |
| 1C | esports_db.py:449 | `db.fetch_all()` (nonexistent) → `db.get_session()` in `compute_calibration_curve` |
| 1D | esports_db.py:859 | `updated_at` → `last_updated` column name in `update_calibration` |
| 1E | esports_trainer.py:465 | Cross-game temporal split inverted — removed `reversed()` |
| 1F | esports_trainer.py:757 | LoL ONNX `n_features=8` → `9` |
| 1G | esports_bot.py:5387 | Series reverse sweep guard — added `return []` |
| 1H | esports_bot.py:5678-5743 | Series WS path: added anti-churn, fixed NO token, added entry tracking |
| 1I | esports_bot.py:3230 | Tournament exposure shares → USD |
| 1J | esports_bot.py:1525 | Hardcoded `'EsportsBot'` → `self.bot_name` in orphan cleanup |
| 1L | esports_bot.py:1910 | Double calibration — Platt on raw prob, not beta-calibrated |
| 1M | esports_bot.py:3545 | Draw handling in Glicko-2 slow-path rebuild |
| 1N | esports_bot.py:1297 | Fire-and-forget → `await` for RESOLUTION writes |
| 1O | esports_bot.py:5171,5181 | Series trade count inside `if _ok:` only |
| 1P | esports_data_collector.py:583 | Added `team_a, team_b` to training SQL SELECT |
| 1R | dota2_model.py, valorant_model.py | `FEATURE_NAMES` as class attribute (fixes ONNX path) |
| 1S | opendota_client.py:160 | `radiant_win=None` guard |
| 1W | patch_drift.py:30 | Brier threshold 0.05 → 0.25 |
| P4 | esports_bot.py:2135+ | `scan_start_mono` in 4 event_data dicts |

### Phase 2: 24 Dead Code Removals (DEPLOYED)
- Deprecated calibrators (`_focal_calibrator`, `_bias_decomp`, `_horizon_calibrator`)
- **Confluence gate removed entirely** (always passed — see PR-1 below for rebuild option)
- `_team_exposure` dict (never wired), `_models_graduated` (redundant)
- Cross-game conformal predictor (always disabled)
- Dead functions: `log_esports_prediction`, `upsert_esports_team/match`, `_extract_lol_features`, `build_game_state_from_timeline`, `detect_momentum_fallacy`, `check_champion_drift`, `classify_buy/projected_loss_bonus`, `_evaluate_binary/_evaluate_binary_cs2`
- Dead constants: `_LOL_FEATURES`, `_CS2_FEATURES`
- Dead settings: `ESPORTS_MAX_EDGE`, `ESPORTS_CS2_ECONOMY_BREAK_THRESHOLD`, all `ESPORTS_CONFLUENCE_*`
- Tautological conditions, unreachable guards, stale variables

### Phase 3: 23 Cleanup Items (DEPLOYED)
- 8 silent `except Exception: pass` → proper logging (`logger.warning` for financial, `logger.debug` for enrichment)
- 5 `hasattr` lazy-init dicts → moved to `__init__`
- 7 redundant `import re`/`import math` inside methods → removed
- Noisy `esportsbot_backfill_skip` log removed (fired 9/10 scans)
- Stale alert string `(>0.30)` → actual threshold variable
- `_series_glicko2_cache` eviction added
- EsportsSeriesBot added to daily P&L + backfill queries
- Stale docstrings fixed, deprecated asyncio call fixed

### VPS .env Cleanup (DEPLOYED)
- Deleted stale `ESPORTS_MAX_EDGE=0.35`, `ESPORTS_MODEL_MAX_BRIER=0.248`
- Updated `ESPORTS_MAX_DAILY_USD` 10000 → 20000 in both `.env` files
- Updated `BOT_BANKROLL_CONFIG` EsportsBot `max_daily_usd` 10000 → 20000

---

## PENDING REVIEW — Removed Features That Could Elevate (USER MUST APPROVE/DENY)

These were removed in S119 because they were broken/dead. The concepts have potential. **Do NOT rebuild any without explicit user approval.**

### PR-1: Confluence Gate — Multi-signal trade filter
**Was**: Edge (65%) + freshness (35%) + agreement (0%) scoring, gate at 0.55. **Broken**: edge normalization saturated to 1.0, gate never rejected. **Could**: With proper normalization, reject borderline trades with stale predictions. ~2h to rebuild.

### PR-2: Momentum Fallacy Detector — Contrarian series signals
**Was**: Detected overpriced momentum in series markets after map 1. **Broken**: Zero callers. **Could**: Real alpha in series trading (documented in literature). **Blocked**: No series markets on Polymarket.

### PR-3: Champion Drift Detection — LoL patch-aware model invalidation
**Was**: Monitored champion win rate shifts >3%. **Broken**: Never wired in. **Could**: LoL is worst game (Brier 0.308). Patches change champion power. This could trigger targeted retraining. ~1h to wire.

### PR-4: Team Exposure Tracking — Correlated risk management
**Was**: Per-team USD exposure dict + cap. **Broken**: Dict declared, never written/read. Config `ESPORTS_MAX_TEAM_EXPOSURE=2000` exists but unenforced. **Could**: Prevent concentration on one org across multiple matches. ~1h to implement.

### PR-5: Volume Filter — Liquidity gating
**Was**: Min volume threshold for markets. **Broken**: Defined but never applied. **Could**: Reject thin markets before trying to trade (24% avg slippage). ~30min.

### PR-6: Cross-Game Conformal Predictor — System-wide sizing safety net
**Was**: Conformal intervals from all resolved trades. **Broken**: Gated `False`. Per-game conformal (still active) does same at finer grain. **Could**: System-wide safety net. **Blocked**: Needs 50+ resolved predictions.

### PR-7: CS2 Economy Helpers — Economy-aware round prediction
**Was**: `classify_buy()` + `projected_loss_bonus()` for CS2. **Broken**: No real-time economy data. **Could**: Economy is #1 CS2 predictor. **Blocked**: Data source needed.

---

## REALISM GAPS — Features Other Bots Have That EsportsBot Doesn't

### GAP-1: CLOB Volume Passthrough (HIGH)
WeatherBot passes `volume_24h` in event_data → paper engine uses for fill probability. EsportsBot uses $50K generic fallback → fill probability artificially high for thin esports books.
**Fix**: Read volume from `_market_index` in `_execute_esports_trade`, pass in event_data.

### GAP-2: Same-Side Dedup (MEDIUM)
WeatherBot checks `_position_details` (side-aware). MirrorBot uses `_entered_market_sides` historical set. EsportsBot checks `has_open_position(market_id)` only — no side awareness. Can create opposing YES+NO on same market.
**Fix**: Track entered sides, block same-side re-entry.

### GAP-3: Fill Failure Tracking (LOW-MEDIUM)
WeatherBot has `_fill_fail_tracker` — skips chronically unfillable markets after N failures.
**Fix**: Add per-market failure counter, skip after 3 consecutive failures within 1h.

### GAP-4: Min Trade Floor (LOW)
WeatherBot has `WEATHER_MIN_TRADE_USD=5.0`. EsportsBot has no floor — dust positions possible.
**Fix**: Add `ESPORTS_MIN_TRADE_USD` setting + guard in `_execute_esports_trade`.

### GAP-5: Calibration Exclusion Flags (MEDIUM)
MirrorBot flags bad entries with `calibration_exclude` in event_data. EsportsBot trains calibrator on all data including junk.
**Fix**: Flag restart-flood entries, same-market re-entries, null-confidence entries.

---

## PREDICTION PIPELINE (in order, current state after S119)

1. **Glicko-2 rating lookup** → raw `model_prob` (P(team A wins))
2. **BetaCalibrator** (if fitted for game) → calibrated probability
3. **Online Platt scaling** (if available) → applied to RAW prob (not beta-calibrated, fixed in S119)
4. **RFLB correction** → favorites-longshot bias adjustment
5. **BO adjustment** → best-of-1 dampening
6. **Cross-game XGBoost blend** → extremized geometric mean with Glicko-2 (0.6/0.4)
7. **Per-game conformal prediction** → uncertainty intervals for sizing
8. **Edge calculation** → `model_prob - market_price` (YES) or `(1-model_prob) - (1-price)` (NO)
9. **BotBankrollManager sizing** → Kelly fraction with conformal bounds

### Full Prediction Path (in `_get_model_prediction()`)
```
Market -> detect_game() -> _get_model_prediction() -> one of:

|- LoL LIVE (live_data + _lol_model.is_trained):
|   -> _inject_glicko2_metadata → predict_with_glicko2

|- CS2 LIVE (live_data + _cs2_model.is_trained):
|   -> predict_match (series model)

|- Dota2 / Valorant (ML model + Glicko2 features):
|   -> _onnx_predict_game or native .predict() + EGM blend

'- ALL GAMES fallback:
    -> _get_glicko2_prediction (Bayesian-blended)
    -> Prior blend: phi>=350 → 80% market, phi<100 → 100% Glicko-2
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
7. P6 max bet cap: if (price * size) > ESPORTS_MAX_BET_USD ($300), clamp
8. Game exposure cap — $5K per game
9. Daily cap — $20K
```

---

## SCAN WATERFALL (what blocks trades, in order)

1. `no_game` — can't detect game from market question
2. `halted` — Brier halt (DISABLED via threshold=999.0)
3. `exposure_cap` — per-game ($5K) / tournament ($8K) / team ($2K) / total ($15K)
4. `observation` — PatchDriftDetector (48h after game patch)
5. `no_prediction` — team name extraction/matching failed OR tournament_winner type
6. `exit_cooldown` — recently exited (5 min Redis-persisted)
7. `max_entries` — 5 entries per market per 12h window
8. `low_confidence` — below 0.48
9. `low_edge` — below 0.05
10. `reentry_rejected` — has position, wrong direction or insufficient edge
11. `passed` → goes to `_execute_esports_trade()`

*Note: Confluence gate REMOVED in S119 (was a no-op). Dead market spread guard (>80%) in order_gateway since S116.*

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
  -> Both gates applied in scan, WS, AND series paths (S119 fixed series WS)
```

---

## ORDER EXECUTION — TWO-LAYER PROTECTION (S115 + S116)

```
Bot calls place_order(side, price, size, confidence, event_data)
  -> order_gateway.py
     1. Book walk: snapshot L2 orderbook, compute VWAP
     2. S116 SPREAD GUARD: if spread > 80% → dead market reject
     3. S115 EDGE-AT-VWAP GATE: if confidence <= VWAP → edge eroded reject
     4. If both pass → paper_trading.py executes at VWAP price
     5. Shadow fill recorded in shadow_fills table
```

---

## CALIBRATION ARCHITECTURE

### Phase 1: BetaCalibrator (batch, per-game)
- `sigmoid(a*ln(p) - b*ln(1-p) + c)`, identity at a=1,b=1,c=0
- Min 15 resolved samples per game, 90-day window from `_GLICKO2_FIX_DATE = 2026-03-16`
- **STATUS**: 0/8 games fitted. S118 deleted corrupted data. Fresh predictions accumulating. ETA first fit: ~48-72h from S119.

### Phase 2: OnlinePlattCalibrator (streaming, per-game)
- River `LogisticRegression` with `SGD(lr=0.01)`
- Applied to RAW prob (not beta-calibrated — fixed in S119)

### Phase 3: ConformalPredictor (per-game, sizing)
- Logit-space residuals, fitted in `_check_monitoring_thresholds`
- Used for phi_factor sizing in `_execute_esports_trade`

### Phase 4: ADWIN Drift Detection (streaming, per-game)
- River `ADWIN(delta=0.002)` — advisory only, does NOT halt trading

---

## CALIBRATOR STATUS (as of S119 end)

| Game | Total Predictions | Resolved | Min Required | Fitted |
|------|------------------|----------|--------------|--------|
| CS2 | 85 | 1 | 15 | No |
| Valorant | 19 | 0 | 15 | No |
| Dota2 | 9 | 0 | 15 | No |
| LoL | 7 | 0 | 15 | No |
| CoD | 3 | 0 | 15 | No |
| R6 | 1 | 0 | 15 | No |

Predictions accumulating since S118 (2026-03-22 ~20:08 UTC). Resolution backfill runs every 20 min via `_backfill_esports_outcomes` (now `await`ed, not fire-and-forget).

---

## P&L SUMMARY

| Day | Net | Notes |
|-----|-----|-------|
| Mar 18 | +$175 | |
| Mar 19 | -$1,709 | |
| Mar 20 | +$1,357 | |
| Mar 21 | +$5,117 | 2 big Valorant wins |
| Mar 22 | -$79 | |
| **All-time** | **+$4,844** | |

**Side asymmetry**: NO +$2,664 (avg +$35.52), YES -$1,435 (avg -$16.50). Model overestimates underdogs.

### Brier by Game
| Game | Brier | Accuracy | Status |
|------|-------|----------|--------|
| Valorant | 0.153 | 72% | Best — carrying P&L |
| Dota2 | 0.231 | 62% | Decent |
| CS2 | 0.273 | 42% | Midrange, improving |
| LoL | 0.308 | 31% | **Worst — active P&L leak** |

---

## LIVE VPS CONFIG (as of S119 deploy)

```env
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_MAX_BET_USD=300
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
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 20000}}
SIMULATION_MODE=true
```

---

## KEY CODE LOCATIONS

### bots/esports_bot.py (~5600 lines post-S119 cleanup)
| Lines (approx) | What |
|----------------|------|
| 30-148 | `BetaCalibrator` class |
| 151-189 | `OnlinePlattCalibrator` class |
| 195-395 | `__init__` — all instance vars |
| 420-600 | `start()` — client init, Glicko-2, calibrators |
| 624-800 | `on_price_update()` — WS reactive trading |
| 810-845 | `_cleanup_caches()` |
| 846-1280 | `scan_and_trade()` — main loop, waterfall, parallel analysis |
| 1300-1360 | `_restore_daily_pnl_from_db` |
| 1430-1580 | `_check_and_execute_exits()` — stop-loss, max hold |
| 1620-1750 | `_resolve_esports_from_clob()` — CLOB resolution |
| 1750-1810 | `_backfill_esports_outcomes()` |
| 1810-2060 | `analyze_opportunity()` — edge/confidence/calibration |
| 2060-2460 | `_get_model_prediction()` — all prediction paths |
| 2460-2500 | `_refresh_live_matches` |
| 2500-2550 | `_inject_glicko2_metadata()` |
| 2700-2850 | Form adjustment methods (PandaScore, OpenDota, Aligulac, Ballchasing) |
| 2850-2940 | `_detect_game()`, `_classify_market_type()` |
| 2940-3200 | `_execute_esports_trade()` — sizing pipeline |
| 3200-3340 | `_check_kelly_graduation()` |
| 3340-3460 | `_init_glicko2_trackers()`, `_save_glicko2_ratings()` |
| 3700-3900 | `_check_monitoring_thresholds()` — Brier, calibrator fitting |
| 4300-4500 | `_get_glicko2_prediction()` — Bayesian blend |
| 4600-4850 | Team extraction + matching (6 regex patterns + fuzzy) |
| 5050-5650 | Series analysis + trading |
| 5650-5740 | `_series_on_price_update()` — series WS reactive |

### Other Key Files
| File | Purpose |
|------|---------|
| `config/settings.py` | All ESPORTS_* config |
| `esports/models/glicko2.py` | Glicko2Rating, expected_score, Glicko2Tracker |
| `esports/models/conformal_wrapper.py` | ConformalPredictor — logit-space residuals |
| `esports/models/lol_win_model.py` | LoL ML model (9 features) |
| `esports/models/cs2_economy_model.py` | CS2 round/map/match prediction |
| `esports/models/dota2_model.py` | Dota2 XGBoost (6 features) |
| `esports/models/valorant_model.py` | Valorant XGBoost (6 features) |
| `esports/models/series_model.py` | BO3/BO5 probability, map veto |
| `esports/models/patch_drift.py` | Patch version monitoring, observation mode |
| `esports/models/esports_trainer.py` | Per-game + cross-game training |
| `esports/data/esports_db.py` | All DB functions (prediction log, resolution, Brier, calibration) |
| `esports/data/esports_data_collector.py` | Training data from PandaScore |
| `esports/data/pandascore_client.py` | PandaScore API (rate-limited, cached) |
| `esports/kelly/esports_bankroll_manager.py` | Kelly sizing, daily exposure |
| `esports/live/esports_game_monitor.py` | Live match state polling |
| `esports/live/esports_event_detector.py` | In-game event classification |
| `esports/live/esports_live_trigger.py` | Live event → trade execution |
| `esports/markets/esports_market_service.py` | Market discovery + price refresh |
| `bots/esports_live_bot.py` | Live in-game bot (shares esports_bot.py) |

---

## OUTSTANDING ITEMS (prioritized)

### Immediate (next session)
1. **Monitor BetaCalibrator** — should produce non-identity params as matches resolve
2. **GAP-1: CLOB volume passthrough** — highest-impact realism fix
3. **GAP-2: Same-side dedup** — prevent opposing positions
4. **1K: Fix `_check_roster_stability()`** — needs PandaScore API investigation for string team IDs

### Pending User Approval (PR-1 through PR-7)
See PENDING REVIEW section above. Each needs explicit approve/deny before rebuilding.

### Design Observations (no code change yet)
- LoL model pickle missing — falls back to pure Glicko-2. Needs training pipeline.
- Dota2/Valorant models are copy-pasted. Refactor to shared base class.
- CS2 model is 1-2 effective features in 14-feature wrapper.
- TabPFN not installed. Sparse games run Glicko-2 only.
- LoL blue side bonus applied without verifying actual side (line 2399).
- CatBoost draft model train/val split inverted (disabled, `ESPORTS_CATBOOST_ENABLED=false`).

### Resolved This Session
- Shadow fills `latency_ms` NULL → fixed (P4)
- VPS .env stale vars → cleaned
- Calibrator data gap → S118 cleanup was correct, fresh data accumulating
- SSH hanging → was journal volume, not connectivity (use temp file + grep)

---

## CRITICAL TRAPS (DO NOT BREAK)

- **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL (SELL is only for exits)
- **`_market_meta_cache` in MirrorBot**: 3-tuple. NEVER expand. (Not esports but in shared memory)
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **Python 3.13 scoping**: `from X import Y` inside function makes `Y` local for ENTIRE function
- **PatchDriftDetector**: `_patch_timestamps` ONLY set on genuine patch changes (`old is not None`)
- **RESOLUTION event idempotency**: Uses atomic INSERT...SELECT with WHERE NOT EXISTS
- **Confluence gate was REMOVED in S119** — do not re-add without user approval (PR-1)
- **`_team_exposure` was REMOVED in S119** — do not re-add without user approval (PR-4)
- **Backfill outcome is now `await`ed (S119)** — do NOT revert to `asyncio.create_task`

---

## DEPLOY PROTOCOL

```bash
# Upload files
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o StrictHostKeyChecking=no <files> ubuntu@34.251.224.21:/tmp/

# Copy to app directory + restart
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo cp /tmp/<file> /opt/polymarket-ai-v2/<path>/ && sudo systemctl restart polymarket-ai"

# Verify (use temp file approach — journal is huge)
ssh ... "sleep 25 && sudo journalctl -u polymarket-ai --since '10 sec ago' -o cat --no-pager | grep 'esportsbot_scan_summary' | tail -2"

# SSH GOTCHA: journalctl | grep over SSH hangs on large output. Always use short --since windows or write to temp file first.
```

## Tests
```bash
python -m pytest tests/unit/ -x -q --ignore=tests/unit/test_weather_bot.py
# Expected: 1415 passed, 2 skipped, 0 failures
```
