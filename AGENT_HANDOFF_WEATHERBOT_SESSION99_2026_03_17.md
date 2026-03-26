# WeatherBot Session 99 Handoff — S99 Post-Deploy Tuning + Critical Cache Fixes

**Date**: 2026-03-17
**Branch**: master
**Commit**: `8595c06` (fix: two infinite-cache bugs + METAR batching + model-run cache TTL)
**Prior Session**: S98 (scan speed optimization, 12 items) + S99 plan (7 optimization items)
**Deploy**: `20260317_150800` (forecast_client.py hotfix)

---

## CRITICAL BUGS FOUND AND FIXED

### Bug 1: `_model_run_cache` Infinite TTL (S98 regression)
- **File**: `base_engine/weather/forecast_client.py`
- **Root cause**: S98 added `_model_run_cache` populated by ModelRunMonitor. Stored raw `CombinedForecast` with NO timestamp/TTL. Once populated, ALL subsequent `get_combined_forecast()` calls returned stale data forever.
- **Symptom**: `api_calls=0` on every scan from S98 deploy (~21:00 Mar 16) through hotfix (~03:52 Mar 17). Zero temperature trading for ~7 hours.
- **Fix**: Store `(mono_time, forecast)` tuples. Initially set 30-min TTL, then reduced to 5-min (see Bug 3).

### Bug 2: Regular `_cache` Never Expired
- **File**: `base_engine/weather/forecast_client.py` line 975
- **Root cause**: Cache stored `(expiry_time, forecast)` but the check did `(now_mono - cached[0]) < cache_ttl`. Since `cached[0]` is an expiry time in the future, `now - future` is always negative, always less than TTL. **Cache entries never expired.**
- **Symptom**: After model_run_cache fix, `api_calls` was still only 0-7 per scan (81 groups). Fresh forecasts almost never fetched.
- **Fix**: Changed to `now_mono < cached[0]` (correct expiry comparison). Immediately jumped to `api_calls=30-110`.
- **Note**: Other caches (precip, snowfall, wind) already used the correct `time.monotonic() < cached[0]` pattern. Only `get_combined_forecast()` was wrong.

### Bug 3: `_model_run_cache_ttl` Too Long (30 min)
- **File**: `base_engine/weather/forecast_client.py`
- **Root cause**: ModelRunMonitor populates cache for ALL stations simultaneously. 30-min TTL meant 15 consecutive scans used stale data.
- **Fix**: Reduced to 300s (5 min) — just long enough to deduplicate the ModelRunMonitor burst.

### All three bugs share the same class: S98 cache work introduced timing comparisons that never actually expire.

---

## S99 OPTIMIZATION ITEMS (7 items, all deployed)

| # | Item | File | Status |
|---|------|------|--------|
| 1 | Fill-failure cooldown per market | `bots/weather_bot.py` | Deployed |
| 2 | Fill probability floor (skip <0.25) | `bots/weather_bot.py` | Deployed |
| 3 | Discovery cache for precip/snow/wind | `bots/weather_bot.py` | Deployed |
| 4 | Precip/snow/wind every-other-scan | `bots/weather_bot.py` | Deployed |
| 5 | Adaptive scan interval (overnight backoff) | `bots/weather_bot.py` | Deployed, untested (restart loop) |
| 6 | METAR batch fix (20→50 stations) | `base_engine/weather/metar_monitor.py` | Deployed, verified |
| 7 | Discovery cache TTL time-of-day aware | `bots/weather_bot.py` | Deployed |

---

## METAR Monitor Improvements (Item 6)
- `us_stations[:20]` replaced with batched loop (3 batches of 20)
- `asyncio.wait_for(timeout=30s)` prevents indefinite blocking
- Jitter on sleep avoids scan loop collision
- Always emits `metar_poll_done` log with `batches` and `poll_ms` fields
- **Verified**: `batches=3, stations_polled=50, poll_ms=697ms`

---

## POST-FIX METRICS (15:30 UTC, US daytime)

| Metric | Pre-S99 (S98 broken) | Post-S99 (cache fix) |
|--------|---------------------|---------------------|
| `api_calls` | 0 | **19-110** |
| `groups_with_edge` | 0 | **3-6** |
| `trades/scan` | 0 | **5-7** |
| `ms_analysis` | 3-50ms (no work) | **3-42s** (real analysis) |
| `ms_precip_snow_wind` | 3-12s every scan | **0ms** on odd scans (every-other) |
| METAR stations_polled | 20 | **50** |
| METAR poll_ms | N/A | **697ms** |

---

## CONFIG ADDITIONS (`config/settings.py`)

```python
WEATHER_FILL_FAIL_COOLDOWN_SCANS = 2      # consecutive fails before cooldown
WEATHER_FILL_FAIL_COOLDOWN_SECS = 900     # 15 min cooldown after N fails
WEATHER_MIN_FILL_PROB_ESTIMATE = 0.25     # skip trades with <25% fill estimate
WEATHER_PSW_SCAN_DIVISOR = 2             # precip/snow/wind every Nth scan
WEATHER_ADAPTIVE_BACKOFF_THRESHOLD = 6    # consecutive no-edge scans before backoff
WEATHER_MAX_SCAN_INTERVAL = 600          # max scan interval in seconds
```

---

## ALL BOT HEALTH (15:44 UTC)

| Bot | Status | Key Metrics |
|-----|--------|-------------|
| WeatherBot | **Healthy** | api_calls=19-66, trades=5, groups_with_edge=3, 800 markets |
| MirrorBot | **Healthy** | scan_ms=1040, elites=500, open_positions=144, rtds=110K dispatched |
| EsportsBot | **Healthy** | 23 markets, 14 live, 1 opportunity, waterfall working |
| EsportsLiveBot | **Healthy** | scan_ms=0.3-12ms |

---

## KNOWN ISSUES

1. **Service restart loop**: Orphaned SSH background tasks from deploy commands caused `systemctl restart` every 2-3 min for ~1h. Stopped after SSH sessions timed out. Adaptive backoff counter (`_consecutive_no_edge`) resets on restart, so overnight backoff was never tested. **Monitor overnight.**

2. **Canary controller resets to stage 0 on restart**: Each restart triggers `CANARY_STAGE: 0 → 1`. Not a bug per se (designed to be cautious on restart) but wastes time ramping back up.

3. **432 temporal ordering violations**: `prediction_log` rows where `resolved_at < prediction_time`. Logged on every startup. Likely clock skew from historical data. Not affecting trading.

---

## ROLLBACK

```bash
# Disable S99 optimizations (without reverting code):
export WEATHER_PSW_SCAN_DIVISOR=1           # scan PSW every cycle
export WEATHER_ADAPTIVE_BACKOFF_THRESHOLD=999  # effectively disable backoff
export WEATHER_FILL_FAIL_COOLDOWN_SCANS=999   # effectively disable cooldown

# Full revert:
git revert 8595c06  # cache fixes
# Then redeploy
```

---

## FILES MODIFIED THIS SESSION

| File | Changes |
|------|---------|
| `base_engine/weather/forecast_client.py` | model_run_cache TTL (infinite→5min), regular cache expiry fix, model_run_cache stores tuples |
| `base_engine/weather/model_run_monitor.py` | Store `(time.monotonic(), forecast)` in cache |
| `base_engine/weather/metar_monitor.py` | Batch all stations, timeout, jitter, always-log |
| `bots/weather_bot.py` | Items 1-5, 7 (fill cooldown, fill floor, PSW cache, every-other-scan, adaptive backoff, TTL aware) |
| `config/settings.py` | 6 new WEATHER_ settings |

---

## VERIFICATION COMMANDS

```bash
# Scan health (api_calls should be 20-100+):
journalctl -u polymarket-ai --since '10 min ago' | grep weatherbot_scan_done

# METAR polls (batches=3, stations_polled=50):
journalctl -u polymarket-ai --since '30 min ago' | grep metar_poll_done

# Trade execution:
journalctl -u polymarket-ai --since '30 min ago' | grep 'Paper trade' | grep Weather

# Adaptive backoff (overnight — intervals should grow to 450-600s):
journalctl -u polymarket-ai --since '2h ago' | grep weatherbot_scan_done | grep -oP '\d{2}:\d{2}:\d{2}'

# Service stability (should be ONE PID):
journalctl -u polymarket-ai --since '30 min ago' | grep -oP 'polymarket-ai\[\d+\]' | sort -u
```
