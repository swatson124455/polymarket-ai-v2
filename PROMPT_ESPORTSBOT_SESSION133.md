# CONTINUATION PROMPT — EsportsBot Session 133
# Carbon-copy agent handoff. Paste into a fresh session. DO NOT bleed into MirrorBot or WeatherBot.

---

## CRITICAL: Read This First
You are continuing an **EsportsBot-only** session for the Polymarket AI V2 automated trading system. Read `CLAUDE.md` in the repo root — it is the prime directive. Then read this document fully before doing anything.

**SCOPE LOCK is active.** Only touch: `bots/esports_bot.py`, `bots/esports_live_bot.py`, `esports/**`, esports tests, `config/settings.py` (ESPORTS_ keys only). Shared modules ONLY if required for an esports bug fix and justified explicitly. NEVER commit changes to mirror_bot.py, weather_bot.py, or other non-esports files.

---

## ⚠️ CRITICAL CODE DIVERGENCE — FIX FIRST

**VPS (`/opt/polymarket-ai-v2/`) has S131+S132 changes. Local git does NOT.**

Local HEAD is at S129 commits (`f125fae`). The S131 SQ fix + S132 data integrity fixes were deployed to VPS via SCP but never committed to git. The VPS is the source of truth.

**Before making ANY new changes, sync VPS→local:**
```bash
# Pull the authoritative esports_bot.py from VPS
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21:/opt/polymarket-ai-v2/bots/esports_bot.py bots/esports_bot.py

# Also pull test file (S131 updated 3 assertions)
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21:/opt/polymarket-ai-v2/tests/unit/test_esports_bot.py tests/unit/test_esports_bot.py

# Commit
git add bots/esports_bot.py tests/unit/test_esports_bot.py
git commit -m "S131+S132: SQ→sizing multiplier, delete _resolve_esports_from_clob, opposing-side guard, confidence in event_data"
```

**What the VPS has that local doesn't:**
1. **S131**: `confidence = side_prob` (4 sites), SQ as sizing multiplier in `_execute_esports_trade`, component defaults raised, Brier cache seeded on startup, 0.52 confidence floor
2. **S132 Fix 1**: `_resolve_esports_from_clob()` deleted (~136 lines) + call site removed
3. **S132 Fix 4**: `_entered_market_sides` set in `__init__`, restored from DB in `scan_and_trade`, guard in `_execute_esports_trade` + WS paths, tracking on successful entry
4. **S132 Fix 5**: `confidence` + `signal_quality` persisted in event_data JSONB

---

## SYSTEM OVERVIEW

**Polymarket AI V2**: 15-bot automated prediction market trading system. Paper trading mode (`SIMULATION_MODE=true`) on Ubuntu VPS (34.251.224.21). Real capital architecture, $0 execution flag. Paper trading IS production — only difference is final order submission.

**EsportsBot**: Trades esports match-winner markets using:
- **Glicko-2 ratings** (per-game trackers for 8 games: LoL, CS2, Valorant, Dota2, SC2, CoD, R6, RL)
- **BetaCalibrator** (Kull et al. 2017): `sigmoid(a*ln(p) - b*ln(1-p) + c)` — fits per-game, needs 10+ resolved samples
- **Conformal prediction**: per-game prediction intervals for uncertainty-aware sizing
- **Cross-game XGBoost**: blended with Glicko-2 via extremized geometric mean (0.6/0.4 weights)
- **Per-game ML models**: CS2 (dual-path: pregame + economy), LoL (dual-path: pregame + live), Dota2 (XGBoost), Valorant (XGBoost)
- **Signal quality system** (S127→S131): 5-component composite, used as SIZING multiplier (not confidence multiplier)
- **Paper trading engine**: shared across 15 bots, fill probability, VWAP book walk, alpha decay

**EsportsLiveBot**: Live in-game trading using WS price feeds + game monitor queue (maxsize=500)
**EsportsSeriesBot**: Series-level trading via `_series_scan()` (currently silent — no series markets on Polymarket)

---

## WHAT HAPPENED IN S131 + S132

### S131 — Signal Quality Root Fix (DEPLOYED 20260325)

**Problem**: Bot stopped trading on March 25. `confidence = side_prob * signal_quality` crushed confidence below market price → negative edge → Kelly=0 → every trade killed.

**Fix**: SQ moved from confidence multiplier to sizing multiplier.
```
BEFORE: confidence = side_prob * SQ → 0.577 * 0.39 = 0.225 → edge = -0.200 → KILLED
AFTER:  confidence = side_prob = 0.577, size *= SQ(0.525) → trade at ~52% Kelly → EXECUTES
```

**Changes**: 4 confidence assignment sites (`confidence = side_prob`), SQ×size in `_execute_esports_trade`, 3 component defaults raised (agreement 0.50→0.70, calibration 0.30→0.50, brier 0.25→0.15), Brier cache seeded in `start()`, confidence gate repurposed to 0.52 side_prob floor.

### S132 — Data Integrity Fix (DEPLOYED 20260325_202511)

**6 fixes**, 5 applied:

| Fix | Description | Status |
|-----|-------------|--------|
| **Fix 1** | Delete `_resolve_esports_from_clob()` (~136 lines) — S104 workaround that corrupted paper_trades (set resolution without realized_pnl, blocking shared backfill forever) | DEPLOYED |
| **Fix 2** | Recompute 115 paper_trades with resolution but NULL P&L | SQL EXECUTED |
| **Fix 3** | Delete 85 phantom RESOLUTION events + re-emit via shared backfill (32 inserted, 17 updated) | SQL EXECUTED |
| **Fix 4** | Opposing-side guard — `_entered_market_sides` set, DB restore on startup, guard in all 3 entry paths, tracking on success | DEPLOYED |
| **Fix 5** | Persist `confidence` + `signal_quality` in event_data JSONB (main + S-T paths) | DEPLOYED (not yet tested — no new entries) |
| **Fix 6** | Phase 4b EXIT P&L subtraction | ALREADY IMPLEMENTED (no changes needed) |

**Bugs killed**: SE-1, EB-1, EB-2 (Fix 1), EB-3 (Fix 4), EB-4 (Fix 1), EB-5 (Fix 5)

---

## CURRENT STATE (as of 2026-03-26 00:31 UTC)

### P&L Summary
```
All-time realized:  -$4,368.04
  Exits:    192  (-$1,448.35)
  Resolutions: 127  (-$2,919.69)
Open positions: 16  ($4,378 cost, -$106 uPnL)
Net P&L:       -$4,473.58
```

### Corrected WR by Game (RESOLUTION events with non-NULL P&L)
| Game | Resolutions | Wins | WR% | P&L |
|------|------------|------|-----|-----|
| sc2 | 1 | 1 | 100.0% | +$40.61 |
| cod | 1 | 1 | 100.0% | +$4.24 |
| valorant | 12 | 6 | 50.0% | -$178.83 |
| cs2 | 62 | 27 | 43.5% | -$382.73 |
| dota2 | 28 | 16 | 57.1% | -$561.35 |
| unknown | 10 | 4 | 40.0% | -$889.70 |
| lol | 13 | 1 | 7.7% | -$951.94 |

### All-Time P&L by Game (EXIT + RESOLUTION, from S130 diagnostic)
| Game | EXIT P&L | RES P&L | **Total** | WR | Notes |
|------|----------|---------|-----------|-----|-------|
| **Valorant** | +$3,911 | +$221 | **+$4,132** | 57.1% | ONLY profitable game. DO NOT TOUCH. |
| SC2 | — | +$41 | **+$41** | 100% | 1 trade |
| CS2 | -$2,967 | +$72 | **-$2,896** | 53.7% | Model barely above random |
| CoD | -$847 | -$304 | **-$1,151** | 0% | No ML model. DISABLE. |
| Dota2 | -$819 | -$585 | **-$1,404** | 75.0% | WR good but avg loss 2x avg win |
| LoL | -$455 | -$1,403 | **-$1,858** | 16.7% | BUG-24 fix working post-S128 |

**Critical insight: EXIT losses are 2.5x worse than RESOLUTION losses.** Stop-loss/max-hold exit logic is the #1 P&L destroyer.

### Service Health
- Scan cycles: 168-175ms, processing 12 markets per cycle
- CVaR exposure: $4,378 (was $12.5K, clearing as positions resolve)
- 34 unresolved esports markets in queue (shared backfill processing)
- 7 historical both-sides markets (Valorant, pre-Fix 4, cannot undo)

### Data Integrity
| Check | Result |
|-------|--------|
| Paper trades NULL realized_pnl | 0 ✅ |
| Phantom RESOLUTION events | 0 ✅ |
| New both-sides entries post-S132 | 0 ✅ |
| Fix 5 confidence in event_data | PENDING (no new entries since deploy) |

---

## SIGNAL QUALITY SYSTEM (S127→S131)

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

**Typical unfitted single-model SQ**: ~0.525 → trades at ~52% Kelly

### Computed at 4 sites:
1. Main path (`analyze_opportunity` ~line 1937 on VPS)
2. WS reactive path (`on_price_update` ~line 781)
3. Series path (`_series_analyze` ~line 5505)
4. Series WS path (`_series_on_price_update` ~line 5844)

### BetaCalibrator Status
| Game | Status | Samples | sq_calibration |
|------|--------|---------|----------------|
| CS2 | **Fitted** (n=43) | 43 | 0.70 |
| Dota2 | **Fitted** (n=10) | 10 | 0.70 |
| LoL | Insufficient | ~6/10 | 0.50 |
| Valorant | Insufficient | ~5/10 | 0.50 |
| Others | No data | 0 | 0.50 |

---

## PREDICTION PIPELINE FLOW

```
scan_and_trade → analyze_opportunity
  → _detect_game (word-boundary regex)
  → _get_model_prediction (game-specific ML)
     → _enrich_prediction → returns TUPLE (prob, _enrich_meta)
     → stores _enrich_meta in prediction_cache event_data
  → BetaCalibrator.calibrate() (if fitted)
  → ConformalPredictor
  → side_prob = model_prob (YES) or 1-model_prob (NO)
  → signal_quality = _compute_signal_quality(game, market_id)
  → confidence = side_prob  ← S131 (was side_prob * SQ)
  → phase_mult applied
  → confidence floor gate (0.52 effective)
  → edge gate (0.05)
  → TRADE → _execute_esports_trade()
```

### Sizing Pipeline (in `_execute_esports_trade`)
```
_execute_esports_trade:
  → _sq_sizing = opp.get("_signal_quality", 1.0)        ← S131 NEW
  → conformal sizing (if enabled)
  → BotBankrollManager.get_bet_size(confidence, price)
     → Kelly: kelly_full = (confidence * b - q) / b
     → if confidence <= price: return 0
  → size *= phi_factor * dd_factor * game_kelly_mult * edge_decay_mult * _sq_sizing
  → upset risk scaling (size *= 0.3 if favorite)
  → exposure checks (game $5K, tournament $8K, total $15K)
  → max bet cap $300, min trade floor $10
  → opposing-side guard check ← S132 FIX 4
  → place_order()
  → _entered_market_sides.add() ← S132 FIX 4
  → write-through daily_counters
```

---

## OPPOSING-SIDE GUARD (S132 Fix 4)

### Mechanism
- `__init__`: `self._entered_market_sides: set = set()`
- `scan_and_trade` (first cycle): Restore from `trade_events` ENTRY records
- `_execute_esports_trade`: Check order_gateway + `_entered_market_sides` for opposite side → block
- WS path: Block if ANY position exists (same or opposite side)
- On successful entry: `_entered_market_sides.add((market_id, side))`

### Why
7 Valorant markets had YES+NO entries — guaranteed fee loss. Guard prevents future occurrences.

---

## SCAN WATERFALL (what blocks trades, in order)

1. `no_game` — can't detect game from market question
2. `halted` — Brier halt (threshold=0.30)
3. `exposure_cap` — per-game ($5K) / tournament ($8K) / total ($15K)
4. `observation` — PatchDriftDetector (48h after game patch)
5. `no_prediction` — team name extraction/matching failed OR tournament_winner type
6. `exit_cooldown` — recently exited (300s Redis-persisted)
7. `max_entries` — 5 entries per market per 12h window
8. `low_confidence` — below 0.52 (effective floor, S131)
9. `low_edge` — below 0.05
10. `reentry_rejected` — has position, wrong direction or insufficient edge (0.08)
11. `passed` → goes to `_execute_esports_trade()`

---

## STATE PERSISTENCE

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

## KEY CONFIG (Live VPS .env)

```
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_MAX_BET_USD=300
ESPORTS_MIN_TRADE_USD=10.0
ESPORTS_MAX_DAILY_USD=20000
ESPORTS_MAX_TOTAL_EXPOSURE_USD=15000
ESPORTS_MAX_GAME_EXPOSURE=5000
ESPORTS_MAX_TOURNAMENT_EXPOSURE=8000
ESPORTS_MAX_TEAM_EXPOSURE=2000
ESPORTS_MIN_CONFIDENCE=0.20          # .env overrides code default 0.35; effective floor is 0.52 (S131)
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

## ARCHITECTURE — FILE MAP

### Core Bot Files
| File | Lines | Role |
|------|-------|------|
| `bots/esports_bot.py` | ~5,800 (VPS, post-S132) | Main bot: scan, predict, trade, exit, calibrate, signal quality |
| `bots/esports_live_bot.py` | ~350 | Live in-game trading wrapper (queue maxsize=500) |

### Code Locations in esports_bot.py (VPS line numbers, approximate)
| Lines | What |
|-------|------|
| 47-149 | `BetaCalibrator` class |
| 151-192 | `OnlinePlattCalibrator` class |
| 195-400 | `__init__` — all instance vars (incl `_entered_market_sides`) |
| 420-600 | `start()` — client init, Glicko-2, calibrators, Brier cache seed |
| 618-793 | `on_price_update()` — WS reactive trading + opposing-side guard |
| 842-1287 | `scan_and_trade()` — main loop, waterfall, `_entered_market_sides` restore |
| 1466-1614 | `_check_and_execute_exits()` — stop-loss, max hold |
| ~1655 | Comment: `_resolve_esports_from_clob` DELETED (S132) |
| 1872-2178 | `analyze_opportunity()` — confidence=side_prob (S131) |
| 2182-2372 | `_enrich_prediction()` — returns TUPLE (prob, _enrich_meta) |
| 2374-2646 | `_get_model_prediction()` — all game paths |
| ~3060 | `_execute_esports_trade()` — sizing pipeline + opposing-side guard + SQ sizing |
| ~3473 | `_compute_signal_quality()` — 5-component composite |
| 4005-4255 | `_check_monitoring_thresholds()` — Brier, calibrator fitting |
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
| `esports/models/cot_validator.py` | Chain-of-thought validation (fail-closed S128) |

### Data / API Clients
| File | Role |
|------|------|
| `esports/data/esports_data_collector.py` | PandaScore data collection |
| `esports/data/esports_db.py` | DB helpers |
| `esports/data/pandascore_client.py` | PandaScore API (8 games, 1000 req/hr, 30s cache, circuit breaker) |
| `esports/data/opendota_client.py` | Dota2 (word-boundary match S128) |

### Scripts
| File | Purpose |
|------|---------|
| `scripts/bot_pnl.py` | Canonical P&L: `python scripts/bot_pnl.py EsportsBot 720` |
| `scripts/esports_diag.py` | Diagnostic (positions table has no closed_at/updated_at) |
| `scripts/esports_charts.py` | WR/P&L by game+confidence |

---

## PRIORITY QUEUE FOR SESSION 133

### P0 — Sync VPS → Local Git
S131+S132 changes exist only on VPS. Must `scp` back + commit. See "CRITICAL CODE DIVERGENCE" section above.

### P1 — Disable CoD
16 trades, -$1,151, no ML model. Set `BOT_ENABLED_ESPORTS_COD=false` in .env on VPS.

### P1 — Verify Fix 5 (confidence in event_data)
No new entries since S132 deploy. On next entry, verify:
```sql
SELECT event_data->>'confidence', event_data->>'signal_quality'
FROM trade_events WHERE bot_name='EsportsBot' AND event_type='ENTRY'
ORDER BY event_time DESC LIMIT 3;
```
Both should be non-NULL for post-deploy entries.

### P2 — EXIT P&L Hemorrhage
EXIT losses (-$4,986) are 2.5x worse than RESOLUTION losses (-$1,959). Stop-loss at 15% and max-hold at 96h are the #1 P&L destroyers. Investigate:
- What % of stop-loss exits would have been profitable if held to resolution?
- Is max-hold forcing exits at systematically bad times?

### P2 — LoL 7.7% WR (1/13 resolutions)
Genuinely bad model performance. BUG-24 fix (S128, IsotonicRegression) is deployed but needs more resolved samples to evaluate. Consider:
- Per-game minimum confidence gate
- LoL-specific Kelly reduction
- Disabling LoL until WR improves

### P3 — CS2 Model Retraining
Brier=0.292 (>0.25 threshold), graduation failing (accuracy 0.542 < 0.55). Options:
- Lower graduation threshold for CS2
- Add more features (map pool, recent form)
- Investigate bias direction

### P3 — `no_prediction: 6` per scan
6 markets where team names can't be matched to Glicko-2 data. Mostly parsing failures.

### P4 — WebSocket Trading Disabled
`ws_trading=False` in all scans. WS reactive path not firing. Investigate.

### P5 — S130 Uncommitted Files
6 esports files deployed via SCP in S130 but never committed: dead code removal, CS2 Glicko-2 metadata passthrough, trainer early_stopping_rounds=20, Dota2/Valorant FEATURE_NAMES attribute, series_model detect_momentum_fallacy removal.

---

## DIAGNOSTIC SSH QUERIES

```bash
SSH="ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o ConnectTimeout=10 ubuntu@34.251.224.21"

# Scan health
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'esportsbot_scan_summary' | tail -3"

# Signal quality values
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'esportsbot_signal_quality' | tail -10"

# Sizing kills
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '30 min ago' --no-pager | grep 'sizing_killed' | tail -5"

# Trade attempts + entries
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep 'EsportsBot trade executed' | tail -10"

# Opposing-side blocks (S132 Fix 4)
$SSH "timeout 15 sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep 'esports_opposing_side' | tail -10"

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

# Recent entries with confidence (Fix 5 validation)
$SSH "timeout 10 sudo -u postgres psql -d polymarket -c \"
SELECT event_time, event_data->>'confidence' as conf, event_data->>'signal_quality' as sq, event_data->>'game' as game
FROM trade_events WHERE bot_name='EsportsBot' AND event_type='ENTRY' ORDER BY event_time DESC LIMIT 5;\""

# Both-sides check (should not grow)
$SSH "timeout 10 sudo -u postgres psql -d polymarket -c \"
SELECT market_id, COUNT(DISTINCT side) FROM trade_events
WHERE bot_name IN ('EsportsBot','EsportsLiveBot','EsportsSeriesBot') AND event_type='ENTRY'
GROUP BY market_id HAVING COUNT(DISTINCT side) > 1;\""

# Unresolved esports markets (should decrease)
$SSH "timeout 10 sudo -u postgres psql -d polymarket -c \"
SELECT COUNT(*) FROM traded_markets WHERE bot_names LIKE '%Esports%' AND resolved = FALSE;\""
```

---

## ALL 8 GAMES — PIPELINE STATUS

| Game | ML Model | Pre-game | Live | BetaCalibrator | Total P&L | Notes |
|------|----------|----------|------|----------------|-----------|-------|
| CS2 | CS2EconomyModel | YES | YES | FITTED (n=43) | -$2,896 | Retrain failing graduation |
| LoL | LoLWinModel | YES | YES | ~6/10 | -$1,858 | 7.7% WR, BUG-24 fix active |
| Dota2 | Dota2Model | YES | No | FITTED (n=10) | -$1,404 | WR good but loss/win ratio bad |
| Valorant | ValorantModel | YES | No | ~5/10 | +$4,132 | **ONLY profitable. DO NOT TOUCH.** |
| CoD | None | No | No | 1/10 | -$1,151 | **DISABLE** |
| R6 | None | No | No | 0 | $0 | No markets |
| SC2 | None | No | No | 0 | +$41 | 1 trade |
| RL | None | No | No | 0 | $0 | No markets |

---

## ANTI-CHURN SYSTEM

```
Stop-loss fires (15% drawdown):
  → SELL order executed
  → _recently_exited[market_id] = monotonic_time (300s cooldown)
  → _save_exit_cooldown_to_redis() (survives restart)
  → _prediction_cache[market_id] cleared

Re-entry attempt:
  → _recently_exited: if < 300s ago, reject ("exit_cooldown")
  → _market_entry_times: if >= 5 in last 12h, reject ("max_entries")
  → Both gates applied in scan, WS, AND series paths
```

---

## ORDER EXECUTION — TWO-LAYER PROTECTION

```
Bot calls place_order(side, price, size, confidence, event_data)
  → order_gateway.py
     1. Book walk: snapshot L2 orderbook, compute VWAP
     2. SPREAD GUARD: if spread > 80% → dead market reject
     3. EDGE-AT-VWAP GATE: if confidence <= VWAP → edge eroded reject
     4. Risk manager: CVaR check (max $10K), position limits
     5. If all pass → paper_trading.py executes at VWAP price
     6. Shadow fill recorded in shadow_fills table
```

---

## DEPLOY PROTOCOL

### Single-file hot-patch:
```bash
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem <file> ubuntu@34.251.224.21:/tmp/
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo cp /tmp/<file> /opt/polymarket-ai-v2/<path>/ && sudo systemctl restart polymarket-ai"
```

### Full deploy:
```bash
bash deploy/deploy.sh
```

**Database connection**: `postgresql://polymarket:polymarket_s46@localhost:6432/polymarket`

---

## CRITICAL TRAPS (DO NOT BREAK) — Updated through S132

1. **VPS deploy path**: `/opt/polymarket-ai-v2/` — NOT `/opt/polymarket-ai/current/`
2. **`trade_events` immutability trigger**: Must DISABLE then re-enable for data corrections
3. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. Never "BUY"/"SELL"
4. **`_enrich_prediction()` returns a TUPLE**: `(prob, _enrich_meta)`. All call sites must unpack
5. **`_compute_signal_quality()` reads from `_prediction_cache`**: Defaults ~0.525 on miss (post-S131)
6. **Signal quality is SIZING multiplier** (S131): `confidence = side_prob`, NOT `side_prob * SQ`
7. **PSEUDO_LABEL_ENABLED=false**: DO NOT enable
8. **Paper trading IS production**: Only difference is final order submission
9. **`asyncio.create_task()` forbidden for financial write-throughs** — always `await`
10. **One fix per commit. Preserve every function signature. No while-I'm-in-here refactors.**
11. **15 bots in BOT_REGISTRY. Shared module change = all 15 verified**
12. **`ESPORTS_MIN_CONFIDENCE=0.20` in .env** — effective floor is 0.52 (S131 `max()` gate)
13. **LoL calibrator is IsotonicRegression** (S128) — not CalibratedClassifierCV
14. **CoT validator is fail-CLOSED** (S128) — API failures REJECT trades
15. **Graduation gate** (S128): accuracy>=0.55, brier<0.30, n>=200
16. **Extremization of Glicko-2 was REJECTED** (S125) — BetaCalibrator is the fix
17. **Confluence gate was REMOVED in S119** — do not re-add
18. **`_team_exposure` was REMOVED in S119** — do not re-add
19. **PR-5 (volume filter) DENIED by user** — book walk handles thin markets
20. **CVaR cap is $10,000** (S120)
21. **`trade_events` JSONB column is `event_data`** — NOT `metadata_json`
22. **RESOLUTION event idempotency**: Atomic INSERT...SELECT with WHERE NOT EXISTS (ON CONFLICT broken on partitioned tables)
23. **Python 3.13 scoping**: `from X import Y` inside function makes Y local for ENTIRE function
24. **SSH**: ICMP blocked. Use `--since '1 hour ago'` or `timeout 30` for journalctl
25. **`esports_prediction_log`**: `actual_outcome` is smallint 0/1 NOT text. NO `game_tag` column.
26. **`positions` table**: `bot_id` (not `bot_name`), NO `size_usd`/`closed_at`/`updated_at`
27. **`trade_events` P&L**: Use `realized_pnl` column, NOT `event_data->>'pnl_usd'`
28. **`_resolve_esports_from_clob()` is DELETED** (S132) — esports resolutions flow through shared queue only
29. **`_entered_market_sides` must be restored from DB on first scan cycle** (S132) — not in `start()`
30. **`confidence` in opp dict is RAW side_prob** (S131) — not SQ-dampened
31. **`_signal_quality` in opp dict defaults to 1.0 if missing** — backward-compatible
32. **SQ component defaults** (S131): agreement=0.70 (single model), calibration=0.50 (unfitted), brier default=0.15
33. **Brier cache seeded in `start()`** (S131) — uses `_get_cached_rolling_accuracy()` with ≥10 sample threshold
34. **7 historical both-sides Valorant markets**: Cannot be undone, Fix 4 prevents new ones
35. **DB creds**: `polymarket:polymarket_s46@localhost:6432/polymarket` — NOT `polymarket_user:polymarket_pass`
36. **`paper_trades` has NO `metadata` JSONB column**
37. **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time
38. **Phase 4b EXIT P&L subtraction already implemented** (verified S132 Fix 6, lines 467-472 in resolution_backfill.py)

---

## SESSION HISTORY

| Session | Date | Key Changes |
|---------|------|-------------|
| S89 | Mar 14 | E2-E5 features + 9 audit fixes, migration 053 |
| S125 | Mar 24 | BetaCalibrator min_samples 15→10, game tag restore |
| S127 | Mar 24 | Signal quality system, min_confidence 0.52→0.35 |
| S128 | Mar 25 | 10 audit bug fixes (BUG-24 LoL calibration P0, graduation gate) |
| S130 | Mar 25 | Diagnostic — bot stopped trading due to SQ blocking |
| **S131** | **Mar 25** | **ROOT FIX: SQ→sizing multiplier. Component defaults. Brier seed. 0.52 floor.** |
| **S132** | **Mar 25** | **Data integrity: delete _resolve_esports_from_clob, opposing-side guard, confidence in event_data, 115 paper_trades + 85 phantom events repaired** |

---

## HANDOFF DOCS (for deep dives)

- `AGENT_HANDOFF_ESPORTS_SESSION132_2026_03_25.md` ← CURRENT
- `AGENT_HANDOFF_ESPORTS_SESSION131_2026_03_25.md`
- `PROMPT_ESPORTSBOT_SESSION131.md` (full system prompt with S125-S130 history)
- `AGENT_HANDOFF_ESPORTS_SESSION89_2026_03_14.md` (E2-E5 features)
