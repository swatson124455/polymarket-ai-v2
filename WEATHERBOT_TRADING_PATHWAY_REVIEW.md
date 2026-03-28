# WeatherBot — Complete Trading Pathway Review

**Date**: 2026-03-27
**System**: polymarket-ai-v2
**Bot**: WeatherBot (1 of 14 bots in BOT_REGISTRY)
**Status**: Paper trading (SIMULATION_MODE=true, $0 execution)
**Runtime**: Python 3.13 async on Ubuntu VPS (34.251.224.21, 16GB/4vCPU)

---

## TABLE OF CONTENTS

1. [System Overview](#1-system-overview)
2. [Market Discovery](#2-market-discovery)
3. [Market Parsing & Matching](#3-market-parsing--matching)
4. [Forecast Acquisition](#4-forecast-acquisition)
5. [Probability Computation](#5-probability-computation)
6. [Calibration System](#6-calibration-system)
7. [Edge Computation & Filtering](#7-edge-computation--filtering)
8. [Confidence Calibration (Logistic Regression)](#8-confidence-calibration-logistic-regression)
9. [Trade Sizing (Kelly Criterion)](#9-trade-sizing-kelly-criterion)
10. [Combined Boost Multipliers](#10-combined-boost-multipliers)
11. [Risk Management Gates](#11-risk-management-gates)
12. [Order Execution](#12-order-execution)
13. [Position Management & Exits](#13-position-management--exits)
14. [Resolution & P&L](#14-resolution--pl)
15. [Data Storage & Persistence](#15-data-storage--persistence)
16. [Configuration Reference](#16-configuration-reference)

---

## 1. SYSTEM OVERVIEW

### What WeatherBot Does

WeatherBot trades temperature prediction markets on Polymarket. Each market asks: "Will the highest temperature in [City] be [range/above/below] on [Date]?" The bot:

1. Discovers active weather markets on Polymarket
2. Matches each market to a weather station (106 cities registered)
3. Fetches ensemble weather forecasts (133 members from 3 NWP models)
4. Computes probability of each temperature outcome via CDF integration
5. Compares model probability against market price to find edge
6. Calibrates confidence using logistic regression (4 features)
7. Sizes position using Kelly criterion with multiple boost/dampener factors
8. Executes via paper trading engine (VWAP fill simulation)
9. Monitors positions and exits via alpha decay or resolution

### Architecture Position

```
main.py → BotScheduler → WeatherBot.scan_and_trade() [every 5 min]
                        → 13 other bots (MirrorBot, EsportsBot, etc.)

WeatherBot dependencies:
  ├── WeatherForecastClient      (Open-Meteo API: GEFS+ECMWF ensembles)
  ├── WeatherProbabilityEngine   (forecast → bucket probability via CDF)
  ├── WeatherMarketMapper        (question text → city/date/bucket)
  ├── WeatherConfidenceCalibrator (LogisticRegression, 4-feature)
  ├── BotBankrollManager         (Kelly sizing, max_bet, daily cap)
  ├── PaperTradingEngine         (VWAP fill simulation)
  ├── MetarClient / MetarMonitor (live airport weather observations)
  ├── ModelRunMonitor             (GFS/ECMWF/HRRR model run tracking)
  ├── Database                   (trade_events, positions, paper_trades)
  └── Redis                      (exit cooldowns, backoff state, 429 tracking)
```

### Key Files

| File | Lines | Role |
|------|-------|------|
| `bots/weather_bot.py` | ~4700 | Main bot logic, calibrator class, all gates |
| `base_engine/weather/probability_engine.py` | ~500 | Forecast → probability via CDF integration |
| `base_engine/weather/forecast_client.py` | ~1300 | Open-Meteo API, ensemble fetch, caching |
| `base_engine/weather/market_mapper.py` | ~1050 | Question text → city/date/bucket parsing |
| `base_engine/weather/station_registry.py` | ~1500 | 106 cities with ICAO codes, coords, aliases |
| `base_engine/risk/bankroll_manager.py` | ~250 | Kelly formula, max_bet, daily cap |
| `base_engine/execution/paper_trading.py` | ~950 | VWAP fill simulation, position tracking |
| `config/settings.py` | ~800 | All env var configuration |

---

## 2. MARKET DISCOVERY

### How markets are found

**Primary path**: Gamma API tag-based query
- Endpoint: `GET {gamma_api}/events?tag_slug=temperature&active=true&closed=false`
- Pagination: 100 per page, up to 5 pages (500 markets max)
- Returns: event objects containing `markets[]` array with `outcomePrices`, `clobTokenIds`
- File: `weather_bot.py:2966-3101`

**Fallback 1**: Database + CLOB midpoint enrichment
- Query `traded_markets` table for `category='weather'`
- Enrich with live prices via CLOB `/midpoint` endpoint per token
- File: `weather_bot.py:1076-1083`

**Fallback 2**: Direct Gamma API (rate-limited to every 30 min)
- `client.get_markets(active=True, limit=500, category="weather")`
- File: `weather_bot.py:2901-2964`

### Discovery caching

- **Daytime** (12:00-23:59 UTC): 5-minute TTL
- **Overnight** (00:00-11:59 UTC): 15-minute TTL
- Cache stores: raw market list + grouped market objects
- File: `weather_bot.py:893, 1063-1067`

### Price enrichment

Markets from the database lack live prices (Polymarket WebSocket subscription limited to ~1000 tokens). For each market without a price:
- Fetch `client.get_token_midpoint(yes_token_id)` from CLOB API
- Semaphore of 10 concurrent HTTP calls
- Capped at 200 markets per enrichment cycle
- File: `weather_bot.py:3103-3159`

---

## 3. MARKET PARSING & MATCHING

### Question text → structured data

Each Polymarket weather market has a question like:
> "Will the highest temperature in New York City be between 48-49°F on March 15?"

The `WeatherMarketMapper` parses this via regex into structured data.

**Regex patterns** (applied in order, file: `market_mapper.py:138-298`):

| Pattern | Example |
|---------|---------|
| RANGE | "be between 48-49°F" |
| AT_OR_BELOW | "be 42°F or below" |
| AT_OR_HIGHER | "be 55°F or higher" |
| EXACT | "be 50°F" |

**Extraction** (file: `market_mapper.py:489-498`):
- Group 0: city text (e.g., "New York City")
- Last group: date string (e.g., "March 15")
- Date parsed via month name lookup + day/year extraction

### City → Station matching

The `lookup_station()` function (file: `station_registry.py:1413-1441`) maps city text to a `WeatherStation`:

1. **Exact alias match**: lowercase city text checked against `_ALIAS_MAP` (compiled from all station aliases)
2. **Word-boundary substring**: longest alias first, regex `\b{alias}\b` to prevent false positives (e.g., "San Francisco Bay Area" matching wrong station)

**WeatherStation fields**:
- `station_id`: ICAO code (e.g., "KLGA" for New York LaGuardia)
- `latitude/longitude`: station coordinates for Open-Meteo API
- `timezone`: IANA timezone for lead-time calculations
- `temp_unit`: "F" (US) or "C" (international)
- `resolution_source`: e.g., "Weather Underground / KLGA" — the authority used to resolve the market
- `has_asos_1min`: True for US ASOS stations (enables 1-minute METAR data)

**Registry**: 106 cities. Includes US domestic (64) and international (42).

### Market grouping

Markets are grouped into `WeatherMarketGroup` objects by (city, target_date):
- Each group = one city + one date + 3-10 temperature buckets
- Buckets sorted by `low_bound` ascending
- Typical scan: 20-50 groups from 100-500 raw markets
- File: `market_mapper.py:500-614`

**TemperatureBucket fields**:
- `market_id`: Polymarket condition_id (hex)
- `token_id` / `no_token_id`: CLOB token IDs for YES/NO sides
- `yes_price`: current market price (0.0-1.0)
- `bucket_type`: "range", "at_or_below", "at_or_higher", "exact"
- `low_bound` / `high_bound`: temperature thresholds
- `temp_unit`: "F" or "C"

---

## 4. FORECAST ACQUISITION

### Data sources

| Source | Members | Coverage | Max Lead | Provider |
|--------|---------|----------|----------|----------|
| GEFS (GFS Ensemble) | 31 | Global, 0.5° | 16 days | NOAA via Open-Meteo |
| ECMWF IFS ENS | 51 | Global, 0.25° | 15 days | ECMWF via Open-Meteo |
| ECMWF AIFS ENS | 51 | Global, 0.25° | 6 days | ECMWF via Open-Meteo |
| NBM (National Blend) | 1 (deterministic) | US only | 7 days | NWS API |
| Local hi-res models | 1 (deterministic) | Regional | 2-5 days | Open-Meteo |

**Total ensemble members**: up to 133 (31 GEFS + 51 IFS + 51 AIFS)

File: `forecast_client.py:33-36, 1058-1298`

### Fetch orchestration

Four parallel async tasks per forecast:
1. `get_deterministic_forecast()` → GFS daily high temperature
2. `get_ensemble_forecast()` → GEFS+IFS+AIFS ensemble members
3. `get_nbm_forecast()` → NWS NBM point forecast (US only)
4. `_fetch_local_model_forecast()` → International hi-res model (if configured)

**Caching**:
- Model-run cache: 5-min TTL (dedupes burst calls within a model run)
- Regular cache: 900s TTL per (station, date) with jitter
- DB warm-start on boot: pre-populates cache from `weather_forecasts` table

### Deterministic high selection

Priority chain for the "best guess" temperature:
1. Local hi-res model (e.g., Météo-France for Paris) — lowest MAE
2. NBM for US stations (NWS blends 31+ models, MAE 0.8-1.5°F at day 1-3)
3. GFS seamless (fallback)

File: `forecast_client.py:1131-1161`

### Ensemble member processing

- Open-Meteo keys: `temperature_2m_max_member01` through `temperature_2m_max_member51` per model
- NaN/Inf members filtered at extraction
- Numeric sort to prevent lexicographic ordering issues

**Lead-time GEFS subsampling** (file: `forecast_client.py:1227-1245`):
- Problem: GFS degrades ~40% faster than ECMWF at 72h+
- At 48-72h: keep 24 of 31 GEFS members (~2:1 ECMWF:GEFS ratio)
- At 72-120h: keep 16 of 31 GEFS members (~3:1 ratio)
- At 120h+: keep 8 of 31 GEFS members (~6:1 ratio)

### Rate limiting

Per-model 1-hour cooldown when Open-Meteo returns HTTP 429:
- Tracked in memory + Redis (persists across restarts)
- Skip model for 1 hour, use remaining models
- File: `forecast_client.py:439-474`

---

## 5. PROBABILITY COMPUTATION

### Pipeline: ensemble members → bucket probabilities

File: `probability_engine.py`

**Step 1: fit_distribution()** (lines 54-124)
- Filter NaN/Inf members
- Compute mean and standard deviation from ensemble
- Apply **std_floor = 0.5°** — minimum standard deviation to prevent overconfidence on tight ensembles
- Apply **EMOS calibration** (see Section 6)
- Fit **skew-normal distribution** via MLE (`scipy.stats.skewnorm.fit()`) if ≥10 members
- Fallback: symmetric normal if skew-normal fit fails or <10 members
- Output: `(loc, scale, shape)` — distribution parameters

**Step 2: Climate prior blend** (lines 462-495, applied in weather_bot.py:1892-1903)
- Only at lead_time > 72 hours
- Blend weight ramps linearly: 0% at 72h → 40% at 168h
- Formula: `loc' = (1-w)*loc + w*clim_mean`, `scale' = sqrt((1-w)*scale² + w*clim_std²)`
- Climatology from ERA5 10-year normals stored in `weather_climatology` table

**Step 3: bucket_probabilities()** (lines 126-175)
- For each temperature bucket, integrate CDF across the bucket's bounds:
  - "range 48-49°F": `P(47.5 ≤ T < 49.5)` = `CDF(49.5) - CDF(47.5)`
  - "at or below 42°F": `P(T ≤ 42.5)` = `CDF(42.5)`
  - "at or higher 55°F": `P(T ≥ 54.5)` = `1 - CDF(54.5)`
- ±0.5° boundary offset accounts for temperature measurement resolution
- Clamp each probability to [0.001, 0.999]
- Normalize so all bucket probabilities sum to ~1.0
- Degenerate guard: if total probability < 0.01, return empty dict (prevents fake edges)

**Step 4: METAR override** (weather_bot.py:2126-2200, applied when lead_time < 12h)
- Fetch running daily max temperature from 1-minute ASOS observations (Iowa Environmental Mesonet)
- Override definitively ruled buckets:
  - Running max already exceeds bucket upper bound → set P(YES) = 0.01
  - Running max already crossed "at or higher" threshold → set P(YES) = 0.99
- Re-normalize after overrides
- US-only (ASOS stations with K-prefix ICAO codes)

---

## 6. CALIBRATION SYSTEM

### EMOS (Ensemble Model Output Statistics)

**What it does**: Corrects systematic forecast bias per station.

**Formula**:
- `μ_emos = a + b × X̄` (mean correction — slope `b` corrects systematic over/under-prediction)
- `σ_emos = sigma` (spread correction — corrects ensemble underdispersion)

**Fallback chain**:
1. **Local EMOS**: per-station, per-lead-time-bucket. Requires ≥20 resolved (forecast, actual) pairs.
2. **Global EMOS**: pooled from all stations. Fallback for cold stations.
3. **Simple bias offset**: `a = bias, b = 1, sigma = None`
4. **Identity**: `a = 0, b = 1, sigma = None` (no correction)

**Storage**: `self._emos[station_id][lead_bucket] = (a, b, sigma)` in memory; loaded from `weather_calibration` table every 6 hours.

**Current coverage**: 24 of 106 stations have local EMOS fitted. All others use global fallback.

File: `probability_engine.py:349-378`

### Confidence Calibration (Logistic Regression)

See Section 8 for full details. This is the post-hoc calibration applied after model probabilities are computed, converting raw model confidence into calibrated confidence for Kelly sizing.

---

## 7. EDGE COMPUTATION & FILTERING

### Edge formula

```
edge = model_prob - market_price
```

- If `edge > 0`: model says bucket is underpriced → trade **YES** (buy the outcome)
- If `edge < 0`: model says bucket is overpriced → trade **NO** (bet against the outcome)
- Absolute edge: `|edge|` used for ranking and filtering

File: `probability_engine.py:239-269`

### Pre-trade gates in `_analyze_group` (weather_bot.py:1783-2124)

These gates filter opportunities BEFORE sizing. Applied to each bucket within a group:

| Gate | Condition | Default | Line |
|------|-----------|---------|------|
| Past date | target_date < today | — | 1792 |
| Max lead time | lead_time > 168h (7 days) | 168h | 1796 |
| Station health | station reported failures | — | 1806 |
| No forecast | API returned None | — | 1821 |
| Group/city exposure cap | already at $10K group or $5K city | $10K/$5K | 1874 |
| Minimum edge | abs_edge < min_edge | 0.08 (domestic), 0.12 (intl) | 1987 |
| Recently exited | market exited < 4h ago | 4h | 1995 |
| Penny bet | price ≤ 0.04 or ≥ 0.97 | — | 2012 |
| NO max entry price | NO side and price > 0.75 | 0.75 | 2018 |
| In-memory position check | already has open position | — | 2026 |
| DB position check | positions table shows open | — | 2033 |
| Confidence calibration | calibrate(raw_conf, side, lead_time, price) | see §8 | 2056 |
| Max buckets per group | already have 3 tradeable buckets | 3 | 2086 |

---

## 8. CONFIDENCE CALIBRATION (LOGISTIC REGRESSION)

### What it is

A `sklearn.LogisticRegression` model that maps (raw_confidence, side, lead_time, price) → calibrated probability of winning. Replaces the previous Platt+Isotonic pipeline (S135).

### Why it exists

The raw model confidence is systematically miscalibrated:
- NO at 0.95+ confidence: 87.6% actual win rate (accurate)
- YES at 0.95+ confidence: 18.8% actual win rate (catastrophically wrong)
- Short lead time trades lose regardless of model confidence
- Low entry price YES trades have 4.7% win rate

A single calibration parameter (Platt T) cannot correct all of these simultaneously. Logistic regression with 4 features learns the correction for each dimension from data.

### Features

| Feature | Type | Meaning |
|---------|------|---------|
| `raw_confidence` | float [0, 0.95] | Model's confidence the trade wins |
| `side` | binary (YES=1, NO=0) | Which side is being traded |
| `lead_time_hours` | float [0, 168] | Hours until market resolution |
| `entry_price` | float [0.04, 0.97] | Market price at entry |

### Training data

- Source: `trade_events` table (immutable, trigger-protected)
- Query: ENTRY events joined to RESOLUTION events for outcome (realized_pnl > 0 = win)
- Window: last 30 days (configurable via `WEATHER_CONFIDENCE_CAL_WINDOW_DAYS`)
- Minimum samples: 200 (configurable via `WEATHER_CONFIDENCE_CAL_MIN_SAMPLES`)
- Current data: ~2,087 resolved trades (1,426 NO + 661 YES)
- `lead_time_hours` extracted from `event_data` JSONB column (COALESCE to 48.0 for older events)

### Fitting

```python
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

X = [raw_confidence, side_encoded, lead_time_hours, entry_price]
y = [1.0 if realized_pnl > 0 else 0.0]

scaler = StandardScaler()  # normalize features
X_scaled = scaler.fit_transform(X)
model = LogisticRegression(C=1.0, solver='lbfgs', max_iter=1000)
model.fit(X_scaled, y)
```

- L2 regularization (C=1.0) prevents overfitting
- StandardScaler ensures coefficients are comparable across features
- Brier score validation: if calibrated Brier > raw Brier + 0.005, reject (revert to identity)
- Refits every 6 hours automatically

### Inference

```python
calibrated = model.predict_proba([[raw_conf, side_enc, lead_time, price]])[0, 1]
# Clipped to [0.01, 0.99]
```

### Expected coefficient signs

| Coefficient | Expected | Meaning |
|-------------|----------|---------|
| `coef_confidence` | **+** | Higher model confidence → more likely to win |
| `coef_side_yes` | **−** | YES trades are overconfident → penalize |
| `coef_lead_time` | **+** | Longer lead → less market efficiency → better edge |
| `coef_entry_price` | **+** | Higher price YES → stronger model signal |

### Rollback

`WEATHER_CONFIDENCE_CAL_ENABLED=false` → disables calibration entirely, raw confidence used directly.

File: `weather_bot.py:66-217`

---

## 9. TRADE SIZING (KELLY CRITERION)

### Kelly formula

```
b = (1 - price) / price          # payout odds
f* = (confidence × b - (1 - confidence)) / b    # optimal fraction
size_usd = f* × kelly_fraction × available_capital
```

- `kelly_fraction`: default 0.25 (quarter-Kelly), auto-graduates to 0.35 via P&L gate
- `available_capital`: from BotBankrollManager (tracks per-bot allocation)

File: `bankroll_manager.py:114-233`

### Kelly graduation

P&L gate (weather_bot.py:4543-4610):
- If 7-day WeatherBot P&L ≥ $0: graduate to next Kelly tier (0.25 → 0.35 → 0.50)
- If 7-day P&L < $0: demote to previous tier
- Checked on each calibration reload (every 6h)

### Smoczynski-Tomkins allocation

When multiple buckets in the same group have positive edge, standard independent Kelly undersizes by 20-40% (ignores that buckets are mutually exclusive — the winner partially funds the losers).

S-T allocation (weather_bot.py:2230-2298):
```
For each bucket with positive Kelly edge f_i:
    allocation_i = (f_i / sum(all f_i)) × kelly_fraction × group_budget
```

Proportional to edge magnitude, scaled to group budget, capped at group exposure limit.

### Max bet

Per-bot max: $300 (WeatherBot) from `BotBankrollManager.max_bet_usd`.
System-wide phase cap: `PHASE_MAX_BET_USD=$1000` (paper trading phase).

### Minimum trade

$5 floor (`WEATHER_MIN_TRADE_USD=5.0`). Trades sized below $5 are logged as shadow entries but not executed.

---

## 10. COMBINED BOOST MULTIPLIERS

After Kelly sizing, the raw size is multiplied by `combined_boost`:

```python
combined_boost = 1.0 + (expiry_boost - 1.0) + (regime_boost - 1.0) * 0.5
                     + (severe_boost - 1.0) * 0.5 + (jump_boost - 1.0) * 0.5
                     + (nbm_boost - 1.0) * 0.5
```

Each factor contributes its excess over 1.0 (additive, not multiplicative). The 0.5× scaling on non-expiry boosts prevents extreme stacking.

| Boost | Range | When | Rationale |
|-------|-------|------|-----------|
| **Expiry** | 1.0-2.0× | Based on lead time | Ensemble converges near resolution |
| **Regime** | 1.0-1.2× | ≥3 cities agree on warm/cold shift | Cross-city validation increases conviction |
| **Severe** | 1.0-1.5× | NWS severe weather warning active | Event certainty increases |
| **Jump** | 1.0-1.5× | Ensemble mean shifted ≥3°F between model runs | Market hasn't priced new forecast |
| **NBM** | 1.0-1.3× | NBM disagrees with market by ≥15pp | Independent model confirmation |

### Expiry boost schedule

| Lead Time | Boost | Rationale |
|-----------|-------|-----------|
| <1h | 1.2× | Capped: paper engine applies 3.0× slippage penalty |
| 1-6h | 1.5× | METAR override window, moderate confidence |
| 6-12h | 2.0× | NOAA final-call, maximum certainty |
| 12-24h | 1.5× | Day-of-event, strong convergence |
| 24-48h | 1.2× | Within hold window, early convergence |
| >48h | 1.0× | Standard (no boost) |

### YES boost disable

When `WEATHER_YES_BOOST_ENABLED=false` (current default): all boosts are set to 1.0 for YES trades. Data showed YES at 18.8% WR with high confidence — boosting amplifies losses.

### Station reliability factor

Applied as `combined_boost *= station_factor`:

| Station MSE | Factor | Meaning |
|-------------|--------|---------|
| <4.0 (avg error <2°F) | 1.2× | Well-calibrated, increase size |
| 4.0-9.0 (2-3°F error) | 1.0× | Baseline |
| 9.0-16.0 (3-4°F error) | 0.8× | Poor calibration, reduce size |
| >16.0 (>4°F error) | 0.5× | Very poor, halve size |

File: `weather_bot.py:676-717`

---

## 11. RISK MANAGEMENT GATES

### Execution-time gates (in `_execute_weather_trade`)

These gates are checked AFTER sizing, before placing the order:

| Gate | Condition | Default | Line |
|------|-----------|---------|------|
| Same-side dedup | Already holding same market+side | — | 2366 |
| Exit cooldown | Exited this market < 4h ago | 4h | 2379 |
| Fill failure cooldown | 3+ consecutive fill failures in 1h | 3/1h | 2386 |
| Fill probability estimate | P(fill) < 0.3 | 0.3 | 2418 |
| Daily loss limit | Daily P&L ≤ -$10,000 | $10K | 2425 |
| Group exposure cap | Group (city+date) at $10,000 | $10K | 2440 |
| City exposure cap | City total at $5,000 | $5K | 2443 |
| Severe weather halt | Hurricane/tornado/blizzard active | — | 2484 |
| Slippage-adjusted edge | Edge after book walk slippage < min_edge | — | 2551 |
| Negative EV gate | calibrated confidence < price | — | 2638 |
| Sub-minimum trade | Sized trade < $5 after all adjustments | $5 | 2668 |

### Negative EV gate (shadow entries)

If `effective_confidence < price`, the trade has negative expected value (would lose money on average). These are logged as `SHADOW_ENTRY` events in `trade_events` for data collection but NOT executed.

Shadow entry event_data includes: city, raw_size_usd, raw_confidence, combined_boost, lead_time_hours, reason ("negative_ev" or "zero_kelly" or "sub_min_trade" or "exposure_cap").

File: `weather_bot.py:2638-2716`

### Drawdown compression

Applied by `BotBankrollManager` to the Kelly fraction:
```
if drawdown_pct > 2%:
    compress = max(0.30, 1.0 - drawdown_pct × 4.0)
    kelly_fraction *= compress
```

| Drawdown | Compression | Effective Kelly |
|----------|-------------|-----------------|
| 2% | 0.92× | 0.23 |
| 5% | 0.80× | 0.20 |
| 10% | 0.60× | 0.15 |
| 18%+ | 0.30× (floor) | 0.075 |

### Daily P&L restore

On startup, today's realized P&L is restored from `trade_events`:
- Query: `SUM(realized_pnl) WHERE event_type = 'EXIT' AND bot_name = 'WeatherBot' AND event_time >= today`
- S134: Uses EXIT-only (RESOLUTION events are corrupted with phantom P&L)
- If daily P&L exceeds -$10K, all trading halted until next UTC day

---

## 12. ORDER EXECUTION

### Place order flow

```
_execute_weather_trade() → self.place_order()
    → base_bot.place_order()
        → order_gateway.submit_order() [PaperTradingEngine]
            → execute_paper_trade()
```

File: `weather_bot.py:2730-2806, base_bot.py, paper_trading.py`

### place_order parameters

```python
result = await self.place_order(
    market_id=opp["market_id"],
    token_id=opp["token_id"],
    side=opp["side"],        # "YES" or "NO" — NEVER "BUY"/"SELL"
    size=size_shares,        # shares (USD / price)
    price=opp["price"],      # entry price
    confidence=opp["confidence"],  # calibrated confidence
    event_data={
        "city": group.city,
        "date": group.target_date.isoformat(),
        "market_type": "temperature",
        "lead_time_hours": lead_time,
        "boundary_risk": boundary_risk,
        "scan_start_mono": scan_start_mono,
        "alpha_decay_half_life_s": 1800,
        "volume_24h": clob_volume,
    },
)
```

### Paper trading engine (VWAP fill simulation)

The paper trading engine simulates realistic fills:
1. **VWAP book walk**: simulates walking the order book (taker fills at weighted average)
2. **Fill probability**: based on market liquidity and order size
3. **Slippage model**: size-dependent price impact
4. **Post-VWAP cost cap**: caps `size × price` to `max_bet × 1.5` after book walk (S131)
5. **Fill confirmation**: trade recorded with actual fill price (may differ from submitted price)

On successful fill:
1. **paper_trades** table: UPSERT with market_id, side, size, price, timestamps
2. **trade_events** table: INSERT ENTRY event (immutable, trigger-protected)
3. **positions** table: INSERT/UPDATE open position
4. **In-memory state**: `_open_position_markets` set updated

File: `paper_trading.py:850-949`

---

## 13. POSITION MANAGEMENT & EXITS

### Exit triggers

1. **Alpha decay** (primary exit mechanism):
   - Exponential decay of edge over time
   - Half-life: 1800 seconds (30 minutes) — `WEATHER_ALPHA_DECAY_HALF_LIFE_S`
   - Edge decays: `current_edge = entry_edge × 0.5^(elapsed / half_life)`
   - Exit when decayed edge < min_edge threshold
   - `scan_start_mono` in event_data timestamps the entry for decay calculation

2. **Resolution** (market settles):
   - Polymarket/UMA oracle resolves YES or NO
   - Resolution backfill runs every 30 min (mini) + daily (full)
   - RESOLUTION event emitted to trade_events with realized_pnl

3. **Position reconciliation**:
   - Every scan cycle, positions table compared to paper_trades
   - Orphaned positions (no matching paper_trade) are closed

### Exit mechanics

EXIT events are written to `trade_events` with:
- `event_type = 'EXIT'`
- `realized_pnl = (exit_price - entry_price) × size` (for YES) or `(entry_price - exit_price) × size` (for NO)
- Exit price from current market midpoint

### Cooldowns

After exit:
- 4-hour re-entry cooldown per market (`_recently_exited` dict, keyed by market_id)
- Redis-backed for persistence across restarts (WeatherBot uses Redis TTL keys)

---

## 14. RESOLUTION & P&L

### Resolution backfill

File: `resolution_backfill.py`

Runs in two modes:
- **Mini backfill** (every 30 min): checks recently resolved markets
- **Full backfill** (daily): checks all open positions against Polymarket resolution status

Resolution flow:
1. Query Polymarket API for market resolution status
2. If resolved (YES/NO/PURGED): emit RESOLUTION event to trade_events
3. RESOLUTION event contains `realized_pnl` computed from ENTRY price and resolution outcome

### P&L calculation

**Win (side matches resolution)**:
```
P&L = (1.0 - entry_price) × remaining_size - remaining_size × 0.015 (fee)
```

**Loss (side doesn't match resolution)**:
```
P&L = -entry_price × remaining_size
```

**P&L authority**: `trade_events` table (immutable). Never use `paper_trades` for P&L.

**Fee**: 1.5% (150 bps) on winning trades only.

### Ground truth P&L query

```sql
WITH entries AS (
    SELECT market_id, side,
           SUM(size) as total_size,
           SUM(price * size) / NULLIF(SUM(size), 0) as avg_price
    FROM trade_events
    WHERE bot_name = 'WeatherBot' AND event_type = 'ENTRY'
    GROUP BY market_id, side
),
pos AS (
    SELECT e.market_id, e.side, e.avg_price,
           e.total_size - COALESCE(x.exit_size, 0) as remaining,
           UPPER(tm.resolution) as resolution
    FROM entries e
    LEFT JOIN exits x ON x.market_id = e.market_id AND x.side = e.side
    LEFT JOIN traded_markets tm ON tm.market_id = e.market_id
    WHERE tm.resolution IS NOT NULL
      AND e.total_size - COALESCE(x.exit_size, 0) > 0
)
SELECT
    SUM(CASE WHEN side = resolution
        THEN (1.0 - avg_price) * remaining - remaining * 0.015
        ELSE -avg_price * remaining
    END) as realized_pnl
FROM pos;
```

---

## 15. DATA STORAGE & PERSISTENCE

### Database tables used by WeatherBot

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `trade_events` | Immutable event log (P&L authority) | sequence_num, event_type, market_id, side, size, price, confidence, event_data (JSONB), realized_pnl |
| `paper_trades` | Paper trading state (mutable) | market_id, side, size, price, status |
| `positions` | Open position tracking | market_id, bot_id, status, unrealized_pnl |
| `traded_markets` | Market metadata + resolution | market_id, resolution (YES/NO/PURGED) |
| `weather_calibration` | EMOS parameters + bias data | station_id, lead_time_hours, bias, forecast_temp, actual_temp |
| `weather_climatology` | ERA5 10-year climate normals | station_id, day_of_year, clim_mean, clim_std |
| `weather_forecasts` | Forecast archive (cache warm-start) | station_id, target_date, forecast data |
| `prediction_log` | Prediction audit trail | market_id, model_prob, confidence, timestamp |

### Partitioning

`trade_events` is range-partitioned by `event_time` (monthly partitions: `trade_events_2026_01` through `trade_events_2026_12`).

### Immutability

`trg_trade_events_immutable` trigger prevents DELETE/UPDATE on trade_events. Must be disabled for data cleanup operations.

### Redis state

| Key Pattern | Purpose | TTL |
|-------------|---------|-----|
| `weatherbot:recently_exited:{market_id}` | Exit cooldown | 14400s (4h) |
| `weatherbot:429:{model}` | API rate limit cooldown | 3600s (1h) |
| `weatherbot:backoff:{key}` | Exponential backoff state | varies |

---

## 16. CONFIGURATION REFERENCE

### Core trading parameters

| Variable | Default | Purpose |
|----------|---------|---------|
| `WEATHER_MIN_EDGE` | 0.08 | Minimum absolute edge to trade (8pp) |
| `WEATHER_INTL_MIN_EDGE` | 0.12 | Minimum edge for international cities (12pp) |
| `WEATHER_NO_MAX_ENTRY_PRICE` | 0.75 | Max NO entry price (skip above) |
| `WEATHER_MIN_TRADE_USD` | 5.0 | Minimum trade size ($5 floor) |
| `WEATHER_MAX_BUCKETS_PER_GROUP` | 3 | Max simultaneous bucket trades per city+date |
| `WEATHER_HOLD_HOURS_BEFORE_RESOLUTION` | 48 | Hold window for expiry boost |
| `WEATHER_MAX_LEAD_TIME_HOURS` | 168 | Max lead time (7 days) |
| `WEATHER_KELLY_FRACTION` | 0.25 | Base Kelly fraction (auto-graduates to 0.35) |

### Calibration parameters

| Variable | Default | Purpose |
|----------|---------|---------|
| `WEATHER_CONFIDENCE_CAL_ENABLED` | true | Enable/disable confidence calibration |
| `WEATHER_CONFIDENCE_CAL_WINDOW_DAYS` | 30 | Training data window |
| `WEATHER_CONFIDENCE_CAL_MIN_SAMPLES` | 200 | Minimum resolved trades to fit |
| `WEATHER_YES_MIN_CONFIDENCE` | 0.35 | YES confidence floor (0.0 = disabled) |
| `WEATHER_YES_BOOST_ENABLED` | false | Whether YES gets combined boost |

### Risk parameters

| Variable | Default | Purpose |
|----------|---------|---------|
| `WEATHER_ALPHA_DECAY_HALF_LIFE_S` | 1800 | Edge decay half-life (30 min) |
| `WEATHER_EMOS_WINDOW_DAYS` | 90 | EMOS calibration data window |
| `WEATHER_CALIBRATION_RELOAD_SECS` | 21600 | Calibration reload interval (6h) |

### Boost parameters

| Variable | Default | Purpose |
|----------|---------|---------|
| `WEATHER_JUMP_THRESHOLD_F` | 3.0 | Min forecast delta for jump boost (°F) |
| `WEATHER_JUMP_MAX_BOOST` | 1.5 | Max jump boost multiplier |
| `WEATHER_NBM_BOOST` | 1.3 | NBM high-conviction boost |
| `WEATHER_NBM_DISAGREE_THRESHOLD` | 0.15 | NBM disagreement threshold (15pp) |

---

## COMPLETE TRADE FLOW DIAGRAM

```
[1] MARKET DISCOVERY
    Gamma API tag_slug=temperature → 100-500 raw markets
    ↓
[2] PARSING & MATCHING
    Regex → city + date + bucket type + bounds
    Station lookup → WeatherStation (ICAO code, coords)
    Group by (city, date) → 20-50 WeatherMarketGroups
    ↓
[3] FORECAST ACQUISITION (per group)
    Open-Meteo API → 133 ensemble members (GEFS+IFS+AIFS)
    NWS API → NBM point forecast (US only)
    Local model → hi-res deterministic (intl)
    ↓
[4] PROBABILITY COMPUTATION
    Ensemble → fit skew-normal distribution
    EMOS calibration → correct mean/spread bias
    Climate prior → blend at >72h lead
    CDF integration → P(temperature in bucket) per bucket
    METAR override → definitively rule buckets at <12h
    ↓
[5] EDGE & FILTERING
    edge = model_prob - market_price
    Filter: min_edge (8-12%), penny bets, NO price cap,
            re-entry guard, position check, max buckets
    ↓
[6] CONFIDENCE CALIBRATION
    LogisticRegression(raw_conf, side, lead_time, price)
    → calibrated_confidence
    ↓
[7] TRADE SIZING
    Kelly: f* = (conf × odds - loss_prob) / odds
    × kelly_fraction (0.25-0.50)
    × combined_boost (expiry, regime, severe, jump, NBM)
    × station_reliability (0.5-1.2)
    ↓
[8] RISK GATES
    Daily loss limit ($10K), group/city exposure caps,
    fill probability, slippage check, negative EV gate
    ↓
[9] EXECUTION
    place_order(side="YES"/"NO", size=shares, price=entry)
    → PaperTradingEngine.execute_paper_trade()
    → VWAP fill simulation
    → trade_events ENTRY + paper_trades + positions
    ↓
[10] POSITION MANAGEMENT
     Alpha decay exit (30-min half-life)
     Resolution backfill (30-min mini + daily full)
     → trade_events EXIT/RESOLUTION + realized_pnl
```
