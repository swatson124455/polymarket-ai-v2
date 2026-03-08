# AGENT HANDOFF — WEATHERBOT SESSION 61
**Date:** 2026-03-08
**Bot Focus:** WeatherBot exclusively
**Session type:** Root cause diagnosis + 4 critical bug fixes + full system audit
**Tests at close:** 1242 passed, 6 skipped (all suites)
**VPS status:** `active (running)` — 200 weather markets scanned, 31 groups, 0 trades (correct behavior — all edges below 8% threshold with real probabilities)
**Commit:** `567f7e7` fix: WeatherBot doom loop — kill M1 uniform fallback + cooldown + confidence

---

## WHAT YOU ARE BUILDING

**WeatherBot** is a Polymarket temperature-bucket trading bot. It:
1. Finds markets phrased as "Will the highest temperature in NYC be between 72-75F on March 10?"
2. Fetches multi-model ensemble forecasts (133 members across GEFS + ECMWF IFS + ECMWF AIFS)
3. Fits a skew-normal distribution to the ensemble and integrates probability across each bucket's bounds
4. Compares model probability vs. Polymarket YES price: edge = model_prob - market_price
5. If edge is 8-25% (configurable), sizes via fractional Kelly and places a paper trade

**The end goal:** Beat a weather market that is systematically exploitable because:
- Retail bettors overweight extreme buckets (favourite-longshot bias)
- Market prices lag NWP model updates by 30-60 minutes
- Resolution-day outcomes can be front-run via METAR aviation observations 1-5 min post-observation

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
journalctl -u polymarket-ai -f | grep -E "weatherbot_|degenerate|pm_exit|edge_cap"
```

**DB queries (from VPS):**
```bash
sudo -u postgres psql -d polymarket -c "SELECT * FROM paper_trades WHERE bot_name='WeatherBot' ORDER BY created_at DESC LIMIT 20;"
```

---

## FILE MAP — WEATHERBOT ARCHITECTURE

```
base_engine/weather/
  forecast_client.py      -- Open-Meteo ensemble + deterministic + NWS NBM + climate normals
  metar_client.py         -- Aviation Weather Center METAR API
  probability_engine.py   -- Skew-normal fit, EMOS calibration, bucket integration, Kelly
  market_mapper.py        -- Regex -> TemperatureBucket + WeatherMarketGroup
  station_registry.py     -- ICAO->city registry (KLGA=NYC, KATL=Atlanta, etc.)
base_engine/execution/
  paper_trading.py        -- Paper trade engine (3x retry on DB persist)
  position_manager.py     -- Exit logic: stop-loss, take-profit, model reversal, hold-to-resolution
  order_gateway.py        -- Position tracking, exposure management
  exit_strategy.py        -- Dynamic exit thresholds (ExitParams)
base_engine/risk/
  liquidity_guardian.py   -- Order book slippage estimation + max safe size
  bankroll_manager.py     -- Per-bot Kelly sizing (WeatherBot: capital=2000, max_bet=100)
bots/
  weather_bot.py          -- Main bot: scan, _analyze_group, _execute_weather_trade
  base_bot.py             -- BaseBot ABC (place_order, scan loop, heartbeat)
tests/
  unit/test_weather_bot.py -- Weather-specific unit tests
```

---

## CRITICAL BUGS FIXED THIS SESSION

### THE DOOM LOOP: Why WeatherBot Lost 9/11 Trades

**Evidence from paper_trades DB:**
- 23 total trades, ALL on penny markets (1.5-4.5 cents). Zero trades on core buckets.
- Market `0x192c` entered 10 TIMES (same market, 10 BUYs, 10 SELLs)
- Buy-sell cycle: BUY -> stop-loss SELL in 3-6 min -> immediate re-BUY -> repeat
- ALL entries show confidence=0.95 (maximum possible) for 2-cent tail bets
- Avg buy: 2.06 cents. Avg sell: 0.55 cents. Systematic 75% loss per trade.

### Bug 1: M1 Uniform Fallback (THE SMOKING GUN)
**File:** `probability_engine.py:158-164`
**Root cause:** When ensemble is tight (low spread), all tail bucket probabilities clamp to 0.001 (line 151), total becomes < 0.01. The M1 fallback then assigns uniform 1/7 = 0.143 to each bucket. Market prices for tail buckets are 0.02-0.05. Bot sees model_prob=0.143, market_price=0.02, edge=12% -- a completely fake edge.
**Fix:** Return empty dict `{}` instead of uniform when total < 0.01. Caller handles empty model_probs correctly (no edges computed, no trades).
**Commit:** `567f7e7`

### Bug 2: Broken Cooldown (Re-Entry Spam)
**File:** `weather_bot.py:77, 151-160, 715`
**Root cause:** `_recently_exited` dict only populated on BUY success (line 715). When position_manager sells (stop-loss, model reversal), WeatherBot never knows. Bot re-enters same losing market every scan cycle (5 min).
**Fix:** Track `_known_open_markets` set. At start of each `scan_and_trade()`, compare against `order_gateway._open_position_markets`. Any market that disappeared was exited by PM -> add to `_recently_exited` with 15-min cooldown.
**Commit:** `567f7e7`

### Bug 3: Wrong Confidence Formula
**File:** `weather_bot.py:300, 454`
**Root cause:** Used `min(0.95, 0.50 + abs(edge))`. With fake 45%+ edges from M1 fallback, confidence hits 0.95 ceiling. This tells position_manager "95% sure" about a 2-cent tail bet.
**Fix:** Changed to `min(0.95, model_prob)` (same as EsportsBot). Model_prob correctly reflects actual model certainty.
**Commit:** `567f7e7`

### Bug 4: No Edge Sanity Cap
**File:** `weather_bot.py:418-420`
**Root cause:** No upper bound on edge. Any edge from model error (stale price, degenerate distribution) was accepted.
**Fix:** Added MAX_EDGE=0.25 (25%) cap. EsportsBot uses 0.20. Anything higher is almost certainly a model error.
**Commit:** `567f7e7`

### Parameter Change: MIN_EDGE 15% -> 8%
**File:** `weather_bot.py:64`
**Why:** With fake edges eliminated, core buckets (10-90 cents) have real 3-12% edge. At 15% threshold, ALL real opportunities were filtered out. EsportsBot uses 5%. WeatherBot at 8% is conservative but allows real trading.
**Commit:** `567f7e7`

---

## PRIOR SESSION FIXES STILL IN EFFECT

### Session 59-60 Self-Scout Fixes (17 items)
- **C1**: NaN/Inf filter in `fit_distribution()` + ensemble extraction
- **C4**: Boost stacking additive (max 2.0x), was multiplicative (max 3.0x)
- **H3**: NWS NBM timezone -- ISO datetime parse instead of string search
- **H6**: Price enrichment cap 50->200 markets
- **M4**: Exposure tracker pre-updated before `place_order()` (race fix)
- **M5**: Tail discount cold-start 0.85->0.90
- **M6**: METAR resolution-day cache 300s->60s
- **M7**: Reject group if >50% buckets lack prices
- **L1**: Boundary risk threshold 0.5F / 0.3C (unit-scaled)
- **H1**: Slippage-adjusted edge via LiquidityGuardian (in `_execute_weather_trade`)
- **H5/M9**: Paper trade DB persist 3x retry with backoff
- **L4**: Position re-evaluation with fresh model probabilities (Phase 4 in scan_and_trade)

### EnsembleBot Archived (Session 61)
- Removed from BOT_REGISTRY, main.py, base_bot.py, scheduler.py, dashboard.py
- 0.2% win rate, -$5,614.13 P&L. Now 14 active bots.
- Commit: `471d989`

### WeatherBot Sizing Increased (Session 61)
- capital: $500 -> $2000, max_bet: $50 -> $100, daily_cap: $200 -> $500, default_size: $25 -> $100
- Penny-bet filter: min price 0.01->0.05, max price 0.99->0.95

---

## DATABASE SCHEMA (PostgreSQL on VPS)

### `weather_calibration` -- EMOS training data
```sql
station_id VARCHAR(10), target_date DATE, forecast_time TIMESTAMP,
lead_time_hours FLOAT, forecast_temp FLOAT, actual_temp FLOAT (NULL until resolved),
bias FLOAT (NULL until actual), model_name VARCHAR(50)
UNIQUE(station_id, target_date, lead_time_hours)
```

### `weather_tail_calibration` -- Isotonic tail calibration
```sql
bucket_type VARCHAR(20), lead_bucket INT, bin_index INT,
model_prob_low FLOAT, model_prob_high FLOAT, calibrated_prob FLOAT, sample_count INT
```

### `paper_trades` -- live trade log (shared by all 14 bots)
```sql
bot_name VARCHAR -- "WeatherBot"
side VARCHAR     -- "YES"/"NO" for entries, "SELL" for exits
```

### `positions` -- open positions (shared)
```sql
bot_id VARCHAR   -- "WeatherBot" (NOT bot_name)
```

**DB access (MANDATORY pattern):**
```python
async with db.get_session() as session:
    from sqlalchemy import text
    rows = await session.execute(text("SELECT ..."), params)
```

---

## CURRENT WEATHERBOT CONFIGURATION

```python
# weather_bot.py __init__
_min_edge = 0.08          # WEATHER_MIN_EDGE (was 0.15)
_max_per_group = 200.0    # WEATHER_MAX_PER_GROUP_USD
_daily_loss_limit = 500.0 # WEATHER_DAILY_LOSS_LIMIT
_max_correlated = 500.0   # WEATHER_MAX_CORRELATED_EXPOSURE
_kelly_mult = 0.25        # WEATHER_KELLY_FRACTION
_default_size = 100.0     # WEATHER_DEFAULT_SIZE (was 25)
_max_lead_time = 168.0    # WEATHER_MAX_LEAD_TIME_HOURS

# Edge sanity cap (hardcoded in _analyze_group)
MAX_EDGE = 0.25           # Skip edges > 25%

# Penny-bet filter (in _analyze_group)
MIN_PRICE = 0.05          # Skip markets <= 5 cents
MAX_PRICE = 0.95          # Skip markets >= 95 cents

# Confidence formula
confidence = min(0.95, model_prob)  # Was min(0.95, 0.50 + abs(edge))

# bankroll_manager.py
WeatherBot: capital=2000, kelly_fraction=0.25, max_bet_usd=100, max_daily_usd=500
```

---

## DATA FLOW — END TO END

```
scan_and_trade():
  |
  |-- Detect PM exits (compare _known_open_markets vs order_gateway)
  |     -> Add to _recently_exited with 15-min cooldown
  |
  |-- _handle_daily_boundary() (reset PnL on new day)
  |-- _maybe_reload_calibration() (every 6h from DB)
  |-- _maybe_update_calibration_actuals() (fill actual_temp post-resolution)
  |
  |-- Phase 1: Analyze all groups
  |     FOR each (city, date) group:
  |       _analyze_group(group) -> (tradeable_opps, model_probs)
  |         |-- get_combined_forecast(station, target_date)
  |         |     |-- GFS deterministic (Open-Meteo)
  |         |     |-- GEFS+IFS+AIFS ensemble (133 members)
  |         |     |-- NBM via NWS API (US only)
  |         |     |-- All 3 in asyncio.gather()
  |         |
  |         |-- fit_distribution(members) -> (loc, scale, shape)
  |         |     |-- EMOS params: _get_emos_params(station, lead_time)
  |         |     |-- corrected_mean = a + b * raw_mean
  |         |     |-- skewnorm.fit + loc shift + sigma override
  |         |
  |         |-- bucket_probabilities(loc, scale, shape, buckets) -> {mid: prob}
  |         |     |-- CDF integration per bucket type
  |         |     |-- Isotonic tail calibration (or fallback 0.90 discount)
  |         |     |-- Normalize to sum=1.0
  |         |     |-- If degenerate (total < 0.01): return {} (M1 FIX)
  |         |
  |         |-- [Resolution day] _apply_metar_resolution_day_override()
  |         |     |-- get_running_daily_max() from METAR T-groups
  |         |     |-- Override eliminated/confirmed buckets
  |         |
  |         |-- [Climate prior] apply_climate_prior() for lead > 72h
  |         |     |-- Open-Meteo archive: 10-year normals, +/-3 day window
  |         |     |-- Blend: 0% at <=72h, ramps to 40% at >=168h
  |         |
  |         |-- compute_edges() -> filter by min_edge=0.08, max_edge=0.25
  |         |-- Apply: cooldown, penny filter, boundary risk, confidence=model_prob
  |
  |-- Phase 2: Regime boost (cross-city warm/cold front detection)
  |     |-- NOAA Nino 3.4 anomaly data (El Nino/La Nina)
  |     |-- Additive boost: 1 + (expiry-1) + (regime-1)*0.5 + (severe-1)*0.5, cap 2.0x
  |
  |-- Phase 3: Execute trades
  |     FOR each opportunity:
  |       _execute_weather_trade(opp, group)
  |         |-- Daily loss limit check
  |         |-- Group/city exposure limits
  |         |-- LiquidityGuardian slippage check (H1 fix)
  |         |     -> Skip if slippage > effective edge
  |         |     -> Cap size to max_safe_size (2% slippage tolerance)
  |         |-- Kelly sizing: kelly_fraction * confidence * boost
  |         |-- Size caps: min(kelly, remaining_group, remaining_city, 4x default, slippage_cap)
  |         |-- place_order(side=YES/NO, size, price, confidence)
  |         |-- Update _recently_exited on success
  |
  |-- Phase 4: Re-evaluate open positions (L4 fix)
  |     |-- Fresh model_probs from Phase 1 -> update position_manager's predicted_prob
  |     |-- Only updates when probability swing > 5%
```

---

## PROBABILITY ENGINE — FULL REFERENCE

```python
class WeatherProbabilityEngine:
    _calibration: Dict[str, Dict[int, float]]      # station_id -> {lead_bucket -> bias}
    _emos: Dict[str, Dict[int, Tuple[float, float, Optional[float]]]]
                                                    # station_id -> {lead_bucket -> (a,b,sigma)}
    _tail_calibration: Dict[Tuple[str,int], List]   # (bucket_type, lead_bucket) -> isotonic bins

    fit_distribution(members, lead_time_hours, station_id) -> (loc, scale, shape)
    bucket_probabilities(loc, scale, shape, buckets) -> {market_id: prob}
      # Returns {} if degenerate (total < 0.01) -- M1 FIX
    _bucket_probabilities_fallback(loc, scale, buckets) -> {market_id: prob}
    _integrate_bucket(dist, bucket) -> float
    _normal_cdf_bucket(loc, scale, bucket) -> float
    compute_edges(model_probs, market_prices) -> List[Dict]
    kelly_fraction(edge, model_prob, market_price, kelly_mult=0.25) -> float
    _get_bias_offset(station_id, lead_time_hours) -> float
    load_calibration(calibration_data) -> None
    _get_emos_params(station_id, lead_time_hours) -> Tuple[float, float, Optional[float]]
    load_emos_calibration(emos_data) -> None
    _get_tail_discount(bucket_type, lead_time_hours) -> float
    apply_climate_prior(model_probs, climate_mean, climate_std, lead_time_hours, buckets) -> Dict
```

---

## WEATHER BOT — KEY METHODS REFERENCE

```python
class WeatherBot(BaseBot):
    # Sub-components
    _forecast_client: WeatherForecastClient
    _metar_client:    MetarClient
    _prob_engine:     WeatherProbabilityEngine
    _market_mapper:   WeatherMarketMapper
    _station_health:  StationHealthMonitor

    # Config (current values)
    _min_edge:         0.08   (WEATHER_MIN_EDGE -- was 0.15)
    _max_per_group:    200.0  (WEATHER_MAX_PER_GROUP_USD)
    _daily_loss_limit: 500.0  (WEATHER_DAILY_LOSS_LIMIT)
    _max_correlated:   500.0  (WEATHER_MAX_CORRELATED_EXPOSURE)
    _kelly_mult:       0.25   (WEATHER_KELLY_FRACTION)
    _default_size:     100.0  (WEATHER_DEFAULT_SIZE -- was 25)
    _max_lead_time:    168.0  (WEATHER_MAX_LEAD_TIME_HOURS)

    # Risk state
    _daily_pnl: float                          # today's realized P&L
    _group_exposure: Dict[str, float]          # "city:date" -> USD deployed
    _city_exposure: Dict[str, float]           # city -> total USD deployed
    _recently_exited: Dict[str, float]         # market_id -> monotonic time (15-min cooldown)
    _known_open_markets: Set[str]              # snapshot for PM exit detection

    # Key methods
    scan_and_trade()                           -- main loop body (4 phases)
    _analyze_group(group) -> Tuple[List[Dict], Dict[str,float]]
                                               -- per city+date opportunity finder + model_probs
    _apply_metar_resolution_day_override(group, model_probs, lead_time_hours)
    _execute_weather_trade(opp, group)         -- place trade with risk checks
    _reevaluate_open_positions(analyzed)        -- Phase 4: update PM exit probabilities
    _get_scan_interval_seconds() -> float      -- adaptive: 60s/90s/120s/300s
    _maybe_reload_calibration()                -- every 6h from DB
    _maybe_update_calibration_actuals()        -- fill in actual_temp post-resolution
    _save_forecast_to_db(station, date, fc)    -- persist forecast snapshot
    _restore_daily_pnl_from_db()               -- load today's P&L on startup/day-change
    _near_boundary(loc, bucket, threshold)     -- WU boundary risk flag
    _fit_emos(pairs) -> (a, b, sigma)          -- pure Python OLS regression
    _compute_regime_boost(analyzed) -> float   -- El Nino/La Nina regime detection
    _get_afd_spread_factor(station) -> float   -- NWS AFD uncertainty keywords
    stop()                                     -- closes forecast_client + metar_client
```

---

## POSITION MANAGER EXIT LOGIC (Shared Engine)

Position manager handles ALL exits for ALL bots. WeatherBot does NOT manage its own exits.

```python
# position_manager.py _check_position():
1. Grace period (5 min): blocks model reversal exits, NOT stop-loss
2. Stop-loss: fires if cost_pnl_pct <= -30% (ExitParams.stop_loss_pct)
3. Take-profit: fires if cost_pnl_pct >= 60% (ExitParams.take_profit_pct)
4. Model reversal: fires if predicted_prob drops below threshold
   - L4 fix: predicted_prob now updated every scan via _reevaluate_open_positions()
5. Hold-to-resolution: only helps winning positions (current > entry)
```

**Key:** Stop-loss fires on PRICE regardless of model confidence. On penny markets (2 cents), 30% stop = exit at 1.4 cents. Penny market spreads are 50%+, so stop fires within minutes. This is why the penny-bet filter (5 cents minimum) is critical.

---

## KEY TRAPS / LESSONS LEARNED

### Trade Execution
1. **BUY/SELL vs YES/NO**: `BaseBot.place_order()` expects `side="YES"` or `side="NO"`. Never pass "BUY"/"SELL".
2. **paper_trades.bot_name vs positions.bot_id**: Different column names, same value "WeatherBot". Wrong column = 0 rows.
3. **Paper trade DB persist**: Now has 3x retry with 0.5s backoff. SELL failures log at ERROR (ghost position risk).

### Probability Engine
4. **M1 uniform fallback is DEAD**: Returns `{}` when distribution is degenerate. This was the #1 cause of fake edges.
5. **Single-bucket normalization**: With 1 bucket, prob normalizes to 1.0 creating artificial 100% edge. MAX_EDGE=0.25 cap catches this. Real groups have 5-9 buckets.
6. **EMOS cold start**: Identity params `(0, 1, None)` = no correction. Activates bucket-by-bucket when >= 20 pairs.
7. **Tail discount**: Isotonic calibration when >= 5 data points, otherwise 0.90 (10% discount).

### Weather APIs
8. **Polymarket category tagging unreliable**: Use keyword regex, not category filter.
9. **CLOB markets have volume=0**: Don't use volume gates.
10. **NWS API**: Requires `Accept: application/geo+json` header or returns 406.
11. **METAR T-group**: `\bT([01])(\d{3})([01])(\d{3})\b` -- needs word boundaries.
12. **METAR override only for lead_time < 6.0**: Resolution day only.
13. **NBM via NWS**: US stations only (temp_unit=="F"). Two-step: /points -> /forecast.
14. **TemperatureBucket**: Requires `temp_unit` field in constructor (or TypeError).
15. **Climate normals**: Open-Meteo archive API, +/-3 day window x 10 years. 1 new per scan. 30-day cache.
16. **AFD parsing**: NWS `/products?type=AFD&location={wfo}&limit=1`. 6h cache. US only.

### System Architecture
17. **14 active bots** (EnsembleBot archived). MomentumBot was deleted earlier.
18. **BOT_REGISTRY in main.py** is the canonical list of active bots.
19. **Position current_price** auto-updated every 10s by position_manager._update_current_prices().
20. **PSEUDO_LABEL_ENABLED=false**: DO NOT enable. Only Location 1 (market resolution) labels are correct.
21. **ENSEMBLE_BLEND=1.0**: Bypasses learning_conf.

---

## CURRENT BOT STATE AT HANDOFF

```
WeatherBot scan (post doom-loop fix):
  weather_markets: 200 (price-enriched)
  groups: 31 (parsed from regex matching)
  groups_with_edge: 0 (all edges below 8% with REAL probabilities)
  degenerate_distributions: 0 (none triggered -- distributions are healthy)
  trades: 0

Status: RUNNING CORRECTLY. Bot is scanning with real probability model.
        Previous 23 trades were ALL from fake M1 uniform edges.
        Bot will now only trade when genuine 8-25% edge exists on
        core buckets (5-95 cent price range).

Calibration: Building up. EMOS activates at >= 20 resolved pairs per bucket.
Paper trade P&L: -$0.33 (all from doom loop; effectively $0 fresh start)
Open positions: 0 (previous doom-loop positions expired/closed)
```

---

## WHAT TO WORK ON NEXT

### Priority 1: Monitor First Real Trades
The doom loop fix changes WeatherBot from "trades garbage constantly" to "trades selectively on real edge." Monitor VPS logs for first legitimate trades:
```bash
journalctl -u polymarket-ai -f | grep -E "weatherbot_trade_filled|weatherbot_scan_done"
```
If `groups_with_edge` stays at 0 for 24h+, consider:
- Lowering MIN_EDGE from 8% to 5% (match EsportsBot)
- Checking if weather market prices have moved closer to model probabilities (no exploitable edge)

### Priority 2: Calibration Data Accumulation
As weather markets resolve, `weather_calibration` table accumulates (forecast, actual) pairs. After 20+ pairs per station+lead_bucket, EMOS activates with proper (a, b, sigma) coefficients. This improves edge accuracy.

### Priority 3: Feature Improvements (Not Yet Built)
- **Confluence gate** (like EsportsBot): `0.55*edge + 0.30*freshness + 0.15*agreement`, min 0.60
- **Adaptive MIN_EDGE per lead time**: Closer to resolution = tighter edge required (less uncertainty)
- **Multi-model disagreement signal**: If GEFS says 72F and IFS says 68F, reduce confidence
- **Historical win-rate tracking per bucket_type**: Are we better at "between" or "at_or_below"?

### Priority 4: CrossPlatformArbBot P0 Fix
The orphan order bug (buy succeeds on Poly, sell fails on Kalshi, no cancel) is still unfixed. This is unrelated to WeatherBot but is a system-wide risk.

---

## FULL COMMIT HISTORY (This Session + Recent)

```
567f7e7 fix: WeatherBot doom loop -- kill M1 uniform fallback + cooldown + confidence
471d989 fix: archive EnsembleBot (0.2% win rate, -$5.6K) + remove WeatherBot penny bets
6b0bba5 fix: root resolves for 3 remaining self-scout issues (H1, H5/M9, L4)
237b5a5 fix: WeatherBot self-scout -- 17 defensive fixes across 6 files
0a809c5 feat: T3B climate normals prior + T3C AFD keyword parsing for WeatherBot
1483f20 feat: LoL model improvement -- Glicko-2 metadata features + blend for live
```

---

## FORECAST MODEL DATA FLOW

```
get_combined_forecast(station, target_date):
  +--------------------+   +-----------------------+   +----------------------+
  | GFS Deterministic  |   | GEFS+IFS+AIFS Ensemble|   | NBM via NWS API      |
  | Open-Meteo         |   | Open-Meteo            |   | (US stations only)   |
  | gfs_seamless       |   | 133 members total     |   | /points -> /forecast |
  | temp_2m_max/day    |   | per-member daily max  |   | isDaytime period     |
  +--------+-----------+   +----------+------------+   +----------+-----------+
           |                           |                            |
           +---------------------------+----------------------------+
                                       |
                               asyncio.gather() -- all 3 parallel
                                       |
                               deterministic_high = NBM ?? GFS
                               ensemble_members = [133 floats]
                               models_used = ["gfs025_ensemble","ecmwf_ifs025","ecmwf_aifs025","nbm"]
```

---

## METAR CLIENT REFERENCE

```python
class MetarClient:
    API: https://aviationweather.gov/api/data/metar?ids={ICAO}&format=json&hours=N
    Free, no key, same ICAO codes as station_registry

    parse_t_group(remarks: str) -> Optional[float]
      # T02890267 -> 28.9C; T12890267 -> -28.9C
    get_latest_metar(station_id) -> Optional[Dict]
      # Returns {temp_c, dew_c, obs_time, raw_text, station_id}
    get_running_daily_max(station_id, target_date, temp_unit="C") -> Optional[float]
      # Last 24h, filters to target_date, returns running max
      # Cache: 60s on resolution day (M6 fix), 300s otherwise
```

---

## CALIBRATION SYSTEM FLOW

```
SCAN CYCLE (every 60-300s)
  |
  +-- _maybe_reload_calibration()     [every 6h]
  |    SQL: SELECT station_id, lead_time_hours, bias, forecast_temp, actual_temp
  |         FROM weather_calibration WHERE bias IS NOT NULL
  |    -> Simple bias: station_id -> {lead_bucket -> mean(bias)}
  |    -> EMOS (>=20 pairs): station_id -> {lead_bucket -> (a, b, sigma)}
  |    -> prob_engine.load_calibration(cal_avg)
  |    -> prob_engine.load_emos_calibration(emos_params)
  |
  +-- _maybe_update_calibration_actuals()  [every scan]
  |    Finds rows with actual_temp=NULL and target_date in past
  |    Calls get_historical_temperature() from Open-Meteo archive
  |    Updates actual_temp + bias in weather_calibration
  |
  +-- _maybe_reload_tail_calibration() [every 6h]
       SQL: SELECT * FROM weather_tail_calibration
       -> Isotonic bins per (bucket_type, lead_bucket)
       -> Falls back to 0.90 discount if < 5 data points
```

---

## ADDITIONAL FEATURES IMPLEMENTED IN PRIOR SESSIONS

### T3B: Climate Normals Bayesian Prior (Session 57)
- `forecast_client.py`: `get_climate_normal()` fetches 10-year archive from Open-Meteo
- `probability_engine.py`: `apply_climate_prior()` blends toward climatology at >72h lead
- Blend: 0% at <=72h, ramps to 40% at >=168h
- Cached per (station, day_of_year) for 30 days

### T3C: AFD Keyword Parsing (Session 57)
- `weather_bot.py`: `_get_afd_spread_factor()` fetches NWS AFD for station's WFO
- Regex scans for uncertainty keywords
- Returns spread multiplier: 1.3 (high uncertainty), 1.15 (moderate), 0.9 (high confidence)
- WFO lookup: NWS `/points/{lat},{lon}` -> `properties.gridId`, cached permanently

### Regime-Conditioned EMOS (Session 57)
- NOAA PSL Nino 3.4 anomaly data for El Nino/La Nina detection
- Cached 24h. Tags calibration rows.
- Regime-specific EMOS when >=20 pairs/regime

### Isotonic Tail Calibration (Session 57)
- `weather_tail_calibration` DB table
- Per (bucket_type, lead_bucket) isotonic regression
- Falls back to 0.90 (was 0.85 before Session 59)

### Severe Weather Sizing (Session 57)
- NWS alerts API at `/alerts/active?point={lat},{lon}`
- Cached 30 min. Hurricane=2.0x, Tornado/Blizzard=1.5x. US only.

### Adaptive Scan Interval (Session 56)
- 60s during ECMWF ENS windows (07:00-08:00, 18:00-19:00 UTC)
- 90s during GFS windows (05:15-06:00, 17:15-18:00 UTC)
- 120s during HRRR window (:40-:59 each hour)
- Default: 300s

### LiquidityGuardian Integration (Session 61, commit 6b0bba5)
- Checks order book slippage before placing trade
- Skips if slippage > effective edge
- Caps size to max_safe_size with 2% slippage tolerance
- Fail-open: if check fails, behaves as before

### Position Re-evaluation (Session 61, commit 6b0bba5)
- Phase 4 in scan_and_trade()
- Updates predicted_prob on open positions with fresh forecast data
- Feeds position_manager's model-reversal exit logic
- Only logs when probability swing > 5%

### Paper Trade DB Persist Retry (Session 61, commit 6b0bba5)
- 3 attempts with 0.5s backoff
- SELL failures log at ERROR level (ghost position risk)
- Shared by all 14 bots, error-path only
