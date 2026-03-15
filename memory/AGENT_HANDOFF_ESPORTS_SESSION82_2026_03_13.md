# Esports Session 82 Handoff — 2026-03-13

## Commit
`7efabe0` — `feat(esports): 6-item calibration & inference sprint`

## What Was Done — 6 Items from 35-Component Blueprint Analysis

### 1. Focal Temperature Scaling + HorizonBiasCalibrator
**Files:** `base_engine/features/calibration.py` (lines 167-472)
- `FocalTemperatureCalibrator`: Grid search T in [0.5,3.0], gamma in [0.0,5.0]. ~20% ECE improvement over plain temperature scaling
- `HorizonBiasCalibrator`: Le (2026) power-law recalibration per (domain, horizon) bucket. 4 TTR buckets (0-7d, 7-30d, 30-90d, 90d+)
- Both use same interface as existing `FavoriteLongshotCalibrator` (async `fit()`, sync `calibrate()`)
- **NOT wired to scan loop yet** — standalone classes ready for integration

### 2. Extremized Geometric Mean of Odds
**Files:** `base_engine/features/aggregation.py` (new), `bots/esports_bot.py`
- `extremized_geometric_mean()` — weighted log-space aggregation with extremization factor d
- Replaces hardcoded linear blending in esports_bot.py:
  - Dota2 blend (line 1263): `0.6*prob + 0.4*glicko2_prob` -> `extremized_geometric_mean([prob, glicko2_prob], d=1.5)`
  - Valorant blend (line 1284): same replacement
  - Cross-game XGB+Glicko2 blend (lines 1345-1347): `extremized_geometric_mean([glicko2_prob, xgb_prob], weights=[0.6, 0.4], d=1.5)`
- d=1.5 is the conservative default; can tune up to 2.5 after data accumulates

### 3. Le (2026) Per-Game Bias Decomposition
**Files:** `esports/calibration/__init__.py`, `esports/calibration/bias_decomposition.py`, `esports/calibration/metaculus_benchmark.py`
- `EsportsBiasDecomposition` class: per-game b parameter from resolved `esports_predictions`
- 4 components: base_bias, recalibration b, ECE, horizon correlation
- Min 30 resolved predictions per game
- `recalibrate(raw_prob, game)` applies power-law correction
- **NOT wired to scan loop yet** — needs 30+ resolved predictions per game before activation

### 4. httpx HTTP/2 + uvloop
**Files:** `esports/data/pandascore_client.py`, `requirements.txt`
- `http2=True` added to httpx.AsyncClient in PandaScore client (line 141)
- `httpx[http2]>=0.24.0` in requirements.txt (was `httpx>=0.24.0`)
- `uvloop>=0.19.0; sys_platform != 'win32'` added to requirements (already in main.py conditional import)
- HTTP/2 multiplexing helps PandaScore 1000 req/hr shared limit (concurrent requests on single TCP connection)

### 5. ONNX Tree Compilation
**Files:** `esports/models/onnx_compiler.py` (new), `esports/models/esports_trainer.py`
- `OnnxCompiler` class: `export_xgboost()`, `load_session()`, `predict_proba()`
- Graceful fallback when onnxmltools/onnxruntime not installed
- Auto-exports ONNX after model.save() in trainer for:
  - LoL model (n_features=8)
  - CS2 model (n_features=14)
  - Cross-game XGB (n_features=9)
- All wrapped in try/except (ONNX is optional enhancement, not required)
- `onnxmltools>=1.12.0`, `onnxruntime>=1.17.0`, `skl2onnx>=1.16.0` in requirements.txt

### 6. Metaculus Calibration Benchmark
**Files:** `esports/calibration/metaculus_benchmark.py`
- Standalone diagnostic tool for validating calibration pipeline
- Fetches 20K+ resolved binary questions from Metaculus public API
- Computes ECE/Brier, optionally applies calibrator and measures improvement
- Not integrated into any bot — run manually for validation

## Tests
- 49 new tests across 6 test files
- Full suite: **1516 passed, 8 skipped** (1 pre-existing MirrorBot failure excluded)
- Fixed flaky `test_bias_decomposition.py::test_fit_basic` — converted from `asyncio.get_event_loop().run_until_complete()` to proper `@pytest.mark.asyncio`

## Pre-existing Issues (NOT from this session)
- `test_mirror_bot_logic.py::TestCanOpenPosition::test_blocks_when_position_limit_reached` — fails in isolation, pre-dates this session
- Other uncommitted changes in working tree from sessions 77-81 (mirror_bot, base_bot, weather_bot, etc.)

## Blueprint Analysis Summary
From 35-component blueprint reviewed:
- **6 FULLY APPLICABLE** (all implemented this session)
- **6 PARTIALLY APPLICABLE** (cherry-pick later: MAPIE conformal, TabPFN for sparse games, GLiNER2 NER, Independent CoT, d3rlpy RL, Pearl Bandits)
- **19 SKIP** (not applicable to esports bots)

## What's NOT Wired Yet
These are standalone classes/utilities ready for integration but NOT yet called from scan loops:
1. `FocalTemperatureCalibrator` — needs resolved prediction_log data to fit
2. `HorizonBiasCalibrator` — needs paper_trades joined with markets
3. `EsportsBiasDecomposition` — needs 30+ resolved esports_predictions per game
4. ONNX inference in prediction paths — trainer exports ONNX, but predict() still uses native XGBoost
5. `MetaculusBenchmark` — manual diagnostic tool

## Next Steps (Priority Order)
- **P1**: Wire `extremized_geometric_mean` d parameter to config (currently hardcoded d=1.5)
- **P2**: Wire `FocalTemperatureCalibrator` into EsportsBot calibration pipeline
- **P2**: Wire ONNX inference into per-game model predict() methods
- **P3**: Run `EsportsBiasDecomposition.fit_from_db()` once 30+ predictions per game resolve
- **P4**: Run `MetaculusBenchmark` to validate calibration pipeline
- **P5**: Evaluate PARTIALLY APPLICABLE items (MAPIE conformal, TabPFN for sparse games)

## Critical Traps
- `extremized_geometric_mean` clips output to [0.05, 0.95] — same as existing blend logic
- ONNX export is try/except wrapped — missing deps = silent skip, not crash
- `HorizonBiasCalibrator` uses scipy.optimize.minimize_scalar — already in requirements
- `bias_decomposition.py` queries `esports_predictions` table — table must exist
- All new calibrators are ADDITIVE (new classes alongside existing), NOT replacements
