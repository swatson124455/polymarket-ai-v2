# AGENT HANDOFF — WeatherBot Session 118 (2026-03-22)

## STATUS: 5 STRUCTURAL P&L FIXES IMPLEMENTED — NOT YET DEPLOYED

- **Session**: 118
- **Date**: 2026-03-22
- **Deploy**: `20260322_154614` (edge cap removal only) — S118 structural fixes NOT YET DEPLOYED
- **Tests**: 1668 passed, 0 failed, 8 skipped

---

## WHAT HAPPENED THIS SESSION (S118)

### Phase 1: S117 Diagnosis Correction

The S117 handoff diagnosed the bot breakage (groups_with_edge=0) as caused by Changes A (YES confidence gate) and B (edge sign flip) in `_analyze_single_bucket()`. **This diagnosis was WRONG.**

- `_analyze_single_bucket()` does NOT EXIST. Changes A+B are in `analyze_opportunity()` — a BaseBot fallback method NOT used in the main scan loop.
- The main scan path goes through `scan_and_trade()` -> `_analyze_group()` -> `_prob_engine.compute_edges()`. None of the S116 code changes touch this path.
- The S116 git diff shows only 65 lines changed across 4 locations in `weather_bot.py`: `__init__` (2 attrs), `analyze_opportunity()` (YES gate + edge flip), wind analysis (YES gate), and boost cap (2.0 -> `self._combined_boost_cap`).
- No changes to `probability_engine.py`, `forecast_client.py`, or any shared module.

### Phase 2: Real Root Cause Discovery

VPS log analysis revealed:
- **0 group errors** post-deploy (no exceptions in `_analyze_group`)
- **6,376 rate limit hits** (Open-Meteo 429s) — the climatology backfill was running simultaneously, throttling the forecast API
- Bot self-healed within ~2 hours as rate limits cleared
- By 19:48 UTC: 35 cities scanning, 145 groups, 1500 markets — healthy

The breakage was ENVIRONMENTAL (API throttling), not CODE. The S116 code changes were cosmetic (dead code path) and harmless.

### Phase 3: S116 Code Cleanup

- Reverted Changes A (YES gate in `analyze_opportunity`) and B (edge sign flip)
- Reverted wind YES gate addition
- Reverted `self._combined_boost_cap` -> back to hardcoded 2.0
- Wired 3 missing env vars into `config/settings.py`:
  - `WEATHER_BUHLMANN_KAPPA` (L713, default 30.0)
  - `WEATHER_YES_MIN_CONFIDENCE` (L715, default 0.0 = disabled)
  - `WEATHER_COMBINED_BOOST_CAP` (L717, default 2.0)

### Phase 4: Edge Cap Removal (DEPLOYED)

- Removed the lead-time-graduated edge cap from `_analyze_group()` (was at L1840-1852)
- Data analysis of 7,886 resolved signals showed the 0.70+ edge bucket had 87.3% win rate — the HIGHEST of any bucket
- The cap was blocking the bot's strongest signals
- Deploy `20260322_154614` — live and verified, 0 edge cap rejections post-deploy

### Phase 5: Deep P&L Analysis (6 root causes identified)

Queried trade_events + prediction_log + positions + weather_calibration tables. Full findings below.

**A. NO Entry Price Trap**

| NO Entry Price | N | WR | P&L | Avg Win | Avg Loss |
|---|---|---|---|---|---|
| <60c | 508 | 73.4% | +$1,836 | +$11.53 | -$18.26 |
| 60-70c | 149 | 63.1% | +$32 | +$12.95 | -$21.56 |
| 70-80c | 330 | 76.4% | **-$484** | +$7.24 | -$29.60 |
| 80-90c | 390 | 88.7% | +$536 | +$4.20 | -$20.83 |
| 90c+ | 150 | 94.0% | +$98 | +$2.65 | -$30.60 |

Root cause: 70-80c has 0.24x win/loss ratio. Need 80%+ WR to break even, only getting 76.4%.

**B. Correlated City Blowups**

| Date | City | Positions Lost | Total Loss |
|---|---|---|---|
| 03-22 | Miami | 12 | -$976 |
| 03-22 | London | 8 | -$608 |
| 03-14 | Seattle | 3 | -$498 |
| 03-22 | Dallas | 10 | -$402 |

Root cause: Multiple positions in same city+date, all lose on same resolution.

**C. Position Stacking**

Only 45% of markets have a single entry. 55% have 2-15 entries. One Chicago market had 15 entries.

Root cause: `order_gateway._open_position_markets` doesn't include paper positions. Bot re-enters same market across scans.

**D. High-Confidence NO Losses**

| Confidence | N Losses | Total Loss | Avg Loss | Worst |
|---|---|---|---|---|
| 90-95% | 133 | -$3,412 | -$25.65 | -$413 |
| 80-85% | 15 | -$607 | -$40.49 | -$327 |

Root cause: Kelly sizes big at 90%+ confidence. When wrong, entire entry cost lost.

**E. Recent Trades Net Negative**

All enriched (post-backfill) temperature trades are net negative. The +$3,045 total P&L comes from early-period trades with market_type=NULL. Possible edge decay or seasonal transition.

**F. Overnight Entries Losing**

UTC 0: -$374, UTC 6: -$438. Model runs are stale at these hours.

**G. Calibration Depth**

5 US stations (KLAX, KIAH, KAUS, KSFO, KDEN) have only 4 calibration pairs each — below Buhlmann minimum.
Madrid (LEMD) has avg_bias of -2.048 degrees C — systematic overshoot.

**H. prediction_log `was_correct` is misleading**

The `was_correct` flag measures calibration accuracy (was model closer to truth than market), NOT trade profitability. A YES trade at 12c with model_prob=0.28 is "correct" 89% of the time, but the token only pays 28% of the time.

### Phase 6: 5 Structural Fixes Implemented (NOT YET DEPLOYED)

All fixes are WeatherBot-scoped. No shared module changes. 1668 tests pass.

**Fix 1: Position Stacking Guard** — DB-backed re-entry check
- Location: `_analyze_group()` after L1879
- Queries `positions` table: `SELECT 1 FROM positions WHERE market_id=:mid AND bot_id='WeatherBot' AND status='open' LIMIT 1`
- Fail-open: if DB unreachable, falls through to trade
- The existing in-memory `_open_position_markets` check stays as fast-path

**Fix 2: NO Entry Price Cap**
- Setting: `WEATHER_NO_MAX_ENTRY_PRICE=0.65` (config/settings.py L718-720)
- Location: `_analyze_group()` after penny-bet filter
- `if side == "NO" and price > _no_max_price: continue`
- Data-driven: <60c is +$1,836, 70-80c is -$484

**Fix 3: Max Buckets Per Group**
- Setting: `WEATHER_MAX_BUCKETS_PER_GROUP=3` (config/settings.py L721-723)
- Location: `_analyze_group()` before `tradeable.append()`
- `if len(tradeable) >= _max_buckets: break`
- Keeps top 3 by edge magnitude (edges are pre-sorted by abs_edge from `compute_edges`)

**Fix 4: High-Confidence NO Discount**
- Settings: `WEATHER_NO_CONFIDENCE_DISCOUNT=0.80`, `WEATHER_NO_CONFIDENCE_DISCOUNT_THRESHOLD=0.70` (config/settings.py L724-728)
- Location: `_analyze_group()` before `tradeable.append()`
- `if side == "NO" and price > threshold: effective_confidence *= discount`
- Reduces Kelly sizing for expensive NO without blocking

**Fix 5: Quiet Hours Edge Boost**
- Settings: `WEATHER_QUIET_HOURS_START=0`, `WEATHER_QUIET_HOURS_END=7`, `WEATHER_QUIET_HOURS_EDGE_MULT=1.5` (config/settings.py L729-733)
- Location: `_get_min_edge()` at end of function (L391-398)
- `if quiet_start <= current_hour_utc < quiet_end: base *= mult`
- Requires 12% edge (vs 8%) during stale-data hours

### Phase 7: trade_events Metadata Backfill

- Ran SQL to backfill city + lead_time_hours into 934 ENTRY events that were missing metadata
- Used market_id JOIN to positions table for city, and event_time math for lead_time
- This makes P&L analysis by city/lead_time possible for the full history

### Phase 8: Climatology Backfill

- Kicked off `backfill_climatology.py` on VPS for the 12 remaining stations
- Previous failures were rate-limit related (Open-Meteo 429)

---

## FILES MODIFIED THIS SESSION

| File | Changes | Safe? |
|---|---|---|
| `bots/weather_bot.py` | Edge cap removed, 5 structural fixes added, S116 dead code reverted | Yes — WeatherBot only |
| `config/settings.py` | 10 new WEATHER_* settings added (3 S116 wiring + 7 S118 new) | Yes — no signature changes |
| `tests/unit/test_weather_bot.py` | Added `settings` import, `@patch.object` for quiet hours on 5 tests, `_spread_history` to bot stub | Yes — test-only |
| `tests/unit/test_weather_cold_start.py` | Added `settings` import, `@patch.object` class decorator on TestGetMinEdge | Yes — test-only |

---

## CURRENT SYSTEM STATE

- **Deploy**: `20260322_154614` (edge cap removal only). S118 structural fixes are local, NOT deployed.
- **Open positions**: ~193 ($6,301 deployed)
- **All-time realized P&L**: ~+$3,045
- **35 active cities** scanning, 145 groups, 1500 markets
- **Station count**: 106 (94 with SAMOS climatology, 12 pending backfill)
- **Bot health**: Scanning normally, `groups_with_edge=0` at 20:00 UTC is expected (US markets resolved for the day)
- **Global EMOS**: `a=0.79, b=0.98, sigma=2.99` from 7,160 pairs
- **Local EMOS**: 20 stations ready, 5 pending

---

## OUTSTANDING ITEMS

### Immediate (deploy)

- Deploy S118 fixes (5 structural changes). `bash deploy/deploy.sh`
- Verify post-deploy: `journalctl -u polymarket-ai -f | grep 'weatherbot_scan_done'`
- Watch for `groups_with_edge` staying reasonable (should decrease slightly due to NO price cap + bucket limit)

### P0 — Edge Decay Investigation (Fix 6, not done)

All enriched temperature trades are net negative. Need to determine if this is:
1. Market efficiency (other traders using ensemble data now)
2. Seasonal transition (spring calibration drift)
3. Sample artifact (small enriched sample vs large pre-backfill)

Run: `PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/weather_brier.py --hours 720`

Compare week-over-week Brier reliability + resolution scores.

### Backlog

| Item | Status | Description |
|---|---|---|
| Climatology backfill | Running | 12/106 stations pending. Re-run if incomplete. |
| 3A (remaining) | Open | ~9 more hardcoded values need env var configs |
| 3B | Open | Test coverage ~44%. Zero tests for S118 fixes |
| 3D | Open | Multi-city correlation (NYC+Boston ~0.6 temp correlation) |
| YES confidence gate | Deferred | Implement properly in `_analyze_group()` after edge decay investigation. Use `WEATHER_YES_MIN_CONFIDENCE` setting (already wired, default 0.0 = disabled) |

### Monitor

| Item | Watch for | Trigger |
|---|---|---|
| S118 fix impact | Position count drop (fewer stacked), NO trade volume drop | 24h after deploy |
| Quiet hours edge boost | Trades at UTC 0-7 should decrease, P&L should improve | 1 week |
| NO price cap | No trades >65c entry. Check that cheap NO trades remain profitable | 1 week |
| Edge decay | Brier scores week over week | Run script weekly |
| Calibration depth | KLAX/KIAH/KAUS/KSFO/KDEN with 4 pairs — monitor resolution accumulation | 2 weeks |

---

## ALL NEW SETTINGS (S118)

```
# S118 Fix 2: NO entry price cap
WEATHER_NO_MAX_ENTRY_PRICE=0.65

# S118 Fix 3: Max buckets per group
WEATHER_MAX_BUCKETS_PER_GROUP=3

# S118 Fix 4: High-confidence NO discount
WEATHER_NO_CONFIDENCE_DISCOUNT=0.80
WEATHER_NO_CONFIDENCE_DISCOUNT_THRESHOLD=0.70

# S118 Fix 5: Quiet hours
WEATHER_QUIET_HOURS_START=0
WEATHER_QUIET_HOURS_END=7
WEATHER_QUIET_HOURS_EDGE_MULT=1.5

# S116 wiring (previously dead, now in Settings class):
WEATHER_BUHLMANN_KAPPA=30.0
WEATHER_YES_MIN_CONFIDENCE=0.0  # disabled
WEATHER_COMBINED_BOOST_CAP=2.0
```

---

## FULL DATA INSIGHTS (carry forward)

### Performance by Side (ALL resolved)

- **NO**: 635 trades, ~80% WR, +$2,018 (but losing at all lead times for enriched data)
- **YES**: 311 trades, ~16% WR, +$682 (wins pay 6x losses — favorable asymmetry)

### NO Entry Price Breakdown

| NO Entry Price | N | WR | P&L | Avg Win | Avg Loss |
|---|---|---|---|---|---|
| <60c | 508 | 73.4% | +$1,836 | +$11.53 | -$18.26 |
| 60-70c | 149 | 63.1% | +$32 | +$12.95 | -$21.56 |
| 70-80c | 330 | 76.4% | **-$484** | +$7.24 | -$29.60 |
| 80-90c | 390 | 88.7% | +$536 | +$4.20 | -$20.83 |
| 90c+ | 150 | 94.0% | +$98 | +$2.65 | -$30.60 |

### Correlated City Blowups

| Date | City | Positions Lost | Total Loss |
|---|---|---|---|
| 03-22 | Miami | 12 | -$976 |
| 03-22 | London | 8 | -$608 |
| 03-14 | Seattle | 3 | -$498 |
| 03-22 | Dallas | 10 | -$402 |

### High-Confidence NO Losses

| Confidence | N Losses | Total Loss | Avg Loss | Worst |
|---|---|---|---|---|
| 90-95% | 133 | -$3,412 | -$25.65 | -$413 |
| 80-85% | 15 | -$607 | -$40.49 | -$327 |

### Losing Cities (NO side specifically)

- Miami: -$792 (82.8% WR, 12 positions lost on Mar 22 boundary)
- London: -$413 (77.3% WR)
- Buenos Aires: -$225 (66.7% WR)
- Dallas: -$186 (79.5% WR)

### Sizing Asymmetry

- NO avg win = $9.19, avg loss = -$28.59 (3.1x ratio)
- YES avg win = +$30.22, avg loss = -$3.31

### Weekly Trend

$6.59/trade (Mar 9) -> $0.22/trade (Mar 16). 97% decline in per-trade profitability.

### YES by Entry Price

- 10-20c is the bleeder (-$180)
- 20-35c is the sweet spot (+$388)

### Position Stacking

Only 45% of markets have a single entry. 55% have 2-15 entries. One Chicago market had 15 entries.

### Overnight Losses by Entry Hour (UTC)

- UTC 0: -$374
- UTC 6: -$438

### Calibration Depth

5 US stations (KLAX, KIAH, KAUS, KSFO, KDEN) have only 4 calibration pairs each — below Buhlmann minimum.
Madrid (LEMD) has avg_bias of -2.048 degrees C — systematic overshoot.

### was_correct vs Profitable

`prediction_log.was_correct` measures calibration accuracy (was model closer to truth than market), NOT trade profitability. A YES trade at 12c with model_prob=0.28 is "correct" 89% of the time, but the token only pays 28% of the time. Do NOT use `was_correct` as a win rate proxy.

### Weekly P&L (RESOLUTION events from S117)

| Week | Side | N | P&L | WR |
|------|------|---|-----|-----|
| Mar 16 | NO | 473 | +$454.15 | 85.8% |
| Mar 16 | YES | 221 | +$145.28 | 15.8% |
| Mar 9 | NO | 162 | +$1,300.65 | 74.7% |
| Mar 9 | YES | 90 | +$468.13 | 15.6% |

### Side Performance Notes

- YES WR is 15-16% but still profitable because winning YES trades pay large (buying cheap tokens). However, the 85% loss rate on YES is a drag. A confidence gate IS the right idea — just needs to be implemented in `_analyze_group()` (not dead `analyze_opportunity()`), with proper data validation.
- NO side: 72% WR overall, +$1,896 — primary profit driver (favourite-longshot bias)
- YES side: 39% WR (note: different stat basis from weekly table above), +$985
- Combined: 62% WR, profitable

### Hold Duration Sweet Spot

- **24-48h holds** are best: 73% WR
- **0-2h exits**: 42% WR — early exits lose
- **48h+ holds**: 62-73% WR — weather signals converge over time

### Trade Size Distribution (from S117)

- Last 20 trades: all NO-side, ranging $3.72-$494.49
- Median around $80-$100 for US cities, $15-$40 for international
- Miami outlier at $494 shows the sizing CAN work when boosts align

---

## CURRENT CONFIGURATION (VPS .env)

```
WEATHER_MIN_EDGE=0.08                    # 8% minimum edge (US)
WEATHER_INTL_MIN_EDGE=0.12               # 12% (international)
WEATHER_MAX_PER_GROUP_USD=200.0           # Max per city+date group
WEATHER_DAILY_LOSS_LIMIT=500.0            # Stop if daily P&L < -$500
WEATHER_MAX_CORRELATED_EXPOSURE=500.0     # Max per city (all dates)
WEATHER_KELLY_FRACTION=0.25              # Kelly multiplier
WEATHER_DEFAULT_SIZE=100.0               # Default position size
WEATHER_MAX_LEAD_TIME_HOURS=168.0        # Max 7 days ahead
WEATHER_EXIT_COOLDOWN_SECS=14400         # 4hr re-entry cooldown
WEATHER_BM_FLOOR=0.50                    # Baker-McHale minimum
WEATHER_MIN_TRADE_USD=5.0                # Min position size
WEATHER_MAX_POSITIONS=500                # Position cap
WEATHER_SKIP_COORDINATOR_BUY=true        # Bypass TradeCoordinator
SIMULATION_MODE=true                     # Paper trading
# S115 env vars (all using defaults — not in .env):
# WEATHER_BUHLMANN_KAPPA=30              # Buhlmann formula denominator
# WEATHER_SPREAD_RATIO_MIN=0.7           # Spread gate min clamp
# WEATHER_SPREAD_RATIO_MAX=1.5           # Spread gate max clamp
# WEATHER_CALIBRATION_RELOAD_SECS=21600  # Calibration reload interval (6h)
# WEATHER_BRIER_HALT_MSE=0.35            # Per-station Brier halt threshold
# WEATHER_DEFAULT_MODEL_SPREAD=3.0       # Fallback when spread unavailable
# WEATHER_SEVERE_HALT_EVENTS=Hurricane Warning,Tornado Warning,Extreme Wind Warning
# S118 env vars (all using defaults — not in .env):
# WEATHER_NO_MAX_ENTRY_PRICE=0.65
# WEATHER_MAX_BUCKETS_PER_GROUP=3
# WEATHER_NO_CONFIDENCE_DISCOUNT=0.80
# WEATHER_NO_CONFIDENCE_DISCOUNT_THRESHOLD=0.70
# WEATHER_QUIET_HOURS_START=0
# WEATHER_QUIET_HOURS_END=7
# WEATHER_QUIET_HOURS_EDGE_MULT=1.5
# WEATHER_YES_MIN_CONFIDENCE=0.0         # disabled
# WEATHER_COMBINED_BOOST_CAP=2.0
```

---

## WEATHERBOT STRATEGY (how it makes money)

1. **Fetch** GFS/HRRR/GEFS + ECMWF ensemble forecasts via Open-Meteo (free, no API key) — 133 ensemble members total (31 GEFS + 51 IFS + 51 AIFS)
2. **Fit** skew-normal distribution to ensemble spread (with EMOS/SAMOS calibration if available)
3. **Integrate** CDF across each temperature bucket's bounds -> model probabilities
4. **Compare** model probs vs market-implied probs (YES prices on Polymarket)
5. **Trade** when edge >= 8% US / 12% international, sized by fractional Kelly (0.25)
6. **Hold** until resolution, TP/SL exit, or model reversal

### Scan Loop Structure (`scan_and_trade()`)
1. Discover weather markets via tag-based API fetch
2. Group by city+date -> `WeatherMarketGroup`
3. Analyze each group (ensemble fetch -> EMOS/SAMOS calibration -> probability -> edge detection)
4. Also scan precipitation, snowfall, wind markets (via shared `_scan_psw_markets()` template)
5. Execute trades for opportunities passing all filters
6. Re-evaluate open positions for exits

---

## WEATHERBOT FILE MAP

### Primary Bot File
| File | Lines | Purpose |
|------|-------|---------|
| `bots/weather_bot.py` | ~4200 | Main bot — scan loop, analysis, trading, state, monitoring, calibration, SAMOS |

### Supporting Modules (under `base_engine/weather/`)
| Module | Lines | Purpose |
|--------|-------|---------|
| `forecast_client.py` | ~1600 | Open-Meteo API wrapper, GFS/HRRR/GEFS/ECMWF ensemble fetching, historical bias bootstrap, ERA5 climate archive, Redis cache |
| `probability_engine.py` | ~530 | Skew-normal CDF integration, bucket probability computation, EMOS calibration, global EMOS/SAMOS fallback |
| `precipitation_engine.py` | ~231 | Precipitation gamma-distribution model, NDFD POP integration |
| `market_mapper.py` | ~1135 | Question parsing -> 8 dataclasses (Temp/Precip/Snow/Wind buckets + groups), 22 regex patterns |
| `station_registry.py` | ~1550 | 106 stations (US+intl+4 Chinese), ICAO/WMO codes, aliases, health monitoring |
| `model_run_monitor.py` | ~200 | GFS/ECMWF/HRRR model run tracking, priority queue |
| `metar_monitor.py` | ~150 | Real-time METAR observation polling, boundary crossing detection |
| `metar_client.py` | ~100 | METAR API client |

### Scripts
| Script | Purpose |
|--------|---------|
| `scripts/bot_pnl.py` | Canonical P&L script (all bots) |
| `scripts/weather_brier.py` | Murphy (1973) Brier decomposition by city/lead/type/side |
| `scripts/backfill_climatology.py` | ERA5 10-year climatology backfill for SAMOS |

### Shared Modules (changes affect ALL 15 bots — REQUIRE full blast-radius analysis)
| Module | WeatherBot-specific notes |
|--------|--------------------------|
| `base_engine/execution/paper_trading.py` | **S115 REWRITE**: All theoretical slippage REMOVED. BUY fills at real VWAP from L2 orderbook walk. |
| `base_engine/execution/order_gateway.py` | Pre-trade book walk + edge-at-VWAP gate. If `confidence <= VWAP`, rejected. |
| `config/settings.py` | All WEATHER_* settings |
| `base_engine/risk/bankroll_manager.py` | `BotBankrollManager` handles SIZING |
| `base_engine/risk/risk_manager.py` | Handles LIMITS (deprecated for sizing) |

---

## KEY STATE DICTIONARIES

```python
_group_exposure: Dict[str, float]       # "city:date" -> USD deployed (lock-protected)
_city_exposure: Dict[str, float]        # city -> total USD deployed
_recently_exited: Dict[str, float]      # market_id -> monotonic time (4hr cooldown)
_fill_fail_tracker: Dict[str, Tuple]    # market_id -> (consec_fails, last_mono)
_market_group_cache: Dict[str, Tuple]   # market_id -> (group_key, city, cost_usd)
_daily_pnl: float                       # today's realized P&L
_scan_start_mono: float                 # monotonic time at scan start
_consecutive_no_edge: int               # adaptive backoff counter (Redis-persisted)
# S114 additions:
_spread_history: Dict[str, deque]       # station_id -> 14-day model_spread rolling window
_station_n_resolved: Dict[str, int]     # station_id -> resolved forecast-actual pairs
_bootstrapped_stations: Set[str]        # stations already bootstrapped this session
# S115 additions:
_climatology: Dict[str, Dict[int, Tuple]]  # station_id -> {doy: (clim_mean, clim_std)}
```

### State Persistence
| State | Mechanism | Restore |
|-------|-----------|---------|
| `_group_exposure`, `_city_exposure` | daily_counters write-through | `_restore_exposure_from_db()` |
| `_market_group_cache` | populated on trade, rebuilt on startup | `_rebuild_market_group_cache()` |
| `_recently_exited` | Redis key per market_id with TTL | `_restore_exits_from_redis()` |
| `_consecutive_no_edge` | Redis key with 1h TTL | `_restore_backoff_from_redis()` |
| `_daily_pnl` | trade_events SUM | `_restore_daily_pnl_from_db()` |
| `_spread_history` | In-memory only — rebuilds in ~3 scans | N/A |
| `_station_n_resolved` | Refreshed from `weather_calibration` every 6h | `_maybe_reload_calibration()` |
| `_climatology` | Loaded from `weather_climatology` every 6h | `_maybe_reload_calibration()` |

---

## SIZING PIPELINE (how trade size is computed)

Located in `_execute_weather_trade()`:

1. **Short-term override**: If `_st_size_override` exists, use directly (capped)
2. **Kelly sizing**: `BotBankrollManager.calculate_kelly_bet()` with multiplicative boosts:
   - Expiry boost (1.0-2.0x based on lead time)
   - Regime boost (1.0-1.2x from cross-city consensus)
   - Jump boost (from model run / METAR boundary events)
   - NBM benchmark boost (1.15x when NBM agrees)
   - Baker-McHale factor (0.50-1.0x from ensemble spread, floored at `WEATHER_BM_FLOOR`)
   - Station reliability factor (0.5-1.2x from per-station MSE)
   - Buhlmann calibration confidence (0.0-1.0x from `n/(n+k)`, k=`WEATHER_BUHLMANN_KAPPA`)
   - **S115 fix**: 2.0x cap now applied AFTER all factors (was before BM/station/calibration)
3. **Min trade floor**: `WEATHER_MIN_TRADE_USD=5.0`
4. **Exposure locks**: Atomic reservation under `_exposure_lock` for group + city
5. **bestAsk pre-filter**: Skips trade if `confidence <= bestAsk` (no edge after depth)
6. **Severe weather halt**: Blocks trade if NWS hurricane/tornado/extreme wind warning active (US only)

---

## EMOS/SAMOS CALIBRATION SYSTEM (S114+S115 architecture)

### How EMOS Works
- **Keys**: `(station_id, lead_time_hours)` where lead_time bucketed in 6h intervals
- **Formula**: `mu_corrected = a + b * X_bar`, `sigma_emos = sigma`
- **Fitting**: OLS regression on (forecast_temp, actual_temp) pairs via `_fit_emos()`
- **Minimum**: 20 pairs per (station, lead_bucket) to activate local EMOS
- **Reload**: Every `WEATHER_CALIBRATION_RELOAD_SECS` (default 6h) from `weather_calibration` table

### SAMOS (S115)
- **What**: Standardized Anomaly MOS — normalizes forecasts/actuals by ERA5 climatological (mean, std) before global EMOS fitting
- **Why**: Chengdu 30C and Helsinki 5C become comparable anomalies. Global fit captures cross-station bias structure without station-specific effects.
- **Data source**: `weather_climatology` table (populated by `backfill_climatology.py`)
- **Formula**: `z = (temp - clim_mean) / clim_std`, then `mu_corrected = clim_mean + clim_std * (a + b * z_forecast)`
- **Recency weighting**: Years 0-2 = full weight, then 0.85 decay per year. `clim_std` floored at 1.0.

### Cold-Start Mitigation (4-layer stack from S114)

**Layer 1 — Spread Confidence Gate** (`_get_min_edge()`):
- `effective_min_edge = base_min_edge * clamp(sigma_current / sigma_typical_14d, 0.7, 1.5)`
- Zero training data needed, works from first scan

**Layer 2 — Buhlmann Sizing Ramp** (`_calibration_confidence()`):
- `w = n/(n+k)` where n = resolved pairs, k = `WEATHER_BUHLMANN_KAPPA` (default 30)
- n<5 blocks trading; n=15: 33% size; n=30: 50%; n=120: 80%

**Layer 3 — Global EMOS Baseline** (+ SAMOS from S115):
- Fallback chain: local EMOS -> SAMOS global -> raw global -> bias offset -> identity
- `load_global_emos()` / `load_samos_emos()` on `WeatherProbabilityEngine`

**Layer 4 — Historical Bias Bootstrap** (`_maybe_bootstrap_cold_station()`):
- First encounter of cold station (n<5) -> fetches 90d GFS+ERA5 from Open-Meteo
- Gives n~90 immediately -> w=0.75 (75% sizing) from day 1

### DB Tables
- `weather_calibration` (station_id, target_date, forecast_temp, actual_temp, lead_time_hours, bias, model_name, crps, regime, clim_mean, clim_std)
- `weather_climatology` (station_id, day_of_year, clim_mean, clim_std, n_years) — 94/106 stations backfilled
- `weather_tail_calibration` (bucket_type, lead_time_bucket, model_prob, actual_outcome, station_id)
- `weather_forecasts` (station_id, target_date, ensemble_members JSONB, deterministic_high, model_spread, models_used)
- `shadow_fills` (S115 cross-bot — book walk VWAP, slippage, edge per signal)

---

## P&L DATA MODEL

- **Authoritative source**: `trade_events` table (NEVER `paper_trades`)
- **Event types**: ENTRY, EXIT, RESOLUTION
- **Formulas** (ALL sides, NEVER invert for NO):
  - `cost = entry_price * size`
  - `uPnL = (current_price - entry_price) * size`
- **Canonical script**: `python scripts/bot_pnl.py WeatherBot 24`
- **Paper trading fill model**: Real L2 orderbook VWAP (S115 rewrite). No more theoretical slippage.

---

## NEW CITY ONBOARDING (fully automated as of S114+S115)

1. **Manual**: Add `WeatherStation` to `station_registry.py` (ICAO, GHCND, lat/lon, elevation, tz, temp_unit, aliases)
2. **Automatic**: Bootstrap -> global EMOS -> Buhlmann ramp -> spread gate (all from first scan)
3. **Manual (one-time)**: `backfill_climatology.py --station <id>` for SAMOS climatology
4. **Automatic**: Next calibration reload picks up climatology

**Only gap**: No auto-detection of new Polymarket cities.

---

## HARDCODED VALUES AUDIT (3A — completed analysis, not yet extracted)

Found 20 actionable hardcoded values across 3 files. Full report from S117:

### Highest Priority (Tier 2 — trade-universe gating)
1. **Edge cap schedule** (5 lead-time tiers): REMOVED in S118 (data showed it blocked best signals)
2. **Penny-bet price filter**: `price <= 0.04 or price >= 0.97`
3. **Drawdown halt/warn**: `20% halt, 10% warn`

### Most Useful to Tune (Tier 1)
4. NBM boost (1.3x)
5. Combined boost cap (2.0 — now configurable via `WEATHER_COMBINED_BOOST_CAP`)
6. Kelly graduation schedule (n>=200 + MSE<4 -> 0.50 Kelly)
7. Station MSE tiers (4/9/16 thresholds -> 1.2/1.0/0.8/0.5 multipliers)
8. Boundary risk factor (0.5x penalty)
9. Regime boost (3+ cities -> 1.2x)
10. 429 cooldown (3600s)
11. API timeout (15s)
12. Tail discount default (0.90)
13. Alpha decay half-life (1800s)
14. Drawdown schedule [(8, 0.25), (5, 0.50), (3, 0.75)]
15. Monitoring interval (600s)
16. Stale position age (20h)
17. Drift error threshold (3.0 degrees F)
18. Ensemble std floor (0.5 degrees)
19. NBM sigma schedule (1.5/2.5/3.5/5.0 by lead time)
20. Climate blend schedule (72h start, 168h end, 40% max weight)

**DO NOT extract all 20 at once.** Do 3-5 per session, test each, deploy incrementally.

---

## CRITICAL TRAPS (35 items — DO NOT BREAK)

1. **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
2. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL
3. **VPS deploys via `deploy.sh`**: atomic symlink swap. Working tree != VPS != git HEAD
4. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
5. **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager used
6. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
7. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
8. **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
9. **Python 3.13 scoping**: `from X import Y` inside function body -> entire function shadows Y
10. **websockets v15**: `import websockets.exceptions` must be explicit
11. **Paper trading IS production** — never skip features because "we're only paper trading"
12. **positions table**: NO `closed_at`, NO `updated_at` columns
13. **prediction_log columns**: NO `rejection_reason` — use `trade_executed` flag
14. **`traded_markets.bot_names`**: TEXT column (not array), use `LIKE '%BotName%'`
15. **`paper_trades` has NO `metadata` JSONB column**
16. **Resolution backfill excludes SELL trades** — SELL P&L computed at exit time
17. **trade_events immutability trigger**: Must DISABLE/ENABLE for data cleanup
18. **RESOLUTION event idempotency**: ON CONFLICT broken on partitioned tables -> atomic INSERT...SELECT
19. **Ghost positions fixed**: Idempotent memory returns `success: False`, order gateway guards `_filled_size > 0`
20. **`_market_group_cache`**: 3-tuple `(group_key, city, cost_usd)` — NEVER expand without updating all consumers
21. **`_close_stale_positions()` does direct DB UPDATE** — no trade_events EXIT record (by design)
22. **Exposure reserved BEFORE `place_order()` under `_exposure_lock`**, reverted on failure
23. **`event_data` dict mutated in-place** by paper_trading.py before DB write
24. **`WEATHER_SKIP_COORDINATOR_BUY=True`** — confirm_position() does direct INSERT
25. **trade_events JSONB column is `event_data`** — NOT `metadata_json`
26. **`_calibration_confidence()` returns 1.0 when `_station_n_resolved` is empty** — prevents false blocks pre-first-reload
27. **SAMOS fallback chain**: local EMOS -> SAMOS global -> raw global -> bias -> identity. Do NOT remove any layer.
28. **`weather_climatology` table**: Populated by `backfill_climatology.py`, NOT by the bot. Bot only reads.
29. **Open-Meteo rate limit**: ERA5 archive throttles at ~25 req/min. Backfill has 2s delays + skip-done. Running backfill during scan causes bot 429s (temporary).
30. **paper_trading.py rewrite (S115 cross-bot)**: Alpha decay, Kyle's lambda, fill probability all DELETED. Uses real L2 orderbook VWAP now. Do NOT re-add theoretical slippage.
31. **Severe weather halt is US-only**: `api.weather.gov` has no international coverage. International stations skip silently.
32. **`scan_start_mono` in event_data**: WeatherBot passes it. Now ignored by paper_trading (alpha decay deleted) but kept for logging. Do NOT remove.
33. **DRY scan template `_scan_psw_markets()`**: Shared by precip/snow/wind. Log tags use f-strings: `f"weatherbot_{market_type}_scan_done"`.
34. **`_get_min_edge()` quiet hours**: Uses `datetime.now(timezone.utc).hour`. Tests MUST patch `settings.WEATHER_QUIET_HOURS_END=0` or results vary by time of day.
35. **`_analyze_group()` bucket limit**: `break` after `_max_buckets` relies on edges being sorted by abs_edge from `compute_edges()`. If that sort order changes, best edges may be skipped.

---

## VERIFICATION COMMANDS

```bash
# WeatherBot scan health
journalctl -u polymarket-ai -f | grep weatherbot_scan_done

# S118 fix verification — position stacking (should be zero rows after fix takes effect)
sudo -u postgres psql -d polymarket -c "SELECT market_id, COUNT(*) FROM positions WHERE bot_id='WeatherBot' AND status='open' GROUP BY market_id HAVING COUNT(*) > 1;"

# S118 fix verification — NO price cap (should be zero >65c after deploy)
journalctl -u polymarket-ai --since '1 hour ago' | grep weatherbot_trade_signal | grep 'side=NO'

# S118 fix verification — quiet hours (should see higher min_edge during UTC 0-7)
journalctl -u polymarket-ai --since '1 hour ago' | grep 'weatherbot_raw_edges'

# SAMOS + calibration
journalctl -u polymarket-ai -f | grep "weatherbot_global_emos\|weatherbot_samos\|weatherbot_calibration_reloaded"

# Cold-start mitigation
journalctl -u polymarket-ai -f | grep "weatherbot_cold_start\|weatherbot_bootstrap"

# Severe weather halt
journalctl -u polymarket-ai -f | grep "weatherbot_severe_halt"

# Climatology coverage
sudo -u postgres psql -d polymarket -c "SELECT COUNT(DISTINCT station_id), COUNT(*) FROM weather_climatology;"

# Shadow fills (24h check)
sudo -u postgres psql -d polymarket -c "SELECT COUNT(*), AVG(book_walk_slippage) FROM shadow_fills WHERE bot_name='WeatherBot';"

# P&L
PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/bot_pnl.py WeatherBot 24

# Brier decomposition (edge decay investigation)
PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/weather_brier.py --hours 720

# Finish climatology backfill
PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/backfill_climatology.py
```

---

## SESSION HISTORY (WeatherBot only)

| Session | Date | Key Changes |
|---------|------|-------------|
| S92 | 03-15 | P1 jump detection, P2 NBM benchmark |
| S95 | 03-16 | 4 paper trading elevations |
| S97 | 03-16 | 3 stations, P&L breakdown script |
| S100 | 03-17 | Alpha decay, canary persistence, SSH timeouts, backoff Redis |
| S104 | 03-18 | Fill quality logging, exposure leak fix, daily counter, alpha decay BUY-only |
| S108 | 03-19 | Fill pipeline: taker 0.85, bestAsk pre-filter, volume passthrough, same-side dedup, ghost fix |
| S111 | 03-19 | Full self-review audit: 25 findings, 6 invalidated, 4 log-level fixes |
| S112 | 03-20 | METAR renorm guard, fallback DB lookup on cache miss |
| S113 | 03-21 | 2E/2B/2C bug fixes, 4 Chinese cities added |
| S114 | 03-21 | EMOS cold-start: spread gate, Buhlmann ramp, global EMOS, historical bootstrap |
| S115 | 03-21 | Fix 2D cap ordering, config extraction, Brier script, severe weather halt, DRY refactor, SAMOS ERA5 climatology |
| S116 | 03-22 | (BROKEN) YES gate, edge flip, boost cap — all reverted. Bot broke from API throttling, not code. |
| **S118** | **03-22** | **S117 diagnosis correction, edge cap removed, 5 structural P&L fixes, metadata backfill, env var wiring** |

---

## INVALIDATED FINDINGS (do NOT re-investigate)

1. ~~Precipitation engine not wired~~ — IS wired via `_scan_psw_markets()`
2. ~~Wind/snow trading disconnected~~ — Both wired via `_scan_psw_markets()`
3. ~~NaN/Inf ZeroDivisionError~~ — `probability_engine.py` guards `len(clean) < 2`
4. ~~Confidence formula inverted~~ — `1.0 - model_prob` IS correct for NO-side
5. ~~Race condition in concurrent `_analyze_group()`~~ — asyncio is single-threaded cooperative
6. ~~Model cache serves week-old data~~ — 30-min TTL prevents staleness
7. ~~SAMOS uses circular bootstrap climatology~~ — **FIXED S115**. 10-year ERA5 data.
8. ~~Baker-McHale loses granularity after cap~~ — **FIXED S115**. Cap moved to end.
9. ~~S116 code changes broke the bot~~ — **WRONG**. Changes were in dead code path (`analyze_opportunity` fallback). Real cause was Open-Meteo API throttling from climatology backfill.
10. ~~prediction_log was_correct = trade win rate~~ — **WRONG**. `was_correct` measures calibration accuracy (model closer to truth than market), not trade profitability. Do NOT use as win rate proxy.

---

## FUTURE ROADMAP

| Priority | Item | Description |
|----------|------|-------------|
| P0 | Deploy S118 fixes | 5 structural changes + edge decay investigation |
| P1 | YES confidence gate | Implement properly in `_analyze_group()` after edge decay investigation. Use `WEATHER_YES_MIN_CONFIDENCE` setting (already wired, default 0.0 = disabled) |
| P2 | Per-city min_edge adjustments | Miami/Dallas/London need higher edge floors based on loss data |
| P3 | Climate-Cluster Semi-Local EMOS | Cluster by quantile features (50+ stations) |
| P3 | Grouped Per-Model EMOS | Fit per-model coefficients (6 params) |
| P4 | Nearest-Station Transfer | Elevation-adjusted analog initialization |
| P4 | City Rotation Prediction | Auto-detect new Polymarket cities |
| P5 | MEMOS / Neural Network | Heavy ML (50+ stations, 6+ months) |
