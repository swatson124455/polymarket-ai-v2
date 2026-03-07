# AGENT HANDOFF — WEATHERBOT SESSION 56
**Date:** 2026-03-07
**Bot Focus:** WeatherBot exclusively
**Session type:** Research-to-implementation — turned 2 major research docs into 8 committed, tested, deployed improvements
**Tests at close:** 1134 passed, 6 skipped (all suites)
**VPS status:** `active (running)` — no errors, `db_weather_regex_match=39`

---

## WHAT YOU ARE BUILDING

**WeatherBot** is a Polymarket temperature-bucket trading bot. It:
1. Finds markets phrased as "Will the highest temperature in NYC be between 72–75°F on March 10?"
2. Fetches multi-model ensemble forecasts (133 members across GEFS + ECMWF IFS + ECMWF AIFS)
3. Fits a skew-normal distribution to the ensemble and integrates probability across each bucket's bounds
4. Compares model probability vs. Polymarket YES price → edge = model_prob - market_price
5. If edge ≥ 15% (configurable), sizes via fractional Kelly and places a paper trade

**The end goal:** Beat a weather market that is systematically exploitable because:
- Retail bettors overweight extreme buckets (favourite-longshot bias)
- Market prices lag NWP model updates by 30–60 minutes
- Resolution-day outcomes can be front-run via METAR aviation observations 1–5 min post-observation

---

## VPS / INFRASTRUCTURE

```
VPS:     Ubuntu-3 at 34.251.224.21 (16GB/4vCPU, eu-west-1)
SSH key: C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem
User:    ubuntu
Path:    /opt/polymarket-ai-v2/
Service: polymarket-ai (systemd)
DB pass: polymarket_s46
Redis:   78psiRhepTgrmWSoy3cgNEIr
```

**Deploy pattern:**
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"
scp -i "$KEY" -o StrictHostKeyChecking=no "local/path/file.py" "$VPS:/tmp/"
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'sudo cp /tmp/file.py /opt/polymarket-ai-v2/path/file.py && sudo systemctl restart polymarket-ai'
```

**Log monitoring:**
```bash
journalctl -u polymarket-ai -f | grep -i weather
journalctl -u polymarket-ai -f | grep -E "weatherbot_|weather_calibration|weather_emos|metar_|nws_"
```

---

## FILE MAP — WEATHERBOT ARCHITECTURE

```
base_engine/weather/
  forecast_client.py      — Open-Meteo ensemble + deterministic + NWS NBM API client
  metar_client.py         — Aviation Weather Center METAR API (NEW Session 56)
  probability_engine.py   — Skew-normal fit, EMOS calibration, bucket integration, Kelly
  market_mapper.py        — Regex → TemperatureBucket + WeatherMarketGroup
  station_registry.py     — ICAO→city registry (KLGA=NYC, KATL=Atlanta, etc.)
bots/
  weather_bot.py          — Main bot: scan, _analyze_group, _execute_weather_trade
tests/
  unit/test_weather_bot.py — 106 unit tests (weather-specific; full suite = 1134)
```

---

## DATABASE SCHEMA (PostgreSQL on VPS)

### `weather_calibration` — EMOS training data
```sql
id                 SERIAL PRIMARY KEY
station_id         VARCHAR(10)       -- ICAO code (KLGA, KATL, etc.)
target_date        DATE
forecast_time      TIMESTAMP
lead_time_hours    FLOAT
forecast_temp      FLOAT             -- model forecast temp (°F or °C)
actual_temp        FLOAT             -- NULL until market resolves
bias               FLOAT             -- NULL until actual_temp populated (actual - forecast)
model_name         VARCHAR(50)
created_at         TIMESTAMP DEFAULT NOW()
UNIQUE(station_id, target_date, lead_time_hours)
```
**How it populates:**
- `forecast_temp` written by `_save_forecast_to_db()` on every scan
- `actual_temp` + `bias` written by `_maybe_update_calibration_actuals()` when market resolves
- `_maybe_reload_calibration()` runs every 6h, reads all rows with `bias IS NOT NULL`
- Builds simple `bias` dict AND EMOS `(a, b, sigma)` dict (≥20 pairs required for EMOS)

### `weather_forecasts` — forecast snapshots
```sql
station_id, target_date, fetch_time (15-min bucket), lead_time_hours,
ensemble_mean, model_spread, ensemble_count, model_name, created_at
UNIQUE(station_id, target_date, lead_time_hours)
```

### `paper_trades` — live trade log (shared by all 15 bots)
```sql
bot_name  VARCHAR   -- "WeatherBot" (NOT bot_id — this column is bot_name)
side      VARCHAR   -- "YES" or "NO" for entries, "SELL" for exits
```

### `positions` — open positions (shared)
```sql
bot_id    VARCHAR   -- "WeatherBot" (NOT bot_name — this column is bot_id)
```

**DB access pattern (MANDATORY):**
```python
async with db.get_session() as session:
    from sqlalchemy import text
    rows = await session.execute(text("SELECT ... FROM table WHERE ..."), params)
    # Direct db.execute() does NOT exist — AttributeError
```

---

## COMPLETE IMPLEMENTATION — ALL 8 IMPROVEMENTS SHIPPED THIS SESSION

### Item 1 ✅ ECMWF AIFS ENS — 82 → 133 members (`72a2944`)
**File:** `base_engine/weather/forecast_client.py`
**What:** Added ECMWF AIFS (51 members) as 3rd parallel API call alongside GEFS (31) + IFS (51)
**Why:** More members = lower CDF sampling error, especially in distribution tails. AIFS is CC-BY-4.0 free since Oct 2025 via Open-Meteo `ecmwf_aifs025` model.
**How:** `get_ensemble_forecast()` fires 3 `asyncio.gather()` tasks. Merge loop uses `running_offset` to assign unique member keys. `>82 members → models_used += ["ecmwf_aifs025"]`.

### Item 2 ✅ Remove fixed 1.5%/hr spread inflation (`fefc95b`)
**File:** `base_engine/weather/probability_engine.py`
**What:** Removed `lead_time_factor = 1.0 + 0.015 * min(lead_time_hours, 168.0)` from both scipy and fallback paths.
**Why:** With 133 real members, the ensemble members themselves naturally diverge at longer lead times (day 5 spread > day 1 spread). The fixed factor was a workaround for small-ensemble underdispersion and was causing 2×–3× overcorrection at day 3–5 → false edges on far-future markets.

### Item 3 ✅ Tail bracket discount — 15% (`2050a27`)
**File:** `base_engine/weather/probability_engine.py`
**What:** `if b.bucket_type in ("at_or_below", "at_or_higher"): p *= 0.85` before normalization.
**Why:** Polymarket retail bettors systematically overweight extreme outcomes (favourite-longshot bias). Tail buckets trade at too-high prices relative to model probability. 15% discount reduces spurious edge signals on extremes. Applied in both scipy and fallback paths.

### Item 4 ✅ Adaptive scan interval (`b533f4f`)
**File:** `bots/weather_bot.py`
**What:** `_get_scan_interval_seconds()` override returns:
- 60s during ECMWF ENS windows (07:00–08:00, 18:00–19:00 UTC)
- 90s during GFS windows (05:15–06:00, 17:15–18:00 UTC)
- 120s during HRRR window (:40–:59 each hour)
- Default: 300s (SCAN_INTERVAL_WEATHER env var)
**Why:** Market prices lag model updates by 30–60 min. Scanning aggressively during the first hour after each model lands captures mispricing before market makers update quotes.

### Item 5 ✅ EMOS (a,b,σ) calibration (`725f556`)
**Files:** `base_engine/weather/probability_engine.py`, `bots/weather_bot.py`

**Theory:**
EMOS (Ensemble Model Output Statistics) corrects two systematic errors:
- **Mean bias slope:** `μ_emos = a + b·X̄` where b≠1 means the ensemble systematically over/under-forecasts
- **Spread underdispersion:** `σ_emos` replaces raw ensemble spread with historically-calibrated spread

**Implementation:**

*probability_engine.py:*
```python
self._emos: Dict[str, Dict[int, Tuple[float, float, Optional[float]]]] = {}
# {station_id → {lead_bucket → (a, b, sigma)}}

def _get_emos_params(self, station_id, lead_time_hours) -> Tuple[float, float, Optional[float]]:
    # Check EMOS dict first; fallback to simple bias offset (a=bias, b=1, sigma=None)

def load_emos_calibration(self, emos_data):
    self._emos = emos_data

def fit_distribution(self, ensemble_members, lead_time_hours, station_id):
    emos_a, emos_b, emos_sigma = self._get_emos_params(station_id, lead_time_hours)
    corrected_mean = emos_a + emos_b * mean
    effective_std = max(emos_sigma if emos_sigma is not None else std, 0.5)
    loc_shift = corrected_mean - mean
    # Apply loc_shift to skewnorm fitted loc
    # Apply emos_sigma as scale (or fallback to fitted scale)
```

*weather_bot.py:*
```python
@staticmethod
def _fit_emos(pairs: List[Tuple[float, float]]) -> Tuple[float, float, float]:
    # OLS: actual = a + b * forecast
    # Returns (a, b, sigma) where sigma = std(residuals), floored at 0.5°
    # Fallback: (mean_bias, 1.0, 2.0) if all forecasts identical (degenerate)

# In _maybe_reload_calibration():
# SQL now: SELECT station_id, lead_time_hours, bias, forecast_temp, actual_temp
# Aggregates (forecast_temp, actual_temp) pairs per station+bucket
# Calls _fit_emos() when ≥20 pairs; loads into prob_engine.load_emos_calibration()
```

**Cold start:** Identity params `(0, 1, None)` = no correction. Falls back to simple bias offset during cold start (< 20 pairs). EMOS activates bucket-by-bucket as data accumulates.

### Item 6 ✅ METAR client + resolution-day front-running (`17284e2`)
**Files:** `base_engine/weather/metar_client.py` (NEW), `bots/weather_bot.py`

**Theory:** METAR aviation observations (T-groups) arrive 1–5 min after each hourly observation at airport ASOS stations — well before Weather Underground compiles its daily summary. On resolution day, we can track the running daily maximum with 0.1°C precision and make near-certainty trades.

**API:** `https://aviationweather.gov/api/data/metar?ids={KLGA}&format=json&hours=24`
Free, no API key, uses same ICAO codes as station_registry.station_id.

**T-group parsing:**
```
T02890267 → temp = +28.9°C  (sign=0 positive, 289 tenths = 28.9°C)
T12890267 → temp = -28.9°C  (sign=1 negative)
```

**Resolution override logic (in `_apply_metar_resolution_day_override()`):**
```
Called when lead_time_hours < 6.0 only

at_or_below:
  running_max > high_bound + 0.5 → model_prob 0.001 (eliminated — can't resolve YES)
  running_max < high_bound - 1.5 → model_prob 0.97  (well below ceiling → YES)

at_or_higher:
  running_max >= low_bound - 0.5 → model_prob 0.97  (threshold crossed → YES)
  running_max < low_bound - 2.0  → model_prob 0.001 (far below → NO)

range:
  running_max > high_bound + 0.5 → model_prob 0.001 (exceeded range → can't be YES)

Probabilities renormalized after overrides.
```

**Cache:** 5-minute cache per `{station_id}:{date_iso}` key to avoid hammering the API.

### Item 7 ✅ NBM for US stations via NWS API (`8c6c124`)
**File:** `base_engine/weather/forecast_client.py`

**Theory:** NWS 7-day forecast is generated directly from NBM (National Blend of Models) — a MAE-weighted blend of 31+ model systems with bias correction. NBM MAE at day-1 is 0.8–1.2°F vs. GFS ~2°F. Using NBM as `deterministic_high` (instead of GFS) gives a better center for the distribution.

**Two-step NWS call:**
1. `GET https://api.weather.gov/points/{lat},{lon}` → returns grid forecast URL
2. `GET {forecast_url}` → returns 7-day periods; find `isDaytime=True` period matching `target_date`

**Caching:** Forecast URL cached 24h per station (grid coordinates never change). Full forecast NOT cached (TTL covered by existing `_cache`).

**Integration:**
```python
# In get_combined_forecast():
nbm_task = (
    self.get_nbm_forecast(lat, lon, station_id, target_date)
    if station.temp_unit.upper() == "F"  # US stations only
    else asyncio.sleep(0, result=None)   # no-op for international
)
det_data, ens_data, nbm_high = await asyncio.gather(det_task, ens_task, nbm_task, ...)

# NBM overrides GFS:
if nbm_high is not None:
    deterministic_high = nbm_high    # Replaces GFS value
    models_used = [..., "nbm"]       # "gfs_seamless" removed
```

### Item 8 ✅ WU boundary uncertainty flag (`de7a6bf`)
**File:** `bots/weather_bot.py`

**Theory:** The Dec 2025 NYC incident showed WU hourly max = 29°F while NWS official daily high = 30°F. When the ensemble mean (loc) is within 0.5°F/°C of a bracket boundary, this 0.5° WU/NWS discrepancy can flip the resolution outcome. We halve the position confidence to protect against this.

**Implementation:**
```python
@staticmethod
def _near_boundary(loc: float, bucket, threshold: float = 0.5) -> bool:
    # at_or_below:  abs(loc - high_bound) <= threshold
    # at_or_higher: abs(loc - low_bound)  <= threshold
    # range/exact:  abs(loc - low_bound) <= threshold OR abs(loc - high_bound) <= threshold

# In _analyze_group():
boundary_risk = WeatherBot._near_boundary(loc, bucket)
effective_confidence = base_confidence * 0.5 if boundary_risk else base_confidence
opp["resolution_boundary_risk"] = boundary_risk
```

---

## CALIBRATION SYSTEM — FULL FLOW

```
SCAN CYCLE (every 60-300s depending on time of day)
  │
  ├─ _maybe_reload_calibration()     [every 6h]
  │    SQL: SELECT station_id, lead_time_hours, bias, forecast_temp, actual_temp
  │         FROM weather_calibration WHERE bias IS NOT NULL
  │    → Simple bias:  station_id → {lead_bucket → mean(bias)}
  │    → EMOS (≥20 pairs): station_id → {lead_bucket → (a, b, sigma)}
  │    → prob_engine.load_calibration(cal_avg)
  │    → prob_engine.load_emos_calibration(emos_params)   [if any buckets have ≥20]
  │
  ├─ _maybe_update_calibration_actuals()  [every scan]
  │    Finds rows with actual_temp=NULL and target_date in past
  │    Calls get_historical_temperature() from Open-Meteo archive
  │    Updates actual_temp + bias in weather_calibration
  │
  └─ _analyze_group()
       Calls prob_engine.fit_distribution():
         1. Get EMOS params: _get_emos_params(station_id, lead_time_hours)
            → if EMOS available: (a, b, sigma)
            → else: (bias_offset, 1.0, None) from simple calibration
         2. corrected_mean = a + b * ensemble_mean
         3. effective_std = max(sigma or raw_std, 0.5)
         4. loc_shift = corrected_mean - ensemble_mean
         5. skewnorm.fit(members) → apply loc_shift + sigma override
```

---

## KEY TRAPS / LESSONS LEARNED

### 1. Polymarket category tagging unreliable for weather
Weather markets appear in politics, crypto, weather, and other categories. Do NOT filter by category. Use keyword regex on market question text.

### 2. Old 2020 markets pollute weather_calibration
All 33+ regex-matched markets had `end_date_iso=2020-11-04` at one point. Markets with `target_date < today` are skipped in `_analyze_group()`. This is correct behavior — the bot IS scanning, just not finding edges when markets expire.

### 3. METAR T-group requires explicit regex `\b` word boundaries
`re.search(r"\bT([01])(\d{3})([01])(\d{3})\b", remarks)` — without `\b`, partial matches on other station codes occur.

### 4. NWS API requires `Accept: application/geo+json` header
Without this, NWS returns a 406 or incorrect content. Always set it.

### 5. asyncio.sleep(0, result=None) as a no-op coroutine
Used to skip NBM fetch for non-US stations while keeping the `asyncio.gather()` structure. Cleaner than `None` task.

### 6. TemperatureBucket requires temp_unit parameter
`TemperatureBucket(market_id=..., bucket_type=..., ..., temp_unit="F")` — missing this field gives `TypeError` in tests. All bucket constructors need it.

### 7. EMOS cold start: identity params
When no EMOS data: `_get_emos_params()` returns `(bias_offset, 1.0, None)` — additive bias only, no slope correction, no sigma. Equivalent to the old simple-bias system. EMOS activates bucket-by-bucket.

### 8. METAR override is only triggered for lead_time < 6.0
DO NOT trigger for normal 24h+ forecasts. METAR running daily max is only meaningful on the actual resolution day.

### 9. paper_trades.bot_name vs positions.bot_id
`paper_trades` uses `bot_name` column = `"WeatherBot"`.
`positions` uses `bot_id` column = `"WeatherBot"`.
Do NOT confuse these — queries will silently return 0 rows.

---

## WHAT IS NOT YET DEPLOYED / STILL PENDING

### paper_trading.py side=BUY bug fix (from Session 55, EsportsBot session)
**Status:** Fixed locally, committed (`memory: Session 55`), **NOT YET deployed to VPS**
**Root cause:** `paper_trading.py` line ~385 stored `side=side` where side was "BUY"/"SELL" (order_gateway format). All entry trades stored as "BUY" regardless of YES/NO direction.
**Fix:** `_db_side = original_side` (YES/NO) for entries, SELL for exits.
**Blast radius:** Shared by ALL 15 bots.
**To deploy:**
```bash
scp -i "$KEY" -o StrictHostKeyChecking=no "bots/paper_trading.py" "$VPS:/tmp/"
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'sudo cp /tmp/paper_trading.py /opt/polymarket-ai-v2/bots/paper_trading.py && sudo systemctl restart polymarket-ai'
```

---

## REMAINING WEATHERBOT IMPROVEMENTS (TIER 2+)

These are designed and researched but not yet implemented. Priority order:

### Tier 2A — Regime-conditioned EMOS
**What:** Separate EMOS params per atmospheric regime (El Niño/La Niña, NAO phase, AO phase)
**Why:** Forecast error is regime-dependent. NOAA's CPC issues monthly El Niño/AO outlooks via public API. Conditioning EMOS on regime can reduce CRPS by additional 10–15%.
**How:**
- `NOAA_CPC_API = "https://www.cpc.ncep.noaa.gov/products/..."` (free, public)
- Add `regime_tag: str` to calibration rows in `weather_calibration` DB (ALTER TABLE needed)
- `_fit_emos_by_regime()`: separate OLS fits per regime
- In `_get_emos_params()`: lookup regime → use regime-specific (a,b,sigma)
- **DB change needed:** `ALTER TABLE weather_calibration ADD COLUMN regime VARCHAR(20)`

### Tier 2B — Isotonic regression for tail calibration
**What:** Replace the fixed 15% tail discount with isotonic regression
**Why:** The 15% discount is a universal constant. Real overpricing varies: tail buckets at day 5 may be overpriced by 25% while day 1 tails may be only 8% overpriced.
**How:**
- After ≥50 resolved tail events: fit isotonic regression on `(model_prob → actual_freq)`
- Store per bucket_type + lead_bucket
- Apply calibrated discount instead of fixed 15%

### Tier 3A — Hurricane/severe weather conditional sizing
**What:** When NOAA issues a hurricane watch/warning, size up aggressively on coastal markets
**Source:** `https://api.weather.gov/alerts/active?status=actual&message_type=alert&area={state}`
**How:** Fetch NWS alerts in parallel with forecast. If `event: "Hurricane Watch"` for markets within 200km → `kelly_mult *= 2.0`.

### Tier 3B — GISTEMP/CPC climate normals for baseline
**What:** Use NOAA CPC climate normals to set prior probability (before ensemble)
**Why:** Ensemble can be wrong in the same direction for multiple days (systematic error). Climate normal gives an independent Bayesian prior.
**Source:** GHCND API via `https://www.ncei.noaa.gov/cdo-web/api/v2/data` (free, requires API token from NOAA)

### Tier 3C — LLM AFD parsing
**What:** Parse NWS Area Forecast Discussion (AFD) text for uncertainty language
**Source:** `https://api.weather.gov/products/types/AFD/locations/{office}`
**Why:** Forecasters explicitly write "high confidence" or "low confidence" in their narratives. LLM can extract confidence → modulate kelly_mult.

---

## CURRENT BOT STATE AT HANDOFF

```
WeatherBot scan:
  db_weather_regex_match=39 markets (up from 33 last session)
  Groups: ~15-16 per scan (most with end_date in past)
  Groups with edge: 0 (no current markets in tradeable future date range)

Status: RUNNING correctly. Bot is scanning, finding markets,
        correctly skipping past-dated markets. No errors.
        Will trade immediately when new temperature markets with
        future resolution dates appear on Polymarket.

Calibration: 0 rows (no resolved pairs yet — normal for new setup)
EMOS: Not yet active (< 20 pairs required). Will activate bucket-by-bucket.

Paper trade P&L: $0 (clean slate — fake trades deleted Session 55)
```

---

## FORECAST MODEL DATA FLOW

```
get_combined_forecast(station, target_date):
  ┌──────────────────┐   ┌─────────────────────┐   ┌──────────────────────┐
  │  GFS Deterministic│   │ GEFS+IFS+AIFS Ensemble│   │  NBM via NWS API     │
  │  Open-Meteo       │   │  Open-Meteo           │   │  (US stations only)   │
  │  gfs_seamless     │   │  133 members total    │   │  /points → /forecast │
  │  temp_2m_max/day  │   │  per-member daily max │   │  isDaytime period     │
  └─────────┬─────────┘   └──────────┬────────────┘   └──────────┬───────────┘
            │                         │                            │
            └─────────────────────────┴────────────────────────────┘
                                      │
                              asyncio.gather() — all 3 parallel
                                      │
                              deterministic_high = NBM ?? GFS
                              ensemble_members = [133 floats]
                              models_used = ["gfs025_ensemble","ecmwf_ifs025","ecmwf_aifs025","nbm"]
```

```
_analyze_group(group):
  ensemble_members → fit_distribution() → (loc, scale, shape)
    ↳ _get_emos_params() → (a, b, sigma) or fallback (bias, 1, None)
    ↳ corrected_mean = a + b * raw_mean
    ↳ skewnorm.fit(members) → shift loc by (corrected_mean - raw_mean)
    ↳ scale = emos_sigma ?? fitted_scale, floored at 0.5°

  (loc, scale, shape) → bucket_probabilities() → {market_id: prob}
    ↳ CDF integration per bucket type
    ↳ 15% tail discount for at_or_below / at_or_higher
    ↳ Normalize to sum=1.0

  IF lead_time < 6.0: → _apply_metar_resolution_day_override()
    ↳ get_running_daily_max(station_id, target_date, temp_unit)
    ↳ Override eliminated/confirmed buckets
    ↳ Renormalize

  compute_edges() → filter by min_edge=15%

  FOR each tradeable edge:
    _near_boundary(loc, bucket) → boundary_risk → confidence *= 0.5 if True
    Append to tradeable list
```

---

## PROBABILITY ENGINE — FULL FUNCTION REFERENCE

```python
class WeatherProbabilityEngine:
    _calibration: Dict[str, Dict[int, float]]      # station_id → {lead_bucket → bias}
    _emos: Dict[str, Dict[int, Tuple[float, float, Optional[float]]]]
                                                    # station_id → {lead_bucket → (a,b,sigma)}

    fit_distribution(members, lead_time_hours, station_id) → (loc, scale, shape)
    bucket_probabilities(loc, scale, shape, buckets) → {market_id: prob}
    _bucket_probabilities_fallback(loc, scale, buckets) → {market_id: prob}
    _integrate_bucket(dist, bucket) → float
    _normal_cdf_bucket(loc, scale, bucket) → float
    compute_edges(model_probs, market_prices) → List[Dict]
    kelly_fraction(edge, model_prob, market_price, kelly_mult=0.25) → float
    _get_bias_offset(station_id, lead_time_hours) → float
    load_calibration(calibration_data) → None
    _get_emos_params(station_id, lead_time_hours) → Tuple[float, float, Optional[float]]
    load_emos_calibration(emos_data) → None
```

---

## WEATHER BOT — KEY METHODS REFERENCE

```python
class WeatherBot(BaseBot):
    # Sub-components
    _forecast_client: WeatherForecastClient
    _metar_client:   MetarClient               # NEW Session 56
    _prob_engine:    WeatherProbabilityEngine
    _market_mapper:  WeatherMarketMapper
    _station_health: StationHealthMonitor

    # Config (from settings/env)
    _min_edge:         0.15   (WEATHER_MIN_EDGE)
    _max_per_group:    200.0  (WEATHER_MAX_PER_GROUP_USD)
    _daily_loss_limit: 500.0  (WEATHER_DAILY_LOSS_LIMIT)
    _max_correlated:   500.0  (WEATHER_MAX_CORRELATED_EXPOSURE)
    _kelly_mult:       0.25   (WEATHER_KELLY_FRACTION)
    _default_size:     25.0   (WEATHER_DEFAULT_SIZE)
    _max_lead_time:    168.0  (WEATHER_MAX_LEAD_TIME_HOURS)

    # Key methods
    scan_and_trade()                          — main loop body
    _analyze_group(group) → List[Dict]        — per city+date opportunity finder
    _apply_metar_resolution_day_override(group, model_probs, lead_time_hours)
    _execute_weather_trade(opp, group)        — place trade with risk checks
    _get_scan_interval_seconds() → float      — adaptive: 60s/90s/120s/300s
    _maybe_reload_calibration()               — every 6h from DB
    _maybe_update_calibration_actuals()       — fill in actual_temp post-resolution
    _save_forecast_to_db(station, date, fc)   — persist forecast snapshot
    _restore_daily_pnl_from_db()              — load today's P&L on startup/day-change
    _near_boundary(loc, bucket, threshold=0.5) → bool   — WU boundary risk flag
    _fit_emos(pairs) → (a, b, sigma)          — pure Python OLS regression
    stop()                                    — closes forecast_client + metar_client
```

---

## METAR CLIENT — REFERENCE

```python
class MetarClient:
    API: https://aviationweather.gov/api/data/metar?ids={ICAO}&format=json&hours=N
    Free, no key, accepts ICAO codes (same as station_registry.station_id)

    parse_t_group(remarks: str) → Optional[float]
      # Extracts 0.1°C precision temp from T-group in METAR remarks
      # T02890267 → 28.9°C; T12890267 → -28.9°C

    get_latest_metar(station_id) → Optional[Dict]
      # Returns {temp_c, dew_c, obs_time, raw_text, station_id}

    get_running_daily_max(station_id, target_date, temp_unit="C") → Optional[float]
      # Queries last 24h, filters to target_date, returns running max
      # Caches 5 min per station+date
      # Returns in temp_unit (°F for US stations)

    close() → None  # Closes aiohttp session
```

---

## STATION REGISTRY — KEY STATIONS

All US stations use `temp_unit="F"`, `station_id` = 4-letter ICAO code.
International stations use `temp_unit="C"`.

```python
STATION_REGISTRY: Dict[str, WeatherStation] = {
    "new_york_city":  KLGA,  40.7772, -73.8726, F
    "atlanta":        KATL,  33.6407, -84.4277, F
    "seattle":        KSEA,  47.4502,-122.3088, F
    "dallas":         ...
    "miami":          ...
    "chicago":        ...
    "boston":         ...
    "phoenix":        ...
    "denver":         ...
    "los_angeles":    ...
    "london":         EGLC,  ..., C   (international)
    # ... more cities
}
```

---

## HOW MARKETS RESOLVE (CRITICAL CONTEXT)

- **Resolution source:** Weather Underground (WU) hourly max for the station's airport
- **NWS backup:** Some markets resolve via NWS official daily high
- **Resolution time:** Midnight local time (WU compiles daily high at end of day)
- **Key discrepancy (Dec 2025 NYC incident):** WU hourly max = 29°F, NWS official = 30°F
  → `at_or_below 30°F` bucket resolved unexpectedly YES on WU, NO on NWS
  → **This is why `_near_boundary()` halves confidence at boundaries**

---

## TEST STRUCTURE

```bash
# Run only weather tests (fast, 106 tests, ~14s)
python -m pytest tests/unit/test_weather_bot.py -q

# Run full suite (1134 tests, ~5 min)
python -m pytest tests/ -q

# Test classes:
TestWeatherStationRegistry       — station lookup, aliases, US_CITY_NAMES
TestWeatherMarketMapper          — regex parsing, bucket creation
TestProbabilityEngine            — fit_distribution, EMOS, tail discount, Kelly
TestWeatherForecastClient        — Open-Meteo API, NBM/NWS API
TestWeatherBot                   — scan, trade, calibration
TestWeatherBotOpportunities      — _analyze_group, near_boundary, _fit_emos
TestMetarClientParseGroup        — parse_t_group (pure unit, no I/O)
TestMetarClientAPI               — METAR API with mocked session
TestMetarResolutionDayOverride   — _apply_metar_resolution_day_override
TestHistoricalTemperatureAPI     — Open-Meteo archive API
TestECMWFEnsembleMerging         — 3-source AIFS merge
TestCrossCity                    — regime boost logic
TestCalibrationActuals           — _maybe_update_calibration_actuals
```

---

## GIT LOG (SESSION 56 COMMITS)

```
de7a6bf  feat: WU boundary uncertainty flag — halves position size near bracket boundaries
8c6c124  feat: NBM daily high via NWS API for US stations — overrides GFS deterministic
17284e2  feat: METAR client + resolution-day front-running override
725f556  feat: EMOS (a,b,sigma) calibration — OLS mean/spread correction from historical forecast pairs
b533f4f  feat: adaptive scan interval (model update windows)
2050a27  feat: tail bracket discount for at_or_below/at_or_higher (favourite-longshot bias)
fefc95b  fix: remove fixed 1.5%/hr spread inflation — use real ensemble spread
72a2944  feat: ECMWF AIFS ENS as 3rd ensemble source (82 → 133 members)
```

**To rollback any commit:** `git revert {sha}` then redeploy the affected files.

---

## CHANGE LOG (MANDATORY FORMAT)

```
## CHANGE: 2026-03-07
**Issue:** 8 Tier 1+2 WeatherBot improvements from research review
**Root cause:** N/A (enhancement session)
**Files modified:**
  base_engine/weather/probability_engine.py (EMOS, spread fix, tail discount)
  base_engine/weather/forecast_client.py (AIFS, NBM)
  base_engine/weather/metar_client.py (NEW)
  bots/weather_bot.py (adaptive scan, EMOS integration, METAR hook, NBM hook, boundary flag)
  tests/unit/test_weather_bot.py (106 total tests, 1134 full suite)
**Lines changed:** ~500 added, ~25 removed
**Blast radius:** WeatherBot only. No shared modules touched.
**Verification:** 1134 passed, 6 skipped. VPS restart clean. weatherbot_startup_availability logged.
**Rollback:** git revert de7a6bf 8c6c124 17284e2 725f556 b533f4f 2050a27 fefc95b 72a2944
```

---

## NEXT AGENT STARTER TASKS (PRIORITY ORDER)

**P1 — Deploy paper_trading.py side=BUY fix (cross-bot, 5 min)**
- File: `bots/paper_trading.py` (fixed locally in Session 55, not deployed)
- Fix: `_db_side = original_side` for entry rows (YES/NO), SELL for exits
- All 15 bots affected — verify each after restart

**P2 — Regime-conditioned EMOS (WeatherBot, ~2h)**
- Add `regime VARCHAR(20)` to `weather_calibration` DB table
- Add NOAA CPC El Niño/AO/NAO regime fetch (free API)
- `_fit_emos_by_regime()` + regime-aware `_get_emos_params()`
- Expected CRPS reduction: 10–15% additional

**P3 — Isotonic tail calibration (WeatherBot, ~3h)**
- Replace fixed 15% tail discount with isotonic regression per (bucket_type, lead_bucket)
- Requires ≥50 resolved tail events per cell (will take time to accumulate)
- Design the DB schema and storage logic now; calibrate once data exists

**P4 — Hurricane/severe weather alert hook (WeatherBot, ~1h)**
- `GET https://api.weather.gov/alerts/active` for coastal stations
- If Hurricane Watch/Warning within 200km → kelly_mult *= 2.0
- Fetch in parallel with forecast in `_analyze_group()`

**P5 — paper_trading.py PnL SQL fix (cross-bot, 30 min)**
- Current: `LOWER(pt.side) IN ('yes', 'buy')` — "buy" was a band-aid for old BUY storage
- After paper_trading.py fix deployed: change to `LOWER(pt.side) = 'yes'` only
- Verify PnL calculations are correct for all 15 bots

---

## CANONICAL REFERENCES

- **Main handoff:** `AGENT_HANDOFF_SESSION47_2026_03_03.md` (full system architecture)
- **This handoff:** `AGENT_HANDOFF_WEATHERBOT_SESSION56_2026_03_07.md`
- **Weather module docs:** `BOT_WEATHERBOT.md` (updated Session 55)
- **Memory file:** `C:\Users\samwa\.claude\projects\...\memory\MEMORY.md`
- **15 bots in BOT_REGISTRY:** MomentumBot DELETED — 15 remain

---

*This handoff is a complete snapshot. A new agent reading only this file can:*
*1. Understand exactly what WeatherBot does and why*
*2. See every file, method, and design decision*
*3. Continue any of the P1–P5 next tasks*
*4. Debug any issue using the traps/lessons section*
*5. Deploy any fix using the deploy pattern above*
