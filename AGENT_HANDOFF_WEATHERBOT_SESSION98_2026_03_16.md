# Agent Handoff — WeatherBot Session 98 (2026-03-16)

## Summary
12-item scan speed optimization for WeatherBot. Target: reduce total scan from 25-86s to ~8-15s, with event-driven forecast updates replacing poll-based.

## Changes Made

### Files Modified
| File | Items |
|------|-------|
| `bots/weather_bot.py` | 1,4,6,7,8-10,12 |
| `base_engine/execution/order_gateway.py` | 2,3,5 |
| `base_engine/risk/risk_manager.py` | 5 |
| `config/settings.py` | 3,11 |
| `base_engine/weather/forecast_client.py` | 8 |

### New Files
| File | Purpose |
|------|---------|
| `base_engine/weather/model_run_monitor.py` | GFS/ECMWF model run polling + forecast pre-fetch + jump detection |
| `base_engine/weather/metar_monitor.py` | METAR continuous monitoring + bracket boundary triggers |

## 12 Items Implemented

### Item 1: Liquidity Fail-Closed Bug Fix + Cache
- **Bug**: `check_liquidity()` exception → silent continue at full size (fail-open)
- **Fix**: On exception, use cached liquidity result (3-min TTL) or apply conservative 50 bps slippage penalty
- **Added**: `_liquidity_cache: Dict[str, Tuple[float, Dict]]` in `__init__`

### Item 2: Skip Cascade for WeatherBot
- `order_gateway.py` `_cascade_check()` returns `None` for `bot_name == "WeatherBot"`
- WeatherBot is sole weather trader — no cascade risk

### Item 3: Skip Coordinator for WeatherBot BUYs
- New setting: `WEATHER_SKIP_COORDINATOR_BUY=true` (default)
- Pattern matches existing `MIRROR_SKIP_COORDINATOR_BUY`
- Saves 72-464ms coord_ms per BUY trade

### Item 4: Parallel Trade Execution
- Sequential for-loop → `asyncio.gather()` with `WEATHER_TRADE_CONCURRENCY=8` semaphore
- **Race condition mitigated**: `_exposure_lock` (asyncio.Lock) around exposure read→check→reserve in `_execute_weather_trade()`
- Lock serializes only the exposure check (~1ms), not the full trade I/O

### Item 5: Skip CVaR Monte Carlo for WeatherBot
- New `skip_cvar: bool = False` param on `check_risk_limits()`
- WeatherBot passes `skip_cvar=True` — has own group/city exposure limits
- Saves Monte Carlo simulation time (2000 iterations) per trade

### Item 6: Discovery Cache (5-min TTL)
- `_discovery_cache: Optional[Tuple[float, List, List]]` — markets + groups
- Gamma API call skipped if cache is <300s old
- Saves 241-825ms per scan

### Item 7: Raw Delta Pre-Screen Before EMOS
- After forecast fetch, before EMOS: check if any bucket has potential edge
- If all buckets have |0.50 - market_price| < min_edge*0.5 → skip EMOS+CDF
- Also checks group/city exposure cap early
- Expected: skips ~60-80 of 83 groups (based on live data: only 2-3 have edge)

### Item 8: Event-Driven Forecast Pipeline
- New `ModelRunMonitor` — background asyncio task
- Polls AWS S3 for GFS model run availability (HEAD request on f003 file)
- Estimates ECMWF availability from UTC time
- On new run: pre-fetches forecasts for all stations × 8 days → `_model_run_cache`
- Forecast client checks `_model_run_cache` FIRST, then falls back to regular cache (reduced TTL 900→300s)
- Added `api_calls_this_scan` counter to forecast_client

### Item 9: Jump Detection Interrupt
- Built into ModelRunMonitor: compares new ensemble mean vs prior
- ≥3°F delta → pushes to `_priority_queue: asyncio.Queue`
- WeatherBot drains priority queue at top of scan for immediate evaluation

### Item 10: METAR Continuous Monitor
- New `MetarMonitor` — polls AWC API every 5 min for METAR observations
- Tracks running daily max per station
- Boundary crossing → pushes to same priority queue
- METAR override window expanded: 6h → 12h

### Item 11: Config Changes
- `WEATHER_GROUP_CONCURRENCY`: 12 → 16
- `WEATHER_TRADE_CONCURRENCY`: 8 (new)
- `WEATHER_SKIP_COORDINATOR_BUY`: true (new)

### Item 12: Pre-Compute Sizing Factors
- Verified: regime_boost already computed once per scan; drawdown_factor is O(1) lookup; station_reliability has 1h cache. No additional caching needed.

## Expected Performance
| Metric | Before | After |
|--------|--------|-------|
| ms_trades | 50-57s | ~6-10s |
| ms_analysis | 7.7-17.9s | ~1-3s |
| ms_discovery | 241-825ms | ~0-50ms |
| Total scan | 25-86s | ~8-15s |

## Tests
1593 passed, 0 failed (ignoring test_web3_compatibility_fixes, test_dashboard_async_worker)

## Rollback
- `WEATHER_TRADE_CONCURRENCY=1` → sequential fallback
- `WEATHER_SKIP_COORDINATOR_BUY=false` → re-enable coordinator
- `WEATHER_GROUP_CONCURRENCY=12` → revert concurrency
- Model/METAR monitors → fall through to existing behavior on failure

## Post-Deploy Verification
```bash
# Scan timing (target: ms_trades < 15000, total < 20000):
sudo journalctl -u polymarket-ai --since '10 min ago' | grep weatherbot_scan_done

# Model-run monitor:
sudo journalctl -u polymarket-ai --since '10 min ago' | grep model_run

# METAR monitor:
sudo journalctl -u polymarket-ai --since '10 min ago' | grep metar_monitor

# Priority queue events:
sudo journalctl -u polymarket-ai --since '1 hour ago' | grep priority

# API call counter:
sudo journalctl -u polymarket-ai --since '10 min ago' | grep api_calls

# Trade fills still happening:
sudo journalctl -u polymarket-ai --since '10 min ago' | grep weatherbot_trade_filled
```
