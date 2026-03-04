# Decision Making / Trade Bottleneck Audit

**Date**: 2026-02-20
**Scope**: ML model logic, position sizing, entry/exit logic, risk management, trade decision pipeline
**Files audited**: prediction_engine.py, ensemble_bot.py, order_gateway.py, risk_manager.py, base_bot.py, momentum_bot.py, arbitrage_bot.py, settings.py

---

## T1 -- MomentumBot Uses BUY/SELL Sides Instead of YES/NO (Semantic Mismatch)

**Severity**: HIGH
**File**: `bots/momentum_bot.py` lines 111-116, 331-335, 495-499
**What's wrong**:
MomentumBot's mean-reversion logic (both reactive WS path and scan loop) emits `side = "SELL"` or `side = "BUY"` instead of the Polymarket-native `"YES"` / `"NO"`. This contradicts the critical design rule documented in MEMORY.md: "YES and NO are both BUY operations -- you are buying that token. SELL only means closing a position."

When MomentumBot sends `side = "SELL"` for a mean-reversion fade, the OrderGateway interprets this as closing an existing position (line 192 of order_gateway.py: `_is_sell = side.upper() == "SELL"`). This means:
- SELL orders bypass all risk checks (line 255: `if self.risk_manager is not None and not _is_sell`)
- SELL orders bypass drawdown controller (line 195: `if self.drawdown_controller is not None and not _is_sell`)
- The paper trading engine maps it to a SELL (line 429: `paper_side = "SELL" if str(side).upper() == "SELL" else "BUY"`)

The same issue exists in `_check_persuasion_fade()` at line 495-499 which emits `"SELL"` or `"BUY"` instead of `"NO"` or `"YES"`.

The convergence_fade mode at lines 387/393 also uses `"SELL"` and `"BUY"`.

**Fix**: Replace all MomentumBot `side = "SELL"` with `side = "NO"` and `side = "BUY"` with `side = "YES"`. The cascade_fade mode (line 461) already correctly uses YES/NO. Apply the same pattern to mean_reversion, convergence_fade, persuasion_fade, and disposition_exploit modes.

---

## T2 -- EnsembleBot optimize_weights() Only Searches 3 of 11 Models

**Severity**: HIGH
**File**: `bots/ensemble_bot.py` lines 669-708
**What's wrong**:
The `optimize_weights()` method grid-searches over only `random_forest`, `xgboost`, and `gradient_boosting` (3 models). It assigns 100% of weight across just those 3 (`gb_weight = 1.0 - rf_weight - xgb_weight`), completely ignoring the other 8 models (extra_trees, hist_gradient_boosting, lightgbm, catboost, logistic_regression, ridge, knn, mlp).

After optimization runs, `self.model_weights` is overwritten with only 3 keys. When `_analyze_one_token()` later uses these weights (line 491-502), all other models fall back to `default_w = 1.0 / len(model_predictions)`. This means optimization destroys the carefully tuned 11-model weight distribution defined in `__init__` (lines 66-77) and replaces it with an incomplete one.

**Fix**: Rewrite `optimize_weights()` to search over all active models. At minimum, preserve the existing weights for models not in the search grid. Better: use a proper optimization method (e.g., scipy.optimize or Bayesian optimization) that handles 11-dimensional weight space efficiently instead of brute-force grid search.

---

## T3 -- Position Sizing Ignores Edge; Sizes on Raw Confidence Alone

**Severity**: HIGH
**File**: `base_engine/risk/risk_manager.py` lines 384-433
**What's wrong**:
`calculate_position_size()` computes size purely from `confidence` -- the model's predicted probability. It does not consider the *edge* (difference between predicted probability and market price). A market priced at 0.70 with a model prediction of 0.72 (edge = 2%) gets the same size as a market priced at 0.30 with prediction 0.72 (edge = 42%).

The Kelly sizing path (lines 435-483) does account for edge via the Kelly formula, but `USE_KELLY_SIZING` defaults to `false` in settings.py line 288. So the default path is pure linear scaling:
```
base_size = available_capital * MAX_POSITION_SIZE_PCT  (= $100,000 * 0.10 = $10,000)
confidence_multiplier = (confidence - 0.55) / 0.45
adjusted_size = base_size * confidence_multiplier
```

This means a 65% confident prediction on a 64-cent market (edge = 1 cent) allocates the same $2,222 as a 65% prediction on a 20-cent market (edge = 45 cents). The edge check in `check_risk_limits()` line 157-175 is a binary gate (pass/fail), not a sizing input.

**Fix**: Incorporate edge into the default (non-Kelly) sizing path. Scale `adjusted_size` by `abs(prediction - price)` relative to some normalizer. Alternatively, enable Kelly sizing by default with conservative fraction (0.15-0.25) now that the Brier gate already protects against poor calibration.

---

## T4 -- Prediction Cache Key Uses Price Only, Not Token Side

**Severity**: MEDIUM
**File**: `base_engine/prediction/prediction_engine.py` lines 1516, 1751
**What's wrong**:
The prediction cache key is `f"{market_id}:{repr(price)}"`. It does not include `token_id`. In a binary market, the YES token and NO token can have the same `market_id` and same `price` (when price = 0.50, or when YES=0.40 and NO=0.40 due to the spread). The system calls `predict()` for both tokens in parallel (ensemble_bot.py lines 427-433), so:

1. YES token prediction runs, caches result under key `"628113:0.4"`
2. NO token prediction runs, finds cached result from YES token, returns it

The model outputs `P(outcome=YES)`. For the YES token, confidence = prediction. For the NO token, confidence = 1 - prediction. The EnsembleBot correctly flips at line 517-518, but it receives the wrong `model_predictions` dict from the cache because the cache already returned the YES-perspective values.

This means in markets where YES and NO tokens share the same price value, the NO-side analysis uses stale/incorrect cached predictions.

**Fix**: Include `token_id` in the cache key: `cache_key = f"{market_id}:{token_id}:{repr(price)}"`. Also update the Redis cache key at line 1754.

---

## T5 -- Arbitrage Bot Uses Stale Prices Without Age Validation

**Severity**: MEDIUM
**File**: `bots/arbitrage_bot.py` lines 338-417
**What's wrong**:
The arbitrage opportunity structure includes a `price_fetched_at` timestamp (line 376), and `ARB_MAX_PRICE_AGE_SECONDS` is configured at 5 seconds (settings.py line 268). However, nowhere in the arbitrage execution pipeline is the price age actually checked. The `_execute_arbitrage()` method and `ArbitrageTransactionCoordinator` never validate whether the prices are still fresh.

Arb is extremely sensitive to price staleness -- a 5-second-old price at 0.48+0.48=0.96 might now be 0.50+0.50=1.00 (no profit). The scan loop processes markets sequentially, so by the time opportunity #10 executes, its prices could be 30+ seconds old. The `ARB_ORDER_DELAY_SECONDS = 0.5` between executions (line 279) further ages prices.

The WS reactive path (line 166) uses live prices, but the scan loop path does not validate freshness.

**Fix**: In `_execute_arbitrage()`, check `time.time() - opportunity.get("price_fetched_at", 0) > ARB_MAX_PRICE_AGE_SECONDS` and re-fetch prices from the CLOB or WS cache before executing. Reject the opportunity if prices have moved past the profit threshold.

---

## T6 -- EnsembleBot Adaptive Confidence Can Over-Tighten or Over-Loosen

**Severity**: MEDIUM
**File**: `bots/ensemble_bot.py` lines 94-128
**What's wrong**:
The adaptive confidence logic adjusts `min_consensus_confidence` based on recent accuracy from `get_recent_brier_from_prediction_log()`. When accuracy >= 0.65, it loosens the threshold to `max(base - 0.10, 0.55)`. When accuracy < 0.45, it tightens to `min(base + 0.15, 0.85)`.

Problems:
1. **Feedback loop**: When the threshold loosens, more marginal trades execute, which likely have lower accuracy. This drives accuracy down, which tightens the threshold, which increases accuracy (only strong signals pass), which loosens again. This creates oscillation between 0.55 and 0.70 every 5-10 minutes.
2. **Cold start amplification**: The `count >= 20` gate uses ALL resolved predictions, not just recent ones. If the system starts with 20 stale backfill predictions that happen to show low accuracy, the threshold immediately jumps to 0.70 (base 0.55 + 0.15), blocking most trades for the entire session.
3. **No hysteresis**: The threshold jumps discretely (base, base-0.10, or base+0.15) with no smoothing. A single scan cycle can swing the threshold by 15% based on one new resolved prediction tipping accuracy past the 0.45/0.65 boundary.

**Fix**: Add exponential smoothing to the threshold adjustment (e.g., `new_threshold = 0.8 * old_threshold + 0.2 * target`). Use only predictions from the last 24-48h (not all-time). Add a minimum sample count that scales with time (e.g., 20 per 6 hours, not a flat 20 total). Add hysteresis bands (different thresholds for loosening vs tightening).

---

## T7 -- MomentumBot Exits Use Wrong P&L Formula for NO Positions

**Severity**: MEDIUM
**File**: `bots/momentum_bot.py` lines 588-602
**What's wrong**:
The `_check_exits()` method computes P&L as:
```python
pnl_pct = (current - entry) / entry
```
The comment on line 589 says "P&L is always (current - entry) / entry regardless of which token was bought." This is correct for the token price itself -- if you bought a NO token at 0.30 and it's now worth 0.25, your loss is -16.7%.

However, this method uses `tokens[0].get("outcomePrice")` (line 198) as the `current` price. `tokens[0]` is conventionally the YES token. If the position was opened on the NO token, the NO token's current price should be used, not the YES token's price.

The `market_id_to_price` dict (lines 190-202) maps each market to `tokens[0]` price only. When a MomentumBot position is on the NO side (e.g., from a mean-reversion SELL/fade), the exit check compares the YES token's price movement against the NO position's entry -- the wrong reference price entirely.

**Fix**: Track which token was used for each position (YES or NO). When building `market_id_to_price`, include both token prices and use the correct one for exit P&L calculation. Or use `1 - yes_price` as a proxy for the NO token price when only the YES price is available.

---

## T8 -- OrderGateway get_all_open_positions_snapshot() Returns Hardcoded Placeholder Data

**Severity**: MEDIUM
**File**: `base_engine/execution/order_gateway.py` lines 95-109
**What's wrong**:
`get_all_open_positions_snapshot()` is called by RiskManager's CVaR tail-risk gate (risk_manager.py line 317). It returns position data used to compute portfolio-level Conditional Value at Risk. However, every position is returned with:
```python
"side": "YES",
"size": 1.0,
"price": 0.5,
"predicted_prob": 0.5,
```

These are hardcoded placeholders. The actual position side, size, and entry price are tracked in `_position_exposure` (which only stores the dollar value, not the components). This means:
- CVaR computation treats every position as a YES position at 0.50, regardless of actual side or price
- A portfolio of 10 NO positions at 0.90 (low risk, high conviction) gets the same CVaR score as 10 YES positions at 0.10 (high risk, speculative)
- The `value_usd` is correct, but `predicted_prob` = 0.5 means the risk model can't differentiate between high-edge and low-edge positions

This undermines the entire CVaR risk gate. It may incorrectly block safe trades or permit risky ones.

**Fix**: Store side, size, price, and predicted_prob when tracking positions in `_track_position_open()`. Return actual values from the snapshot. Alternatively, query the `Position` table for these fields during snapshot generation (with a short cache TTL).

---

## T9 -- Validation Gate at min_acc < 0.50 Accepts Trivially Bad Models

**Severity**: MEDIUM
**File**: `base_engine/prediction/prediction_engine.py` lines 697-703
**What's wrong**:
After training, the validation gate rejects the new ensemble only if ANY single model has accuracy below 0.50 (random chance for binary classification):
```python
min_acc = min(val_accs)
if min_acc < 0.50:
    return  # Don't update self.models
```

This means if the worst model has 50.1% accuracy, all models are accepted. Combined with the DummyClassifier gate (lines 682-688) which only rejects if ZERO models beat the dummy, the system can promote an ensemble where 10 out of 11 models are barely above random, and 1 model is slightly below random but has high variance that occasionally pushes accuracy above 50%.

The real issue: 50% accuracy on a class-imbalanced dataset (which this is -- Polymarket resolutions are often 70-80% YES) can be worse than just predicting the majority class. The DummyClassifier gate catches this only if literally zero models beat it by 1%, but a 51% model in a 70/30 split is much worse than a 70% majority-class predictor.

Additionally, the Brier rollback gate (lines 706-731) compares mean Brier across old vs new models, but only when old models exist. On the very first training, there are no old models, so the 50% accuracy gate is the only protection.

**Fix**: Raise the minimum accuracy threshold to be at least `max(0.55, dummy_accuracy + 0.02)` instead of a flat 0.50. Also change the DummyClassifier gate to require a majority (e.g., 6/11) of models to beat the dummy, not just 1/11. Consider using Brier score or log-loss instead of raw accuracy for the gate, as they better capture calibration quality.

---

## T10 -- Fallback Position Sizing in base_bot.py Ignores All Risk Limits

**Severity**: MEDIUM
**File**: `bots/base_bot.py` lines 289-307
**What's wrong**:
When `calculate_bot_position_size()` fails (any exception from risk_manager), it falls back to:
```python
except Exception as e:
    logger.warning("Risk manager failed, using default size", ...)
    base_size = 100.0
    return base_size * confidence
```

At confidence = 0.80, this returns $80. At confidence = 0.95, this returns $95. This bypasses:
- Position size percentage limits (`MAX_POSITION_SIZE_PCT`)
- USD hard caps (`RISK_MAX_POSITION_SIZE_USD = $1,000`)
- Calibration-aware sizing (Brier degradation)
- Kelly sizing
- Capital allocation per bot

While $80-95 is within the $1,000 USD cap, the fallback has no awareness of the current exposure, drawdown state, or number of open positions. If the risk manager fails persistently (e.g., DB connection issue), every bot on every scan cycle will use this fallback, accumulating unchecked exposure.

Worse, the `base_size = 100.0` is hardcoded -- not from settings. If capital settings change (e.g., `TOTAL_CAPITAL = $1,000` for a small account), the fallback still sizes at $80-95 per trade, potentially risking 8-10% of capital on a single trade when the intended max is 1%.

**Fix**: The fallback should return 0.0 (refuse to trade) rather than silently bypass risk limits. At minimum, cap the fallback at `min(100.0 * confidence, settings.RISK_MAX_POSITION_SIZE_USD * 0.1)` and log at ERROR level to ensure the risk manager failure is investigated.

---

## Summary Table

| ID  | Severity | File | Issue |
|-----|----------|------|-------|
| T1  | HIGH   | momentum_bot.py:111-116,331-335,495 | Uses BUY/SELL instead of YES/NO; SELL bypasses all risk checks |
| T2  | HIGH   | ensemble_bot.py:669-708 | optimize_weights() searches only 3 of 11 models, destroys weight config |
| T3  | HIGH   | risk_manager.py:384-433 | Position sizing ignores edge; sizes on confidence alone, Kelly off by default |
| T4  | MEDIUM | prediction_engine.py:1516,1751 | Prediction cache key missing token_id; YES/NO cross-contamination at price=0.50 |
| T5  | MEDIUM | arbitrage_bot.py:338-417 | price_fetched_at timestamp never validated; stale arb prices executed |
| T6  | MEDIUM | ensemble_bot.py:94-128 | Adaptive confidence oscillates with no smoothing, hysteresis, or recency filter |
| T7  | MEDIUM | momentum_bot.py:588-602 | Exit P&L uses tokens[0] price for all positions; wrong for NO-side positions |
| T8  | MEDIUM | order_gateway.py:95-109 | CVaR snapshot returns hardcoded side/size/price; undermines tail-risk gate |
| T9  | MEDIUM | prediction_engine.py:697-703 | 50% accuracy gate accepts trivially bad models on imbalanced data |
| T10 | MEDIUM | base_bot.py:289-307 | Fallback sizing bypasses all risk limits with hardcoded $100 base |
