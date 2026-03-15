# AGENT HANDOFF — WEATHERBOT SESSION 62
**Date:** 2026-03-08
**Bot Focus:** WeatherBot exclusively — NO other bots
**Tests at close:** 1242 passed, 6 skipped
**VPS status:** `active (running)` — 210+ weather markets, 28 groups, trades filling at $100-$695 per position
**Prior session:** Session 61 handoff at `AGENT_HANDOFF_WEATHERBOT_SESSION61_2026_03_08.md`

---

## WHAT YOU ARE BUILDING

**WeatherBot** is a Polymarket temperature-bucket trading bot. It:
1. Finds markets phrased as "Will the highest temperature in NYC be between 72-75F on March 10?"
2. Fetches multi-model ensemble forecasts (133 members across GEFS + ECMWF IFS + ECMWF AIFS)
3. Fits a skew-normal distribution to the ensemble and integrates probability across each bucket's bounds
4. Compares model probability vs. Polymarket YES price: edge = model_prob - market_price
5. If edge passes filters, sizes via fractional Kelly and places a paper trade

**The edge:** Beat a weather market that is systematically exploitable because:
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
DB:      postgresql://polymarket:polymarket_s46@localhost:5432/polymarket
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
# Live trades
journalctl -u polymarket-ai -f | grep -i "weatherbot_trade_filled\|weatherbot_scan_done"
# Full weather logs
journalctl -u polymarket-ai -f | grep -i "weatherbot"
# Specific signal
journalctl -u polymarket-ai -f | grep -E "weatherbot_trade_signal|Order blocked.*WeatherBot"
```

**DB queries (from VPS):**
```bash
PGPASSWORD='polymarket_s46' psql -h localhost -U polymarket -d polymarket -c "SELECT LEFT(market_id,12) as mkt, side, size, entry_price, opened_at FROM positions WHERE bot_id='WeatherBot' AND status='open' ORDER BY opened_at DESC LIMIT 20;"
PGPASSWORD='polymarket_s46' psql -h localhost -U polymarket -d polymarket -c "SELECT COUNT(*) FROM weather_calibration;"
PGPASSWORD='polymarket_s46' psql -h localhost -U polymarket -d polymarket -c "SELECT COUNT(*) FROM weather_forecasts;"
```

---

## FILE MAP

```
bots/
  weather_bot.py              -- Main bot: scan_and_trade, _analyze_group, _execute_weather_trade
  base_bot.py                 -- BaseBot ABC (place_order, scan loop, heartbeat)

base_engine/weather/
  forecast_client.py          -- Open-Meteo ensemble + deterministic + NWS NBM + climate normals
  metar_client.py             -- Aviation Weather Center METAR API (running daily max)
  probability_engine.py       -- Skew-normal fit, EMOS calibration, bucket integration, Kelly
  market_mapper.py            -- Regex → TemperatureBucket + WeatherMarketGroup
  station_registry.py         -- ICAO→city registry (KLGA=NYC, KATL=Atlanta, etc.)

base_engine/risk/
  bankroll_manager.py         -- Per-bot Kelly sizing (WeatherBot: capital=5000, max_bet=500)
  risk_manager.py             -- Risk checks (WeatherBot-specific MIN_CONFIDENCE=0.15)
  liquidity_guardian.py       -- Order book slippage estimation

base_engine/execution/
  paper_trading.py            -- Paper trade engine (3x retry on DB persist)
  position_manager.py         -- Exit logic: stop-loss -30%, take-profit +60%, model reversal, hold-to-resolution
  order_gateway.py            -- Position tracking, exposure management

config/
  settings.py                 -- All WeatherBot settings (WEATHER_* env vars)
```

---

## SESSION 62 CHANGES — WHAT WAS FIXED AND WHY

### Fix 1: Market Discovery (ROOT CAUSE — bot was completely inert)
**Problem:** Standard ingestion pipeline fetches events paginated by ID, stops at ~1000 markets. Temperature events have IDs ~249000+ (page 45+), never reached. All 39 "weather" markets in DB had NULL prices and no CLOB orderbooks.
**Fix:** Added `_fetch_weather_events_by_tag()` using Gamma API `tag_slug=temperature` as PRIMARY market source. Returns all live temperature events with prices from `outcomePrices` field.
**File:** `bots/weather_bot.py` — new method + modified `scan_and_trade()` market fetch order
**Result:** 210-255 markets found per scan, all priced

### Fix 2: NO-Side Confidence (Kelly + risk manager broken for NO bets)
**Problem:** `confidence = min(0.95, model_prob)` for ALL trades. For NO side where model_prob=0.02, confidence=0.02 → Kelly returns $0 (confidence < price) → size=$1 floor. Risk manager also blocks (2% < 45% threshold).
**Fix:** YES side: `confidence = model_prob`. NO side: `confidence = 1.0 - model_prob`. This is correct for Kelly (represents P(NO outcome)) and passes risk manager.
**Files:** `bots/weather_bot.py` — two locations (line ~304 single-bucket path, line ~495 group path)

### Fix 3: Confidence Threshold (45% global floor kills multi-bucket trades)
**Problem:** `MIN_CONFIDENCE_THRESHOLD=0.45` in risk_manager designed for binary markets. In 9-bucket temperature markets, even the best bucket rarely exceeds 45% model_prob.
**Fix:** Added WeatherBot-specific override in risk_manager: uses `WEATHER_MIN_CONFIDENCE` (default 0.15) instead of global 0.45.
**Files:** `base_engine/risk/risk_manager.py` (3 lines added), `config/settings.py` (default 0.60→0.15)

### Fix 4: Calibration DB Writes (silent failure since inception)
**Problem:** `_save_forecast_to_db()` used `::jsonb` cast syntax which conflicts with asyncpg parameter binding. Every forecast save silently failed.
**Fix:** Changed to `CAST(:param AS jsonb)` syntax.
**File:** `bots/weather_bot.py` — `_save_forecast_to_db()`
**Result:** 26+ rows in both `weather_calibration` and `weather_forecasts` tables

### Fix 5: Bet Sizing Caps (user requested larger bets)
**Problem:** `max_bet_usd=100`, `capital=2000`, `max_daily_usd=500` capped all trades at $100.
**Fix:** Raised to `capital=5000`, `max_bet_usd=500`, `max_daily_usd=2000`. Group cap $200→$1000, city cap $500→$2000. Removed `_default_size * 4.0` redundant cap.
**Files:** `base_engine/risk/bankroll_manager.py`, `config/settings.py`, `bots/weather_bot.py`
**Result:** First trade post-fix: Chicago YES $457, NO trade $695

### Fix 6: Lead-Time-Graduated Edge Cap
**Problem:** Flat 25% edge cap killed legitimate high-edge trades at short lead times where the 130-member ensemble is highly accurate.
**Fix:** Graduated cap matching expiry_boost tiers:
- `<6h: 0.70` (METAR available)
- `<12h: 0.50` (final-call)
- `<24h: 0.40` (day-of)
- `<48h: 0.30` (moderate)
- `>=48h: 0.25` (conservative)
**File:** `bots/weather_bot.py` — `_analyze_group()` edge filter
**Result:** `best_edge=-0.3988` passing through (40% edge at short lead time)

---

## CURRENT CONFIGURATION (LIVE ON VPS)

### BotBankrollManager
```python
WeatherBot: {capital: 5000, kelly_fraction: 0.25, max_bet_usd: 500, max_daily_usd: 2000}
```

### settings.py defaults (env vars override)
```python
WEATHER_MIN_EDGE = 0.08            # 8% minimum edge to trade (was 0.15, lowered Session 61)
WEATHER_MIN_CONFIDENCE = 0.15      # Risk manager floor (multi-bucket: 9 outcomes → peak ~35-40%)
WEATHER_MAX_PER_GROUP_USD = 1000   # Max per (city, date) group
WEATHER_DAILY_LOSS_LIMIT = 2000    # Daily loss halt
WEATHER_MAX_CORRELATED_EXPOSURE = 2000  # Max per city across dates
WEATHER_KELLY_FRACTION = 0.25     # Quarter-Kelly (can graduate to 0.35/0.50 via monitoring)
WEATHER_DEFAULT_SIZE = 25          # Fallback size when Kelly fails
WEATHER_MAX_LEAD_TIME_HOURS = 168  # 7-day max
WEATHER_FORECAST_CACHE_TTL = 900   # 15-min forecast cache
SCAN_INTERVAL_WEATHER = 300        # 5-min scan default (adaptive: 60-120s during NWP windows)
```

### VPS .env overrides (only weather-relevant ones)
```
BOT_ENABLED_WEATHER=true
RISK_MIN_VOL_WEATHERBOT=0
RISK_MIN_PRICE_WEATHERBOT=0.005
# WEATHER_MIN_CONFIDENCE not set → uses settings.py default 0.15
```

---

## DATA FLOW — COMPLETE SCAN CYCLE

```
scan_and_trade()
  │
  ├─ 1. _handle_daily_boundary() — reset P&L at UTC midnight
  ├─ 2. _maybe_reload_calibration() — every 6h: EMOS + bias + tail cal from DB
  ├─ 3. _check_monitoring_thresholds() — every 10min: Brier/drawdown halt
  ├─ 4. Detect PM exits → add to _recently_exited (15min cooldown)
  ├─ 5. _reset_climate_cycle() on forecast_client
  ├─ 6. _check_weather_market_availability() — one-time startup
  │
  ├─ 7. MARKET DISCOVERY (primary → fallback → last resort):
  │     a) _fetch_weather_events_by_tag()        ← Gamma API tag_slug=temperature (PRIMARY)
  │     b) get_all_tradeable_markets(weather)     ← DB category
  │     c) _fetch_weather_markets_direct()        ← DB+API fallback (30min rate limit)
  │
  ├─ 8. _market_mapper.group_markets() → groups by (city, date)
  │
  ├─ 9. PHASE 1: For each group → _analyze_group()
  │     ├─ Skip past dates / lead_time > 168h
  │     ├─ Station health check
  │     ├─ get_combined_forecast() → 133 ensemble members
  │     ├─ _save_forecast_to_db() → weather_forecasts + weather_calibration
  │     ├─ fit_distribution() → (loc, scale, shape) skew-normal
  │     ├─ Climate prior blend (>72h lead time)
  │     ├─ AFD spread adjustment
  │     ├─ bucket_probabilities() → {market_id: prob}
  │     ├─ METAR override (<6h lead time)
  │     ├─ M7 coherence check (>50% buckets need prices)
  │     ├─ compute_edges() → [{market_id, edge, side, abs_edge}]
  │     └─ Filter: min_edge → graduated edge cap → cooldown → penny filter → position check → boundary risk
  │         → confidence: YES=model_prob, NO=1-model_prob (halved if boundary_risk)
  │
  ├─ 10. PHASE 2: _compute_regime_boost() → 1.2x if ≥3 US cities agree
  │
  ├─ 11. PHASE 3: Execute trades
  │     ├─ ≥2 opps in group → _execute_group_trades() (S-T multi-bucket sizing)
  │     └─ 1 opp → _execute_weather_trade() (independent Kelly)
  │         ├─ Daily loss limit check
  │         ├─ Group/city exposure check
  │         ├─ Expiry boost: <12h=2.0x, <24h=1.5x, <48h=1.2x
  │         ├─ Regime boost: 1.2x if detected
  │         ├─ Severe weather boost: 2.0x hurricane, 1.5x tornado
  │         ├─ Combined boost (additive, capped 2.0x) × Baker-McHale k*
  │         ├─ Slippage check (LiquidityGuardian)
  │         ├─ Kelly sizing via BotBankrollManager ($500 max, $2K daily)
  │         └─ place_order() → risk_manager checks → paper_trading
  │
  └─ 12. PHASE 4: _reevaluate_open_positions() → fresh probs for exit logic
```

---

## KEY INTERFACES — WHAT WEATHERBOT DEPENDS ON

### probability_engine.py
```python
fit_distribution(ensemble_members, lead_time_hours, station_id) → (loc, scale, shape)
bucket_probabilities(loc, scale, shape, buckets, lead_time_hours) → {market_id: prob}
compute_edges(model_probs, market_prices) → [{market_id, model_prob, market_price, edge, abs_edge, side}]
apply_climate_prior(loc, scale, clim_mean, clim_std, lead_time_hours) → (loc, scale)  # static
```

### market_mapper.py
```python
TemperatureBucket: market_id, token_id, no_token_id, yes_price, bucket_type, low_bound, high_bound, temp_unit
WeatherMarketGroup: city, target_date, station, buckets, slug_prefix, temp_unit
parse_market(market_data) → Optional[TemperatureBucket]
group_markets(weather_markets) → List[WeatherMarketGroup]
```

### forecast_client.py
```python
CombinedForecast: ensemble_members (133 floats), deterministic_high, model_spread, lead_time_hours, models_used
get_combined_forecast(station, target_date) → Optional[CombinedForecast]  # cached 900s
get_climate_normal(lat, lon, date, temp_unit) → Optional[(mean, std)]
get_historical_temperature(lat, lon, date, temp_unit) → Optional[float]
```

### bankroll_manager.py
```python
WeatherBot config: {capital: 5000, kelly_fraction: 0.25, max_bet_usd: 500, max_daily_usd: 2000}
get_bet_size(confidence, price, calibration_quality?, category?) → float USD
# Kelly: f = (p*b - q) / b where b=(1-price)/price
# Calibration scaling if Brier > 0.15, drawdown compression if DD > 2%
```

### risk_manager.py
```python
check_risk_limits(bot_name, market_id, size, price, confidence, prediction?) → {allowed, reasons}
# WeatherBot uses WEATHER_MIN_CONFIDENCE (0.15) instead of global MIN_CONFIDENCE_THRESHOLD (0.45)
```

---

## DB SCHEMA — WEATHER TABLES

### weather_calibration
```sql
id BIGINT PK, station_id VARCHAR(20), target_date TIMESTAMP, forecast_temp DOUBLE,
actual_temp DOUBLE (nullable), lead_time_hours DOUBLE, bias DOUBLE (nullable),
model_name VARCHAR(50), regime VARCHAR(20), created_at TIMESTAMP
UNIQUE(station_id, target_date, lead_time_hours)
```

### weather_forecasts
```sql
id BIGINT PK, station_id VARCHAR(20), target_date TIMESTAMP, forecast_time TIMESTAMP,
lead_time_hours DOUBLE, ensemble_members JSONB, deterministic_high DOUBLE,
model_spread DOUBLE, models_used JSONB, created_at TIMESTAMP
UNIQUE(station_id, target_date, forecast_time)
```

### positions (WeatherBot rows)
```sql
bot_id='WeatherBot', market_id, side (YES/NO), size (shares), entry_price,
current_price (auto-updated 10s), status (open/closed), opened_at, closed_at
```

### paper_trades (WeatherBot rows)
```sql
bot_name='WeatherBot', market_id, side (YES/NO), size (shares), price (fill price), created_at
```

---

## CALIBRATION SYSTEM

```
EVERY SCAN:
  _maybe_update_calibration_actuals()
    → Find weather_calibration rows where actual_temp IS NULL and target_date in past
    → Fetch actual via get_historical_temperature() from Open-Meteo archive
    → UPDATE actual_temp, bias = actual_temp - forecast_temp

EVERY 6 HOURS (_maybe_reload_calibration):
  → Load weather_calibration → compute per-station EMOS (OLS: actual = a + b*forecast)
  → Regime-conditioned EMOS when ≥20 samples per regime
  → Load weather_tail_calibration → isotonic bins per (bucket_type, lead_bucket)
  → prob_engine.load_calibration(), load_emos_calibration(), load_tail_calibration()

EVERY 10 MINUTES (_check_monitoring_thresholds):
  → Compute 7-day Brier score (MSE of model_prob vs actual_outcome)
  → MSE > 25 → CRITICAL halt (stop trading)
  → MSE > 16 → WARNING
  → Dynamic Kelly graduation:
    * 100+ resolved + MSE<9 → kelly_fraction 0.25→0.35
    * 200+ resolved + MSE<4 → kelly_fraction 0.35→0.50
    * Auto-downgrades if MSE degrades
```

---

## FEATURE INVENTORY (ALL IMPLEMENTED)

| Feature | Location | Description |
|---------|----------|-------------|
| Tag-based market discovery | weather_bot.py `_fetch_weather_events_by_tag()` | Gamma API `tag_slug=temperature` — PRIMARY source |
| 133-member ensemble | forecast_client.py | GEFS(31) + ECMWF IFS(51) + ECMWF AIFS(51) |
| NWS NBM deterministic | forecast_client.py `get_nbm_forecast()` | US-only, 7-day National Blend of Models |
| Skew-normal distribution | probability_engine.py `fit_distribution()` | MLE fit with normal fallback |
| EMOS calibration | probability_engine.py | OLS regression per station × lead_time bucket (6h bins) |
| Regime-conditioned EMOS | weather_bot.py `_maybe_reload_calibration()` | El Nino/La Nina/Neutral via NOAA Nino 3.4 |
| Isotonic tail calibration | probability_engine.py `_get_tail_discount()` | Fallback 0.90 if <5 data points |
| Climate normals prior | forecast_client.py `get_climate_normal()` | 10-year archive, blends 0-40% at >72h lead |
| AFD spread adjustment | weather_bot.py `_get_afd_spread_factor()` | NWS Area Forecast Discussion uncertainty keywords |
| METAR resolution override | weather_bot.py `_apply_metar_resolution_day_override()` | <6h lead: actual obs replace model probs |
| Severe weather boost | weather_bot.py `_get_severe_weather_boost()` | NWS alerts: 2.0x hurricane, 1.5x tornado, US only |
| Cross-city regime boost | weather_bot.py `_compute_regime_boost()` | ≥3 US cities unanimous → 1.2x Kelly |
| S-T multi-bucket sizing | weather_bot.py `_smoczynski_tomkins_allocate()` | Pro-rata by edge for mutually exclusive buckets |
| Baker-McHale k* scaling | weather_bot.py `_execute_weather_trade()` | k* = 1/(1+σ²), reduces size when spread is high |
| Expiry boost | weather_bot.py | <12h=2.0x, <24h=1.5x, <48h=1.2x |
| Combined boost stacking | weather_bot.py | Additive: 1+(expiry-1)+(regime-1)*0.5+(severe-1)*0.5, cap 2.0x |
| Lead-time graduated edge cap | weather_bot.py `_analyze_group()` | <6h=0.70, <12h=0.50, <24h=0.40, <48h=0.30, ≥48h=0.25 |
| Boundary risk discount | weather_bot.py `_near_boundary()` | 0.5°F US / 0.3°C intl → 50% confidence reduction |
| Penny-bet filter | weather_bot.py | Skip price ≤0.05 or ≥0.95 |
| Adaptive scan interval | weather_bot.py `_get_scan_interval_seconds()` | 60s ECMWF, 90s GFS, 120s HRRR windows, 300s default |
| Slippage guard | weather_bot.py (H1) | LiquidityGuardian: skip if slippage > edge |
| Position re-evaluation | weather_bot.py `_reevaluate_open_positions()` | Fresh probs → position_manager exit logic |
| Dynamic Kelly graduation | weather_bot.py `_check_monitoring_thresholds()` | 100+resolved+MSE<9→0.35, 200++MSE<4→0.50 |
| CRPS scoring | weather_bot.py `_compute_crps()` | Ferro 2014 fair CRPS on ensemble |
| Forecast persistence | weather_bot.py `_save_forecast_to_db()` | weather_forecasts + weather_calibration tables |
| NO-side confidence | weather_bot.py | YES: model_prob, NO: 1-model_prob (correct for Kelly) |

---

## CURRENT STATE — LIVE TRADING

### Open Positions (as of session end)
- **34 open positions**: 21 NO / 13 YES across cities (Paris, Seoul, Chicago, NYC, Buenos Aires, Atlanta, Ankara, Seattle, São Paulo, London, Toronto, Miami, Dallas)
- **Total deployed**: ~$79 USD (before sizing increase)
- **First large trade**: Chicago YES $457 (post sizing fix)

### Calibration Pipeline
- `weather_calibration`: 26+ rows, populating every scan
- `weather_forecasts`: 26+ rows, snapshot per (station, date, 15min bucket)
- `actual_temp` backfill: runs every scan for past dates via Open-Meteo archive

### What to Monitor
```bash
# Trades filling
journalctl -u polymarket-ai -f | grep weatherbot_trade_filled
# Scan summary
journalctl -u polymarket-ai -f | grep weatherbot_scan_done
# Bankroll sizing
journalctl -u polymarket-ai -f | grep "BotBankrollManager.*WeatherBot"
# Calibration saves
journalctl -u polymarket-ai --since "1 hour ago" | grep weatherbot_forecast_saved
# Any errors
journalctl -u polymarket-ai --since "1 hour ago" | grep -i "weatherbot.*error\|weatherbot.*failed"
```

---

## TRAPS AND GOTCHAS

1. **BUY/SELL vs YES/NO**: `BaseBot.place_order()` expects `side="YES"` or `side="NO"`. Never pass "BUY"/"SELL".
2. **TemperatureBucket requires `temp_unit`** in constructor or TypeError.
3. **NWS API requires `Accept: application/geo+json`** header or returns 406.
4. **asyncpg jsonb**: Use `CAST(:param AS jsonb)` not `:param::jsonb` with SQLAlchemy text().
5. **Gamma API tag_slug=temperature**: Only reliable weather market discovery method. `_q=`, `tag=`, `slug_contains=` all return garbage.
6. **CLOB midpoint returns "No orderbook exists"** for weather tokens. Use `outcomePrices` from Gamma events endpoint instead.
7. **Weather market IDs ~249000+**: Far beyond standard ingestion pipeline reach (stops at ~1000 markets by ID order).
8. **Polymarket category tagging unreliable**: Weather markets have `category: None`. Use keyword matching.
9. **CLOB markets have volume=0**: Don't use volume gates for weather.
10. **position `current_price`**: Auto-updated every 10s by `position_manager._update_current_prices()`.
11. **PSEUDO_LABEL_ENABLED=false**: DO NOT enable. Only Location 1 (market resolution) labels are correct.
12. **paper_trades schema**: `bot_name` column (not `bot_id`). `positions` schema: `bot_id` column.
13. **DB query pattern**: `db.get_session()` → `async with ... as session:` → `session.execute(text(...))`.
14. **`websockets.exceptions`** must be imported explicitly (v15 lazy-loads).

---

## WHAT TO WORK ON NEXT

### Priority 1: Monitor Trade Outcomes
- Watch paper_trades for WeatherBot resolved positions
- Check if model probabilities are calibrated (do 35% model_prob buckets win ~35% of the time?)
- Track daily P&L: `SELECT SUM(size * (exit_price - entry_price)) FROM positions WHERE bot_id='WeatherBot' AND status='closed';`

### Priority 2: Calibration Data Accumulation
- weather_calibration needs 20+ rows per station to activate EMOS
- weather_calibration needs 5+ per (bucket_type, lead_bucket) for isotonic tail calibration
- Monitor: `SELECT station_id, COUNT(*) FROM weather_calibration WHERE actual_temp IS NOT NULL GROUP BY station_id;`

### Priority 3: Edge Quality Analysis
- Are high-edge trades (>25%) at short lead times winning?
- Is boundary_risk correctly identifying risky bucket boundaries?
- Are NO-side trades profitable? (These are the highest-confidence trades now)

### Priority 4: Feature Improvements (if warranted by data)
- Tighten/loosen graduated edge caps based on win rate by tier
- Add more weather stations (currently ~15 cities)
- Consider MIN_EDGE reduction from 0.08 to 0.05 if calibration is good
- METAR resolution-day trading could be more aggressive at <2h lead time

### Priority 5: Code Cleanup
- Diagnostic `weatherbot_group_pricing` and `weatherbot_raw_edges` info-level logs could be demoted to debug once confident
- `_fetch_weather_markets_direct()` fallback path is rarely used now, could simplify
- Consider making graduated edge cap configurable via settings

---

## SESSION 61 BUG FIXES (CONTEXT — ALREADY DEPLOYED)

These were fixed BEFORE Session 62. Listed here for context:

1. **M1 Uniform Fallback**: `probability_engine.py:158-164` — degenerate distributions returned uniform probs creating fake edges. Fixed: return `{}` instead.
2. **Broken Cooldown**: PM exits never populated `_recently_exited`. Fixed: track `_known_open_markets`, detect PM exits by diff.
3. **Wrong Confidence Formula**: Used `0.50+edge` (hit 0.95 ceiling with fake edges). Fixed: use `model_prob` (Session 61), then refined to YES=model_prob / NO=1-model_prob (Session 62).
4. **No Edge Sanity Cap**: No upper bound on edge. Fixed: MAX_EDGE=0.25, then graduated by lead time (Session 62).
5. **MIN_EDGE too high**: Was 15%, lowered to 8% (Session 61).

---

## SYSTEM ARCHITECTURE (14 ACTIVE BOTS)

WeatherBot is one of 14 active bots in the system. Other bots (EsportsBot, MirrorBot, etc.) run independently. When modifying shared modules (`base_bot.py`, `bankroll_manager.py`, `risk_manager.py`, `settings.py`):
- Check blast radius for all 14 bots
- Run full test suite (1242 tests)
- The risk_manager WeatherBot confidence override is scoped: `if bot_name == "WeatherBot"`

**BOT_REGISTRY**: 14 active bots. MomentumBot DELETED, EnsembleBot ARCHIVED.
