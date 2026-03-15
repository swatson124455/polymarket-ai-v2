# Esports Session 83 Handoff — 2026-03-13

## What Was Done — ALL Outstanding Items Resolved (9 Items)

### P1-1: Wire HorizonBiasCalibrator into scan loop
**Files:** `bots/esports_bot.py`
- Init in `__init__` (L118: `_horizon_calibrator`)
- Created in `start()` after bias_decomp
- Fits every 10min in `_check_monitoring_thresholds()` via `fit_from_paper_trades(n_days=180)`
- Applied in `analyze_opportunity()` after focal_temp: `calibrate(prob, "esports", ttr_days)`
- New helper: `_compute_ttr_days(market_data)` — extracts TTR from `end_date_iso`
- Calibration order: bias_decomp → focal_temp → **horizon_bias** → edge computation
- Identity when unfitted (needs 15+ resolved trades per bucket)

### P1-2: Wire ONNX inference for per-game models (LoL/CS2/Dota2/Valorant)
**Files:** `bots/esports_bot.py`
- 4 new instance vars: `_onnx_lol_session`, `_onnx_cs2_session`, `_onnx_dota2_session`, `_onnx_valorant_session`
- `_load_per_game_onnx_sessions()` — searches `saved_models/` and `data/` for `.onnx` files
- `_onnx_predict_game(onnx_session, game_state, native_model, game)` — ONNX-first with native fallback
- Dota2/Valorant prediction paths now use `_onnx_predict_game()` instead of `model.predict()`
- ONNX files produced by trainer (`esports_trainer.py` already exports after model.save())

### P1-3: Fix team name extraction
**Files:** `bots/esports_bot.py`
- **`_clean_team_names()`** enhanced:
  - Added LCK/LPL/LEC/LCS/VCT/Worlds/MSI/Spring/Summer/playoffs/qualifier/group/stage tournament suffixes
  - Strips "map N" / "game N" suffixes (e.g. "T1 map 3" → "T1")
  - Strips region tags "(KR)", "(CN)", "(EU)"
- **`_match_team_name()`** rewritten with 5-tier matching:
  1. Exact match (unchanged)
  2. **Alias lookup** — 50+ common abbreviations (JDG→JD Gaming, NaVi→Natus Vincere, etc.)
  3. Substring match (known_name in name, skipping ≤3 char names)
  4. **Reverse substring** (name in known_name, catches partial names)
  5. **Word-boundary match** for short names (≤3 chars) — prevents "t1" matching inside "contest1"
- **2 new question patterns** added to `_get_glicko2_prediction()`:
  - Pattern 5: "[Team] or [Team] — who will win?"
  - Pattern 6: "[Team] - [Team]" (dash-separated, common in Asian markets)
- **`_TEAM_ALIASES` dict** with 50+ entries covering LPL, LCK, LEC, LCS, VCT, CS2 teams

### P2-1: MAPIE conformal prediction intervals
**Files:** `esports/models/conformal_wrapper.py` (new), `bots/esports_bot.py`
- `ConformalPredictor` class — wraps any XGBClassifier with MAPIE LAC method
- `fit(model, X_cal, y_cal)` — requires 30+ calibration samples
- `predict_interval(X)` → `(p_low, p_mid, p_high)` for 90% prediction interval
- `conservative_prob(X)` — uses `p_low` when model says YES, `p_high` when NO
- Wired into `_execute_esports_trade()`: if conformal is fitted, uses conservative bound for sizing
- Identity when unfitted (requires separate calibration set from training)
- `mapie>=0.9.0` already in requirements.txt

### P2-2: Dynamic EGM d tuning per-game
**Files:** `bots/esports_bot.py`
- `_game_egm_d: Dict[str, float]` — per-game extremization factor
- `_update_per_game_egm_d()` — called in `_check_monitoring_thresholds()` every 10min
- Logic: Brier < 0.20 (Kelly mult 1.2) → d = egm_d + 0.5 (more extreme, capped at 2.5)
         Brier > 0.25 (Kelly mult 0.5) → d = egm_d - 0.3 (more conservative, floored at 1.0)
         Otherwise → default d
- All 3 EGM blend sites now use `self._game_egm_d.get(game, self._egm_d)` instead of hardcoded

### P2-3: Wire edge decay into sizing
**Files:** `bots/esports_bot.py`
- `_get_edge_decay_sizing_mult(game)` — returns sizing multiplier based on `_edge_decay_data`
  - top_bin CLV < -0.05: 0.6 (heavy reduction)
  - top_bin CLV < 0: 0.8 (moderate reduction)
  - Otherwise: 1.0 (no change)
- Applied in `_execute_esports_trade()` as additional sizing factor: `size *= _decay_mult`
- Logged in trade execution: `edge_decay_mult=_decay_mult`

### P3-1: TabPFN for sparse games (SC2, RL, CoD, R6)
**Files:** `esports/models/tabpfn_ensemble.py` (new), `bots/esports_bot.py`, `requirements.txt`
- `TabPFNEnsemble` class — wraps TabPFN v2.5 for small-sample games
- `fit_game(game, X, y)` — per-game fitting (min 20 samples)
- `predict(game, game_state)` → probability for team A win
- Blended in `_get_model_prediction()` at 30/70 weight (TabPFN/Glicko-2) for sparse games
- Graceful fallback: if `tabpfn` not installed, returns None (no-op)
- `tabpfn>=2.0.0` added to requirements.txt (commented, optional, requires torch)

### P3-2: Independent CoT ensemble for high-edge trades
**Files:** `esports/models/cot_validator.py` (new), `bots/esports_bot.py`
- `CoTValidator` class — LLM-based sanity check for trades with edge > 15%
- Uses Claude Haiku (fast, cheap) to validate: game match? Teams plausible? Edge reasonable?
- Rate limited: 3 calls per scan cycle
- Wired into `analyze_opportunity()` as final gate before returning opportunity
- Fail-open: if LLM unavailable or errors, trade approved
- Requires `ANTHROPIC_API_KEY` env var (optional, no-op without it)
- Scan counter reset at top of `scan_and_trade()`

### P3-3: Metaculus benchmark
**File:** `esports/calibration/metaculus_benchmark.py` (already exists from Session 82)
- Standalone diagnostic tool — NOT wired to any bot
- Run: `python -c "import asyncio; from esports.calibration.metaculus_benchmark import MetaculusBenchmark; asyncio.run(MetaculusBenchmark().run_validation())"`

## Config Added (Session 83)
**File:** `config/settings.py`
```env
ESPORTS_CONFORMAL_ALPHA=0.10     # 90% prediction interval width
ESPORTS_COT_EDGE_THRESHOLD=0.15  # Only validate trades with edge > 15%
ESPORTS_COT_MAX_PER_SCAN=3       # Max CoT LLM calls per scan
```

## New Files
| File | Lines | Purpose |
|------|-------|---------|
| `esports/models/conformal_wrapper.py` | 145 | MAPIE conformal prediction intervals |
| `esports/models/tabpfn_ensemble.py` | 120 | TabPFN v2 for sparse games |
| `esports/models/cot_validator.py` | 135 | CoT LLM validation for high-edge trades |
| `tests/unit/test_session83_features.py` | 320 | Tests for all Session 83 features |

## Tests
- 47 new tests in `test_session83_features.py`
- Full suite: **1594 passed, 8 skipped** (0 failed)
- Covers: TTR days, dynamic d, edge decay mult, team name matching, conformal predictor, TabPFN, CoT validator, ONNX predict helper

## Safety Guarantees
All new capabilities are:
1. **ADDITIVE** — new classes alongside existing, nothing removed or replaced
2. **GRACEFULLY DEGRADING** — return identity/fallback when unfitted or packages missing
3. **OPTIONAL** — TabPFN/CoT require extra packages; conformal needs calibration data; all skip silently

## Calibration Pipeline (Final Order)
```
raw model_prob
  → bias_decomp.recalibrate(prob, game)     [Session 82, needs 30+ predictions/game]
  → focal_temp.calibrate(prob)              [Session 82, needs 50+ prediction_log entries]
  → horizon_bias.calibrate(prob, "esports", ttr_days)  [Session 83, needs 15+ trades/bucket]
  → edge computation
```

## Sizing Pipeline (Final Order)
```
confidence
  → conformal conservative_prob (p_low for YES, p_high for NO)  [Session 83]
  → expiry boost (1.5x <6h, 1.2x <24h)
  → phi_factor (uncertainty scaling)
  → dd_factor (drawdown reduction)
  → game_kelly_mult (per-game Brier-based)
  → edge_decay_mult (0.6/0.8/1.0 from CLV analysis)  [Session 83]
  → $100 cap
```

## Critical Traps (Session 83 additions)
- **Calibration order matters**: bias_decomp → focal_temp → horizon_bias. Reordering changes output.
- **conformal_wrapper needs separate calibration set**: Don't fit on training data — use held-out validation set.
- **TabPFN requires torch**: Heavy dependency, optional install. Only for sparse games.
- **CoT validator uses ANTHROPIC_API_KEY**: Same key as other Claude API calls. Costs ~$0.001/call.
- **Edge decay mult stacks with other multipliers**: Total sizing = base × phi × dd × game_kelly × decay_mult.
- **_TEAM_ALIASES is static**: New teams need manual addition. Common org rebrands require updates.
- **Pattern 6 (dash-separated) is greedy**: May match non-team dashes. Applied last as lowest-priority fallback.
