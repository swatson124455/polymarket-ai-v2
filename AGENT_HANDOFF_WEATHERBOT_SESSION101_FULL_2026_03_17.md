# WeatherBot Session 101 Complete Handoff — Full System Context for Agent Continuation

**Date**: 2026-03-17 (UTC evening)
**Branch**: master
**Commits this session**: `81d7b7b`, `2582baf`, `0721b10`, `653e6ff`
**Prior Sessions**: S99 (cache fixes), S100 (alpha decay, canary, SSH timeouts)
**Deploys**: `20260317_195848`, `20260317_201339`, `20260317_204424`, `20260317_204822`

---

## WHAT THIS BOT IS

WeatherBot is one of 15 bots in the `polymarket-ai-v2` automated trading system. It trades **temperature prediction markets** on Polymarket (a prediction market platform). The bot:

1. **Discovers** weather markets via Gamma API (`tag_slug=temperature`)
2. **Groups** markets by (city, date) — each group has ~11 temperature buckets ("42F or below", "between 43-44F", etc.)
3. **Forecasts** using NOAA GFS/ECMWF/HRRR ensemble data via Open-Meteo API
4. **Fits** EMOS (Error Modeling Output Statistics) + skew-normal distribution to compute bucket probabilities
5. **Compares** model probabilities vs market prices to find edges
6. **Sizes** bets via Kelly criterion with multiple adjustments (regime boost, NBM benchmark, expiry boost, tail discount)
7. **Executes** via paper trading engine (SIMULATION_MODE=true) — paper trades use realistic CLOB order book simulation

**P&L**: +$2,881 realized (932 closed trades, 62% win rate, 578W/354L). NO-side trades are the primary profit driver (+$1,896, 72% WR).

---

## COMPLETE FILE MAP (WeatherBot-relevant files)

### Core Bot
- `bots/weather_bot.py` (~4000 lines) — Main bot: scan_and_trade, _analyze_group, trade execution, discovery, all S101 changes
- `config/settings.py` — All WEATHER_* config keys

### Forecasting Pipeline
- `base_engine/weather/forecast_client.py` — Open-Meteo API client, ensemble fetching, caching (model_run_cache + regular cache)
- `base_engine/weather/probability_engine.py` — EMOS calibration, skew-normal fit, bucket probability integration
- `base_engine/weather/station_registry.py` — 102 WeatherStation entries with ICAO codes, coords, aliases. `lookup_station()` maps city text to station.
- `base_engine/weather/market_mapper.py` — Regex parsing of market questions, grouping by (city, date), `_last_unmatched_cities` for discovery

### Monitoring
- `base_engine/weather/model_run_monitor.py` — Background asyncio task detecting GFS/ECMWF model runs, jump detection (>=3F shift), parallel station sweep (S101)
- `base_engine/weather/metar_monitor.py` — METAR observation polling, batched (3x20 stations), daily max tracking

### Execution
- `base_engine/execution/paper_trading.py` — Realistic fill model, alpha decay, resolution proximity penalty, Kyle lambda slippage
- `base_engine/execution/order_gateway.py` — Routes orders, extracts `scan_start_mono` from event_data for latency computation

### Risk
- `base_engine/risk/bankroll_manager.py` — Kelly sizing per bot
- `base_engine/risk/liquidity_guardian.py` — CLOB order book depth checks

### Shared
- `base_engine/base_engine.py` — Base class for all bots
- `base_engine/data/database.py` — asyncpg database layer, trade_events insertion
- `base_engine/learning/scheduler.py` — Canary stage management (system_kv persistence)

---

## S101 CHANGES (this session, 4 commits)

### Commit 1: `81d7b7b` — 6 Elevation Fixes

| # | Change | File | Detail |
|---|--------|------|--------|
| 1 | Alpha decay 300s→1800s | weather_bot.py:~2350 | `"alpha_decay_half_life_s": 1800` in event_data. Weather signal valid ~6h, not 5min. |
| 2 | Fill cooldown 900s→120s | settings.py:685 | IOC gas ~$0.001/fail. 2 fails / 120s = 1 scan cycle. |
| 3 | Fill prob floor 0.25→0.15 | settings.py:687 | Pre-flight filter only, full model still gates. |
| 4 | Penny-bet 0.05→0.04 / 0.95→0.97 | weather_bot.py:~1795 | Tail buckets at 4c fillable on CLOB. Don't go to 0.03 until live. |
| 5 | Parallel model-run sweep | model_run_monitor.py:~159-205 | Batches of 20 via asyncio.gather. ~7s→~2-3s. |
| 6 | Expiry boost graduated | weather_bot.py:~2148 | <1h: 1.2x, 1-6h: 1.5x, 6-12h: 2.0x, 12-24h: 1.5x, else: 1.0x |

### Commit 2: `2582baf` — Pre-screening NoneType Hotfix

- `at_or_below` buckets have `low_bound=None`. Midpoint calculation crashed. Added guard + fallback.
- Pre-existing bug, triggered by UTC midnight new market data. 22 groups/scan affected.

### Commit 3: `0721b10` — City Discovery Logging + Gamma API Pagination

- **market_mapper.py**: Grouping drop counters (`weather_grouping_drops` INFO log), `_last_unmatched_cities` attribute
- **weather_bot.py**:
  - Paginated Gamma API (was `limit=100`, now loops up to 500 events)
  - `weatherbot_city_universe` log (sorted city list per discovery refresh)
  - `weatherbot_unmatched_cities` WARNING + alerting_system alert (deduped per session)
  - `weatherbot_daily_city_digest` (once per UTC day: active cities, unmatched, registry size, total markets)
  - `active_cities` field added to `weatherbot_scan_done`

### Commit 4: `653e6ff` — SCAN_MARKET_LIMIT 800→1500 + Milan Station

- Pagination revealed 1139 markets (was truncated to 800). Raised limit.
- Milan (LIML/Linate, MeteóFrance local model, °C) added to station_registry.

---

## KEY DISCOVERY: We Were Missing Half the Market

| Metric | Before S101b | After S101b |
|--------|-------------|-------------|
| weather_markets | 800 (capped) | **1139** |
| groups | 82 | **114** (+39%) |
| active_cities | 12 (visible) | **25** (actual) |
| unmatched_cities | unknown | **0** |
| pages_fetched | 1 | **2** (114 events) |

Cities we were missing: Ankara, Buenos Aires, Hong Kong, Lucknow, Madrid, Milan, Shanghai, Singapore, São Paulo, Taipei, Tel Aviv, Warsaw, Wellington. All had markets on Polymarket but the `limit=100` Gamma API cap prevented discovery.

---

## CURRENT CONFIG (verified live values)

```
WeatherBot:  capital=$20000, kelly=0.25, max_bet=$300, max_daily=$10000
             MAX_POSITIONS=500, MIN_EDGE=0.08 (US), 0.12 (intl w/o local model)
             FILL_FAIL_COOLDOWN_SCANS=2, FILL_FAIL_COOLDOWN_SECS=120 (S101)
             MIN_FILL_PROB_ESTIMATE=0.15 (S101)
             PSW_SCAN_DIVISOR=2, ADAPTIVE_BACKOFF_THRESHOLD=6, MAX_SCAN_INTERVAL=600
             MAX_PER_GROUP_USD=1000, DAILY_LOSS_LIMIT=2000, MAX_CORRELATED_EXPOSURE=2000
             SCAN_MARKET_LIMIT=1500 (S101b, was 800)
Paper:       REALISTIC_FILLS=true, KYLE_LAMBDA=true, CROSS_SCAN=true
             ALPHA_DECAY_HALF_LIFE_S=300 (global, overridden to 1800 for WeatherBot)
             RESOLUTION_PROXIMITY=true
```

---

## ARCHITECTURE DEEP DIVE

### Scan Flow (`scan_and_trade()`)
1. **Discovery** — Gamma API `tag_slug=temperature` (paginated, cached 5min)
2. **Grouping** — `market_mapper.group_markets()` → List[WeatherMarketGroup]
3. **Pre-fetch alerts** — NWS severe weather for US stations
4. **Parallel analysis** — `asyncio.Semaphore(12)` bounded concurrency per group
5. **Per-group analysis** (`_analyze_group`):
   - Fetch ensemble forecast (GFS+ECMWF via Open-Meteo)
   - EMOS calibration (station-specific bias correction)
   - Skew-normal fit → bucket probabilities
   - Edge = model_prob - market_price (YES) or (1-model_prob) - (1-market_price) (NO)
   - Min edge gate: 0.08 (US), 0.12 (intl without local model)
   - Pre-screening rough estimate (fast-reject if no bucket has edge potential)
6. **Sizing** — Kelly criterion × regime_boost × NBM_boost × expiry_boost × tail_discount
7. **Execution** — Paper trading via OrderGateway → paper_trading.py fill model
8. **Post-scan** — Adaptive backoff, PSW every-other-scan, fill cooldown tracking

### Key Sizing Factors
- **regime_boost** (1.0-1.3): Cross-city regime detection — if many cities show ensemble convergence, boost
- **nbm_boost** (1.0-1.5): NBM (National Blend of Models) benchmark — boost if NBM agrees with EMOS
- **expiry_boost** (1.0-2.0): Graduated by lead time (S101 change)
- **tail_discount** (0.5-1.0): Reduce sizing on extreme tail buckets
- **calibration_scaling** (0.8-1.2): Per-station CRPS calibration history

### Paper Trading Realism
- **Alpha decay**: Exponential signal deterioration. Half-life 1800s for WeatherBot (S101).
- **Kyle lambda slippage**: Order book impact model based on real CLOB depth
- **Resolution proximity**: <30min: 3.0x slippage, 0.5x fill probability. Compounded with expiry boost.
- **Realistic fills**: Walk the actual order book. Track fill_frac and vwap.

### Model Run Monitor
- Background asyncio task started on first WeatherBot scan
- Polls NOMADS/AWS for new GFS (6-hourly) and estimates ECMWF (12-hourly) runs
- On new run detection: pre-fetches all station×date pairs (batches of 20, parallel — S101)
- Jump detection: if ensemble mean shifts >=3°F between runs, pushes to priority queue
- WeatherBot consumes priority queue at start of each scan for immediate evaluation

### Station Registry
- 102 WeatherStation entries (97 original + 5 recently added including Milan)
- Each has: city_name, ICAO code, GHCND ID, lat/lon, timezone, temp_unit (F/C), aliases, local_model
- `lookup_station()`: exact alias match → word-boundary substring match → None
- `_ALIAS_MAP` built at import time from all station aliases

---

## KNOWN ISSUES (as of S101 end)

| Priority | Item | Notes |
|----------|------|-------|
| P2 | ~479 markets unresolved | Resolving naturally via backfill |
| P3 | NO vs YES asymmetry (72% vs 39% WR) | Monitor before config change |
| P3 | City/lead-time P&L data sparse | 905/932 closed are "unknown" (pre-metadata) |
| P3 | HRRR model run detection | Not monitored. Hourly 3km model. 2-3h effort. |
| P3 | MetarMonitor daily max Redis persistence | Lost on restart. 1h effort. |
| P4 | Ensemble member weighting (ECMWF > GFS > 72h) | Future session |
| P5 | Lower penny-bet to 0.03 | Only after live fill rate verification at 0.04 |
| P5 | 432 temporal ordering violations | Static, filtered, harmless |
| P5 | Kalshi cross-platform arbitrage | 8-16h effort, separate session |

---

## WHAT THE NEXT SESSION SHOULD DO

1. **Monitor S101b impact** — With 1139 markets (was 800) and 25 cities (was 12), expect significantly more trades. Watch for:
   - Trade volume increase
   - Milan's first trades
   - International city P&L (many now have local_model for better forecasts)

2. **Re-run city/lead-time P&L in 3-5 days** — More S101 trades will have resolved with metadata.

3. **NO vs YES deep dive** — If YES-side continues at 39% WR, consider raising YES min_edge to 0.10.

4. **HRRR model run detection** (P3) — 2-3h effort.

5. **MetarMonitor daily max Redis persistence** (P3) — 1h effort.

**Or**: Follow user instructions. Scope lock applies.

---

## CRITICAL TRAPS (from CLAUDE.md + session experience)

- **trade_events is P&L AUTHORITY** — never paper_trades
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL.
- **Alpha decay requires `scan_start_mono` in event_data**: Only WeatherBot passes it.
- **`_market_meta_cache` in MirrorBot**: 3-tuple. NEVER expand.
- **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
- **Python 3.13 scoping**: Local imports shadow top-level. Never import inside function if name used before.
- **`system_kv` table**: Generic KV store (migration 054). Canary stage persistence.
- **`traded_markets.bot_names`**: TEXT column, use `LIKE '%BotName%'`.
- **Paper trading IS production** — every feature must work identically when SIMULATION_MODE flips to false.
- **`group_markets()` now has `_last_unmatched_cities`** — read by WeatherBot for alerting.
- **SCAN_MARKET_LIMIT=1500** — raised from 800 in S101b. Universe is 1139 markets currently.
- **Gamma API pagination**: `_fetch_weather_events_by_tag()` now loops up to 5 pages of 100 events.

---

## VERIFICATION COMMANDS

```bash
# Full scan health:
journalctl -u polymarket-ai --since '10 min ago' | grep weatherbot_scan_done

# City universe (should be 25 cities):
journalctl -u polymarket-ai --since '30 min ago' | grep weatherbot_city_universe

# Unmatched cities (should be empty — if not, add to station_registry):
journalctl -u polymarket-ai --since '30 min ago' | grep weatherbot_unmatched_cities

# Daily digest:
journalctl -u polymarket-ai --since '24h ago' | grep weatherbot_daily_city_digest

# Grouping drops:
journalctl -u polymarket-ai --since '30 min ago' | grep weather_grouping_drops

# Alpha decay (decay_factor ~0.97-0.99):
journalctl -u polymarket-ai --since '10 min ago' | grep paper_alpha_decay

# All bot health:
journalctl -u polymarket-ai --since '5 min ago' | grep -E 'scan_done|scan_ms'
```

---

## TESTS

1,604+ passed (weather-specific: 149 passed). Two pre-existing failures in unrelated modules (dead dashboard test + esports edge cap behavior).

---

## ROLLBACK

```bash
# Revert all S101 + S101b:
git revert 653e6ff 0721b10 2582baf 81d7b7b

# Config-only rollback (no code revert):
export SCAN_MARKET_LIMIT=800
export WEATHER_FILL_FAIL_COOLDOWN_SECS=900
export WEATHER_MIN_FILL_PROB_ESTIMATE=0.25
```
