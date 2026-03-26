# AGENT HANDOFF — EsportsBot Session 120 (2026-03-23)

## Session Type: EsportsBot-scoped (CS2 fix + pipeline audit + 6 gap fixes)

## CRITICAL CONTEXT FOR NEXT AGENT

Single-bot session for EsportsBot only. Read `CLAUDE.md` in repo root. Read `PROMPT_ESPORTSBOT_SESSION120.md` for full system context.

---

## What Was Done This Session (S120)

### 1. CS2 Pre-Game Prediction Path (was DEAD — now LIVE)
**Root cause:** `esports_bot.py:2109` had `and live_data` guard. CS2 ML model only fired for live matches. Pre-game CS2 markets (majority of volume) fell through to raw Glicko-2 fallback.

**Fix (3 files):**
- `esports/data/esports_data_collector.py` — Added Glicko-2 metadata (`matchup_uncertainty`, `rd_asymmetry`, `team_a_volatility`, `team_b_volatility`, `best_of`) to CS2 training data via `_get_glicko2_metadata()`
- `esports/models/cs2_economy_model.py` — Added dual-path architecture:
  - `PREGAME_FEATURES` (6 Glicko-2 features matching Valorant/Dota2)
  - `predict_pregame()` — XGBoost on 6 features with heuristic fallback
  - `train_pregame()` — XGBClassifier(n_estimators=60, max_depth=2)
  - `save()`/`load()` updated (backward-compatible)
  - Existing round→map→match chain UNTOUCHED
- `esports/models/esports_trainer.py` — `_train_cs2()` now also calls `model.train_pregame(train_set)`

**Result:** CS2 pregame model trained on 3316 samples. `team_strength_diff` has importance=1.0 (old training data lacks new Glicko-2 features — will diversify as new data accumulates).

### 2. LoL Pre-Game Prediction Path (was DEAD — now LIVE)
**Root cause:** Same `and live_data` guard at line 2074. LoL pre-game markets also fell through to Glicko-2.

**Fix:** Added LoL pre-game block in `esports_bot.py`. Builds game_state with neutral live features (game_time=30, gold=0.5, towers=0, dragons=0) + real Glicko-2 from `_build_glicko2_game_state()`. Calls `predict_with_glicko2()` for ML+Glicko-2 blend.

### 3. Unified Enrichment Pipeline (`_enrich_prediction()`)
**Root cause:** Game-specific ML paths (LoL, CS2, Dota2, Valorant) returned predictions BEFORE the Glicko-2 fallback, which is where form adjustments, cross-game XGB, LAN adj, blue side bonus, and BO adjustment lived. The 4 games with ML models skipped all enrichment.

**Fix:** Extracted ~170 lines of enrichment from Glicko-2 fallback into `_enrich_prediction()` method. All 7 prediction paths (LoL live, LoL pre-game, CS2 live, CS2 pre-game, Dota2, Valorant, Glicko-2 fallback) now call the same method.

**Enrichment stack (in order):**
1. Form adjustment (OpenDota for Dota2, PandaScore for others, Aligulac for SC2, Ballchasing for RL)
2. TabPFN blend (CoD, R6, SC2, RL)
3. Cross-game XGB blend (all 8 games)
4. CatBoost draft model blend (if enabled)
5. LAN adjustment (CS2, Valorant)
6. Blue side bonus (LoL)
7. BO format adjustment (all games)

### 4. GAP-1: CLOB Volume Passthrough
EsportsBot used $50K generic fallback for fill probability. Now reads real `volume_24h` from `order_gateway._market_index` (same pattern as WeatherBot) and passes in event_data.

### 5. GAP-4: Min Trade Floor
Added `ESPORTS_MIN_TRADE_USD=10.0` setting. Rejects trades where cost (price × size) < $10. Prevents dust positions. Logs `esportsbot_below_min_trade`.

### 6. GAP-2: Same-Side Dedup (WS + Series paths)
Scan path already had side-aware checking (line 1128-1154). WS path (line 750) and series WS path (line 5664) only checked market_id, not side. Now both check `_position_details` for side match — only blocks re-entry on same side.

### 7. 1K: Roster Stability Hygiene
- Normalized `team_id` to consistent str key (`str(int(team_id))`)
- Added 1h API failure cooldown (`_roster_fail_cache`) — prevents hammering PandaScore after timeouts

### 8. `no_prediction` Logging
Failed predictions now log question text: `esportsbot_no_prediction` with game, market_type, market_id, question.

---

## PENDING REVIEW — GAP-5: Calibration Exclusion Flags

**What:** BetaCalibrator trains on ALL trade outcomes including bad ones (restart floods, patch drift era, null-confidence entries). No mechanism to exclude junk data from calibration training.

**Why it matters:** When calibrators fit (ETA ~48-72h), bad trades will pollute the sigmoid parameters. MirrorBot had a similar issue but solved it differently (data quality audit, not exclusion flags).

**Current state:** 0/8 games fitted. Not urgent yet — no data being calibrated.

**Proposed fix (when ready):**
1. Add `calibration_exclude=true` to event_data for trades with known issues
2. Filter these in `esports_db.py` calibration queries
3. Add admin mechanism to flag bad periods retroactively

**Effort:** ~3h. **Blast radius:** MEDIUM-HIGH (calibration is core to confidence).

**Action required:** User approve/deny before implementing. Defer until first game fits BetaCalibrator.

---

## PENDING REVIEW — Removed Features (from S119)

These were removed in S119 because they were broken. User must approve before rebuilding.

| ID | Feature | Status | Notes |
|----|---------|--------|-------|
| PR-1 | Confluence gate | PENDING | Multi-signal filter, ~2h |
| PR-2 | Momentum fallacy | BLOCKED | No series markets on Polymarket |
| PR-3 | Champion drift (LoL) | PENDING | LoL worst game (0.308 Brier), ~1h |
| PR-4 | Team exposure tracking | PENDING | Correlated risk, ~1h |
| ~~PR-5~~ | ~~Volume filter~~ | **DENIED** | User: real CLOB fills what it can, book walk handles it |
| PR-6 | Cross-game conformal | BLOCKED | Needs 50+ resolved predictions |
| PR-7 | CS2 economy helpers | BLOCKED | Needs real-time data source |

---

## Live State at Session End

| Metric | Value |
|--------|-------|
| Markets scanned | 4-15 (varies with active matches) |
| Live matches | 16-20 |
| Opportunities | 1-2 per scan |
| Errors | 0 |
| Daily cap | $20,000 |

## P&L (unchanged from S119)
| Day | Net |
|-----|-----|
| **All-time** | **+$4,844** |

## Calibrator Status
- **0/8 games fitted** — fresh predictions accumulating since S118
- CS2 has 3 resolved samples, needs 15 minimum
- ETA first fit: ~24-48h

## Files Modified This Session (5)
- `bots/esports_bot.py` — CS2/LoL pre-game paths, `_enrich_prediction()`, GAP-1/2/4, 1K, no_prediction logging
- `esports/models/cs2_economy_model.py` — `PREGAME_FEATURES`, `predict_pregame()`, `train_pregame()`, save/load
- `esports/models/esports_trainer.py` — `model.train_pregame()` in `_train_cs2()`
- `esports/data/esports_data_collector.py` — Glicko-2 metadata in CS2 training data
- `config/settings.py` — `ESPORTS_MIN_TRADE_USD`

## Tests
1428 passed, 2 skipped, 0 failures.

## Next Session Priorities
1. **Monitor BetaCalibrator** — should start fitting within 24-48h
2. **User review of GAP-5** — approve/deny calibration exclusion flags
3. **User review of PR-1, PR-3, PR-4** — approve/deny feature rebuilds
4. **Monitor CS2 pregame model** — watch for feature importance diversification as new Glicko-2-enriched training data accumulates
5. **LoL accuracy** — still worst game (Brier 0.308). PR-3 (champion drift) could help if approved.
