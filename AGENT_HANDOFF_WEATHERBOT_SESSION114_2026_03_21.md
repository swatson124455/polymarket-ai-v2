# WeatherBot Session 114 — EMOS Cold-Start Mitigation

**Date**: 2026-03-21
**Commits**: `32daca8` (S113 fixes), `325e0f2` (S114 cold-start)
**Deploy**: `20260321_120217`
**Tests**: 1642 passed, 0 failed, 8 skipped

## Session Scope

Carried forward from S113 (2E/2B/2C fixes + 4 Chinese cities), then implemented a full cold-start mitigation stack based on deep research into meteorological post-processing literature.

## S113 Changes (committed earlier this session)

1. **Fix 2E**: Negative daily counter restore — `continue` → `counter=0.0` in `_restore_daily_counters()`
2. **Fix 2B**: Cache jitter direction — subtract 0-10% instead of add 0-50% in `forecast_client.py`
3. **Fix 2C**: Gamma clamp logging in `precipitation_engine.py` — silent clamps now warn
4. **4 Chinese cities**: Chengdu (ZUUU), Chongqing (ZUCK), Shenzhen (ZGSZ), Wuhan (ZHHH) added to `station_registry.py`

## S114 Changes — Cold-Start Mitigation (4 items)

### Problem
New cities trade uncalibrated for 20+ days (EMOS needs ≥20 resolved pairs). Polymarket rotates cities every 2-3 weeks, so calibration data is often wasted.

### Item 1: Spread Confidence Gate
- **File**: `weather_bot.py` — `_get_min_edge()` + `_analyze_group()`
- **What**: Scale `min_edge` by `spread_ratio = clamp(σ_current / σ_typical_14d, 0.7, 1.5)`
- Tight ensemble spread → lower edge required, wide → higher
- Rolling 14-day `_spread_history` per station (deque maxlen=14)
- Needs ≥3 samples before activating; defaults to 1.0 (no effect) until then
- **Why**: Spread-skill relationship is the most robust signal in ensemble forecasting. Zero training data needed.

### Item 2: Bühlmann Sizing Ramp
- **File**: `weather_bot.py` — `_calibration_confidence()` + trade execution path
- **What**: Position size × `w = n/(n+30)` where n = resolved pairs per station
- n<5: block trading (log `weatherbot_cold_start_skip`)
- n=15: 33% size, n=30: 50%, n=120: 80%
- Returns 1.0 if calibration hasn't been loaded yet (pre-first-reload)
- `_station_n_resolved` exposed from `_maybe_reload_calibration()` (was local `_sc`)
- **Why**: Baker & McHale (2013) proved naive Kelly with estimated probs overbets. Bühlmann formula is minimum-MSE estimator.

### Item 3: Global EMOS Baseline
- **Files**: `weather_bot.py` + `probability_engine.py`
- **What**: Pool ALL stations' (forecast, actual) pairs → fit single global EMOS (a,b,σ)
- New fallback chain in `_get_emos_params()`: local → global → bias offset → identity
- `load_global_emos()` added to `WeatherProbabilityEngine`
- Global fitted in `_maybe_reload_calibration()` after per-station EMOS
- **Why**: Rasp & Lerch (2018) showed global EMOS ~10% worse than local but works from day 1.

### Item 4: Historical Bias Bootstrap
- **Files**: `forecast_client.py` + `weather_bot.py`
- **What**: On first encounter of cold station (n<5), fetch 90d GFS forecasts + ERA5 actuals from Open-Meteo, insert into `weather_calibration` table
- `fetch_historical_bias()` in `WeatherForecastClient` — uses `_HISTORICAL_URL` (ERA5) + `_DETERMINISTIC_URL` (GFS)
- `_maybe_bootstrap_cold_station()` in `WeatherBot` — called in `_analyze_group()` after forecast fetch
- Only runs once per station per session (`_bootstrapped_stations` set)
- Forces `_calibration_last_loaded = 0.0` to trigger immediate EMOS reload
- **Why**: Gives n≈90 immediately → w=0.75 (75% sizing) from day 1 instead of 20+ day ramp.

## Post-Deploy Monitoring

```bash
# Verify spread gate active
journalctl -u polymarket-ai -f | grep "weatherbot_spread_gate\|spread_ratio"

# Verify cold-start skip/bootstrap
journalctl -u polymarket-ai -f | grep "weatherbot_cold_start\|weatherbot_bootstrap"

# Verify global EMOS fitted
journalctl -u polymarket-ai -f | grep "weatherbot_global_emos"

# Verify calibration confidence in trade signals
journalctl -u polymarket-ai -f | grep "weatherbot_trade_signal" | head -5
```

## Key Implementation Notes

- `_calibration_confidence()` returns 1.0 when `_station_n_resolved` is empty (pre-first-reload), preventing false blocks before DB state is loaded
- `_spread_history` is in-memory only — resets on restart, rebuilds within ~3 scans
- `_bootstrapped_stations` is per-session — will re-bootstrap on restart if station still cold (idempotent via `ON CONFLICT DO NOTHING`)
- Global EMOS uses same `_fit_emos()` OLS as local — no new math, just pooled data
- `fetch_historical_bias()` estimates 24h lead time for all pairs (day-ahead forecast)

## Future Work (Viable Items)

### P2: SAMOS (Standardized Anomaly EMOS)
Normalize by ERA5 climatology before fitting global EMOS. Eliminates station-specific effects. Austrian Weather Service runs this operationally. **When**: After global EMOS validated (1-2 weeks).

### P3: Climate-Cluster Semi-Local EMOS
Cluster stations by climatological quantile features (Lerch & Baran 2017). **When**: If Polymarket reaches 50+ cities.

### P3: Grouped Per-Model EMOS
Fit per-model coefficients `μ = a + b_GFS·X̄_GFS + b_IFS·X̄_IFS + b_AIFS·X̄_AIFS`. **When**: After confirming blended EMOS + GEFS subsampling leaves skill on table.

### P4: Nearest-Station Transfer
Initialize new station EMOS from elevation-adjusted nearest analog (100m vertical ≈ 15km horizontal). **When**: After global EMOS, if it proves too generic.

### P4: City Rotation Prediction
Track Polymarket city presence matrix → predict additions → pre-compute bias before markets open. **When**: After item 4 is live.

### P5: MEMOS (Spatial EMOS)
Full Gaussian Markov Random Field. Needs 50+ stations + R-INLA. **When**: If station count exceeds 100.

### P5: Neural Network Post-Processing
Station embeddings (Rasp & Lerch 2018). Needs thousands of station-days. **When**: 6+ months data.

## S115 Cross-Bot Change: Shadow Fill Tracking (affects WeatherBot)

**Session**: S115 (same day, separate scope — all bots)
**Full handoff**: `AGENT_HANDOFF_SHADOW_FILLS_SESSION115_2026_03_21.md`

### What changed for WeatherBot:
- **paper_trading.py**: All theoretical slippage models REMOVED (alpha decay, Kyle's lambda, size tiers, fill probability, etc.). BUY orders now fill at real VWAP from L2 orderbook walk. WeatherBot's `alpha_decay_half_life_s: 1800` in event_data is now ignored (alpha decay deleted).
- **order_gateway.py**: Pre-trade book walk + edge-at-VWAP gate added. If `confidence <= VWAP`, trade rejected. Applies to paper AND live.
- **shadow_fills table**: Every BUY signal recorded with full book snapshot, VWAP, slippage, edge. Resolution backfill computes retroactive P&L.
- **scan_start_mono**: WeatherBot already had this (S100) — no change needed.
- **OrderBookTracker**: Now actually wired to PaperTradingEngine (was dead code before). WeatherBot's book walk fills are now live.
- **Net effect**: WeatherBot trades will fill at real book prices instead of theoretical slippage-adjusted prices. Fewer false rejections (no more random fill probability model). Edge gate uses real VWAP instead of fabricated slippage.

### Review items:
- [ ] After 24h: `SELECT COUNT(*), AVG(book_walk_slippage) FROM shadow_fills WHERE bot_name='WeatherBot'` — verify book data flowing
- [ ] WebSocket orderbook upgrade — deferred, review if shadow data shows >1 cent avg staleness cost

## Config (unchanged)
```
WEATHER_MIN_EDGE=0.08, WEATHER_INTL_MIN_EDGE=0.12
WEATHER_KELLY_FRACTION=0.25, WEATHER_MAX_POSITIONS=500
SIMULATION_MODE=true
```
