# CONTINUATION PROMPT — EsportsBot Session 125
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
- **BetaCalibrator** (Kull et al. 2017): `sigmoid(a*ln(p) - b*ln(1-p) + c)` — fits per-game, needs 10+ resolved samples (lowered from 15 in S125)
- **Conformal prediction**: per-game prediction intervals for uncertainty-aware sizing
- **Cross-game XGBoost**: blended with Glicko-2 via extremized geometric mean (0.6/0.4 weights)
- **Per-game ML models**: CS2 (dual-path: pregame + economy), LoL (dual-path: pregame + live), Dota2 (XGBoost), Valorant (XGBoost)
- **Paper trading engine**: shared across 15 bots, fill probability, VWAP book walk, alpha decay

**EsportsLiveBot**: Live in-game trading using WS price feeds (shares `esports_bot.py`)
**EsportsSeriesBot**: Series-level trading via `_series_scan()` (currently silent — no series markets on Polymarket)

---

## SESSION 125 RECAP — WHAT WAS DONE (2026-03-23/24)

### Fix 1: BetaCalibrator min_samples 15→10 (ISSUE-1 root fix)
**Problem:** NO-side trades had 18% WR (-$1,445 in 48h). Root cause: Glicko-2 `expected_score()` uses `_g(phi)` dampening which inherently compresses outputs toward 0.5. When model says P(underdog)=0.35, true probability may be 0.25. Bot flips to NO side with inflated confidence=0.65, finds phantom edge.

**Analysis performed:** Evaluated 4 options:
1. Extremize Glicko-2 output (rejected — cascading tuning debt, interacts with 7 downstream enrichment steps, cross-game XGB trained on compressed outputs, creates discontinuity when BetaCalibrator fits per-game)
2. Mirror RFLB for underdogs (rejected — band-aid, only addresses one direction)
3. Lower BetaCalibrator min_samples (CHOSEN — accelerates the permanent fix, zero interaction risk)
4. NO-side edge premium (rejected — symptom treatment, doesn't fix model)

**Why extremization was rejected (IMPORTANT — do not revisit):** Cross-game XGB was trained on compressed Glicko-2 outputs. Extremizing changes input distribution to the EGM blend. Auto-disable per-game when BetaCalibrator fits creates inconsistent distributions across games feeding cross-game XGB. Every downstream step (RFLB, edge thresholds, sizing, conformal) was tuned to compressed outputs. In 4 months when all games have calibrators, the extremization code is dead with tombstone cleanup needed.

**Fix:** `bots/esports_bot.py` line 297: `min_samples=15` → `min_samples=10`. Lambda_reg=10.0 provides strong regularization safety net with fewer samples. BetaCalibrator IS the permanent calibration fix — it learns per-game a,b,c params that naturally de-compress predictions.

**VPS state at time of fix:** CS2 had 9 resolved samples, Dota2 had 1, all others 0. With min_samples=10 + the markets-table backfill (Fix 3), CS2 jumps to 15 resolved → fits immediately.

### Fix 2: Restore `_market_game` from DB on startup (ISSUE-2)
**Problem:** `_market_game` dict (line 246) populated on ENTRY but never restored on startup. After bot restart, EXIT events tagged with empty game string → per-game P&L tracking broken. S121 had added game tags to EXIT (line 1490) and RESOLUTION (line 1678) events, but both relied on the in-memory dict.

**Fix:** New `_restore_market_game_from_db()` method (~25 lines) following exact pattern of `_restore_exposure_from_db()`. Queries ENTRY trade_events JOINed to open positions, rebuilds `_market_game[market_id] = game`. Called in `scan_and_trade()` on first run (DB not available in `start()`), guarded by `_market_game_restored` flag.

**SQL:** `SELECT DISTINCT ON (te.market_id) te.market_id, te.event_data->>'game' FROM trade_events te JOIN positions p ON te.market_id = p.market_id WHERE te.event_type = 'ENTRY' AND p.status = 'open' AND bot_name IN (...)`

### Fix 3: Markets-table backfill fallback (ISSUE-3/4 accelerator)
**Problem:** `_backfill_esports_outcomes()` only resolved predictions from `trade_events` RESOLUTION events within 7-day window. 13 predictions sat unresolved despite their markets being resolved in the `markets` table. Causes:
1. Bot predicted but didn't trade (no RESOLUTION trade_event exists)
2. RESOLUTION trade_event older than 7-day window
3. Bot entered AFTER market resolved (stale market re-scanned)

**Evidence:** LoL had 0/9 resolved (1 market resolved Mar 9 but RESOLUTION event >7d old). CS2 had 9/118 resolved but 6 more resolvable from markets table. Dota2 had 1/19 but 4 more resolvable.

**Fix:** Added ~18-line fallback pass in `_backfill_esports_outcomes()` after the existing trade_events pass. Queries `esports_prediction_log JOIN markets WHERE actual_outcome IS NULL AND resolution IN ('YES','NO')`. Calls existing `resolve_predictions(db, market_id, outcome)`.

**Impact:** CS2 jumps from 9 to 15+ resolved → BetaCalibrator fits on first cycle. Dota2 jumps to 5. All games accumulate faster.

### Weather Bug Audit (no code change — verification only)
**Context:** WeatherBot S123 deployed Platt+Isotonic confidence calibration (T=2.271) that compressed confidence so aggressively that `confidence <= price` in Kelly → killed 85% of trades.

**Full audit performed:** Traced every path where confidence/model_prob gets modified before Kelly's `kelly_full <= 0` kill gate in EsportsBot:

| Path | Risk | Why Safe |
|------|------|----------|
| BetaCalibrator | SAFE | Lambda_reg=10 keeps params near identity (a≈1, b≈1, c≈0). Cannot compress 0.55 below 0.50 |
| OnlinePlattCalibrator | LOW | SGD lr=0.01 + min_samples=30. Conservative streaming. Would take 100+ biased samples to deviate |
| RFLB | SAFE | Max 1.35% adjustment, only touches favorites |
| _enrich_prediction() | SAFE | All bounded ±3%, clipped [0.05, 0.95] |
| Expiry boost | SAFE | Only increases confidence |
| Conformal phi_factor | SAFE | Affects sizing multiplier, not confidence |
| WS path | SAFE | Uses cached scan-path prob, same min_edge gate |
| Series path | SAFE | No calibrator, min_edge=0.10 (2x base) |

**Critical difference from WeatherBot:** In EsportsBot, calibration happens BEFORE the edge check (line 1942-1948 → edge check at 1992). Compressed predictions simply don't pass the 5% edge gate. In WeatherBot, calibration happens AFTER the edge check — trades pass the gate with raw edge but get killed at Kelly when calibrated confidence drops below price. **0% chance of the weather bug in EsportsBot.**

### SSH/VPS Connectivity Diagnosis
- ICMP (ping) is blocked by AWS Lightsail firewall — 100% packet loss is NORMAL
- TCP port 22 is OPEN — `Test-NetConnection -Port 22` returns `TcpTestSucceeded=True`
- SSH connects and authenticates fine
- Previous timeouts caused by `journalctl --no-pager | grep` streaming entire journal before filtering. Fix: use `--since` with short windows, or `timeout` wrapper, or simple commands first.

---

## SESSION 120 RECAP (prior session, 2026-03-23)

### Phase 1: CS2 Pre-Game Prediction Path (was DEAD — now LIVE)
- `esports/data/esports_data_collector.py` — Added `_get_glicko2_metadata()` for CS2 training data
- `esports/models/cs2_economy_model.py` — `PREGAME_FEATURES`, `predict_pregame()`, `train_pregame()`
- `esports/models/esports_trainer.py` — `model.train_pregame(train_set)` in `_train_cs2()`

### Phase 2: LoL Pre-Game Prediction Path (was DEAD — now LIVE)
- Added LoL pre-game block with neutral live features + `_build_glicko2_game_state()`

### Phase 3: Unified Enrichment Pipeline (`_enrich_prediction()`)
- Extracted ~170 lines into single method. All 7 prediction paths now call it.

### Phase 4-8: GAP-1 (CLOB volume), GAP-4 (min trade floor $10), GAP-2 (side-aware dedup), roster hygiene, no_prediction logging

### Phase 9: CVaR Cap $5K→$10K

---

## PREDICTION PIPELINE (current state after S125)

1. **Glicko-2 rating lookup** → raw `model_prob` (P(team A wins))
2. **BetaCalibrator** (if fitted for game, min_samples=10) → calibrated probability
3. **Online Platt scaling** (if available) → applied to RAW prob (not beta-calibrated)
4. **RFLB correction** → favorites-longshot bias (price>0.70, model_prob>0.60)
5. **BO adjustment** → best-of-1 dampening (via `_enrich_prediction`)
6. **Cross-game XGBoost blend** → EGM with Glicko-2 (0.6/0.4 weights, via `_enrich_prediction`)
7. **Per-game conformal prediction** → uncertainty intervals for sizing
8. **Edge calculation** → `model_prob - market_price` (YES) or `(1-model_prob) - (1-price)` (NO)
9. **Min edge gate** → 0.05 (scan), 0.05 (WS), 0.10 (series). **This is the safety net that prevents the WeatherBot calibration kill bug.**
10. **BotBankrollManager sizing** → Kelly fraction with conformal bounds

### Full Prediction Path (in `_get_model_prediction()`)
```
Market -> detect_game() -> _get_model_prediction() -> one of:

|- LoL LIVE (live_data + _lol_model.is_trained):
|   -> _inject_glicko2_metadata → predict_with_glicko2 → _enrich_prediction

|- LoL PRE-GAME (not live_data + _lol_model.is_trained):   [S120]
|   -> neutral live features + _build_glicko2_game_state → predict_with_glicko2 → _enrich_prediction

|- CS2 LIVE (live_data + _cs2_model.is_trained):
|   -> predict_match (round→map→match chain) → _enrich_prediction

|- CS2 PRE-GAME (not live_data + _cs2_model.is_trained):   [S120]
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
10. GAP-4 min floor: if (price * size) < ESPORTS_MIN_TRADE_USD ($10), reject   [S120]
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
5. `no_prediction` — team name extraction/matching failed OR tournament_winner type
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
- Min 10 resolved samples per game (lowered from 15 in S125), 90-day window from `_GLICKO2_FIX_DATE = 2026-03-16`
- Lambda_reg=10.0 (strong regularization, keeps params near identity with few samples)
- Bounds: a∈[0.1,5.0], b∈[0.1,5.0], c∈[-2.0,2.0]
- **STATUS**: CS2 will fit on next cycle after S125 deploy (9 samples + 6 from markets-table backfill = 15). Dota2 at 5. Others 0-1.

### Phase 2: OnlinePlattCalibrator (streaming, per-game)
- River `LogisticRegression` with `SGD(lr=0.01)`, min_samples=30
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
| Mar 23-24 | -$252 | 48h window from S125 P&L check |
| **All-time** | **+$618** | From trade_events (AUTHORITY) |

**48h breakdown (S125 check):**
- 48 entries, 18 exits (-$1,593 realized), 13 resolutions (+$1,211)
- 30 open positions ($7,162 cost, $7,292 value, +$130 unrealized)
- Heavy NO-side exit losses: -$311, -$253, -$207, -$196 (confirms NO-side problem)

**Side asymmetry**: NO 18% WR (BAD), YES 46% WR. BetaCalibrator will fix once fitted.

### Brier by Game
| Game | Brier | Accuracy | Status |
|------|-------|----------|--------|
| Valorant | 0.153 | 72% | Best — carrying P&L |
| Dota2 | 0.231 | 62% | Decent |
| CS2 | 0.273 | 42% | BetaCalibrator about to fit |
| LoL | 0.308 | 31% | **Worst — active P&L leak, fewest resolutions** |

---

## LIVE VPS CONFIG (as of S122 deploy — S125 not yet deployed)

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

### bots/esports_bot.py (~5850 lines post-S125)
| Lines (approx) | What |
|----------------|------|
| 47-149 | `BetaCalibrator` class |
| 151-192 | `OnlinePlattCalibrator` class |
| 195-395 | `__init__` — all instance vars |
| 420-600 | `start()` — client init, Glicko-2, calibrators |
| 684-800 | `on_price_update()` — WS reactive trading (GAP-2 side-aware dedup) |
| 826-845 | `_cleanup_caches()` |
| 846-1280 | `scan_and_trade()` — main loop, waterfall, parallel analysis |
| 1316-1360 | `_restore_exposure_from_db`, `_restore_market_game_from_db` [S125] |
| 1360-1400 | `_restore_daily_pnl_from_db` |
| 1430-1580 | `_check_and_execute_exits()` — stop-loss, max hold |
| 1620-1800 | `_resolve_esports_from_clob()` — CLOB resolution |
| 1789-1843 | `_backfill_esports_outcomes()` — trade_events + markets-table fallback [S125] |
| 1845-2130 | `analyze_opportunity()` — edge/confidence/volume passthrough |
| 2135-2260 | `_enrich_prediction()` — unified enrichment [S120] |
| 2260-2480 | `_get_model_prediction()` — LoL live/pregame, CS2 live/pregame, Dota2, Valorant, fallback |
| 2700-2950 | Form adjustment methods |
| 2960-3080 | `_detect_game()`, `_classify_market_type()` |
| 3083-3260 | `_execute_esports_trade()` — sizing pipeline (GAP-4 min floor) |
| 3325-3460 | `_apply_expiry_boost()`, `_get_phi_sizing_factor()` |
| 3700-3950 | `_check_monitoring_thresholds()` — Brier, calibrator fitting |
| 4357-4460 | `_get_glicko2_prediction()` — Bayesian blend |
| 4463-4550 | `_check_roster_stability()` — 1K hygiene fixes |
| 4600-4850 | Team extraction + matching (6 regex patterns + fuzzy) |
| 5050-5670 | Series analysis + trading (GAP-2 side-aware dedup on series WS) |
| 5650-5760 | `_series_on_price_update()` |

### Other Key Files
| File | Purpose |
|------|---------|
| `config/settings.py` | All ESPORTS_* config + RISK_MAX_PORTFOLIO_CVAR_USD |
| `esports/models/cs2_economy_model.py` | CS2 model: PREGAME_FEATURES, predict_pregame, train_pregame, round→map→match chain |
| `esports/models/esports_trainer.py` | Per-game + cross-game training, `_train_cs2()` trains pregame too |
| `esports/data/esports_data_collector.py` | Training data: CS2 has Glicko-2 metadata |
| `esports/models/glicko2.py` | Glicko2Rating, expected_score, Glicko2Tracker |
| `esports/models/conformal_wrapper.py` | ConformalPredictor — logit-space residuals |
| `esports/models/lol_win_model.py` | LoL ML model (9 features) |
| `esports/models/dota2_model.py` | Dota2 XGBoost (6 features) |
| `esports/models/valorant_model.py` | Valorant XGBoost (6 features) |
| `esports/models/series_model.py` | BO3/BO5 probability, map veto, bo1_underdog_adjustment |
| `esports/models/patch_drift.py` | Patch version monitoring, observation mode |
| `esports/data/esports_db.py` | All DB functions incl. resolve_predictions() |
| `esports/data/esports_data_collector.py` | Training data from PandaScore |
| `esports/data/opendota_client.py` | OpenDota API |
| `bots/esports_live_bot.py` | Live in-game bot |
| `base_engine/risk/bankroll_manager.py` | Kelly sizing: `kelly_full = (confidence*b - q) / b`, kill at ≤0 |
| `base_engine/features/aggregation.py` | `extremized_geometric_mean()` |
| `scripts/bot_pnl.py` | P&L reporting from trade_events |

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

---

## STATE PERSISTENCE (what survives restarts)

| State | Mechanism | Restored in |
|-------|-----------|-------------|
| `_game_exposure` | `daily_counters` write-through | `_restore_exposure_from_db()` in scan_and_trade |
| `_daily_pnl` | Query `trade_events` SUM | `_restore_daily_pnl_from_db()` in scan_and_trade |
| `_recently_exited` | Redis TTL keys | `_restore_exit_cooldowns_from_redis()` in start() |
| `_market_game` | Query trade_events ENTRY + positions | `_restore_market_game_from_db()` in scan_and_trade [S125] |
| `_open_positions` | `positions` table | position_manager |
| Glicko-2 ratings | `esports_glicko2_ratings` table | `_init_glicko2_trackers()` in start() |
| BetaCalibrator params | Re-fitted from `esports_prediction_log` every 10 min | `_check_monitoring_thresholds()` |
| Prediction cache, live match tracking | NOT persisted — 10-second re-sync on restart | N/A |

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
- **Extremization of Glicko-2 was REJECTED** [S125] — cascading tuning debt, cross-game XGB input mismatch. BetaCalibrator is the permanent fix.
- **min_samples=10 is intentional** [S125] — lambda_reg=10 provides safety. Do NOT raise back to 15.
- **Markets-table backfill is the second pass** [S125] — runs AFTER trade_events pass. Both are needed.
- **SSH to VPS**: ICMP blocked (ping fails), TCP:22 open. Use `ssh` directly, avoid `journalctl --no-pager | grep` on large windows (hangs). Use `--since '1 hour ago'` or `timeout 30`.

---

## DEPLOY PROTOCOL

```bash
# Upload files
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o StrictHostKeyChecking=no <files> ubuntu@34.251.224.21:/tmp/

# Copy to app directory + restart
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo cp /tmp/<file> /opt/polymarket-ai-v2/<path>/ && sudo systemctl restart polymarket-ai"

# Verify (use short --since windows — journal is huge)
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sleep 25 && sudo journalctl -u polymarket-ai --since '30 sec ago' -o cat --no-pager | grep 'esportsbot_scan_summary' | tail -2"

# VPS venv: /opt/polymarket-ai-v2/venv/bin/activate
# VPS .env: /opt/pa2-shared/.env
# Current deploy: 20260323_161454 (symlink /opt/polymarket-ai-v2 -> /opt/pa2-releases/20260323_161454)
# Deploy script: bash deploy/deploy.sh (atomic symlink swap)
# PYTHONPATH must be set for scripts: PYTHONPATH=/opt/polymarket-ai-v2
```

## Tests
```bash
python -m pytest tests/unit/ -x -q --ignore=tests/unit/test_weather_bot.py
# Expected: 1460+ passed, 2 skipped, 0 failures
# Esports-only: python -m pytest tests/unit/test_esports_bot.py tests/unit/test_esports_series_model.py -x -q
# Expected: 86+ passed
```

---

## S125 DEPLOYMENT STATUS: NOT YET DEPLOYED

Three fixes are ready in local code, tests pass. Files to deploy:
- `bots/esports_bot.py` — all 3 fixes

**Post-deploy verification:**
```bash
# 1. BetaCalibrator should fit CS2 within 10 min
ssh ... "sleep 600 && sudo journalctl -u polymarket-ai --since '10 min ago' -o cat --no-pager | grep 'esportsbot_beta_cal_fitted'"

# 2. Markets-table backfill should resolve orphaned predictions
ssh ... "sleep 120 && sudo journalctl -u polymarket-ai --since '2 min ago' -o cat --no-pager | grep 'esportsbot_markets_table_backfill'"

# 3. Game tag restore on restart
ssh ... "sudo journalctl -u polymarket-ai --since '1 min ago' -o cat --no-pager | grep 'esports_market_game_restored'"
```

---

## NEXT SESSION PRIORITIES

### Immediate (post-deploy monitoring)
1. **Verify BetaCalibrator CS2 fit** — check a,b,c params. If significantly non-identity, monitor P&L impact.
2. **Monitor NO-side WR** — should improve from 18% toward 30%+ as calibration kicks in.
3. **48h P&L check** — run `python scripts/bot_pnl.py EsportsBot 48` after 48h of calibrated trading.

### Pending User Approval
4. **GAP-5: Calibration exclusion flags** — exclude bad trades from BetaCalibrator training. Implement when first game has fitted calibrator.
5. **PR-1: Confluence gate rebuild** — multi-signal filter. ~2h.
6. **PR-3: Champion drift detection** — LoL patch-aware. Could help LoL (worst game). ~1h.
7. **PR-4: Team exposure tracking** — correlated risk. ~1h.

### Design Observations (no code change yet)
- LoL model pickle missing on VPS — falls back to pure Glicko-2. Needs training pipeline.
- Dota2/Valorant models are copy-pasted. Refactor to shared base class.
- CS2 model is 1-2 effective features in 14-feature wrapper (live path).
- TabPFN not installed. Sparse games run Glicko-2 + cross-game XGB only.
- LoL blue side bonus applied without verifying actual side (line ~2399).
- CatBoost draft model disabled (`ESPORTS_CATBOOST_ENABLED=false`).
- `bot_pnl.py` shows 100 data integrity warnings (SELL with no matching ENTRY) — historical positions entered before trade_event logging existed. Not a live bug.

---

## FILES MODIFIED IN S125 (1 file)
- `bots/esports_bot.py` — min_samples 15→10, `_restore_market_game_from_db()`, markets-table backfill fallback

## CUMULATIVE UNCOMMITTED CHANGES (S118-S125, 35 files)
All changes span multiple session tracks (esports, mirror, weather). Esports-scoped changes verified clean — no bleed. Full test suite: 1460 passed, 2 skipped, 0 failures.
