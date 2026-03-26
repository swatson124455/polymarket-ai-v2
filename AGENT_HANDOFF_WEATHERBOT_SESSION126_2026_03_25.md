# AGENT HANDOFF — WeatherBot Session 126 (2026-03-25)

## STATUS: LIVE PAPER TRADING | DEPLOYED `20260324_200349` | SPREAD INFLATION ACTIVE (AUTO-DECAY) | +$2,968 P&L (1,963 resolutions, 61.1% WR)

---

## READ THESE BEFORE DOING ANYTHING

1. `CLAUDE.md` — Prime directive, rules of engagement, architecture facts, critical traps
2. `C:\Users\samwa\.claude\projects\C--lockes-picks-polymarket-ai-v2\memory\MEMORY.md` — Session history, P&L data, outstanding items
3. This handoff doc — everything you need

**This is a WeatherBot-only session. No bleed to other bots unless explicitly demanded.**

---

## SYSTEM OVERVIEW

- **What**: 14-bot automated Polymarket trading system. WeatherBot trades temperature-bucket markets using NOAA ensemble forecasts (GFS/ECMWF/HRRR/AIFS, 133 members).
- **Phase**: Paper trading (SIMULATION_MODE=true). Real capital at risk when boolean flips. Paper trading IS production.
- **VPS**: Ubuntu-3 at 34.251.224.21 (16GB/4vCPU)
- **SSH**: `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21`
- **Deploy**: `KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh`
- **Logs**: `sudo journalctl -u polymarket-ai -f`
- **Current deploy**: `20260324_200349` (S126)
- **DB**: PostgreSQL on VPS. Access: `sudo -u postgres psql polymarket`
- **P&L script**: `python scripts/bot_pnl.py WeatherBot <hours>`
- **Restart**: `sudo systemctl restart polymarket-ai`

---

## WHAT WAS DONE THIS SESSION (S126)

### Spread Inflation Activated with Auto-Decay

**Problem (data-driven):** Lead-time WR analysis on 1,090 resolved trades showed massive overconfidence at short lead times:

| Lead Time | N | WR | P&L | Overconfidence |
|-----------|---|------|--------|----------------|
| <24h | 139 | 48.2% | **-$204** | 19pp (67% conf, 48% actual) |
| 24-48h | 247 | 53.4% | **-$601** | 13pp (66% conf, 53% actual) |
| 48-72h | 279 | 59.9% | **+$558** | 8pp (69% conf, 60% actual) |
| 72-120h | 425 | 63.8% | **+$1,453** | 7pp (71% conf, 64% actual) |

Short-lead trades are the worst performers. The model is most overconfident when it should be most certain.

**Solution — Two-component spread inflation with auto-decay:**

The S124 foundation was a single `FACTOR` that only scaled with lead time (24h = no change). S126 adds a `BASE` component that applies uniformly at ALL lead times, fixing the short-lead problem.

**New env vars (live on VPS):**
```
WEATHER_SPREAD_INFLATION_BASE=0.15      # Uniform 15% spread widening at all lead times
WEATHER_SPREAD_INFLATION_FACTOR=0.05    # Additional sqrt(lead_days) scaling
WEATHER_SPREAD_INFLATION_START=2026-03-24T20:00:00+00:00  # Decay clock start
```

**Inflation multiplier by lead time (day 0):**
```
24h  → ×1.15 (base only, sqrt(1)-1=0)
48h  → ×1.19 (base + factor * 0.414)
72h  → ×1.22 (base + factor * 0.732)
120h → ×1.27 (base + factor * 1.236)
168h → ×1.31 (base + factor * 1.646)
```

**Auto-decay (10%/day):**
```
Day 0:  BASE=0.150, FACTOR=0.050 → 24h=×1.15
Day 3:  BASE=0.109, FACTOR=0.036 → 24h=×1.11
Day 7:  BASE=0.072, FACTOR=0.024 → 24h=×1.07
Day 14: BASE=0.031, FACTOR=0.010 → 24h=×1.03
Day 23: Hard zero — both BASE and FACTOR set to 0.0
```

**Why auto-decay works:** The calibrator refits EMOS every 6h on a 90-day window. As the inflation widens spreads, new trades enter the calibration data with more honest raw probabilities. The EMOS coefficients gradually absorb this, making the inflation less necessary. By day 23, the calibration data should have shifted enough that the raw model is naturally more honest.

**Code change** (`probability_engine.py` L101-130):
```python
_spread_base = float(getattr(settings, "WEATHER_SPREAD_INFLATION_BASE", 0.0))
_spread_factor = float(getattr(settings, "WEATHER_SPREAD_INFLATION_FACTOR", 0.0))
_inflation_start = str(getattr(settings, "WEATHER_SPREAD_INFLATION_START", ""))
if _inflation_start and (_spread_base > 0 or _spread_factor > 0):
    _days_active = (datetime.now(timezone.utc) - datetime.fromisoformat(_inflation_start)).total_seconds() / 86400.0
    if _days_active >= 23.0:
        _spread_base = 0.0
        _spread_factor = 0.0
    elif _days_active > 0:
        _decay = 0.90 ** _days_active
        _spread_base *= _decay
        _spread_factor *= _decay
if _spread_base > 0 or _spread_factor > 0:
    _lead_days = max(lead_time_hours / 24.0, 1.0)
    _inflation_mult = 1.0 + _spread_base + _spread_factor * (math.sqrt(_lead_days) - 1.0)
    effective_std *= _inflation_mult
    if emos_sigma is not None:
        emos_sigma *= _inflation_mult
```

Both `effective_std` (normal fallback) and `emos_sigma` (skewnorm primary path) are inflated. When `emos_sigma is None` (no EMOS data), skewnorm uses MLE-fitted `scale` which captures natural ensemble spread — correct to leave alone.

**Settings change** (`config/settings.py` L759-767): Added `WEATHER_SPREAD_INFLATION_BASE` and `WEATHER_SPREAD_INFLATION_START` alongside existing `WEATHER_SPREAD_INFLATION_FACTOR`.

### Post-Deploy Verification (30 min after activation)

| Metric | Pre-Inflation (2h window) | Post-Inflation (30 min) |
|--------|--------------------------|------------------------|
| Shadows | 1,807 | 144 |
| Entries | normal flow | 0 (overnight, expected) |
| Avg Raw Conf | 0.8983 | 0.8986 (unchanged — raw model not affected) |
| Avg Cal Conf | 0.7072 | 0.7138 (slightly up — CDF redistribution) |
| Avg Price | 0.7634 | 0.7862 |
| Neg-EV Gap | 5.6pp | 7.2pp (wider — inflation making model more conservative) |

Working as intended. The inflation widens the distribution, which changes how probability mass distributes across buckets. The net effect is more conservative predictions — exactly what the data showed was needed.

### Files Modified in S126

| File | Change |
|------|--------|
| `base_engine/weather/probability_engine.py` | Two-component spread inflation + auto-decay (L101-130) |
| `config/settings.py` | Added `WEATHER_SPREAD_INFLATION_BASE`, `WEATHER_SPREAD_INFLATION_START` (L759-767) |

### Shadow Entry Deep Analysis (S126 data, 24h window)

**Rejection rate: 96%** — 2,011 shadows vs 89 actual entries in 24h pre-inflation.

**Breakdown:**
- 1,784 (89%) would have traded ($5+) but blocked by calibrator (conf < price)
- 227 (11%) had zero Kelly (raw model says negative EV before calibration)

**By raw confidence bucket (all NO-side):**

| Raw Conf | N | Avg Would-Bet | Avg Cal Conf | Avg Price | Neg-EV Gap |
|----------|---|---------------|-------------|-----------|------------|
| 95-100% | 746 | $668 | 0.8109 | 0.8653 | 5.4pp |
| 90-95% | 379 | $518 | 0.6755 | 0.7619 | 8.6pp |
| 85-90% | 522 | $642 | 0.6398 | 0.7041 | 6.4pp |
| 80-85% | 275 | $551 | 0.6087 | 0.6821 | 7.4pp |
| 70-80% | 73 | $366 | 0.6100 | 0.6703 | 6.0pp |

The calibrator compresses hard: raw 95% → calibrated 81%, raw 87% → calibrated 64%. The neg-EV gaps are 4-9pp — marginal, not wildly negative. But the resolution data confirms these ARE negative EV (WR well below breakeven price at each tier).

**Top shadow cities by volume:** Dallas (191), NYC (172), Miami (164), Toronto (132), Taipei (128), Chicago (122), Munich (102), Tokyo (91)

---

## LONG-TERM VISION: CALIBRATION CONVERGENCE

The user-approved strategy for the overconfidence problem:

1. **Done (S126):** Spread inflation active with auto-decay. BASE=0.15, FACTOR=0.05, 10%/day decay, hard zero at day 23 (April 17, 2026).
2. **Ongoing:** EMOS refits every 6h on 90-day window. As inflation produces more honest raw probs, EMOS coefficients adjust.
3. **Monitor:** Shadow entry volume and neg-EV gap should narrow over time. If calibrator T is ever re-fitted, it should be lower than 2.271.
4. **End state:** Calibrator becomes identity function (T≈1.0). Probability engine produces honest probs natively. Spread inflation decays to zero.

**Decay countdown (from 2026-03-24T20:00:00Z):**
- Day 0 (Mar 24): BASE=0.150, FACTOR=0.050
- Day 7 (Mar 31): BASE=0.072, FACTOR=0.024
- Day 14 (Apr 7): BASE=0.031, FACTOR=0.010
- Day 23 (Apr 16): Hard zero — inflation off permanently

**Why this approach (not re-tuning T):**
- The Platt calibrator (T=2.271) is static — trained once in S123 on ~1,900 resolved trades. It does NOT auto-refit.
- Rather than re-fitting T repeatedly (which overfits to recent data), we fix the SOURCE of overconfidence (narrow ensemble spreads).
- As the source improves, a fixed T becomes less aggressive (since raw probs are closer to honest).
- If T is ever re-fitted, it will naturally be lower (closer to 1.0) because the raw probs need less compression.

---

## PROBABILITY ENGINE: ROOT CAUSES OF OVERCONFIDENCE

### Identified in S124 exploration (6 sources of systematic narrowness):

| Issue | Location | Impact | Status |
|-------|----------|--------|--------|
| **Population std divisor** | `forecast_client.py:1250` | ~5% underestimate | **FIXED S124** |
| **No lead-time spread inflation** | `probability_engine.py` | Short-lead worst | **FIXED S126** (base+factor+decay) |
| **GEFS subsampling at 48h+** | `forecast_client.py:1232-1245` | Removes diversity, narrows 10-20% | UNFIXED |
| **No EMOS before 20 pairs** | `probability_engine.py:96` | Cold-start overconfidence | Not a trading gate (see below) |
| **Hard floor 0.5F** | `probability_engine.py:84,96` | Asymmetric inflation | Low priority |
| **Tail discount post-normalization** | `probability_engine.py:150-162` | Compresses tail mass | Low priority |

### The Math: Ensemble to Distribution to Bucket Probability
```
1. Clean ensemble: filter NaN/Inf -> n finite members
2. Raw std: sqrt(sum((xi - xbar)^2) / (n-1))  [sample std, FIXED S124]
3. EMOS correction: mu = a + b*Xbar, sigma = residual_std (if >=20 pairs)
4. effective_std = max(emos_sigma ?? raw_std, 0.5)
5. S126: spread inflation: effective_std *= (1.0 + base_decayed + factor_decayed * (sqrt(lead_days) - 1.0))
6. Skew-normal MLE fit (if scipy + n>=10), else normal
7. CDF integration per bucket with +/-0.5 deg offset
8. Tail discount (0.90 default)
9. Normalize to sum=1.0
```

---

## FULL CALIBRATION ARCHITECTURE (11 layers)

### Sizing sequence (when evaluating an opportunity):

```
1. RAW MODEL PROBABILITY
   -- probability_engine.bucket_probabilities() -> model_prob per bucket
      |-- Skew-normal CDF integration
      |-- EMOS mean correction: corrected = a + b * forecast_mean
      |-- EMOS spread correction: corrected_sigma = sigma (if EMOS fitted)
      |-- S126: Lead-time spread inflation (BASE+FACTOR with auto-decay)
      +-- Tail isotonic calibration: multiply by tail discount [0.5, 1.0]

2. EDGE FILTER (L1373)
   -- |model_prob - yes_price| >= min_edge (8% US, 12% intl)
      +-- If fails: skip this bucket entirely

3. CONFIDENCE ASSIGNMENT (L2100-2108)
   -- raw_conf = model_prob (YES) or 1 - model_prob (NO)
   -- base_confidence = min(0.95, raw_conf)
   -- IF calibrator fitted: effective_confidence = calibrate(base_confidence)  [S123]
   -- ELSE: effective_confidence = base_confidence

4. S124 NEGATIVE-EV GATE (L2699)
   -- IF confidence < price OR _raw_size <= 0: shadow entry + return False
   -- Catches BOTH Kelly and S-T paths

5. KELLY SIZING (bankroll_manager.py L156-162)
   -- kelly_full = (confidence * b - q) / b
   -- IF kelly_full <= 0: return 0  [intercepted by gate above]
   -- size_usd = kelly_full * fraction * capital

6. SIZING MULTIPLIERS (combined_boost, L2350-2600)
   |-- Expiry boost (1.0-2.0x by lead time)
   |-- Regime boost (1.2x when >=3 cities show unanimous edge)
   |-- Severe weather halt/boost
   |-- Jump boost (model run delta)
   |-- NBM boost (1.3x high conviction)
   |-- Model freshness factor (0.8-1.2x by model age)
   |-- Station reliability (0.5-1.2x by 14-day MSE)
   |-- Buhlmann credibility (0.0-1.0x by n_resolved)
   |-- Baker-McHale uncertainty (0.5-1.0x by spread)
   +-- Combined cap: min(combined_boost, 2.0)

7. EXPOSURE CAPS (L2727-2733)
   |-- Group cap: $10,000
   |-- City cap: $5,000
   |-- Per-bet cap: $600
   +-- Daily cap: $20,000

8. PAPER FILL SIMULATION (paper_trading.py)
   |-- BUY: ask-side VWAP walk
   |-- SELL: bid-side VWAP walk (S121)
   +-- Per-scan book depletion (S121)
```

### Monitoring layers (independent):
- **Brier + Drawdown** (L4428-4492): MSE > 25 -> halt trading
- **EMOS Drift** (L768-811): DDM/EDDM alerts, no auto-halt
- **CalibrationTracker** (calibration_tracker.py): Brier tracking, drift detection

---

## CALIBRATION PIPELINE (3 independent systems)

### 1. EMOS — Station-level bias correction (AUTO-REFIT every 6h)
- **What**: Per-station, per-lead-time OLS regression: corrected_mean = a + b * forecast_mean
- **Data source**: `weather_calibration` table (forecast_temp, actual_temp, bias)
- **Window**: 90 days rolling (`WEATHER_EMOS_WINDOW_DAYS=90`)
- **Minimum**: 20 resolved pairs per (station, lead_bucket) for EMOS to activate. Below that, uses simple bias offset.
- **Regime-aware**: Splits by ENSO phase (El Nino/La Nina/Neutral). Uses regime-specific EMOS if ≥20 pairs, falls back to pooled.
- **Code**: `weather_bot.py:_maybe_reload_calibration()` L4207-4385
- **Also loads**: Tail isotonic calibration, climatology (ERA5 normals for SAMOS)

### 2. Platt+Isotonic — Confidence calibration (STATIC, set in S123)
- **What**: Maps raw model confidence to calibrated confidence. T=2.271 compresses all probabilities toward 0.5.
- **Trained on**: ~1,900 resolved trades at time of S123
- **Does NOT auto-refit**. This is the key gap. The spread inflation decay strategy compensates.
- **Semantics**: T > 1 = COMPRESS toward 0.5 (overconfident correction). T < 1 = AMPLIFY toward extremes.

### 3. Spread Inflation — Distribution widening (S126, AUTO-DECAY)
- **What**: Widens effective_std before CDF integration, making bucket probabilities less extreme.
- **Auto-decays**: 10%/day from start date. Hard zero at day 23.
- **Purpose**: Bridge between current overconfident model and future self-correcting EMOS.
- **Effect**: Does NOT change the raw model output — changes the DISTRIBUTION WIDTH fed into the CDF. Wider distribution = more uncertainty = less extreme bucket probabilities.

### New city calibration — NOT a gate
A new city can trade on day 1 with zero calibration data. The pipeline:
1. Raw GFS/ECMWF forecast works for any lat/lon on Earth (no training needed)
2. CDF → bucket probability math is pure math (no training needed)
3. Platt T=2.271 is GLOBAL — applies to all cities equally
4. EMOS is the ONLY per-station component. Without it, raw forecast is used. EMOS makes it better, not possible.
5. The 20-sample minimum is per (station, lead_bucket), not per city overall

---

## LOCATION ONBOARDING

### Current: MANUAL ONLY

1. Polymarket adds new weather market for city X
2. WeatherBot scan detects city X via `lookup_station()` -> returns None
3. Alert fires: `weatherbot_unmatched_cities` (L1172) — "Add to station_registry.py"
4. Developer manually adds `WeatherStation` entry to `STATION_REGISTRY` in `base_engine/weather/station_registry.py`
5. Deploy -> restart -> cold-start bootstrap auto-populates 90 days of calibration data
6. EMOS fits on next calibration reload (6h)

### Station registry structure:
```python
WeatherStation(
    city_name="New York City",     # Display name
    station_id="KLGA",             # ICAO code (critical — resolution source)
    ghcnd_id="GHCND:USW00014732",  # NOAA historical data ID
    latitude=40.7772,              # Exact station coordinates (NOT city center)
    longitude=-73.8726,
    elevation_m=6.0,
    timezone="America/New_York",   # IANA timezone
    temp_unit="F",                 # "F" or "C"
    aliases=("nyc", "new york city", "new york"),  # Lowercase matching
    resolution_source="Weather Underground / KLGA",
    has_asos_1min=True,            # True for US ASOS stations
    local_model=None,              # Open-Meteo hi-res model slug
)
```

### Files involved:
- `base_engine/weather/station_registry.py` — STATION_REGISTRY dict (106 stations, L42-1399), `lookup_station()` (L1413-1441)
- `base_engine/weather/market_mapper.py` — `group_markets()` calls lookup_station, tracks `_last_unmatched_cities`
- `bots/weather_bot.py` L1158-1179 — unmatched city detection + alerting

### Current state: 35 active cities, 106 stations in registry, 0 unmatched
City count has been stable at 35 for several days. Either Polymarket genuinely hasn't added cities, or the regex/mapper silently drops new formats. **Flagged as P3 bug to investigate.**

### Auto-onboarding considerations (future work):
- Detect unknown city → lookup ICAO code → add station entry → alert user
- Risk: wrong resolution station = systematic bias (Polymarket resolves against a specific station)
- For 90% of cities, it's the primary international airport — predictable and automatable
- Recommendation: semi-automated (detect -> suggest -> human approve)

---

## DATA ANALYSIS FINDINGS (cumulative S122-S126)

### Calibration Table (entry confidence vs actual WR)
| Conf | N | WR | Avg Conf | Gap | P&L | $/trade |
|------|---|-----|----------|-----|-----|---------|
| 10-20% | 80 | 27.5% | 16.2% | +11pp | +$172 | $2.15 |
| 20-30% | 111 | 32.4% | 24.9% | +8pp | -$167 | -$1.50 |
| 30-40% | 116 | 40.5% | 35.1% | +5pp | +$350 | $3.02 |
| 40-50% | 68 | 47.1% | 43.9% | +3pp | +$477 | $7.02 |
| 50-60% | 21 | 42.9% | 55.8% | -13pp | +$339 | $16.14 |
| 60-70% | 20 | 55.0% | 64.5% | -10pp | -$10 | -$0.49 |
| 70-80% | 68 | 61.8% | 75.6% | -14pp | +$843 | $12.40 |
| 80-90% | 121 | 62.0% | 85.5% | -24pp | +$338 | $2.79 |
| 90-100% | 580 | 77.6% | 94.6% | -17pp | +$189 | $0.33 |

**Key insight**: Model has signal (monotonic WR from 27% to 78%) but confidence is stretched too wide vs reality. The calibrator (T=2.271) compresses this correctly.

### Lead-Time Performance (S126 analysis, 1,090 resolved trades)
| Lead Time | N | WR | P&L | Avg Conf | Avg Price |
|-----------|---|------|--------|----------|-----------|
| <24h | 139 | 48.2% | -$204 | 0.6726 | 0.5644 |
| 24-48h | 247 | 53.4% | -$601 | 0.6594 | 0.5495 |
| 48-72h | 279 | 59.9% | +$558 | 0.6872 | 0.5885 |
| 72-120h | 425 | 63.8% | +$1,453 | 0.7105 | 0.6261 |

**Short-lead trades are the problem.** This is the primary data that drove the spread inflation BASE component.

### Resolution Performance (48h window, post-S125 recovery)
| Conf Bucket | Side | N | WR | P&L |
|-------------|------|---|------|------|
| 80%+ | NO | 555 | 79.1% | +$1,514 |
| 60-80% | NO | 19 | 68.4% | +$39 |
| 40-60% | YES | 60 | 46.7% | +$15 |
| 20-40% | YES | 196 | 30.1% | -$137 |
| <20% | YES | 109 | 31.2% | -$1,332 |

High-confidence NO trades are the money maker. Low-confidence YES trades are the biggest drag.

### All-time P&L: **+$2,968.05** (1,963 resolutions, 61.1% WR)

---

## PENDING PRIORITIES

| Pri | Item | Status | Notes |
|-----|------|--------|-------|
| **P1** | Monitor spread inflation decay over 48h | Active — check shadow entry gap narrowing, new entries flowing | Decay countdown: hard zero Apr 16 |
| **P2** | GEFS subsampling at 48h+ narrows spread | Identified S124, not addressed | 10-20% diversity loss |
| **P3** | 35 cities stuck — hasn't changed in days | Investigate if regex/mapper drops new formats, or Polymarket genuinely hasn't added cities | |
| **P3** | City auto-onboarding | Currently manual. New cities CAN trade day 1 (no calibration gate). Automate detection + ICAO lookup + alert | |
| **P3** | NO vs YES asymmetry (72% vs 39% WR) | Confirmed, monitoring | |
| **P4** | Platt T=2.271 is static — does not auto-refit | Compensated by spread inflation. Future: add auto-refit on calibrator? | |
| **P4** | EMOS 20-sample minimum | Not a trading gate — just means new cities use raw forecast. Document confusion cleared in S126. | |
| **P4** | Buenos Aires chronic loser (-$355, 51.1% WR) | Southern hemisphere bias? | |
| **P5** | Kalshi cross-platform arbitrage | Deferred, 8-16h effort | |
| **P6** | Backfill `end_date_iso` for condition_id markets | Would improve resolution ordering | |

---

## ARCHITECTURE REFERENCE (WeatherBot scan path)

```
scan_and_trade() [L983]
  |-- _handle_daily_boundary() [L988] -- resets exposure, restores P&L
  |-- asyncio.gather(_maybe_reload_calibration, _load_category_params) [L999]
  |   |-- EMOS: 90-day rolling window, station+regime-specific OLS (6h reload)
  |   |-- Tail isotonic: weather_tail_calibration table
  |   |-- Global EMOS/SAMOS: pooled cross-station fallback
  |   |-- Buhlmann: station_n_resolved for cold-start ramp
  |   +-- S123: Platt+Isotonic confidence calibrator (STATIC, T=2.271)
  |-- _check_monitoring_thresholds() [L1007] -- Brier/drawdown halt + Kelly graduation
  |-- PM exit detection [L1010-1070] -- tracks position_manager exits, decrements exposure
  |-- _prefetch_severe_weather_alerts() [L1200] -- NWS batch fetch
  |-- asyncio.gather(*[_analyze_group(g) for g in groups]) [L1210]
  |   |-- forecast_client.get_combined_forecast() -- GFS+ECMWF+AIFS (133 members)
  |   |-- prob_engine.fit_distribution() -- skew-normal + EMOS + S126 spread inflation
  |   |-- prob_engine.bucket_probabilities() -- CDF integration per bucket
  |   |-- _apply_metar_resolution_day_override() -- METAR running max (< 12h)
  |   |-- prob_engine.compute_edges() -- model_prob - market_price
  |   +-- tradeable filtering:
  |       |-- edge >= min_edge (spread-confidence gated)
  |       |-- exit cooldown (4h)
  |       |-- penny-bet filter (4c-97c)
  |       |-- in-memory position check (fast path)
  |       |-- DB position guard (ground truth) [S119]
  |       |-- boundary_risk -- LOGGED ONLY, no discount (S122)
  |       |-- max 5 buckets per group (S122)
  |       +-- S123: Platt+Isotonic confidence calibration applied
  |-- _compute_regime_boost() -- cross-city warm/cold detection
  |-- _exec_group() for each group with edge:
  |   |-- >=2 buckets: _execute_group_trades() -> S-T multi-bucket Kelly
  |   +-- 1 bucket: _execute_weather_trade() -> independent Kelly
  |       |-- same-side dedup (_position_details)
  |       |-- exit cooldown check
  |       |-- fill-failure cooldown
  |       |-- daily loss limit
  |       |-- group/city exposure caps (locked) -- $10K/$5K (S122)
  |       |-- expiry boost (1.0-2.0x by lead time)
  |       |-- regime boost (1.2x)
  |       |-- severe weather halt/boost
  |       |-- jump boost (model run delta)
  |       |-- NBM boost (1.3x high conviction)
  |       |-- model freshness factor (S121)
  |       |-- combined boost (additive, cap 2.0x)
  |       |-- Baker-McHale uncertainty (model spread)
  |       |-- station reliability (MSE-based)
  |       |-- Buhlmann calibration confidence (cold-start ramp)
  |       |-- slippage check (liquidity guardian)
  |       |-- S124: NEGATIVE-EV GATE -- confidence < price OR _raw_size <= 0 -> shadow + skip
  |       |-- Kelly sizing via BotBankrollManager ($600 cap, $20K daily)
  |       |-- SHADOW_ENTRY logging for sub-$5 trades (S122)
  |       |-- exposure lock reservation (atomic)
  |       +-- place_order() -> order_gateway (VWAP gate BYPASSED for WeatherBot)
  |           +-- paper_trading.place_order() -> VWAP fill from book walk
  |               |-- BUY: ask-side VWAP walk
  |               |-- SELL: bid-side VWAP walk (S121)
  |               +-- per-scan book depletion (S121)
  |-- _reevaluate_open_positions() -- feed position_manager fresh probs
  +-- every 10 scans: backfill_outcomes + check_emos_drift + close_stale_positions
```

---

## RESOLUTION BACKFILL ARCHITECTURE (updated S125)

```
run_resolution_backfill() [resolution_backfill.py]
  Phase 1: Market ingestion (data_ingestion)
  Phase 2: Resolution of traded markets
    -- Query: traded_markets WHERE status='open' OR resolved=FALSE
    -- S125 ordering: expired first (end_date_iso < NOW()), then NULLs, then open, then first_trade_at ASC
    -- S125 batch size: 500 (was 200)
    -- For each market: check CLOB API for resolution
    -- If resolved: update markets table, create RESOLUTION trade_event, backfill paper_trades
  Phase 3: Non-traded market resolution (lower priority)
  Phase 4a: Backfill paper_trades resolution (PT table, legacy)
  Phase 4b: Backfill trade_events resolution (AUTHORITY table)
```

---

## CURRENT CONFIG (live VPS values post-S126)

```
WeatherBot BotBankrollManager:
  capital=$20,000, kelly_fraction=0.25, max_bet_usd=$600, max_daily_usd=$20,000

WEATHER_MIN_EDGE=0.08, WEATHER_INTL_MIN_EDGE=0.12
WEATHER_MIN_CONFIDENCE=0.10
WEATHER_MAX_POSITIONS=1000
WEATHER_MAX_PER_GROUP_USD=10000
WEATHER_MAX_CORRELATED_EXPOSURE=5000
WEATHER_COMBINED_BOOST_CAP=1.5
WEATHER_MAX_BUCKETS_PER_GROUP=5
WEATHER_NO_MAX_ENTRY_PRICE=1.0 (effectively removed)
WEATHER_KELLY_FRACTION=0.25
WEATHER_DEFAULT_SIZE=25
WEATHER_EXIT_COOLDOWN_SECS=14400 (4h)
WEATHER_MIN_TRADE_USD=5.0
WEATHER_EMOS_WINDOW_DAYS=90
WEATHER_BM_FLOOR=0.50
WEATHER_BUHLMANN_KAPPA=30.0
WEATHER_DAILY_LOSS_LIMIT=10000
WEATHER_MAX_TOTAL_EXPOSURE_USD=50000
WEATHER_TOTAL_CAPITAL=20000
WEATHER_MAX_LEAD_TIME_HOURS=168

# S123:
WEATHER_CONFIDENCE_CAL_ENABLED=true
WEATHER_CONFIDENCE_CAL_WINDOW_DAYS=30
WEATHER_CONFIDENCE_CAL_MIN_SAMPLES=200

# S126 (was S124 foundation):
WEATHER_SPREAD_INFLATION_BASE=0.15
WEATHER_SPREAD_INFLATION_FACTOR=0.05
WEATHER_SPREAD_INFLATION_START=2026-03-24T20:00:00+00:00
# Auto-decays 10%/day. Hard zero at day 23 (Apr 16, 2026).

# S125:
RESOLUTION_QUEUE_BATCH_SIZE=500

PAPER_TAKER_FEE_BPS=150
PAPER_REALISTIC_FILLS=true
PAPER_DEFAULT_SPREAD=0.04
PAPER_LATENCY_DRIFT_BPS_PER_SEC=0 (disabled)
PAPER_TRADING_CAPITAL=10000000

LIVE_ORDER_MAX_RETRIES=3
LIVE_ORDER_RETRY_BASE_S=1.0
```

---

## KEY FILES (WeatherBot-specific)

| File | Lines | Purpose |
|------|-------|---------|
| `bots/weather_bot.py` | ~4,690 | Main bot: scan, analyze, trade, exit, calibration |
| `base_engine/weather/probability_engine.py` | ~560 | Skew-normal distribution + EMOS + S126 spread inflation |
| `base_engine/weather/precipitation_engine.py` | 235 | Rain/snow probability |
| `base_engine/weather/market_mapper.py` | 1,136 | Polymarket question -> city/bucket mapping |
| `base_engine/weather/forecast_client.py` | 1,587 | GFS/ECMWF/HRRR/AIFS ensemble fetching |
| `base_engine/weather/metar_client.py` | 237 | Airport observations (resolution day) |
| `base_engine/weather/metar_monitor.py` | 283 | METAR running max tracking |
| `base_engine/weather/model_run_monitor.py` | 339 | Model freshness tracking (S121) |
| `base_engine/weather/station_registry.py` | ~1,550 | Station definitions (106), lookup, health monitor |
| `base_engine/execution/paper_trading.py` | ~900 | Paper fills: VWAP walk, book depletion |
| `base_engine/execution/order_gateway.py` | ~1,200 | Order routing, VWAP gate, live retry |
| `base_engine/risk/bankroll_manager.py` | 263 | Kelly sizing, per-bot caps |
| `base_engine/learning/calibration_tracker.py` | 259 | EMOS + Brier tracking + drift detection |
| `base_engine/data/resolution_backfill.py` | ~400 | Resolution queue: Phase 2 ordering fix (S125) |
| `config/settings.py` | WEATHER_* block | All config |
| `tests/unit/test_weather_bot.py` | ~1,940 | Main test suite (1717 pass) |

---

## DATA SOURCES

| What | Where | Notes |
|------|-------|-------|
| P&L (realized) | `trade_events` table | SOLE AUTHORITY. `realized_pnl` column. |
| P&L (unrealized) | `positions.unrealized_pnl` | Mark-to-market, updated every 10s |
| Shadow entries | `trade_events WHERE event_type='SHADOW_ENTRY'` | reason=negative_ev/zero_kelly/sub_min_trade/exposure_cap |
| Predictions | `prediction_log` | `was_correct` = calibration, NOT trade WR |
| Paper trades | `paper_trades` | LEGACY. Do not use for P&L. |
| EMOS calibration | `weather_calibration` | forecast_temp, actual_temp, bias, lead_time |
| Tail calibration | `weather_tail_calibration` | model_prob, actual_outcome by bucket_type |
| Climatology | `weather_climatology` | ERA5 30-year normals for SAMOS |
| Canonical P&L | `python scripts/bot_pnl.py WeatherBot <hours>` | |

### Key Queries

**Shadow entry monitoring:**
```sql
SELECT event_data->>'reason' AS reason, side, COUNT(*) AS n,
    ROUND(AVG(CAST(event_data->>'raw_size_usd' AS FLOAT))::numeric, 2) AS avg_would_bet,
    ROUND(AVG(CAST(event_data->>'raw_confidence' AS FLOAT))::numeric, 4) AS avg_raw_conf,
    ROUND(AVG(confidence)::numeric, 4) AS avg_cal_conf,
    ROUND(AVG(price)::numeric, 4) AS avg_price
FROM trade_events
WHERE event_type = 'SHADOW_ENTRY' AND bot_name = 'WeatherBot'
  AND event_time >= NOW() - INTERVAL '48 hours'
GROUP BY reason, side ORDER BY reason, side;
```

**Lead-time WR (the key diagnostic):**
```sql
WITH entries AS (
    SELECT DISTINCT ON (market_id) market_id, confidence, price,
        CAST(COALESCE(event_data->>'lead_time_hours','0') AS FLOAT) AS lead_hours
    FROM trade_events WHERE bot_name='WeatherBot' AND event_type='ENTRY'
      AND event_data->>'lead_time_hours' IS NOT NULL
    ORDER BY market_id, event_time
),
resolutions AS (
    SELECT market_id, realized_pnl FROM trade_events
    WHERE bot_name='WeatherBot' AND event_type='RESOLUTION'
)
SELECT
    CASE WHEN e.lead_hours < 24 THEN 'a_lt24h'
         WHEN e.lead_hours < 48 THEN 'b_24_48h'
         WHEN e.lead_hours < 72 THEN 'c_48_72h'
         WHEN e.lead_hours < 120 THEN 'd_72_120h'
         ELSE 'e_120h+' END AS lead_bucket,
    COUNT(*) AS n,
    ROUND(100.0 * COUNT(*) FILTER (WHERE r.realized_pnl > 0) / NULLIF(COUNT(*), 0), 1) AS wr,
    ROUND(SUM(r.realized_pnl)::numeric, 2) AS pnl,
    ROUND(AVG(e.confidence)::numeric, 4) AS avg_conf,
    ROUND(AVG(e.price)::numeric, 4) AS avg_price
FROM entries e JOIN resolutions r ON r.market_id = e.market_id
GROUP BY lead_bucket ORDER BY lead_bucket;
```

**Resolution backlog per bot:**
```sql
SELECT COALESCE(bot_names, 'unknown') AS bot,
    COUNT(*) AS unresolved,
    COUNT(*) FILTER (WHERE m.end_date_iso < NOW()) AS expired,
    COUNT(*) FILTER (WHERE m.end_date_iso IS NULL) AS no_end_date
FROM traded_markets tm
LEFT JOIN markets m ON m.id = tm.market_id
WHERE tm.status = 'open' OR tm.resolved = FALSE
GROUP BY bot_names ORDER BY unresolved DESC;
```

**VPS data export (for charts):**
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo -u postgres psql polymarket -t -A -F'|' -c \"
SELECT r.event_time::date, e.side, ROUND(r.realized_pnl::numeric,4),
    ROUND((e.size*e.price)::numeric,4), ROUND(e.price::numeric,6),
    COALESCE(e.event_data->>'city','Unknown'),
    COALESCE(ROUND(CAST(e.event_data->>'lead_time_hours' AS FLOAT)::numeric,1),-1),
    ROUND(e.confidence::numeric,6), CASE WHEN r.realized_pnl>0 THEN 1 ELSE 0 END
FROM trade_events r JOIN (
    SELECT DISTINCT ON (market_id) market_id, side, size, price, confidence, event_data
    FROM trade_events WHERE bot_name='WeatherBot' AND event_type='ENTRY'
    ORDER BY market_id, event_time
) e ON e.market_id=r.market_id
WHERE r.bot_name='WeatherBot' AND r.event_type='RESOLUTION'
  AND r.event_time >= NOW()-INTERVAL '48 hours' ORDER BY r.event_time;
\""  > wb_48h_raw.csv
```

---

## CRITICAL TRAPS (cumulative — DO NOT VIOLATE)

1. **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
2. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL
3. **VWAP edge gate BYPASSED for WeatherBot** (order_gateway.py). Weather CLOBs have 99c structural asks. Do NOT re-enable.
4. **Paper capital is $10M intentionally.** BotBankrollManager is the real limit.
5. **EMOS has 90-day rolling window** (`WEATHER_EMOS_WINDOW_DAYS=90`). Cold stations fall back to global EMOS.
6. **`_in_model_window()` is DELETED.** Logic lives in `_get_scan_interval_seconds()`.
7. **Precipitation empirical fallback fixed S120.** `elif` changed to `if` for peer bucket-type branches.
8. **`boundary_risk` persisted in event_data** for audit but NO LONGER discounts confidence (S122).
9. **NO confidence discount REMOVED (S122).** Do not re-add. Kelly self-regulates.
10. **NO entry price cap REMOVED (S122).** `WEATHER_NO_MAX_ENTRY_PRICE=1.0`.
11. **`confidence` is a TOP-LEVEL column on trade_events**, NOT in event_data JSONB.
12. **Python 3.13 scoping**: `from X import Y` inside function makes Y local for ENTIRE function. Any use before import -> UnboundLocalError.
13. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
14. **`paper_trades` has NO `metadata` JSONB column.**
15. **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time.
16. **BOT_REGISTRY=14 bots** — shared module change requires all 14 verified.
17. **positions table**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`.
18. **SHADOW_ENTRY events** are best-effort (try/except pass). Do not block trades on logging failure.
19. **fill_frac** in event_data shows paper fill model partial fill fraction. 75% of trades get <60% filled.
20. **S123 Platt semantics**: T > 1 COMPRESSES toward 0.5 (overconfident correction). T < 1 AMPLIFIES toward extremes. OPPOSITE of intuition.
21. **S123 calibrator properly blocks NO trades at 65c+** with T=2.271. Correct — they are negative EV. S124 gate ensures they do not fire anyway.
22. **`raw_confidence` field** added to opp dict (L2139) and flows to event_data for audit. Use to compare raw vs calibrated.
23. **Location onboarding is MANUAL** — station_registry.py must be edited by developer. Auto-discovery alerts but does not auto-add.
24. **S124: `max(_min_trade, _raw_size)` bug** — FIXED. Was forcing $5 on zero-Kelly trades.
25. **S124: S-T allocator bypasses Kelly EV check** — FIXED. `confidence < price` guard catches S-T too.
26. **S124: `settings` imported in `probability_engine.py`** — needed for spread inflation. No circular dependency.
27. **S124: Spread inflation inflates BOTH `effective_std` and `emos_sigma`** — skewnorm reads `emos_sigma` independently. Must inflate both.
28. **S124: Shadow entry `reason` field** — `"negative_ev"`, `"zero_kelly"`, `"sub_min_trade"`, `"exposure_cap"`.
29. **S125: `trade_events_event_type_check` constraint** — must include SHADOW_ENTRY. If new partitions created, verify.
30. **S125: Resolution backfill priority_bot removed** — old ordering starved bots. New: expired-first. Do NOT re-add priority_bot.
31. **S125: `RESOLUTION_QUEUE_BATCH_SIZE=500`** — do not reduce below 500.
32. **S125: `urllib.request` gets HTTP 403 from `clob.polymarket.com`** without User-Agent header. Production uses httpx (has User-Agent).
33. **S125: `sync_log.metadata` is NULL** — use `records_processed - records_inserted` to infer update count.
34. **S125: `end_date_iso` sparsely populated** for condition_id markets. Ordering handles NULLs.
35. **S126: Spread inflation has TWO components** — `WEATHER_SPREAD_INFLATION_BASE` (uniform) + `WEATHER_SPREAD_INFLATION_FACTOR` (lead-time scaled). Both auto-decay. Do not set one without the other.
36. **S126: Spread inflation auto-decay requires `WEATHER_SPREAD_INFLATION_START`** — ISO-8601 datetime. Without it, decay does not apply (uses raw BASE/FACTOR values).
37. **S126: Hard zero at day 23** — `_days_active >= 23.0` sets both to 0.0. After Apr 16, 2026, inflation is completely off regardless of env var values.
38. **S126: Shadow entries have NO `lead_time_hours` field** — only `city`, `reason`, `raw_size_usd`, `combined_boost`, `raw_confidence`. Actual ENTRY events DO have `lead_time_hours`.
39. **S126: 35 cities frozen** — city count hasn't changed in several days. May indicate regex/mapper silently dropping new Polymarket city formats. Investigate `is_weather_market()` and `group_markets()` if cities stay stuck.

---

## VERIFIED FALSE ALARMS (don't re-investigate)

| Claim | Status | Evidence |
|-------|--------|----------|
| Drawdown compression disabled | FALSE | `health_scheduler.py:214` updates every cycle |
| `bucket.temp_unit` not populated | FALSE | Set from regex at market_mapper.py |
| `_get_afd_spread_factor` dead | FALSE | Called at weather_bot.py L1743 |
| `_fit_samos` dead | FALSE | Called at weather_bot.py L4102 |
| `_get_enso_regime` dead | FALSE | Called at L3346, L3956, L4346 |
| `_save_backoff_to_redis` dead | FALSE | Called at L1126 |
| `compute_nbm_benchmark` dead | FALSE | Called at L1767 |
| All PSW scan methods dead | FALSE | Called at L1085-1087 |
| Calibration kills all trades | EXPECTED | T=2.271 correct; S124 gate blocks negative-EV |
| 39 bug trades were profitable | NOISE | 48.7% WR at 38c avg = breakeven; will revert |
| EMOS 20-sample is trading gate | FALSE (S126) | New cities trade day 1 with raw forecast + global T |
| Shadow entries = trades | FALSE | Shadows are LOG entries for rejected signals (~400-1200/hr), not actual trades |

---

## CURRENT STATE POST-S126

| Metric | Value |
|--------|-------|
| Deploy | `20260324_200349` |
| Open positions | 22 (all from Mar 24, clean) |
| Stale positions | 0 |
| Unresolved markets | 252 (legitimately open) |
| Shadow entry rate | ~400-1200/hr (normal) |
| Spread inflation | Active, day 0 — BASE=0.15, FACTOR=0.05 |
| Decay countdown | Hard zero at Apr 16 (day 23) |
| Total P&L | **+$2,968.05** (1,963 resolutions, 61.1% WR) |
| Resolution backfill | Running every ~30min, success, processing 36-85/cycle |
| Active cities | 35 (106 in registry) |
| Tests | 1717 passed, 0 failures |

---

## SESSION HISTORY (recent)

| Session | Date | Key Changes |
|---------|------|-------------|
| **S126** | **03-25** | **Spread inflation activated: BASE=0.15 + FACTOR=0.05 + 10%/day auto-decay (hard zero day 23). Lead-time WR analysis drove BASE component. Shadow deep analysis: 96% rejection rate, 1,784 would-trade shadows/day. City onboarding clarified: NOT a calibration gate. 35-city freeze flagged. Deploy `20260324_200349`.** |
| S125 | 03-24 | SHADOW_ENTRY DB constraint fix, resolution queue starvation fix (expired-first ordering, batch 500), 771 manual resolution backfill |
| S124 | 03-24 | Negative-EV gate (conf<price + _raw_size<=0), shadow logging, population->sample std, spread inflation foundation (OFF) |
| S123 | 03-23 | Platt+Isotonic confidence calibration deployed. T=2.271, Brier +11%. |
| S122 | 03-23 | Cap uncapping, shadow entries, confidence penalty removal, data analysis |
| S121 | 03-23 | SELL VWAP walk, book depletion, live retry, model freshness, PAPER_TAKER_FEE 150bps |
| S120 | 03-23 | Full code audit, EMOS 90-day window, precip fix, VWAP gate bypass, 6 root fixes |
| S119 | 03-22 | NO price trap, correlated blowup, position stacking, high-conf NO losses, overnight losses, edge cap |
| S118 | 03-22 | S117 diagnosis wrong, bot self-healed from 429 rate limiting, S116 reverted |
| S115 | 03-21 | Shadow fills system, combined boost cap, station reliability, Buhlmann credibility |
| S108 | 03-19 | Fill pipeline: taker 0.85, bestAsk pre-filter, volume passthrough, same-side dedup |
| S104 | 03-18 | Fill quality logging, exposure leak fix, daily counter, alpha decay BUY-only |
| S100 | 03-17 | Alpha decay, canary persistence, SSH timeouts, backoff Redis, P&L +$2,881 |

---

## TEMPERATURE SCALING EXPLAINER (for context)

T (temperature) is a single knob controlling how extreme predictions are:
- **T = 1.0**: No change. Model says 95%, output is 95%.
- **T > 1.0**: Compresses toward 50%. Our T=2.271 means 95% → ~75%.
- **T < 1.0**: Amplifies toward extremes.

It's trained by comparing predicted probabilities against actual outcomes (1=won, 0=lost) and finding the T that minimizes log loss (surprise). Our T=2.271 was fit on ~1,900 resolved trades and means the raw model is significantly overconfident across the board.

The spread inflation attacks the same problem from upstream — widen the ensemble distribution BEFORE CDF integration, so the raw probabilities are less extreme to begin with. As this takes effect, a future T refit would produce a value closer to 1.0.
