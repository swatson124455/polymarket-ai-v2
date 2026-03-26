# EsportsBot Session 108 — Full Carbon-Copy Continuation Prompt
# Paste into a fresh session. DO NOT bleed into MirrorBot or WeatherBot.

---

## SCOPE LOCK
You are working on **EsportsBot ONLY** (includes EsportsLiveBot and EsportsSeriesBot which share code). Do not touch MirrorBot, WeatherBot, or any other bot's files. If a shared module needs changes, justify it explicitly and verify all 14 bots.

---

## READ FIRST (in this order)
1. `CLAUDE.md` — development rules (surgical fixes, zero collateral damage)
2. `AGENT_HANDOFF_ESPORTS_SESSION107_2026_03_19.md` — latest handoff (config alignment, BetaCalibrator fitted, P&L corrections)
3. `AGENT_HANDOFF_ESPORTS_SESSION105_2026_03_18.md` — prior handoff (cross-bot position isolation, bankroll alignment, paper engine quality)
4. `bots/esports_bot.py` — the bot (~5,560 lines)
5. `esports/models/glicko2.py` — Glicko-2 rating system
6. `esports/models/conformal_wrapper.py` — conformal predictor
7. `base_engine/data/daily_counter.py` — exposure persistence
8. `config/settings.py` — all config
9. `tests/unit/test_esports_bot.py` — unit tests

---

## SYSTEM OVERVIEW

**Polymarket AI V2** — Multi-bot automated prediction market trading system.
- **14 active bots** in BOT_REGISTRY. EsportsBot is one of 3 esports bots (EsportsBot, EsportsLiveBot, EsportsSeriesBot).
- **Paper trading mode** (`SIMULATION_MODE=true`). Paper trading IS production — every feature matters identically. The only difference is whether the final order goes to CLOB or logs to paper_trades.
- **VPS**: Ubuntu on AWS Lightsail at `34.251.224.21` (16GB/4vCPU)
- **Python 3.13** — critical scoping rules (see traps)
- **trade_events is P&L authority** — never read paper_trades for P&L
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS** — both must pass

---

## CURRENT STATE (as of S107, 2026-03-19 03:02 UTC)

### P&L
- **+$310.76 realized** (131 entries, 55 exits, 106 resolutions)
- EXIT P&L: +$369.21, RESOLUTION P&L: -$58.45
- Up from -$189.29 at S106 start (S106 NameError fix + S107 P&L corrections + new trades)

### Bot Status
- **Scanning healthy**: 18 markets, 10s intervals, `min_confidence=0.48`, `min_edge=0.05`
- **Waterfall**: `no_prediction=6, low_edge=3, edge_cap=2, low_confidence=4, passed=3, reentry_rejected=3`
- **3 open positions** (skipped_has_position=3)
- **6 new trades** since S106 NameError fix (2026-03-19 02:26-02:46)

### BetaCalibrator — 4/8 FITTED
| Game | Resolved | Status | Parameters |
|------|----------|--------|------------|
| Valorant | 1,543 | **FITTED** | a=1.00, b=1.01, c=0.01 |
| LoL | 281 | **FITTED** | a=0.99, b=1.00, c=0.01 |
| SC2 | 52 | **FITTED** | a=1.01, b=1.00, c=-0.01 |
| Dota2 | 40 | **FITTED** | a=0.99, b=1.00, c=0.01 |
| CS2 | 22/30 | 73% | Accumulating |
| CoD/R6/RL | 0 | No data | — |

All fitted parameters near identity (a≈1, b≈1, c≈0) — raw Glicko-2 is already well-calibrated.

### Learning-Phase Suspensions
| Suspension | Status | Notes |
|-----------|--------|-------|
| Edge cap (0.45→0.35) | **DEACTIVATED** for fitted games | LoL now blocked by 0.35 cap |
| Monitoring halt | Deactivated per game as fitted | — |
| Tournament phase | Deactivated per game as fitted | — |
| Game kelly mult | Deactivated per game as fitted | — |
| Phi sizing floor (0.8→0.5) | Deactivated per game as fitted | — |
| Kelly degradation | **STILL SUSPENDED** | Requires ALL games fitted — CS2 blocks |

### Brier Scores (post-fix, from `_GLICKO2_FIX_DATE = 2026-03-16`)
| Game | N | Brier | Win Rate |
|------|---|-------|----------|
| SC2 | 57 | 0.0195 | 0.0% (predicts losers correctly) |
| Valorant | 1,543 | 0.1390 | 69.7% |
| CS2 | 22 | 0.2051 | 0.0% |
| LoL | 371 | 0.2842 | 62.5% (borderline) |
| Dota2 | 40 | 0.3002 | 77.5% (over threshold but high WR) |

### Config (post-S107 alignment)
```
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 10000}}
ESPORTS_MAX_BET_USD=300          # Enforced by P6 cap in _execute_esports_trade()
ESPORTS_MAX_DAILY_USD=10000
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_MIN_CONFIDENCE=0.48     # Lowered from 0.50 in S106
ESPORTS_MIN_EDGE=0.05
ESPORTS_MAX_EDGE=0.35           # Code raises to 0.45 for unfitted games (only CS2 now)
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_MAX_GAME_EXPOSURE=600   # USD cap per game
ESPORTS_USE_CONFORMAL=true
ESPORTS_RETRAIN_INTERVAL_HOURS=24
ESPORTS_MODEL_MAX_BRIER=0.248
ESPORTS_MIN_VOLUME_USD=0
RISK_MIN_VOL_ESPORTSBOT=0
SIMULATION_MODE=true
```

---

## CALIBRATION ARCHITECTURE (4 Phases)

### Phase 1: BetaCalibrator (batch, per-game)
- **Class**: `BetaCalibrator` at `esports_bot.py` lines 47-148
- **Algorithm**: Kull et al., AISTATS 2017 — `sigmoid(a·ln(p) - b·ln(1-p) + c)`
- **Identity state**: a=1, b=1, c=0 (passthrough when unfitted)
- **Bayesian regularization**: λ=10, penalizes deviation from identity
- **Fitting**: L-BFGS-B with bounds [(0.1,5.0), (0.1,5.0), (-2.0,2.0)]
- **Min samples**: 30 resolved predictions per game
- **Training window**: Starts from `_GLICKO2_FIX_DATE = 2026-03-16`
- **Current**: FITTED for LoL, Valorant, SC2, Dota2. UNFITTED for CS2, CoD, R6, RL.

### Phase 2: OnlinePlattCalibrator (streaming, per-game)
- **Class**: `OnlinePlattCalibrator` at `esports_bot.py` lines 151-193
- **Algorithm**: River `LogisticRegression` with `SGD(lr=0.01)`
- **Applied**: AFTER BetaCalibrator in `analyze_opportunity()` at lines 1601-1603

### Phase 3: ConformalPredictor (batch per-game, sizing not calibration)
- **Class**: `ConformalPredictor` at `esports/models/conformal_wrapper.py`
- **Applied**: In `_execute_esports_trade()` for phi_factor sizing

### Phase 4: ADWIN Drift Detection (streaming, per-game)
- **Library**: River `ADWIN(delta=0.002)` — advisory only, does NOT halt trading

### Calibration Pipeline Flow
```
Raw model_prob (from Glicko-2 or ML model)
  → BetaCalibrator.calibrate(model_prob)        [Phase 1, if fitted]
  → OnlinePlattCalibrator.calibrate(model_prob)  [Phase 2, if fitted]
  → RFLB correction (favorites overbetting guard, lines 1605-1624)
  → edge computation (YES/NO side selection)
  → edge cap check (0.35 normal, 0.45 while unfitted)
  → high uncertainty filter
  → tournament phase mult (1.0 while unfitted)
  → confidence vs min_confidence check
  → confluence gate
  → return opportunity dict
```

---

## PREDICTION PIPELINE

### Full Prediction Path (in `_get_model_prediction()`)
```
Market → detect_game() → _get_model_prediction() → one of:

├─ LoL LIVE (live_data exists + _lol_model.is_trained):
│   → _inject_glicko2_metadata(game_state, game, live_data)
│   │   └─ Uses opponents[i].get("opponent", {}).get("name", "").lower()
│   │   └─ Injects: team_strength_diff, matchup_uncertainty, rd_asymmetry, volatility
│   │   └─ Guards: both teams phi >= 349 → skip
│   → glicko2_est = 0.5 + team_strength_diff
│   → _lol_model.predict_with_glicko2(game_state, glicko2_est)
│
├─ CS2 LIVE (live_data exists + _cs2_model.is_trained):
│   → _inject_glicko2_metadata(game_state, game, live_data)
│   → cs2_model.predict_match(maps_won_a, maps_won_b, best_of, map_probs)
│
├─ Dota2 (ML model + Glicko2 features):
│   → _get_glicko2_prediction(market_data, game, price) for expected_score
│   → _build_glicko2_game_state() for 6 ML features
│   → _dota2_model.predict_with_features(game_state)
│
├─ Valorant (ML model + Glicko2 features):
│   → Same pattern as Dota2
│
└─ ALL GAMES fallback (pre-match, no ML model):
    → _get_glicko2_prediction(market_data, game, price)
    → Bayesian-blended Glicko-2 expected score
    → Prior blend based on max(phi): ≥350→80%, ≥200→50%, ≥100→20%, <100→0%
    → Prior = market_price (S94-P3 fix), not 0.50
```

---

## SIZING PIPELINE

### In `_execute_esports_trade()` (lines ~2770-2900)
```python
# 1. Conformal sizing (Phase 3)
if conformal_fitted:
    conservative_prob → conservative_edge → phi_factor = min(1.0, conservative_edge / edge)
else:
    phi_factor = _get_phi_sizing_factor(opp)  # edge-based proxy

# 2. Base sizing from BotBankrollManager
size = await calculate_bot_position_size(confidence, price, category="esports")

# 3. Drawdown Kelly reduction
dd_factor = _get_drawdown_kelly_factor()

# 4. Apply ALL multipliers
size = size × phi_factor × dd_factor × game_kelly_mult × edge_decay_mult

# 5. P6 max bet cap
_max_bet = float(getattr(settings, "ESPORTS_MAX_BET_USD", 300.0))
_cost = price * size
if _cost > _max_bet:
    size = _max_bet / max(price, 0.01)

# 6. Exposure tracking in USD
_entry_cost = price * size
self._game_exposure[game] += _entry_cost

# 7. Upset risk scaling
# 8. place_order(side="YES" or "NO")
```

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
if self._game_exposure.get(game, 0) + _entry_cost > ESPORTS_MAX_GAME_EXPOSURE ($600):
    # reject — "exposure_cap" waterfall

# Startup restore:
counters = await _restore_daily(self.db, "EsportsBot")
# game_exposure rebuilt from daily_counters table
```

---

## KEY CLASSES & CODE LOCATIONS

### esports_bot.py (~5560 lines)
| Lines | Content |
|-------|---------|
| 47-148 | `BetaCalibrator` class |
| 151-193 | `OnlinePlattCalibrator` class |
| 270-313 | `__init__` calibration additions |
| ~1400-1405 | Exit path: `_game_exposure` decrement in USD |
| 1480-1502 | Resolution feed into streaming calibrators |
| 1595-1603 | Calibration application in `analyze_opportunity()` |
| 1605-1624 | RFLB favorites correction |
| 1664-1670 | Dynamic edge cap (0.45 while unfitted) |
| 1695-1707 | Tournament phase penalty suspension |
| 1810-1940 | `_get_model_prediction()` all paths + event_data |
| 2175-2219 | `_inject_glicko2_metadata()` — uses team name not ID |
| ~2757-2790 | Series S-T path with USD exposure |
| 2778-2786 | Conformal sizing |
| ~2877 | ESPORTS_MAX_BET_USD cap |
| ~2889-2941 | Entry/rollback exposure tracking in USD |
| 2975-2991 | `_get_phi_sizing_factor()` with learning-phase floor |
| 2993-3021 | `_update_streaming_on_resolution()` |
| 3060-3075 | Kelly degradation suspension |
| 3464-3475 | Monitoring halt suspension |
| 3515-3534 | Game kelly mult suspension |
| 3564-3579 | BetaCalibrator batch fitting |
| 3581-3613 | ConformalPredictor batch fitting |
| 4040-4142 | `_get_glicko2_prediction()` — Bayesian-blended |
| 4387-4504 | `_extract_team_ids_from_question()` — 6 regex patterns |
| 4506-4560 | `_build_glicko2_game_state()` — ML feature dict |
| 4665-4729 | `_match_team_name()` — 6-tier fuzzy matching |

### Other Key Files
| File | Purpose |
|------|---------|
| `base_engine/data/daily_counter.py` | Write-through daily counters. **Commits** (S103 fix). |
| `base_engine/execution/paper_trading.py` | Paper engine. Position key: `(bot_name, market_id)` (S105 fix). |
| `base_engine/execution/order_gateway.py` | Liquidity skip for esports bots. Per-bot `realized_pnl_today`. |
| `esports/models/conformal_wrapper.py` | `ConformalPredictor` — logit-space residuals |
| `esports/models/glicko2.py` | `Glicko2Rating`, `expected_score()`, `Glicko2Tracker` |
| `base_engine/risk/bankroll_manager.py` | `BotBankrollManager` — Kelly sizing |
| `base_engine/data/database.py` | DB layer — `insert_trade_event()` with idempotency |
| `base_engine/base_engine.py` | `BaseBot` parent class — `place_order()` |
| `config/settings.py` | All config defaults |
| `main.py` | Bot registry, watchdog. EsportsSeriesBot in `_bot_enabled_map` (S106 fix). |

---

## GLICKO-2 RATING SYSTEM

### Architecture (`esports/models/glicko2.py`)
- **`Glicko2Rating`**: Dataclass with mu (1500), phi (350), sigma (0.06)
- **`expected_score(A, B)`**: P(A beats B) accounting for rating diff AND opponent uncertainty
- **`Glicko2Tracker`**: Manages all team ratings per game. 8 trackers (one per game).

### Bayesian Prior Blending (in `_get_glicko2_prediction()`)
```
max_phi = max(rating_a.phi, rating_b.phi)
phi >= 350 (unrated):    80% market_price + 20% Glicko-2
phi 200-350 (sparse):    50% market_price + 50% Glicko-2
phi 100-200 (developing): 20% market_price + 80% Glicko-2
phi < 100 (mature):       100% Glicko-2
```

### Team Name Matching — 6-Tier System (lines 4665-4729)
1. Exact match → 2. Alias lookup (`_TEAM_ALIASES`) → 3. Substring match →
4. Reverse substring → 5. Word-boundary match (short names) → 6. Difflib fuzzy (0.78)

---

## DATA FLOW & RESOLUTION

```
1. Scan loop (every 10s):
   → analyze_opportunity() → predictions logged to esports_prediction_log

2. Trade execution:
   → _execute_esports_trade() → paper orders → trade_events ENTRY (with event_data)

3. Resolution (every 10 scans ~100s):
   → _backfill_esports_outcomes() + _resolve_esports_from_clob()
   → Updates esports_prediction_log.actual_outcome
   → Feeds streaming calibrators (ADWIN + OnlinePlatt)

4. Monitoring (every 20 scans ~200s):
   → BetaCalibrator.fit_from_db() + ConformalPredictor.fit_from_predictions()
```

### Key Tables
| Table | Purpose |
|-------|---------|
| `trade_events` | P&L authority. Partitioned by month. Immutable trigger. |
| `paper_trades` | Legacy. No `metadata` column. No `resolved_pnl` column. |
| `positions` | Open positions. No `closed_at`/`updated_at`. |
| `esports_prediction_log` | Prediction history. `predicted_prob`, `actual_outcome`, `game`. |
| `glicko2_ratings` | Persisted Glicko-2 ratings per team per game. |
| `traded_markets` | Market registry. `bot_names` is TEXT (use LIKE). |
| `daily_counters` | Per-game exposure persistence. Auto-resets UTC midnight. |
| `system_kv` | Generic key-value store (migration 054). |

---

## SESSION HISTORY (EsportsBot)

| Session | Date | Key Changes |
|---------|------|-------------|
| **S107** | **2026-03-19** | **Config aligned $20K/$300/$10K, BetaCalibrator 4/8 fitted, 3 EXIT P&L corrections (+$12), taker_side deferred** |
| S106 | 2026-03-18 | NameError fix (ALL trades broken since S105), EsportsSeriesBot watchdog fix |
| S105 | 2026-03-18 | Cross-bot position isolation, bankroll alignment, paper engine quality |
| S103 | 2026-03-18 | P4 event_data, P6 max_bet cap, P7 exposure units fix, daily_counter commit |
| S100-S100c | 2026-03-17 | BetaCalibrator greenfield, 4 calibration phases, learning suspensions |
| S99 | 2026-03-16 | max_edge 0.35, capital 2x, EsportsLiveBot scanner fix |
| S97 | 2026-03-16 | _inject_glicko2_metadata() name fix, 9 fixes |
| S94 | 2026-03-15 | Latency 2967ms→11.9ms, Bayesian prior blend |
| S89 | 2026-03-14 | E2-E5 features + 9 audit fixes, migration 053 |
| S88 | 2026-03-14 | Observation mode fix (PatchDriftDetector false trigger) |
| S87 | 2026-03-14 | Resolution dedup (atomic INSERT...SELECT) |

---

## OUTSTANDING ITEMS (EsportsBot-scoped)

| Priority | Item | Status |
|----------|------|--------|
| P2 | CS2 BetaCalibrator: 22/30 resolved — 8 more needed | Accumulating naturally |
| P2 | Kelly degradation still suspended (needs ALL games fitted — CS2 blocks) | Blocked on CS2 |
| P3 | LoL Brier=0.2842 — borderline near 0.30 halt threshold | Monitor |
| P3 | LoL edge_cap blocking 2 markets (edges 0.43, 0.62 > 0.35 cap) | Working as designed |
| P3 | `no_prediction=6` — minor league teams not in PandaScore | Self-healing |
| P3 | EsportsSeriesBot silent — no series markets available | Expected |
| P4 | Dota2 Brier=0.3002 — just over halt threshold (77.5% WR) | Monitor |
| P5 | taker_side dead code — no data source | Deferred |
| P5 | `PAPER_BOOK_WALK_ENABLED` — implemented but disabled | Deferred |
| P5 | CoD/R6/RL — no BetaCalibrator data, too few markets | Low priority |

---

## VPS CONNECTION & DEPLOY

```bash
KEY="$HOME/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# SSH
ssh -i "$KEY" "$VPS"

# SCP deploy
scp -i "$KEY" file1 file2 "$VPS:/tmp/"
ssh -i "$KEY" "$VPS" 'sudo cp /tmp/file1 /opt/polymarket-ai-v2/path/ && sudo chown polymarket:polymarket /opt/polymarket-ai-v2/path/file1'
ssh -i "$KEY" "$VPS" 'sudo systemctl restart polymarket-ai'

# Verify
ssh -i "$KEY" "$VPS" 'journalctl -u polymarket-ai --since "5 min ago" | grep esportsbot_scan_summary | tail -3'
ssh -i "$KEY" "$VPS" 'journalctl -u polymarket-ai --since "10 min ago" | grep -iE "(error|warning|failed)" | grep -i esport | head -20'

# P&L
ssh -i "$KEY" "$VPS" 'cd /opt/polymarket-ai-v2 && python scripts/bot_pnl.py EsportsBot 720'

# BetaCalibrator
ssh -i "$KEY" "$VPS" 'journalctl -u polymarket-ai --since "30 min ago" | grep beta_cal'

# DB queries
ssh -i "$KEY" "$VPS" "sudo -u polymarket psql -d polymarket -c \"YOUR_QUERY\""
```

---

## CRITICAL TRAPS (DO NOT BREAK)

### EsportsBot-Specific
- **BetaCalibrator training window starts 2026-03-16** — `_GLICKO2_FIX_DATE`. Stale pre-fix data excluded.
- **4 games now fitted (LoL, Valorant, SC2, Dota2)** — edge cap dropped to 0.35 for these. CS2 still at 0.45.
- **All learning suspensions check `_beta_calibrators.get(game)._fitted`** — auto-deactivate. Don't remove manually.
- **Kelly degradation checks ALL games** — won't degrade until ALL games fitted (CS2 blocks).
- **BOT_BANKROLL_CONFIG in .env overrides code defaults** — this is the REAL config. Code defaults are fallbacks only.
- **ESPORTS_MAX_BET_USD in .env enforced by P6 cap in `_execute_esports_trade()`** — separate from BotBankrollManager. Both apply.
- **`_tournament_phase` must be defined BEFORE the if/else** — Python 3.13 scoping.
- **OnlinePlattCalibrator requires `river` package** — gracefully degrades if not installed.
- **Conformal sizing handled by BotBankrollManager** — do NOT add conformal override.
- **`_inject_glicko2_metadata()` uses `.get("name", "").lower()`** — S97 fix. Do NOT change to numeric ID.
- **PatchDriftDetector**: `_patch_timestamps` only set when `old is not None` (S88 fix).
- **`_game_exposure` tracks USD, not shares** — S103 fix.
- **`daily_counter.py` now commits** — S103 fix. Do NOT remove `await sess.commit()`.
- **`_resolve_esports_from_clob()` processes ALL unresolved** — no LIMIT (S104 fix).
- **BetaCalibrator parameters near identity** — all 4 fitted games show a≈1, b≈1, c≈0. Major calibration shifts unlikely.

### System-Wide (From CLAUDE.md — MUST follow)
- **trade_events is P&L authority** — never read paper_trades for P&L
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL.
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS** — both must pass
- **`risk_manager.calculate_position_size()` is DEPRECATED** — BotBankrollManager used
- **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **asyncpg DATE**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
- **Python 3.13 scoping**: `from X import Y` inside function makes Y local for ENTIRE function.
- **trade_events immutability trigger**: `trg_trade_events_immutable` prevents DELETE/UPDATE. Must DISABLE/ENABLE for corrections.
- **RESOLUTION event idempotency**: `ON CONFLICT` broken on partitioned tables. Uses atomic INSERT...SELECT.
- **Paper engine positions key**: `(bot_name, market_id)` tuple — NEVER `market_id` alone (S105 fix).
- **`realized_pnl_today`**: Now `Dict[str, float]` not `float`. Access via `.get(bot_name, 0.0)` (S105 fix).
- **Partial exit fee proration**: `prorated_entry_fee = entry_fee * (exit_size / pos_size)` (S105 fix).
- **positions table**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`.
- **`prediction_log`**: NO `rejection_reason` column. Use `trade_executed` (bool).
- **`traded_markets.bot_names`**: TEXT column (not array), use `LIKE '%BotName%'`.
- **BOT_REGISTRY=14 bots** — shared module change requires all 14 verified.
- **Paper_trades has NO `metadata` JSONB column**.
- **Resolution backfill excludes SELL trades**.
- **CLOB API + httpx**: Works with full 66-char condition_ids. Returns 404 for numeric market_ids.
- **`system_kv` table**: Generic key-value store (migration 054).
- **Alpha decay requires `scan_start_mono` in event_data**: Only WeatherBot passes it.

### P&L Calculation Rules (MANDATORY)
- **NEVER invert formulas for NO positions** — prices are token-specific
- `cost = entry_price * size` (ALL sides)
- `unrealized_pnl = (current_price - entry_price) * size` (ALL sides)
- Canonical script: `python scripts/bot_pnl.py EsportsBot 720`
- Data source: `trade_events` (realized), `positions.unrealized_pnl` (mark-to-market)

---

## CROSS-BOT FEATURES TO CONSIDER (assessment only, no implementation without approval)

1. **Price bucket dampeners** (MirrorBot) — insufficient data for EsportsBot (131 trades)
2. **Per-market entry cap** (MirrorBot caps 2/market) — EsportsBot has no cap, could stack
3. **Fill quality logging** (WeatherBot) — EsportsBot doesn't log slippage_bps/fill_prob/fill_frac
4. **Alpha decay** (WeatherBot) — may help penalize stale pre-match predictions
5. **Exposure decrement on exit** — already fixed in S103

---

## VERIFICATION AFTER ANY CHANGES
1. `pytest tests/unit/test_esports_bot.py` — all tests pass
2. `pytest` — full suite, all 1623+ pass
3. List every file modified
4. One fix per commit
5. Write change log per CLAUDE.md format
6. Verify on VPS after deploy:
   - `journalctl -u polymarket-ai --since "5 min ago" | grep esportsbot_scan_summary | tail -3`
   - `journalctl -u polymarket-ai --since "10 min ago" | grep -iE "(error|warning|failed)" | grep -i esport | head -20`
