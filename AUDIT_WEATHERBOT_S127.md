# WeatherBot Audit Report — Session 127

**Date**: 2026-03-24
**Scope**: Full line-by-line audit of WeatherBot + all supporting modules
**Files audited**: `weather_bot.py`, `forecast_client.py`, `probability_engine.py`, `precipitation_engine.py`, `metar_monitor.py`

---

## BUGS

### BUG-17 — `asyncio.sleep(0, result=None)` Crashes on Python 3.13 (CRITICAL) [P0]
**File**: `base_engine/weather/forecast_client.py:1098, 1107`
**What**: `asyncio.sleep(0, result=None)` is used as a no-op coroutine placeholder for non-US stations (no NBM) and stations without a local model. The `result` parameter was **removed in Python 3.12** (deprecated since 3.8).
**Why it hurts**: On Python 3.13 (which you're running), this raises `TypeError: sleep() got an unexpected keyword argument 'result'` on EVERY forecast fetch for international stations. The `asyncio.gather()` at line 1109 catches it as an exception, so `nbm_high` and `local_high` become `TypeError` objects instead of `None`. Downstream code checks `isinstance(nbm_high, Exception)` and logs a warning but proceeds without NBM/local data — effectively all international stations lose their hi-res model corrections.
**Fix**: Replace with a simple async noop:
```python
async def _noop(): return None
nbm_task = self.get_nbm_forecast(...) if station.temp_unit.upper() == "F" else _noop()
local_task = self._fetch_local_model_forecast(...) if station.local_model else _noop()
```

### BUG-18 — JOIN on `condition_id` Instead of `id` [P2]
**File**: `bots/weather_bot.py:1045`
**What**: `JOIN markets m ON p.market_id = m.condition_id` — positions store `market_id` which could be either `markets.id` or `markets.condition_id`. This join only matches one of the two possible formats.
**Why it hurts**: When a position's `market_id` is the markets table `id` (UUID format) rather than `condition_id`, the JOIN fails. The fallback exit exposure calculation can't find the market question, can't extract city/date, and silently skips the exposure decrement. Over time, `_group_exposure` and `_city_exposure` leak upward, eventually blocking new trades in affected cities.
**Fix**: Use the same OR-JOIN pattern (or better, UNION ALL) that `elite_detector.py` uses:
```sql
JOIN markets m ON (p.market_id = CAST(m.id AS TEXT) OR p.market_id = m.condition_id)
```

### BUG-19 — GEFS Ensemble Member Count Assumption [P3]
**File**: `base_engine/weather/forecast_client.py:1185`
**What**: Code assumes GEFS returns exactly 31 ensemble members (1 control + 30 perturbations). Open-Meteo sometimes returns fewer members (network issues, partial data), and the code doesn't validate.
**Why it hurts**: When member count drops (e.g., 20 instead of 31), ensemble spread is underestimated. Probabilities for extreme events are biased high because the missing members were likely the outliers that got dropped.
**Fix**: Log a warning when member count < 25. Weight spread calculations by actual member count rather than assuming 31.

### BUG-20 — Precipitation Probabilities Not Normalized [P2]
**File**: `base_engine/weather/precipitation_engine.py:170-180`
**What**: Probability buckets use `if` instead of `elif`:
```python
if bucket.bucket_type == "at_or_below": ...
if bucket.bucket_type == "at_or_higher": ...
if bucket.bucket_type == "range": ...
```
Each member is checked against ALL bucket types. A member at 0.5 inches matches "at_or_below 1.0" AND "range 0.0-1.0" AND potentially "at_or_higher 0.0", getting counted multiple times. The probabilities are not normalized to sum to 1.0.
**Why it hurts**: Overlapping probability estimates. If buckets overlap (which they do in Polymarket temperature/precip markets), total probability > 1.0. The bot may see "edge" on multiple buckets simultaneously when in reality the probabilities should be exclusive. This leads to over-sizing across correlated positions.
**Fix**: Use `elif` chain. Or better: after computing raw counts, normalize: `total = sum(probs.values()); probs = {k: v/total for k,v in probs.items()}`.

### BUG-21 — Tail Threshold 5 vs Documented 50 [P3]
**File**: `base_engine/weather/probability_engine.py:361`
**What**: Code uses `if not points or len(points) < 5` as the cold-start threshold for tail calibration. The design doc says 50 resolved markets minimum for reliable calibration.
**Why it hurts**: With only 5 data points, the isotonic calibration curve is wildly noisy. Tail probability discounts swing between 0.5 and 1.0 based on 5 markets, introducing random variance into every extreme-weather prediction.
**Fix**: Change to `len(points) < 50` to match the documented requirement.

### BUG-10 — Tail Docstring Says 0.85, Code Uses 0.90 [P5]
**File**: `base_engine/weather/probability_engine.py:362`
**What**: Comment says "Less aggressive cold-start fallback (was 0.85)" but this was updated in S122. The doc/code mismatch is just a stale comment.
**Why it hurts**: Misleading for developers reading the code. Not a runtime issue.
**Fix**: Update docstring at line 355 to say 0.90.

### BUG-22 — `date.today()` Uses Local Time Not UTC [P3]
**File**: `base_engine/weather/metar_monitor.py:105`
**What**: `date.today().isoformat()` returns the LOCAL date on the VPS. Weather markets on Polymarket resolve on UTC dates.
**Why it hurts**: Between 00:00 UTC and the VPS timezone offset (Ireland = UTC+0 in winter, UTC+1 in summer), METAR observations for "today's" markets may query the wrong date. During BST (March-October), there's a 1-hour window where `date.today()` returns tomorrow's date relative to UTC.
**Fix**: `datetime.now(timezone.utc).date().isoformat()`.

### BUG-23 — Wind Variance Uses Population Formula [P3]
**File**: `bots/weather_bot.py` (wind variance calculation)
**What**: Wind speed variance is calculated with population standard deviation (dividing by N) instead of sample standard deviation (dividing by N-1). With small ensemble sizes (e.g., 5-10 members), this underestimates variance.
**Why it hurts**: Underestimated wind variance → overconfident wind probabilities → oversized positions on wind markets. The effect is ~10% for N=10, ~20% for N=5.
**Fix**: Use `np.std(values, ddof=1)` instead of `np.std(values)`.

### BUG — Cooldown Set on ENTRY Not EXIT [P2]
**File**: `bots/weather_bot.py`
**What**: The market cooldown timer starts when a position is opened, not when it's closed. The cooldown is meant to prevent re-entry after an exit (to avoid whipsawing). But because it starts at entry, by the time the position exits (hours/days later), the cooldown has already expired.
**Why it hurts**: Bot exits a position at a loss, then immediately re-enters the same market if conditions haven't changed. This is the classic whipsaw trap — you lose on the exit and re-enter at a worse price.
**Fix**: Set cooldown timestamp in the exit handler, not the entry handler.

### BUG — Discovery Cache Mutation by Reference [P3]
**File**: `bots/weather_bot.py`
**What**: Market discovery results are cached and returned by reference. Downstream code mutates the returned dict (adding computed fields). On the next cache hit, the mutated version is returned, potentially with stale computed fields from a previous scan.
**Why it hurts**: Stale discovery data on cache hits. Market metadata from a previous scan cycle leaks into the current cycle.
**Fix**: Return `copy.deepcopy(cached)` or better, cache immutable tuples/namedtuples.

### BUG — `_market_group_cache` Not Persisted [P3]
**File**: `bots/weather_bot.py`
**What**: `_market_group_cache` maps market IDs to their city/date groups. This is rebuilt from API calls on restart but takes several scan cycles to fully populate.
**Why it hurts**: For the first few scans after restart, group exposure tracking is incomplete. The bot may oversize into a single city/date group because it doesn't know the other positions in that group.
**Fix**: Persist to Redis on update, restore on startup (same pattern as `_recently_exited`).

### BUG — `_consecutive_losses` Not Persisted [P4]
**File**: `bots/weather_bot.py`
**What**: Consecutive loss counter resets to 0 on restart. If the bot was on a 5-loss streak, it restarts with full aggression.
**Why it hurts**: Restart clears loss-streak protection. If the losing streak is due to a systematic market condition (e.g., weather model bias), the bot re-enters aggressively into the same losing condition.
**Fix**: Restore from recent trade_events on startup (count consecutive losses from most recent trades).

---

## INEFFICIENCIES

### INEFF-4 — Session Recreation Race [P4]
**File**: `bots/weather_bot.py`
**What**: Multiple concurrent scan operations can trigger session pool exhaustion. When the pool is exhausted, a new session is created outside the pool, bypassing connection limits.
**Why it hurts**: Under pool pressure (many concurrent DB operations), temporary connections are created and not returned to the pool, leading to PostgreSQL connection count spikes.
**Fix**: Use `wait_for()` with timeout on session acquisition instead of creating out-of-pool connections.

### INEFF-5 — SQL f-string Injection Risk [P2]
**File**: `base_engine/weather/forecast_client.py:134`
**What**: SQL query uses f-string interpolation for table/column names. While the interpolated values come from config (not user input), this pattern is a code-review red flag and could become an injection vector if config sources change.
**Why it hurts**: Not exploitable today (values are from trusted config). But any future config source change (env vars from CI, user input) creates a SQL injection vulnerability.
**Fix**: Whitelist-validate table/column names before interpolation. Or use SQLAlchemy's `table()` and `column()` constructs.

---

## DATA FLOW ISSUES

### DATA-4 — Exposure Lock Race on Concurrent Exits [P3]
**File**: `bots/weather_bot.py:1055-1057`
**What**: The exposure decrement uses `async with self._exposure_lock`, but exit processing and new entry processing can run concurrently. If an exit decrements exposure while an entry is between its exposure check and its increment, the entry may see artificially low exposure and oversize.
**Why it hurts**: Rare but real: on scan boundaries, a just-exited market frees exposure, and a new entry sees the freed capacity before the exposure tracking is consistent. Can lead to brief over-exposure in a city/date group.
**Fix**: Acquire the lock for the entire check-then-act sequence in entry processing, not just the individual read/write.

---

## LOGGING GAPS

### LOG-3 — MetarMonitor Precision [P4]
**File**: `base_engine/weather/metar_monitor.py`
**What**: METAR temperature parsing uses 1-degree precision from the main body of the METAR report. The T-group (remarks section) provides 0.1-degree precision when available, but it's not parsed.
**Why it hurts**: For temperature markets where the threshold is between integer degrees (e.g., "above 72°F"), 1-degree precision means the METAR observation can't distinguish 72.3 from 72.7, potentially causing wrong resolution signals.
**Fix**: Parse the T-group (`Txxxxxxxx` in remarks) when present. Fall back to integer precision only when T-group is absent.

### LOG — Redis Key Parsing Fragility [P4]
**File**: `bots/weather_bot.py` (Redis restore)
**What**: Redis keys for `_recently_exited` are parsed by string splitting on `:`. If any market ID contains `:` (unlikely but possible), the parsing breaks silently.
**Why it hurts**: Low risk. Polymarket IDs are hex/numeric and don't contain colons. But the pattern is fragile.
**Fix**: Use a separator that can't appear in IDs (e.g., `||`) or JSON-encode the key.

---

## SUMMARY TABLE

| ID | Severity | Description | Est. Fix |
|----|----------|-------------|----------|
| BUG-17 | **P0** | asyncio.sleep Py3.13 crash | 5 min |
| BUG-18 | **P2** | JOIN wrong column | 10 min |
| BUG-20 | **P2** | Precip probs not normalized | 10 min |
| BUG-COOL | **P2** | Cooldown on entry not exit | 15 min |
| INEFF-5 | **P2** | SQL f-string injection risk | 15 min |
| BUG-19 | P3 | GEFS member count | 10 min |
| BUG-21 | P3 | Tail threshold 5 vs 50 | 2 min |
| BUG-22 | P3 | date.today() not UTC | 5 min |
| BUG-23 | P3 | Wind variance population | 5 min |
| BUG-DISC | P3 | Discovery cache mutation | 10 min |
| BUG-GRP | P3 | _market_group_cache not persisted | 15 min |
| DATA-4 | P3 | Exposure lock race | 15 min |
| BUG-LOSS | P4 | _consecutive_losses reset | 10 min |
| INEFF-4 | P4 | Session creation race | 10 min |
| LOG-3 | P4 | METAR 1-degree precision | 20 min |
| LOG-REDIS | P4 | Redis key parsing fragile | 5 min |
| BUG-10 | P5 | Docstring 0.85 vs 0.90 | 2 min |

**Total bugs**: 12 | **Inefficiencies**: 2 | **Data flow**: 1 | **Other**: 2
**Critical P0/P2 fixes**: 5 items, ~55 min total
**Highest-impact fix**: BUG-17 (asyncio.sleep) — all international stations are silently losing NBM/local model corrections right now.
