# EsportsBot Session 135 — Full System Handoff

**Date**: 2026-03-27
**Scope**: EsportsBot-only (scope lock active)
**Previous**: S134 (exit hemorrhage fixes, git sync, calibration review), S133 (data cleanup + guardrails + learning), S132 (data integrity), S131 (SQ root fix)
**Commits**: `74c800f` (S135: exec fail cooldown, divergence cap, game tag backfill)
**Deploy**: `20260327_151630` (service restarted, scanning healthy)
**Tests**: 87 EsportsBot + 35 resolution tests passing

---

## 1. CRITICAL: Read This First

**SCOPE LOCK is active.** You are continuing an EsportsBot-only session. Only touch:
- `bots/esports_bot.py`, `bots/esports_live_bot.py`
- `esports/**`
- Esports tests (`tests/unit/test_esports_bot.py`, etc.)
- `config/settings.py` (ESPORTS_ keys only)
- `base_engine/data/resolution_backfill.py` (only esports-related changes)
- Shared modules ONLY if required for an esports bug fix and justified explicitly

**Read `CLAUDE.md` in the repo root first — it is the prime directive.**

**Paper trading IS production.** The ONLY difference between paper and live is whether the final order goes to the CLOB or to the paper trade table. Every system, check, feature, and edge case matters identically.

**NEVER disable games without explicit user instruction.** (Learned S134 — user rejected CoD disable.)

---

## 2. What Happened in S135 (THIS SESSION)

### Investigation Phase — Data-Driven Analysis

#### All-Time P&L (Updated, from `trade_events`)
| Game | EXIT P&L | RES P&L | Total | Note |
|------|----------|---------|-------|------|
| Valorant | +$3,842 | -$94 | **+$3,748** | Still the only profitable game |
| SC2 | -- | +$41 | **+$41** | 1 trade |
| Dota2 | -$819 | +$297 | **-$522** | Improved (resolutions turning positive) |
| LoL | +$268 | -$952 | **-$684** | EXIT now positive, RESOLUTION brutal |
| CoD | -$605 | +$4 | **-$601** | |
| CS2 | -$2,928 | -$373 | **-$3,301** | Biggest loser by far |
| ? (unknown) | -- | -$1,478 | **-$1,478** | Pre-game-tagging (NOW BACKFILLED — see Fix 3a) |
| (blank) | -$207 | -- | **-$207** | |
| **TOTAL** | | | **~-$3,004** | Improved from -$4,368 |

#### Calibrator Progress (Updated)
| Game | Total | Resolved | BetaCal Status |
|------|-------|----------|----------------|
| CS2 | 172 | 70 | **FITTED** |
| Dota2 | 42 | 28 | **FITTED** |
| Valorant | 35 | 9 | 1 more needed |
| LoL | 24 | 10 | **JUST HIT 10 — should be fitting now** |
| CoD | 6 | 1 | 9 more needed |
| R6 | 2 | 0 | 10 more needed |

#### ROOT CAUSE FINDING: Model-Market Divergence = 95% Wrong

**Corrected Model Accuracy (prediction matched outcome, NOT raw YES outcome rate):**

| Bucket | Game | N | Correct | Accuracy |
|--------|------|---|---------|----------|
| **High div (>0.30)** | CS2 | 23 | 1 | **4.3%** |
| **High div (>0.30)** | Dota2 | 8 | 1 | **12.5%** |
| **High div (>0.30)** | LoL | 3 | 0 | **0%** |
| **High div (>0.30)** | Valorant | 3 | 0 | **0%** |
| Low div (≤0.15) | CS2 | 22 | 16 | **72.7%** |
| Low div (≤0.15) | Dota2 | 9 | 6 | **66.7%** |
| Low div (≤0.15) | LoL | 5 | 5 | **100%** |

**Key insight**: When the Glicko-2 model diverges massively from the market, it is wrong ~95% of the time across ALL games. The market has live in-match information that Glicko-2 (a pre-match rating) does not. When divergence is low (model agrees with market), accuracy is 67-100%.

**Previous analysis was wrong**: Initial queries used `AVG(actual_outcome)` which gives "% of time YES won", NOT model accuracy. For NO-side predictions, actual_outcome=1 means model was WRONG. The corrected query checks if (side=YES AND outcome=1) OR (side=NO AND outcome=0). Dota2 was originally reported as 100% WR on high divergence — actually 12.5%.

### Bug 1: R6 Dead-Market Execution Spam (FIXED)

**Symptom**: The R6 market `0x70256331...` was passing the waterfall (confidence=0.5454, edge=0.3404) but `_execute_esports_trade()` returned `success=False` every 2 seconds for hours. Scan summary showed `opportunities=1, trades=0`.

**Root cause**: The order gateway correctly rejected the trade because the orderbook was dead (best_ask=0.99, best_bid=0.13, spread=0.86). The midpoint LOOKED like 0.20, so `analyze_opportunity` computed edge=0.34 and passed it. But to actually BUY YES, you'd pay 0.99 — killing all edge. Logged as `order_dead_market_spread` and `order_edge_eroded edge_at_fill=-1.0`.

**The bug**: No cooldown after execution failure. The bot retried the same dead-orderbook market every 2s scan cycle indefinitely. This is why 0 trades executed for 6+ hours — the bot was stuck spam-retrying one market.

**Fix**: Added `self._exec_fail_cooldown: Dict[str, float] = {}` in `__init__`. After `_execute_esports_trade()` returns False, store `self._exec_fail_cooldown[market_id] = time.monotonic()`. In scan loop, skip markets still in cooldown (300s, configurable via `ESPORTS_EXEC_FAIL_COOLDOWN_S`). Waterfall counter: `exec_fail_cooldown`.

### Bug 2: Global Model-Market Divergence Cap (IMPLEMENTED)

**Fix**: In `_build_opportunity()`, reject trade when `abs(model_prob - market_price) > ESPORTS_MAX_MODEL_DIVERGENCE` (default 0.25). Logged as `esportsbot_divergence_capped`. This would have blocked 37 trades across all games that had 5.4% accuracy, preserving the 74.4% accurate low-divergence trades.

**Data justification**:
- High div (>0.30): 2/37 correct = 5.4%
- Low div (≤0.15): 29/39 correct = 74.4%
- Cap at 0.25 is conservative — catches the worst offenders while leaving room for moderate disagreement

### Bug 3a: Unknown/Blank Game Tag Backfill (DATA FIX — SQL on VPS)

**Problem**: `esportsbot_pnl_summary` showed `unknown: 119 trades, -$2,407, avg_edge=0.0`. These were pre-game-tagging trades where `event_data` had no `game` field. Plus 1 blank EXIT at -$207.

**Fix**: Ran SQL backfill on VPS joining `trade_events` ENTRY events against `esports_prediction_log.game` to tag RESOLUTION/EXIT events. 58 events tagged. Zero untagged remaining.

### Bug 3b: Resolution Backfill Game Tag Propagation (CODE FIX)

**Problem**: `resolution_backfill.py` Phase 4b and 4b-alt never included the `game` field in RESOLUTION event `event_data`. Every new resolution would create another untagged event.

**Fix**: Both Phase 4b and 4b-alt now query the ENTRY event's `event_data->>'game'` via subquery and pass it through to the RESOLUTION event's `event_data` JSONB.

### Commit Details

**`74c800f` — fix(esports): S135 — exec fail cooldown, divergence cap, game tag backfill**

| File | Change |
|------|--------|
| `bots/esports_bot.py` | +34 lines: `_exec_fail_cooldown` dict, cooldown check in scan loop, divergence cap in `_build_opportunity()` |
| `config/settings.py` | +6 lines: `ESPORTS_EXEC_FAIL_COOLDOWN_S=300`, `ESPORTS_MAX_MODEL_DIVERGENCE=0.25` |
| `base_engine/data/resolution_backfill.py` | +20/-3 lines: game tag extraction from ENTRY in Phase 4b and 4b-alt |
| `tests/unit/test_esports_bot.py` | +29/-18 lines: 3 test fixtures narrowed for divergence cap |

---

## 3. Pending Investigation: Full Code Audit (NOT STARTED)

User requested an exhaustive line-by-line audit of MirrorBot, EsportsBot, and WeatherBot covering:
- All data logging, reading, ingesting, reviewing, storing channels
- Every code line for bugs, errors, inefficiencies
- Full pipeline verification

**Status**: Scope was confirmed (all 3 bots + shared modules), but audit has NOT been started. This is the next task for the continuation agent.

---

## 4. System Overview

### What Is This?
A **15-bot automated Polymarket trading system** running on a VPS (Ubuntu, 34.251.224.21). Currently in **paper trading mode** (`SIMULATION_MODE=true`). Paper trading IS production per CLAUDE.md.

### EsportsBot's Role
Trades **esports match-winner markets** (CS2, Dota2, Valorant, LoL, CoD, R6, SC2, RL) using:
- **Glicko-2 ratings** for team strength modeling (per-game trackers for 8 games)
- **BetaCalibrator** (Kull et al. 2017): `sigmoid(a*ln(p) - b*ln(1-p) + c)` — fits per-game, needs 10+ resolved samples
- **OnlinePlattCalibrator**: River LogisticRegression, streaming update, 30+ samples to activate
- **Conformal prediction**: per-game prediction intervals for uncertainty-aware sizing
- **Per-game ML models**: CS2 (dual-path: pregame + economy), LoL (dual-path: pregame + live), Dota2 (XGBoost), Valorant (XGBoost)
- **Cross-game XGBoost**: blended with Glicko-2 via extremized geometric mean (0.6/0.4 weights)
- **Signal Quality (SQ)** system (S127→S131): 5-component composite, used as SIZING multiplier (not confidence multiplier)
- **Series tracking** — correlated bets across BO3/BO5 series

### Tech Stack
- Python 3.13, asyncio, asyncpg (PostgreSQL), Redis, WebSockets
- PandaScore API (live match data), Polymarket CLOB API (orderbook/prices)
- VPS: Ubuntu-3 at 34.251.224.21 (16GB/4vCPU), systemd service `polymarket-ai`

---

## 5. Prediction Pipeline Flow (Full)

```
scan_and_trade() [line ~1100]
  |-- Phase A: Market discovery (get_active_markets from esports service)
  |-- Phase B: Per-market analysis via _analyze_one() [line ~1171]
  |   |-- S135: Exec fail cooldown check (300s) ← NEW
  |   |-- Exit cooldown check (300s, Redis)
  |   |-- Per-market entry cap (5 per 12h window)
  |   |-- Position re-entry checks (direction match + higher edge bar 0.08)
  |   |-- analyze_opportunity(market_data) [line ~1785]
  |       |-- Game detection (_detect_game, word-boundary regex)
  |       |-- ESPORTS_DISABLED_GAMES check (S134, currently empty/inert)
  |       |-- Monitoring halt check (Brier > 0.30 threshold)
  |       |-- Game exposure cap check ($5K/game)
  |       |-- Observation mode check (48h after game patch)
  |       |-- Market type classification (match_winner/map_winner/tournament/etc.)
  |       |-- _get_model_prediction() → Glicko-2 probability [line ~1888]
  |       |-- BetaCalibrator.calibrate() if fitted [line ~1901]
  |       |-- OnlinePlatt override if fitted (applied to raw, not beta) [line ~1906]
  |       |-- RFLB favorites correction (strength=0.03) [line ~1913]
  |       |-- S133: Early prediction logging (ALL predictions) [line ~1931]
  |       |-- S135: Model-market divergence cap (0.25) ← NEW
  |       |-- Edge validation (YES/NO side selection) [line ~1962]
  |       |-- Conformal interval [if enabled]
  |       |-- Signal Quality computation [~1937]
  |       |-- confidence = side_prob (S131)
  |       |-- Confidence floor gate (0.52 effective)
  |       |-- Edge gate (0.05)
  |       |-- Return opportunity dict or None
  |-- Phase C: Execution (_execute_esports_trade per opportunity) [line ~3127]
  |   |-- S133: Penny/extreme price guard (0.05-0.95)
  |   |-- S132: Opposing-side guard (_entered_market_sides)
  |   |-- Kelly sizing via BotBankrollManager
  |   |-- Size multipliers: phi_factor * dd_factor * game_kelly_mult * edge_decay_mult * SQ
  |   |-- Upset risk scaling
  |   |-- Max-bet cap ($300), min-trade floor ($10)
  |   |-- place_order() → order_gateway → risk_manager (CVaR SKIPPED) → paper_trading
  |   |-- S135: On failure, record _exec_fail_cooldown[market_id] ← NEW
  |   |-- Post-trade: update exposure, daily counters, entered_market_sides
  |-- Exit check (_check_and_execute_exits) [line ~1530]
  |-- WS subscription for new tokens [line ~1269]
```

---

## 6. Scan Waterfall (Full Rejection Funnel)

In order, from `analyze_opportunity` + `_analyze_one`:

1. `exec_fail_cooldown` — S135: market recently failed execution (300s) ← NEW
2. `exit_cooldown` — recently exited (300s, Redis-persisted)
3. `max_entries` — 5 entries per market per 12h window
4. `no_game` — can't detect game from market question
5. `halted` — ESPORTS_DISABLED_GAMES check (S134, currently empty)
6. `halted` — Brier halt (threshold=0.30) — currently halting CS2
7. `exposure_cap` — per-game ($5K) / tournament ($8K) / total ($15K)
8. `observation` — PatchDriftDetector (48h after game patch)
9. `no_prediction` — market type unsupported OR team name matching failed
10. `divergence_capped` — S135: abs(model_prob - market_price) > 0.25 ← NEW
11. `low_confidence` — below 0.52 (effective floor, S131 confidence=side_prob)
12. `low_edge` — below 0.05
13. `reentry_rejected` — has position, wrong direction or insufficient edge (0.08)
14. `passed` → goes to `_execute_esports_trade()`

---

## 7. Exit Logic (with S134 changes)

Full exit flow in `_check_and_execute_exits()` starting at line 1530:

1. **Dead-market guard (S134 Fix A)** [line 1548]: If `current < 0.10 AND entry >= 0.20`, skip exit entirely. Let resolution handle it.
2. **Stop-loss with floor (S134 Fix B)** [line 1560-1573]:
   - Calculate `pnl_pct = (current - entry) / entry`
   - If `pnl_pct <= -stop_pct` (15%):
     - If `current < 0.10`: SKIP stop-loss (floor guard). Logged as `esportsbot_stop_loss_floor_skip`.
     - Otherwise: trigger stop-loss.
3. **Max-hold** [line 1576-1590]: Position held > 48h → trigger exit.
4. **Daily loss limit** [line ~1510]: Daily losses > $10K → halt all exits.
5. **Execution** [line 1594]: SELL order using the SAME token_id.

---

## 8. Signal Quality System (S127→S131)

### Formula
```
confidence = side_prob                                    # Raw model belief
signal_quality = _compute_signal_quality(game, market_id) # [0.30, 1.0]
size *= signal_quality                                    # SQ scales BET SIZE, not probability
```

### 5 Components
| Component | Weight | Source | Default (no data) |
|-----------|--------|--------|-------------------|
| model_agreement | 0.30 | stdev of XGB/CatBoost/Glicko-2/final_prob | 0.70 |
| calibration_score | 0.25 | BetaCalibrator + OnlinePlatt fitted status | 0.50 |
| uncertainty | 0.20 | Glicko-2 phi (matchup_uncertainty) | varies |
| enrichment_depth | 0.15 | Count of enrichment layers that fired | 0.33 |
| brier_component | 0.10 | Rolling game-level Brier | 0.40 |

Computed at 4 sites: main path, WS reactive path, series path, series WS path.

---

## 9. Calibrator & Learning System

### BetaCalibrator [lines 48-149]
- `sigmoid(a*ln(p) - b*ln(1-p) + c)` per-game
- 10+ resolved samples to fit, L-BFGS-B, identity priors, lambda_reg=10.0
- Training data: `esports_prediction_log WHERE actual_outcome IS NOT NULL`

### OnlinePlattCalibrator [lines 152-192]
- River LogisticRegression, 30+ samples to activate
- Applied to RAW probability (not beta-calibrated) to avoid double calibration

### ConformalPredictor
- Per-game, 30+ samples, conservative bounds for sizing
- `ESPORTS_USE_CONFORMAL=true` on VPS

### RFLB [lines 1910-1929]
- Favorites correction: price > 0.70 AND model_prob > 0.60
- `model_prob -= strength * (price - 0.50)`, strength=0.03

---

## 10. Configuration Knobs (All ESPORTS_ Settings)

### Core Trading
| Setting | Default | VPS | Description |
|---------|---------|-----|-------------|
| `ESPORTS_MIN_EDGE` | 0.05 | 0.05 | Min edge to trade |
| `ESPORTS_MIN_CONFIDENCE` | 0.20 | 0.20 | Min confidence (effective floor 0.52) |
| `ESPORTS_MAX_BET_USD` | 300.0 | 300 | Max bet size |
| `ESPORTS_MIN_TRADE_USD` | 10.0 | 10.0 | Min trade size |
| `ESPORTS_KELLY_DEFAULT_FRACTION` | 0.25 | 0.25 | Kelly fraction |
| `ESPORTS_KELLY_MAX_FRACTION` | 0.35 | -- | Kelly cap |
| `ESPORTS_EGM_D` | 1.5 | -- | Extremization factor |

### Risk & Exposure
| Setting | Default | VPS | Description |
|---------|---------|-----|-------------|
| `ESPORTS_STOP_LOSS_PCT` | 0.25 | 0.15 | Stop-loss percentage |
| `ESPORTS_MAX_HOLD_HOURS` | 96 | 48 | Max position hold time |
| `ESPORTS_MAX_GAME_EXPOSURE` | 5000.0 | 5000 | Per-game cap |
| `ESPORTS_MAX_TOURNAMENT_EXPOSURE` | 8000.0 | 8000 | Per-tournament cap |
| `ESPORTS_MAX_TEAM_EXPOSURE` | 2000.0 | 2000 | Per-team cap |
| `ESPORTS_MAX_TOTAL_EXPOSURE_USD` | 15000 | 15000 | Total cap |
| `ESPORTS_PER_MARKET_CAP` | 600 | 600 | Per-market cap |
| `ESPORTS_DAILY_LOSS_LIMIT` | 10000.0 | 10000 | Daily loss halt |
| `ESPORTS_TOTAL_CAPITAL` | 20000.0 | 20000 | Total capital |
| `ESPORTS_DRAWDOWN_HALT_PCT` | 0.40 | -- | Halt at 40% drawdown |
| `ESPORTS_DRAWDOWN_REDUCE_PCT` | 0.20 | -- | Reduce at 20% drawdown |

### Entry Guards
| Setting | Default | VPS | Description |
|---------|---------|-----|-------------|
| `ESPORTS_MIN_ENTRY_PRICE` | 0.05 | -- | Penny floor (S133) |
| `ESPORTS_MAX_ENTRY_PRICE` | 0.95 | -- | Extreme ceiling (S133) |
| `ESPORTS_EXIT_COOLDOWN_SECONDS` | 300.0 | 300 | Post-exit cooldown |
| `ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW` | 5 | 5 | Max entries per window |
| `ESPORTS_ENTRY_WINDOW_HOURS` | 12.0 | 12.0 | Entry counting window |
| `ESPORTS_REENTRY_MIN_EDGE` | 0.08 | 0.08 | Higher edge for re-entry |
| `ESPORTS_DISABLED_GAMES` | "" | "" | Comma-separated (inert) |
| `ESPORTS_EXEC_FAIL_COOLDOWN_S` | 300 | 300 | S135: Cooldown after exec fail |
| `ESPORTS_MAX_MODEL_DIVERGENCE` | 0.25 | 0.25 | S135: Max model-market divergence |

### Model & Calibration
| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_OBSERVATION_HOURS` | 48 | Observation after game patch |
| `ESPORTS_MIN_ACCURACY_TO_TRADE` | 0.52 | Min accuracy to trade game |
| `ESPORTS_BRIER_HALT_THRESHOLD` | 0.30 | Halt game if Brier exceeds |
| `ESPORTS_RFLB_STRENGTH` | 0.03 | Favorites correction strength |
| `ESPORTS_USE_CONFORMAL` | False (VPS: true) | Conformal sizing |
| `ESPORTS_CONFORMAL_ALPHA` | 0.10 | 90% prediction interval |
| `ESPORTS_CONFORMAL_MIN_RESOLVED` | 50 | Min samples for conformal |
| `ESPORTS_MODEL_MIN_ACCURACY` | 0.55 | Model graduation gate |
| `ESPORTS_MODEL_MAX_BRIER` | 0.24 (VPS: 0.248) | Model graduation gate |
| `ESPORTS_RETRAIN_INTERVAL_HOURS` | 24 | Model retrain interval |
| `ESPORTS_KELLY_BRIER_PENALTY` | 0.25 | Kelly reduction for high Brier |
| `ESPORTS_KELLY_BRIER_BOOST` | 0.20 | Kelly boost for low Brier |
| `ESPORTS_KELLY_DEGRADE_BRIER` | 0.28 | Brier threshold for degradation |

### Series / WebSocket / Live Bot
| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_SERIES_MIN_EDGE` | 0.10 | Min edge for series bets |
| `ESPORTS_SERIES_HEDGE_ENABLED` | True | Enable series hedging |
| `ESPORTS_WS_PRICE_CHANGE_PCT` | 0.01 | Min price change for WS trade |
| `ESPORTS_WS_COOLDOWN_SECONDS` | 10 | WS per-market cooldown |
| `ESPORTS_LIVE_COOLDOWN_SECONDS` | 60.0 | Live trade cooldown |
| `ESPORTS_LIVE_POLL_TIMEOUT` | 10 | PandaScore poll timeout (S89) |

---

## 11. Database Schema

### Key Tables

**`trade_events`** (P&L AUTHORITY — partitioned by event_time)
```
Columns: sequence_num, market_id, bot_name, event_type, side, size, price,
         realized_pnl, event_data (JSONB), event_time, correlation_id, idempotency_key
Event types: ENTRY, EXIT, RESOLUTION
Immutability trigger: trg_trade_events_immutable
NOTE: Uses sequence_num NOT id. JSONB column is event_data NOT metadata_json.
```

**`paper_trades`** (legacy, still used for position tracking)
```
Columns: market_id, bot_name, side, price, size, status, resolution,
         realized_pnl, created_at, resolved_at
Status: 'filled' (open), 'resolved', 'sold'
NOTE: Uses price NOT entry_price. No metadata JSONB column.
```

**`esports_prediction_log`** (calibrator training data)
```
Columns: match_id, game, market_id, bot_name, predicted_prob, market_price,
         side, edge, actual_outcome, created_at
ON CONFLICT (match_id, bot_name) UPDATE
actual_outcome filled by S125 fallback from markets table
```

**`traded_markets`** — `bot_names` is TEXT (not array), use `LIKE '%EsportsBot%'`

**`daily_counters`** — `_game_exposure` write-through, restored on startup

---

## 12. State Persistence

| State | Mechanism | Restore Method |
|-------|-----------|----------------|
| `_game_exposure` | daily_counters write-through | `_restore_exposure_from_db()` |
| `_daily_pnl` | Query trade_events SUM | `_restore_daily_pnl_from_db()` |
| `_recently_exited` | Redis TTL (300s) | `_restore_exit_cooldowns_from_redis()` |
| `_market_game` | Restored from ENTRY trade_events | `_restore_market_game_from_db()` |
| `_entered_market_sides` | Restored from ENTRY trade_events | One-time in scan_and_trade (S132) |
| `_exec_fail_cooldown` | In-memory only (S135) | Lost on restart (acceptable — markets change) |
| `_open_positions` | positions table | position_manager |
| Glicko-2 ratings | esports_glicko2_ratings table | `_init_glicko2_trackers()` |
| BetaCalibrator params | Re-fitted from esports_prediction_log | `_check_monitoring_thresholds()` every 10 min |
| `_game_brier_cache` | Seeded on startup + refreshed | `_get_cached_rolling_accuracy()` |
| `_prediction_cache` | In-memory, 1h TTL | Lost on restart (10s re-sync) |

---

## 13. Architecture — File Map

### Core Bot Files
| File | Lines | Role |
|------|-------|------|
| `bots/esports_bot.py` | ~5,900 | Main bot: scan, predict, trade, exit, calibrate, signal quality |
| `bots/esports_live_bot.py` | ~350 | Live in-game trading wrapper |

### Key Line Ranges in esports_bot.py
| Lines | What |
|-------|------|
| 47-149 | `BetaCalibrator` class |
| 151-192 | `OnlinePlattCalibrator` class |
| 195-277 | `__init__` — all instance vars |
| 420-600 | `start()` — client init, Glicko-2, calibrators |
| 636-793 | `on_price_update()` — WS reactive trading |
| 842-1287 | `scan_and_trade()` — main loop, waterfall |
| 1530-1600 | `_check_and_execute_exits()` — S134 Fix A/B |
| 1785-1960 | `analyze_opportunity()` — full prediction pipeline |
| 2182-2372 | `_enrich_prediction()` |
| 2374-2646 | `_get_model_prediction()` — all game paths |
| 3107-3125 | `_classify_market_type()` |
| 3127-3406 | `_execute_esports_trade()` — sizing pipeline |
| ~3473 | `_compute_signal_quality()` |
| 4032-4080 | `_check_monitoring_thresholds()` — Brier halt, calibrator fitting |
| 5259-5638 | Series analysis + trading |

### ML Models
| File | Role |
|------|------|
| `esports/models/lol_win_model.py` | LoL XGBoost + IsotonicRegression |
| `esports/models/cs2_economy_model.py` | CS2 round+map+series |
| `esports/models/dota2_model.py` | Dota2 XGBoost |
| `esports/models/valorant_model.py` | Valorant XGBoost |
| `esports/models/glicko2.py` | Glicko-2 rating system |
| `esports/models/esports_trainer.py` | Training orchestrator |
| `esports/models/conformal_wrapper.py` | Conformal prediction |
| `esports/models/patch_drift.py` | Patch detection + observation mode |

### Data / API
| File | Role |
|------|------|
| `esports/data/esports_data_collector.py` | PandaScore data collection |
| `esports/data/esports_db.py` | DB helpers |
| `esports/data/pandascore_client.py` | PandaScore API (8 games, 1000 req/hr) |
| `esports/data/opendota_client.py` | Dota2 client |

---

## 14. All 8 Games — Status

| Game | ML Model | BetaCalibrator | Total P&L | Status |
|------|----------|----------------|-----------|--------|
| CS2 | CS2EconomyModel | FITTED (n=70) | -$3,301 | **Brier 0.322, HALTED** |
| LoL | LoLWinModel | JUST FITTED (n=10) | -$684 | Active |
| Dota2 | Dota2Model | FITTED (n=28) | -$522 | Active |
| Valorant | ValorantModel | 9/10 (1 more) | +$3,748 | **ONLY profitable. DO NOT TOUCH.** |
| CoD | None | 1/10 | -$601 | Active (no model, user chose to keep) |
| R6 | None | 0/10 | $0 | No markets |
| SC2 | None | 0/10 | +$41 | 1 trade |
| RL | None | 0/10 | $0 | No markets |

---

## 15. Known Issues & Priority Queue

1. **CS2 model quality** — Brier 0.322, worse than random. Glicko-2 has no in-match awareness. Model is systematically wrong when it disagrees with market. Divergence cap (S135) protects when Brier halt flickers off.

2. **LoL calibrator just fitted (10 samples)** — Monitor accuracy now that BetaCalibrator should be active. Previous 7.7% WR may improve.

3. **Dota2 sizing paradox** — 57% WR but negative P&L. Average loss is 2x average win. Kelly/sizing miscalibration.

4. **CoD -$601** — No ML model, Glicko-2 only. User chose to keep enabled for data collection.

5. **`no_prediction` 5-12/scan** — Team name matching failures. Fuzzy matching or alias table would help.

6. **Valorant 1 sample from BetaCalibrator fitting** — Monitor.

7. **Bot may be too conservative** — Many overlapping guards (Brier halt + divergence cap + confidence floor + edge gate + penny guard + SQ sizing). Could be filtering out all tradeable opportunities. Monitor trade count post-S135.

8. **Full code audit requested but NOT started** — User requested exhaustive line-by-line audit of MirrorBot + EsportsBot + WeatherBot.

---

## 16. Critical Traps (DO NOT BREAK)

### EsportsBot-Specific
1. `_resolve_esports_from_clob()` is DELETED — shared pipeline only
2. `confidence = side_prob` (S131) — SQ is sizing multiplier, NOT confidence multiplier
3. `_entered_market_sides` restored from DB on first scan
4. `_prediction_log_cache` dedup: skip if prob unchanged (<0.01) within 10 min
5. `paper_trades` uses `price` not `entry_price`
6. `trade_events` uses `sequence_num` not `id`, JSONB is `event_data` not `metadata_json`
7. PatchDriftDetector: Only set `_patch_timestamps` when `old is not None` (S88)
8. Brier halt: game Brier > 0.30 → ALL that game's markets skipped
9. S135: `_exec_fail_cooldown` is in-memory only, lost on restart (by design)
10. S135: Divergence cap is GLOBAL (all games) at 0.25 — justified by data showing ALL games fail at high divergence

### Shared Infrastructure
11. `trade_events` is P&L AUTHORITY — NEVER read `paper_trades` for P&L
12. YES/NO mandate: `place_order()` requires `side="YES"/"NO"`, NEVER "BUY"/"SELL"
13. Immutability trigger on partitioned tables — disable/re-enable for cleanup
14. RESOLUTION idempotency: INSERT...SELECT WHERE NOT EXISTS (ON CONFLICT broken on partitions)
15. asyncpg JSONB: `CAST(:x AS jsonb)` NOT `:x::jsonb`
16. asyncpg timestamps: `paper_trades` uses tz-naive — `.replace(tzinfo=None)`
17. Python 3.13: local imports shadow top-level for ENTIRE function
18. `traded_markets.bot_names`: TEXT, use `LIKE '%EsportsBot%'`
19. BotBankrollManager = SIZING; risk_manager = LIMITS
20. `risk_manager.calculate_position_size()` DEPRECATED
21. PSEUDO_LABEL_ENABLED=false — DO NOT enable
22. Paper trading IS production
23. NEVER disable games without explicit user instruction

---

## 17. Feedback Rules (CRITICAL)

- **SCOPE LOCK ACTIVE**: Only fix what the handoff or user explicitly requests
- **NEVER disable games** without explicit user instruction
- **Paper trading IS production**
- **P&L Math**: NEVER invert for NO positions. `cost = entry_price * size` ALL sides. `uPnL = (current - entry) * size` ALL sides
- **trade_events is P&L AUTHORITY**
- **Audit Self-Validation**: ALL findings must be self-validated before reporting
- **One fix per commit**

---

## 18. Session History

| Session | Date | Key Changes |
|---------|------|-------------|
| S83 | 2026-03-13 | P7 roadmap, architecture |
| S88 | 2026-03-14 | PatchDriftDetector fix |
| S89 | 2026-03-14 | E2-E5 features + 9 audit fixes |
| S129 | 2026-03-25 | Cross-game mutation fix |
| S131 | 2026-03-25 | SQ → sizing multiplier |
| S132 | 2026-03-25 | Data integrity: delete _resolve_esports_from_clob, opposing-side guard |
| S133 | 2026-03-26 | Data cleanup, guardrails, learning acceleration |
| S134 | 2026-03-26 | Git sync, EXIT hemorrhage fixes, calibration review |
| **S135** | **2026-03-27** | **Exec fail cooldown, divergence cap (0.25), game tag backfill, data analysis correcting model accuracy calculations** |

---

## 19. VPS Operations

```bash
SSH="ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o ConnectTimeout=10 ubuntu@34.251.224.21"

# SSH
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21

# Deploy single file
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem bots/esports_bot.py ubuntu@34.251.224.21:/tmp/
$SSH "sudo cp /tmp/esports_bot.py /opt/polymarket-ai-v2/bots/ && sudo systemctl restart polymarket-ai"

# Logs
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep -i esports"

# DB query
$SSH "timeout 10 sudo -u postgres psql -d polymarket -c 'QUERY'"

# Restart
$SSH "sudo systemctl restart polymarket-ai"
```

---

## 20. Verification Queries

```bash
# Scan health
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'esportsbot_scan_summary' | tail -3"

# S135: Check for divergence cap fires
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep 'esportsbot_divergence_capped' | tail -10"

# S135: Check for exec fail cooldown fires
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep 'exec_fail_cooldown' | tail -10"

# S134: Exit guard fires
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep -E 'exit_skip_dead_market|stop_loss_floor_skip' | tail -10"

# Trade attempts
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep 'EsportsBot trade executed' | tail -10"

# Errors
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep -i esports | grep -iE 'error|exception|traceback' | head -10"
```

```sql
-- Per-game P&L
SELECT COALESCE(event_data->>'game','?') as game, event_type, COUNT(*) as n, ROUND(SUM(realized_pnl)::numeric,2) as pnl
FROM trade_events WHERE bot_name='EsportsBot' AND event_type IN ('EXIT','RESOLUTION')
GROUP BY 1,2 ORDER BY game, event_type;

-- Model accuracy by divergence (THE key diagnostic)
WITH tagged AS (
  SELECT game, side, actual_outcome, predicted_prob, market_price,
    ABS(predicted_prob - market_price) as div,
    CASE WHEN side='YES' AND actual_outcome=1 THEN 1
         WHEN side='NO' AND actual_outcome=0 THEN 1 ELSE 0 END as model_correct
  FROM esports_prediction_log WHERE actual_outcome IS NOT NULL)
SELECT game,
  CASE WHEN div > 0.25 THEN 'over_cap' ELSE 'under_cap' END as bucket,
  COUNT(*), SUM(model_correct), ROUND(AVG(model_correct)::numeric,3) as accuracy
FROM tagged GROUP BY 1,2 ORDER BY game, bucket;

-- Calibrator progress
SELECT game, COUNT(*) total, COUNT(*) FILTER (WHERE actual_outcome IS NOT NULL) resolved
FROM esports_prediction_log GROUP BY game ORDER BY total DESC;

-- Open positions
SELECT COUNT(*) as open, ROUND(SUM(unrealized_pnl)::numeric,2) as upnl, ROUND(SUM(size*entry_price)::numeric,2) as exposure
FROM positions WHERE bot_id='EsportsBot' AND status='open';

-- Data integrity: no untagged events remaining
SELECT COUNT(*) FROM trade_events WHERE bot_name='EsportsBot'
AND event_type='RESOLUTION' AND (event_data->>'game' IS NULL OR event_data->>'game' = '');
-- Should be 0

-- S132 Fix 5: confidence in entries
SELECT event_time, event_data->>'confidence' as conf, event_data->>'signal_quality' as sq, event_data->>'game' as game
FROM trade_events WHERE bot_name='EsportsBot' AND event_type='ENTRY' ORDER BY event_time DESC LIMIT 5;
```

---

## 21. Rollback

```bash
# Full S135 rollback
git revert 74c800f
# Deploy reverted code to VPS

# Full S134+S135 rollback
git revert 74c800f d88a6e9

# Surgical: remove divergence cap only
# In esports_bot.py: remove the divergence_capped check block
# In settings.py: remove ESPORTS_MAX_MODEL_DIVERGENCE

# Surgical: remove exec fail cooldown only
# In esports_bot.py: remove _exec_fail_cooldown dict and check block
# In settings.py: remove ESPORTS_EXEC_FAIL_COOLDOWN_S
```
