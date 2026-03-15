# AGENT HANDOFF — WeatherBot Session 85 (2026-03-13)
# COMPLETE CARBON-COPY: All Files, Logic, Vision, Plans, Learnings, Functions

**This document is a full-context transfer. A new agent reading ONLY this file + CLAUDE.md should be able to continue ALL WeatherBot work seamlessly.**

**Session scope**: WeatherBot ONLY. Do not modify other bot files unless manually demanded.
**Previous handoffs**: `AGENT_HANDOFF_WEATHERBOT_SESSION81_2026_03_12.md`, `WEATHERBOT_FULL_AGENT_HANDOFF.md` (Sessions 53-69)
**Latest WeatherBot commit**: `02ea94a perf(weather): parallelize scan init + bound NWS alert concurrency`
**Test suite**: 1446+ passed, 6 skipped, 0 failures
**Current date**: 2026-03-13

---

## TABLE OF CONTENTS
1. [What WeatherBot Is](#1-what-weatherbot-is)
2. [Infrastructure & Deploy](#2-infrastructure--deploy)
3. [File Map & Architecture](#3-file-map--architecture)
4. [Core Data Flow — Complete Scan Cycle](#4-core-data-flow--complete-scan-cycle)
5. [Market Type Coverage](#5-market-type-coverage)
6. [Key Interfaces & Signatures](#6-key-interfaces--signatures)
7. [Configuration — Live VPS Values](#7-configuration--live-vps-values)
8. [DB Schema](#8-db-schema)
9. [Calibration & Learning Systems](#9-calibration--learning-systems)
10. [State Persistence (Cross-Restart)](#10-state-persistence-cross-restart)
11. [BUY/SELL vs YES/NO — How the System Works](#11-buysell-vs-yesno--how-the-system-works)
12. [Feature Inventory — All Implemented](#12-feature-inventory--all-implemented)
13. [Traps, Gotchas & Critical Patterns](#13-traps-gotchas--critical-patterns)
14. [Session History (53–84)](#14-session-history-5384)
15. [Outstanding Items / Roadmap](#15-outstanding-items--roadmap)
16. [Diagnostic & Verification Commands](#16-diagnostic--verification-commands)
17. [Development Rules (CLAUDE.md Summary)](#17-development-rules)
18. [Session Scope Rule](#18-session-scope-rule)

---

## 1. WHAT WEATHERBOT IS

**WeatherBot** is one of 5 active bots in a 14-bot automated Polymarket trading system. Currently in **paper trading mode** (`SIMULATION_MODE=true`). Real capital is NOT at risk yet — all trades are simulated via `PaperTradingEngine`.

### The Strategy
1. Finds markets: "Will the highest temperature in NYC be between 72-75°F on March 10?"
2. Fetches 133-member ensemble forecasts (GEFS 31 + ECMWF IFS 51 + ECMWF AIFS 51) via Open-Meteo
3. Fits a skew-normal distribution; integrates probability across each bucket's bounds
4. Compares model_prob vs market-implied probs (YES prices)
5. Trades when edge >= min_edge (per-type: 8-12%), sized by fractional Kelly criterion
6. Multi-bucket awareness: each city+date has ~7 bucket markets analyzed as a group

### The Edge
- Retail bettors overweight extreme buckets (favourite-longshot bias)
- Market prices lag NWP model updates by 30-60 minutes
- Resolution-day outcomes can be front-run via METAR aviation observations 1-5 min post-observation

### Active Bots (system context)
| Bot | Status | P&L |
|-----|--------|-----|
| **WeatherBot** | Active | +$461.74 paper (140 resolved) |
| MirrorBot | Active (RTDS live) | +$230.59 paper (14 resolved). ~199 open positions |
| EsportsBot | Active | — |
| EsportsLiveBot | Active | — |
| EsportsSeriesBot | Active | — |
| 9 others | Disabled | BOT_ENABLED_* flags off |

---

## 2. INFRASTRUCTURE & DEPLOY

### VPS
- **Host**: Ubuntu-3, 34.251.224.21, 16GB/4vCPU, eu-west-1 (Dublin)
- **SSH**: `ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21`
- **Service**: `sudo systemctl restart polymarket-ai`
- **Logs**: `journalctl -u polymarket-ai -f | grep weatherbot`
- **DB**: PostgreSQL, localhost, user=polymarket, db=polymarket
- **VPS path**: `/opt/polymarket-ai-v2` -> symlink to latest in `/opt/pa2-releases/`
- **Shared state**: `/opt/pa2-shared/{data,saved_models,venv}`

### Deploy
```bash
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
# Rollback:
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```
Atomic symlink swap. Migrations run automatically during deploy step 4. Health check 90s timeout.

---

## 3. FILE MAP & ARCHITECTURE

### Core Bot Files
```
bots/
  weather_bot.py              -- Main bot: scan_and_trade, 4 market type scanners, trade execution (3652 lines)
  base_bot.py                 -- BaseBot ABC (place_order, scan loop, heartbeat) (938 lines)
  llm_forecaster_bot.py       -- LLM-based forecasting variant (292 lines)
```

### Weather Prediction Engine (`base_engine/weather/`)
```
  forecast_client.py          -- Open-Meteo ensemble + NWS NBM + climate normals + precip/snow/wind (1261 lines)
  metar_client.py             -- Aviation Weather Center METAR API (running daily max) (220 lines)
  probability_engine.py       -- Skew-normal fit, EMOS calibration, bucket integration, Kelly (439 lines)
  precipitation_engine.py     -- Gamma distribution fit, P(rain) x P(amount|rain) (231 lines)
  market_mapper.py            -- Regex -> 4 market types: Temp/Precip/Snow/Wind buckets + groups (1105 lines)
  station_registry.py         -- ICAO->city registry, 30+ stations, lookup_station() (1340 lines)
```

### Shared Infrastructure (WeatherBot depends on all of these)
```
base_engine/execution/
  order_gateway.py            -- Position tracking, per-bot exposure, paper trade recording (947 lines)
  paper_trading.py            -- Paper trade engine (SELL skip at source)
  position_manager.py         -- Exit logic: stop-loss, take-profit, model reversal, hold-to-resolution (847 lines)

base_engine/risk/
  bankroll_manager.py         -- Per-bot Kelly sizing (WeatherBot: capital=5000, max_bet=500) (255 lines)
  risk_manager.py             -- Position limits, margin checks, exposure gates (962 lines)
  liquidity_guardian.py       -- Order book slippage estimation

base_engine/data/
  database.py                 -- PostgreSQL session mgmt, asyncpg pool, all queries (5488 lines)
  resolution_backfill.py      -- Phase 2a traded_markets + 2b on-chain resolution pipeline

config/
  settings.py                 -- All settings (WEATHER_* env vars) (1123 lines)

main.py                       -- BOT_REGISTRY (14 bots), entry point (722 lines)
```

### Database Migrations (WeatherBot-related)
```
schema/migrations/
  022_weather_tables.sql       -- weather_forecasts, weather_observations tables
  032_weather_calibration_crps.sql -- CRPS column on weather_calibration
  033_weather_tail_calibration.sql -- weather_tail_calibration table
  037_weather_calibration_regime.sql -- Regime-aware calibration
  040_weather_category_params_seed.sql -- Per-type min_edge seeds
  041_paper_trades_constraints.sql -- UNIQUE + CHECK constraints
  042_traded_markets.sql       -- traded_markets table + partial index + seed
```

### Tests
```
tests/unit/
  test_weather_bot.py          -- Comprehensive WeatherBot unit tests (1530 lines)
  test_chronos_forecaster.py   -- Chronos time-series tests (68 lines)
```

### Import Graph
```
weather_bot.py
  +-- base_bot.py (BaseBot ABC)
  +-- forecast_client.py (WeatherForecastClient, CombinedForecast)
  +-- metar_client.py (MetarClient)
  +-- probability_engine.py (WeatherProbabilityEngine)
  +-- precipitation_engine.py (PrecipitationProbabilityEngine)
  +-- market_mapper.py (WeatherMarketMapper + 8 dataclasses)
  +-- station_registry.py (StationHealthMonitor, US_CITY_NAMES, WeatherStation)
  +-- bankroll_manager.py (BotBankrollManager)
  +-- risk_manager.py (RiskManager)
  +-- liquidity_guardian.py (LiquidityGuardian)
  +-- paper_trading.py (PaperTradingEngine)
  +-- position_manager.py (PositionManager)
  +-- settings.py (settings)
```

---

## 4. CORE DATA FLOW — COMPLETE SCAN CYCLE

```
scan_and_trade()
  |
  +- 1. _handle_daily_boundary() -- reset P&L at UTC midnight
  +- 2. _restore_daily_pnl_from_db() -- refresh intra-day P&L every scan
  +- 3. _maybe_reload_calibration() -- every 6h: EMOS + bias + tail cal from DB
  +- 4. _load_category_params() -- per-type min_edge from bot_category_params (first scan)
  +- 5. _check_monitoring_thresholds() -- every 10min: Brier/drawdown halt + Kelly graduation
  +- 6. Detect PM exits -> add to _recently_exited (15min cooldown) + Redis
  +- 7. One-time startup: restore forecast cache, exits from Redis, exposure from DB, close stale
  |
  +- 8. MARKET DISCOVERY (primary -> fallback -> last resort):
  |     a) _fetch_weather_events_by_tag(tag_slug="temperature") <- Gamma API (PRIMARY)
  |     b) get_all_tradeable_markets(weather) <- DB category
  |     c) _fetch_weather_markets_direct() <- DB+API fallback (30min rate limit)
  |
  +- 9. _market_mapper.group_markets() -> List[WeatherMarketGroup] (by city+date)
  |
  +- 10. PHASE 1: For each WeatherMarketGroup -> _analyze_group()
  |     +- Skip: past dates, lead_time > 168h, station unhealthy
  |     +- get_combined_forecast() -> CombinedForecast (133 members, 3600s cache)
  |     +- _save_forecast_to_db() -> weather_forecasts + weather_calibration rows
  |     +- fit_distribution() -> (loc, scale, shape) skew-normal via MLE
  |     +- apply_climate_prior() -- blend 0-40% at >72h lead time
  |     +- AFD spread adjustment (NWS Area Forecast Discussion)
  |     +- bucket_probabilities() -> {market_id: prob}
  |     +- METAR override (<6h lead_time -- resolution day only)
  |     |   +- <2h: range boost 0.92, tighter margins 0.5F/0.3C
  |     +- M7 coherence check (>50% buckets need prices)
  |     +- compute_edges() -> [{market_id, edge, side, abs_edge, model_prob}]
  |     +- Filter: min_edge -> graduated edge cap -> cooldown -> penny filter
  |         -> position check -> boundary risk (0.5F/0.3C)
  |         -> confidence: YES=model_prob, NO=1-model_prob (halved if boundary_risk)
  |
  +- 11. PHASE 2: _compute_regime_boost() -> 1.2x if >=3 US cities unanimous
  |
  +- 12. PHASE 3: Execute trades
  |     +- >=2 opps in group -> _execute_group_trades() (S-T multi-bucket sizing)
  |     +- 1 opp -> _execute_weather_trade() (independent Kelly)
  |         +- Daily loss limit check (self.bankroll.capital)
  |         +- City/date group exposure check (WEATHER_MAX_PER_GROUP_USD=1000)
  |         +- Bot-scoped total exposure (WEATHER_MAX_TOTAL_EXPOSURE_USD=50000)
  |         +- Expiry boost: <12h=2.0x, <24h=1.5x, <48h=1.2x
  |         +- Regime boost: 1.2x if detected
  |         +- Severe weather boost: 2.0x hurricane, 1.5x tornado/blizzard
  |         +- Combined boost (additive, capped 2.0x) x Baker-McHale k*
  |         +- Slippage check (LiquidityGuardian)
  |         +- Kelly sizing via BotBankrollManager ($500 max, $2K daily)
  |         +- place_order() -> risk_manager checks -> paper_trading
  |         +- _log_weather_prediction() -> prediction_log table (wired Session 81)
  |
  +- 13. PHASE 4: _reevaluate_open_positions() -> fresh probs for exit logic
  |
  +- 14. PHASE 5 (every 10 scans): backfill outcomes, check EMOS drift, close stale
  |
  +- 15. PHASE 6: Parallel precip/snowfall/wind scanning
  |     +- _scan_precipitation_markets() (Gamma CDF, monthly/daily, NDFD PoP blend)
  |     +- _scan_snowfall_markets() (Gamma CDF, cm->inches)
  |     +- _scan_wind_markets() (Normal CDF via math.erf, km/h->mph)
  |
  +- 16. Log diagnostics
```

---

## 5. MARKET TYPE COVERAGE

### Temperature (tag_slug=temperature) — PRIMARY, ACTIVELY TRADING
- **Regex**: `_RE_RANGE`, `_RE_AT_OR_BELOW`, `_RE_AT_OR_HIGHER`, `_RE_EXACT`
- **Format**: "Will the highest temperature in CITY be between X-Y°F on DATE?"
- **Dataclasses**: `TemperatureBucket`, `WeatherMarketGroup`
- **Distribution**: Skew-normal (scipy `skewnorm`)
- **Ensemble**: 133 members (GEFS 31 + ECMWF IFS 51 + ECMWF AIFS 51) via `get_combined_forecast()`
- **Min edge**: 0.08

### Precipitation (tag_slug=precipitation) — ACTIVE, SCANNING
- **Regex V1** (hypothetical format): `_RE_PRECIP_RANGE`, `_RE_PRECIP_AT_OR_BELOW`, `_RE_PRECIP_AT_OR_HIGHER`
- **Regex V2** (actual Polymarket format): `_RE_PRECIP_RANGE_V2`, `_RE_PRECIP_BELOW_V2`, `_RE_PRECIP_HIGHER_V2`
  - V2: "Will CITY have between X and Y inches of precipitation in MONTH?"
- **Dataclasses**: `PrecipitationBucket`, `PrecipitationMarketGroup` (has `.period` = "daily" or "monthly")
- **Distribution**: Gamma (via `PrecipitationProbabilityEngine`)
- **Monthly ensemble**: `get_monthly_precipitation_ensemble()` = archive actuals (elapsed days) + ensemble (remaining)
- **Daily**: `get_precipitation_ensemble()` + `get_ndfd_pop()` (40% blend)
- **Min edge**: 0.10

### Snowfall (tag_slug=snowfall) — READY, NO ACTIVE MARKETS
- **Regex**: `_RE_SNOW_RANGE_V2`, `_RE_SNOW_BELOW_V2`, `_RE_SNOW_HIGHER_V2`
- **Dataclasses**: `SnowfallBucket` (field: `snow_unit`, NOT `precip_unit`), `SnowfallMarketGroup`
- **Distribution**: Gamma (reuses `PrecipitationProbabilityEngine`)
- **Ensemble**: `get_snowfall_ensemble()` -- snowfall_sum, cm -> inches for US
- **Min edge**: 0.12

### Wind Gusts (tag_slug=wind) — READY, NO ACTIVE MARKETS
- **Regex**: `_RE_WIND_RANGE_V2`, `_RE_WIND_BELOW_V2`, `_RE_WIND_HIGHER_V2`
- **Dataclasses**: `WindBucket`, `WindMarketGroup`
- **Distribution**: Normal CDF via `math.erf` (no scipy needed)
- **Ensemble**: `get_wind_ensemble()` -- wind_gusts_10m_max, km/h -> mph via `val / 1.609`
- **Min edge**: 0.10

---

## 6. KEY INTERFACES & SIGNATURES

### forecast_client.py -- WeatherForecastClient
```python
# Temperature
get_combined_forecast(station: WeatherStation, target_date: date) -> Optional[CombinedForecast]
# 133 members, 3600s cache. Combines GEFS(31) + ECMWF IFS(51) + AIFS(51)

get_nbm_forecast(station, target_date) -> Optional[float]         # US only, NWS NBM
get_climate_normal(lat, lon, date, temp_unit) -> Optional[Tuple[float, float]]  # (mean, std)
get_ndfd_pop(station) -> Optional[List[Tuple[str, float, str]]]   # [(name, pop%, start_date_iso)]
get_afdbulletin(station) -> Optional[str]                          # NWS AFD text

# Precipitation
get_precipitation_ensemble(station, target_date) -> Optional[List[float]]   # daily mm/inches
get_monthly_precipitation_ensemble(station, month: int, year: int) -> Optional[List[float]]

# Snowfall
get_snowfall_ensemble(station, target_date) -> Optional[List[float]]  # daily cm->inches

# Wind
get_wind_ensemble(station, target_date) -> Optional[List[float]]      # daily km/h->mph

# Infrastructure
get_session() -> aiohttp.ClientSession        # shared session -- DO NOT create new sessions
invalidate_forecast_cache()                    # clears ONLY _cache (temperature)
set_redis_cache(redis_cache)                   # inject Redis for 429 cooldown persistence
```

### probability_engine.py -- WeatherProbabilityEngine
```python
fit_distribution(ensemble_members, lead_time_hours, station_id) -> Tuple[float, float, float]
bucket_probabilities(loc, scale, shape, buckets, lead_time_hours) -> Dict[str, float]
compute_edges(model_probs, market_prices) -> List[Dict]
apply_climate_prior(loc, scale, clim_mean, clim_std, lead_time_hours) -> Tuple[float, float]
load_calibration(calibration_data)
load_emos_calibration(a, b, station_id)
load_tail_calibration(tail_data)
```

### precipitation_engine.py -- PrecipitationProbabilityEngine
```python
compute_bucket_probabilities(
    ensemble_members: List[float],
    buckets: List[PrecipitationBucket|SnowfallBucket],
    ndfd_pop: Optional[float] = None
) -> Dict[str, float]   # Gamma MLE; P(wet) x P(amount|wet); ndfd_pop 40% blend for daily only

compute_edges(model_probs, buckets, min_edge=0.08) -> List[Dict]
```

### market_mapper.py -- WeatherMarketMapper
```python
# Temperature
group_markets(weather_markets: List[dict]) -> List[WeatherMarketGroup]
parse_market(market_data: dict) -> Optional[TemperatureBucket]
lookup_station(city_text: str) -> Optional[WeatherStation]

# Precipitation
group_precipitation_markets(markets: List[dict]) -> List[PrecipitationMarketGroup]
parse_precipitation_market(market_data: dict) -> Optional[PrecipitationBucket]

# Snowfall
group_snowfall_markets(markets: List[dict]) -> List[SnowfallMarketGroup]

# Wind
group_wind_markets(markets: List[dict]) -> List[WindMarketGroup]

# Internal
_extract_city_and_date(question) -> (city_text, date_object)
_parse_date(date_str)            # "January 22", "Feb 3, 2026"; L2 next-year inference
```

### Dataclass Quick Reference
```python
WeatherStation: city_name, station_id, ghcnd_id, latitude, longitude, elevation_m, timezone, temp_unit, aliases
CombinedForecast: ensemble_members, deterministic_high, model_spread, lead_time_hours, models_used

TemperatureBucket: market_id, token_id, no_token_id, yes_price, bucket_type, low_bound, high_bound, temp_unit
WeatherMarketGroup: city, target_date, station, buckets(List[TemperatureBucket]), slug_prefix, temp_unit

PrecipitationBucket: ..., precip_unit
PrecipitationMarketGroup: ..., precip_unit, period ("daily"/"monthly")

SnowfallBucket: ..., snow_unit          # NOTE: snow_unit, NOT precip_unit
SnowfallMarketGroup: ..., snow_unit

WindBucket: ..., wind_unit
WindMarketGroup: ..., wind_unit
```

---

## 7. CONFIGURATION — LIVE VPS VALUES

### BotBankrollManager
```python
WeatherBot: {capital: 5000, kelly_fraction: 0.25, max_bet_usd: 500, max_daily_usd: 2000}
```

### Kelly Graduation (auto-upgrades/downgrades)
- 100+ resolved + 7-day MSE < 9 -> kelly_fraction 0.25 -> 0.35
- 200+ resolved + 7-day MSE < 4 -> kelly_fraction 0.35 -> 0.50
- MSE degrades -> auto-downgrade

### settings.py / VPS .env Values
```bash
WEATHER_MIN_EDGE=0.08                    # Global fallback; per-type in bot_category_params
WEATHER_MIN_CONFIDENCE=0.10              # Risk manager floor
WEATHER_MAX_PER_GROUP_USD=1000           # Max per (city, date) group
WEATHER_DAILY_LOSS_LIMIT=2000            # Daily loss halt
WEATHER_MAX_CORRELATED_EXPOSURE=2000     # Max per city across dates
WEATHER_MAX_TOTAL_EXPOSURE_USD=50000     # Bot-scoped total exposure
WEATHER_KELLY_FRACTION=0.25              # Quarter-Kelly (graduates dynamically)
WEATHER_DEFAULT_SIZE=25                  # Fallback when Kelly fails
WEATHER_MAX_LEAD_TIME_HOURS=168          # 7-day max
WEATHER_FORECAST_CACHE_TTL=3600          # Raised from 1800 to reduce Open-Meteo 429s
WEATHER_HOLD_HOURS_BEFORE_RESOLUTION=48.0
WEATHER_MAX_POSITIONS=500                # Override global 50 (raised from 200 Session 72)
SCAN_INTERVAL_WEATHER=300                # Overridden dynamically by NWP model windows
SIMULATION_MODE=true
PAPER_TRADING=true
RISK_MAX_POSITION_SIZE_USD=100
```

### Per-Type Min Edge (bot_category_params table, migration 040)
| Market Type | min_edge |
|-------------|----------|
| temperature | 0.08 |
| precipitation | 0.10 |
| snowfall | 0.12 |
| wind | 0.10 |

### Graduated Edge Caps (by lead time)
```
< 6h:  max_edge = 0.70  (METAR available, same day)
< 12h: max_edge = 0.50
< 24h: max_edge = 0.40
< 48h: max_edge = 0.30
>=48h: max_edge = 0.25  (conservative)
```

### Boost Stacking Formula
```python
combined_boost = 1 + (expiry_boost - 1) + (regime_boost - 1) * 0.5 + (severe_boost - 1) * 0.5
combined_boost = min(combined_boost, 2.0)

# Baker-McHale k* position sizing
sigma = model_spread / 3.0
k_star = 1 / (1 + sigma ** 2)

final_kelly_multiplier = combined_boost * k_star
```

### Boost Values
```
Expiry:  <12h=2.0x | <24h=1.5x | <48h=1.2x | >=48h=1.0x
Regime:  >=3 US cities unanimous warm/cold -> 1.2x (applied x0.5 in additive formula)
Severe:  Hurricane=2.0x | Tornado/Blizzard=1.5x (applied x0.5 in additive formula)
```

### Adaptive Scan Intervals (NWP Model Windows)
```
ECMWF: 07:00-08:00, 18:00-19:00 UTC -> 60s scan, invalidate_forecast_cache() (temp only)
GFS:   05:15-06:00, 17:15-18:00 UTC -> 90s scan, invalidate_forecast_cache() (temp only)
HRRR:  :40-:59 each hour              -> 120s scan
Default:                               -> 300s scan
```
NOTE: `invalidate_forecast_cache()` only clears `_cache` (temperature). Precip/snowfall/wind caches survive.

### Open-Meteo API Quota
```
Free tier: ~10,000 calls/day
With ~44 groups, 3 models, 3600s TTL: ~2,160 temp calls/day + ~288 precip = ~2,448 total
If 429 returns: already mitigated with 3600s TTL
```

---

## 8. DB SCHEMA

### weather_calibration
```sql
id BIGSERIAL PK, station_id VARCHAR(20), target_date TIMESTAMP,
forecast_temp DOUBLE PRECISION, actual_temp DOUBLE PRECISION (nullable),
lead_time_hours DOUBLE PRECISION, bias DOUBLE PRECISION (nullable),
crps FLOAT (nullable), model_name VARCHAR(50), regime VARCHAR(20),
created_at TIMESTAMP
UNIQUE(station_id, target_date, lead_time_hours)
```

### weather_forecasts
```sql
id BIGSERIAL PK, station_id VARCHAR(20), target_date TIMESTAMP,
forecast_time TIMESTAMP, lead_time_hours DOUBLE PRECISION,
ensemble_members JSONB, deterministic_high DOUBLE PRECISION,
model_spread DOUBLE PRECISION, models_used JSONB, created_at TIMESTAMP
UNIQUE(station_id, target_date, forecast_time)
```

### weather_tail_calibration (migration 033)
```sql
id BIGSERIAL PK, bucket_type VARCHAR(20), lead_time_bucket INTEGER (0=<6h, 1=6-24h, 2=24-72h, 3=72h+),
model_prob FLOAT, actual_outcome INTEGER (0/1), station_id VARCHAR(20),
created_at TIMESTAMP DEFAULT NOW()
INDEX idx_tail_cal_bucket (bucket_type, lead_time_bucket)
```

### traded_markets (migration 042)
```sql
market_id TEXT PRIMARY KEY, condition_id TEXT, bot_names TEXT NOT NULL,
first_trade_at TIMESTAMP, resolved BOOLEAN DEFAULT FALSE,
resolution TEXT, resolved_at TIMESTAMP, last_checked_at TIMESTAMP
PARTIAL INDEX idx_traded_markets_unresolved ON (resolved) WHERE resolved = FALSE
```
- UPSERT fires on `insert_paper_trade()` (YES/NO entries only)
- UPDATE on `save_market_resolution()`
- Both wrapped in try/except for pre-migration safety

### bot_category_params (migration 040)
```sql
Per market-type min_edge: temperature=0.08, precipitation=0.10, snowfall=0.12, wind=0.10
```

### paper_trades (WeatherBot rows)
```sql
bot_name='WeatherBot', market_id, side (YES/NO), size, price, created_at
UNIQUE(bot_name, market_id, side), CHECK(side IN ('YES','NO','SELL'))
-- SELL trades no longer persisted (eliminated at paper_trading.py source)
-- Uses UPSERT (ON CONFLICT DO UPDATE) for re-entry handling
```

### positions (WeatherBot rows)
```sql
bot_id='WeatherBot', market_id, side, size, entry_price,
current_price (auto-updated every 10s), status ('open'/'closed'),
opened_at, closed_at, unrealized_pnl
```

### daily_counters
```sql
WeatherBot daily_exposure_usd -- ABSOLUTE-SET pattern via order_gateway flush
```

---

## 9. CALIBRATION & LEARNING SYSTEMS

WeatherBot has 6 learning/calibration systems:

### 1. EMOS (Ensemble Model Output Statistics)
- **File**: `base_engine/weather/emos_calibration.py` + `probability_engine.py`
- **Formula**: mu_cal = a + b*X_mean, sigma_cal = c + d*S^2
- **Status**: 13/15 stations EMOS READY (NZWN needs +3, RJTT needs +19 observations). Expected ~2026-03-15.
- **Drift detection**: DDM/EDDM in `_check_emos_drift()` -- advisory only, does NOT halt trading
- **Reload**: Every 6h via `_maybe_reload_calibration()`

### 2. Station Reliability Factor
- MSE-based station reliability -> confidence multiplier
- Source: `weather_calibration` table, 1h cache TTL
- Used in `_analyze_group()` to adjust confidence

### 3. Regime Boost
- `_compute_regime_boost()`: >=3 US cities unanimous warm/cold -> 1.2x Kelly boost
- NOAA PSL Nino 3.4 anomaly for ENSO regime, 24h cache

### 4. METAR Resolution-Day Override
- `_apply_metar_resolution_day_override()`: On resolution day (<6h lead), override model probs with actual METAR observations
- <2h: range boost 0.92, tighter margins (0.5°F US / 0.3°C intl)

### 5. Category-Specific Parameters
- `bot_category_params` table (migration 040)
- `_load_category_params()` on first scan, `_get_min_edge(market_type)` with global fallback

### 6. Prediction Logging (ACTIVE since Session 81)
- `_log_weather_prediction()` writes to `prediction_log` table
- Wired at trade execution time + during analysis (5 call sites)
- Dedup: skips if same market with delta < 0.01 within 600s

### Calibration Data Flow
```
EVERY SCAN -- _maybe_update_calibration_actuals():
  -> Find weather_calibration rows: actual_temp IS NULL AND target_date < NOW()
  -> Attempt WU daily high (preferred -- WU is Polymarket's resolution source)
    - Chrome 122 UA, 4-pattern regex chain, WU sanity check (reject >10F/5C from Open-Meteo)
    - URL: d.isoformat() for zero-padding
  -> Fallback: Open-Meteo archive API
  -> UPDATE actual_temp, bias, crps

EVERY 6 HOURS -- _maybe_reload_calibration():
  -> Load weather_calibration per station -> EMOS OLS per station x lead_time bucket
  -> Regime-conditioned EMOS when >=20 samples per regime
  -> Load weather_tail_calibration -> isotonic bins
  -> prob_engine.load_calibration(), load_emos_calibration(), load_tail_calibration()

EVERY 10 MINUTES -- _check_monitoring_thresholds():
  -> 7-day Brier score: MSE > 25 = CRITICAL halt, MSE > 16 = WARNING
  -> Dynamic Kelly graduation check
```

---

## 10. STATE PERSISTENCE (CROSS-RESTART)

| State | Mechanism | Restore Method |
|-------|-----------|----------------|
| Exit cooldowns (`_recently_exited`) | Redis `weatherbot:exit:{mid}` with 900s TTL | `_restore_exits_from_redis()` on first scan |
| Group/city exposure | Query `paper_trades` (filled_at >= today 00:00 UTC) | `_restore_exposure_from_db()` on startup |
| Daily P&L | Query `paper_trades.realized_pnl` SUM for today | `_restore_daily_pnl_from_db()` on day boundary |
| Forecast cache | `weather_forecasts` DB + Redis PTTL | `_forecast_client.warm_cache_from_db()` + `restore_state()` |
| Station MSE | `weather_calibration` avg MSE, 1h cache | `_get_station_reliability_factor()` |
| Category params | `bot_category_params` table | `_load_category_params()` on first scan |
| Daily exposure counter | `daily_counters` table, 60s flush + SIGTERM | `order_gateway._restore_daily_exposure()` on startup |
| Open positions | `positions` table | `order_gateway.seed_positions_from_db()` |

**ALL GAPS CLOSED** -- no financial state is lost on restart.

---

## 11. BUY/SELL vs YES/NO — HOW THE SYSTEM WORKS

Polymarket binary markets have two tokens: YES and NO. Sum to $1.00.

### Bot -> Paper Engine Translation (`order_gateway.py:652`)
| Bot calls | Paper engine gets | DB record |
|-----------|-------------------|-----------|
| `side="YES"` | `paper_side="BUY"`, `original_side="YES"` | `side="YES"` in paper_trades |
| `side="NO"` | `paper_side="BUY"`, `original_side="NO"` | `side="NO"` in paper_trades |
| `side="SELL"` | `paper_side="SELL"` | **NOT written to DB** (skipped at paper_trading.py) |

### P&L at Market Resolution (`database.py:3078-3084`)
| Your bet | Resolved | Payout | P&L formula |
|----------|----------|--------|-------------|
| YES at $P | YES | $1.00 | `size x (1.00 - P) - fee` -> WIN |
| YES at $P | NO | $0.00 | `size x (0.00 - P) - fee` -> LOSE |
| NO at $P | NO | $1.00 | `size x (1.00 - P) - fee` -> WIN |
| NO at $P | YES | $0.00 | `size x (0.00 - P) - fee` -> LOSE |

Fee = 1.5% taker on `size x entry_price`. Backfill excludes SELL rows.

---

## 12. FEATURE INVENTORY — ALL IMPLEMENTED

| Feature | Location | Description |
|---------|----------|-------------|
| Tag-based market discovery | weather_bot.py | Gamma API `tag_slug=temperature/precipitation/snowfall/wind` |
| 133-member ensemble | forecast_client.py | GEFS(31) + ECMWF IFS(51) + ECMWF AIFS(51) |
| NWS NBM deterministic | forecast_client.py | US-only, 2-step /points -> /forecast |
| Skew-normal distribution | probability_engine.py | MLE fit, normal fallback |
| Gamma distribution | precipitation_engine.py | MLE fit, P(wet)xP(amount|wet) |
| Normal CDF | weather_bot.py | math.erf for wind gusts |
| EMOS calibration | probability_engine.py | OLS per station x lead_time bucket |
| Regime-conditioned EMOS | weather_bot.py | NOAA Nino 3.4, >=20 per regime |
| Isotonic tail calibration | probability_engine.py | Fallback 0.90 |
| Climate normals prior | forecast_client.py | 10-year archive, blend 0-40% at >72h |
| AFD spread adjustment | weather_bot.py | NWS Area Forecast Discussion |
| METAR override (<6h) | weather_bot.py | Running daily max overrides model |
| METAR <2h aggressiveness | weather_bot.py | Range boost 0.92, tighter margins |
| Severe weather boost | weather_bot.py | NWS alerts: 2.0x hurricane, 1.5x tornado |
| Cross-city regime boost | weather_bot.py | >=3 US cities unanimous -> 1.2x |
| S-T multi-bucket sizing | weather_bot.py | `_smoczynski_tomkins_allocate()` |
| Baker-McHale k* | weather_bot.py | k* = 1/(1+sigma^2) |
| Expiry boost | weather_bot.py | <12h=2.0, <24h=1.5, <48h=1.2 |
| Combined boost stacking | weather_bot.py | Additive, capped 2.0x |
| Graduated edge cap | weather_bot.py | <6h=0.70, <12h=0.50, <24h=0.40, <48h=0.30 |
| Boundary risk discount | weather_bot.py | 0.5F/0.3C -> 50% confidence halving |
| Penny-bet filter | weather_bot.py | Skip price <=0.05 or >=0.95 |
| Adaptive scan interval | weather_bot.py | 60s ECMWF, 90s GFS, 120s HRRR, 300s default |
| Slippage guard | weather_bot.py | LiquidityGuardian |
| Position re-evaluation | weather_bot.py | Fresh probs -> exit logic per scan |
| Dynamic Kelly graduation | weather_bot.py | 100+/MSE<9->0.35, 200+/MSE<4->0.50 |
| CRPS scoring | weather_bot.py | Ferro 2014 fair CRPS |
| Forecast persistence | weather_bot.py | CAST(:x AS jsonb) syntax for asyncpg |
| NO-side confidence | weather_bot.py | YES=model_prob, NO=1-model_prob |
| WU resolution verification | weather_bot.py | WU is Polymarket's resolution source |
| WU scraping robustness | weather_bot.py | Chrome 122 UA, 4-pattern regex chain |
| Parse caches (4) | market_mapper.py | Per-type skip regex on seen IDs |
| Batch NWS alerts | weather_bot.py | One pass per scan for all US stations |
| AlertingSystem | weather_bot.py | station-offline, tag-fetch-fail, daily-loss-limit |
| Precipitation markets | weather_bot.py | Gamma, V1+V2 regex, monthly ensemble |
| Snowfall markets | weather_bot.py | Gamma, tag_slug=snowfall |
| Wind gust markets | weather_bot.py | Normal CDF, tag_slug=wind |
| Monthly precip ensemble | forecast_client.py | Archive actuals + ensemble totals |
| Prediction logging | weather_bot.py | `_log_weather_prediction()` to prediction_log |
| traded_markets table | database.py | Fast resolution backfill for our markets |
| Parallel scan init | weather_bot.py | Steps 1-5 parallelized (Session 84 commit) |
| NWS alert semaphore | weather_bot.py | Bounded concurrency (Session 84 commit) |
| 5-layer position guard | weather_bot.py | In-memory + cooldown + eviction + Redis + DB UNIQUE |
| Date-aware stale cleanup | weather_bot.py | Parses target date from question, closes past-date positions |

---

## 13. TRAPS, GOTCHAS & CRITICAL PATTERNS

### Platform-Wide (DO NOT BREAK)
1. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER pass BUY/SELL.
2. **asyncpg JSONB**: Use `CAST(:x AS jsonb)` NOT `:x::jsonb`. asyncpg misparses `::`.
3. **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime string.
4. **asyncpg timestamps**: `paper_trades` uses `timestamp without time zone` -- pass `.replace(tzinfo=None)`. `created_at` has NO DEFAULT.
5. **PSEUDO_LABEL_ENABLED=false**: DO NOT enable. Only Location 1 labels are correct.
6. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
7. **`risk_manager.calculate_position_size()` DEPRECATED** -- BotBankrollManager is the real sizer.
8. **BOT_REGISTRY=14 bots** -- shared module changes require all 14 verified.
9. **`paper_trades` has NO `metadata` JSONB column** -- never assume metadata is available.

### WeatherBot-Specific
10. **SELL paper_trades eliminated at source**: paper_trading.py skips DB persist for SELL. Do NOT re-enable.
11. **paper_trades UNIQUE constraint**: `(bot_name, market_id, side)`. Uses UPSERT. Do NOT use ORM `session.add()`.
12. **paper_trades CHECK constraint**: side must be YES, NO, or SELL.
13. **`_open_position_markets` eviction**: `_close_stale_positions()` must evict from `order_gateway._open_position_markets` or positions block re-entry forever.
14. **Resolution backfill excludes SELL trades**: `AND LOWER(pt.side) != 'sell'`.
15. **Open-Meteo rate limit**: Free tier ~10,000 req/day. Cache TTL=3600s on VPS.
16. **traded_markets try/except**: Both write paths have try/except for pre-migration safety. Do NOT remove.
17. **4 typed forecast caches**: `_cache` (CombinedForecast), `_precip_cache`, `_snowfall_cache`, `_wind_cache`. NEVER mix types.
18. **4 parse caches**: Per-type in market_mapper. Skip regex on seen market IDs.
19. **Shared aiohttp session**: `forecast_client.get_session()` reused everywhere. Do NOT create new sessions.
20. **V1 + V2 precip regex**: Both preserved. V2 = actual Polymarket format.
21. **Monthly vs daily precip**: `PrecipitationMarketGroup.period` field. NDFD PoP NOT blended for monthly.
22. **SnowfallBucket field**: `snow_unit`, NOT `precip_unit`. Easy confusion.
23. **WU URL zero-padding**: Use `d.isoformat()` not f-string formatting.
24. **Wind conversion**: `val / 1.609` converts km/h -> mph. This is CORRECT.
25. **CLOB volume=0**: Never use volume gates for weather markets.
26. **Weather market IDs ~249000+**: Far beyond standard pipeline. tag_slug discovery is mandatory.
27. **`_precip_to_temp_group()` returns `buckets=[]`**: INTENTIONAL. Trade execution uses group.city/target_date/station.
28. **ECMWF monthly exclusion**: Expected log: `monthly_precip_model_no_members model=ecmwf_ifs025` when >21 remaining days. Informational, not error.
29. **NWS API**: Requires `Accept: application/geo+json` header or returns 406.
30. **`invalidate_forecast_cache()`**: Only clears `_cache` (temperature). Precip/snow/wind caches survive NWP invalidations.

### Bot-Scoped Limits (overrides in risk_manager.py)
```python
if bot_name == "WeatherBot":
    max_positions = settings.WEATHER_MAX_POSITIONS   # 500
    total_exposure = og.get_bot_exposure_usd("WeatherBot")
    max_total_exposure = settings.WEATHER_MAX_TOTAL_EXPOSURE_USD  # 50000
```

### 5-Layer Position Guard
1. In-memory `_open_position_markets` set check
2. 15-min `_recently_exited` cooldown
3. `_close_stale_positions()` evicts from `order_gateway._open_position_markets`
4. Redis TTL persistence for exit cooldowns
5. DB UNIQUE constraint `(bot_name, market_id, side)` with UPSERT

---

## 14. SESSION HISTORY (53-84)

### Session 84 (2026-03-13) — Trade Ledger DB Overhaul (NOT WeatherBot-scoped)
- Event-sourced trade ledger, snapshots, recon, ML registry (migrations 043-050)
- Parallel scan init for WeatherBot (`02ea94a`)
- NWS alert semaphore bound concurrency

### Session 81 (2026-03-12) — Resolution Backfill + traded_markets + Prediction Logging
- **THE BIG ONE**: 96 unresolved WeatherBot paper trades stuck -- `traded_markets` table (migration 042) fixed resolution backfill priority. Phase 2a reads our markets first, no LIMIT.
- `_log_weather_prediction()` wired into `_execute_weather_trade()` at trade execution time
- SELECT DISTINCT fix for Phase 2b
- EMOS drift analysis: KDFW (86.7%), KLGA (50%) need recalibration
- Commits: `b8f8b26`, `e76df44`

### Session 80 (2026-03-12) — SELL Elimination + P&L Audit + DB Constraints
- SELL paper_trades eliminated at source (paper_trading.py)
- 9 P&L queries fixed with YES/NO filters
- Exit learning migrated from paper_trades to positions table
- UNIQUE + CHECK constraints (migration 041)
- UPSERT for re-entry handling
- Full data audit: 1090 records purged (934 SELLs, 776 EnsembleBot dupes, etc.)
- Date-aware `_close_stale_positions()` rewrite
- Per-type min_edge via `bot_category_params` (migration 040)
- Commits: `88e34ee`, `fc54a85`, `07c8266`, `c453fe0`, `fc7a242`, `5b686f1`

### Session 77 (2026-03-11) — Cross-Bot Audit
- WeatherBot: `_log_weather_prediction()` function created (NOT wired until Session 81)
- Tiered slippage, paper_trades status/timestamps (migration 039), PandaScore shared counter

### Session 69 (2026-03-09) — Full Codebase Audit + Boot Blocker Fix
- market_mapper.py (1105 lines) and precipitation_engine.py (231 lines) committed (were in working tree but never `git add`ed)
- ECMWF silent exclusion warning log
- crps ORM gap fixed
- Migration 033 created (weather_tail_calibration)

### Session 68 (2026-03-09) — Exposure Limit + Open-Meteo 429 + WU Scraping
- Bot-scoped exposure: `WEATHER_MAX_TOTAL_EXPOSURE_USD=50000`
- Cache TTL 900->1800s, selective invalidation (temp only)
- WU Chrome 122 UA, 4-pattern regex chain

### Session 67 (2026-03-09) — 5 Bugs + Snowfall + Wind + Precip V2 Regex
- 4 typed forecast caches, WU URL fix, NDFD PoP filtering, shared session, parse caches
- Snowfall pipeline (M2), Wind pipeline (M3)
- Precipitation V2 regex (actual Polymarket format) -- ROOT CAUSE of 0 groups found
- Monthly ensemble: `get_monthly_precipitation_ensemble()`

### Session 66 (2026-03-09) — Bugs + Precipitation Engine + Unblock
- Capital reference fix, intra-day P&L refresh, batch NWS alerts
- WU resolution verification, AlertingSystem
- Precipitation engine NEW, MAX_POSITIONS=200, MIN_EDGE=0.08, MIN_CONFIDENCE=0.10

### Session 64 (2026-03-08) — S-T Sizing + Baker-McHale + Kelly Graduation
- `_smoczynski_tomkins_allocate()`, k* sizing, dynamic Kelly, CRPS scoring

### Session 62 (2026-03-08) — Market Discovery + Confidence Fix
- ROOT FIX: `_fetch_weather_events_by_tag()` -- bot was inert without Gamma API
- NO-side confidence, JSONB cast fix, $500 max bet/$2K daily

### Session 61 (2026-03-08) — Doom Loop Fix
- Uniform fallback returning fake edges -> return {} (empty)
- EnsembleBot archived (-$5.6K)

### Sessions 53-60 — Foundation
- S55: WS YES/NO fix, ENSEMBLE_BLEND=1.0
- S57: Climate normals, AFD integration
- S58: Regime EMOS (NOAA Nino 3.4)
- S59: METAR client, NWS alerts, severe weather boosts

---

## 15. OUTSTANDING ITEMS / ROADMAP

### ALL PRIOR ITEMS RESOLVED (verified 2026-03-13)

| Prior Item | Resolution |
|------------|-----------|
| DB_POOL_SIZE increase (15->30) | **Already done** — `settings.py:24` default is `"30"`. Total pool = 35 (30+5 overflow). |
| Stale position N+1 batch query | **Already optimized** — `_close_stale_positions()` uses batch `SELECT` + `UPDATE WHERE market_id = ANY(:ids)`. No per-position queries. |
| paper_trades status='resolved' | **Already done** — `backfill_paper_trades_resolution()` at `database.py:3219` sets `status = 'resolved'`. |
| EMOS recalibration (KDFW/KLGA) | **Auto-resolving** — EMOS coefficients recompute every 6h via `_maybe_reload_calibration()` OLS refit. Drift decreases as observations accumulate. No code change needed. |
| Combine temp+precip API calls | **Not worth doing** — precip = 2 groups (NYC, Seattle), 6 extra calls/hour with 3600s cache. Different scan phases, different tag_slugs, architectural change for negligible gain. |
| Snowfall/Wind regex validation | **Blocked** — 0 markets exist on Polymarket. Will validate when first markets appear. |

### Remaining Future Work

1. **Hurricane/Climate (M4)**: NOAA CFS v3 seasonal data, separate `SeasonalWeatherBot`. Significant new work.
2. **Polymarket tag validation**: When snowfall/wind markets first appear, verify `tag_slug=snowfall`/`tag_slug=wind` and regex patterns match actual question format.
3. **Monitor EMOS activation**: ~March 15-17 for early stations (started collecting March 8). 13/15 stations ready.

### Deferred (not WeatherBot scope)
- MirrorBot RTDS stability monitoring
- EsportsBot LoL team name extraction
- Redis shared rate limiter (pre-live)

---

## 16. DIAGNOSTIC & VERIFICATION COMMANDS

### Local
```bash
# Full test suite
cd C:/lockes-picks/polymarket-ai-v2 && python -m pytest tests/ -x -q --tb=short

# Quick import check
python -c "import bots.weather_bot; print('WeatherBot import OK')"
python -c "from base_engine.weather.market_mapper import PrecipitationMarketGroup, SnowfallBucket, WindMarketGroup; print('Dataclasses OK')"

# Verify Gamma API tags
curl -s "https://gamma-api.polymarket.com/events?active=true&closed=false&tag_slug=temperature&limit=5" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d),'temperature events')"
curl -s "https://gamma-api.polymarket.com/events?active=true&closed=false&tag_slug=precipitation&limit=5" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d),'precipitation events')"
```

### VPS
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Service status
ssh -i "$KEY" "$VPS" 'systemctl is-active polymarket-ai'

# WeatherBot logs (real-time)
sudo journalctl -u polymarket-ai -f | grep weatherbot

# Scan health
sudo journalctl -u polymarket-ai --since "1 hour ago" | grep weatherbot_scan_done | tail -10

# Precip scan
sudo journalctl -u polymarket-ai --since "1 hour ago" | grep weatherbot_precip_scan_done | tail -10

# Trades placed
sudo journalctl -u polymarket-ai --since "6 hours ago" | grep weatherbot_trade_filled

# Resolution backfill
sudo journalctl -u polymarket-ai --since "5 min ago" --no-pager -o cat | grep -i 'backfill\|resolution'

# 429 quota check
sudo journalctl -u polymarket-ai --since "1 hour ago" | grep "429"

# Errors
sudo journalctl -u polymarket-ai --since "1 hour ago" | grep -i "weatherbot.*error\|traceback"
```

### VPS DB Queries
```bash
# P&L check (resolved vs pending)
sudo -u polymarket psql -d polymarket -c "SELECT COUNT(*) FILTER (WHERE realized_pnl IS NOT NULL) AS resolved, COUNT(*) FILTER (WHERE realized_pnl IS NULL) AS pending, ROUND(SUM(COALESCE(realized_pnl,0))::numeric,2) AS total_pnl FROM paper_trades WHERE bot_name='WeatherBot' AND side IN ('YES','NO');"

# traded_markets status
sudo -u polymarket psql -d polymarket -c "SELECT bot_names, COUNT(*) AS total, COUNT(*) FILTER (WHERE resolved = FALSE) AS unresolved FROM traded_markets GROUP BY bot_names ORDER BY total DESC;"

# Open positions
sudo -u polymarket psql -d polymarket -c "SELECT status, COUNT(*), ROUND(SUM(unrealized_pnl)::numeric,2) FROM positions WHERE source_bot='WeatherBot' GROUP BY status;"

# Data integrity
sudo -u polymarket psql -d polymarket -c "SELECT side, COUNT(*) FROM paper_trades GROUP BY side;"

# Recent trades
sudo -u polymarket psql -d polymarket -c "SELECT created_at, side, size, price, market_id FROM paper_trades WHERE bot_name='WeatherBot' ORDER BY created_at DESC LIMIT 10;"

# EMOS progress
sudo -u polymarket psql -d polymarket -c "SELECT station_id, COUNT(*) actuals FROM weather_calibration WHERE actual_temp IS NOT NULL GROUP BY station_id ORDER BY actuals DESC LIMIT 15;"

# Category params
sudo -u polymarket psql -d polymarket -c "SELECT * FROM bot_category_params WHERE bot_name='WeatherBot';"
```

---

## 17. DEVELOPMENT RULES

**Prime Directive:** Working code is sacred. Fix only what is broken.

### Before writing ANY code:
1. State the bug in one sentence
2. List files you will touch (if >3, justify)
3. Grep for dependents
4. Git snapshot before editing
5. Read the ENTIRE file you're modifying

### Rules of engagement:
- One fix per commit
- Preserve function signatures (unless signature IS the bug)
- Preserve external interfaces
- No silent behavior changes
- Never delete code you don't understand
- No new dependencies without justification
- No structural refactors during bug fixes

### Forbidden patterns:
- "While I'm in here" refactor
- Band-aid try/except hiding real errors
- Shotgun fix
- Scope creep
- Silent migration
- Optimistic rewrite

### Test suite:
```bash
python -m pytest tests/ -x -q --tb=short
# Must see 1446+ passed, 0 failed
```

---

## 18. SESSION SCOPE RULE

**This is a WeatherBot-only session.** Hardcoded scope:
- Only modify: `bots/weather_bot.py`, `base_engine/weather/**`, WeatherBot tests
- Shared modules (`base_engine/`, `database.py`, `config/`) ONLY if directly fixing a WeatherBot bug
- NEVER commit changes to `mirror_bot.py`, `esports_bot.py`, or other non-weather files
- Cross-bot changes require explicit user approval
- If prior sessions left uncommitted non-weather changes, leave them alone

---

## 19. API REFERENCE

### External APIs Used
| System | URL Pattern | Purpose |
|--------|-------------|---------|
| Polymarket Gamma | `gamma-api.polymarket.com/events?tag_slug={slug}` | Market discovery |
| Polymarket CLOB | `clob.polymarket.com/` | Order placement, prices |
| Open-Meteo Ensemble | `ensemble-api.open-meteo.com/v1/ensemble` | 133-member NWP ensemble |
| Open-Meteo Archive | `archive-api.open-meteo.com/v1/archive` | Historical actuals |
| NWS Points | `api.weather.gov/points/{lat},{lon}` | Grid data, WFO lookup |
| NWS Forecast | `api.weather.gov/gridpoints/{wfo}/{x},{y}/forecast` | NBM deterministic |
| NWS Alerts | `api.weather.gov/alerts/active?point={lat},{lon}` | Severe weather |
| NWS AFD | `api.weather.gov/products?type=AFD&location={wfo}` | Area Forecast Discussion |
| Weather Underground | `wunderground.com/history/daily/{station}/date/{d}` | Resolution source |
| NOAA PSL | `psl.noaa.gov/data/correlation/nina34.anom.data` | ENSO regime detection |
| METAR (AWC) | Aviation Weather Center | Real-time airport obs |

### Station Registry (30+ stations)
Major: NYC (KLGA), Chicago (KORD), Atlanta (KATL), Miami (KMIA), Dallas (KDFW), Seattle (KSEA), Denver (KDEN), Phoenix (KPHX), Houston (KIAH), San Francisco (KSFO), Toronto (CYYZ), London (EGLL), Paris (LFPG), Tokyo (RJTT), Sydney (YSSY), Buenos Aires (SAEZ), Sao Paulo (SBGR), Seoul (RKSI), Ankara (LTAC), Mumbai (VABB), and more.

Each: `city_name, station_id (ICAO), ghcnd_id, lat/lon, elevation_m, timezone, temp_unit (F/C), aliases, resolution_source`

---

## 20. WeatherBot __init__ STATE VARIABLES

```python
# Sub-components
self._forecast_client     # WeatherForecastClient (Open-Meteo, 3600s cache)
self._metar_client        # MetarClient (METAR aviation obs)
self._prob_engine         # WeatherProbabilityEngine (skew-normal CDF)
self._precip_engine       # PrecipitationProbabilityEngine (Gamma CDF)
self._market_mapper       # WeatherMarketMapper (regex -> buckets -> groups)
self._station_health      # StationHealthMonitor (MSE-based reliability)

# Config (from settings.py / env)
self._min_edge            # 0.08 global fallback
self._max_per_group       # 1000.0 USD
self._daily_loss_limit    # 2000.0 USD
self._max_correlated      # 2000.0 USD
self._kelly_mult          # 0.25
self._default_size        # 25.0 USD
self._max_lead_time       # 168.0 hours (7 days)

# Risk state (restored from DB on startup)
self._daily_pnl           # float, restored from paper_trades SUM
self._group_exposure      # Dict[str, float]: "city:date" -> USD
self._city_exposure        # Dict[str, float]: city -> total USD
self._recently_exited     # Dict[str, float]: market_id -> mono time (15min cooldown)
self._known_open_markets  # Set[str]: snapshot for PM exit detection

# Calibration
self._calibration_last_loaded    # float: monotonic time of last reload
self._calibration_reload_interval # 21600.0 (6 hours)
self._monitoring_halt            # bool: True = stop trading until Brier improves
self._monitoring_last_check      # float
self._monitoring_check_interval  # 600.0 (10 minutes)

# Startup flags
self._startup_check_done         # bool: one-time market availability check
self._cache_warmed               # bool: one-time cache restore from Redis/DB
self._last_direct_probe          # float: rate-limit DB+Gamma probe (30min)

# Dedup/tracking
self._written_forecasts          # Set[str]: "station_id:date_iso" (forecast write dedup)
self._prediction_log_cache       # Dict: market_id -> (prob, mono_ts) (prediction log dedup)
self._scan_count                 # int: scan counter
self._consecutive_losses         # Dict: market_type -> streak count
self._category_params            # Dict: market_type -> {min_edge, ...}
self._category_params_loaded     # bool

# Station/EMOS caches
self._station_mse_cache          # Dict: station_id -> (mse, mono_ts) (1h TTL)
self._drift_detectors            # Dict: station_id -> DriftDetector

# Regime
self._regime_tag                 # Optional[str]: "el_nino"/"la_nina"/"neutral"
self._regime_last_fetched        # float (24h cache)

# NWS caches
self._afd_cache                  # Dict: station_id -> (expiry_mono, spread_factor)
self._wfo_cache                  # Dict: station_id -> Optional[wfo_code] (never expires)
self._severe_weather_batch       # Dict: station_id -> boost_factor
self._severe_weather_batch_time  # float
```

---

**END OF HANDOFF. This document + CLAUDE.md is sufficient context to continue all WeatherBot development.**
