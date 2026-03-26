# WeatherBot Calibration Cold-Start Research Prompt

## Context

You are researching solutions for a **calibration cold-start problem** in a Polymarket weather trading bot. The bot trades temperature bucket markets (e.g. "Will NYC high temp be 75-79°F?") using ensemble weather model forecasts (GFS 31-member, ECMWF IFS 51-member, ECMWF AIFS 51-member = 133 total members). The system runs EMOS (Ensemble Model Output Statistics) calibration per station to correct systematic bias in the raw ensemble output.

### The Problem

When a new city appears on Polymarket (e.g. Chengdu, Shenzhen), the bot has **zero calibration data** for that station. EMOS needs ≥20 resolved forecast-actual pairs per (station_id, lead_time_bucket) to activate. Each pair requires a market to resolve (1 day), so a new city trades **uncalibrated for 20+ days** — using raw ensemble spread with a wider min_edge safety margin (12% intl vs 8% US) as the only protection.

Additionally, Polymarket **rotates cities** — a city may appear for 2-3 weeks then vanish, replaced by another. This means:
- Calibration data for dropped cities is wasted
- Replacement cities start cold again
- The bot may never reach calibrated state for churning cities
- Weeks of suboptimal trading per rotation cycle

### Current Architecture

**EMOS calibration** (per station, per lead-time bucket):
- Keys: `(station_id, lead_time_hours)` where lead_time is bucketed in 6h intervals
- Formula: `μ_corrected = a + b·X̄`, `σ_emos = sigma`
- Fitting: OLS regression on (forecast_temp, actual_temp) pairs
- Minimum: 20 pairs to activate, otherwise raw ensemble used
- Reload: Every 6 hours from `weather_calibration` table
- Regime-aware: Optionally splits by ENSO regime (el_nino/la_nina/neutral) when ≥20 regime-specific pairs exist

**Tail calibration** (isotonic regression on tail bracket model_prob vs actual_freq):
- Keys: `(bucket_type, lead_time_bucket)`
- Minimum: 50 resolved tail events to activate, otherwise fixed 0.90 discount
- Already partially pooled (not per-station), but still slow to accumulate for rare tail events

**Brier MSE halt gate**:
- 7-day rolling MSE from `weather_calibration` table
- Needs 10 samples to activate
- MSE > 25 halts trading; MSE > 16 warns

**Station registry**: Each station has ICAO code, lat/lon, timezone, temp_unit (F/C), aliases, optional local_model override. Currently ~25 US + ~8 international stations.

**Data flow**: Market question → city extraction → station lookup → ensemble fetch (by lat/lon) → EMOS correction → distribution fit → bucket probabilities → edge calculation → trade if edge > min_edge.

**DB table**: `weather_calibration` stores `(station_id, target_date, forecast_temp, actual_temp, lead_time_hours, bias, model_name, crps, regime)`.

---

## Research Questions

Investigate ALL of the following areas. For each, provide: (1) the approach, (2) statistical justification for why it works, (3) failure modes / risks, (4) implementation complexity estimate (trivial / moderate / significant), (5) whether it requires DB schema changes.

### 1. Regional EMOS Pooling

Can we share EMOS calibration across geographically similar stations?

- **Hypothesis**: GFS systematic bias is spatially correlated — Beijing, Chengdu, Chongqing, and Wuhan (all continental China, similar latitude, inland) likely share similar GFS warm/cold bias patterns. Pooling their calibration data would reach the 20-sample threshold 4x faster.
- **Research**: What does the meteorological literature say about spatial correlation of NWP bias? At what distance does bias correlation break down? Is it latitude-dependent, elevation-dependent, or regime-dependent?
- **Design questions**: How to define regions? By country? Climate zone (Köppen)? Lat/lon radius? Manual grouping? Should pooling be a hard merge (combine all data) or a hierarchical prior (Bayesian shrinkage toward regional mean)?
- **Risk**: Two cities 500km apart may have opposite biases (coastal vs inland, mountain vs plain). Pooling could make calibration worse than no calibration.

### 2. Global Baseline EMOS (Bayesian Hierarchical)

Instead of waiting for station-specific data, start with a **global prior** derived from all stations' historical EMOS parameters, then shrink toward station-specific as local data accumulates.

- **Approach**: Fit a global EMOS `(a_global, b_global, σ_global)` from all stations combined. For new stations, start with this prior. As station-specific pairs accumulate, blend: `a_station = w·a_local + (1-w)·a_global` where `w = n_local / (n_local + κ)` and κ is a shrinkage strength hyperparameter.
- **Research**: What's the right shrinkage schedule? How to set κ? Is this equivalent to an empirical Bayes approach? What does the NWP post-processing literature recommend for this?
- **Risk**: Global prior may be dominated by US stations (21 vs 8 intl), creating bias for international cities using Celsius with different model suites.

### 3. Transfer Learning from Nearby Resolved Stations

When a new city appears, find the nearest station(s) with established EMOS and transfer their parameters as a warm start.

- **Approach**: On new station registration, query `weather_calibration` for the K nearest stations (by lat/lon), weight by inverse distance, compute initial EMOS.
- **Research**: Does NWP bias transfer reliably over distance? Is elevation-adjusted distance better than raw Haversine? Should we match on climate zone instead of distance?
- **Risk**: Nearest station could be across an ocean or mountain range with very different bias characteristics.

### 4. Model-Run Bias Tables (Pre-computed)

GFS and ECMWF publish systematic bias statistics. Could we bootstrap EMOS from published model bias data rather than waiting for our own observations?

- **Research**: Do NOAA, ECMWF, or Open-Meteo publish gridded bias statistics? Are there public datasets of NWP verification scores by region? Could we pull CRPS/MAE from the Open-Meteo historical API to pre-compute station-level bias before any market resolves?
- **Approach**: For each new station's lat/lon, fetch 90 days of historical ensemble forecasts + reanalysis actuals from Open-Meteo, compute EMOS offline.
- **Risk**: Historical bias may not match real-time bias (model updates, seasonal drift).

### 5. Adaptive Min-Edge Scaling

Instead of a binary calibrated/uncalibrated state, **continuously scale the min_edge threshold** based on calibration confidence.

- **Approach**: `effective_min_edge = base_min_edge * (1 + uncertainty_factor)` where `uncertainty_factor = max(0, 1 - n_pairs/threshold) * penalty_mult`. With 0 pairs, edge requirement might be 2x base. With 20 pairs, it drops to base.
- **This doesn't solve calibration** but limits downside exposure during ramp-up.
- **Research**: Is a linear ramp optimal, or should it be exponential/sigmoid? What penalty_mult balances opportunity cost vs uncalibrated risk?

### 6. Station Persistence Prediction

Can we predict which cities Polymarket will keep vs drop, to avoid investing calibration effort in transient cities?

- **Research**: Analyze historical Polymarket weather market listings — do cities follow a pattern? Are there "core" cities that never rotate? Is churn concentrated in international markets?
- **Approach**: Track `traded_markets` table for city appearance/disappearance. If a city has been listed for >30 days, it's probably permanent. If <7 days, treat as transient and apply higher min_edge.
- **Minimal implementation**: Just a flag in station_registry indicating persistence confidence.

### 7. Cross-Model Calibration

Different models (GFS vs ECMWF) have different bias characteristics. Should EMOS be fit per-model rather than on the blended ensemble?

- **Research**: Does per-model EMOS outperform blended EMOS in the literature? The current system blends all 133 members before EMOS — would fitting separate EMOS per model source and then combining the corrected distributions be more accurate?
- **Trade-off**: 3x more EMOS parameters to fit = 3x more data needed = longer cold start. Only worth it if accuracy gain is substantial.

### 8. Ensemble Spread as Confidence Signal

Even without EMOS, raw ensemble spread contains calibration-adjacent information.

- **Research**: Can we use ensemble spread (std across 133 members) as a proxy for forecast uncertainty? When spread is tight (< 2°F), the raw forecast is probably reliable even without EMOS. When spread is wide (> 6°F), we should be cautious regardless of EMOS.
- **Approach**: Weight min_edge by normalized ensemble spread. Tight spread → lower min_edge, wide spread → higher min_edge.
- **This is orthogonal to EMOS** and could be implemented independently.

---

## Deliverable

Produce a ranked recommendation with:
1. **Tier 1 (implement now)**: Approaches that are high-ROI, low-risk, and can ship in 1-2 sessions
2. **Tier 2 (implement next)**: Approaches that need more data or design work but are clearly valuable
3. **Tier 3 (monitor/defer)**: Approaches that are interesting but premature or high-risk

For each recommendation, include:
- Concrete implementation sketch (which files, which functions, what changes)
- Expected improvement (quantified if possible — e.g. "reduces cold-start from 20 days to 3 days")
- Data requirements and availability
- Failure modes and rollback strategy

Also flag any approaches that are **actively harmful** and should NOT be implemented.

## Constraints

- This is a live trading system — changes must be backward-compatible
- EMOS fitting runs every 6h in `_maybe_reload_calibration()` — any pooling must integrate there
- `weather_calibration` table schema changes require a migration (numbered 057+)
- The bot has 25 US + 8 international stations — sample sizes are small
- Polymarket weather market volume is moderate (~$5k-50k per market) — edge matters more than throughput
- Paper trading phase with SIMULATION_MODE=true — we can experiment, but per CLAUDE.md, paper trading IS production
