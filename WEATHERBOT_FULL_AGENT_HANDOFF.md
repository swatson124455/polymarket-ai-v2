# WEATHERBOT — COMPLETE AGENT HANDOFF (Carbon Copy)
**Date:** 2026-03-09 | **Sessions covered:** 53–69 | **Tests:** 1333 passed, 6 skipped
**Commit:** `65abd0e` — fix: WeatherBot boot blocker + schema gaps (Session 69)
**VPS:** `active (running)` — process 1766867 started ~19:02 UTC
**This document is a full-context transfer. A new agent reading ONLY this file should be able to continue all work seamlessly.**

---

## TABLE OF CONTENTS
1. [What WeatherBot Is](#1-what-weatherbot-is)
2. [Infrastructure & Deploy](#2-infrastructure--deploy)
3. [File Map & Architecture](#3-file-map--architecture)
4. [Core Data Flow — Complete Scan Cycle](#4-core-data-flow)
5. [Market Type Coverage](#5-market-type-coverage)
6. [Key Interfaces & Signatures](#6-key-interfaces)
7. [Configuration — Live Values](#7-configuration)
8. [DB Schema](#8-db-schema)
9. [Calibration System](#9-calibration-system)
10. [Feature Inventory — All Implemented](#10-feature-inventory)
11. [Traps, Gotchas & Patterns](#11-traps-gotchas--patterns)
12. [Session History (53–69)](#12-session-history)
13. [Known Issues & Future Work](#13-known-issues--future-work)
14. [Development Rules (CLAUDE.md)](#14-development-rules)
15. [System Context (14-Bot Platform)](#15-system-context)
16. [Verification Commands](#16-verification-commands)

---

## 1. WHAT WEATHERBOT IS

**WeatherBot** is one of 14 active bots in a live Polymarket automated trading system. Real capital is at risk. It trades weather-bucket markets across 4 product types:

### Temperature (PRIMARY — actively trading)
1. Finds markets: "Will the highest temperature in NYC be between 72-75°F on March 10?"
2. Fetches 133-member ensemble forecasts (GEFS 31 + ECMWF IFS 51 + ECMWF AIFS 51)
3. Fits a skew-normal distribution; integrates probability across each bucket's bounds
4. Compares model_prob vs market YES price → edge = model_prob - market_price
5. If edge passes filters → sizes via fractional Kelly → paper trade

### Precipitation (ACTIVE — scanning, pending quota reset to trade)
- Monthly cumulative markets: "Will NYC have between 3 and 4 inches of precipitation in March?"
- Gamma distribution fit to ensemble precipitation sums
- Historical actuals (elapsed days) + ensemble forecasts (remaining days)
- **NYC has massive mispricings confirmed:** "2–3 in" YES @ 8.45% (model ~68%), ">6 in" YES @ 16.5% (model ~0%)

### Snowfall (READY — no active markets on Polymarket as of 2026-03-09)
- Same framework as precipitation, Gamma distribution
- Ensemble variable: `snowfall_sum` (cm → inches for US)

### Wind Gusts (READY — no active markets on Polymarket as of 2026-03-09)
- Normal CDF (math.erf) for bucket probabilities
- Ensemble variable: `wind_gusts_10m_max` (km/h → mph for US)

### The Edge
- Retail bettors overweight extreme buckets (favourite-longshot bias)
- Market prices lag NWP model updates by 30-60 minutes
- Resolution-day outcomes can be front-run via METAR aviation observations 1-5 min post-observation

---

## 2. INFRASTRUCTURE & DEPLOY

```
VPS:     Ubuntu-3 at 34.251.224.21 (16GB/4vCPU, eu-west-1)
SSH key: C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem
User:    ubuntu
Path:    /opt/polymarket-ai-v2/
Service: polymarket-ai (systemd)
DB:      postgresql://polymarket:polymarket_s46@localhost:5432/polymarket
Redis:   78psiRhepTgrmWSoy3cgNEIr
```

### CRITICAL: VPS has NO git repo
- `/opt/polymarket-ai-v2/` has no `.git` directory
- Files are deployed via manual `scp` + `sudo cp`
- VPS state ≠ local git state. Always explicitly deploy after committing.
- Ownership: files owned by `polymarket` user. Ubuntu user needs `sudo cp` to write.

### Deploy pattern (every file must go through /tmp)
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Single file deploy
scp -i "$KEY" -o StrictHostKeyChecking=no "local/path/file.py" "$VPS:/tmp/"
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'sudo cp /tmp/file.py /opt/polymarket-ai-v2/path/file.py && \
   sudo chown polymarket:polymarket /opt/polymarket-ai-v2/path/file.py && \
   sudo systemctl restart polymarket-ai'

# Multiple files
scp -i "$KEY" -o StrictHostKeyChecking=no file1.py file2.py "$VPS:/tmp/"
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'sudo cp /tmp/file1.py /opt/polymarket-ai-v2/base_engine/weather/file1.py && \
   sudo cp /tmp/file2.py /opt/polymarket-ai-v2/bots/file2.py && \
   sudo systemctl restart polymarket-ai'

# Restart only
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" 'sudo systemctl restart polymarket-ai'

# Migration (run as polymarket user or root)
PGPASSWORD='polymarket_s46' psql -h localhost -U polymarket -d polymarket -f /tmp/migration.sql
```

### Log monitoring
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Scan summaries
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "1 hour ago" | grep weatherbot_scan_done'

# Precip scan
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "1 hour ago" | grep weatherbot_precip_scan_done'

# Trades placed
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "1 hour ago" | grep weatherbot_trade_filled'

# Errors
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "1 hour ago" | grep -i "weatherbot.*error\|weatherbot.*failed\|ImportError\|Traceback"'

# Open-Meteo 429 (quota exhaustion)
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "1 hour ago" | grep "429"'

# Exposure blocks
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "30 minutes ago" | grep "Total exposure"'
```

### DB queries (run on VPS)
```bash
# Open positions
PGPASSWORD='polymarket_s46' psql -h localhost -U polymarket -d polymarket -c \
  "SELECT LEFT(market_id,12), side, size, entry_price, opened_at FROM positions WHERE bot_id='WeatherBot' AND status='open' ORDER BY opened_at DESC LIMIT 20;"

# Calibration actuals progress (EMOS activates at 20+ per station)
PGPASSWORD='polymarket_s46' psql -h localhost -U polymarket -d polymarket -c \
  "SELECT station_id, COUNT(*) actuals FROM weather_calibration WHERE actual_temp IS NOT NULL GROUP BY station_id ORDER BY actuals DESC LIMIT 15;"

# Tail calibration rows
PGPASSWORD='polymarket_s46' psql -h localhost -U polymarket -d polymarket -c \
  "SELECT bucket_type, lead_time_bucket, COUNT(*) FROM weather_tail_calibration GROUP BY 1,2 ORDER BY 1,2;"
```

---

## 3. FILE MAP & ARCHITECTURE

```
bots/
  weather_bot.py              -- Main bot: scan_and_trade, 4 market type scanners, trade execution (~3038 lines)
  base_bot.py                 -- BaseBot ABC (place_order, scan loop, heartbeat)

base_engine/weather/
  forecast_client.py          -- Open-Meteo ensemble + NWS NBM + climate normals + precip/snow/wind ensemble (~1076 lines)
  metar_client.py             -- Aviation Weather Center METAR API (running daily max)
  probability_engine.py       -- Skew-normal fit, EMOS calibration, bucket integration, Kelly (temperature)
  precipitation_engine.py     -- Gamma distribution fit, P(rain) × P(amount|rain) (precip + snowfall) (~231 lines)
  market_mapper.py            -- Regex → 4 market types: Temp/Precip/Snow/Wind buckets + groups (~1105 lines)
  station_registry.py         -- ICAO→city registry, 30+ stations, lookup_station() (~1340 lines)

base_engine/risk/
  bankroll_manager.py         -- Per-bot Kelly sizing (WeatherBot: capital=5000, max_bet=500)
  risk_manager.py             -- Risk checks (WeatherBot MIN_CONFIDENCE=0.10, MAX_POSITIONS=200, bot-scoped exposure)
  liquidity_guardian.py       -- Order book slippage estimation

base_engine/execution/
  paper_trading.py            -- Paper trade engine (3x retry on DB persist)
  position_manager.py         -- Exit logic: stop-loss -30%, take-profit +60%, model reversal, hold-to-resolution
  order_gateway.py            -- Position tracking, per-bot exposure management

config/
  settings.py                 -- All settings (WEATHER_* env vars)

schema/migrations/
  032_weather_calibration_crps.sql  -- Adds crps FLOAT to weather_calibration
  033_weather_tail_calibration.sql  -- Creates weather_tail_calibration table (Session 69)

tests/
  unit/test_weather_bot.py    -- WeatherBot unit tests (1333 passing as of Session 69)
```

### Import graph (WeatherBot dependencies)
```
weather_bot.py
  ├── base_bot.py (BaseBot ABC)
  ├── forecast_client.py (WeatherForecastClient)
  ├── metar_client.py (MetarClient)
  ├── probability_engine.py (WeatherProbabilityEngine)
  ├── precipitation_engine.py (PrecipitationProbabilityEngine)   ← committed Session 69
  ├── market_mapper.py (WeatherMarketMapper + all 8 dataclasses) ← committed Session 69
  ├── station_registry.py (lookup_station, WeatherStation)
  ├── bankroll_manager.py (BotBankrollManager)
  ├── risk_manager.py (RiskManager)
  ├── liquidity_guardian.py (LiquidityGuardian)
  ├── paper_trading.py (PaperTradingEngine)
  └── position_manager.py (PositionManager)
```

---

## 4. CORE DATA FLOW — COMPLETE SCAN CYCLE

```
scan_and_trade()
  │
  ├─ 1. _handle_daily_boundary() — reset P&L at UTC midnight
  ├─ 2. _restore_daily_pnl_from_db() — refresh intra-day P&L every scan (Bug 2 fix, Session 66)
  ├─ 3. _maybe_reload_calibration() — every 6h: EMOS + bias + tail cal from DB
  ├─ 4. _check_monitoring_thresholds() — every 10min: Brier/drawdown halt + Kelly graduation
  ├─ 5. Detect PM exits → add to _recently_exited (15min cooldown)
  ├─ 6. _reset_climate_cycle() on forecast_client
  ├─ 7. _check_weather_market_availability() — one-time startup
  │
  ├─ 8. MARKET DISCOVERY (primary → fallback → last resort):
  │     a) _fetch_weather_events_by_tag(tag_slug="temperature") ← Gamma API (PRIMARY)
  │     b) get_all_tradeable_markets(weather)                   ← DB category
  │     c) _fetch_weather_markets_direct()                      ← DB+API fallback (30min rate limit)
  │
  ├─ 9. _market_mapper.group_markets() → List[WeatherMarketGroup] (by city+date)
  │
  ├─ 10. PHASE 1: For each WeatherMarketGroup → _analyze_group()
  │     ├─ Skip: past dates, lead_time > 168h, station unhealthy
  │     ├─ get_combined_forecast() → CombinedForecast (133 members, 1800s cache)
  │     ├─ _save_forecast_to_db() → weather_forecasts + weather_calibration rows
  │     ├─ fit_distribution() → (loc, scale, shape) skew-normal via MLE
  │     ├─ apply_climate_prior() — blend 0-40% at >72h lead time
  │     ├─ AFD spread adjustment (NWS Area Forecast Discussion)
  │     ├─ bucket_probabilities() → {market_id: prob}
  │     ├─ METAR override (<6h lead_time — resolution day only)
  │     │   └─ <2h: range boost 0.92, tighter margins 0.5°F/0.3°C
  │     ├─ M7 coherence check (>50% buckets need prices)
  │     ├─ compute_edges() → [{market_id, edge, side, abs_edge, model_prob}]
  │     └─ Filter: min_edge → graduated edge cap → cooldown → penny filter
  │         → position check → boundary risk (0.5°F/0.3°C)
  │         → confidence: YES=model_prob, NO=1-model_prob (halved if boundary_risk)
  │
  ├─ 11. PHASE 2: _compute_regime_boost() → 1.2x if ≥3 US cities unanimous
  │
  ├─ 12. PHASE 3: Execute trades
  │     ├─ ≥2 opps in group → _execute_group_trades() (S-T multi-bucket sizing)
  │     └─ 1 opp → _execute_weather_trade() (independent Kelly)
  │         ├─ Daily loss limit check (self.bankroll.capital — NOT hardcoded)
  │         ├─ City/date group exposure check (WEATHER_MAX_PER_GROUP_USD=1000)
  │         ├─ Bot-scoped total exposure (WEATHER_MAX_TOTAL_EXPOSURE_USD=50000)
  │         ├─ Expiry boost: <12h=2.0x, <24h=1.5x, <48h=1.2x
  │         ├─ Regime boost: 1.2x if detected
  │         ├─ Severe weather boost: 2.0x hurricane, 1.5x tornado/blizzard
  │         ├─ Combined boost (additive, capped 2.0x) × Baker-McHale k*
  │         ├─ Slippage check (LiquidityGuardian)
  │         ├─ Kelly sizing via BotBankrollManager ($500 max, $2K daily)
  │         └─ place_order() → risk_manager checks → paper_trading
  │
  ├─ 13. PHASE 4: _reevaluate_open_positions() → fresh probs for exit logic
  │
  ├─ 14. PHASE 5: _scan_precipitation_markets() → tag_slug=precipitation
  │     ├─ Discover via Gamma API (finds 2 groups: NYC, Seattle)
  │     ├─ group_precipitation_markets() → List[PrecipitationMarketGroup]
  │     ├─ _analyze_precipitation_group():
  │     │   ├─ period="monthly" → get_monthly_precipitation_ensemble()
  │     │   │     (archive actuals for elapsed days + ensemble for remaining days)
  │     │   ├─ period="daily" → get_precipitation_ensemble() + get_ndfd_pop() (40% blend)
  │     │   ├─ PrecipitationProbabilityEngine.compute_bucket_probabilities() [Gamma CDF]
  │     │   └─ PrecipitationProbabilityEngine.compute_edges()
  │     └─ Execute via _execute_weather_trade() (same path as temperature)
  │
  ├─ 15. PHASE 6: _scan_snowfall_markets() → tag_slug=snowfall
  │     └─ Same flow as precipitation; reuses PrecipitationProbabilityEngine
  │         get_snowfall_ensemble() → snowfall_sum (cm → inches for US)
  │
  └─ 16. PHASE 7: _scan_wind_markets() → tag_slug=wind
        └─ Normal CDF (math.erf), NOT Gamma
            get_wind_ensemble() → wind_gusts_10m_max (km/h → mph: val/1.609 — CORRECT)
```

---

## 5. MARKET TYPE COVERAGE

### Temperature (tag_slug=temperature)
- **Regex V1 (4 patterns):** `_RE_RANGE`, `_RE_AT_OR_BELOW`, `_RE_AT_OR_HIGHER`, `_RE_EXACT`
- **Format:** "Will the highest temperature in CITY be between X-Y°F on DATE?"
- **Dataclasses:** `TemperatureBucket`, `WeatherMarketGroup`
- **Distribution:** Skew-normal (scipy `skewnorm`)
- **Ensemble:** 133 members (GEFS 31 + ECMWF IFS 51 + ECMWF AIFS 51) via `get_combined_forecast()`

### Precipitation (tag_slug=precipitation)
- **Regex V1 (3 patterns — hypothetical format):** `_RE_PRECIP_RANGE`, `_RE_PRECIP_AT_OR_BELOW`, `_RE_PRECIP_AT_OR_HIGHER`
- **Regex V2 (3 patterns — ACTUAL POLYMARKET FORMAT):** `_RE_PRECIP_RANGE_V2`, `_RE_PRECIP_BELOW_V2`, `_RE_PRECIP_HIGHER_V2`
  - V2 format: `"Will CITY have between X and Y inches of precipitation in MONTH?"`
  - V2 format: `"Will CITY have less than X inches of precipitation in MONTH?"`
  - V2 format: `"Will CITY have more than X inches of precipitation in MONTH?"`
- **Dataclasses:** `PrecipitationBucket`, `PrecipitationMarketGroup`
  - `PrecipitationMarketGroup.period` = "daily" or "monthly" (monthly = full-month aggregate)
- **Distribution:** Gamma (via `PrecipitationProbabilityEngine`)
- **Ensemble monthly:** `get_monthly_precipitation_ensemble()` = archive actuals (elapsed days) + GEFS/ECMWF/AIFS ensemble (remaining days). Returns per-member March total in inches.
  - ECMWF IFS only forecasts 15 days → excluded when remaining_days > ~21. Logs `monthly_precip_model_no_members` warning.
  - NDFD PoP NOT blended for monthly (only blended for daily markets)
- **Ensemble daily:** `get_precipitation_ensemble()` + `get_ndfd_pop()` (40% blend)
- **Active markets (2026-03):** 2 groups (NYC, Seattle), 13 markets total

### Snowfall (tag_slug=snowfall)
- **Regex (3 patterns):** `_RE_SNOW_RANGE_V2`, `_RE_SNOW_BELOW_V2`, `_RE_SNOW_HIGHER_V2`
- **Dataclasses:** `SnowfallBucket`, `SnowfallMarketGroup` (field: `snow_unit`, NOT `precip_unit`)
- **Distribution:** Gamma (reuses `PrecipitationProbabilityEngine`)
- **Ensemble:** `get_snowfall_ensemble()` — `snowfall_sum`, cm → inches for US
- **Active markets (2026-03):** 0 events on Polymarket. Scanner ready.

### Wind Gusts (tag_slug=wind)
- **Regex (3 patterns):** `_RE_WIND_RANGE_V2`, `_RE_WIND_BELOW_V2`, `_RE_WIND_HIGHER_V2`
- **Dataclasses:** `WindBucket`, `WindMarketGroup`
- **Distribution:** Normal CDF via `math.erf` (no scipy needed)
- **Ensemble:** `get_wind_ensemble()` — `wind_gusts_10m_max`, km/h → mph via `val / 1.609` (CORRECT division)
- **Active markets (2026-03):** 0 events on Polymarket. Scanner ready.

### Active Market Snapshot (2026-03-09)
| Type | Tag Slug | Events | Markets | Groups | Status |
|------|----------|--------|---------|--------|--------|
| Temperature | temperature | ~44 | 407 | 44 | Trading (Dallas NO, etc.) |
| Precipitation | precipitation | 2 | 13 | 2 (NYC, Seattle) | Scanning; edges exist but 429 blocks |
| Snowfall | snowfall | 0 | 0 | 0 | Ready, no markets |
| Wind | wind | 0 | 0 | 0 | Ready, no markets |

---

## 6. KEY INTERFACES & SIGNATURES

### forecast_client.py — WeatherForecastClient
```python
# Temperature
get_combined_forecast(station: WeatherStation, target_date: date) -> Optional[CombinedForecast]
# 133 members, 1800s cache (_cache). Combines GEFS(31) + ECMWF IFS(51) + AIFS(51)

get_nbm_forecast(station, target_date) -> Optional[float]         # US only, NWS NBM deterministic
get_climate_normal(lat, lon, date, temp_unit) -> Optional[Tuple[float, float]]  # (mean, std), 10yr archive
get_ndfd_pop(station) -> Optional[List[Tuple[str, float, str]]]   # [(name, pop%, start_date_iso)]
get_afdbulletin(station) -> Optional[str]                          # NWS Area Forecast Discussion text

# Precipitation
get_precipitation_ensemble(station, target_date) -> Optional[List[float]]   # daily mm/inches per member
get_monthly_precipitation_ensemble(station, month: int, year: int) -> Optional[List[float]]  # monthly total per member

# Snowfall
get_snowfall_ensemble(station, target_date) -> Optional[List[float]]  # daily cm→inches per member

# Wind
get_wind_ensemble(station, target_date) -> Optional[List[float]]      # daily km/h→mph per member

# Infrastructure
get_session() -> aiohttp.ClientSession        # shared session — DO NOT create new sessions
invalidate_forecast_cache()                    # clears ONLY _cache (temperature). Precip/snow/wind live.
```

### probability_engine.py — WeatherProbabilityEngine
```python
fit_distribution(ensemble_members, lead_time_hours, station_id) -> Tuple[float, float, float]  # (loc, scale, shape)
bucket_probabilities(loc, scale, shape, buckets, lead_time_hours) -> Dict[str, float]  # {market_id: prob}
compute_edges(model_probs, market_prices) -> List[Dict]  # [{market_id, model_prob, market_price, edge, abs_edge, side}]
apply_climate_prior(loc, scale, clim_mean, clim_std, lead_time_hours) -> Tuple[float, float]
load_calibration(calibration_data)            # load bias corrections
load_emos_calibration(a, b, station_id)       # load OLS EMOS per station
load_tail_calibration(tail_data)              # load isotonic tail bins
```

### precipitation_engine.py — PrecipitationProbabilityEngine
```python
compute_bucket_probabilities(
    ensemble_members: List[float],
    buckets: List[PrecipitationBucket|SnowfallBucket],
    ndfd_pop: Optional[float] = None
) -> Dict[str, float]  # {market_id: prob}
# Uses Gamma MLE for wet members; P(wet) × P(amount|wet)
# Falls back to empirical distribution if <3 wet members
# ndfd_pop blended 40% for daily, NOT blended for monthly

compute_edges(
    model_probs: Dict[str, float],
    buckets: List[PrecipitationBucket|SnowfallBucket],
    min_edge: float = 0.08
) -> List[Dict]  # [{market_id, token_id, side, model_prob, price, edge, abs_edge, confidence}]
```

### market_mapper.py — WeatherMarketMapper
```python
# Temperature
group_markets(weather_markets: List[dict]) -> List[WeatherMarketGroup]
parse_market(market_data: dict) -> Optional[TemperatureBucket]
lookup_station(city_text: str) -> Optional[WeatherStation]  # fuzzy match with aliases

# Precipitation
group_precipitation_markets(markets: List[dict]) -> List[PrecipitationMarketGroup]
parse_precipitation_market(market_data: dict) -> Optional[PrecipitationBucket]
_extract_precip_city_and_date(question: str) -> Tuple[str, Optional[date], str]  # (city, date, period_type)

# Snowfall
group_snowfall_markets(markets: List[dict]) -> List[SnowfallMarketGroup]
parse_snowfall_market(market_data: dict) -> Optional[SnowfallBucket]

# Wind
group_wind_markets(markets: List[dict]) -> List[WindMarketGroup]
parse_wind_market(market_data: dict) -> Optional[WindBucket]

# Helpers
_parse_month_period(month_str: str) -> Optional[date]  # "March" → last day of month in current year
```

### Dataclass quick reference
```python
# Core
WeatherStation: city_name, station_id, ghcnd_id, latitude, longitude, elevation_m, timezone, temp_unit, aliases

# Temperature
TemperatureBucket: market_id, token_id, no_token_id, yes_price, bucket_type, low_bound, high_bound, temp_unit
WeatherMarketGroup: city, target_date, station, buckets(List[TemperatureBucket]), slug_prefix, temp_unit

# Precipitation
PrecipitationBucket: market_id, token_id, no_token_id, yes_price, bucket_type, low_bound, high_bound, precip_unit
PrecipitationMarketGroup: city, target_date, station, buckets(List[PrecipitationBucket]), slug_prefix, precip_unit, period

# Snowfall — NOTE: field is snow_unit, NOT precip_unit
SnowfallBucket: market_id, token_id, no_token_id, yes_price, bucket_type, low_bound, high_bound, snow_unit
SnowfallMarketGroup: city, target_date, station, buckets(List[SnowfallBucket]), slug_prefix, snow_unit

# Wind
WindBucket: market_id, token_id, no_token_id, yes_price, bucket_type, low_bound, high_bound, wind_unit
WindMarketGroup: city, target_date, station, buckets(List[WindBucket]), slug_prefix, wind_unit

# Forecast
CombinedForecast: ensemble_members, deterministic_high, model_spread, lead_time_hours, models_used
```

---

## 7. CONFIGURATION — LIVE VALUES

### BotBankrollManager (bankroll_manager.py)
```python
WeatherBot: {capital: 5000, kelly_fraction: 0.25, max_bet_usd: 500, max_daily_usd: 2000}
```
**Kelly graduation** (auto-upgrades/downgrades based on performance):
- 100+ resolved + 7-day MSE < 9 → kelly_fraction 0.25 → 0.35
- 200+ resolved + 7-day MSE < 4 → kelly_fraction 0.35 → 0.50
- MSE degrades → auto-downgrade

### settings.py defaults (CRITICAL — confirm .env overrides on VPS)
```python
WEATHER_MIN_EDGE = 0.08                    # 8% minimum edge (was 0.15, fixed Session 66)
WEATHER_MIN_CONFIDENCE = 0.10              # Risk manager floor (was 0.60 → 0.15 → 0.10)
WEATHER_MAX_PER_GROUP_USD = 1000           # Max per (city, date) group
WEATHER_DAILY_LOSS_LIMIT = 2000            # Daily loss halt (uses self.bankroll.capital, NOT hardcoded)
WEATHER_MAX_CORRELATED_EXPOSURE = 2000     # Max per city across dates
WEATHER_MAX_TOTAL_EXPOSURE_USD = 50000     # Bot-scoped total exposure (was global $10K, Session 68)
WEATHER_KELLY_FRACTION = 0.25              # Quarter-Kelly (graduates dynamically)
WEATHER_DEFAULT_SIZE = 25                  # Fallback when Kelly fails
WEATHER_MAX_LEAD_TIME_HOURS = 168          # 7-day max
WEATHER_FORECAST_CACHE_TTL = 1800          # 30-min forecast cache (was 900s, raised Session 68)
SCAN_INTERVAL_WEATHER = 300                # 5-min default scan
WEATHER_MAX_POSITIONS = 200                # Override global 50 (Session 66)
```

### Graduated edge caps (by lead time)
```python
<6h:   0.70  # METAR available
<12h:  0.50  # final-call
<24h:  0.40  # day-of
<48h:  0.30  # moderate
>=48h: 0.25  # conservative
```

### Boost stacking formula
```python
# Boosts are additive (not multiplicative), then capped
combined_boost = 1 + (expiry_boost - 1) + (regime_boost - 1) * 0.5 + (severe_boost - 1) * 0.5
combined_boost = min(combined_boost, 2.0)

# Baker-McHale k* position sizing
sigma = model_spread / 3.0               # model_spread from CombinedForecast
k_star = 1 / (1 + sigma ** 2)           # k* = 0.5 at sigma=1.0, 0.5 for temp typical values

final_kelly_multiplier = combined_boost * k_star
```

### Boost values
```python
# Expiry boost
<12h: 2.0x | <24h: 1.5x | <48h: 1.2x | >=48h: 1.0x

# Regime boost (≥3 US cities unanimous direction)
1.2x boost | applied × 0.5 in additive formula

# Severe weather boost (NWS alerts, US only)
Hurricane: 2.0x | Tornado/Blizzard: 1.5x | applied × 0.5 in additive formula
```

### Adaptive scan intervals (NWP model update windows)
```python
ECMWF: 07:00-08:00, 18:00-19:00 UTC → 60s scan, invalidate_forecast_cache() (temp only)
GFS:   05:15-06:00, 17:15-18:00 UTC → 90s scan, invalidate_forecast_cache() (temp only)
HRRR:  :40-:59 each hour           → 120s scan
Default:                            → 300s scan
```

**NOTE:** `invalidate_forecast_cache()` only clears `_cache` (temperature). Precip/snowfall/wind caches (`_precip_cache`, `_snowfall_cache`, `_wind_cache`) survive NWP invalidations and live for their full TTL.

### Open-Meteo API quota management
```
Free tier: ~10,000 calls/day
With 44 groups, 3 models, 1800s TTL: ~4,320 temp calls/day + ~288 precip calls/day ≈ 4,608 total
If groups grow to 60: ~5,760 + precip ≈ 6,000 — still within limit
IF 429 returns: raise WEATHER_FORECAST_CACHE_TTL to 3600 in settings.py + redeploy
```

---

## 8. DB SCHEMA

### weather_calibration
```sql
id BIGSERIAL PK
station_id VARCHAR(20)
target_date TIMESTAMP WITHOUT TIME ZONE
forecast_temp DOUBLE PRECISION
actual_temp DOUBLE PRECISION              -- nullable; backfilled by WU or Open-Meteo archive
lead_time_hours DOUBLE PRECISION
bias DOUBLE PRECISION                      -- nullable; = actual_temp - forecast_temp
crps FLOAT                                 -- nullable; Ferro 2014 fair CRPS (migration 032 + ORM Session 69)
model_name VARCHAR(50)
regime VARCHAR(20)                         -- 'el_nino', 'la_nina', 'neutral'
created_at TIMESTAMP
UNIQUE(station_id, target_date, lead_time_hours)
```

### weather_forecasts
```sql
id BIGSERIAL PK
station_id VARCHAR(20)
target_date TIMESTAMP WITHOUT TIME ZONE
forecast_time TIMESTAMP WITHOUT TIME ZONE
lead_time_hours DOUBLE PRECISION
ensemble_members JSONB                     -- List[float] stored as JSONB
deterministic_high DOUBLE PRECISION
model_spread DOUBLE PRECISION
models_used JSONB
created_at TIMESTAMP
UNIQUE(station_id, target_date, forecast_time)
```

### weather_tail_calibration (migration 033, Session 69)
```sql
id BIGSERIAL PK
bucket_type VARCHAR(20) NOT NULL           -- 'range', 'at_or_below', 'at_or_higher'
lead_time_bucket INTEGER NOT NULL          -- 0=<6h, 1=6-24h, 2=24-72h, 3=72h+
model_prob FLOAT NOT NULL
actual_outcome INTEGER NOT NULL            -- 0 or 1
station_id VARCHAR(20)
created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT NOW()
INDEX idx_tail_cal_bucket (bucket_type, lead_time_bucket)
```
**Note:** Table was created by an earlier session before migration was written. Table exists on VPS and has rows. Migration 033 documented the schema in git.

### positions (WeatherBot rows)
```sql
bot_id = 'WeatherBot'
market_id, side (YES/NO), size (shares), entry_price
current_price FLOAT                        -- auto-updated every 10s by position_manager
status: 'open' / 'closed'
opened_at, closed_at TIMESTAMP
```

### paper_trades (WeatherBot rows)
```sql
bot_name = 'WeatherBot'
market_id, side (YES/NO), size (shares), price, created_at
```

### glicko2_ratings (EsportsBot — NOT WeatherBot scope)
Exists in same DB. Do not confuse.

---

## 9. CALIBRATION SYSTEM

```
EVERY SCAN — _maybe_update_calibration_actuals():
  → Find weather_calibration rows: actual_temp IS NULL AND target_date < NOW()
  → Attempt WU daily high (preferred — WU is Polymarket's resolution source)
    - Browser UA: Chrome 122 (4-pattern regex fallback chain)
    - Sanity check: reject if >10°F/5°C from Open-Meteo (logs weatherbot_wu_sanity_rejected)
    - URL format: d.isoformat() for zero-padding (NOT f"{d.year}-{d.month}-{d.day}")
  → Fallback: Open-Meteo archive API
  → UPDATE actual_temp, bias = actual_temp - forecast_temp, crps = _compute_crps(...)

EVERY 6 HOURS — _maybe_reload_calibration():
  → Load weather_calibration rows per station
  → Compute per-station EMOS (OLS: actual_temp = a + b * forecast_temp)
    - Regime-conditioned EMOS when ≥20 samples per regime (el_nino/la_nina/neutral)
    - Regime from NOAA PSL Nino 3.4 anomaly data (24h cache)
  → Load weather_tail_calibration → isotonic bins per (bucket_type, lead_time_bucket)
    - Falls back to 0.90 if <5 data points per bin
  → prob_engine.load_calibration(), load_emos_calibration(), load_tail_calibration()

EVERY 10 MINUTES — _check_monitoring_thresholds():
  → Compute 7-day Brier score (MSE of model_prob vs actual_outcome)
  → MSE > 25 → CRITICAL halt (stop all trading)
  → MSE > 16 → WARNING log
  → Dynamic Kelly graduation check (see Section 7)
  → CRPS scoring via _compute_crps() (Ferro 2014 fair CRPS)

EMOS TIMELINE:
  Started collecting actuals: ~March 8, 2026
  ~3 actuals/station/day → 20 actuals needed
  Expected EMOS activation: ~March 15-17 for early stations
  Until then: raw EMOS coefficients (a=0, b=1 — identity)
```

---

## 10. FEATURE INVENTORY — ALL IMPLEMENTED

| Feature | Location | Description |
|---------|----------|-------------|
| Tag-based market discovery | weather_bot.py | Gamma API `tag_slug=temperature/precipitation/snowfall/wind` |
| 133-member ensemble | forecast_client.py | GEFS(31) + ECMWF IFS(51) + ECMWF AIFS(51) |
| NWS NBM deterministic | forecast_client.py | US-only, 2-step /points → /forecast |
| Skew-normal distribution | probability_engine.py | MLE fit, normal fallback |
| Gamma distribution | precipitation_engine.py | MLE fit, P(wet)×P(amount\|wet) |
| Normal CDF | weather_bot.py | math.erf for wind gusts |
| EMOS calibration | probability_engine.py | OLS per station × lead_time bucket (6h bins) |
| Regime-conditioned EMOS | weather_bot.py | NOAA Nino 3.4, ≥20 per regime |
| Isotonic tail calibration | probability_engine.py | Fallback 0.90 |
| Climate normals prior | forecast_client.py | 10-year archive, blend 0-40% at >72h |
| AFD spread adjustment | weather_bot.py | NWS Area Forecast Discussion |
| METAR override (<6h) | weather_bot.py | Running daily max overrides model |
| METAR <2h aggressiveness | weather_bot.py | Range boost 0.92, tighter margins (0.5°F US / 0.3°C intl) |
| Severe weather boost | weather_bot.py | NWS alerts: 2.0x hurricane, 1.5x tornado/blizzard |
| Cross-city regime boost | weather_bot.py | ≥3 US cities unanimous → 1.2x |
| S-T multi-bucket sizing | weather_bot.py | `_smoczynski_tomkins_allocate()` pro-rata by edge |
| Baker-McHale k* | weather_bot.py | k* = 1/(1+σ²) where σ=model_spread/3.0 |
| Expiry boost | weather_bot.py | <12h=2.0, <24h=1.5, <48h=1.2 |
| Combined boost stacking | weather_bot.py | Additive, capped 2.0x |
| Graduated edge cap | weather_bot.py | <6h=0.70, <12h=0.50, <24h=0.40, <48h=0.30 |
| Boundary risk discount | weather_bot.py | 0.5°F US / 0.3°C intl → 50% confidence halving |
| Penny-bet filter | weather_bot.py | Skip price ≤0.05 or ≥0.95 |
| Adaptive scan interval | weather_bot.py | 60s ECMWF, 90s GFS, 120s HRRR, 300s default |
| Slippage guard | weather_bot.py | LiquidityGuardian |
| Position re-evaluation | weather_bot.py | Fresh probs → exit logic per scan |
| Dynamic Kelly graduation | weather_bot.py | 100+/MSE<9→0.35, 200+/MSE<4→0.50, auto-downgrades |
| CRPS scoring | weather_bot.py | Ferro 2014 fair CRPS via _compute_crps() |
| Forecast persistence | weather_bot.py | CAST(:x AS jsonb) syntax for asyncpg |
| NO-side confidence | weather_bot.py | YES=model_prob, NO=1-model_prob (halved if boundary_risk) |
| WU resolution verification | weather_bot.py | WU is Polymarket's resolution source |
| WU scraping robustness | weather_bot.py | Chrome 122 UA, 4-pattern regex chain, weatherbot_wu_no_match log |
| WU sanity check | weather_bot.py | Reject >10°F/5°C from Open-Meteo |
| Parse caches (4) | market_mapper.py | Per-type: temp, precip, snow, wind (skip regex on seen IDs) |
| Batch NWS alerts | weather_bot.py | One pass per scan for all US stations |
| AlertingSystem | weather_bot.py | station-offline, tag-fetch-fail, daily-loss-limit-hit |
| Precipitation markets (M1) | weather_bot.py | Gamma distribution, V1+V2 regex, monthly ensemble |
| Snowfall markets (M2) | weather_bot.py | Gamma, tag_slug=snowfall, snow_unit field |
| Wind gust markets (M3) | weather_bot.py | Normal CDF (math.erf), tag_slug=wind |
| Monthly precip ensemble | forecast_client.py | Archive actuals + ensemble forecasts → member totals |
| Shared aiohttp session | forecast_client.py | `get_session()` reused everywhere |
| Intra-day P&L refresh | weather_bot.py | `_restore_daily_pnl_from_db()` every scan |
| Bot-scoped exposure limit | risk_manager.py | `og.get_bot_exposure_usd()`, WEATHER_MAX_TOTAL_EXPOSURE_USD=50000 |
| Selective cache invalidation | forecast_client.py | `invalidate_forecast_cache()` only clears `_cache` |
| ECMWF exclusion warning | forecast_client.py | `monthly_precip_model_no_members` log when model excluded |

---

## 11. TRAPS, GOTCHAS & PATTERNS

### Platform-wide critical patterns
- **BUY/SELL vs YES/NO**: `BaseBot.place_order()` expects `side="YES"` or `side="NO"`. NEVER pass "BUY"/"SELL".
- **asyncpg JSONB**: Use `CAST(:x AS jsonb)` NOT `:x::jsonb`. asyncpg misparses `::` as parameter suffix.
- **asyncpg datetime**: Timestamp columns need `datetime.fromisoformat()`, not raw ISO strings.
- **PSEUDO_LABEL_ENABLED=false**: DO NOT enable. Only Location 1 (market resolution) labels are correct.
- **ENSEMBLE_BLEND=1.0**: Bypasses learning_conf.
- **`websockets.exceptions`**: Must be imported explicitly (v15 lazy-loads).
- **`risk_manager.calculate_position_size()`**: DEPRECATED. BotBankrollManager used instead.

### WeatherBot-specific
- **4 typed forecast caches**: `_cache` (CombinedForecast), `_precip_cache` (List[float]), `_snowfall_cache` (List[float]), `_wind_cache` (List[float]). NEVER mix types between caches.
- **4 parse caches**: `_parse_cache`, `_precip_parse_cache`, `_snow_parse_cache`, `_wind_parse_cache` in market_mapper. Skip regex on seen market IDs. Each type has its own cache.
- **Shared aiohttp session**: `forecast_client.get_session()` reused by WU scraper, NWS alerts, ALL ensemble fetches. Do NOT create new sessions per-call.
- **V1 + V2 precip regex**: Both preserved. V1 = hypothetical format, V2 = actual Polymarket format (confirmed 2026-03). V2 matches "CITY have [less than/between X and Y/more than] inches of precipitation in MONTH".
- **Monthly vs daily precip**: `PrecipitationMarketGroup.period` field. Monthly uses `get_monthly_precipitation_ensemble()`, daily uses `get_precipitation_ensemble()`. NDFD PoP NOT blended for monthly.
- **SnowfallBucket field**: `snow_unit`, NOT `precip_unit`. Easy field name confusion.
- **WU URL zero-padding**: Must use `d.isoformat()` (e.g., "2026-03-08"). Using `f"{d.year}-{d.month}-{d.day}"` produces "2026-3-8" → 404.
- **WU scraping**: Chrome 122 UA required. 4-pattern regex. `weatherbot_wu_no_match` debug log if all fail. Sanity check rejects if >10°F/5°C from Open-Meteo.
- **NWS NDFD PoP**: Filter by `startTime` ISO date string, NOT period name. Returns list of `(name, pop%, start_date_iso)` tuples.
- **NWS API**: Requires `Accept: application/geo+json` header or returns 406.
- **TemperatureBucket requires `temp_unit`** field in constructor or TypeError.
- **`_precip_to_temp_group()` returns `buckets=[]`**: INTENTIONAL. `_execute_weather_trade` uses `group.city`, `group.target_date`, `group.station` — never iterates `group.buckets`.
- **Wind conversion**: `val / 1.609` converts km/h → mph. `100 km/h / 1.609 = 62.1 mph`. This is CORRECT.
- **ECMWF monthly exclusion**: Expected log: `monthly_precip_model_no_members model=ecmwf_ifs025` when >21 remaining days. Informational, not an error. Monthly ensemble falls back to GFS-only.
- **Weather market IDs ~249000+**: Far beyond standard ingestion pipeline reach. tag_slug discovery is mandatory.
- **CLOB markets have volume=0**: Don't use volume gates for weather.
- **`_execute_weather_trade` group exposure tracking**: Uses `group_key = f"{city}:{date}"` set correctly in all market types.

### Bot-scoped limits (WeatherBot-specific overrides in risk_manager.py)
```python
if bot_name == "WeatherBot":
    max_positions = settings.WEATHER_MAX_POSITIONS   # 200 (not global 50)
    total_exposure = og.get_bot_exposure_usd("WeatherBot")    # bot-scoped (not global)
    max_total_exposure = settings.WEATHER_MAX_TOTAL_EXPOSURE_USD  # 50000 (not global 10000)
```

### API patterns
```python
# Gamma API (Polymarket)
"https://gamma-api.polymarket.com/events?active=true&closed=false&tag_slug={slug}&limit=100"
# Returns events with markets[].outcomePrices for YES prices

# Open-Meteo ensemble
"https://ensemble-api.open-meteo.com/v1/ensemble?latitude={lat}&longitude={lon}&daily={vars}&models={model}&forecast_days=16"
# Models: gfs025 (31 members), ecmwf_ifs025 (51), ecmwf_aifs025 (51)

# Open-Meteo archive (actuals)
"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}&start_date={d}&end_date={d}&daily=temperature_2m_max,precipitation_sum"

# NWS points
"https://api.weather.gov/points/{lat},{lon}"  # → properties.forecastGridData, properties.gridId (WFO)

# NWS NBM forecast
"https://api.weather.gov/gridpoints/{wfo}/{x},{y}/forecast"  # US only, 2-step lookup

# NWS alerts
"https://api.weather.gov/alerts/active?point={lat},{lon}"    # US only, 30min cache

# NWS NDFD PoP
"https://api.weather.gov/points/{lat},{lon}/forecast"        # → periods[].probabilityOfPrecipitation

# NWS AFD
"https://api.weather.gov/products?type=AFD&location={wfo}&limit=1"  # → productText

# Weather Underground history
"https://www.wunderground.com/history/daily/{station}/date/{d.isoformat()}"  # WU is Polymarket's resolution source

# NOAA PSL Nino 3.4
"https://psl.noaa.gov/data/correlation/nina34.anom.data"  # regime detection, 24h cache
```

### Database
- **paper_trades schema**: `bot_name` column. **positions schema**: `bot_id` column.
- **Database session pattern**: `db.get_session()` → `async with ... as session:` → `session.execute(text(...))`.
- **weather_calibration.crps**: In DB (migration 032) AND in ORM (added Session 69). Safe for ORM access.

---

## 12. SESSION HISTORY (53–69)

### Session 69 (2026-03-09) — Full Codebase Audit + Boot Blocker Fix
**Commit:** `65abd0e`
**Critical finding:** WeatherBot was not importable locally (though VPS worked due to no-git deploy pattern).
- **Boot blocker FIXED**: `market_mapper.py` (1105 lines) and `precipitation_engine.py` (231 lines) were in working tree but never committed. Sessions 66/67 never ran `git add`. Committed in 69.
- **ECMWF silent exclusion**: Added `monthly_precip_model_no_members` warning log in `forecast_client.py`
- **crps ORM gap FIXED**: Added `crps = Column(Float, nullable=True)` to WeatherCalibration ORM (`database.py:743`)
- **Migration 033 CREATED**: `schema/migrations/033_weather_tail_calibration.sql` — documents the tail calibration table schema
- **False alarms confirmed**: Wind conversion `val / 1.609` is CORRECT. `buckets=[]` in `_precip_to_temp_group` is intentional.
- Tests: 1333 passed. VPS clean boot confirmed.

### Session 68 (2026-03-09) — Exposure Limit + Open-Meteo 429 + WU Scraping
**Commit:** `5522654`
- **Exposure blocker FIXED**: Miami NO trade (13.7% edge) blocked every scan. Root cause: global $10K total exposure hit. Fix: bot-scoped `og.get_bot_exposure_usd("WeatherBot")` + `WEATHER_MAX_TOTAL_EXPOSURE_USD=50000`.
- **Open-Meteo 429 FIXED**: 44 groups × 3 models × 900s TTL = ~11K calls/day > 10K free tier. Fix: TTL 900→1800s. `invalidate_forecast_cache()` now selective (temp only).
- **WU scraping robustness**: Chrome 122 UA, 4-pattern regex chain, `weatherbot_wu_no_match` debug log.
- **snowfall/wind tag confirmed**: `tag_slug=snowfall` and `tag_slug=wind` → 0 events on Polymarket. Scanners ready.
- **NYC precip mispricings quantified**: "2–3 in" @ 8.45% YES (model ~68%) — edge ~60%. Expected to trade post-quota reset.

### Session 67 (2026-03-09) — Bugs A-E + Snowfall + Wind + Precip Regex Fix
**Commit:** `(prior to 5522654)`
- **Bug A**: Typed forecast caches — separate `_precip_cache`, `_snowfall_cache`, `_wind_cache` (was sharing `_cache`)
- **Bug B**: WU URL zero-padding — `d.isoformat()` instead of `f"{d.year}-{d.month}-{d.day}"`
- **Bug C**: NWS NDFD PoP filtering by ISO date, not period name string
- **Bug D**: Shared aiohttp session via `get_session()` — no more per-call sessions
- **Bug E**: `_precip_parse_cache` added to `group_precipitation_markets()`
- **WU sanity check**: Reject if >10°F/5°C from Open-Meteo
- **Snowfall pipeline (M2)**: Full end-to-end — `get_snowfall_ensemble()`, `SnowfallBucket`, `_scan_snowfall_markets()`
- **Wind pipeline (M3)**: Full end-to-end — `get_wind_ensemble()`, `WindBucket`, normal CDF
- **ROOT CAUSE FIX**: Precipitation V2 regex — actual Polymarket format confirmed
- **Monthly ensemble**: `get_monthly_precipitation_ensemble()` — archive actuals + ensemble forecasts
- **`_parse_month_period()`**: "March" → last day of month
- **Result**: `precip_scan_done groups=2 markets=13` (was 0). Deployed 15:16 UTC.

### Session 66 (2026-03-09) — Bugs + Precipitation Markets + Unblock
- **Bug 1**: Drawdown check hardcoded capital → `self.bankroll.capital`
- **Bug 2**: Intra-day P&L refresh every scan (was only at day boundary)
- **Bug 3**: Climate normal rate limiter: bool→counter, 3 per cycle
- **Performance**: Batch NWS alerts (one pass per scan for all US stations)
- **WU resolution verification (B1)**: WU is Polymarket's resolution source; preferred for EMOS actuals
- **Parse cache (B2)**: `WeatherMarketMapper._parse_cache` skips regex on seen market IDs
- **Semaphore (B3)**: `asyncio.Semaphore(10)` for parallelized CLOB enrichment
- **AlertingSystem (B4)**: station-offline, tag-fetch-fail, daily-loss-limit-hit events
- **Precipitation engine (NEW)**: `precipitation_engine.py` — Gamma distribution, NDFD PoP blending
- **MAX_POSITIONS blocker**: Added `WEATHER_MAX_POSITIONS=200` in settings + scoped risk_manager
- **MIN_EDGE**: Fixed to 0.08 (was 0.15 due to unchecked settings.py default)
- **MIN_CONFIDENCE**: Lowered to 0.10 (boundary-risk halving was pushing trades below threshold)
- **METAR <2h aggressiveness**: Range boost 0.92, unit-aware margins (0.5°F/0.3°C)

### Session 64 (2026-03-08) — Deep-Dive Tiers 2+3
- **S-T multi-bucket sizing (W3+W5)**: `_smoczynski_tomkins_allocate()` — pro-rata by edge for groups ≥2
- **Bayesian prior (E5)**: EsportsBot only — phi-based blending toward 0.50
- **Baker-McHale k* (W7)**: `k* = 1/(1+(model_spread/3.0)²)`
- **Dynamic Kelly graduation (W6)**: Auto-upgrade at 100+/MSE<9, 200+/MSE<4; auto-downgrade
- **CRPS scoring (W8)**: Ferro 2014 fair CRPS via `_compute_crps()`
- Tests: 1242 passed (pre-Session 66/67 additions).

### Session 62 (2026-03-08) — Market Discovery + Confidence Fix
- **ROOT FIX**: `_fetch_weather_events_by_tag()` via Gamma API `tag_slug=temperature` — bot was inert without this
- **NO-side confidence**: `1 - model_prob`
- **JSONB cast fix**: `CAST(:x AS jsonb)` syntax
- Bet sizing: $500 max bet, $2K daily (was $100/$500)

### Session 61 (2026-03-08) — Doom Loop Fix
- **M1 uniform fallback**: was creating fake edges → return {} (empty)
- **Cooldown fix**: Was not respecting exit cooldown
- **Confidence formula fix**: Was using wrong formula
- MIN_EDGE 0.15 → 0.08 (first time — reverted in settings.py, re-fixed Session 66)
- EnsembleBot archived (0.2% win rate, -$5.6K). 14 active bots now.

### Sessions 55–60
- **S55**: WS YES/NO token fix, label leakage prevention, ENSEMBLE_BLEND=1.0
- **S57**: Climate normals, AFD integration, self-scout 17 fixes
- **S58**: Regime EMOS (NOAA Nino 3.4), EsportsBot Glicko-2 blend for LoL
- **S59**: METAR client, WFO caching, NWS alerts, severe weather boosts
- **S60**: Kalshi integration (separate module, not WeatherBot scope)

---

## 13. KNOWN ISSUES & FUTURE WORK

### Immediate monitoring (check after UTC midnight 2026-03-10)
1. **Verify precipitation trades fire** — `grep weatherbot_precip_edges` or `weatherbot_trade_filled` in logs. NYC "2–3 in" YES @ 8.45% (model ~68%) should produce a near-certain edge.
2. **Verify no more 429s** — `grep "429"`. Should be clear post-reset with 1800s TTL.
3. **Verify no exposure blocks** — `grep "Total exposure"`. Should be clean with bot-scoped $50K cap.
4. **ECMWF monthly warning** — `grep monthly_precip_model_no_members` — expected for markets with >21 remaining days, informational only.

### Near-term
5. **Open-Meteo API calls audit** — with 44 groups at 1800s TTL = ~4,320 temp calls/day. If groups grow to 80+, may need to raise TTL to 3600s or combine temp+precip into single API call.
6. **Combine temp+precip into single API call** — `get_combined_forecast()` and `get_precipitation_ensemble()` both call ensemble API separately for same station. Combining `daily=temperature_2m_max,precipitation_sum` would halve per-station API calls. ~60 LOC refactor (Tier 2 complexity).
7. **EMOS calibration activation** — at ~3 actuals/station/day from March 8, expect activation ~March 15-17. Until then: raw forecast probs (no EMOS correction).
8. **WU HTML regex fragility** — Chrome UA improves acceptance. But WU Angular structure can change. 4-pattern chain is resilient but not future-proof. The sanity check catches errors.
9. **Snowfall/Wind V2 regex validation** — When snowfall/wind markets appear on Polymarket, verify actual question format matches `_RE_SNOW_RANGE_V2` / `_RE_WIND_RANGE_V2`. Precipitation V1/V2 mismatch was the root cause of 0 groups found until Session 67 fix.

### Not yet started
10. **Hurricane/Climate (M4)**: Would require NOAA CFS v3 seasonal data, different bot architecture. Separate `SeasonalWeatherBot` class. Significant new work.
11. **Polymarket tag validation** — Polymarket may tag snowfall/wind markets under different slugs. When first markets appear, manually curl to check: `tag_slug=snowfall`, `tag_slug=wind_gust`, `tag_slug=wind gusts`, etc.

### Not in scope for WeatherBot
- **EsportsBot**: `series_prob_with_map_veto()` wrong params in `esports_series_bot.py:269`
- **MirrorBot**: MAX_POSITIONS warnings (separate bot)
- **Kalshi**: RSA credentials still needed, paper trading not started

---

## 14. DEVELOPMENT RULES (CLAUDE.md)

**Prime Directive:** Working code is sacred. Fix only what is broken. Fix it at the root. If you cannot explain exactly why a line needs to change and exactly what breaks if you don't change it, do not change it.

### Before writing ANY code — complete this checklist
1. State the bug in one sentence. If you can't, you don't understand it yet.
2. List files you will touch. If more than 3, stop and justify.
3. Grep for dependents: `grep -rn "from <module> import" --include="*.py"`
4. Git snapshot: `git stash` or `git commit -m "pre-fix: <desc>"` before editing.
5. Read the ENTIRE file you're modifying, not just the function.

### Rules of engagement
- **One fix per commit** — no "while I'm in here" changes
- **Preserve function signatures** — unless signature IS the bug; then update every caller
- **Preserve external interfaces** — no API path changes, no DB column renames, no config key changes
- **No silent behavior changes** — document any behavior change explicitly
- **Never delete code you don't understand**
- **No new dependencies without justification**
- **No structural refactors during bug fixes**

### Config tuning tiers
- **Tier 1** (threshold tuning): State what changed + expected impact
- **Tier 2** (trade-universe gating): State trades now blocked/allowed + rollback command
- **Tier 3** (code changes): Full blast-radius analysis

### Forbidden patterns
- "While I'm in here" refactor
- Band-aid `try/except` hiding real errors
- Shotgun fix (changed 4 things hoping one works)
- Scope creep
- Silent migration (DB/config/API change without updating all consumers)
- Optimistic rewrite ("module is messy so I rewrote it")

### After every change
```bash
cd C:/lockes-picks/polymarket-ai-v2 && python -m pytest tests/ -x -q
# Must see 1333+ passed, 0 failed
```

### Change log format (mandatory)
```
## CHANGE: [date] (Session N)
**Issue:** [one sentence]
**Root cause:** [one sentence]
**Files modified:** [list]
**Lines changed:** [count per file]
**Blast radius:** [every module that depends on changed code]
**Verification:** [what you tested]
**Rollback:** git revert <sha>
```

---

## 15. SYSTEM CONTEXT (14-Bot Platform)

WeatherBot is one of 14 active bots. **MomentumBot DELETED. EnsembleBot ARCHIVED (-$5.6K).**

When modifying shared modules (`base_bot.py`, `bankroll_manager.py`, `risk_manager.py`, `position_manager.py`, `database.py`, `main.py`):
- Run full test suite — 1333+ passing required
- List every bot affected by name
- WeatherBot-specific overrides in risk_manager are scoped: `if bot_name == "WeatherBot"`

### Station Registry (30+ stations)
Major cities: NYC (KLGA), Chicago (KORD), Atlanta (KATL), Miami (KMIA), Dallas (KDFW), Seattle (KSEA), Denver (KDEN), Phoenix (KPHX), Houston (KIAH), San Francisco (KSFO), Toronto (CYYZ), London (EGLL), Paris (LFPG), Tokyo (RJTT), Sydney (YSSY), Buenos Aires (SAEZ), Sao Paulo (SBGR), Seoul (RKSI), Ankara (LTAC), Mumbai (VABB), and more.

Each station: `city_name, station_id (ICAO), ghcnd_id, lat/lon, elevation_m, timezone, temp_unit (F/C), aliases, resolution_source`

### Key integration points
| System | Purpose |
|--------|---------|
| Polymarket Gamma API | Market discovery (tag_slug), event prices |
| Polymarket CLOB API | Order placement, orderbook prices |
| Open-Meteo Ensemble API | 133-member ensemble (GEFS/ECMWF IFS/AIFS) |
| Open-Meteo Archive API | Historical actuals for EMOS calibration |
| NWS API | NBM forecasts, NDFD PoP, alerts, AFD, WFO grid |
| Weather Underground | Resolution verification (Polymarket's source) |
| NOAA PSL | Nino 3.4 anomaly for ENSO regime detection |
| METAR (Aviation WC) | Real-time airport obs for <6h front-running |

---

## 16. VERIFICATION COMMANDS

### Local (run on dev machine)
```bash
# Full test suite
cd C:/lockes-picks/polymarket-ai-v2 && python -m pytest tests/ -x -q

# Quick import check (critical — catches boot failures like Session 69 found)
python -c "import bots.weather_bot; print('WeatherBot import OK')"
python -c "from base_engine.weather.market_mapper import PrecipitationMarketGroup, SnowfallBucket, WindMarketGroup; print('Dataclasses OK')"

# Verify Gamma API tags
curl -s "https://gamma-api.polymarket.com/events?active=true&closed=false&tag_slug=precipitation&limit=5" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d),'precipitation events')"
curl -s "https://gamma-api.polymarket.com/events?active=true&closed=false&tag_slug=snowfall&limit=5" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d),'snowfall events')"
curl -s "https://gamma-api.polymarket.com/events?active=true&closed=false&tag_slug=wind&limit=5" | python3 -c "import sys,json; d=json.load(sys.stdin); print(len(d),'wind events')"

# Verify Open-Meteo ensemble
curl -s "https://ensemble-api.open-meteo.com/v1/ensemble?latitude=40.78&longitude=-73.97&daily=precipitation_sum&models=gfs025&forecast_days=3" | python3 -c "import sys,json; d=json.load(sys.stdin); print(list(d.get('daily',{}).keys())[:5])"
```

### VPS health checks
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Service status
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" 'systemctl is-active polymarket-ai && systemctl status polymarket-ai --no-pager | head -5'

# Temperature scan health (last 1h)
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "1 hour ago" | grep weatherbot_scan_done | tail -10'

# Precipitation scan health (last 1h)
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "1 hour ago" | grep weatherbot_precip_scan_done | tail -10'

# Trades placed (last 6h)
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "6 hours ago" | grep weatherbot_trade_filled'

# 429 quota check
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "1 hour ago" | grep "429"'

# Exposure blocks
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "30 minutes ago" | grep "Total exposure"'

# ECMWF monthly exclusion warning (expected for monthly precip markets)
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "1 hour ago" | grep monthly_precip_model_no_members'

# Errors / crashes
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "1 hour ago" | grep -i "error\|traceback\|importerror\|exception" | tail -20'

# Open positions
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  "PGPASSWORD='polymarket_s46' psql -h localhost -U polymarket -d polymarket -c \
  \"SELECT LEFT(market_id,12), side, size, entry_price, opened_at FROM positions WHERE bot_id='WeatherBot' AND status='open' ORDER BY opened_at DESC LIMIT 20;\""

# Calibration actuals progress
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  "PGPASSWORD='polymarket_s46' psql -h localhost -U polymarket -d polymarket -c \
  \"SELECT station_id, COUNT(*) actuals FROM weather_calibration WHERE actual_temp IS NOT NULL GROUP BY station_id ORDER BY actuals DESC LIMIT 15;\""
```

---

## CHANGE LOG (Sessions 66–69)

```
## CHANGE: 2026-03-09 (Session 69) — Commit 65abd0e
**Issue 1:** WeatherBot boot failure — ImportError on PrecipitationMarketGroup
**Root cause:** market_mapper.py and precipitation_engine.py had critical classes in working tree but were never committed (Sessions 66/67 never ran git add)
**Files modified:** base_engine/weather/market_mapper.py (working tree committed), base_engine/weather/precipitation_engine.py (new committed file)
**Lines changed:** +813 market_mapper, +231 precipitation_engine
**Blast radius:** WeatherBot only
**Verification:** python -c "import bots.weather_bot; print('OK')" → OK; VPS process 1766867 clean start

**Issue 2:** ECMWF IFS silently excluded from monthly precip ensemble when remaining_days > 21
**Root cause:** 70% coverage threshold drops ECMWF when its 15-day horizon can't cover remaining days
**Files modified:** base_engine/weather/forecast_client.py (+6 lines)
**Blast radius:** WeatherBot monthly precip logging only
**Verification:** monthly_precip_model_no_members warning now emitted

**Issue 3:** crps column missing from WeatherCalibration ORM
**Root cause:** Migration 032 added DB column but ORM model not updated
**Files modified:** base_engine/data/database.py (+1 line)
**Blast radius:** WeatherBot CRPS ORM access
**Verification:** ORM column confirmed via Read + grep

**Issue 4:** weather_tail_calibration table had no migration file
**Root cause:** Table queried in code but never documented in schema/migrations/
**Files added:** schema/migrations/033_weather_tail_calibration.sql (+18 lines)
**Blast radius:** None (documentation only; table already existed on VPS)
**Rollback:** git revert 65abd0e

---

## CHANGE: 2026-03-09 (Session 68) — Commit 5522654
**Issue 1:** Global exposure check blocked WeatherBot trades (Miami NO, 13.7% edge, blocked every scan)
**Root cause:** RISK_MAX_TOTAL_EXPOSURE_USD=10000 uses all-bot combined; WeatherBot alone at $10,988
**Files modified:** base_engine/risk/risk_manager.py, config/settings.py
**Lines changed:** +8 risk_manager, +2 settings
**Blast radius:** WeatherBot only (scoped if bot_name == "WeatherBot")
**Verification:** No "Total exposure" WeatherBot blocks post-deploy
**Rollback:** git revert 5522654

**Issue 2:** Open-Meteo 429 — daily quota hit by mid-day; precip ensemble returns None; precip_trades=0
**Root cause:** 900s TTL × 44 groups × 3 models × 2 = ~11,000 calls/day exceeds free tier (~10,000)
**Files modified:** config/settings.py (TTL 900→1800), base_engine/weather/forecast_client.py
**Lines changed:** +1 settings, +11 forecast_client
**Blast radius:** Temperature forecast freshness (30-min vs 15-min refresh; acceptable trade-off)
**Rollback:** git revert 5522654

**Issue 3:** WU scraping fragile (bot-like UA, only 2 regex patterns)
**Root cause:** Single UA likely rate-limited; Angular DOM layout may vary
**Files modified:** bots/weather_bot.py
**Lines changed:** +28/-12 in _fetch_wu_daily_high
**Blast radius:** WeatherBot calibration actuals backfill only
**Rollback:** git revert 5522654
```

---

**END OF HANDOFF. A new agent reading this document has complete context to continue all WeatherBot development seamlessly through Session 69.**
