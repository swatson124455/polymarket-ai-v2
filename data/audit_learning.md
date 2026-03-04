# Learning Bottleneck Audit -- Top 10 Issues

Audit date: 2026-02-20
Scope: Feedback loops, model retraining, pattern learning, performance tracking, adaptation

---

## L1: DriftTracker Never Fed Outcomes -- Drift Detection is Blind

**Severity**: CRITICAL
**File**: `base_engine/prediction/prediction_engine.py` lines 59-67, 243, 800-802

**What's wrong**: `_DriftTracker` has `record_prediction()` and `record_outcome()` methods, but neither is ever called anywhere in the codebase. The tracker's `_recent_predictions` and `_recent_outcomes` lists are permanently empty. `check_model_drift()` always returns `{"drifted": False, "checks": {"insufficient_data": True}}`. Furthermore, `check_model_drift()` itself is never called by any scheduler, monitor, or bot -- it is a dead method. The ADWIN drift test, calibration drift check, and confidence collapse detection are all implemented but completely inert.

**Evidence**: `grep -r "_drift_tracker.record" *.py` returns zero matches. `grep -r "check_model_drift" *.py` returns only the definition itself.

**Fix**: In `predict()`, call `self._drift_tracker.record_prediction(ensemble_prediction)`. In the resolution backfill or calibration tracker, call `record_outcome(predicted_prob, actual)` when markets resolve. Wire `check_model_drift()` into the scheduler's `_retrain_cycle()` so drift triggers retraining.

---

## L2: MetaLearner Assigns Identical Weights to All Models -- "Optimization" is a No-Op

**Severity**: CRITICAL
**File**: `base_engine/learning/scheduler.py` lines 161-166; `base_engine/learning/meta_learning.py` lines 169-213

**What's wrong**: In `_run_meta_learner_tuning()`, the scheduler builds `model_performances` by assigning the **same aggregate accuracy** from `get_recent_brier_from_prediction_log()` to every model. All 10 models get `{"accuracy": 0.62, "sharpe_ratio": 0.0}` (for example). When `learn_optimal_ensemble()` computes weights as `model_score / total_performance`, every model gets `1/N` -- exactly equal weights. The "learning" produces the same result as no learning at all. The MetaLearner runs every 4th retrain cycle (~24h of wall clock) and accomplishes nothing.

**Evidence**: Line 163-164: `model_performances[name] = {"accuracy": perf.get("accuracy", 0.5), "sharpe_ratio": 0.0}` -- the same `perf` dict is used for all models with no per-model breakdown.

**Fix**: Compute per-model accuracy by logging each model's individual prediction alongside the ensemble prediction in `prediction_log`, then querying per-model Brier/accuracy. Alternatively, use the validation-set accuracy computed during `_train_models()` and cache it for MetaLearner consumption.

---

## L3: user_performance Pattern Dict is Never Populated -- 60% Weight on Empty Data

**Severity**: HIGH
**File**: `base_engine/learning/learning_engine.py` lines 54, 214-304, 349-354, 416-439

**What's wrong**: The `patterns["user_performance"]` BoundedDict is initialized but **never written to**. The `_update_patterns()` method updates `market_types`, `price_ranges`, and `time_to_resolution`, but has no code path that writes to `user_performance`. Yet `calculate_combined_confidence()` uses it with a 60% weight (`confidence_weights["user_based"] = 0.60`). Since the dict is always empty, `user_conf` always falls back to 0.5, making the combined confidence `0.60 * 0.5 + 0.40 * bet_type_conf = 0.30 + 0.40 * bet_type_conf`. The user-based signal contributes nothing but a constant bias of 0.30, diluting the bet-type confidence that actually has data behind it.

**Evidence**: `grep "user_performance\[" learning_engine.py` shows only reads (lines 350, 427) and no writes.

**Fix**: Either (a) add user-level tracking in `_update_patterns()` by extracting user_address from trades and recording per-user win/loss, or (b) remove the dead 60% user_based weight and use 100% bet_type confidence until user-level data is available.

---

## L4: AUTO_RETRAIN_ON_DEGRADATION Defaults to False -- Autonomous Recovery is Disabled

**Severity**: HIGH
**File**: `config/settings.py` line 179; `base_engine/learning/scheduler.py` lines 104-141

**What's wrong**: The degradation-triggered retrain feature (`_check_degradation_and_force_retrain`) is fully implemented and wired into the scheduler, but `AUTO_RETRAIN_ON_DEGRADATION` defaults to `false` and is not set in `.env`. This means the system will continue trading on models with Brier > 0.30 or accuracy < 45% indefinitely, waiting for the next scheduled 6h retrain cycle. In a fast-moving prediction market, 6 hours of degraded models can mean significant losses.

**Evidence**: `settings.py` line 179: `AUTO_RETRAIN_ON_DEGRADATION: bool = os.getenv("AUTO_RETRAIN_ON_DEGRADATION", "false")`. `.env` has no override. The feature works correctly when enabled but is dead by default.

**Fix**: Set `AUTO_RETRAIN_ON_DEGRADATION=true` in `.env`. The existing cooldown (`AUTO_RETRAIN_COOLDOWN_HOURS=1.0`) prevents runaway retraining.

---

## L5: get_trades_since() Uses Single JOIN -- Misses 37% of Trades for Learning

**Severity**: HIGH
**File**: `base_engine/data/database.py` lines 1811-1813

**What's wrong**: `get_trades_since()` joins `Trade.market_id == Market.id` only, not the UNION ALL pattern used in `_prepare_training_data()`. Trades stored with `market_id = condition_id` (from the Data API) are silently dropped. The MEMORY.md documents that 35,311 trades join on `m.id` and 20,579 join on `m.condition_id` -- so roughly 37% of eligible trades never reach `learn_from_trades()`. The LearningEngine's pattern counters (market_types, price_ranges, time_to_resolution) are systematically under-informed.

**Evidence**: Line 1812-1813: `.join(Market, Trade.market_id == Market.id)` -- no OR clause for `condition_id`. Compare to `prediction_engine.py` line 1203 which uses UNION ALL on both id paths.

**Fix**: Change the join to `.join(Market, or_(Trade.market_id == cast(Market.id, String), Trade.market_id == Market.condition_id))` or use a UNION ALL pattern matching `_prepare_training_data()`.

---

## L6: save_patterns_to_db() Skips user_performance and simulation_errors

**Severity**: MEDIUM
**File**: `base_engine/learning/learning_engine.py` lines 156-196

**What's wrong**: `save_patterns_to_db()` iterates over `["market_types", "price_ranges", "time_to_resolution"]` only -- it does not save `user_performance` (even if it were populated, per L3) or the per-market `simulation_errors` that `update_simulation_confidence()` accumulates. On restart, all simulation confidence data is lost. The `load_patterns_from_db()` method also only loads those three pattern types. If user_performance is ever fixed (L3), it would still be lost on every restart.

**Evidence**: Line 163: `for ptype in ["market_types", "price_ranges", "time_to_resolution"]` -- user_performance and market-level simulation patterns are excluded.

**Fix**: Add `"user_performance"` to the save/load loop. For simulation_errors (which are per-market_id keyed in the top-level `self.patterns` dict), add a separate serialization path or use a dedicated table.

---

## L7: PerformanceTracker Uses entry_time as exit_time -- Hold Time Always Zero

**Severity**: MEDIUM
**File**: `base_engine/data/resolution_backfill.py` lines 272-273

**What's wrong**: When `resolution_backfill` feeds resolved paper trades to `PerformanceTracker.record_trade_outcome()`, it passes `entry_time=row[6], exit_time=row[6]` (both are `created_at`). The comment acknowledges "resolution time unknown." This means `hold_time_hours` is always 0.0 for every recorded trade. The `time_to_resolution_days` dimension in the Pattern Analysis dashboard is always NULL. The PerformanceTracker cannot learn whether short-duration or long-duration trades are more profitable -- a critical dimension for strategy adaptation.

**Evidence**: Line 273: `exit_time=row[6],  # Use created_at as proxy (resolution time unknown)`.

**Fix**: Query `m.resolved_at` or `m.end_date_iso` from the markets table during backfill and pass it as `exit_time`. The resolution_backfill already has the market data available in the same query context.

---

## L8: EnsembleBot.optimize_weights() Only Searches 3 of 10+ Models

**Severity**: MEDIUM
**File**: `bots/ensemble_bot.py` lines 669-708

**What's wrong**: `optimize_weights()` grid-searches over `random_forest`, `xgboost`, and `gradient_boosting` only, computing `gb_weight = 1.0 - rf_weight - xgb_weight`. The other 7-8 models (extra_trees, hist_gradient_boosting, lightgbm, catboost, logistic_regression, ridge, knn, mlp) are completely ignored. Furthermore, this method is never called from production code -- the only call is in `tests/test_bots.py` line 188. Even if it were called, it would produce weights that sum to 1.0 for only 3 models, leaving the rest at default or zero.

**Evidence**: Lines 682-688: The grid only iterates `rf_weight`, `xgb_weight`, and computes `gb_weight`. No other model names appear.

**Fix**: If this method is intended for production use, extend it to all models or replace it with the MetaLearner's `learn_optimal_ensemble()` (once L2 is fixed). If it is test-only, mark it as deprecated or remove it to avoid confusion.

---

## L9: LearningEngine.learn_from_price_history() Treats All Price Increases as Wins

**Severity**: MEDIUM
**File**: `base_engine/learning/learning_engine.py` lines 91-139

**What's wrong**: The price-history learning fallback converts consecutive price pairs into trade-like entries where `pnl = next_price - current_price`. Any positive price movement is counted as a "win" in pattern tracking. This is a false signal: a price going from 0.40 to 0.45 does not mean buying at 0.40 was correct -- the market could still resolve NO. This method runs whenever `len(recent_trades) < 10` (the default for `LEARN_FROM_PRICES_MIN_TRADES`), which is the common case in paper trading. It pollutes `market_types`, `price_ranges`, and `time_to_resolution` patterns with noise that has no relationship to actual market outcomes.

**Evidence**: Line 122: `pnl = nxt_p - curr_p` and line 286: `is_win = isinstance(pnl, (int, float)) and pnl > 0`.

**Fix**: Either (a) disable price-history learning when real resolved-market data exists (`LEARN_FROM_PRICES_WHEN_TRADES_SPARSE=false`), or (b) filter to resolved markets and use resolution as the label instead of price direction, matching the approach in `_fallback_training_from_prices()`.

---

## L10: Prediction Cache Prevents DriftTracker and Calibration Feedback

**Severity**: MEDIUM
**File**: `base_engine/prediction/prediction_engine.py` lines 1514-1531

**What's wrong**: The `predict()` method has a two-layer cache (Redis 30s + local 300s). When a cached prediction is returned (line 1525/1531), the method returns early before reaching the prediction logging code (line 1637-1661). This means repeated queries for the same market at the same price -- which is the common case during scan loops -- never log to `prediction_log`. Since the prediction_log is the primary data source for `get_recent_brier_from_prediction_log()`, calibration tracking, MetaLearner tuning, and the degradation-triggered retrain check, the caching significantly reduces the feedback signal. In the worst case (300s TTL, 10s scan interval), only 1 in 30 predictions are logged.

**Evidence**: Lines 1525 and 1531 both `return dict(cached_val)` before reaching line 1637 (`PREDICTION_LOG_ENABLED` check and `_bg_log()`).

**Fix**: Move the prediction logging to a separate concern that runs regardless of cache hits, or log predictions at the bot level (in `_analyze_one_token`) where the cache return already provides the data needed for logging. Alternatively, if deduplication is desired, log only when `trade_executed=true`.

---

## Summary Table

| ID  | Severity | File:Line | Issue | Impact |
|-----|----------|-----------|-------|--------|
| L1  | CRITICAL | prediction_engine.py:59,243,802 | DriftTracker never fed, never checked | Drift detection completely inert; degraded models run indefinitely undetected |
| L2  | CRITICAL | scheduler.py:161-166, meta_learning.py:169 | MetaLearner gets identical accuracy for all models | Weight "optimization" always produces equal weights; 24h cycles wasted |
| L3  | HIGH | learning_engine.py:54,214-304 | user_performance never written to | 60% of combined confidence is a constant 0.5; dilutes real bet-type signal |
| L4  | HIGH | settings.py:179 | AUTO_RETRAIN_ON_DEGRADATION=false by default | System cannot self-heal on degradation; waits 6h for scheduled retrain |
| L5  | HIGH | database.py:1811-1813 | get_trades_since() single JOIN misses condition_id trades | ~37% of trades excluded from LearningEngine pattern updates |
| L6  | MEDIUM | learning_engine.py:163 | save_patterns_to_db skips user_performance + simulation_errors | Pattern data lost on restart; simulation calibration resets to zero |
| L7  | MEDIUM | resolution_backfill.py:272-273 | exit_time = entry_time in PerformanceTracker | hold_time_hours always 0; time-to-resolution dimension unusable |
| L8  | MEDIUM | ensemble_bot.py:669-708 | optimize_weights() searches only 3 of 10+ models | Grid search ignores 70% of ensemble; method is also dead code (never called) |
| L9  | MEDIUM | learning_engine.py:91-139 | Price-history learning counts price increase as "win" | Noise injected into patterns; no relationship to market resolution |
| L10 | MEDIUM | prediction_engine.py:1514-1531 | Cache hits skip prediction logging | ~97% of predictions not logged; starves calibration, MetaLearner, degradation checks |
