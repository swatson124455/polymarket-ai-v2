# AGENT HANDOFF — WeatherBot Session 123 (2026-03-23)

## STATUS: LIVE PAPER TRADING | 1 COMMIT PENDING | CALIBRATION DEPLOYED — NEEDS TUNING

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
- **Current deploy**: `20260323_161454` (S123)
- **DB**: PostgreSQL on VPS. Access: `sudo -u postgres psql polymarket`
- **P&L script**: `python scripts/bot_pnl.py WeatherBot <hours>`
- **48h charts**: `python scripts/weather_48h_charts.py` (needs `wb_48h_raw.csv` from VPS export)

---

## WHAT WAS DONE THIS SESSION (S123)

### Platt + Isotonic Confidence Calibration Pipeline (deployed)

**Problem**: Model confidence is badly miscalibrated. Predicted 94.6% → actual 77.6% WR. Predicted 16.2% → actual 27.5%. Kelly sizes based on confidence → wrong bet sizes → 87% of capital goes to the 80-100% confidence bucket earning only $0.33/trade, while the 40-60% bucket earns $9.17/trade.

**Solution**: Two-stage calibration pipeline inserted between raw model confidence and Kelly sizing:

1. **Stage 1 — Platt Temperature Scaling**: `calibrated = sigmoid(logit(raw) / T)`. Fit T via scipy minimize_scalar on log-loss. T > 1 compresses overconfident predictions toward 0.5.
2. **Stage 2 — Isotonic Regression**: sklearn IsotonicRegression on Platt residuals. Catches non-logistic kinks (e.g., the 50-60% dip where WR drops unexpectedly).
3. **Rolling refit**: Every 6h inside existing `_maybe_reload_calibration()` hook. 30-day window. Needs ≥200 resolved trades.
4. **Brier guard**: Rejects calibration if it worsens Brier by >0.005.
5. **Kill switch**: `WEATHER_CONFIDENCE_CAL_ENABLED=false` → instant revert.

**Current fit results (live on VPS)**:
- Temperature T = 2.271 (heavy compression)
- Isotonic = fitted
- Raw Brier = 0.225 → Calibrated Brier = 0.200 (+11% improvement)
- 1,183 samples in 30-day window

### Files Modified
- `bots/weather_bot.py` — New `WeatherConfidenceCalibrator` class (lines 60-257), wired into `__init__` (L382-384), `_maybe_reload_calibration()` (L4414-4427), confidence assignment (L2100-2108), `raw_confidence` audit field added to opp dict (L2139)
- `config/settings.py` — 3 new settings: `WEATHER_CONFIDENCE_CAL_ENABLED`, `_WINDOW_DAYS` (30), `_MIN_SAMPLES` (200)
- `tests/unit/test_weather_bot.py` — 8 new tests (1689 total pass, 0 fail)

---

## P0: CALIBRATION IS TOO STRICT — NEEDS TUNING

### The Problem

With T=2.271, the Platt stage compresses so aggressively that:

| Trade Type | Raw Conf | Calibrated | Price | Edge |
|-----------|----------|------------|-------|------|
| NO at 85c | 0.95 | 0.785 | 0.85 | **-0.065** (killed) |
| NO at 80c | 0.90 | 0.725 | 0.80 | **-0.075** (killed) |
| NO at 70c | 0.85 | 0.682 | 0.70 | **-0.018** (killed) |
| NO at 65c | 0.80 | 0.648 | 0.65 | **-0.002** (killed) |
| NO at 55c | 0.70 | 0.592 | 0.55 | **+0.042** (survives) |
| YES at 15c | 0.30 | 0.408 | 0.15 | **+0.258** (boosted 72%) |
| YES at 25c | 0.40 | 0.455 | 0.25 | **+0.205** (boosted 37%) |

**ALL NO trades at 65c+ are killed.** These were 85% of trade volume. YES trades survive and get larger sizing.

### Is The Calibrator Correct?

Mathematically YES — the calibration data proves it:
- NO 80-100 conf bucket: 75.5% WR at avg price 80-90c
- Breakeven at 80c = 80% WR, at 90c = 90% WR
- 75.5% WR < 80% breakeven → **negative EV on a per-dollar basis**
- The old system earned +$289 on 691 trades = $0.42/trade — barely positive, likely noise

But practically the bot now makes near-zero trades on any given scan. Need to find the sweet spot.

### Resolution Options (for next session)

**Option A: Soften the temperature**
- Cap T at 1.5-1.8 instead of letting minimize_scalar find 2.27
- Pro: More trades survive, NO side still active
- Con: Less Brier-optimal, may keep some negative-EV trades

**Option B: Blend raw and calibrated confidence**
- `final = alpha * calibrated + (1 - alpha) * raw` where alpha = 0.5-0.7
- Pro: Gradual adoption, no trades killed entirely
- Con: Dilutes the calibration signal

**Option C: Apply calibration to sizing only, not edge gating**
- Keep raw confidence for the `confidence > price` check in Kelly
- Apply calibrated confidence only to the Kelly fraction (sizing magnitude)
- Pro: Trades still fire but with right-sized bets
- Con: Still bets on negative-EV trades, just smaller

**Option D: Separate calibration for NO vs YES sides**
- NO side has different miscalibration profile than YES
- Fit T_no and T_yes independently
- Pro: Precision. YES side may need T<1 (amplification), NO side needs T>1 (compression)
- Con: Smaller samples per side; adds complexity

**My recommendation**: Option C first (fastest, no code at risk), then evaluate Option D with more data.

---

## FULL CALIBRATION ARCHITECTURE (10 layers)

### Sizing sequence (when evaluating an opportunity):

```
1. RAW MODEL PROBABILITY
   └─ probability_engine.bucket_probabilities() → model_prob per bucket
      ├─ Skew-normal CDF integration
      ├─ EMOS mean correction: corrected = a + b * forecast_mean
      ├─ EMOS spread correction: corrected_sigma = sigma (if EMOS fitted)
      └─ Tail isotonic calibration: multiply by tail discount [0.5, 1.0]

2. EDGE FILTER (L1373)
   └─ |model_prob - yes_price| >= min_edge (8% US, 12% intl)
      └─ If fails: skip this bucket entirely

3. CONFIDENCE ASSIGNMENT (L2100-2108)
   └─ raw_conf = model_prob (YES) or 1 - model_prob (NO)
   └─ base_confidence = min(0.95, raw_conf)
   └─ IF calibrator fitted: effective_confidence = calibrate(base_confidence)  ← S123 NEW
   └─ ELSE: effective_confidence = base_confidence

4. KELLY SIZING (bankroll_manager.py L156-162)
   └─ kelly_full = (confidence * b - q) / b
   └─ IF confidence <= price: return 0  ← THIS KILLS TRADES WHEN CALIBRATOR OVER-COMPRESSES
   └─ size_usd = kelly_full * fraction * capital

5. SIZING MULTIPLIERS (combined_boost, L2350-2600)
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

6. EXPOSURE CAPS (L2690-2700)
   ├─ Group cap: $10,000
   ├─ City cap: $5,000
   ├─ Per-bet cap: $600
   └─ Daily cap: $20,000

7. PAPER FILL SIMULATION (paper_trading.py)
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

### Daily digest log (L1181-1194):
```
weatherbot_daily_city_digest active_cities=35 registry_size=106 unmatched_cities=[] unmatched_count=0
```

### Auto-onboarding is NOT implemented. Considerations for future:
- Would need: ICAO station lookup API, automatic lat/lon, timezone detection, temp_unit inference
- Risk: wrong station match → bad calibration → bad trades
- Recommendation: semi-automated (detect → suggest → human approve)

---

## DATA ANALYSIS FINDINGS (S122-S123, 1,185 resolutions)

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

### Recent P&L
| Window | N | WR | P&L |
|--------|---|-----|-----|
| 24h | 239 | 61.9% | +$163 |
| 48h | 505 | 62.6% | +$244 |
| 7d | 760 | 63.0% | +$415 |
| All-time | 1,185 | 61.1% | +$2,531 |

### Shadow Entries: 0 recorded (S122 logging may not be hitting code path, investigate)

### Buenos Aires: -$355 all-time, 51.1% WR — chronic loser, monitor

---

## PENDING PRIORITIES

| Pri | Item | Status |
|-----|------|--------|
| **P0** | Tune calibration — too strict, killing all NO 65c+ trades | Options A-D above |
| **P1** | Verify shadow entry logging is actually firing | 0 entries since S122 deploy |
| **P2** | Review location auto-onboarding feasibility | Currently manual only |
| **P3** | Buenos Aires chronic loser | -$355, southern hemisphere bias? |
| **P4** | YES undersized ($3.45 avg) — monitor post-calibration | Should improve with calibrator boosting YES |

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
  │   └─ S123: Platt+Isotonic confidence calibrator refit  ← NEW
  ├─ _check_monitoring_thresholds() [L1007] — Brier/drawdown halt + Kelly graduation
  ├─ PM exit detection [L1010-1070] — tracks position_manager exits, decrements exposure
  ├─ _prefetch_severe_weather_alerts() [L1200] — NWS batch fetch
  ├─ asyncio.gather(*[_analyze_group(g) for g in groups]) [L1210]
  │   ├─ forecast_client.get_combined_forecast() — GFS+ECMWF+AIFS (133 members)
  │   ├─ prob_engine.fit_distribution() — skew-normal + EMOS correction
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
  │       └─ S123: Platt+Isotonic confidence calibration applied  ← NEW
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

## CURRENT CONFIG (live VPS values post-S123)

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

# S123 NEW:
WEATHER_CONFIDENCE_CAL_ENABLED=true
WEATHER_CONFIDENCE_CAL_WINDOW_DAYS=30
WEATHER_CONFIDENCE_CAL_MIN_SAMPLES=200

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
| `bots/weather_bot.py` | ~4,658 | Main bot: scan, analyze, trade, exit, calibration |
| `base_engine/weather/probability_engine.py` | 520 | Skew-normal distribution + EMOS calibration |
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
| `tests/unit/test_weather_bot.py` | ~1,835 | Main test suite (1689 pass) |

---

## DATA SOURCES

| What | Where | Notes |
|------|-------|-------|
| P&L (realized) | `trade_events` table | SOLE AUTHORITY. `realized_pnl` column. |
| P&L (unrealized) | `positions.unrealized_pnl` | Mark-to-market, updated every 10s |
| Shadow entries | `trade_events WHERE event_type='SHADOW_ENTRY'` | Sub-$5 trades (S122) — 0 recorded so far |
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
21. **S123 calibrator kills NO trades at 65c+** with current T=2.271. This is Brier-optimal but trade-count-zero. Needs tuning (see P0 above).
22. **`raw_confidence` field** added to opp dict (L2139) and flows to event_data for audit. Use this to compare raw vs calibrated.
23. **Location onboarding is MANUAL** — station_registry.py must be edited by developer. Auto-discovery alerts but does not auto-add.

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
| Calibration kills all trades | EXPECTED | T=2.271 is correct; NO 65c+ is negative EV per breakeven math |

---

## SESSION HISTORY (recent)

| Session | Date | Key Changes |
|---------|------|-------------|
| **S123** | **03-23** | **Platt+Isotonic confidence calibration deployed. T=2.271, Brier +11%. All NO 65c+ killed — needs tuning.** |
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
- **Calibration philosophy**: Multiple approaches can stack (Platt + Isotonic). But calibration should enable trades, not kill them. Find the sweet spot between Brier-optimal and trade-volume-optimal.
