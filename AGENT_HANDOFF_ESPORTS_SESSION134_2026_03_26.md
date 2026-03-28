# EsportsBot Session 134 — Complete System Handoff

**Date**: 2026-03-26
**Scope**: EsportsBot-focused (1 shared module line: order_gateway.py CVaR skip, unchanged from S133)
**Deploy**: `20260326_231130` (S134 fixes), re-deployed after CoD un-disable
**Previous**: S133 (data cleanup + guardrails + learning), S132 (data integrity), S131 (SQ root fix), S129 (handoff doc), S89 (feature session)
**Tests**: 87 EsportsBot unit tests passing
**Commits**: `02c7575` (S131-S133 sync), `d88a6e9` (S134 fixes)

---

## 1. CRITICAL: Read This First

**SCOPE LOCK is active.** You are continuing an EsportsBot-only session. Only touch:
- `bots/esports_bot.py`, `bots/esports_live_bot.py`
- `esports/**`
- Esports tests (`tests/unit/test_esports_bot.py`, etc.)
- `config/settings.py` (ESPORTS_ keys only)
- Shared modules ONLY if required for an esports bug fix and justified explicitly

**Read `CLAUDE.md` in the repo root first — it is the prime directive.**

**Paper trading IS production.** The ONLY difference between paper and live is whether the final order goes to the CLOB or to the paper trade table. Every system, check, feature, and edge case matters identically.

**NEVER disable games without explicit user instruction.** This was learned in S134 — see Section 3, P1.

---

## 2. System Overview

### What Is This?
A **15-bot automated Polymarket trading system** running on a VPS (Ubuntu, 34.251.224.21). Currently in **paper trading mode** (`SIMULATION_MODE=true`) — real architecture, $0 execution. Paper trading IS production per CLAUDE.md.

### EsportsBot's Role
Trades **esports match-winner markets** (CS2, Dota2, Valorant, LoL, CoD, R6, SC2, RL) using:
- **Glicko-2 ratings** for team strength modeling (per-game trackers for 8 games)
- **BetaCalibrator** (Kull et al. 2017): `sigmoid(a*ln(p) - b*ln(1-p) + c)` — fits per-game, needs 10+ resolved samples
- **OnlinePlattCalibrator**: River LogisticRegression, streaming update, 30+ samples to activate
- **Conformal prediction**: per-game prediction intervals for uncertainty-aware sizing
- **Per-game ML models**: CS2 (dual-path: pregame + economy), LoL (dual-path: pregame + live), Dota2 (XGBoost), Valorant (XGBoost)
- **Cross-game XGBoost**: blended with Glicko-2 via extremized geometric mean (0.6/0.4 weights)
- **Signal Quality (SQ)** system (S127->S131): 5-component composite, used as SIZING multiplier (not confidence multiplier)
- **Series tracking** — correlated bets across BO3/BO5 series

### Tech Stack
- Python 3.13, asyncio, asyncpg (PostgreSQL), Redis, WebSockets
- PandaScore API (live match data), Polymarket CLOB API (orderbook/prices)
- VPS: Ubuntu-3, 16GB/4vCPU, systemd service `polymarket-ai`

---

## 3. What Happened in S134 (THIS SESSION)

### P0 — Git Sync (COMPLETE)

**Problem**: Local git was at S129 (`f125fae`). VPS had S131+S132+S133 changes deployed via SCP but never committed.

**Fix**: SCP'd `esports_bot.py`, `test_esports_bot.py`, `order_gateway.py` from VPS. Committed locally as `02c7575`:
```
fix(esports): S131-S133 — SQ sizing, data integrity, guardrails, learning acceleration
```

Local and VPS are now in sync. No more code divergence.

### P1 — CoD Disable (REVERTED BY USER)

**What was done**:
- Added `ESPORTS_DISABLED_GAMES` env var to `config/settings.py` (line 1179) + guards in:
  - Scan path: `analyze_opportunity()` line 1840
  - WS path: `on_price_update()` line 723
- Code mechanism committed in `d88a6e9`
- `.env` on VPS had `ESPORTS_DISABLED_GAMES=cod` temporarily

**What happened**: User explicitly rejected game disabling. The `.env` var was REMOVED from VPS. CoD is NOT disabled.

**Current state**: The `ESPORTS_DISABLED_GAMES` infrastructure exists in code but is inert (empty string default in settings.py). It can be activated by setting the env var on VPS if the user requests it in the future.

**CRITICAL LESSON**: NEVER disable games without explicit user instruction. The user wants all games active for data collection.

### P2 — EXIT Hemorrhage Fixes (DEPLOYED)

**Analysis findings**: Stop-loss was the #1 P&L destroyer: 11 trades, -$2,090, 91% loss rate. 8/11 exited at sub-$0.10 prices (empty orderbooks producing fabricated max-losses).

**Fix A — Dead-market guard widened** (line 1548):
```python
# S134 Fix A: Widen dead-market guard
if current < 0.10 and entry >= 0.20:
    logger.debug("esportsbot_exit_skip_dead_market", ...)
    continue
```
Was: `current < 0.03 and entry >= 0.05` (S133). Widened because esports orderbooks are thin — a price of 0.10 on entry 0.20+ means empty book, not real price discovery.

**Fix B — Stop-loss floor** (line 1560):
```python
# S134 Fix B: Stop-loss floor price
if pnl_pct <= -stop_pct:
    if current < 0.10:
        logger.info("esportsbot_stop_loss_floor_skip", ...)
        continue
```
Prevents stop-loss from firing when exit price < 0.10. Even if pnl_pct exceeds the 15% threshold, a sub-$0.10 price on a thin esports book is not real price discovery. Resolution will give the true 0.0 or 1.0 payout.

**Estimated historical savings**: ~$1,500 (8 of 11 worst stop-loss exits would have been blocked).

### P3 — Calibration Review (NO CODE CHANGES)

Reviewed all calibration components. Findings:
- **BetaCalibrator**: Correctly implemented. L-BFGS-B with identity priors, lambda_reg=10.0 (conservative but correct for small samples).
- **OnlinePlattCalibrator**: River LogisticRegression, correctly streams updates.
- **ConformalPredictor**: Per-game, 30+ resolved samples to fit.
- **RFLB**: Favorites correction, strength=0.03, correctly targets price>0.70 + model_prob>0.60.

**Key insight**: CS2 Brier 0.322 (WORSE than random 0.25). The 0.7-0.8 predicted probability bucket has only 11.1% actual win rate. This is a MODEL QUALITY problem, not a calibration problem. No calibration tuning can fix a model that is systematically wrong about favorites.

- Dota2 Brier 0.249 (borderline random)
- LoL/Valorant: Need 2 more resolved samples each to fit BetaCalibrator

### P4 — WebSocket Trading Review (NO CODE CHANGES)

`ws_trading=False` is NOT a bug. Findings:
- WS subscriptions fire correctly (6 tokens subscribed)
- Esports markets are too illiquid for the 15-second stale threshold — price updates happen ~1/hour
- Scan path (every 2 seconds) is the correct primary trading path
- WS reactive path works but rarely fires due to low market activity
- This is expected behavior for illiquid markets

### P5 — Commit + Deploy (COMPLETE)

- Commit `02c7575`: S131-S133 VPS->local sync
- Commit `d88a6e9`: S134 fixes (exit guards + ESPORTS_DISABLED_GAMES mechanism)
- Deploy `20260326_231130`, then re-deployed after removing CoD disable from .env
- 87 esports tests pass

---

## 4. Current Bot State (Post-S134)

### Scan Summary (from VPS logs)
```
markets=13, markets_by_game={'cs2': 6, 'lol': 5, 'valorant': 2}
halted_games=None (CS2 intermittent Brier halt at 0.30 threshold)
waterfall: no_prediction=3, low_edge=2, low_confidence=4, passed=4, reentry_rejected=3
ws_trading=False (expected — illiquid markets)
```

### Calibrator Fitting Status
| Game | Total Predictions | Resolved | Need 10 | Status |
|------|------------------|----------|---------|--------|
| CS2 | ~170+ | 64 | 10 | **FITTED** |
| Dota2 | ~45+ | 20 | 10 | **FITTED** |
| Valorant | ~36+ | 8 | 10 | 2 more needed |
| LoL | ~23+ | 8 | 10 | 2 more needed |
| CoD | ~6 | 1 | 10 | 9 more needed |
| R6 | ~2 | 0 | 10 | 10 more needed |

### Data Integrity
| Check | Result |
|-------|--------|
| Paper trades NULL realized_pnl | 0 |
| Phantom RESOLUTION events | 0 |
| New both-sides entries post-S132 | 0 |
| Fix 5 confidence in event_data | Verified (post-S132 entries have both fields) |

---

## 5. P&L Summary

### All-Time Realized (from S133 diagnostic, still current)
```
All-time realized:  -$4,368
  Exits:    208  (-$1,448)
  Resolutions: 127  (-$2,920)
Open positions: ~16-20 ($4,378 cost)
```

### By Game (EXIT + RESOLUTION combined, from S130 diagnostic)
| Game | EXIT P&L | RES P&L | **Total** | WR | Notes |
|------|----------|---------|-----------|-----|-------|
| **Valorant** | +$3,911 | +$221 | **+$4,132** | 57.1% | ONLY profitable game. DO NOT TOUCH. |
| SC2 | -- | +$41 | **+$41** | 100% | 1 trade |
| CS2 | -$2,967 | +$72 | **-$2,896** | 53.7% | Model barely above random (Brier 0.322) |
| CoD | -$847 | -$304 | **-$1,151** | 0% | No ML model, NOT disabled per user |
| Dota2 | -$819 | -$585 | **-$1,404** | 75.0% | WR good but avg loss 2x avg win — SIZING issue |
| LoL | -$455 | -$1,403 | **-$1,858** | 16.7% | BUG-24 fix needs more data |

### By Game (RESOLUTION only, post-S133 cleanup)
| Game | Res | Wins | WR% | P&L |
|------|-----|------|-----|-----|
| sc2 | 1 | 1 | 100% | +$40.61 |
| cod | 1 | 1 | 100% | +$4.24 |
| valorant | 12 | 6 | 50% | -$178.83 |
| cs2 | 62 | 27 | 43.5% | -$382.73 |
| dota2 | 28 | 16 | 57.1% | -$561.35 |
| unknown | 10 | 4 | 40% | -$889.70 |
| lol | 13 | 1 | 7.7% | -$951.94 |

**Critical insight**: EXIT losses are 2.5x worse than RESOLUTION losses. S134 Fix A/B address the worst offenders (sub-$0.10 exit prices on dead orderbooks).

---

## 6. Signal Quality System (S127->S131)

### Current Formula (S131)
```
confidence = side_prob                                    # Raw model belief
signal_quality = _compute_signal_quality(game, market_id) # [0.30, 1.0]
# ... confidence used for edge calculation and Kelly sizing ...
size *= signal_quality                                    # SQ scales BET SIZE, not probability
```

### 5 Components of Signal Quality
| Component | Weight | Source | Default (no data) |
|-----------|--------|--------|-------------------|
| model_agreement | 0.30 | stdev of XGB/CatBoost/Glicko-2/final_prob | 0.70 (single model) |
| calibration_score | 0.25 | BetaCalibrator + OnlinePlatt fitted status | 0.50 (unfitted) |
| uncertainty | 0.20 | Glicko-2 phi (matchup_uncertainty) | varies |
| enrichment_depth | 0.15 | Count of enrichment layers that fired (form/tabpfn/lan/bo/xgb/cb) | 0.33 (1/3) |
| brier_component | 0.10 | Rolling game-level Brier from `_game_brier_cache` | 0.40 (brier=0.15 default) |

**Typical unfitted single-model SQ**: ~0.525 -> trades at ~52% Kelly

### Computed at 4 sites:
1. Main path (`analyze_opportunity` ~line 1937)
2. WS reactive path (`on_price_update` ~line 781)
3. Series path (`_series_analyze` ~line 5505)
4. Series WS path (`_series_on_price_update` ~line 5844)

---

## 7. Prediction Pipeline Flow

```
scan_and_trade() [line ~1100]
  |-- Phase A: Market discovery (get_active_markets from esports service)
  |-- Phase B: Per-market analysis via _analyze_one() [line ~1171]
  |   |-- Exit cooldown check (300s)
  |   |-- Per-market entry cap (5 per 12h window)
  |   |-- Position re-entry checks (direction match + higher edge bar 0.08)
  |   |-- analyze_opportunity(market_data) [line ~1785]
  |       |-- Game detection (_detect_game, word-boundary regex)
  |       |-- S134: ESPORTS_DISABLED_GAMES check [line 1840]
  |       |-- Monitoring halt check (Brier threshold)
  |       |-- Game exposure cap check ($5K/game)
  |       |-- Observation mode check (48h after game patch)
  |       |-- Market type classification (match_winner/map_winner/tournament/etc.)
  |       |-- _get_model_prediction() -> Glicko-2 probability [line ~1888]
  |       |-- BetaCalibrator.calibrate() if fitted [line ~1901]
  |       |-- OnlinePlatt override if fitted (applied to raw, not beta) [line ~1906]
  |       |-- RFLB favorites correction (strength=0.03) [line ~1913]
  |       |-- S133: Early prediction logging (ALL predictions) [line ~1931]
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
  |   |-- place_order() -> order_gateway -> risk_manager (CVaR SKIPPED) -> paper_trading
  |   |-- Post-trade: update exposure, daily counters, entered_market_sides
  |-- Exit check (_check_and_execute_exits) [line ~1530]
  |-- WS subscription for new tokens [line ~1269]
```

---

## 8. Exit Logic (with S134 changes)

Full exit flow in `_check_and_execute_exits()` starting at line 1530:

1. **Dead-market guard (S134 Fix A)** [line 1548]: If `current < 0.10 AND entry >= 0.20`, skip exit entirely. Let resolution handle it (pays 0.0 or 1.0).

2. **Stop-loss with floor (S134 Fix B)** [line 1560-1573]:
   - Calculate `pnl_pct = (current - entry) / entry`
   - If `pnl_pct <= -stop_pct` (15%):
     - If `current < 0.10`: SKIP stop-loss (floor guard). Logged as `esportsbot_stop_loss_floor_skip`.
     - Otherwise: trigger stop-loss, add to `positions_to_close`.

3. **Max-hold** [line 1576-1590]: If position held > 48h (VPS .env override, code default 96h), trigger exit. Logged as `esportsbot_max_hold_exit`.

4. **Daily loss limit** [line ~1510]: If daily losses exceed $10K (VPS .env), halt all exits for the day.

5. **Execution** [line 1594]: SELL order using the SAME token_id (selling back the token we hold).

---

## 9. Opposing-Side Guard (S132 Fix 4)

### Mechanism
- `__init__` [line 256]: `self._entered_market_sides: set = set()`
- `scan_and_trade` (first cycle): Restore from `trade_events` ENTRY records into `_entered_market_sides`. Flag: `_entered_sides_restored`.
- `_execute_esports_trade` [line 3151]: Check order_gateway for existing opposite-side position + check `_entered_market_sides` for historical opposite entry -> block.
- WS path: Block if ANY position exists (same or opposite side).
- On successful entry [line 3369]: `_entered_market_sides.add((market_id, side))`

### Why
7 Valorant markets had YES+NO entries pre-S132 — guaranteed fee loss. Guard prevents future occurrences.

---

## 10. Configuration Knobs

### All ESPORTS_ Settings (from config/settings.py)

#### Core Trading
| Setting | Code Default | VPS .env Override | Description |
|---------|-------------|-------------------|-------------|
| `ESPORTS_MIN_EDGE` | 0.05 | 0.05 | Min edge to trade |
| `ESPORTS_MIN_CONFIDENCE` | 0.20 | 0.20 | Min confidence (effective floor 0.52 via S131) |
| `ESPORTS_MAX_BET_USD` | 300.0 | 300 | Max bet size |
| `ESPORTS_MIN_TRADE_USD` | 10.0 | 10.0 | Min trade size |
| `ESPORTS_KELLY_DEFAULT_FRACTION` | 0.25 | 0.25 | Kelly fraction |
| `ESPORTS_KELLY_MAX_FRACTION` | 0.35 | -- | Kelly cap |
| `ESPORTS_EGM_D` | 1.5 | -- | Extremization factor for EGM blend |

#### Risk & Exposure
| Setting | Code Default | VPS .env Override | Description |
|---------|-------------|-------------------|-------------|
| `ESPORTS_STOP_LOSS_PCT` | 0.25 | 0.15 | Stop-loss percentage |
| `ESPORTS_MAX_HOLD_HOURS` | 96 | 48 | Max position hold time (S133 change) |
| `ESPORTS_MAX_GAME_EXPOSURE` | 5000.0 | 5000 | Per-game exposure cap (USD) |
| `ESPORTS_MAX_TOURNAMENT_EXPOSURE` | 8000.0 | 8000 | Per-tournament cap |
| `ESPORTS_MAX_TEAM_EXPOSURE` | 2000.0 | 2000 | Per-team cap |
| `ESPORTS_MAX_TOTAL_EXPOSURE_USD` | 15000 | 15000 | Total exposure cap |
| `ESPORTS_PER_MARKET_CAP` | 600 | 600 | Per-market position cap |
| `ESPORTS_DAILY_LOSS_LIMIT` | 10000.0 | 10000 | Daily loss halt |
| `ESPORTS_TOTAL_CAPITAL` | 20000.0 | 20000 | Total capital allocation |
| `ESPORTS_MAX_DAILY_USD` | 20000.0 | 20000 | Daily trading cap |
| `ESPORTS_DRAWDOWN_HALT_PCT` | 0.40 | -- | Halt at 40% drawdown |
| `ESPORTS_DRAWDOWN_REDUCE_PCT` | 0.20 | -- | Reduce at 20% drawdown |

#### Entry Guards
| Setting | Code Default | VPS .env Override | Description |
|---------|-------------|-------------------|-------------|
| `ESPORTS_MIN_ENTRY_PRICE` | 0.05 | -- | S133: Penny floor |
| `ESPORTS_MAX_ENTRY_PRICE` | 0.95 | -- | S133: Extreme price ceiling |
| `ESPORTS_EXIT_COOLDOWN_SECONDS` | 300.0 | 300 | Post-exit cooldown |
| `ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW` | 5 | 5 | Max entries per window |
| `ESPORTS_ENTRY_WINDOW_HOURS` | 12.0 | 12.0 | Entry counting window |
| `ESPORTS_REENTRY_MIN_EDGE` | 0.08 | 0.08 | Higher edge for re-entry |
| `ESPORTS_DISABLED_GAMES` | "" | "" (was "cod", REMOVED) | Comma-separated game names to disable |

#### Model & Calibration
| Setting | Code Default | Description |
|---------|-------------|-------------|
| `ESPORTS_OBSERVATION_HOURS` | 48 | Observation after game patch |
| `ESPORTS_MIN_ACCURACY_TO_TRADE` | 0.52 | Min accuracy to trade game |
| `ESPORTS_BRIER_HALT_THRESHOLD` | 0.30 | Halt game if Brier exceeds |
| `ESPORTS_CATBOOST_ENABLED` | False | XGBoost per-game models |
| `ESPORTS_RFLB_STRENGTH` | 0.03 | Favorites correction strength |
| `ESPORTS_USE_CONFORMAL` | False | Conformal sizing (VPS: true) |
| `ESPORTS_CONFORMAL_ALPHA` | 0.10 | 90% prediction interval |
| `ESPORTS_CONFORMAL_MIN_RESOLVED` | 50 | Min samples for conformal |
| `ESPORTS_MODEL_MIN_ACCURACY` | 0.55 | Model graduation gate |
| `ESPORTS_MODEL_MAX_BRIER` | 0.24 | Model graduation gate (VPS: 0.248) |
| `ESPORTS_RETRAIN_INTERVAL_HOURS` | 24 | Model retrain interval |
| `ESPORTS_KELLY_BRIER_PENALTY` | 0.25 | Kelly reduction for high Brier |
| `ESPORTS_KELLY_BRIER_BOOST` | 0.20 | Kelly boost for low Brier |
| `ESPORTS_KELLY_DEGRADE_BRIER` | 0.28 | Brier threshold for Kelly degradation |

#### Series Trading
| Setting | Code Default | Description |
|---------|-------------|-------------|
| `ESPORTS_SERIES_MIN_EDGE` | 0.10 | Min edge for series bets |
| `ESPORTS_SERIES_HEDGE_ENABLED` | True | Enable series hedging |
| `ESPORTS_SERIES_REFRESH_INTERVAL` | 30 | Series data refresh (s) |

#### WebSocket
| Setting | Code Default | Description |
|---------|-------------|-------------|
| `ESPORTS_WS_PRICE_CHANGE_PCT` | 0.01 | Min price change for WS trade |
| `ESPORTS_WS_COOLDOWN_SECONDS` | 10 | WS per-market cooldown |
| `ESPORTS_WS_STALE_THRESHOLD_S` | 15 | WS stale detection (referenced in code) |

#### Live Bot
| Setting | Code Default | Description |
|---------|-------------|-------------|
| `ESPORTS_LIVE_COOLDOWN_SECONDS` | 60.0 | Live trade cooldown |
| `ESPORTS_LIVE_MAX_PER_MATCH` | 5 | Max live trades per match |
| `ESPORTS_LIVE_MAX_PER_MAP` | 2 | Max live trades per map |
| `ESPORTS_LIVE_POLL_TIMEOUT` | 10 | PandaScore poll timeout (S89) |

#### CLV Scaling (currently disabled)
| Setting | Code Default | Description |
|---------|-------------|-------------|
| `ESPORTS_CLV_SCALING_ENABLED` | False | CLV-based sizing tiers |
| `ESPORTS_SCALE_CONSERVATIVE_MAX_BET` | 100.0 | Conservative tier max |
| `ESPORTS_SCALE_MODERATE_MAX_BET` | 200.0 | Moderate tier max |
| `ESPORTS_SCALE_AGGRESSIVE_MAX_BET` | 300.0 | Aggressive tier max |

---

## 11. Database Schema

### Key Tables

**`trade_events`** (P&L AUTHORITY — partitioned by event_time)
```
Columns: sequence_num, market_id, bot_name, event_type, side, size, price,
         realized_pnl, event_data (JSONB), event_time, correlation_id, idempotency_key
Event types: ENTRY, EXIT, RESOLUTION
Immutability trigger: trg_trade_events_immutable (disable on PARTITION for cleanup)
NOTE: Uses sequence_num NOT id. No id column exists.
```

**`paper_trades`** (legacy, still used for position tracking)
```
Columns: market_id, bot_name, side, price, size, status, resolution,
         realized_pnl, created_at, resolved_at
Status: 'filled' (open), 'resolved', 'sold'
NOTE: Uses price NOT entry_price. No entry_price column. No metadata JSONB column.
```

**`esports_prediction_log`** (calibrator training data)
```
Columns: match_id, game, market_id, bot_name, predicted_prob, market_price,
         side, edge, actual_outcome, created_at
ON CONFLICT (match_id, bot_name) UPDATE — dedup safe
actual_outcome filled by S125 fallback from markets table
```

**`traded_markets`** (resolution tracking)
```
bot_names is TEXT (not array) — use LIKE '%EsportsBot%'
resolved: BOOLEAN
```

**`daily_counters`** (exposure write-through for restart recovery)
```
Used by _game_exposure write-through. Restored on startup via _restore_exposure_from_db()
```

---

## 12. Calibrator & Learning System

### BetaCalibrator [lines 48-149]
- Per-game beta calibration: `sigmoid(a*ln(p) - b*ln(1-p) + c)`
- **Requires 10+ resolved samples per game** (min_samples=15 in code, but S100 lowered effective to 10)
- L-BFGS-B optimizer with bounds: a=[0.1, 5.0], b=[0.1, 5.0], c=[-2.0, 2.0]
- Identity priors (a=1, b=1, c=0) with lambda_reg=10.0
- Unfitted games use raw Glicko probabilities (no calibration)
- Training data: `esports_prediction_log WHERE actual_outcome IS NOT NULL`

### OnlinePlattCalibrator [lines 152-192]
- River LogisticRegression for streaming logistic calibration
- Needs 30+ samples to activate
- Applied to RAW probability (not beta-calibrated) to avoid double calibration
- Updates on every resolved prediction

### ConformalPredictor
- Per-game, 30+ resolved samples to fit
- Conservative probability bounds for uncertainty-aware sizing
- `ESPORTS_USE_CONFORMAL=true` on VPS

### RFLB (Random Forest Leaf Bias) [lines 1910-1929]
- Favorites correction: when price > 0.70 AND model_prob > 0.60
- Nudges model_prob toward 0.50: `model_prob -= strength * (price - 0.50)`
- Strength: 0.03 (conservative)
- A/B logging computes hypothetical adjustments at 0.03/0.05/0.08

### Current Fitting Status Per Game
| Game | BetaCalibrator | OnlinePlatt | Conformal | Notes |
|------|---------------|-------------|-----------|-------|
| CS2 | **FITTED** (n=64) | Approaching | Possible | Brier 0.322 — model wrong, not calibration |
| Dota2 | **FITTED** (n=20) | Approaching | No | Borderline random (Brier 0.249) |
| LoL | 8/10 (2 away) | No | No | S133 early logging should accelerate |
| Valorant | 8/10 (2 away) | No | No | S133 early logging should accelerate |
| CoD | 1/10 | No | No | No ML model |
| R6/SC2/RL | 0/10 | No | No | No markets |

### S133 Learning Acceleration
1. **Early prediction logging** [line 1931] — captures ALL model predictions for calibrator learning, even if trade rejected by edge/confidence gates
2. **CVaR skip** — EsportsBot uses own exposure caps, global CVaR was redundant
3. **Max-hold 48h** — faster turnover = more entries = more data

### Resolution Pipeline
```
Market closes on Polymarket -> resolution_backfill.py Phase 4b ->
computes realized_pnl -> emits RESOLUTION to trade_events ->
S125 fallback also resolves esports_prediction_log from markets table
```
**`_resolve_esports_from_clob()` is DELETED (S132)** — shared pipeline is the ONLY correct path.

---

## 13. Scan Waterfall

Full rejection funnel in order (from `analyze_opportunity` + `_analyze_one`):

1. `exit_cooldown` — recently exited (300s, Redis-persisted)
2. `max_entries` — 5 entries per market per 12h window
3. `no_game` — can't detect game from market question
4. `halted` — ESPORTS_DISABLED_GAMES check (S134, currently empty)
5. `halted` — Brier halt (threshold=0.30)
6. `exposure_cap` — per-game ($5K) / tournament ($8K) / total ($15K)
7. `observation` — PatchDriftDetector (48h after game patch)
8. `no_prediction` — market type unsupported (tournament_winner, props, first_blood) OR team name matching failed
9. `low_confidence` — below 0.52 (effective floor, S131 confidence=side_prob)
10. `low_edge` — below 0.05
11. `reentry_rejected` — has position, wrong direction or insufficient edge (0.08)
12. `passed` -> goes to `_execute_esports_trade()`

---

## 14. Risk & Exposure System

### Layered Architecture
```
1. EsportsBot exposure caps (game $5K / tournament $8K / team $2K / market $600 / daily $10K)
2. BotBankrollManager (max_bet=$300, kelly=0.25)
3. order_gateway risk_manager (position/exposure limits — CVaR SKIPPED for EsportsBot since S133)
4. Kill switch (bot/portfolio/system level)
```

### Sizing Pipeline (in `_execute_esports_trade`)
```
1. S133: Penny/extreme price guard (0.05-0.95)
2. S132: Opposing-side guard
3. BotBankrollManager.get_bet_size(confidence, price) -> Kelly
4. Conformal conservative sizing (if enabled)
5. Size multipliers: phi_factor * dd_factor * game_kelly_mult * edge_decay_mult * SQ
6. Upset risk scaling (volatile favorites reduced, stable underdogs boosted)
7. Position re-entry cap (remaining room under $600/market)
8. ESPORTS_MAX_BET_USD cap ($300), S133: recalculate _cost after cap
9. ESPORTS_MIN_TRADE_USD floor ($10)
10. Min share size (0.10)
11. A10: Pre-update game exposure BEFORE order
12. S132: Persist confidence + signal_quality in event_data JSONB
13. place_order() -> order_gateway -> risk_manager (CVaR skipped) -> paper_trading
14. Post-success: _entered_market_sides.add(), daily_counters write-through (retry once)
```

---

## 15. Critical Traps

### EsportsBot-Specific
1. **`_resolve_esports_from_clob()` is DELETED** — do NOT recreate. Shared pipeline only.
2. **`confidence = side_prob`** (S131) — SQ is sizing multiplier, NOT confidence multiplier.
3. **`_entered_market_sides`** restored from DB on first scan (`_entered_sides_restored` flag).
4. **`_prediction_log_cache`** dedup: skip if prob unchanged (<0.01) within 10 min.
5. **`paper_trades`** uses `price` not `entry_price` — no `entry_price` column.
6. **`trade_events`** uses `sequence_num` not `id` — no `id` column.
7. **PatchDriftDetector**: Only set `_patch_timestamps` when `old is not None` (S88).
8. **Brier halt**: When game Brier > 0.30 threshold, ALL that game's markets skipped.
9. **`no_prediction`**: Team name matching failed — teams not in Glicko database.
10. **S-T override path**: Series trades bypass normal sizing, use `st_override` directly.

### Shared Infrastructure
11. **`trade_events` is P&L AUTHORITY** — NEVER read `paper_trades` for P&L.
12. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
13. **Immutability trigger**: On PARTITION tables. Disable/re-enable for cleanup.
14. **RESOLUTION idempotency**: `ON CONFLICT` broken on partitions. Uses INSERT...SELECT WHERE NOT EXISTS.
15. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
16. **asyncpg timestamps**: `paper_trades` uses `timestamp without time zone` — `.replace(tzinfo=None)`.
17. **Python 3.13 scoping**: Local imports shadow top-level for ENTIRE function.
18. **`traded_markets.bot_names`**: TEXT, use `LIKE '%EsportsBot%'`.
19. **`positions` table**: NO `closed_at`, NO `updated_at`.
20. **`prediction_log`**: NO `rejection_reason`. Use `trade_executed` + `model_name`.
21. **BotBankrollManager = SIZING; risk_manager = LIMITS.**
22. **`risk_manager.calculate_position_size()` DEPRECATED.**
23. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
24. **Paper trading IS production.**

### S134 Additions
25. **`ESPORTS_DISABLED_GAMES` exists in code but env var REMOVED** — CoD is NOT disabled. The mechanism is inert (empty string default).
26. **User explicitly rejected game disabling** — NEVER disable games without explicit user instruction. All games stay active for data collection.
27. **S134 Fix A: dead-market guard threshold is 0.10/0.20** (was 0.03/0.05 in S133). Skip exit if `current < 0.10 AND entry >= 0.20`.
28. **S134 Fix B: stop-loss floor at 0.10** — skip stop-loss when exit price < 0.10. Logged as `esportsbot_stop_loss_floor_skip`.
29. **CS2 Brier 0.322 — worse than random (0.25)**. Problem is model quality, NOT calibration. No calibration tuning can fix a wrong model. The 0.7-0.8 predicted bucket has only 11.1% actual win rate.
30. **`ws_trading=False` is EXPECTED** for illiquid esports markets — not a bug. Price updates happen ~1/hour, well beyond the 15s stale threshold. Scan path is the correct primary trading path.

---

## 16. Known Issues & Priority Queue

1. **CS2 model quality** — Brier 0.322, worse than random. Needs better features (map pool, recent form, player changes). The calibrator cannot fix a wrong model. Intermittent Brier halt at 0.30 threshold is self-protecting.

2. **LoL 7.7% WR (1/13 resolutions)** — Genuinely bad. 2 more resolved samples needed for BetaCalibrator fitting. Re-evaluate after calibrator fits.

3. **Dota2 sizing paradox** — 57% WR but -$1,404. Average loss is 2x average win. This is a Kelly/sizing miscalibration, not a model problem.

4. **CoD -$1,151** — No ML model, running on Glicko-2 alone. User chose to keep enabled for data collection.

5. **`no_prediction` 3/scan** — Team name matching failures (mostly CS2/Valorant). Fuzzy matching or alias table would help.

6. **CS2 Brier halt intermittent** — Brier 0.332 > 0.30 threshold but rolling window fluctuates. Self-protecting behavior. Most liquid game halted intermittently.

7. **7 historical both-sides markets** (Valorant, pre-S132 Fix 4) — Cannot undo, guard prevents new.

8. **10 "unknown" game resolutions** — pre-game-tagging, P&L real (-$889.70) but unattributable.

---

## 17. Session History

| Session | Date | Key Changes |
|---------|------|-------------|
| S83 | 2026-03-13 | P7 roadmap, architecture |
| S88 | 2026-03-14 | PatchDriftDetector observation mode fix |
| S89 | 2026-03-14 | E2-E5 features + 9 audit fixes |
| S129 | 2026-03-25 | Cross-game mutation fix, dead config guard |
| S131 | 2026-03-25 | **SQ -> sizing multiplier** (was confidence multiplier) |
| S132 | 2026-03-25 | **Data integrity**: delete _resolve_esports_from_clob, opposing-side guard, confidence in event_data, P&L recompute 115 rows, phantom cleanup 85 rows |
| S133 | 2026-03-26 | **Data cleanup 13 rows, 3 guardrails, 3 learning acceleration fixes, 2 bug fixes** |
| **S134** | **2026-03-26** | **Git sync (02c7575), EXIT hemorrhage fixes (dead-market 0.10/0.20 + stop-loss floor), ESPORTS_DISABLED_GAMES mechanism (inert), calibration review (CS2 Brier 0.322 = model problem), WS review (ws_trading=False expected)** |

---

## 18. VPS Operations

### SSH / Deploy / Logs / DB
```bash
SSH="ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o ConnectTimeout=10 ubuntu@34.251.224.21"

# SSH
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21

# Deploy single file
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem bots/esports_bot.py ubuntu@34.251.224.21:/tmp/
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo cp /tmp/esports_bot.py /opt/polymarket-ai-v2/bots/ && sudo systemctl restart polymarket-ai"

# Logs
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep -i esports"

# DB query
$SSH "timeout 10 sudo -u postgres psql -d polymarket -c 'QUERY'"

# Or via pgbouncer:
$SSH "PGPASSWORD=polymarket_s46 psql -h localhost -p 6432 -U polymarket -d polymarket -c 'QUERY'"

# .env change
$SSH "sudo bash -c \"echo 'KEY=VALUE' >> /opt/polymarket-ai-v2/.env\""

# Restart service
$SSH "sudo systemctl restart polymarket-ai"
```

### Code Sync (VPS -> Local)
```bash
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21:/opt/polymarket-ai-v2/bots/esports_bot.py bots/esports_bot.py
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21:/opt/polymarket-ai-v2/tests/unit/test_esports_bot.py tests/unit/test_esports_bot.py
```

**NOTE**: As of S134, local git and VPS are in sync (commits `02c7575` + `d88a6e9`). No divergence.

---

## 19. Files Modified in S134

| File | Change | Status |
|------|--------|--------|
| `bots/esports_bot.py` | Fix A+B exit guards (lines 1548-1569), ESPORTS_DISABLED_GAMES mechanism (lines 723-726, 1840-1844) | Committed (`d88a6e9`) + Deployed |
| `config/settings.py` | `ESPORTS_DISABLED_GAMES` setting (line 1179) | Committed (`d88a6e9`) + Deployed |
| `base_engine/execution/order_gateway.py` | CVaR skip (unchanged from S133, synced in `02c7575`) | Committed (`02c7575`) + Deployed |
| `tests/unit/test_esports_bot.py` | Synced from VPS (S131 assertion updates) | Committed (`02c7575`) |
| VPS `.env` | `ESPORTS_DISABLED_GAMES=cod` REMOVED | Active (no games disabled) |

---

## 20. Verification Queries

```bash
SSH="ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o ConnectTimeout=10 ubuntu@34.251.224.21"

# Scan health
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'esportsbot_scan_summary' | tail -3"

# S134 Fix A/B: Check for dead-market or floor skips
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep -E 'esportsbot_exit_skip_dead_market|esportsbot_stop_loss_floor_skip' | tail -10"

# Signal quality values
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'esportsbot_signal_quality' | tail -10"

# Trade attempts
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep 'EsportsBot trade executed' | tail -10"

# Opposing-side blocks (S132 Fix 4)
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep 'esports_opposing_side' | tail -10"

# Errors
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep -i esports | grep -iE 'error|exception|traceback' | head -10"
```

```sql
-- Per-game P&L (all-time)
SELECT COALESCE(event_data->>'game','?') as game, event_type, COUNT(*) as n, ROUND(SUM(realized_pnl)::numeric,2) as pnl
FROM trade_events WHERE bot_name='EsportsBot' AND event_type IN ('EXIT','RESOLUTION')
GROUP BY 1,2 ORDER BY game, event_type;

-- Open positions
SELECT COUNT(*) as open, ROUND(SUM(unrealized_pnl)::numeric,2) as upnl, ROUND(SUM(size*entry_price)::numeric,2) as exposure
FROM positions WHERE bot_id='EsportsBot' AND status='open';

-- Data integrity: no corrupt events
SELECT COUNT(*) FROM trade_events WHERE bot_name='EsportsBot'
AND event_type='RESOLUTION' AND (side='SELL' OR realized_pnl IS NULL);
-- Should be 0

SELECT COUNT(*) FROM trade_events WHERE bot_name='EsportsBot'
AND event_type='EXIT' AND price=0.0;
-- Should be 0

-- S132 Fix 5: confidence in new entries
SELECT event_time, event_data->>'confidence' as conf, event_data->>'signal_quality' as sq, event_data->>'game' as game
FROM trade_events WHERE bot_name='EsportsBot' AND event_type='ENTRY' ORDER BY event_time DESC LIMIT 5;
-- Both non-NULL for recent entries

-- Calibrator progress (are LoL/Valorant close to fitting?)
SELECT game, COUNT(*) total, COUNT(*) FILTER (WHERE actual_outcome IS NOT NULL) resolved
FROM esports_prediction_log GROUP BY game ORDER BY total DESC;

-- Both-sides check (should not grow past 7)
SELECT market_id, COUNT(DISTINCT side) FROM trade_events
WHERE bot_name IN ('EsportsBot','EsportsLiveBot','EsportsSeriesBot') AND event_type='ENTRY'
GROUP BY market_id HAVING COUNT(DISTINCT side) > 1;

-- Prediction log growth (S133 early logging)
SELECT game, COUNT(*) FROM esports_prediction_log
WHERE created_at > NOW() - INTERVAL '1 hour' GROUP BY game;

-- Open positions by status
SELECT status, COUNT(*) FROM paper_trades WHERE bot_name='EsportsBot' GROUP BY status;
```

---

## 21. Rollback

```bash
# Full S134 rollback (reverts exit guards + ESPORTS_DISABLED_GAMES mechanism)
git revert d88a6e9
# Deploy reverted code to VPS

# Surgical: restore S133 exit thresholds only
# In esports_bot.py line 1552: change 0.10 -> 0.03, 0.20 -> 0.05
# Remove lines 1564-1569 (stop-loss floor)

# Full S131-S134 rollback (back to S129)
git revert d88a6e9 02c7575
# WARNING: This reverts ALL S131-S134 fixes including SQ root fix

# .env rollback (if any VPS env changes):
# Currently no VPS-only .env changes in S134 (ESPORTS_DISABLED_GAMES was added then removed)
```

---

## 22. FEEDBACK RULES (CRITICAL)

- **SCOPE LOCK ACTIVE**: Only fix what the handoff or user explicitly requests. NEVER add unsolicited features.
- **NEVER disable games** without explicit user instruction (learned S134 -- user rejected CoD disable).
- **Paper trading IS production** per CLAUDE.md. Every feature matters identically in paper and live modes.
- **P&L Math**: NEVER invert formulas for NO positions. `cost = entry_price * size` for ALL sides. `uPnL = (current - entry) * size` for ALL sides.
- **trade_events is P&L AUTHORITY** -- never read paper_trades for P&L. SELL/EXIT trades only exist in trade_events.
- **Audit Self-Validation**: ALL audit findings must be self-validated (re-read code, trace paths, check tests, rate confidence, remove false positives) before reporting.
- **One fix per commit**: Each commit addresses exactly ONE issue. No "while I'm in here" refactors.

---

## 23. All 8 Games — Pipeline Status

| Game | ML Model | Pre-game | Live | BetaCalibrator | Total P&L | Notes |
|------|----------|----------|------|----------------|-----------|-------|
| CS2 | CS2EconomyModel | YES | YES | FITTED (n=64) | -$2,896 | Brier 0.322, worse than random |
| LoL | LoLWinModel | YES | YES | 8/10 | -$1,858 | 7.7% WR, BUG-24 fix active |
| Dota2 | Dota2Model | YES | No | FITTED (n=20) | -$1,404 | WR good but loss/win ratio bad |
| Valorant | ValorantModel | YES | No | 8/10 | +$4,132 | **ONLY profitable. DO NOT TOUCH.** |
| CoD | None | No | No | 1/10 | -$1,151 | No ML model. NOT disabled per user. |
| R6 | None | No | No | 0 | $0 | No markets |
| SC2 | None | No | No | 0 | +$41 | 1 trade |
| RL | None | No | No | 0 | $0 | No markets |

---

## 24. State Persistence

| State | Mechanism | Restore Method |
|-------|-----------|----------------|
| `_game_exposure` | daily_counters write-through | `_restore_exposure_from_db()` |
| `_daily_pnl` | Query trade_events SUM | `_restore_daily_pnl_from_db()` |
| `_recently_exited` | Redis TTL (300s) | `_restore_exit_cooldowns_from_redis()` in start() |
| `_market_game` | Restored from ENTRY trade_events | `_restore_market_game_from_db()` in scan_and_trade |
| `_entered_market_sides` | Restored from ENTRY trade_events | One-time in scan_and_trade (S132) |
| `_open_positions` | positions table | position_manager |
| Glicko-2 ratings | esports_glicko2_ratings table | `_init_glicko2_trackers()` in start() |
| BetaCalibrator params | Re-fitted from esports_prediction_log every 10 min | `_check_monitoring_thresholds()` |
| `_game_brier_cache` | Seeded on startup (S131) + refreshed by monitoring | Uses `_get_cached_rolling_accuracy()` |
| `_prediction_cache` | In-memory, 1h TTL | Lost on restart (10s re-sync) |

---

## 25. Architecture — File Map

### Core Bot Files
| File | Lines | Role |
|------|-------|------|
| `bots/esports_bot.py` | ~5,900 | Main bot: scan, predict, trade, exit, calibrate, signal quality |
| `bots/esports_live_bot.py` | ~350 | Live in-game trading wrapper (queue maxsize=500) |

### Code Locations in esports_bot.py (approximate line numbers)
| Lines | What |
|-------|------|
| 47-149 | `BetaCalibrator` class |
| 151-192 | `OnlinePlattCalibrator` class |
| 195-277 | `__init__` — all instance vars (incl `_entered_market_sides`, `_prediction_log_cache`) |
| 420-600 | `start()` — client init, Glicko-2, calibrators, Brier cache seed |
| 636-793 | `on_price_update()` — WS reactive trading + S134 disabled games guard |
| 842-1287 | `scan_and_trade()` — main loop, waterfall, `_entered_market_sides` restore |
| 1530-1600 | `_check_and_execute_exits()` — S134 Fix A/B, stop-loss, max hold |
| 1785-1960 | `analyze_opportunity()` — S134 disabled games + full prediction pipeline |
| 2182-2372 | `_enrich_prediction()` — returns TUPLE (prob, _enrich_meta) |
| 2374-2646 | `_get_model_prediction()` — all game paths |
| 3107-3125 | `_classify_market_type()` |
| 3127-3406 | `_execute_esports_trade()` — sizing pipeline + opposing-side guard + SQ sizing |
| ~3473 | `_compute_signal_quality()` — 5-component composite |
| 4032-4080 | `_check_monitoring_thresholds()` — Brier halt, calibrator fitting |
| 5259-5638 | Series analysis + trading |

### ML Models
| File | Role |
|------|------|
| `esports/models/lol_win_model.py` | LoL XGBoost + IsotonicRegression (S128) |
| `esports/models/cs2_economy_model.py` | CS2 round+map+series with `_heterogeneous_series_prob` (S128) |
| `esports/models/dota2_model.py` | Dota2 XGBoost |
| `esports/models/valorant_model.py` | Valorant XGBoost |
| `esports/models/series_model.py` | Generic BO3/BO5 probability |
| `esports/models/glicko2.py` | Glicko-2 rating system |
| `esports/models/esports_trainer.py` | Training orchestrator with graduation gate (accuracy>=0.55, brier<0.30, n>=200) |
| `esports/models/conformal_wrapper.py` | Conformal prediction intervals |
| `esports/models/patch_drift.py` | Patch detection + observation mode |

### Data / API Clients
| File | Role |
|------|------|
| `esports/data/esports_data_collector.py` | PandaScore data collection |
| `esports/data/esports_db.py` | DB helpers (log_prediction, get_rolling_accuracy_batch) |
| `esports/data/pandascore_client.py` | PandaScore API (8 games, 1000 req/hr, 30s cache, circuit breaker) |
| `esports/data/opendota_client.py` | Dota2 (word-boundary match S128) |

### Scripts
| File | Purpose |
|------|---------|
| `scripts/bot_pnl.py` | Canonical P&L: `python scripts/bot_pnl.py EsportsBot 720` |
| `scripts/esports_diag.py` | Diagnostic (positions table has no closed_at/updated_at) |
| `scripts/esports_charts.py` | WR/P&L by game+confidence |
