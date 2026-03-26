# AGENT HANDOFF — WeatherBot Session 124 (2026-03-24)

## STATUS: LIVE PAPER TRADING | DEPLOYED `20260323_215951` | NEGATIVE-EV BUG FIXED | SPREAD INFLATION FOUNDATION LAID

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
- **Deploy**: `bash deploy/deploy.sh` from local repo (atomic symlink swap)
- **Logs**: `sudo journalctl -u polymarket-ai -f`
- **Current deploy**: `20260323_215951` (S124)
- **DB**: PostgreSQL on VPS. Access: `sudo -u postgres psql polymarket`
- **P&L script**: `python scripts/bot_pnl.py WeatherBot <hours>`
- **48h charts**: `python scripts/weather_48h_charts.py` (needs `wb_48h_raw.csv` from VPS export)

---

## WHAT WAS DONE THIS SESSION (S124)

### Critical Bug Fix: Negative-EV Forced Bet Bug

**Problem discovered**: When the S123 Platt+Isotonic calibrator compresses confidence below market price, Kelly correctly returns 0.0 (negative EV). But two bugs forced trades anyway:

**Bug 1 — Kelly path (`max(_min_trade, _raw_size)`):**
- `kelly_shares = 0.0` → `_raw_size = 0.0`
- `size = max(5.0, 0.0) = 5.0` — forced $5 bet on negative-EV trade
- Shadow entry code at L2703 never fired because `5.0 < 5.0` = False

**Bug 2 — S-T (Smoczynski-Tomkins) multi-bucket path:**
- S-T allocator distributes budget by edge ratio, doesn't check Kelly's EV signal
- `_st_size_override` produces `_raw_size > 0` even when `confidence < price`
- Example: Dallas NO at 91c, conf=0.81 → $163 forced bet (caught in VPS data)

**Fix**: Single guard at L2699 catches BOTH paths:
```python
if opp["confidence"] < opp["price"] or _raw_size <= 0:
    # Log shadow entry with reason="negative_ev" or "zero_kelly"
    return False
```

**Safety verification (all paths to `_raw_size`):**
- S-T override: `st_size >= 1.0`, `combined_boost > 0` → `_raw_size >= 1.0`. Caught by `confidence < price` check. ✅
- Kelly positive: `kelly_shares > 0` → `_raw_size > 0`, `confidence >= price`. Guard skipped. ✅
- Kelly zero: `kelly_shares = 0.0` → `_raw_size = 0.0`. Caught by `_raw_size <= 0`. ✅
- Exception fallback: `_raw_size = self._default_size = 25`. Guard skipped. ✅
- Calibrator OFF: raw confidence always > price for edge-filtered trades. Guard skipped. ✅

**Shadow entry logging enhanced:**
- `reason="negative_ev"`: confidence < price (either path)
- `reason="zero_kelly"`: _raw_size = 0 but confidence >= price (edge case)
- `raw_size_usd` now recorded for S-T override trades so we see what WOULD have been bet
- DB write: `insert_trade_event(event_type="SHADOW_ENTRY", ...)`

### Population → Sample Standard Deviation Fix

**File:** `base_engine/weather/forecast_client.py` L1250

`/ len(ensemble_members)` → `/ max(len(ensemble_members) - 1, 1)`

Consistency fix — `probability_engine.py` L83 already used N-1 (sample std). ~2% spread increase with 51+ members.

### Spread Inflation Foundation (Default OFF)

**Strategy** (user-approved): Leave the calibrator as-is (it's mathematically correct). Gradually improve the probability engine so raw model probabilities are more honest. As raw probs improve, the calibrator's T naturally drifts toward 1.0 (identity) and eventually becomes a no-op. The calibrator refits every 6h on a 30-day window — self-healing by design.

**New setting:** `WEATHER_SPREAD_INFLATION_FACTOR=0.0` (default OFF, zero runtime impact)

**New code in `probability_engine.py` after L98:**
```python
_spread_inflation = float(getattr(settings, "WEATHER_SPREAD_INFLATION_FACTOR", 0.0))
if _spread_inflation > 0:
    _lead_days = max(lead_time_hours / 24.0, 1.0)
    _inflation_mult = 1.0 + _spread_inflation * (math.sqrt(_lead_days) - 1.0)
    effective_std *= _inflation_mult
    if emos_sigma is not None:
        emos_sigma *= _inflation_mult  # affects skewnorm path at L125
```

Both `effective_std` (normal fallback) and `emos_sigma` (skewnorm primary path) are inflated. When `emos_sigma is None` (no EMOS), skewnorm uses MLE-fitted `scale` which captures natural ensemble spread — correct to leave alone.

With factor=0.10: 24h→×1.00, 48h→×1.04, 72h→×1.07, 120h→×1.12, 168h→×1.16

**To activate:** Set `WEATHER_SPREAD_INFLATION_FACTOR=0.10` on VPS env, restart service.

### Files Modified
- `bots/weather_bot.py` — Negative-EV guard + shadow logging (L2695-2725)
- `base_engine/weather/probability_engine.py` — `settings` import + spread inflation hook (L100-109)
- `base_engine/weather/forecast_client.py` — Population→sample std (L1250)
- `config/settings.py` — `WEATHER_SPREAD_INFLATION_FACTOR` setting
- `tests/unit/test_weather_bot.py` — 4 new tests (1717 total pass, 0 fail)

---

## LONG-TERM VISION: CALIBRATION CONVERGENCE

The user-approved strategy for the overconfidence problem:

1. **Now (deployed):** Calibrator stays as-is. Negative-EV trades properly blocked. Shadow entries collect data on what's being rejected.
2. **Next:** Analyze shadow entries after 24-48h. Tune `WEATHER_SPREAD_INFLATION_FACTOR` upward based on data.
3. **Ongoing:** As spread inflation makes raw probs more honest, the calibrator auto-relaxes (T→1.0).
4. **End state:** Calibrator becomes identity function and can be removed. Probability engine produces honest probs natively.

**Why this approach:**
- The calibrator data proves NO trades at 65c+ are genuinely negative EV (75.5% WR at 80-90c entry, breakeven requires 85%+)
- The +$189 on 580 trades ($0.33/trade) in the high-confidence bucket was noise, not edge
- Caps, blends, and knobs are bandaids that dilute a correct signal
- Fixing the source (probability engine spread) is the clean long-term path

---

## PROBABILITY ENGINE: ROOT CAUSES OF OVERCONFIDENCE

### Identified in S124 exploration (6 sources of systematic narrowness):

| Issue | Location | Impact |
|-------|----------|--------|
| **Population std divisor** | `forecast_client.py:1250` | **FIXED S124.** Was ~5% underestimate |
| **No lead-time spread inflation** | `probability_engine.py` (was missing) | **FOUNDATION LAID S124.** Default OFF. |
| **GEFS subsampling at 48h+** | `forecast_client.py:1232-1245` | Removes diversity, narrows dist 10-20%. UNFIXED. |
| **No EMOS before 20 pairs** | `probability_engine.py:96` | Raw underdispersed std used. Cold-start overconfidence. |
| **Hard floor 0.5°F** | `probability_engine.py:84,96` | Asymmetric — inflates tight, doesn't widen normal. |
| **Tail discount post-normalization** | `probability_engine.py:150-162` | Compresses tail mass after integration. |

### The Math: Ensemble → Distribution → Bucket Probability
```
1. Clean ensemble: filter NaN/Inf → n finite members
2. Raw std: sqrt(Σ(xᵢ - x̄)² / (n-1))  [sample std, FIXED S124]
3. EMOS correction: μ = a + b·X̄, σ = residual_std (if ≥20 pairs)
4. effective_std = max(emos_sigma ?? raw_std, 0.5)
5. S124: spread inflation applied to effective_std and emos_sigma
6. Skew-normal MLE fit (if scipy + n≥10), else normal
7. CDF integration per bucket with ±0.5° offset
8. Tail discount (0.90 default)
9. Normalize to sum=1.0
```

---

## FULL CALIBRATION ARCHITECTURE (11 layers)

### Sizing sequence (when evaluating an opportunity):

```
1. RAW MODEL PROBABILITY
   └─ probability_engine.bucket_probabilities() → model_prob per bucket
      ├─ Skew-normal CDF integration
      ├─ EMOS mean correction: corrected = a + b * forecast_mean
      ├─ EMOS spread correction: corrected_sigma = sigma (if EMOS fitted)
      ├─ S124: Lead-time spread inflation (default OFF)
      └─ Tail isotonic calibration: multiply by tail discount [0.5, 1.0]

2. EDGE FILTER (L1373)
   └─ |model_prob - yes_price| >= min_edge (8% US, 12% intl)
      └─ If fails: skip this bucket entirely

3. CONFIDENCE ASSIGNMENT (L2100-2108)
   └─ raw_conf = model_prob (YES) or 1 - model_prob (NO)
   └─ base_confidence = min(0.95, raw_conf)
   └─ IF calibrator fitted: effective_confidence = calibrate(base_confidence)  ← S123
   └─ ELSE: effective_confidence = base_confidence

4. S124 NEGATIVE-EV GATE (L2699)
   └─ IF confidence < price OR _raw_size <= 0: shadow entry + return False
   └─ Catches BOTH Kelly and S-T paths

5. KELLY SIZING (bankroll_manager.py L156-162)
   └─ kelly_full = (confidence * b - q) / b
   └─ IF kelly_full <= 0: return 0  ← intercepted by gate above
   └─ size_usd = kelly_full * fraction * capital

6. SIZING MULTIPLIERS (combined_boost, L2350-2600)
   ├─ Expiry boost (1.0-2.0x by lead time)
   ├─ Regime boost (1.2x when ≥3 cities show unanimous edge)
   ├─ Severe weather halt/boost
   ├─ Jump boost (model run delta)
   ├─ NBM boost (1.3x high conviction)
   ├─ Model freshness factor (0.8-1.2x by model age)
   ├─ Station reliability (0.5-1.2x by 14-day MSE)
   ├─ Bühlmann credibility (0.0-1.0x by n_resolved)
   ├─ Baker-McHale uncertainty (0.5-1.0x by spread)
   └─ Combined cap: min(combined_boost, 2.0)

7. EXPOSURE CAPS (L2727-2733)
   ├─ Group cap: $10,000
   ├─ City cap: $5,000
   ├─ Per-bet cap: $600
   └─ Daily cap: $20,000

8. PAPER FILL SIMULATION (paper_trading.py)
   ├─ BUY: ask-side VWAP walk
   ├─ SELL: bid-side VWAP walk (S121)
   └─ Per-scan book depletion (S121)
```

### Monitoring layers (independent):
- **Brier + Drawdown** (L4428-4492): MSE > 25 → halt trading
- **EMOS Drift** (L768-811): DDM/EDDM alerts, no auto-halt
- **CalibrationTracker** (calibration_tracker.py): Brier tracking, drift detection

---

## LOCATION ONBOARDING

### Current: MANUAL ONLY

1. Polymarket adds new weather market for city X
2. WeatherBot scan detects city X via `lookup_station()` → returns None
3. Alert fires: `weatherbot_unmatched_cities` (L1172) — "Add to station_registry.py"
4. Developer manually adds `WeatherStation` entry to `STATION_REGISTRY` in `base_engine/weather/station_registry.py`
5. Deploy → restart → cold-start bootstrap auto-populates 90 days of calibration data
6. EMOS fits on next calibration reload (6h)

### Files involved:
- `base_engine/weather/station_registry.py` — STATION_REGISTRY dict (~65 stations, L42-1399), `lookup_station()` (L1413-1441), alias matching
- `base_engine/weather/market_mapper.py` — `group_markets()` calls lookup_station, tracks `_last_unmatched_cities`
- `bots/weather_bot.py` L1158-1179 — unmatched city detection + alerting

### Auto-onboarding is NOT implemented. Considerations for future:
- Would need: ICAO station lookup API, automatic lat/lon, timezone detection, temp_unit inference
- Risk: wrong station match → bad calibration → bad trades
- Recommendation: semi-automated (detect → suggest → human approve)

---

## DATA ANALYSIS FINDINGS (S122-S124, 1,185+ resolutions)

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

### Key insight: Model has signal (monotonic WR) but confidence is stretched too wide vs reality.

### Edge Buckets
| Edge | N | WR | P&L | $/trade |
|------|---|-----|-----|---------|
| <5% | 208 | 63.0% | +$310 | $1.49 |
| 5-10% | 317 | 65.9% | +$395 | $1.25 |
| 10-15% | 339 | 61.4% | +$144 | $0.43 |
| 15-20% | 179 | 59.2% | +$839 | **$4.69** |
| 20-30% | 112 | 51.8% | +$519 | **$4.63** |

### By Side x Price
- **NO winners**: 20-40c (+$544), 40-60c (+$761), 80-100c (+$348)
- **NO loser**: 60-80c (-$164)
- **YES winners**: 20-40c (+$811), 40-60c (+$116)
- **YES losers**: 0-20c (-$175), 60-80c (-$203)

### 92h P&L (at time of S124 deploy)
| Category | N | P&L | WR |
|----------|---|-----|-----|
| **Total** | 506 | +$247.57 | 62.65% |
| Legit (conf≥price) | 467 | +$198.16 | 63.8% |
| Bug (conf<price) | 39 | +$49.42 | 48.7% |

### All-time P&L: +$2,531 (1,185 resolutions, 61.1% WR)

### Shadow Entries: Will start flowing post-S124 deploy. Query:
```sql
SELECT event_data->>'reason' AS reason, COUNT(*) AS n,
    ROUND(AVG(CAST(event_data->>'raw_size_usd' AS FLOAT))::numeric, 2) AS avg_would_bet,
    ROUND(AVG(confidence)::numeric, 4) AS avg_conf,
    ROUND(AVG(price)::numeric, 4) AS avg_price
FROM trade_events WHERE event_type = 'SHADOW_ENTRY' AND bot_name = 'WeatherBot'
GROUP BY reason;
```

### Buenos Aires: -$355 all-time, 51.1% WR — chronic loser, monitor

---

## PENDING PRIORITIES

| Pri | Item | Status |
|-----|------|--------|
| **P0** | Analyze shadow entries (24-48h post-deploy) | Wait for data |
| **P1** | Tune WEATHER_SPREAD_INFLATION_FACTOR based on shadow data | Blocked on P0 |
| **P2** | GEFS subsampling at 48h+ narrows spread | Identified, not yet addressed |
| **P3** | Location auto-onboarding feasibility | Currently manual only |
| **P4** | Buenos Aires chronic loser | -$355, southern hemisphere bias? |
| **P5** | VPS stability — went offline during S124 | Investigate Lightsail instance health |

---

## ARCHITECTURE REFERENCE (WeatherBot scan path)

```
scan_and_trade() [L983]
  ├─ _handle_daily_boundary() [L988] — resets exposure, restores P&L
  ├─ asyncio.gather(_maybe_reload_calibration, _load_category_params) [L999]
  │   ├─ EMOS: 90-day rolling window, station+regime-specific OLS
  │   ├─ Tail isotonic: weather_tail_calibration table
  │   ├─ Global EMOS/SAMOS: pooled cross-station fallback
  │   ├─ Bühlmann: station_n_resolved for cold-start ramp
  │   └─ S123: Platt+Isotonic confidence calibrator refit (6h cycle, 30-day window)
  ├─ _check_monitoring_thresholds() [L1007] — Brier/drawdown halt + Kelly graduation
  ├─ PM exit detection [L1010-1070] — tracks position_manager exits, decrements exposure
  ├─ _prefetch_severe_weather_alerts() [L1200] — NWS batch fetch
  ├─ asyncio.gather(*[_analyze_group(g) for g in groups]) [L1210]
  │   ├─ forecast_client.get_combined_forecast() — GFS+ECMWF+AIFS (133 members)
  │   ├─ prob_engine.fit_distribution() — skew-normal + EMOS + S124 spread inflation
  │   ├─ prob_engine.bucket_probabilities() — CDF integration per bucket
  │   ├─ _apply_metar_resolution_day_override() — METAR running max (< 12h)
  │   ├─ prob_engine.compute_edges() — model_prob - market_price
  │   └─ tradeable filtering:
  │       ├─ edge >= min_edge (spread-confidence gated)
  │       ├─ exit cooldown (4h)
  │       ├─ penny-bet filter (4c-97c)
  │       ├─ in-memory position check (fast path)
  │       ├─ DB position guard (ground truth) [S119]
  │       ├─ boundary_risk — LOGGED ONLY, no discount (S122)
  │       ├─ max 5 buckets per group (S122)
  │       └─ S123: Platt+Isotonic confidence calibration applied
  ├─ _compute_regime_boost() — cross-city warm/cold detection
  ├─ _exec_group() for each group with edge:
  │   ├─ ≥2 buckets: _execute_group_trades() → S-T multi-bucket Kelly
  │   └─ 1 bucket: _execute_weather_trade() → independent Kelly
  │       ├─ same-side dedup (_position_details)
  │       ├─ exit cooldown check
  │       ├─ fill-failure cooldown
  │       ├─ daily loss limit
  │       ├─ group/city exposure caps (locked) — $10K/$5K (S122)
  │       ├─ expiry boost (1.0-2.0x by lead time)
  │       ├─ regime boost (1.2x)
  │       ├─ severe weather halt/boost
  │       ├─ jump boost (model run delta)
  │       ├─ NBM boost (1.3x high conviction)
  │       ├─ model freshness factor (S121)
  │       ├─ combined boost (additive, cap 2.0x)
  │       ├─ Baker-McHale uncertainty (model spread)
  │       ├─ station reliability (MSE-based)
  │       ├─ Bühlmann calibration confidence (cold-start ramp)
  │       ├─ slippage check (liquidity guardian)
  │       ├─ S124: NEGATIVE-EV GATE — confidence < price OR _raw_size <= 0 → shadow + skip
  │       ├─ Kelly sizing via BotBankrollManager ($600 cap, $20K daily)
  │       ├─ SHADOW_ENTRY logging for sub-$5 trades (S122)
  │       ├─ exposure lock reservation (atomic)
  │       └─ place_order() → order_gateway (VWAP gate BYPASSED for WeatherBot)
  │           └─ paper_trading.place_order() → VWAP fill from book walk
  │               ├─ BUY: ask-side VWAP walk
  │               ├─ SELL: bid-side VWAP walk (S121)
  │               └─ per-scan book depletion (S121)
  ├─ _reevaluate_open_positions() — feed position_manager fresh probs
  └─ every 10 scans: backfill_outcomes + check_emos_drift + close_stale_positions
```

---

## CURRENT CONFIG (live VPS values post-S124)

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

# S124 NEW:
WEATHER_SPREAD_INFLATION_FACTOR=0.0 (default OFF — set to 0.10 to activate)

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
| `base_engine/weather/probability_engine.py` | 535 | Skew-normal distribution + EMOS + S124 spread inflation |
| `base_engine/weather/precipitation_engine.py` | 235 | Rain/snow probability |
| `base_engine/weather/market_mapper.py` | 1,136 | Polymarket question → city/bucket mapping |
| `base_engine/weather/forecast_client.py` | 1,587 | GFS/ECMWF/HRRR/AIFS ensemble fetching |
| `base_engine/weather/metar_client.py` | 237 | Airport observations (resolution day) |
| `base_engine/weather/metar_monitor.py` | 283 | METAR running max tracking |
| `base_engine/weather/model_run_monitor.py` | 339 | Model freshness tracking (S121) |
| `base_engine/weather/station_registry.py` | ~1,550 | Station definitions, lookup, health monitor |
| `base_engine/execution/paper_trading.py` | ~900 | Paper fills: VWAP walk, book depletion |
| `base_engine/execution/order_gateway.py` | ~1,200 | Order routing, VWAP gate, live retry |
| `base_engine/risk/bankroll_manager.py` | 263 | Kelly sizing, per-bot caps |
| `base_engine/learning/calibration_tracker.py` | 259 | EMOS + Brier tracking + drift detection |
| `base_engine/features/calibration.py` | 515 | FavoriteLongshot, Domain, FocalTemp, HorizonBias (NOT wired to WeatherBot) |
| `config/settings.py` | WEATHER_* block | All config |
| `tests/unit/test_weather_bot.py` | ~1,940 | Main test suite (1717 pass) |

---

## DATA SOURCES

| What | Where | Notes |
|------|-------|-------|
| P&L (realized) | `trade_events` table | SOLE AUTHORITY. `realized_pnl` column. |
| P&L (unrealized) | `positions.unrealized_pnl` | Mark-to-market, updated every 10s |
| Shadow entries | `trade_events WHERE event_type='SHADOW_ENTRY'` | S124: reason=negative_ev/zero_kelly |
| Predictions | `prediction_log` | `was_correct` = calibration, NOT trade WR |
| Paper trades | `paper_trades` | LEGACY. Do not use for P&L. |
| EMOS calibration | `weather_calibration` | forecast_temp, actual_temp, bias, lead_time |
| Tail calibration | `weather_tail_calibration` | model_prob, actual_outcome by bucket_type |
| Climatology | `weather_climatology` | ERA5 30-year normals for SAMOS |
| Canonical P&L | `python scripts/bot_pnl.py WeatherBot <hours>` | |

### VPS Data Export (for charts)
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

### Shadow entry analysis query (NEW S124)
```sql
-- What is the calibrator rejecting? Run 24-48h after deploy.
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

### Calibration diagnostic query
```sql
-- Entry confidence vs resolution outcome (for verifying calibrator impact)
WITH entries AS (
    SELECT DISTINCT ON (market_id) market_id, confidence, side, price, size,
        event_data->>'raw_confidence' AS raw_confidence
    FROM trade_events
    WHERE bot_name='WeatherBot' AND event_type='ENTRY'
      AND event_time >= NOW() - INTERVAL '48 hours'
    ORDER BY market_id, event_time
)
SELECT e.side,
    ROUND(AVG(e.confidence)::numeric, 4) AS avg_cal_conf,
    ROUND(AVG(CAST(e.raw_confidence AS FLOAT))::numeric, 4) AS avg_raw_conf,
    ROUND(AVG(e.price)::numeric, 4) AS avg_price,
    COUNT(*) AS n
FROM entries e GROUP BY e.side;
```

---

## CRITICAL TRAPS (cumulative — DO NOT VIOLATE)

1. **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
2. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL
3. **VWAP edge gate BYPASSED for WeatherBot** (order_gateway.py). Weather CLOBs have 99c structural asks. Do NOT re-enable.
4. **Paper capital is $10M intentionally.** BotBankrollManager is the real limit.
5. **EMOS has 90-day rolling window** (`WEATHER_EMOS_WINDOW_DAYS=90`). Cold stations fall back to global EMOS.
6. **`_in_model_window()` is DELETED.** Logic lives in `_get_scan_interval_seconds()`.
7. **Precipitation empirical fallback fixed S120.** `elif` → `if` for peer bucket-type branches.
8. **`boundary_risk` persisted in event_data** for audit but NO LONGER discounts confidence (S122).
9. **NO confidence discount REMOVED (S122).** Do not re-add. Kelly self-regulates.
10. **NO entry price cap REMOVED (S122).** `WEATHER_NO_MAX_ENTRY_PRICE=1.0`.
11. **`confidence` is a TOP-LEVEL column on trade_events**, NOT in event_data JSONB.
12. **Python 3.13 scoping**: `from X import Y` inside function makes Y local for ENTIRE function. Any use before import → UnboundLocalError.
13. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
14. **`paper_trades` has NO `metadata` JSONB column.**
15. **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time.
16. **BOT_REGISTRY=14 bots** — shared module change requires all 14 verified.
17. **positions table**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`.
18. **SHADOW_ENTRY events** are best-effort (try/except pass). Don't block trades on logging failure.
19. **fill_frac** in event_data shows paper fill model partial fill fraction. 75% of trades get <60% filled.
20. **S123 Platt semantics**: T > 1 COMPRESSES toward 0.5 (overconfident correction). T < 1 AMPLIFIES toward extremes (underconfident correction). This is the OPPOSITE of what you might expect.
21. **S123 calibrator properly blocks NO trades at 65c+** with T=2.271. This is correct — they're negative EV. S124 gate ensures they don't fire anyway.
22. **`raw_confidence` field** added to opp dict (L2139) and flows to event_data for audit. Use this to compare raw vs calibrated.
23. **Location onboarding is MANUAL** — station_registry.py must be edited by developer. Auto-discovery alerts but does not auto-add.
24. **S124: `max(_min_trade, _raw_size)` bug** — FIXED. Was forcing $5 on zero-Kelly trades. Guard at L2699 now catches `confidence < price` AND `_raw_size <= 0`.
25. **S124: S-T allocator bypasses Kelly EV check** — FIXED. The `confidence < price` guard catches S-T trades too. Example: Dallas $163 forced bet would now be blocked.
26. **S124: `settings` imported in `probability_engine.py`** — needed for spread inflation. No circular dependency.
27. **S124: Spread inflation inflates BOTH `effective_std` and `emos_sigma`** — the skewnorm path at L125 reads `emos_sigma` independently. Must inflate both.
28. **S124: Shadow entry `reason` field** — `"negative_ev"` (confidence < price, either path), `"zero_kelly"` (_raw_size = 0 but confidence >= price), `"sub_min_trade"` (Kelly > 0 but < $5), `"exposure_cap"` (capped by group/city limit).

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
| Calibration kills all trades | EXPECTED | T=2.271 is correct; S124 gate properly blocks negative-EV trades |
| 39 bug trades were profitable | NOISE | 48.7% WR at 38c avg price ≈ breakeven; will revert over time |

---

## SESSION HISTORY (recent)

| Session | Date | Key Changes |
|---------|------|-------------|
| **S124** | **03-24** | **Negative-EV gate (conf<price + _raw_size<=0), shadow logging, population→sample std, spread inflation foundation (OFF)** |
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

## USER PREFERENCES (from memory)

- **Scope lock**: NEVER add unsolicited features. Only fix what the handoff or user explicitly requests.
- **Root fixes only**: No bandaids, no gates, no config tuning without data backing.
- **Show don't tell**: User wants charts opened in image viewer (`start "" "path.png"`), not ASCII or inline.
- **Terse responses**: User doesn't want trailing summaries or over-explanation.
- **"Paper trading IS production"**: Every feature must work identically in paper and live. No shortcuts.
- **Kelly self-regulation**: User believes Kelly + BotBankrollManager should be the sizing authority. Artificial caps reduce EV. Only keep catastrophic backstops.
- **No bleed**: WeatherBot sessions touch WeatherBot only. No other bot changes unless explicitly demanded.
- **Calibration philosophy**: The calibrator is correct. Fix the source (probability engine), not the calibrator. Multiple approaches can stack (Platt + Isotonic). Calibration should converge to identity as the engine improves.
- **Verify before shipping**: User caught us mid-edit and demanded full path verification. Always trace every code path before claiming a fix is safe.
