# EsportsBot Audit Report — Session 127

**Date**: 2026-03-24
**Scope**: Full line-by-line audit of EsportsBot + all supporting modules
**Files audited**: `esports_bot.py`, `esports_live_bot.py`, `esports_game_monitor.py`, `esports_trainer.py`, `lol_win_model.py`, `dota2_model.py`, `cs2_economy_model.py`, `valorant_model.py`, `series_model.py`, `cot_validator.py`, `patch_drift.py`, `opendota_client.py`, `esports_data_collector.py`, `esports_db.py`

---

## BUGS

### BUG-24 — LoL Calibration Shape Mismatch (CRITICAL) [P0]
**File**: `esports/models/lol_win_model.py:108-109`
**What**: `CalibratedClassifierCV.predict_proba()` is called with `np.array([[proba]])` — shape `(1,1)`. But the calibrator was trained on 9-feature input from the XGBoost model, so it expects shape `(1,9)`. sklearn's `CalibratedClassifierCV` wraps the base estimator and re-runs `predict_proba` internally, which fails silently or returns the base model's default.
**Why it hurts**: **Every LoL prediction falls back to 0.5 when calibration is loaded.** The calibrator's `predict_proba()` receives a single scalar wrapped as a 2D array, which doesn't match the expected feature count. The except clause at line 112-113 catches the error and returns 0.5. This means the LoL model has NO edge — every trade is sized as a coin flip.
**Fix**: The calibrator should receive raw probabilities through `CalibratedClassifierCV(method='isotonic', cv='prefit')` fitted on `(raw_probs, labels)`. Then predict with `calibrator.predict_proba(np.array([[proba]]))` where the calibrator expects shape `(n,1)`. Alternatively, use `sklearn.isotonic.IsotonicRegression` directly:
```python
if self._calibrator is not None:
    proba = float(self._calibrator.predict([proba])[0])
```

### BUG-25 — Graduation Gate Hardcoded True [P2]
**File**: `esports/models/esports_trainer.py:284-285`
**What**: The graduation check that validates model quality before promoting to production is hardcoded to return `True`. Comment says "TODO: implement proper graduation criteria."
**Why it hurts**: Every trained model immediately graduates to production regardless of quality. A model with AUC=0.51 (barely above random) gets deployed. Combined with BUG-24, this means broken models are silently promoted.
**Fix**: Implement minimum thresholds: `return cv_auc >= 0.58 and n_samples >= 500`.

### BUG-6 — `list.pop(0)` O(n) in Event Queue [P3]
**File**: `esports_bot.py:683`
**What**: `self._pending_events.pop(0)` on a Python list is O(n) — it shifts every remaining element left by one position.
**Why it hurts**: With 200+ pending events during a busy esports night, each pop shifts 199 elements. Total cost is O(n²) for draining the queue.
**Fix**: Use `collections.deque` with `popleft()` — O(1) per operation.

### BUG-7 — `_market_token_map.clear()` WebSocket Blackout [P2]
**File**: `esports_bot.py:810-811`
**What**: When refreshing market token mappings, the entire map is cleared first, then rebuilt from API calls. During the rebuild (which involves network I/O), the map is empty.
**Why it hurts**: Any trade execution that runs concurrently during the refresh window cannot look up token IDs. Trades fail silently or use stale data. On a busy esports night, this 2-5 second blackout window can miss opportunities.
**Fix**: Build the new map into a local variable, then swap atomically: `self._market_token_map = new_map`.

### BUG-8 — Silent Exception in Prediction Logging [P3]
**File**: `esports_bot.py` — prediction log insert
**What**: The prediction logging `try/except` catches all exceptions and logs at `debug` level. If the DB is down or the schema changed, prediction logs silently stop recording.
**Why it hurts**: Prediction logging is essential for model validation and backtesting. Silent failures mean you discover months later that you have no data for a critical analysis period.
**Fix**: Log at `warning` level with `exc_info=True`.

### BUG-26 — Stale Match Detection Never Triggers [P2]
**File**: `esports/live/esports_game_monitor.py:173`
**What**: `self._last_score_update[mid] = time.monotonic()` is updated on EVERY poll, not just when the score changes. The stale detection checks `if time.monotonic() - self._last_score_update[mid] > STALE_THRESHOLD`, but since the timestamp is refreshed every poll cycle, it never exceeds the threshold.
**Why it hurts**: If PandaScore reports a match as "running" but scores are frozen (API bug, delayed data), the bot continues trading on stale game state. Live in-game models produce predictions based on outdated scores.
**Fix**: Only update `_last_score_update[mid]` when `cur_score != self._prev_scores.get(mid)`:
```python
if cur_score != self._prev_scores.get(mid):
    self._last_score_update[mid] = time.monotonic()
    self._prev_scores[mid] = cur_score
```

### BUG-27 — Dota2 Patch Detection False Positives [P3]
**File**: `esports/models/patch_drift.py:283-303`
**What**: Steam News API filter searches for "update" in title. Matches maintenance posts ("Steam Client Update"), community updates ("Dota 2 Community Update"), cosmetic patches, and unrelated announcements.
**Why it hurts**: False patch detection triggers 48h observation mode, freezing all Dota2 trading. With Steam publishing ~3 non-gameplay updates per week, the bot is in observation mode more than it trades.
**Fix**: Add stricter filters: require "gameplay" or "patch" or version number pattern (e.g., `7.37`). Exclude titles containing "client", "workshop", "community", "cosmetic".

### BUG-28 — CS2 Map Heterogeneity Ignored [P3]
**File**: `esports/models/cs2_economy_model.py:433-440`
**What**: For BO3/BO5 series predictions, the model averages individual map probabilities. But CS2 maps have vastly different team strengths (e.g., a team might be 80% on Mirage but 30% on Vertigo). Averaging treats maps as i.i.d.
**Why it hurts**: Series predictions are miscalibrated. A team that's heavily favored on 2 maps and heavily unfavored on 1 gets a moderate average, missing that they're likely to win 2-1.
**Fix**: Use the heterogeneous series model (`series_model.py`) which already supports per-map probabilities. Pass individual map probs instead of averaging.

### BUG-29 — CoT Validator Fail-Open at Debug Level [P3]
**File**: `esports/models/cot_validator.py:145-146`
**What**: When the chain-of-thought validator throws an exception, it logs at `debug` level and returns `True` (pass). This means ANY failure — malformed response, API timeout, invalid JSON — silently approves the prediction.
**Why it hurts**: The CoT validator exists to catch hallucinated or contradictory reasoning from the prediction pipeline. If it fails open, garbage predictions pass through unfiltered.
**Fix**: Log at `warning` level. Return `False` (fail-closed) on exception — better to skip a trade than to trade on garbage reasoning.

### BUG-30 — Team Name Substring False Match [P3]
**File**: `esports/data/opendota_client.py:209`
**What**: Team name matching uses `if team_name.lower() in hero_name.lower()` (or similar substring check). "og" matches "rogue", "bo" matches "betboom", "navi" matches "unavailable".
**Why it hurts**: Wrong team gets matched to Glicko-2 rating data. A tier-1 team's prediction uses a tier-3 team's Glicko-2 ratings, producing wildly miscalibrated probabilities.
**Fix**: Use exact match with normalization, or word-boundary regex: `re.search(r'\b' + re.escape(team_name) + r'\b', target, re.I)`.

---

## INEFFICIENCIES

### INEFF-3 — Module-Level Lock Contention [P4]
**File**: `esports_bot.py`
**What**: Several module-level asyncio locks are shared across all instances. In the current single-bot setup this is fine, but it means any future multi-instance test setup would deadlock.
**Why it hurts**: Not a production issue today. Technical debt for testability.
**Fix**: Move locks to instance variables in `__init__`.

---

## DATA FLOW ISSUES

### DATA-3 — No Calibrator Persistence [P3]
**File**: `esports/models/lol_win_model.py`, `esports_trainer.py`
**What**: The `CalibratedClassifierCV` calibrator is trained during `esports_trainer.py` runs but there's no evidence it's saved/loaded correctly alongside the base model. If the calibrator pickle is missing or mismatched, the model silently falls back to uncalibrated predictions.
**Why it hurts**: Even if BUG-24 is fixed, the calibrator may not load on restart. The model reverts to raw XGBoost probabilities which are systematically overconfident.
**Fix**: Save calibrator as part of the model pickle payload (same pattern as `mirror_ml_selector.py`). Add a logged warning when calibrator fails to load.

---

## LOGGING GAPS

### LOG-1 — PandaScore Circuit Breaker Missing [P3]
**File**: `esports/data/esports_data_collector.py`
**What**: PandaScore API calls retry on failure but have no circuit breaker. If PandaScore is down, every scan cycle attempts all API calls, gets timeouts, and wastes the entire scan budget.
**Why it hurts**: During a PandaScore outage (which happens ~1x/week), the bot's scan loop takes 60s+ instead of 5s. Live match updates are delayed for ALL games, not just the ones that failed.
**Fix**: Add a simple circuit breaker: after 3 consecutive failures, skip PandaScore calls for 5 minutes before retrying.

---

## RACE CONDITIONS

### RACE-2 — Bankroll Manager Private Attr Access [P4]
**File**: `esports_bot.py`
**What**: Several places access `self._bankroll_manager._available_capital` directly (private attribute) instead of going through the public API.
**Why it hurts**: If BotBankrollManager's internal representation changes, these accesses silently read stale or wrong values. Not a race condition per se, but a coupling violation that could produce incorrect sizing.
**Fix**: Use `self._bankroll_manager.get_available_capital()` everywhere.

---

## STORAGE CONCERNS

### STORE-2 — Queue maxsize=200 Drops Events [P4]
**File**: `esports/live/esports_game_monitor.py:179-182`
**What**: `self._queue.put_nowait(state)` drops events when the queue is full (maxsize=200). Only logged at `debug` level.
**Why it hurts**: During high-activity periods (major tournament with 10+ concurrent matches), game state updates are silently dropped. The live bot misses score changes and produces stale predictions.
**Fix**: Increase to 1000, or use an unbounded queue with a periodic size warning. Log drops at `warning` level.

---

## SUMMARY TABLE

| ID | Severity | Description | Est. Fix |
|----|----------|-------------|----------|
| BUG-24 | **P0** | LoL calibration shape mismatch | 30 min |
| BUG-7 | **P2** | Token map clear blackout | 10 min |
| BUG-25 | **P2** | Graduation gate always True | 10 min |
| BUG-26 | **P2** | Stale detection never fires | 5 min |
| BUG-6 | P3 | list.pop(0) O(n) | 5 min |
| BUG-8 | P3 | Prediction log silent fail | 5 min |
| BUG-27 | P3 | Patch detection false positive | 15 min |
| BUG-28 | P3 | CS2 map averaging | 20 min |
| BUG-29 | P3 | CoT fail-open | 5 min |
| BUG-30 | P3 | Team name substring match | 10 min |
| DATA-3 | P3 | Calibrator not persisted | 15 min |
| LOG-1 | P3 | No PandaScore circuit breaker | 20 min |
| INEFF-3 | P4 | Module-level locks | 10 min |
| RACE-2 | P4 | Private attr access | 10 min |
| STORE-2 | P4 | Queue drops at 200 | 5 min |

**Total bugs**: 10 | **Inefficiencies**: 1 | **Data flow**: 1 | **Other**: 3
**Critical P0/P2 fixes**: 4 items, ~55 min total
**Highest-impact fix**: BUG-24 (LoL calibration) — fixing this alone could recover all LoL edge that's currently being thrown away.
