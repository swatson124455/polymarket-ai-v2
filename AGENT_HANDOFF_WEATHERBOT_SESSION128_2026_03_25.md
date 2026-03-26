# WeatherBot Handoff — Session 128 (2026-03-25)

## Summary
8 bugs fixed from S127 audit (`AUDIT_WEATHERBOT_S127.md`). All verified against actual code before fixing. 1 audit false positive identified (DATA-4 exposure lock race — lock is correctly applied everywhere).

## Current State
- **All-time P&L**: +$3,401 (2,013 resolutions, 61.5% WR)
- **Last 18h**: +$341 (35 resolutions, 74.3% WR)
- **Open positions**: ~52
- **Spread inflation**: Active since S126 deploy (BASE=0.15, FACTOR=0.05, 10%/day decay)
- **Tests**: 135 + 42 passed (test_weather_bot + test_weather_cold_start)

## Fixes Applied (8)

### Fix 1: BUG-17 — asyncio.sleep Py3.13 crash [P0]
**File**: `base_engine/weather/forecast_client.py:1098,1107`
**Was**: `asyncio.sleep(0, result=None)` — `result=` param removed in Py3.12
**Now**: `asyncio.sleep(0)` — returns None, valid on all Python versions
**Impact**: All ~20 international stations were silently losing NBM/local model corrections

### Fix 2: BUG-COOL — Cooldown on ENTRY not EXIT [P2]
**File**: `bots/weather_bot.py:2846-2851`
**Was**: `_recently_exited[market_id]` set at ENTRY time + saved to Redis. 4h cooldown started when position opened, expired before exit.
**Now**: Removed entry-side set. Exit-side set at line 1015-1016 (PM exit detection, added S104) is the sole cooldown source.
**Impact**: Whipsaw protection now works — cooldown starts when position actually closes

### Fix 3: BUG-22 — date.today() not UTC [P3, ACTIVE in BST]
**File**: `base_engine/weather/metar_monitor.py:105,186,218,232`
**Was**: `date.today()` — returns VPS local date (UTC+1 during BST, which is NOW)
**Now**: `datetime.now(timezone.utc).date()` — always UTC
**Impact**: 1-hour midnight window where METAR observations were attributed to wrong day is eliminated

### Fix 4: BUG-18 — JOIN on condition_id instead of id [P2]
**File**: `bots/weather_bot.py:1046,3357`
**Was**: `JOIN markets m ON p.market_id = m.condition_id` — fails when position's market_id is UUID format
**Now**: `JOIN markets m ON (p.market_id = CAST(m.id AS TEXT) OR p.market_id = m.condition_id)` — matches both formats
**Impact**: Exit exposure fallback AND startup cache rebuild now find markets regardless of ID format. Stops exposure leak.
**Locations**: Exit fallback (line 1046) + `_rebuild_market_group_cache` (line 3357)

### Fix 5: BUG-20 — Precip probabilities not normalized [P2]
**File**: `base_engine/weather/precipitation_engine.py:174,177`
**Was**: `if`/`if`/`if` — ensemble member counted in multiple bucket types simultaneously
**Now**: `if`/`elif`/`elif` — each member counted in exactly one bucket type
**Impact**: Overlapping probability > 1.0 eliminated. No more phantom edge on correlated buckets.

### Fix 6: BUG-21 — Tail calibration threshold 5 vs 50 [P3]
**File**: `base_engine/weather/probability_engine.py:361`
**Was**: `len(points) < 5` — isotonic calibration on 5 data points = noise
**Now**: `len(points) < 50` — requires 50 resolved tail events for stable calibration
**Also**: Fixed stale docstring (0.85 → 0.90)
**Impact**: More conservative tail probabilities until sufficient data accumulates. Safer.

### Fix 7: BUG-23 — Wind variance population formula [P3]
**File**: `bots/weather_bot.py:1738`
**Was**: `/ len(ensemble)` (population variance, biased)
**Now**: `/ (len(ensemble) - 1)` (sample variance, unbiased). Guard `len(ensemble) > 1` already exists at line 1737.
**Impact**: Wind spread ~5-10% wider. Slightly more conservative wind positions.

### Fix 8: BUG-DISC — Discovery cache mutation by reference [P3]
**File**: `bots/weather_bot.py:1114`
**Was**: `weather_markets, groups = self._discovery_cache[1], self._discovery_cache[2]` — shared references
**Now**: `copy.deepcopy(...)` on both. Import added at line 24.
**Impact**: Prevents stale computed fields from one scan cycle leaking into next via cache mutation.

## Files Modified
1. `base_engine/weather/forecast_client.py` — Fix 1
2. `bots/weather_bot.py` — Fixes 2, 4, 7, 8
3. `base_engine/weather/metar_monitor.py` — Fix 3
4. `base_engine/weather/precipitation_engine.py` — Fix 5
5. `base_engine/weather/probability_engine.py` — Fix 6

## Audit False Positive
- **DATA-4 (Exposure lock race)**: `_exposure_lock` is correctly acquired in ALL entry, exit, and fallback paths. No race condition exists. No fix needed.

## Deferred Items (Next Session)

| # | Bug | Priority | Description | Fix |
|---|-----|----------|-------------|-----|
| BUG-19 | P3 | GEFS member count assumption | Hardcoded 31/82 thresholds. Log warning when `< 25`, weight spread by actual count. |
| BUG-GRP | P3 | `_market_group_cache` not persisted | Startup rebuild covers open positions. Mid-session exit loss is rare. Add Redis persistence if exposure leaks observed. |
| BUG-LOSS | P4 | `_consecutive_losses` not persisted | Resets to 0 on restart (1h backfill window too short). Restore from recent `trade_events` on startup. |
| INEFF-4 | P4 | Session pool exhaustion | No direct evidence. Use `wait_for()` with timeout instead of out-of-pool connections. |
| INEFF-5 | P4 | SQL f-string in forecast_client.py:134 | Safe (int cast) but anti-pattern. Parameterize if config sources change. |
| LOG-3 | P4 | METAR 1-degree precision | Parse T-group from remarks for 0.1-degree resolution on boundary markets. |
| LOG-REDIS | P4 | Redis key parsing fragility | Zero practical risk (hex IDs don't contain `:`). Use `||` separator if paranoid. |
| BUG-10 | P5 | Stale docstring | Trivial comment fix. |

## Known Issue: Empty event_data on ENTRY trades
Pre-existing (not from S127 audit): Post-S126 ENTRY events have NULL confidence and empty event_data (no city, lead_time_hours). 9 entries affected since 2026-03-24 20:00 UTC. Shadow entries DO have event_data. Investigate whether `_place_trade()` or `insert_trade_event()` is dropping the event_data dict for ENTRY events.

## Monitoring
- Spread inflation: 4,468 shadow entries post-activation (18h). Avg gap narrowed -0.0648 → -0.0587. Working as intended.
- Lead-time WR: `<24h=-$204`, `24-48h=-$601`, `48-72h=+$631`, `72-120h=+$1,662`. Short lead times remain negative.
- 35-city freeze: P3 outstanding from S126. Cities not expanding despite `unmatched_cities=[]`.

## Deploy
Not yet deployed. Commit and deploy when ready:
```bash
cd /opt/polymarket-ai && git pull && sudo systemctl restart polymarket-ai
journalctl -u polymarket-ai -f | grep -i "weatherbot\|forecast"
# Verify: no TypeError in forecast logs (BUG-17 fix)
# Verify: no date.today() related warnings around midnight BST
```
