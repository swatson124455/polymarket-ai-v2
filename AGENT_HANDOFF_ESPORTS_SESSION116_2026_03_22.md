# AGENT HANDOFF — EsportsBot Session 116 (2026-03-22)

## Session Type: EsportsBot-scoped (3 root-cause fixes + cap raise + diagnostics)

## CRITICAL CONTEXT FOR NEXT AGENT

This is a **single-bot session for EsportsBot only**. Do not touch MirrorBot, WeatherBot, or any other bot's code/config unless explicitly requested. Read `CLAUDE.md` in the repo root — it contains non-negotiable rules for this live trading system.

### System Architecture (EsportsBot-specific)
- **15 bots** total in BOT_REGISTRY, but this session is EsportsBot-scoped
- **EsportsBot** = pre-game match winner predictions using Glicko-2 ratings + GBM models
- **EsportsLiveBot** = live in-game trading using WS price feeds (shares `esports_bot.py`)
- **EsportsSeriesBot** = series-level trading via `_series_scan()` (currently silent — no series markets on Polymarket)
- **Paper trading mode**: `SIMULATION_MODE=true`. Paper trading IS production (see CLAUDE.md)
- **VPS**: Ubuntu at 34.251.224.21, SSH key `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **Deploy**: `scp` files to `/tmp/`, `sudo cp` to `/opt/polymarket-ai-v2/`, `sudo systemctl restart polymarket-ai`

---

## What Was Done This Session (S116)

### 1. Zero-Price Position Display Fix — ROOT CAUSE IN POSITION MANAGER
- **Root cause**: `_update_current_prices()` in `position_manager.py` accepted `price=0.000` from `market_prices` table as valid. When `market_prices` had zero (no activity on a token), it stored the token in `latest_prices` dict, BLOCKING the fallback tiers (CLOB API + `markets` table) from providing the real price.
- **Impact**: 2 positions showed `current_price=0.000` with phantom losses of -$157 and -$189. Real prices were 0.33 and 0.50 respectively.
- **Fix** (`position_manager.py` line 364): Changed `if r[1] is not None:` to `if r[1] is not None and float(r[1]) > 0:`. Zero prices are now skipped in tier 1, token falls through to tier 3 (`markets` table) which provides the correct YES/NO price.
- **Why stop-loss didn't fire on these**: `esports_bot.py` line 1460 has `current = float(pos.get("current_price", entry) or entry)` — Python's `0.0 or entry` returns `entry`, so zero-price positions use entry price for stop-loss. Accidental safeguard.
- **Verified**: Prices hold correct values after 2+ minutes. Zero open positions with `current_price=0` across all 459 positions (all bots).
- **Affects all 15 bots** — this is a shared module fix.

### 2. Dead Market Spread Guard — ROOT CAUSE IN ORDER GATEWAY
- **Root cause**: S115's edge-at-VWAP gate (`confidence - VWAP > 0`) only catches when slippage pushes VWAP ABOVE confidence. It completely misses dead markets where VWAP is near zero — any model probability > 0 creates phantom "huge edge" (e.g., `0.65 - 0.002 = 0.648`).
- **Impact**: CoD market `0xed68ee...` had already resolved (NO won). YES token worthless (bid=0.001, ask=0.999, spread=99.8%). Bot entered at 0.002, stopped out at 0.000, lost $242.64. The S115 gate let it through because edge_at_vwap was +0.648.
- **Fix** (`order_gateway.py` lines 746-751): Added spread guard BEFORE edge-at-VWAP check. If `_shadow_spread > 0.80`, force `_edge_at_vwap = -1.0` (guaranteed rejection). Logs as `order_dead_market_spread`.
- **Threshold rationale**: Normal liquid markets have 1-10% spread. Thin esports markets are 30-50%. Only truly dead/resolved markets hit 80%+. Conservative threshold.
- **Two-layer protection now**: (1) Spread > 80% → dead market reject; (2) confidence <= VWAP → edge eroded reject.
- **Affects all 15 bots** — order gateway is shared.

### 3. Exposure Caps Raised — Config Change
- **Root cause of low opportunity flow**: CS2 per-game cap of $3,000 was hit constantly (exposure $3,253), blocking 4-6 markets every scan. Total exposure was only $2,214 out of $15,000 total cap — massive headroom.
- **Changes** (`config/settings.py` defaults):

| Setting | Before | After | % of $20K Capital |
|---------|--------|-------|-------------------|
| `ESPORTS_MAX_GAME_EXPOSURE` | $3,000 | **$5,000** | 25% |
| `ESPORTS_MAX_TOURNAMENT_EXPOSURE` | $5,000 | **$8,000** | 40% |
| `ESPORTS_MAX_TEAM_EXPOSURE` | $1,000 | **$2,000** | 10% |
| `ESPORTS_MAX_TOTAL_EXPOSURE_USD` | $15,000 | $15,000 | 75% (unchanged) |

- **Hierarchy**: team ($2K) < game ($5K) < tournament ($8K) < total ($15K) < capital ($20K)
- **Result**: `exposure_cap` completely gone from waterfall (was 4-6 per scan). `passed: 5` (was 2-3).
- **Note**: VPS `.env` files do NOT set these vars — they use `settings.py` defaults. The `EnvironmentFile=/opt/pa2-shared/.env` only has `ESPORTS_MAX_DAILY_USD=10000`.

### 4. All Outstanding Items Reviewed (P2-P4)

#### P2: High-Edge (>0.40) Outcomes — NO NEGATIVE TREND
- 12 resolved predictions with edge > 0.35: 7/12 correct (58%)
- 5 losses are ALL tail-price LoL markets (price < 0.20 or > 0.93) where Glicko-2 prior blend fights extreme market prices
- 17 high-edge predictions still open (mostly CS2/Dota2 tail-price)
- **Verdict**: No cap needed. Tail-price false edges are structural, not a trend.

#### P2: Exit Cooldown 15 Min — WORKING PERFECTLY
- 50 re-entries analyzed: ALL are FLIP direction (not same-side churn)
- 13 profitable, 37 unprofitable, but net +$3,202 from re-entries
- 15-min same-side cooldown is correctly blocking churn. Flip re-entries are legitimate signal changes.
- **Verdict**: No change needed.

#### P3: LoL Brier Score — DETERIORATING
- Aggregate: 0.3080 (over 0.30 concern threshold)
- Mar 21 spike: 0.4135 on 5 markets
- LoL is the weakest game — 3 LoL resolution losses totaled -$614 overnight
- Halt disabled per user directive. Monitor only.

#### P4: Shadow Fills — LIVE AND WORKING
- 5 EsportsBot records, 7 WeatherBot records, 0 MirrorBot records
- Edge-at-VWAP gate correctly rejected 3/5 EsportsBot signals (negative edge at real price)
- `latency_ms` column is NULL — `scan_start_mono` not reaching the shadow_fills insertion path
- **MirrorBot has zero shadow fills** — investigate in a MirrorBot session

### 5. Cross-Bot Live Readiness Assessment (user-requested scope override)
- **MirrorBot**: +$23,860 P&L, best performer, but zero shadow fill data. Can't assess real execution quality.
- **WeatherBot**: +$2,964, safest profile (67% win rate, zero losing days), 0.72% avg slippage in shadow fills.
- **EsportsBot**: +$4,844, most volatile. 24% avg slippage in shadow fills. Esports books chronically thin.
- **Recommendation**: WeatherBot first to live, MirrorBot needs shadow fill investigation, EsportsBot last (liquidity concerns).

---

## P&L Summary (as of S116)

### All-Time
| Event | Count | Realized |
|-------|-------|----------|
| ENTRY | 246 | $0 |
| EXIT | 143 | **+$3,615.51** |
| RESOLUTION | 162 | **+$1,228.95** |
| **Total** | | **+$4,844.46** |

### Daily Trend
| Day | Entries | Exit P&L | Resolution P&L | Net |
|-----|---------|----------|----------------|-----|
| Mar 19 | 38 | -$592.68 | -$1,115.78 | -$1,708.46 |
| Mar 20 | 34 | +$670.22 | +$687.22 | +$1,357.44 |
| Mar 21 | 46 | +$3,586.65 | +$71.32 | +$3,657.96 |
| Mar 22 (partial) | 2 | $0 | +$1,307.67 | +$1,307.67 |

### Since 6pm Mar 21 (analyzed in detail)
- 24 entries, 11 exits, 9 resolutions across 22 unique markets
- ~1 trade per match (anti-churn working)
- 40% win rate, 4.5:1 reward-to-risk ratio
- Bug-adjusted net: **+$3,571** (removing $244 dead market loss)
- P&L concentrated in rare outsized wins — 2 Valorant exits (+$3,647) carried the session
- Without those 2 wins, remaining 18 trades netted -$76

### Trade Profile Characteristics
- Bot loses more often than wins (40% WR) but wins are 4-5x bigger
- ~1 entry per match (low re-entry rate post anti-churn fixes)
- ~4 entries per hour during active esports hours
- LoL is the weakest game (Brier 0.308, -$614 in overnight resolutions)
- CS2 resolutions are strong (+$645 overnight)

---

## Current VPS Config (LIVE as of S116 deploy)

```env
# Bankroll
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_MAX_BET_USD=300
ESPORTS_MAX_DAILY_USD=20000

# Exposure caps (S116: raised from 3K/5K/1K)
# Hierarchy: team ($2K) < game ($5K) < tournament ($8K) < total ($15K) < capital ($20K)
ESPORTS_MAX_TOTAL_EXPOSURE_USD=15000
ESPORTS_MAX_GAME_EXPOSURE=5000          # S116: was 3000
ESPORTS_MAX_TOURNAMENT_EXPOSURE=8000    # S116: was 5000
ESPORTS_MAX_TEAM_EXPOSURE=2000          # S116: was 1000

# Trading thresholds
ESPORTS_MIN_CONFIDENCE=0.48
ESPORTS_MIN_EDGE=0.05
ESPORTS_CONFLUENCE_MIN=0.60
# ESPORTS_MAX_EDGE — REMOVED in S112. No upper edge cap. High edges logged.
ESPORTS_STOP_LOSS_PCT=0.15

# Anti-churn (S109 + S111)
ESPORTS_EXIT_COOLDOWN_SECONDS=900       # 15 min
ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=3
ESPORTS_ENTRY_WINDOW_HOURS=12.0

# Scan
SCAN_INTERVAL_ESPORTS_LIVE=2

# Halt
ESPORTS_BRIER_HALT_THRESHOLD=999.0      # effectively disabled — ALL games trade

# Other
ESPORTS_DAILY_LOSS_LIMIT=10000
ESPORTS_MAX_HOLD_HOURS=96
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 20000}}
SIMULATION_MODE=true
```

**VPS .env notes:**
- `EnvironmentFile=/opt/pa2-shared/.env` — only has `ESPORTS_MAX_DAILY_USD=10000` (overrides settings.py default of 20000)
- `/opt/polymarket-ai-v2/.env` — has `ESPORTS_MAX_BET_USD=300`, `ESPORTS_MAX_DAILY_USD=10000`, `ESPORTS_MAX_EDGE=0.35` (stale, edge cap removed in S112 code)
- Game/tournament/team caps are NOT in any .env — they use `settings.py` defaults

---

## Open Positions: 18

### Brier Scores by Game (since 2026-03-16)
| Game | N | Brier | Status |
|------|---|-------|--------|
| SC2 | 1 | 0.0198 | Excellent (tiny N) |
| Valorant | 18 | 0.1528 | Good |
| Dota2 | 13 | 0.2305 | OK |
| CS2 | 19 | 0.2727 | Improving (was 0.334) |
| CoD | 4 | 0.2895 | OK (small N) |
| **LoL** | **16** | **0.3080** | **Concern — over 0.30** |

---

## Outstanding Items (EsportsBot-scoped)

| Priority | Item | Status | Action |
|----------|------|--------|--------|
| **P2** | High-edge (>0.40) outcomes | **REVIEWED S116** — no negative trend. Losses are tail-price LoL. | Continue monitoring |
| **P2** | Exit cooldown 15 min | **REVIEWED S116** — working perfectly. All re-entries FLIP. +$3,202 net. | No change needed |
| **P2** | RC4: Entry price inflation — positions stores requested price not fill price | Deferred | Separate session (shared module) |
| **P2** | Kelly degradation suspended (needs ALL 8 games fitted) | Blocked on CoD/R6/RL data | Wait |
| **P3** | **LoL Brier=0.308, Mar 21 spike to 0.4135** | **DETERIORATING** | Weakest game. -$614 in overnight resolutions. Halt disabled per directive. Monitor. |
| **P3** | CS2 Brier=0.273 | **IMPROVING** (was 0.334) | Positive trend |
| **P3** | `no_prediction: 2-5` per scan — mostly tournament_winner skips | Healthy | Only actionable if count grows |
| **P3** | WS reconnect drops every ~40s-5min | Auto-reconnects working | Monitor |
| **P3** | EsportsSeriesBot silent — no series markets | Expected | No fix |
| **P4** | Shadow fills `latency_ms` NULL | **NEW** | `scan_start_mono` not reaching shadow_fills insertion |
| **P4** | MirrorBot shadow fills = 0 | **NEW** | Cross-bot: investigate in MirrorBot session |
| **P5** | VPS .env has stale `ESPORTS_MAX_EDGE=0.35` | Cosmetic | Edge cap removed in code (S112). Env var is ignored but should be cleaned up |
| **P5** | VPS .env has `ESPORTS_MAX_DAILY_USD=10000` overriding default 20000 | Working | May want to align to 20000 |
| **P5** | P&L profile fragile — dependent on rare outsized wins | Structural | Monitor. 40% WR + 4.5:1 ratio is profitable but volatile. |

### Items RESOLVED This Session

| Item | Resolution |
|------|-----------|
| Zero-price positions (current_price=0.000) | Root cause: `_update_current_prices` accepted price=0. Fix: skip `price <= 0` in tier 1, falls through to `markets` table. |
| Dead market trades (CoD $242 loss) | Root cause: edge-at-VWAP gate blind to dead markets. Fix: spread > 80% guard. |
| CS2 exposure cap blocking 4-6 markets/scan | Raised game $3K→$5K, tournament $5K→$8K, team $1K→$2K. Zero cap blocks now. |
| S112 outstanding items reviewed (high-edge, cooldown, LoL Brier, shadow fills) | All reviewed. No code changes needed. LoL Brier flagged as concern. |

---

## PREDICTION PIPELINE (in order)

1. **Glicko-2 rating lookup** → raw `model_prob` (team A win probability)
2. **BetaCalibrator** (if fitted for game) → calibrated probability
3. **Online Platt scaling** (if available) → override calibration
4. **RFLB correction** → favorites-longshot bias adjustment
5. **BO adjustment** → best-of-1 dampening
6. **Cross-game XGBoost blend** → extremized geometric mean with Glicko-2 (0.6/0.4)
7. **Conformal prediction** → uncertainty intervals
8. **Edge calculation** → `model_prob - market_price` (YES) or `(1-model_prob) - (1-price)` (NO)
9. **Confluence scoring** → 0.65*edge + 0.35*freshness, gate at 0.55
10. **BotBankrollManager sizing** → Kelly fraction with conformal bounds

### Full Prediction Path (in `_get_model_prediction()`)
```
Market -> detect_game() -> _get_model_prediction() -> one of:

|- LoL LIVE (live_data exists + _lol_model.is_trained):
|   -> _inject_glicko2_metadata(game_state, game, live_data)
|   -> glicko2_est = 0.5 + team_strength_diff
|   -> _lol_model.predict_with_glicko2(game_state, glicko2_est)
|
|- CS2 LIVE (live_data exists + _cs2_model.is_trained):
|   -> _inject_glicko2_metadata(game_state, game, live_data)
|   -> cs2_model.predict_match(maps_won_a, maps_won_b, best_of, map_probs)
|
|- Dota2 / Valorant (ML model + Glicko2 features):
|   -> _get_glicko2_prediction(market_data, game, price) for expected_score
|   -> _build_glicko2_game_state() for 6 ML features
|   -> model.predict_with_features(game_state)
|
'- ALL GAMES fallback (pre-match, no ML model):
    -> _get_glicko2_prediction(market_data, game, price)
    -> Bayesian-blended Glicko-2 expected score
    -> Prior blend based on max(phi): >=350 -> 80% market + 20% Glicko-2
                                       >=200 -> 50/50
                                       >=100 -> 20% market / 80% Glicko-2
                                       <100  -> 100% Glicko-2
```

---

## SIZING PIPELINE (in order)

```
1. BotBankrollManager.calculate_bot_position_size() — Kelly with kelly_fraction=0.25
2. Near-expiry confidence boost (A5)
3. Conformal conservative bounds (A6/S100b) — shrinks size by prediction interval width
4. Drawdown Kelly reduction (A8) — reduces Kelly when daily P&L is negative
5. CLV-gated scaling — tier-based size multiplier
6. Apply ALL multipliers: size * phi_factor * dd_factor * game_kelly_mult * edge_decay_mult
7. P6 max bet cap: if (price * size) > ESPORTS_MAX_BET_USD ($300), clamp
8. Game exposure cap — $5K per game (S116)
9. Daily cap — max_daily_usd=$20K
```

---

## SCAN WATERFALL (what blocks trades, in order)

1. `no_game` — can't detect game from market question
2. `halted` — Brier halt (**DISABLED** via threshold=999.0)
3. `exposure_cap` — per-game ($5K) / tournament ($8K) / team ($2K) / total ($15K) exceeded
4. `observation` — PatchDriftDetector (48h after game patch)
5. `no_prediction` — team name extraction/matching failed OR tournament_winner type
6. `exit_cooldown` — recently exited this market (15 min Redis-persisted)
7. `max_entries` — 3 entries per market per 12h window
8. `low_confidence` — below 0.48
9. `low_edge` — below 0.05
10. `reentry_rejected` — has position, wrong direction or insufficient edge
11. `passed` → goes to `_execute_esports_trade()`

*Note: `edge_cap` REMOVED in S112. Dead market spread guard (>80%) in order_gateway since S116.*

---

## ORDER EXECUTION — TWO-LAYER PROTECTION (S115 + S116)

```
Bot calls place_order(side, price, size, confidence, event_data)
  -> order_gateway.py
     1. Book walk: snapshot L2 orderbook, compute VWAP via _vwap_from_book()
     2. S116 SPREAD GUARD: if spread > 80% → force reject (dead market)
     3. S115 EDGE-AT-VWAP GATE: if confidence <= VWAP → reject (edge eroded)
     4. If both pass → paper_trading.py executes at VWAP price
     5. Shadow fill recorded in shadow_fills table

Paper engine fill:
  - Reads _shadow_vwap from event_data (set by order_gateway)
  - Sets fill price = max(0.001, min(0.999, _shadow_vwap))
  - Position created at VWAP price, not signal price
```

---

## POSITION PRICE UPDATE — THREE-TIER FALLBACK (S116 fixed)

```
position_manager._update_current_prices():
  Tier 1: market_prices table (most recent price per token_id)
          S116 FIX: skip price <= 0 (was accepting 0.000 as valid)
  Tier 2: CLOB API orderbook (mid-price if spread < 50%)
          Skips esports tokens (spread too wide)
  Tier 3: markets table (yes_price / no_price from CLOB API refresh)
          Guard: skip price <= 0 or >= 1
          This is where esports positions get their prices
```

---

## ANTI-CHURN SYSTEM (S109 + S111)

```
Stop-loss fires (15% drawdown):
  -> SELL order executed
  -> _recently_exited[market_id] = monotonic_time  (900s cooldown)
  -> _save_exit_cooldown_to_redis()  (survives restart)
  -> _prediction_cache[market_id] cleared  (forces fresh Glicko-2)

Re-entry attempt:
  -> Check _recently_exited: if < 900s ago, reject ("exit_cooldown")
  -> Check _market_entry_times: if >= 3 in last 12h, reject ("max_entries")
  -> Both checks in _churn_blocked() — gates scan, WS reactive, AND series paths

Current behavior: ~1 entry per match. Re-entries are almost all FLIP (direction change).
```

---

## CALIBRATION ARCHITECTURE (4 Phases)

### Phase 1: BetaCalibrator (batch, per-game)
- `sigmoid(a*ln(p) - b*ln(1-p) + c)`, L-BFGS-B fitting, 30+ samples needed
- Training window starts `_GLICKO2_FIX_DATE = 2026-03-16`
- All fitted games show a~1, b~1, c~0 (raw Glicko-2 already well-calibrated)

### Phase 2: OnlinePlattCalibrator (streaming, per-game)
- River `LogisticRegression` with SGD, applied AFTER BetaCalibrator

### Phase 3: ConformalPredictor (batch, per-game)
- Applied in `_execute_esports_trade()` for phi_factor sizing

### Phase 4: ADWIN Drift Detection (streaming, per-game)
- Advisory only, does NOT halt trading

---

## KEY CODE LOCATIONS

### bots/esports_bot.py (~5,735 lines)
| Lines (approx) | What |
|----------------|------|
| 30-148 | `BetaCalibrator` class |
| 151-193 | `OnlinePlattCalibrator` class |
| 270-360 | `__init__` — all instance vars |
| 580-608 | `start()` — market service init, Redis cooldown restore |
| 624-670 | `on_price_update()` — WS event handler |
| 740-815 | WS reactive trade path |
| 857-920 | `scan_and_trade()` top — exposure restore, daily P&L, stop-loss |
| 1080-1260 | `_analyze_one()` + scan results — waterfall filters |
| 1290-1553 | `_check_and_execute_exits()` — stop-loss 15%, max hold |
| 1460 | Stop-loss zero-price safeguard: `current = float(...) or entry` |
| 1595-1624 | Calibration application + RFLB in `analyze_opportunity()` |
| 1810-1940 | `_get_model_prediction()` all paths |
| 2175-2219 | `_inject_glicko2_metadata()` |
| 2290-2320 | XGB + Glicko-2 blend |
| 2757-2941 | `_execute_esports_trade()` — sizing, exposure tracking |
| 2955-3015 | `_compute_confluence_score()` |
| 3350-3387 | Redis cooldown save/restore |
| 3564-3613 | BetaCalibrator + ConformalPredictor batch fitting |
| 4040-4142 | `_get_glicko2_prediction()` — Bayesian-blended |
| 4387-4504 | `_extract_team_ids_from_question()` — 6 regex patterns |
| 4665-4729 | `_match_team_name()` — 6-tier fuzzy matching |
| ~5161-5174 | Series scan path with `_churn_blocked()` gate |

### Other Key Files
| File | Lines | Purpose |
|------|-------|---------|
| `config/settings.py` | 1269 | All ESPORTS_* config (~lines 1008-1059) |
| `base_engine/execution/order_gateway.py` | 1199 | Pre-trade book walk, spread guard (S116), edge-at-VWAP gate (S115) |
| `base_engine/execution/position_manager.py` | 847 | Position price updates, 3-tier fallback (S116 fix at line 364) |
| `base_engine/execution/paper_trading.py` | 898 | Paper engine, `_vwap_from_book()`, fill at VWAP |
| `esports/models/glicko2.py` | 279 | `Glicko2Rating`, `expected_score()`, `Glicko2Tracker` |
| `esports/models/conformal_wrapper.py` | — | `ConformalPredictor` — logit-space residuals |
| `esports/data/esports_db.py` | 943 | Prediction logging (ON CONFLICT upsert since S112) |
| `esports/data/pandascore_client.py` | 678 | PandaScore API wrapper |
| `base_engine/data/daily_counter.py` | — | Write-through daily counters |
| `base_engine/data/database.py` | — | DB layer, `insert_trade_event()` with idempotency |
| `base_engine/base_engine.py` | — | `BaseBot` parent — `place_order()`, WS registration |
| `main.py` | — | Bot registry, watchdog |
| `scripts/bot_pnl.py` | — | Canonical P&L script: `python scripts/bot_pnl.py EsportsBot 24` |

---

## GLICKO-2 RATING SYSTEM

### Bayesian Prior Blending (in `_get_glicko2_prediction()`)
```
max_phi = max(rating_a.phi, rating_b.phi)
phi >= 350 (unrated):    80% market_price + 20% Glicko-2
phi 200-350 (sparse):    50% market_price + 50% Glicko-2
phi 100-200 (developing): 20% market_price + 80% Glicko-2
phi < 100 (mature):       100% Glicko-2
```

### Team Name Matching — 6-Tier System (lines ~4665-4729)
1. Exact → 2. Alias (`_TEAM_ALIASES`) → 3. Substring →
4. Reverse substring → 5. Word-boundary (short names) → 6. Difflib fuzzy (0.78)

---

## DATA FLOW & RESOLUTION

```
1. Scan loop (every 2s):
   -> analyze_opportunity() -> predictions logged to esports_prediction_log (ON CONFLICT upsert)

2. Trade execution:
   -> order_gateway (spread guard + edge gate) -> paper_trading (VWAP fill) -> trade_events ENTRY

3. Resolution (every 10 scans ~20s):
   -> _backfill_esports_outcomes() + _resolve_esports_from_clob()
   -> Updates esports_prediction_log.actual_outcome
   -> Feeds streaming calibrators (ADWIN + OnlinePlatt)

4. Monitoring (every 20 scans ~40s):
   -> BetaCalibrator.fit_from_db() + ConformalPredictor.fit_from_predictions()
```

### Key Tables
| Table | Purpose |
|-------|---------|
| `trade_events` | **P&L AUTHORITY**. Partitioned by month. Immutable trigger. |
| `paper_trades` | Legacy. No `metadata` column. No `resolved_pnl` column. |
| `positions` | Open positions. No `closed_at`/`updated_at`. Use `source_bot` not `bot_name`. |
| `esports_prediction_log` | Prediction history. Unique on `(market_id, bot_name)` since S112. |
| `glicko2_ratings` | Persisted Glicko-2 ratings per team per game. |
| `traded_markets` | Market registry. `bot_names` is TEXT (use LIKE, not = ANY()). |
| `daily_counters` | Per-game exposure persistence. Auto-resets UTC midnight. |
| `shadow_fills` | Book walk snapshots + VWAP + edge (S115). |
| `market_prices` | Token price history. May contain price=0.000 (S116: position_manager skips these). |
| `markets` | Market metadata. `yes_price`/`no_price` used as fallback for position pricing. |

---

## GAME EXPOSURE & DAILY_COUNTERS PERSISTENCE

```python
self._game_exposure: Dict[str, float]  # {game: USD_amount}

# On trade entry:
_entry_cost = price * size  # USD
self._game_exposure[game] += _entry_cost
await _inc_daily(db, "EsportsBot", f"game_{game}", _entry_cost)

# On trade exit:
_exit_cost = entry_price * size  # USD
self._game_exposure[game] -= _exit_cost
await _inc_daily(db, "EsportsBot", f"game_{game}", -_exit_cost)

# Cap check (S116 caps):
if self._game_exposure.get(game, 0) + _entry_cost > ESPORTS_MAX_GAME_EXPOSURE ($5000):
    # reject — "exposure_cap" waterfall

# Startup restore from daily_counters table
```

---

## SHADOW FILLS (S115 + S116)

- Paper engine fills BUY orders at real VWAP from L2 orderbook walk
- **S116**: Spread > 80% → dead market rejection BEFORE edge check
- **S115**: If `confidence <= VWAP` → edge eroded rejection
- `shadow_fills` table records every BUY signal with full book snapshot
- `latency_ms` currently NULL for EsportsBot — needs investigation

---

## CRITICAL TRAPS (DO NOT BREAK)

### EsportsBot-Specific
1. **`_game_exposure` is tracked in USD** (`price * size`), not shares.
2. **`_churn_blocked()` must gate ALL paths** — scan, WS reactive, AND series.
3. **`_recently_exited` persists to Redis** — survives restarts.
4. **`_market_entry_times` does NOT persist** — resets on restart.
5. **BetaCalibrator training window starts 2026-03-16** — `_GLICKO2_FIX_DATE`.
6. **PandaScore rate limit**: 1000/hr budget, ~400/hr used.
7. **Edge cap is REMOVED** — no `_max_edge`, no `edge_cap` waterfall counter.
8. **Brier halt is DISABLED** — `ESPORTS_BRIER_HALT_THRESHOLD=999.0`.
9. **`esports_prediction_log` unique on (market_id, bot_name)** — must use ON CONFLICT.
10. **Exposure cap hierarchy**: team ($2K) < game ($5K) < tournament ($8K) < total ($15K) < capital ($20K).
11. **BetaCalibrator parameters near identity** — raw Glicko-2 already well-calibrated.
12. **All learning suspensions check `_beta_calibrators.get(game)._fitted`** — auto-deactivate.
13. **`ESPORTS_MAX_BET_USD` enforced by P6 cap in `_execute_esports_trade()`** — separate from BotBankrollManager.
14. **`_tournament_phase` must be defined BEFORE if/else** — Python 3.13 scoping.
15. **`_inject_glicko2_metadata()` uses `.get("name", "").lower()`** — S97 fix.
16. **PatchDriftDetector**: `_patch_timestamps` only set when `old is not None` (S88 fix).
17. **`daily_counter.py` now commits** — S103 fix.
18. **`_resolve_esports_from_clob()` processes ALL unresolved** — no LIMIT.
19. **Stop-loss zero-price safeguard**: line 1460 `current = float(...) or entry` — 0.0 is falsy, falls back to entry.
20. **Dead market spread guard**: order_gateway.py — spread > 80% → force reject. Do NOT lower threshold.

### System-Wide (from CLAUDE.md)
21. **trade_events is P&L authority** — never read paper_trades for P&L.
22. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
23. **BotBankrollManager handles SIZING; risk_manager handles LIMITS** — both must pass.
24. **`risk_manager.calculate_position_size()` is DEPRECATED**.
25. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
26. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
27. **asyncpg DATE**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime.
28. **Python 3.13 scoping**: local imports shadow module-level for ENTIRE function.
29. **trade_events immutability trigger**: must DISABLE/ENABLE for corrections.
30. **RESOLUTION event idempotency**: ON CONFLICT broken on partitioned tables. Uses WHERE NOT EXISTS.
31. **Paper engine position key**: `(bot_name, market_id)` — NEVER `market_id` alone.
32. **`realized_pnl_today`**: `Dict[str, float]`, access via `.get(bot_name, 0.0)`.
33. **Partial exit fee proration**: `prorated_entry_fee = entry_fee * (exit_size / pos_size)`.
34. **positions table**: NO `closed_at`/`updated_at`. Use `source_bot` not `bot_name`.
35. **`prediction_log`**: NO `rejection_reason`. Use `trade_executed` (bool).
36. **`traded_markets.bot_names`**: TEXT (use LIKE, not = ANY()).
37. **BOT_REGISTRY=15 bots** — shared module change requires all verified.
38. **`paper_trades` has NO `metadata` JSONB column**.
39. **Resolution backfill excludes SELL trades**.
40. **Alpha decay requires `scan_start_mono` in event_data** — EsportsBot passes it.
41. **`market_prices` may contain price=0.000** — position_manager now skips these (S116).
42. **Order gateway spread guard at 80%** — catches dead markets that edge-at-VWAP misses (S116).

### P&L Calculation Rules (MANDATORY)
- **NEVER invert formulas for NO positions** — prices are token-specific
- `cost = entry_price * size` (ALL sides)
- `unrealized_pnl = (current_price - entry_price) * size` (ALL sides)
- Canonical script: `python scripts/bot_pnl.py EsportsBot 720`

---

## COMMITS THIS SESSION

| SHA | Description |
|-----|-------------|
| (not committed yet) | S116: position_manager skip price<=0, order_gateway spread>80% guard, caps raised |

**Files modified:**
- `base_engine/execution/position_manager.py` — line 364: skip price <= 0 in tier 1
- `base_engine/execution/order_gateway.py` — lines 746-751: dead market spread guard
- `config/settings.py` — lines 1051-1053: caps raised (game $5K, tournament $8K, team $2K)

**Tests**: 1668 passed, 0 failed (run twice — once after position_manager + caps, once after order_gateway)

---

## VERIFICATION COMMANDS

```bash
SSH_KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem
VPS=ubuntu@34.251.224.21

# Scan health (exposure_cap should be 0 or very low)
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '2 min ago' --no-pager | grep esportsbot_scan_summary | tail -3"

# Dead market spread guard (should see order_dead_market_spread when dead markets encountered)
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '30 min ago' --no-pager | grep order_dead_market_spread | tail -10"

# Zero-price check (should be 0 zero-price positions)
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT COUNT(*) FILTER (WHERE current_price = 0) as zero_price FROM positions WHERE status='open';\""

# P&L
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT event_type, COUNT(*), ROUND(COALESCE(SUM(realized_pnl),0)::numeric,2) FROM trade_events WHERE bot_name='EsportsBot' GROUP BY event_type;\""

# P&L by day
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT event_time::date as day, event_type, COUNT(*), ROUND(COALESCE(SUM(realized_pnl),0)::numeric,2) FROM trade_events WHERE bot_name='EsportsBot' AND event_time > '2026-03-19' GROUP BY 1,2 ORDER BY 1,2;\""

# Open positions
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT COUNT(*) FROM positions WHERE source_bot='EsportsBot' AND status='open';\""

# Brier scores
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT game, COUNT(*), ROUND(AVG((predicted_prob-COALESCE(actual_outcome,0))^2)::numeric,4) as brier FROM esports_prediction_log WHERE created_at>'2026-03-16' AND actual_outcome IS NOT NULL GROUP BY game ORDER BY brier;\""

# High-edge monitoring
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '30 min ago' --no-pager | grep esportsbot_high_edge | tail -10"

# Shadow fills
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT bot_name, COUNT(*), ROUND(AVG(fill_fraction)::numeric,3), COUNT(*) FILTER (WHERE edge_at_vwap <= 0) as neg_edge FROM shadow_fills GROUP BY bot_name;\""

# Errors
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager | grep -i 'EsportsBot.*error\|EsportsBot.*exception' | tail -10"

# Prediction log integrity
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT COUNT(*) as total_rows, COUNT(DISTINCT market_id) as unique_markets FROM esports_prediction_log;\""
```

---

## SESSION CHECKLIST (do this before any code change)

1. Read `CLAUDE.md` (repo root)
2. Read this prompt fully
3. State what you will work on
4. List files you will touch (max 3 unless justified)
5. Grep for dependents before editing
6. Git snapshot before any edit
7. Read the ENTIRE file you're modifying
8. One fix per commit
9. Write change log per CLAUDE.md format
10. Verify on VPS after deploy

---

## USER DIRECTIVES (carry forward — standing orders)

1. **"All games trade, even if they are shit. We need to learn."** — Do not re-enable Brier halting without explicit user request.
2. **"Remove edge cap, but report anything over .40 in handoff if we have a negative trend."** — Edge cap removed. Monitor `esportsbot_high_edge` logs.
3. **"Paper trading is production."** — Never cut corners because SIMULATION_MODE=true.
4. **"Monitor exit cooldown and review on handoffs."** — 15-min cooldown working. All re-entries are FLIP direction. No change needed.
5. **Scope lock** — Fix only what is requested. No unsolicited features, refactors, or "while I'm in here" changes.

---

## FEEDBACK RULES (MANDATORY)

1. **Scope Lock** (`memory/feedback_scope_lock.md`): NEVER add unsolicited features.
2. **Bot Sessions** (`memory/feedback_bot_sessions.md`): Esports sessions are esports-only.
3. **P&L Math** (`memory/feedback_pnl_math.md`): NEVER invert formulas for NO positions.
