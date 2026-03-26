# CONTINUATION PROMPT — EsportsBot Session 116
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
- **BetaCalibrator** (Kull et al. 2017): `sigmoid(a*ln(p) - b*ln(1-p) + c)` — fits per-game, needs 30+ resolved samples
- **Conformal prediction**: prediction intervals for uncertainty-aware sizing
- **Cross-game XGBoost**: blended with Glicko-2 via extremized geometric mean (0.6/0.4 weights)
- **Confluence scoring**: 65% edge weight + 35% freshness weight, gate at 0.55
- **Paper trading engine**: shared across 15 bots, fill probability, VWAP book walk, alpha decay

**EsportsLiveBot**: Live in-game trading using WS price feeds (shares `esports_bot.py`)
**EsportsSeriesBot**: Series-level trading via `_series_scan()` (currently silent — no series markets on Polymarket)

---

## LIVE STATE SNAPSHOT (2026-03-21 ~19:00 UTC)

### P&L (ALL-TIME)
| Event | Count | Realized |
|-------|-------|----------|
| ENTRY | 235 | $0 |
| EXIT | 142 | **+$3,858.15** |
| RESOLUTION | 144 | **-$443.82** |
| **Total** | | **+$3,414.33** |

**Trend**: Massively positive recovery arc. Was -$1,535 on Mar 19 (pre-S109 churn), flipped to -$643 by S111, then +$535 by S112, now **+$3,414** thanks to S111 exposure cap changes unlocking trade volume.

### Open Positions: 17

### Brier Scores by Game (post-Glicko2 fix, since 2026-03-16)
| Game | N | Brier | Notes |
|------|---|-------|-------|
| SC2 | 1 | 0.0198 | Excellent (tiny N) |
| CoD | 2 | 0.1380 | Good (tiny N) |
| Valorant | 17 | 0.1471 | Good |
| Dota2 | 11 | 0.2588 | OK |
| CS2 | 9 | 0.2821 | Improving (was 0.334) |
| LoL | 16 | 0.3080 | **OVER 0.30 threshold** (halt disabled) |

### Scan Waterfall (live, 2-second interval)
```
markets=22, markets_by_game={'lol': 7, 'cs2': 13, 'valorant': 1, 'cod': 1}
live_matches=17, ws_trading=True
waterfall={'exposure_cap': 13, 'no_prediction': 2, 'low_edge': 1, 'low_confidence': 3, 'passed': 3, 'reentry_rejected': 1}
timing_ms={'phase_a': ~10, 'phase_b': ~130, 'phase_c': ~15, 'total': ~150}
```

### Active Errors
- Fill cooldown warnings on 3 markets — paper engine correctly rejecting illiquid tail-price markets (fill probability 14-15%). **Not a bug.**

---

## SESSION HISTORY (EsportsBot only, most recent first)

### S112 (2026-03-21) — Prediction Log Dedup + Edge Cap Removed
**Commit**: `68ba29b`
- **Prediction log dedup**: `esports_prediction_log` had no unique constraint → 97x duplicate rows per market. Added migration 057 (unique index on `(market_id, bot_name)`, ON CONFLICT upsert). Deleted 19,660 dupes.
- **Edge cap removed**: `ESPORTS_MAX_EDGE=0.35` was blocking 6-7 markets/scan. Removed entirely. High edges (>0.40) logged as `esportsbot_high_edge` for monitoring.
- **Finding**: "100% accuracy on 70 samples at edge>0.30" was actually 2 unique SC2/CoD matches logged 35x each.
- **User directive**: "Remove edge cap, but report anything over .40 in handoff if we have a negative trend."

### S111 (2026-03-21) — Exposure Caps Raised + Brier Halt Disabled
**Commits**: `85e3ba1` (S110 deploy), `a916348` (S111 caps)
- **ROOT CAUSE of low trade volume**: `exposure_cap` blocked 27/29 markets. Per-game $600 exhausted after ~2 bets.
- **Caps raised**: game $600→$3K, tournament $400→$5K, team $300→$1K, daily $10K→$20K, entries/12h 2→3
- **Brier halt disabled**: `ESPORTS_BRIER_HALT_THRESHOLD=999.0`. CS2 was halted (Brier 0.334). User directive: "All games trade, even if they are shit. We need to learn."
- **S110 included**: retrain parallelization, scan interval 10s→2s, `_churn_blocked()` for series path

### S109 (2026-03-19) — Anti-Churn + WS Reactive Activation
**Commits**: `f4cf596`, `9f9ac4c`
- **Anti-churn**: -$925 in 28 minutes from 13+5 stop-loss/re-entry cycles. Fixed: 900s exit cooldown (Redis-persisted), cache clear on stop-loss, rolling 12h entry cap (max 2→now 3/market)
- **WS reactive**: Never worked for esports (16+ days). Subscribed general tokens, zero overlap with esports. Fixed: subscribe esports tokens after scan populates `_market_token_map`. Now reacts in ~1-2s vs ~10s scan cadence.

### S107 (2026-03-19) — Config Alignment + BetaCalibrator Fitted
- Config aligned to $20K/$300/$10K across BotBankrollManager + .env
- BetaCalibrator fitted for 4 games (LoL, Valorant, SC2, Dota2). All params near identity (a~1, b~1, c~0) = raw Glicko-2 already well-calibrated.
- 3 EXIT P&L corrections (+$12)

### S106 (2026-03-18) — NameError Fix
- ALL trades broken since S105 due to NameError. EsportsSeriesBot watchdog fix.

### S105 (2026-03-18) — Cross-Bot Position Isolation
- Paper engine key: `(bot_name, market_id)` (was `market_id` alone). Per-bot `realized_pnl_today`. Partial exit fee proration.

### S103 (2026-03-18) — P4-P7 Fixes
- event_data populated (P4), max_bet cap (P6), exposure units shares→USD (P7), daily_counter commit

### S100-S100c (2026-03-17) — BetaCalibrator Greenfield
- 4 calibration phases: BetaCalibrator → OnlinePlatt → Conformal → ADWIN
- 6 learning-phase suspensions (auto-deactivate per game when fitted)

### S99 (2026-03-16), S97 (2026-03-16), S94 (2026-03-15), S89 (2026-03-14), S88 (2026-03-14), S87 (2026-03-14)
- max_edge, capital 2x, glicko2 metadata name fix, latency 2967→11.9ms, Bayesian prior blend, E2-E5 features, observation mode fix, resolution dedup

---

## CURRENT VPS CONFIG (LIVE as of S112 deploy)

```env
# Bankroll
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_MAX_BET_USD=300
ESPORTS_MAX_DAILY_USD=20000

# Exposure caps (hierarchy: team < game < tournament < total < capital)
ESPORTS_MAX_TOTAL_EXPOSURE_USD=15000    # 75% of capital — DO NOT exceed capital
ESPORTS_MAX_GAME_EXPOSURE=3000          # S111: was 600
ESPORTS_MAX_TOURNAMENT_EXPOSURE=5000    # S111: was 400
ESPORTS_MAX_TEAM_EXPOSURE=1000          # S111: was 300

# Trading thresholds
ESPORTS_MIN_CONFIDENCE=0.48
ESPORTS_MIN_EDGE=0.05
ESPORTS_CONFLUENCE_MIN=0.60
# ESPORTS_MAX_EDGE — REMOVED in S112. No upper edge cap. High edges logged for monitoring.
ESPORTS_STOP_LOSS_PCT=0.15

# Anti-churn (S109 + S111)
ESPORTS_EXIT_COOLDOWN_SECONDS=900       # 15 min. Monitor for adequacy.
ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=3 # S111: was 2
ESPORTS_ENTRY_WINDOW_HOURS=12.0

# Scan
SCAN_INTERVAL_ESPORTS_LIVE=2            # S110: was 10

# Halt
ESPORTS_BRIER_HALT_THRESHOLD=999.0      # S111: effectively disabled. ALL games trade.

# Other
ESPORTS_DAILY_LOSS_LIMIT=10000
ESPORTS_MAX_HOLD_HOURS=96
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 20000}}
SIMULATION_MODE=true
```

---

## PREDICTION PIPELINE (in order)

1. **Glicko-2 rating lookup** -> raw `model_prob` (team A win probability)
2. **BetaCalibrator** (if fitted for game) -> calibrated probability
3. **Online Platt scaling** (if available) -> override calibration
4. **RFLB correction** -> favorites-longshot bias adjustment
5. **BO adjustment** -> best-of-1 dampening
6. **Cross-game XGBoost blend** -> extremized geometric mean with Glicko-2 (0.6/0.4)
7. **Conformal prediction** -> uncertainty intervals
8. **Edge calculation** -> `model_prob - market_price` (YES) or `(1-model_prob) - (1-price)` (NO)
9. **Confluence scoring** -> 0.65*edge + 0.35*freshness, gate at 0.55
10. **BotBankrollManager sizing** -> Kelly fraction with conformal bounds

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
    -> Prior blend based on max(phi): >=350 -> 80% market_price + 20% Glicko-2
                                       >=200 -> 50/50
                                       >=100 -> 20% market / 80% Glicko-2
                                       <100  -> 100% Glicko-2
```

---

## SIZING PIPELINE (in order)

```
1. BotBankrollManager.calculate_bot_position_size() — Kelly with kelly_fraction=0.25
2. Near-expiry confidence boost (A5) — increases confidence for markets near resolution
3. Conformal conservative bounds (A6/S100b) — shrinks size by prediction interval width
4. Drawdown Kelly reduction (A8) — reduces Kelly when daily P&L is negative
5. CLV-gated scaling — tier-based size multiplier
6. Apply ALL multipliers: size * phi_factor * dd_factor * game_kelly_mult * edge_decay_mult
7. P6 max bet cap: if (price * size) > ESPORTS_MAX_BET_USD ($300), clamp
8. Game exposure cap — $3K per game
9. Daily cap — max_daily_usd=$20K
```

---

## CALIBRATION ARCHITECTURE (4 Phases)

### Phase 1: BetaCalibrator (batch, per-game)
- Class: `BetaCalibrator` at `esports_bot.py` lines 47-148
- Algorithm: Kull et al., AISTATS 2017 — `sigmoid(a*ln(p) - b*ln(1-p) + c)`
- Identity state: a=1, b=1, c=0 (passthrough when unfitted)
- Bayesian regularization: lambda=10, penalizes deviation from identity
- Fitting: L-BFGS-B with bounds [(0.1,5.0), (0.1,5.0), (-2.0,2.0)]
- Min samples: 30 resolved predictions per game
- Training window: Starts from `_GLICKO2_FIX_DATE = 2026-03-16`

### Phase 2: OnlinePlattCalibrator (streaming, per-game)
- Class: `OnlinePlattCalibrator` at `esports_bot.py` lines 151-193
- Algorithm: River `LogisticRegression` with `SGD(lr=0.01)`
- Applied AFTER BetaCalibrator in `analyze_opportunity()`

### Phase 3: ConformalPredictor (batch per-game, sizing not calibration)
- Class: `ConformalPredictor` at `esports/models/conformal_wrapper.py`
- Applied in `_execute_esports_trade()` for phi_factor sizing

### Phase 4: ADWIN Drift Detection (streaming, per-game)
- Library: River `ADWIN(delta=0.002)` — advisory only, does NOT halt trading

---

## LEARNING-PHASE SUSPENSIONS (auto-deactivate per game when BetaCalibrator fits)

| Suspension | Behavior When Active | Deactivates When |
|-----------|---------------------|-----------------|
| Edge cap | 0.45 max (REMOVED entirely in S112) | N/A — removed |
| Monitoring halt | Brier halt disabled globally (S111) | N/A — disabled |
| Tournament phase | Observation mode on patch changes | BetaCalibrator fitted for game |
| Game kelly mult | 1.0 (no reduction) | BetaCalibrator fitted for game |
| Phi sizing floor | 0.8 minimum (conservative) | BetaCalibrator fitted -> 0.5 floor |
| Kelly degradation | ALL games must be fitted | Blocked on CoD/R6/RL data |

---

## SCAN WATERFALL (what blocks trades, in order)

1. `no_game` — can't detect game from market question
2. `halted` — Brier halt (**DISABLED** via threshold=999.0)
3. `exposure_cap` — per-game ($3K) / tournament ($5K) / team ($1K) / total ($15K) exceeded
4. `observation` — PatchDriftDetector (48h after game patch)
5. `no_prediction` — team name extraction/matching failed OR tournament_winner type
6. `exit_cooldown` — recently exited this market (15 min Redis-persisted)
7. `max_entries` — 3 entries per market per 12h window
8. `low_confidence` — below 0.48
9. `low_edge` — below 0.05
10. `reentry_rejected` — has position, wrong direction or insufficient edge
11. `passed` -> goes to `_execute_esports_trade()`

*Note: `edge_cap` REMOVED in S112. High edges trade through and are logged as `esportsbot_high_edge`.*

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

# Cap check:
if self._game_exposure.get(game, 0) + _entry_cost > ESPORTS_MAX_GAME_EXPOSURE ($3000):
    # reject — "exposure_cap" waterfall

# Startup restore from daily_counters table
```

---

## ANTI-CHURN SYSTEM (S109 + S111)

```
Stop-loss fires (15% drawdown):
  -> SELL order executed
  -> _recently_exited[market_id] = monotonic_time  (900s cooldown)
  -> _save_exit_cooldown_to_redis()  (survives restart)
  -> _prediction_cache[market_id] cleared  (forces fresh Glicko-2 on re-entry)

Re-entry attempt:
  -> Check _recently_exited: if < 900s ago, reject ("exit_cooldown")
  -> Check _market_entry_times: if >= 3 in last 12h, reject ("max_entries")
  -> Both checks in _churn_blocked() helper — gates scan, WS reactive, AND series paths
```

---

## SHADOW FILLS (S115 cross-bot, affects EsportsBot)

- Paper engine now fills BUY orders at real VWAP from L2 orderbook walk (no more theoretical slippage models)
- Pre-trade book walk + edge-at-VWAP gate: if `confidence <= VWAP`, trade rejected
- `esports_bot.py`: `self._scan_start_mono = _now` at scan entry, `"scan_start_mono"` in `_event_data`
- `shadow_fills` table records every BUY signal with full book snapshot + VWAP + edge
- Review after 24h: `SELECT COUNT(*), AVG(latency_ms), AVG(fill_fraction) FROM shadow_fills WHERE bot_name='EsportsBot'`

---

## KEY CODE LOCATIONS

### bots/esports_bot.py (~5,000+ lines)
| Lines (approx) | What |
|----------------|------|
| 30-148 | `BetaCalibrator` class — `fit_from_db()`, L-BFGS-B, identity priors |
| 151-193 | `OnlinePlattCalibrator` class |
| 270-360 | `__init__` — all instance vars: Glicko-2, caches, `_recently_exited`, `_market_entry_times`, `_beta_calibrators`, WS state |
| 580-608 | `start()` — market service init, Redis cooldown restore, super().start() |
| 624-670 | `on_price_update()` — WS event handler. Token map lookup. Calls super() only for matched markets |
| 740-815 | WS reactive trade path — cooldown check, entry cap check, position check, trade execution |
| 820-855 | `_cleanup_caches()` — evicts stale predictions, token maps, exit cooldowns, entry timestamps |
| 857-920 | `scan_and_trade()` top — exposure restore, daily P&L, position fetch, loss limit, stop-loss exits |
| 1080-1260 | `_analyze_one()` + scan results — waterfall filters, WS token subscription, scan summary log |
| 1290-1553 | `_check_and_execute_exits()` — stop-loss 15%, max hold time, SELL exec, `_recently_exited` + Redis save |
| 1480-1502 | Resolution feed into streaming calibrators |
| 1595-1624 | Calibration application + RFLB correction in `analyze_opportunity()` |
| 1664-1707 | Dynamic edge handling + tournament phase |
| 1810-1940 | `_get_model_prediction()` all paths + event_data |
| 2175-2219 | `_inject_glicko2_metadata()` — uses team name not ID |
| 2290-2320 | XGB + Glicko-2 blend via extremized geometric mean |
| 2757-2941 | `_execute_esports_trade()` — sizing pipeline, exposure tracking, place_order |
| 2955-3015 | `_compute_confluence_score()` — edge=0.65, freshness=0.35 |
| 2975-2991 | `_get_phi_sizing_factor()` with learning-phase floor |
| 2993-3021 | `_update_streaming_on_resolution()` |
| 3060-3130 | Kelly degradation + conformal sizing |
| 3350-3387 | Redis cooldown save/restore |
| 3464-3534 | Monitoring halt / Game kelly mult suspensions |
| 3564-3613 | BetaCalibrator + ConformalPredictor batch fitting |
| 3892-3907 | BetaCalibrator fitting loop in `_check_monitoring_thresholds` |
| 4040-4142 | `_get_glicko2_prediction()` — Bayesian-blended |
| 4387-4504 | `_extract_team_ids_from_question()` — 6 regex patterns |
| 4506-4560 | `_build_glicko2_game_state()` — ML feature dict |
| 4665-4729 | `_match_team_name()` — 6-tier fuzzy matching |
| ~5161-5174 | Series scan path with `_churn_blocked()` gate |

### Other Key Files
| File | Purpose |
|------|---------|
| `config/settings.py` | All ESPORTS_* config with env var overrides (lines ~1008-1197) |
| `esports/models/glicko2.py` | `Glicko2Rating`, `expected_score()`, `Glicko2Tracker` |
| `esports/models/conformal_wrapper.py` | `ConformalPredictor` — logit-space residuals |
| `esports/data/esports_db.py` | Prediction logging (ON CONFLICT upsert since S112) |
| `esports/data/pandascore_client.py` | PandaScore API wrapper (team search, match data) |
| `esports/data/esports_data_collector.py` | Feature extraction from PandaScore matches |
| `base_engine/data/daily_counter.py` | Write-through daily counters (commits since S103) |
| `base_engine/execution/paper_trading.py` | Paper engine. Position key: `(bot_name, market_id)` (S105) |
| `base_engine/execution/order_gateway.py` | Pre-trade book walk, edge-at-VWAP gate (S115) |
| `base_engine/risk/bankroll_manager.py` | `BotBankrollManager` — Kelly sizing |
| `base_engine/data/database.py` | DB layer — `insert_trade_event()` with idempotency |
| `base_engine/base_engine.py` | `BaseBot` parent class — `place_order()`, WS registration |
| `base_engine/data/websocket_manager.py` | WS connect/subscribe/dispatch/reconnect |
| `main.py` | Bot registry, watchdog. EsportsSeriesBot in `_bot_enabled_map` (S106) |
| `schema/migrations/057_esports_prediction_log_dedup.sql` | Dedup migration |
| `scripts/bot_pnl.py` | Canonical P&L script: `python scripts/bot_pnl.py EsportsBot 24` |
| `scripts/esports_diag.py` | Diagnostic script (waterfall, P&L, exposure) |

---

## GLICKO-2 RATING SYSTEM

### Architecture (`esports/models/glicko2.py`)
- `Glicko2Rating`: Dataclass with mu (1500), phi (350), sigma (0.06)
- `expected_score(A, B)`: P(A beats B) accounting for rating diff AND opponent uncertainty
- `Glicko2Tracker`: Manages all team ratings per game. 8 trackers (one per game).

### Bayesian Prior Blending (in `_get_glicko2_prediction()`)
```
max_phi = max(rating_a.phi, rating_b.phi)
phi >= 350 (unrated):    80% market_price + 20% Glicko-2
phi 200-350 (sparse):    50% market_price + 50% Glicko-2
phi 100-200 (developing): 20% market_price + 80% Glicko-2
phi < 100 (mature):       100% Glicko-2
```

### Team Name Matching — 6-Tier System (lines ~4665-4729)
1. Exact match -> 2. Alias lookup (`_TEAM_ALIASES`) -> 3. Substring match ->
4. Reverse substring -> 5. Word-boundary match (short names) -> 6. Difflib fuzzy (0.78)

---

## DATA FLOW & RESOLUTION

```
1. Scan loop (every 2s):
   -> analyze_opportunity() -> predictions logged to esports_prediction_log (ON CONFLICT upsert)

2. Trade execution:
   -> _execute_esports_trade() -> paper orders -> trade_events ENTRY (with event_data)

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
| `system_kv` | Generic key-value store (migration 054). |
| `shadow_fills` | Book walk snapshots + VWAP + edge (S115). |

---

## OUTSTANDING ITEMS (EsportsBot-scoped, Priority Order)

| Priority | Item | Status | Action |
|----------|------|--------|--------|
| **P2** | **High-edge (>0.40) trade outcomes** — monitor for negative trend since S112 removed edge cap | **NEEDS REVIEW** | Check `esportsbot_high_edge` logs. If high-edge trades show negative P&L, consider re-adding cap at 0.50+ |
| **P2** | Exit cooldown 15 min — same-side re-entries 15-20 min after stop-loss, mixed results | Monitoring | Review data. Consider same-side 30 min guard if losses mount |
| **P2** | RC4: Entry price inflation — positions stores requested price not fill price | Deferred | Separate session — touches shared position_manager |
| **P2** | Kelly degradation suspended (needs ALL 8 games fitted) | Blocked on CoD/R6/RL data | Wait for data |
| **P3** | **LoL Brier=0.308 — NOW OVER 0.30 threshold** | Monitoring | Halt disabled, but model may be miscalibrated for LoL. Monitor trend. |
| **P3** | CS2 Brier=0.282 — improving (was 0.334) | Trading for learning data | Positive trend |
| **P3** | `no_prediction: 2-6` per scan — mostly tournament_winner skips | Healthy | Only actionable if count grows |
| **P3** | WS reconnect drops every ~40s-5min | Auto-reconnects working | Monitor |
| **P3** | EsportsSeriesBot silent — no series markets | Expected | No fix |
| **P3** | Tail-price markets (edge>0.50) consistently failing fills (15-22%) | Expected | Paper engine correctly simulating illiquidity |
| **P3** | `exposure_cap: 13` markets blocked this scan | Working as designed | Caps are $3K/game, $5K/tournament, $1K/team — healthy limits |
| **P4** | Shadow fills review — check EsportsBot fill quality after 24h | **NEW from S115** | `SELECT COUNT(*), AVG(latency_ms), AVG(fill_fraction) FROM shadow_fills WHERE bot_name='EsportsBot'` |
| **P5** | taker_side dead code / PAPER_BOOK_WALK_ENABLED | No data source | Deferred |
| **P5** | CoD/R6/RL — no BetaCalibrator data, too few markets | Low priority | Wait |

---

## CRITICAL TRAPS (DO NOT BREAK)

### EsportsBot-Specific
1. **`_game_exposure` is tracked in USD** (`price * size`), not shares. Do not mix units.
2. **`_churn_blocked()` must gate ALL paths to `_execute_esports_trade()`** — scan, WS reactive, AND series. Missing any one creates a churn backdoor.
3. **`_recently_exited` persists to Redis** via `_save_exit_cooldown_to_redis()`. Survives restarts.
4. **`_market_entry_times` does NOT persist** — resets on restart. Acceptable (12h window).
5. **BetaCalibrator training window starts 2026-03-16** — `_GLICKO2_FIX_DATE`. Stale pre-fix data excluded.
6. **PandaScore rate limit**: 1000/hr budget. Current usage ~400/hr. `_refresh_live_matches()` has 15s time guard regardless of scan interval.
7. **Edge cap is REMOVED** — no `_max_edge` attribute, no `edge_cap` waterfall counter. Do not re-add without user request.
8. **Brier halt is DISABLED** — `ESPORTS_BRIER_HALT_THRESHOLD=999.0`. All games trade. Do not re-enable without user request.
9. **`esports_prediction_log` now has unique index on (market_id, bot_name)** — INSERT must use ON CONFLICT or will fail on duplicates.
10. **Exposure cap hierarchy**: team ($1K) < game ($3K) < tournament ($5K) < total ($15K) < capital ($20K).
11. **BetaCalibrator parameters near identity** — all fitted games show a~1, b~1, c~0. Raw Glicko-2 already well-calibrated.
12. **All learning suspensions check `_beta_calibrators.get(game)._fitted`** — auto-deactivate. Don't remove manually.
13. **BOT_BANKROLL_CONFIG in .env overrides code defaults** — this is the REAL config.
14. **ESPORTS_MAX_BET_USD enforced by P6 cap in `_execute_esports_trade()`** — separate from BotBankrollManager. Both apply.
15. **`_tournament_phase` must be defined BEFORE the if/else** — Python 3.13 scoping.
16. **OnlinePlattCalibrator requires `river` package** — gracefully degrades if not installed.
17. **`_inject_glicko2_metadata()` uses `.get("name", "").lower()`** — S97 fix. Do NOT change to numeric ID.
18. **PatchDriftDetector**: `_patch_timestamps` only set when `old is not None` (S88 fix).
19. **`daily_counter.py` now commits** — S103 fix. Do NOT remove `await sess.commit()`.
20. **`_resolve_esports_from_clob()` processes ALL unresolved** — no LIMIT (S104 fix).

### System-Wide (from CLAUDE.md)
21. **trade_events is P&L authority** — never read paper_trades for P&L.
22. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
23. **BotBankrollManager handles SIZING; risk_manager handles LIMITS** — both must pass.
24. **`risk_manager.calculate_position_size()` is DEPRECATED** — BotBankrollManager used.
25. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
26. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
27. **asyncpg DATE**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime.
28. **Python 3.13 scoping**: `from X import Y` inside function makes Y local for ENTIRE function. Any use of Y BEFORE that import -> UnboundLocalError.
29. **trade_events immutability trigger**: `trg_trade_events_immutable` prevents DELETE/UPDATE. Must DISABLE/ENABLE for corrections.
30. **RESOLUTION event idempotency**: ON CONFLICT broken on partitioned tables. Uses atomic INSERT...SELECT with WHERE NOT EXISTS.
31. **Paper engine positions key**: `(bot_name, market_id)` — NEVER `market_id` alone (S105).
32. **`realized_pnl_today`**: Now `Dict[str, float]` not `float`. Access via `.get(bot_name, 0.0)` (S105).
33. **Partial exit fee proration**: `prorated_entry_fee = entry_fee * (exit_size / pos_size)` (S105).
34. **positions table**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`. Use `source_bot` not `bot_name`.
35. **`prediction_log`**: NO `rejection_reason` column. Use `trade_executed` (bool).
36. **`traded_markets.bot_names`**: TEXT column (not array), use `LIKE '%BotName%'`.
37. **BOT_REGISTRY=15 bots** — shared module change requires all 15 verified.
38. **`paper_trades` has NO `metadata` JSONB column**.
39. **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time.
40. **Alpha decay requires `scan_start_mono` in event_data** — EsportsBot now passes it (S115).

### P&L Calculation Rules (MANDATORY)
- **NEVER invert formulas for NO positions** — prices are token-specific
- `cost = entry_price * size` (ALL sides)
- `unrealized_pnl = (current_price - entry_price) * size` (ALL sides)
- Canonical script: `python scripts/bot_pnl.py EsportsBot 720`
- Data source: `trade_events` (realized), `positions.unrealized_pnl` (mark-to-market)

---

## USER DIRECTIVES (carry forward — these are standing orders)

1. **"All games trade, even if they are shit. We need to learn."** — Do not re-enable Brier halting without explicit user request.
2. **"Remove edge cap, but report anything over .40 in handoff if we have a negative trend."** — Edge cap removed. Monitor `esportsbot_high_edge` logs on handoffs.
3. **"Paper trading is production."** — Never cut corners because SIMULATION_MODE=true. Max total exposure must not exceed capital.
4. **"Monitor exit cooldown and review on handoffs."** — 15-min cooldown question deferred. Collect data, don't change yet.
5. **Scope lock** — Fix only what is requested. No unsolicited features, refactors, or "while I'm in here" changes.

---

## VPS ACCESS & DEPLOY

```bash
SSH_KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem
VPS=ubuntu@34.251.224.21

# SSH
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS "COMMAND"

# Deploy (SCP + copy + restart)
scp -i $SSH_KEY bots/esports_bot.py $VPS:/tmp/
ssh -i $SSH_KEY $VPS "sudo cp /tmp/esports_bot.py /opt/polymarket-ai-v2/bots/esports_bot.py && sudo chown polymarket:polymarket /opt/polymarket-ai-v2/bots/esports_bot.py && sudo systemctl restart polymarket-ai"

# Use python3 not python on VPS
```

---

## VERIFICATION COMMANDS

```bash
SSH_KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem
VPS=ubuntu@34.251.224.21

# Scan health (edge_cap should NOT appear in waterfall)
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '2 min ago' --no-pager | grep esportsbot_scan_summary | tail -3"

# High-edge monitoring (esportsbot_high_edge lines for edge>0.40)
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '30 min ago' --no-pager | grep esportsbot_high_edge | tail -10"

# P&L
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT event_type, COUNT(*), ROUND(COALESCE(SUM(realized_pnl),0)::numeric,2) FROM trade_events WHERE bot_name='EsportsBot' GROUP BY event_type;\""

# P&L by day
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT event_time::date as day, event_type, COUNT(*), ROUND(COALESCE(SUM(realized_pnl),0)::numeric,2) FROM trade_events WHERE bot_name='EsportsBot' AND event_time > '2026-03-19' GROUP BY 1,2 ORDER BY 1,2;\""

# Open positions
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT COUNT(*) FROM positions WHERE source_bot='EsportsBot' AND status='open';\""

# Brier scores by game
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT game, COUNT(*), ROUND(AVG((predicted_prob-COALESCE(actual_outcome,0))^2)::numeric,4) as brier FROM esports_prediction_log WHERE created_at>'2026-03-16' AND actual_outcome IS NOT NULL GROUP BY game ORDER BY brier;\""

# BetaCalibrator
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '30 min ago' --no-pager | grep beta_cal"

# Anti-churn (after stop-loss fires)
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai -f | grep 'exit_cooldown\|max_entries\|esportsbot_stop_loss'"

# WS status
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'esportsbot_ws_subscribed\|ws_trading'"

# Shadow fills (S115)
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT COUNT(*), ROUND(AVG(latency_ms)::numeric,1), ROUND(AVG(fill_fraction)::numeric,3) FROM shadow_fills WHERE bot_name='EsportsBot';\""

# Errors
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager | grep -i 'EsportsBot.*error\|EsportsBot.*exception' | tail -10"

# Prediction log integrity (total_rows should equal unique_markets)
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS \
  "sudo -u polymarket psql -d polymarket -c \"SELECT COUNT(*) as total_rows, COUNT(DISTINCT market_id) as unique_markets FROM esports_prediction_log;\""
```

---

## SESSION CHECKLIST (do this before any code change)

1. Read `CLAUDE.md` (repo root)
2. Read this prompt fully
3. State what you will work on (from outstanding items or user request)
4. List files you will touch (max 3 unless justified)
5. Grep for dependents before editing
6. Git snapshot before any edit (`git stash` or `git commit -m "pre-fix: <desc>"`)
7. Read the ENTIRE file you're modifying, not just the function
8. One fix per commit
9. Write change log per CLAUDE.md format
10. Verify on VPS after deploy

---

## FEEDBACK RULES (MANDATORY)

1. **Scope Lock** (`memory/feedback_scope_lock.md`): NEVER add unsolicited features. Only fix what handoff/user explicitly requests. "I noticed X could be improved" -> mention in handoff, do NOT implement.
2. **Bot Sessions** (`memory/feedback_bot_sessions.md`): Esports sessions are hardcoded esports-only. Never commit non-esports changes.
3. **P&L Math** (`memory/feedback_pnl_math.md`): NEVER invert formulas for NO positions. `cost = entry_price * size` for ALL sides.
