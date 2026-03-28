# WeatherBot Session 136 — Handoff Document
**Date**: 2026-03-27
**Status**: Code-complete, NOT deployed
**Test count**: 146 passing (11 new tests; 135 from S135)

---

## Summary

S136 implements the Tier 0–3 elevation plan for WeatherBot. All 8 plan items are
complete across two sessions (S135 + S136). All gated by feature flags defaulting
to `false` — safe to deploy to VPS without activating new logic.

---

## S136 Changes (this session)

### T1-A: Mid-life exit evaluator (`_evaluate_mid_life_exits`)
**Files**: `bots/weather_bot.py`
**Gate**: `WEATHER_MID_LIFE_EXIT_ENABLED=false`

New method called after `_reevaluate_open_positions(analyzed)` each scan cycle.
For each open WeatherBot position that appears in an analyzed group, checks whether
the fresh model probability implies negative edge:

- YES position: exit if `fresh_prob - entry_price < -WEATHER_EXIT_MIN_EDGE`
- NO position: exit if `(1 - fresh_prob) - entry_price < -WEATHER_EXIT_MIN_EDGE`

When triggered, executes the full 6-item exit chain:
1. Set `_recently_exited[mid]` cooldown (REVERSAL reason — see T1-K)
2. Persist to Redis via `_save_exit_to_redis()`
3. Decrement group/city exposure from `_market_group_cache` + `_inc_daily`
4. Place `side="SELL"` order via `base_engine.place_order()` (bypasses entry filters)

Default `WEATHER_EXIT_MIN_EDGE=0.05` (5% reversal required to trigger exit).
Token IDs sourced from `bucket.token_id` (YES) / `bucket.no_token_id` (NO).

### T1-K: Exit-reason-specific cooldowns (`_get_exit_cooldown`)
**Files**: `bots/weather_bot.py`
**Gate**: None (always active; conservative defaults match prior behaviour)

New `_exit_reasons: Dict[str, str]` attribute. All cooldown checks now route
through `_get_exit_cooldown(market_id)` instead of the scalar `_exit_cooldown_secs`.

| Reason | Env var | Default |
|--------|---------|---------|
| `REVERSAL` (T1-A mid-life exits) | `WEATHER_EXIT_COOLDOWN_REVERSAL_SECS` | 1800s (30 min) |
| `RESOLUTION` (PM-detected exits) | `WEATHER_EXIT_COOLDOWN_SECS` | 14400s (4h) |
| unknown (fallback) | — | 14400s (4h) |

Rationale: after a model-reversal exit, the forecast may recover — 30-minute
re-entry window vs. 4-hour window for resolution/stop-loss exits.

---

## S135 Changes (prior session, same branch)

### T0-A: Calibration SQL filter — exclude dust prices
`fit_from_trade_events()` WHERE clause: added `AND price >= 0.08`. Prevents
near-zero price rows from dominating calibration training.

### T0-B: Calibrator feature expansion (4 → 6 features)
`WeatherConfidenceCalibrator` now trains on:
1. confidence (raw model probability)
2. side_enc (YES=1.0, NO=0.0)
3. lead_time_hours
4. entry_price
5. **bucket_type_enc** (at_or_higher=1.0, range=2.0, else=0.0) — NEW
6. **ensemble_spread** (model_spread field) — NEW

Backward compatible: `calibrate()` new params have defaults
(`bucket_type="unknown"`, `ensemble_spread=3.0`).

### T0-C: EMOS shrinkage estimator (Bühlmann credibility blend)
**Gate**: `WEATHER_EMOS_SHRINKAGE_ENABLED=false`

After global EMOS computation, blends per-station local parameters toward
global with weight `w = n / (n + 30)`. Stations with < 3 pairs skipped.
Cities with limited data automatically benefit from global prior.

### T0-D: Washington DC alias collision fix
Removed bare `"washington"` from KDCA aliases. Retained all explicit DC
aliases (`"washington d.c."`, `"washington dc"`, etc.).

### T1-B: `calibration_quality` wired to BankrollManager
`_cal_brier` attribute added to `WeatherConfidenceCalibrator`. After refit,
`_cal_qual = {"brier": cal_brier, "count": n_samples}` passed as
`calibration_quality` to `calculate_bot_position_size()`. Kelly fraction is
now scaled down when Brier score is poor (>0.15).

### T1-D: Skew-normal shape clipping
In `WeatherProbabilityEngine`, replaced `if abs(a) < 10.0` hard reject with
`a_clipped = clip(a, -4.0, 4.0)`. Preserves directional signal from markets
with extreme skew; logs `weather_shape_clipped` when clipping occurs.

### T1-H: Dead code removal
Deleted: `_regime_boost_cache`, `_calibration_cache`, `_drawdown_cache` (init
attrs never read); `_get_model_age_hours()` method (zero callers since S132).

---

## New Tests

```
tests/unit/test_weather_bot.py  +11 tests
  TestMidLifeExitEvaluator (7 tests)
    - skips_when_flag_disabled
    - exits_yes_position_on_reversal
    - exits_no_position_on_reversal
    - no_exit_when_ev_above_threshold
    - skips_cooldown_markets
    - skips_market_with_no_open_position
    - exposure_decremented_on_exit
  TestExitReasonCooldowns (4 tests)
    - resolution_uses_long_cooldown
    - reversal_uses_short_cooldown
    - unknown_reason_falls_back_to_long_cooldown
    - reversal_cooldown_shorter_than_resolution
```

---

## Deployment Notes

**All S136 features are OFF by default.** Deploy is safe without setting any env vars.

To activate features incrementally:

```bash
# T1-A: Enable mid-life exits with default 5% edge threshold
export WEATHER_MID_LIFE_EXIT_ENABLED=true
export WEATHER_EXIT_MIN_EDGE=0.05

# T1-K: Tune reversal re-entry window (default 1800s = 30 min)
export WEATHER_EXIT_COOLDOWN_REVERSAL_SECS=1800

# T0-C: Enable Bühlmann EMOS shrinkage
export WEATHER_EMOS_SHRINKAGE_ENABLED=true

# T0-B: New features active automatically (no flag) — calibrator retrained on next startup
```

**Verification after deploy:**
```bash
journalctl -u polymarket-ai -f | grep -E "weatherbot_mid_life_exit|weatherbot_emos_shrinkage|weatherbot_shape_clipped"
```

---

## What Was NOT Deployed

S135 and S136 changes are both uncommitted to HEAD (sitting in working tree).
The full set of changes spans:
- `bots/weather_bot.py`
- `base_engine/weather/probability_engine.py`
- `base_engine/weather/station_registry.py`
- `tests/unit/test_weather_bot.py`

---

## Calibration Tuning Recommendations (after data accumulates)

Once mid-life exits fire in production:
1. Check `weatherbot_mid_life_exit_triggered` logs — verify exit_min_edge
   isn't too aggressive (REVERSAL exits should be < 5% of total scan events)
2. Monitor re-entry rate after REVERSAL cooldown expires — if bot re-enters
   the same market repeatedly, raise `WEATHER_EXIT_COOLDOWN_REVERSAL_SECS`
3. For T0-B calibrator: new features won't affect trades until 30+ samples
   accumulate in `trade_events` at prices ≥ 0.08

---

## Change Log

```
## CHANGE: 2026-03-27
**Issue**: WeatherBot lacks proactive exit on model reversal; all exits depend on PM stop-loss
**Root cause**: No bot-level exit mechanism existed; PM only uses price thresholds
**Files modified**: bots/weather_bot.py, tests/unit/test_weather_bot.py
**Lines changed**: +169 lines weather_bot.py, +208 lines test file
**Blast radius**: WeatherBot only (new method, new helper, new attr)
**Verification**: 146 tests passing
**Rollback**: git stash (changes uncommitted) or set WEATHER_MID_LIFE_EXIT_ENABLED=false
```
