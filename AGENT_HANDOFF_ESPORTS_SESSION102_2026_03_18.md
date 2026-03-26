# COMPLETE SYSTEM HANDOFF — EsportsBot Session 102
# Carbon Copy for Seamless Agent Continuation
# Date: 2026-03-18 | Operator: Sam Watson | Scope: EsportsBot ONLY

---

## TABLE OF CONTENTS
1. [System Overview](#system-overview)
2. [Current State & VPS Status](#current-state)
3. [Calibration Architecture (4 Phases)](#calibration-architecture)
4. [Prediction Pipeline](#prediction-pipeline)
5. [Sizing Pipeline](#sizing-pipeline)
6. [Learning-Phase Suspensions](#learning-phase-suspensions)
7. [Key Classes & Code Locations](#key-classes)
8. [Files Modified in S100 Sessions](#files-modified)
9. [Outstanding Bugs & Next Steps](#outstanding-bugs)
10. [Critical Traps](#critical-traps)
11. [VPS Config & Deploy](#vps-config)
12. [P&L Status](#pnl-status)
13. [Session History](#session-history)
14. [Glicko-2 Rating System](#glicko2)
15. [Team Name Matching](#team-matching)
16. [Data Flow & Resolution Feed](#data-flow)
17. [Monitoring & Safety Systems](#monitoring)
18. [CLAUDE.md Rules (Must Follow)](#claude-md)

---

## 1. SYSTEM OVERVIEW <a name="system-overview"></a>

**Polymarket AI V2** — Multi-bot automated prediction market trading system.
- **14 active bots** in BOT_REGISTRY. EsportsBot is one of 3 esports bots (EsportsBot, EsportsLiveBot, EsportsSeriesBot).
- **Paper trading mode** (`SIMULATION_MODE=true`). Paper trading IS production — every feature matters identically. The only difference is whether the final order goes to CLOB or logs to paper_trades.
- **VPS**: Ubuntu on AWS Lightsail at `34.251.224.21` (16GB/4vCPU)
- **Python 3.13** — critical scoping rules (see traps)
- **trade_events is P&L authority** — never read paper_trades for P&L
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS** — both must pass

### Bot Status (as of Session 102)
| Bot | Status | P&L | Notes |
|-----|--------|-----|-------|
| MirrorBot | Active (RTDS live) | +$18,469 realized | Fantasy fills (100% fill rate). Kelly=0.25 |
| WeatherBot | Active | +$2,881 realized | 932 closed, 62% WR, 0 open. Alpha decay ON |
| EsportsBot | Active | -$189.29 realized | 74 trades, 48.6% WR. ~7 open positions |
| EsportsLiveBot | Active | — | Shares EsportsBot code |
| EsportsSeriesBot | Active | — | Shares EsportsBot code |
| 9 others | Disabled | — | MomentumBot DELETED |

---

## 2. CURRENT STATE & VPS STATUS <a name="current-state"></a>

### Git HEAD
```
4054b6b fix(esports): S100c — 5 learning-phase trade blockers removed
```
Deploy: `20260317_202205` (latest, verified healthy)

### Commits This Session Chain (S100-S100c)
```
4a819e1 feat(esports): S100 — BetaCalibrator greenfield + 4 root-cause fixes
76aec57 fix(esports): S100 — suspend monitoring halt when BetaCalibrator unfitted
3f26ba4 feat(esports): S100b — immediate fixes + calibration phases 2-4
4054b6b fix(esports): S100c — 5 learning-phase trade blockers removed
```

### Latest Scan Summary (2026-03-18 00:40 UTC)
```
markets=28, no_prediction=6, low_edge=3, edge_cap=2, low_confidence=10,
passed=7, opportunities=0, trades=0, reentry_rejected=7,
skipped_has_position=8, halted_games=None, ws_trading=False,
min_confidence=0.5, min_edge=0.05, live_matches=2
markets_by_game={'lol': 15, 'cs2': 9, 'sc2': 1, 'cod': 2, 'valorant': 1}
```

### Why trades=0
All 7 markets that pass the quality waterfall already have existing positions → `reentry_rejected=7`. No NEW markets without positions are available. When fresh esports markets appear (new matches/tournaments), the bot will trade.

### Confirmed Fixes Active
- No `Order blocked: liquidity` (liquidity skip working)
- No `prediction logging failed` (`_tournament_phase` scoping fixed)
- No `kelly_degraded` (kelly degradation suspended)
- No `game_kelly_mult` penalties (game mult suspended)
- `halted_games=None` (no games halted — Valorant unblocked)
- `min_confidence=0.5` (correct)

---

## 3. CALIBRATION ARCHITECTURE (4 Phases) <a name="calibration-architecture"></a>

**Replaced**: Old 3-stage sequential pipeline (HorizonBias → EGM → Isotonic) — deleted in S100.
**New**: Single-stage BetaCalibrator + streaming supplements.

### Phase 1: BetaCalibrator (batch, per-game)
- **Class**: `BetaCalibrator` at `esports_bot.py` lines 47-148
- **Algorithm**: Kull et al., AISTATS 2017 — `sigmoid(a·ln(p) - b·ln(1-p) + c)`
- **Identity state**: a=1, b=1, c=0 (passthrough when unfitted)
- **Bayesian regularization**: λ=10, penalizes deviation from identity: `λ(a-1)² + λ(b-1)² + λc²`
- **Fitting**: L-BFGS-B with bounds [(0.1,5.0), (0.1,5.0), (-2.0,2.0)]
- **Min samples**: 30 resolved predictions per game
- **Data source**: `esports_prediction_log WHERE actual_outcome IS NOT NULL`
- **Training window**: Starts from `_GLICKO2_FIX_DATE = 2026-03-16` to exclude stale pre-fix data
- **Applied**: FIRST in `analyze_opportunity()` at lines 1595-1599
- **Current state**: UNFITTED for all 8 games (insufficient post-fix data)
- **8 instances**: one per game (lol, cs2, dota2, valorant, cod, r6, sc2, rl)
- **Fitting location**: `_check_monitoring_thresholds()` lines 3564-3579

### Phase 2: OnlinePlattCalibrator (streaming, per-game)
- **Class**: `OnlinePlattCalibrator` at `esports_bot.py` lines 151-193
- **Algorithm**: River `LogisticRegression` with `SGD(lr=0.01)`
- **Update**: Fed on each resolved prediction via `_update_streaming_on_resolution()`
- **Applied**: AFTER BetaCalibrator in `analyze_opportunity()` at lines 1601-1603
- **Identity state**: Returns p unchanged when <30 samples
- **Purpose**: Fresher signal than batch Beta — supplements between batch refits
- **8 instances**: one per game, initialized in `__init__` lines 311-313

### Phase 3: ConformalPredictor (batch per-game, used at SIZING not calibration)
- **Class**: `ConformalPredictor` at `esports/models/conformal_wrapper.py`
- **Applied**: In `_execute_esports_trade()` lines 2778-2786 for phi_factor sizing
- **Algorithm**: Logit-space residuals → `conservative_prob()` returns p_low (YES bets) or p_high (NO bets)
- **Purpose**: Kelly sizing uses conservative edge, preventing overbetting on uncertain predictions
- **Fitted in**: `_check_monitoring_thresholds()` lines 3581-3613
- **Min samples**: 30 resolved predictions per game

### Phase 4: ADWIN Drift Detection (streaming, per-game)
- **Library**: River `ADWIN(delta=0.002)`
- **Fed**: `(predicted - actual)²` Brier contributions per resolved prediction
- **Purpose**: Advisory — logs warning `esportsbot_adwin_drift` on drift detection
- **Does NOT halt trading** — informational only
- **Fed via**: `_update_streaming_on_resolution()` lines 2993-3021
- **Lazy-initialized**: Created on first call per game

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

### Data Flow: How Calibrators Get Fed
```
Market resolves
  → trade_events RESOLUTION row created
  → _backfill_esports_outcomes() runs every 10 scans (~100s)
    → Queries RESOLUTION events from last 7 days
    → Calls resolve_predictions() → sets actual_outcome in esports_prediction_log
    → Queries recently resolved predictions (15-min window)
    → For each: _update_streaming_on_resolution(game, predicted, actual)
      → ADWIN.update(brier_contribution)
      → OnlinePlattCalibrator.update(predicted, actual)

_check_monitoring_thresholds() runs every 20 scans (~200s)
  → BetaCalibrator.fit_from_db(game, days=min(days_since_fix, 90))
  → ConformalPredictor.fit_from_predictions(preds, outcomes) per game
  → Both query esports_prediction_log WHERE actual_outcome IS NOT NULL
```

---

## 4. PREDICTION PIPELINE <a name="prediction-pipeline"></a>

### Full Prediction Path (in `_get_model_prediction()`)
```
Market → detect_game() → _get_model_prediction() → one of:

├─ LoL LIVE (live_data exists + _lol_model.is_trained):
│   → _inject_glicko2_metadata(game_state, game, live_data)
│   │   └─ Uses opponents[i].get("opponent", {}).get("name", "").lower()
│   │      to look up Glicko2 ratings (S97 FIX — was numeric ID)
│   │   └─ Injects: team_strength_diff, matchup_uncertainty, rd_asymmetry, volatility
│   │   └─ Guards: both teams phi >= 349 → skip (returns without injecting)
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
    → Returns Bayesian-blended Glicko-2 expected score
    → Prior blend based on max(phi): ≥350→80%, ≥200→50%, ≥100→20%, <100→0%
    → Prior = market_price (S94-P3 fix), not 0.50
    → Roster stability adjustment
```

### _get_glicko2_prediction() (lines 4040-4142)
1. Get Glicko2 tracker for game
2. Extract team names via `_extract_team_ids_from_question()` (6 regex patterns)
3. Match to known teams via `_match_team_name()` (6-tier fuzzy matching)
4. Get raw `expected_score(team_a, team_b)` from tracker
5. Bayesian prior blend based on phi uncertainty
6. Roster stability adjustment
7. Return blended probability

### _extract_team_ids_from_question() (lines 4387-4504)
- **S100b fix**: Strips game prefixes ("lol:", "cs2:", etc.) and format suffixes ("- game 1 winner", "(bo3)") BEFORE running regex patterns
- 6 patterns: "vs", "beat/defeat", "win against", "to win", "or", dash-separated
- Returns (team_a_id, team_b_id, clean_name_a, clean_name_b)

### _match_team_name() (lines 4665-4729)
6-tier matching: exact → alias → substring (longest first) → reverse substring → word-boundary (short names) → difflib fuzzy (0.78 threshold)

---

## 5. SIZING PIPELINE <a name="sizing-pipeline"></a>

### In `_execute_esports_trade()` (lines ~2770-2860)
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

# 5. Upset risk scaling
# 6. place_order(side="YES" or "NO")
```

### _get_phi_sizing_factor() (lines 2975-2991)
```
During learning (BetaCalibrator unfitted): floor = 0.8
Normal: floor = 0.5

edge ≥ 0.15 && confidence ≥ 0.65 → 1.0
edge ≥ 0.10 && confidence ≥ 0.58 → 0.8
edge ≥ 0.06                       → max(0.7, floor)
else                               → floor
```

### BotBankrollManager (NOT in esports_bot.py — shared module)
- **Capital**: 10000 (code default), **Kelly**: 0.25
- **Max bet**: $200 (code) vs $100 (.env) — mismatch, verify which is effective
- **Conformal dampening**: Handled via width-based approach (S91 fix). Do NOT add conformal override in `_execute_esports_trade()`.

---

## 6. LEARNING-PHASE SUSPENSIONS <a name="learning-phase-suspensions"></a>

All suspensions check `_beta_calibrators.get(game)._fitted` and auto-deactivate when BetaCalibrator fits (30+ resolved predictions per game).

| Suspension | Normal Behavior | During Learning | Re-engages When | Code Location |
|-----------|----------------|-----------------|-----------------|---------------|
| Monitoring halt | Brier>0.30 → halt game | Don't halt | BetaCalibrator fits | Lines 3464-3475 |
| Tournament phase penalty | confidence *= phase_mult (0.90-1.15) | phase_mult = 1.0 | BetaCalibrator fits | Lines 1695-1707 |
| Edge cap | 0.35 max | 0.45 max | BetaCalibrator fits | Lines 1664-1670 |
| Kelly degradation | Stale Brier>0.28 → kelly capped 0.20 | No cap | ALL BetaCalibrators fitted | Lines 3060-3075 |
| Game kelly mult | Per-game 0.5x for bad Brier | 1.0x (no penalty) | Game's BetaCalibrator fits | Lines 3515-3534 |
| Phi sizing floor | 0.5 minimum | 0.8 minimum | Game's BetaCalibrator fits | Lines 2975-2991 |

### Self-Healing Flow
```
Suspensions ON → trades happen → predictions generated → markets resolve
→ esports_prediction_log populated → BetaCalibrator fits (30+ samples)
→ suspensions auto-deactivate → system self-regulates with clean data
```

---

## 7. KEY CLASSES & CODE LOCATIONS <a name="key-classes"></a>

### esports_bot.py (5357 lines)
| Lines | Content |
|-------|---------|
| 47-148 | `BetaCalibrator` class |
| 151-193 | `OnlinePlattCalibrator` class |
| 270-313 | `__init__` calibration additions |
| 1480-1502 | Resolution feed into streaming calibrators |
| 1595-1603 | Calibration application in `analyze_opportunity()` |
| 1605-1624 | RFLB favorites correction |
| 1664-1670 | Dynamic edge cap (0.45 while unfitted) |
| 1695-1707 | Tournament phase penalty suspension + `_tournament_phase` scoping fix |
| 1810-1865 | `_get_model_prediction()` — LoL/CS2 ML model paths |
| 1866-1940 | `_get_model_prediction()` — Dota2/Valorant/fallback paths |
| 2175-2219 | `_inject_glicko2_metadata()` — S97 FIX uses team name, not numeric ID |
| 2778-2786 | Conformal sizing in `_execute_esports_trade()` |
| 2975-2991 | `_get_phi_sizing_factor()` with learning-phase floor |
| 2993-3021 | `_update_streaming_on_resolution()` — ADWIN + OnlinePlatt feeds |
| 3060-3075 | Kelly degradation suspension |
| 3464-3475 | Monitoring halt suspension |
| 3515-3534 | Game kelly mult suspension |
| 3564-3579 | BetaCalibrator batch fitting |
| 3581-3613 | ConformalPredictor batch fitting |
| 4040-4142 | `_get_glicko2_prediction()` — Bayesian-blended Glicko2 |
| 4387-4504 | `_extract_team_ids_from_question()` — 6 regex patterns |
| 4506-4560 | `_build_glicko2_game_state()` — ML feature dict |
| 4665-4729 | `_match_team_name()` — 6-tier fuzzy matching |

### Other Key Files
| File | Purpose |
|------|---------|
| `base_engine/execution/order_gateway.py` | Liquidity skip for esports bots (line ~543) |
| `esports/models/conformal_wrapper.py` | `ConformalPredictor` class — logit-space residuals |
| `esports/models/glicko2.py` | `Glicko2Rating`, `expected_score()`, `Glicko2Tracker` |
| `base_engine/risk/bankroll_manager.py` | `BotBankrollManager` — Kelly sizing |
| `base_engine/risk/liquidity_guardian.py` | `LiquidityGuardian` — orderbook slippage check |
| `base_engine/data/database.py` | DB layer — `insert_trade_event()` with idempotency |
| `base_engine/base_engine.py` | `BaseBot` parent class — `place_order()` |

---

## 8. FILES MODIFIED IN S100 SESSIONS <a name="files-modified"></a>

| File | Commits | Changes |
|------|---------|---------|
| `bots/esports_bot.py` | 4a819e1, 76aec57, 3f26ba4, 4054b6b | BetaCalibrator, OnlinePlatt, all calibration phases, learning suspensions, team name extraction, _tournament_phase fix |
| `base_engine/execution/order_gateway.py` | 4054b6b | Liquidity guardian skip for EsportsBot/EsportsLiveBot/EsportsSeriesBot |

### Blast Radius
- `order_gateway.py`: Only added early `return None` for esports bots by bot_name check. No behavior change for MirrorBot/WeatherBot/others.
- `esports_bot.py`: EsportsBot-only. No shared module impact.

---

## 9. OUTSTANDING BUGS & NEXT STEPS <a name="outstanding-bugs"></a>

### CORRECTED: S96 P0 is ALREADY FIXED
The S96/S100 handoff claims `_inject_glicko2_metadata()` uses numeric PandaScore IDs — **this is WRONG**. The S97 fix is IN the code at line 2195: `.get("name", "").lower()`. The LoL model_prob ≈ 0.50 is because current LoL matchups have closely-rated teams (genuine ~50/50), NOT because of a key mismatch. Confirmed by code inspection 2026-03-18.

### P1: `no_prediction=6` per scan
6 markets can't match team names to Glicko data. Mostly SC2/minor teams not in PandaScore. Team name extraction was improved (prefix/suffix stripping in S100b), reducing from 7 to 5-6. Could add more team aliases or improve fuzzy matching.

### P2: `low_confidence=10` per scan — structural issue
10/28 markets fail confidence gate. Many LoL markets produce model_prob ≈ 0.5076 (closely-rated teams) → confidence on NO side = 0.4924 < 0.50 threshold. Options:
- Lower `ESPORTS_MIN_CONFIDENCE` from 0.50 to 0.48 (trade more, accept less certain predictions)
- Wait for BetaCalibrator to fit and potentially shift probabilities
- This is working as designed — the bot correctly identifies uncertain matchups

### P3: `edge_cap=2` per scan
2 markets with edge > 0.45 (learning-phase cap). These are genuine large edges on heavy favorites. The 0.45 cap during learning is intentional safety. When BetaCalibrator fits, cap returns to 0.35.

### P4: `unknown` game trades with -$276 P&L
11 historical trades classified as `unknown` game with 27.3% WR. These are markets where `detect_game()` failed. They bypass all game-specific calibration. Investigate what markets generated these.

### P5: WS trading always False
`ws_trading=False` every scan. WS-primary mode from S94 cannot engage for esports markets. Scan-based fallback trading works fine.

### P6: .env vs code config mismatch
`.env` has `ESPORTS_MAX_BET_USD=100`, `ESPORTS_MAX_DAILY_USD=500`. BotBankrollManager may use code defaults of `max_bet_usd=200`, `max_daily_usd=1000`. Verify which values are effective.

### NEXT SESSION PRIORITIES (in order)
1. **Monitor calibration data** — Check `esports_prediction_log` post-fix predictions accumulating:
   ```sql
   SELECT game, COUNT(*) as total, COUNT(actual_outcome) as resolved
   FROM esports_prediction_log WHERE created_at > '2026-03-16' GROUP BY game;
   ```
2. **Verify trades on fresh markets** — Current `trades=0` is because all passing markets have positions. When new markets appear, confirm full pipeline executes.
3. **Investigate `unknown` game trades** — Query `trade_events WHERE bot_name='EsportsBot'` and cross-reference with markets.
4. **Consider lowering MIN_CONFIDENCE** — If 10/28 markets consistently blocked by low_confidence, evaluate 0.48 threshold.
5. **Future phases** (not yet implemented):
   - Phase 5: Multi-Domain Temperature Scaling (cross-game signal sharing)
   - Phase 6: Brier → log-loss migration

---

## 10. CRITICAL TRAPS <a name="critical-traps"></a>

### EsportsBot-Specific
- **BetaCalibrator training window starts 2026-03-16** — `_GLICKO2_FIX_DATE`. Stale pre-fix data excluded.
- **All learning suspensions check `_beta_calibrators.get(game)._fitted`** — they auto-deactivate. Don't remove manually.
- **Kelly degradation checks ALL games** — `any(cal and not cal._fitted for cal in self._beta_calibrators.values())`. Won't degrade until ALL games fitted.
- **Liquidity guardian skip in `order_gateway.py`** — shared module. Only affects EsportsBot/EsportsLiveBot/EsportsSeriesBot by bot_name check.
- **`_tournament_phase` must be defined BEFORE the if/else** — Python 3.13 scoping. Both branches use it downstream.
- **OnlinePlattCalibrator requires `river` package** — gracefully degrades if not installed.
- **ConformalPredictor requires `esports.models.conformal_wrapper`** — imported lazily in fitting code.
- **ADWIN requires `river.drift`** — imported lazily, caught with `except ImportError`.
- **Conformal sizing handled by BotBankrollManager** — do NOT add conformal override in `_execute_esports_trade()`.
- **`_inject_glicko2_metadata()` S97 fix is IN the code** — uses `.get("name", "").lower()`. Do NOT change to numeric ID.
- **PatchDriftDetector**: `_patch_timestamps` must ONLY be set on genuine patch changes (`old is not None`). Setting on first check falsely triggers 48h observation mode on every restart.

### System-Wide (From CLAUDE.md — MUST follow)
- **trade_events is P&L authority** — never read paper_trades for P&L
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL.
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS** — both must pass
- **`risk_manager.calculate_position_size()` is DEPRECATED** — BotBankrollManager used
- **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **asyncpg DATE**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
- **Python 3.13 scoping**: `from X import Y` inside function makes Y local for ENTIRE function. Any use before import → `UnboundLocalError`.
- **trade_events immutability trigger**: `trg_trade_events_immutable` prevents DELETE/UPDATE. Must DISABLE then re-enable for cleanup.
- **RESOLUTION event idempotency**: `ON CONFLICT` broken on partitioned tables. `insert_trade_event()` uses atomic INSERT...SELECT with WHERE NOT EXISTS.
- **positions table**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`.
- **prediction_log**: NO `rejection_reason` column. Use `trade_executed` (bool).
- **`system_kv` table**: Generic key-value store (migration 054). Canary stage persistence.
- **`traded_markets.bot_names`**: TEXT column (not array), use `LIKE '%BotName%'`.
- **BOT_REGISTRY=14 bots** — shared module change requires all 14 verified.
- **Paper_trades has NO `metadata` JSONB column** — never assume metadata available.
- **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time.

---

## 11. VPS CONFIG & DEPLOY <a name="vps-config"></a>

### VPS Connection
```
Host: ubuntu@34.251.224.21
SSH key: ~/.ssh/LightsailDefaultKey-eu-west-1.pem
Code: /opt/polymarket-ai-v2 (symlink to latest release)
Shared: /opt/pa2-shared/{data,saved_models,venv}
.env: /opt/pa2-shared/.env
```

### EsportsBot Config (from VPS .env)
```bash
SIMULATION_MODE=true                  # Paper trading
BOT_ENABLED_ESPORTS=true
BOT_ENABLED_ESPORTS_LIVE=true
BOT_ENABLED_ESPORTS_SERIES=true
ESPORTS_TOTAL_CAPITAL=5000
ESPORTS_MAX_BET_USD=100
ESPORTS_MAX_DAILY_USD=500
ESPORTS_MIN_EDGE=0.05
ESPORTS_MIN_CONFIDENCE=0.50
ESPORTS_MAX_EDGE=0.35                # Code raises to 0.45 while BetaCalibrator unfitted
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_RETRAIN_INTERVAL_HOURS=24
ESPORTS_MODEL_MAX_BRIER=0.248
ESPORTS_USE_CONFORMAL=true
ESPORTS_MIN_VOLUME_USD=0
RISK_MIN_VOL_ESPORTSBOT=0
```

### BotBankrollManager (code defaults)
```
capital=10000, kelly_fraction=0.25, max_bet_usd=200, max_daily_usd=1000
```
Note: .env `ESPORTS_MAX_BET_USD=100` may or may not override code default of 200. Verify.

### Deploy Process
```bash
# From local Windows machine:
cd C:\lockes-picks\polymarket-ai-v2
git add bots/esports_bot.py [other files]
git commit -m "feat(esports): S1XX — description"
bash deploy/deploy.sh

# Deploy does: tar → upload → extract → symlink → restart → 90s health check → auto-rollback on failure

# Verify:
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
journalctl -u polymarket-ai --since "2 min ago" | grep esportsbot_scan_summary | tail -3

# Check errors:
journalctl -u polymarket-ai --since "5 min ago" | grep -iE "(error|warning|failed)" | grep -i esport | head -20
```

---

## 12. P&L STATUS <a name="pnl-status"></a>

### EsportsBot P&L Breakdown (as of S100c deploy)
```
Total: -$189.29 across 74 trades (48.6% win rate)
CS2:      48 trades, 52.1% WR, +$96.01
Dota2:    8 trades, 75.0% WR, +$199.79
CoD:      1 trade, 100% WR, +$4.24
LoL:      1 trade, 0% WR, -$4.15
Valorant: 5 trades, 20% WR, -$208.79
Unknown:  11 trades, 27.3% WR, -$276.39
```

### P&L Calculation Rules (MANDATORY)
- **NEVER invert formulas for NO positions** — prices are token-specific
- `cost = entry_price * size` (ALL sides)
- `unrealized_pnl = (current_price - entry_price) * size` (ALL sides)
- Canonical script: `python scripts/bot_pnl.py EsportsBot 720`
- Data source: `trade_events` (realized), `positions.unrealized_pnl` (mark-to-market)

---

## 13. SESSION HISTORY <a name="session-history"></a>

### Recent EsportsBot Sessions
| Session | Date | Key Changes |
|---------|------|-------------|
| S100c | 2026-03-17 | 5 learning-phase trade blockers removed: liquidity skip, kelly suspension, phi floor, tournament phase fix |
| S100b | 2026-03-17 | Calibration Phases 2-4: OnlinePlatt, Conformal, ADWIN. Team name extraction. Edge cap 0.45. |
| S100 | 2026-03-17 | BetaCalibrator greenfield. Replaced 3-stage pipeline. Monitoring halt suspension. |
| S99 | 2026-03-16 | max_edge 0.35, capital 2x, EsportsLiveBot scanner fix |
| S98 | 2026-03-16 | Retrain thrash fix, partial fill, diagnostic logging, sigma |
| S97 | 2026-03-16 | **_inject_glicko2_metadata() key fix** (name→ID), pipeline audit, 9 fixes |
| S96 | 2026-03-16 | Sizing root cause, Glicko2 crisis identification |
| S94 | 2026-03-15 | Latency 2967ms→11.9ms, Bayesian prior blend |
| S89 | 2026-03-14 | E2-E5 features + 9 audit fixes, migration 053 |
| S88 | 2026-03-14 | Observation mode fix (PatchDriftDetector false trigger) |
| S87 | 2026-03-14 | Resolution dedup (atomic INSERT...SELECT) |

### Handoff Documents (latest per bot)
- **EsportsBot S100**: `AGENT_HANDOFF_ESPORTS_SESSION100_2026_03_17.md` (has stale P0 claim)
- **MirrorBot S99**: `memory/AGENT_HANDOFF_MIRRORBOT_SESSION99_2026_03_17.md`
- **WeatherBot S100**: `AGENT_HANDOFF_WEATHERBOT_SESSION100_2026_03_17.md`
- **System-wide S85**: `AGENT_HANDOFF_SESSION85_DATA_OVERHAUL_2026_03_14.md`

---

## 14. GLICKO-2 RATING SYSTEM <a name="glicko2"></a>

### Architecture (`esports/models/glicko2.py`)
- **`Glicko2Rating`**: Dataclass with mu (1500), phi (350), sigma (0.06)
- **`expected_score(A, B)`**: P(A beats B) accounting for rating diff AND opponent uncertainty
- **`update_rating(player, opponents, outcomes)`**: 5-step Glicko-2 algorithm
- **`Glicko2Tracker`**: Manages all team ratings per game

### Per-Game Trackers
```python
self._glicko2_trackers: Dict[str, Glicko2Tracker]
# One per game: lol, cs2, dota2, valorant, cod, r6, sc2, rl
```

### Rating Persistence
- **DB table**: `glicko2_ratings` (game, team_id, mu, phi, sigma, match_count)
- **Keys**: Lowercased team names (e.g., "bilibili gaming"), NOT numeric IDs
- **Save**: `_save_glicko2_ratings()` persists after match processing
- **Load**: Restored from DB on startup via `_check_monitoring_thresholds()`

### Bayesian Prior Blending (in `_get_glicko2_prediction()`)
```
max_phi = max(rating_a.phi, rating_b.phi)
phi >= 350 (unrated):    80% market_price + 20% Glicko-2
phi 200-350 (sparse):    50% market_price + 50% Glicko-2
phi 100-200 (developing): 20% market_price + 80% Glicko-2
phi < 100 (mature):       100% Glicko-2
```

---

## 15. TEAM NAME MATCHING <a name="team-matching"></a>

### `_team_name_to_id` Dictionary
- Maps lowercased team names → same lowercased team names (identity mapping)
- Populated from: PandaScore API, glicko2_ratings DB restore, match processing
- Used by `_match_team_name()` for fuzzy matching

### `_match_team_name()` 6-Tier System (lines 4665-4729)
1. **Exact match**: Direct dict lookup
2. **Alias lookup**: `_TEAM_ALIASES` dict (e.g., "jdg" → "jd gaming")
3. **Substring match**: Longest known name first (prevents short-name collision)
4. **Reverse substring**: Market name contains known name
5. **Word-boundary match**: Short names (2-3 chars) — "t1", "g2", "og"
6. **Difflib fuzzy match**: 0.78 threshold (typos/transliterations)

### `_extract_team_ids_from_question()` (lines 4387-4504)
- **Pre-processing (S100b)**: Strips game prefixes ("lol:", "cs2:") and format suffixes ("- game 1 winner", "(bo3)")
- **6 regex patterns**: vs, beat/defeat, win against, to win, or, dash-separated
- Returns (team_a_id, team_b_id, clean_name_a, clean_name_b)

---

## 16. DATA FLOW & RESOLUTION FEED <a name="data-flow"></a>

### Prediction → Resolution → Calibration Cycle
```
1. Scan loop (every 10s):
   → analyze_opportunity() generates predictions
   → Logged to esports_prediction_log (predicted_prob, game, market_id)

2. Trade execution:
   → _execute_esports_trade() places paper orders
   → trade_events ENTRY row created
   → paper_trades row created

3. Resolution (every 10 scans ~100s):
   → _backfill_esports_outcomes() runs
   → Queries trade_events RESOLUTION events (last 7 days)
   → Updates esports_prediction_log.actual_outcome
   → Feeds _update_streaming_on_resolution() for ADWIN + OnlinePlatt

4. Monitoring (every 20 scans ~200s):
   → _check_monitoring_thresholds()
   → BetaCalibrator.fit_from_db() (batch refit from esports_prediction_log)
   → ConformalPredictor.fit_from_predictions() (batch refit)
   → Per-game Brier scores → game_kelly_mult, monitoring halts
```

### Key Tables
| Table | Purpose |
|-------|---------|
| `trade_events` | P&L authority. ENTRY/EXIT/RESOLUTION events. Immutable trigger. Partitioned by month. |
| `paper_trades` | Legacy compatibility. No `metadata` column. No `resolved_pnl` column. |
| `positions` | Open positions. No `closed_at`/`updated_at`. Only `opened_at` + `status`. |
| `esports_prediction_log` | Prediction history. `predicted_prob`, `actual_outcome`, `game`, `model_name`. |
| `glicko2_ratings` | Persisted Glicko-2 ratings per team per game. |
| `traded_markets` | Market registry. `bot_names` is TEXT (use LIKE, not array). |
| `system_kv` | Generic key-value store (migration 054). Canary stage persistence. |

---

## 17. MONITORING & SAFETY SYSTEMS <a name="monitoring"></a>

### Per-Game Monitoring (`_check_monitoring_thresholds()`)
- **Brier score tracking**: Per-game accuracy from last 90 days
- **Monitoring halt**: Game halted if Brier > 0.30 (SUSPENDED while BetaCalibrator unfitted)
- **Kelly degradation**: Aggregate Brier > 0.28 → cap kelly at 0.20 (SUSPENDED while any unfitted)
- **Game kelly mult**: Per-game 0.5x/1.0x/1.2x based on Brier (SUSPENDED while unfitted)

### PatchDriftDetector
- Detects game version patches (e.g., LoL patch 14.7 → 14.8)
- 48h observation mode after genuine patch detection
- **TRAP**: Only set `_patch_timestamps` when `old is not None` — not on first check (S88 fix)

### Drawdown Kelly Factor
- Monitors recent P&L drawdown
- Reduces kelly_fraction when in drawdown
- Used in sizing: `size *= dd_factor`

### Confluence Gate
- `ESPORTS_CONFLUENCE_MIN=0.60`
- Multiple signal sources must agree above threshold
- Checked in `analyze_opportunity()` quality waterfall

---

## 18. CLAUDE.md RULES (MUST FOLLOW) <a name="claude-md"></a>

### Prime Directive
Working code is sacred. Fix only what is broken. Fix it at the root. Prove it before and after.

### Key Rules
1. **One fix per commit** — each commit addresses exactly ONE issue
2. **Preserve every function signature** — unless the signature IS the bug
3. **No silent behavior changes** — state what changes and verify callers
4. **Never delete code you don't understand** — may handle edge cases
5. **No new dependencies without justification**
6. **No structural refactors during bug fixes**
7. **Paper trading IS production** — implement everything fully

### Before Every Edit
1. State the bug in one sentence
2. List files you will touch (if >3, justify)
3. Grep for dependents
4. Git snapshot before any edit
5. Read the ENTIRE file you're modifying

### Cross-Bot Verification (for shared modules)
After modifying base_bot.py, bankroll_manager.py, risk_manager.py, database.py, etc.:
- Run `pytest` — all tests must pass
- List every affected bot by name
- Verify scan output, not just tests passing

---

## CHANGE LOG

```
## CHANGE: 2026-03-17 (Session 100/100b/100c)
**Issue:** EsportsBot calibration pipeline was stale 3-stage sequential, trades blocked by 5+ independent gates
**Root cause:** Stale calibration, Python 3.13 scoping, liquidity guardian, kelly degradation, game kelly penalties, phi sizing
**Files modified:** bots/esports_bot.py, base_engine/execution/order_gateway.py
**Lines changed:** ~250 added, ~30 removed
**Blast radius:** EsportsBot, EsportsLiveBot, EsportsSeriesBot. order_gateway.py scoped by bot_name.
**Verification:** 479 tests passed. 4 deploys verified on VPS. All fixes confirmed in logs.
**Rollback:** git revert 4054b6b 3f26ba4 76aec57 4a819e1
```

---

## QUICK REFERENCE — VERIFICATION COMMANDS

```bash
# SSH to VPS
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21

# Scan summary
journalctl -u polymarket-ai --since "5 min ago" | grep esportsbot_scan_summary | tail -3

# Errors
journalctl -u polymarket-ai --since "10 min ago" | grep -iE "(error|warning|failed)" | grep -i esport | head -20

# Low confidence details
journalctl -u polymarket-ai --since "2 min ago" | grep esportsbot_low_confidence | tail -10

# Paper trades
journalctl -u polymarket-ai --since "30 min ago" | grep paper_trade | grep -i esport

# Calibration data accumulation
# (run on VPS psql)
SELECT game, COUNT(*) as total, COUNT(actual_outcome) as resolved
FROM esports_prediction_log WHERE created_at > '2026-03-16' GROUP BY game;

# BetaCalibrator fitting status
journalctl -u polymarket-ai --since "10 min ago" | grep esportsbot_beta_cal

# P&L
python scripts/bot_pnl.py EsportsBot 720
```
