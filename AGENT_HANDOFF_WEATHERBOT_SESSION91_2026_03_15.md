# AGENT HANDOFF — WeatherBot Session 91
**Date**: 2026-03-15
**Bot Scope**: WeatherBot ONLY (scope lock: non-negotiable)
**Prior Session**: Session 90 (all 4 fixes deployed, 3 cities added, article analysis complete)

---

## HARD RULES (READ BEFORE DOING ANYTHING)

### Scope Lock (NON-NEGOTIABLE)
1. **ONLY touch WeatherBot files** unless fixing a shared module bug that directly breaks WeatherBot
2. **NEVER add unsolicited features** — only fix/build what this handoff or the user explicitly requests
3. **Observation duty**: Note issues and surface to user. Do NOT silently implement.
4. **Read CLAUDE.md** before modifying any file — it contains the Prime Directive and Rules of Engagement

### Critical Traps (WILL BREAK THINGS IF IGNORED)
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL"
- **trade_events is P&L AUTHORITY** — never read `paper_trades` for P&L. SELL/EXIT trades only exist in trade_events
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass
- **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer
- **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
- **Python 3.13 scoping**: `from X import Y` inside a function makes Y a local for the ENTIRE function. Any use of Y BEFORE that import line → `UnboundLocalError`. NEVER use local imports that shadow top-level names
- **trade_events immutability trigger**: `trg_trade_events_immutable` prevents DELETE/UPDATE. Must `DISABLE TRIGGER` then re-enable for data cleanup
- **RESOLUTION event idempotency**: `ON CONFLICT (idempotency_key, event_time)` is BROKEN on partitioned tables. `insert_trade_event()` uses atomic INSERT...SELECT with WHERE NOT EXISTS for RESOLUTION events instead
- **trade_events JSONB column is `event_data`** — NOT `metadata_json`. `paper_trades` has NO `resolved_pnl` column (it's `resolved_at`)
- **PatchDriftDetector**: `_patch_timestamps` must ONLY be set on genuine patch changes (`old is not None`)
- **positions table columns**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`
- **prediction_log columns**: NO `rejection_reason`. Use `trade_executed` (bool) + `model_name`
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime string
- **asyncpg timestamps**: `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`. `created_at` has NO DEFAULT
- **BOT_REGISTRY=14 bots** — shared module change requires all 14 verified
- **`paper_trades` has NO `metadata` JSONB column**
- **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time
- **MirrorBot entry price**: Uses CURRENT market price from `get_market_from_index()`, NOT trader's fill price
- **RTDS envelope**: Must unwrap `data.get("payload", data)` — trade data is NOT at top level
- **P&L formula**: `cost = entry_price * size` (ALL sides), `uPnL = (current - entry) * size` (ALL sides). NEVER invert for NO positions

---

## SYSTEM OVERVIEW

### What This Is
A 14-bot automated Polymarket trading system. WeatherBot is one of 5 active bots. Currently in **paper trading mode** (`SIMULATION_MODE=true`). Real capital is NOT at risk — all trades are simulated via `PaperTradingEngine`. Paper trading IS production (see CLAUDE.md).

### WeatherBot Identity
WeatherBot trades temperature, precipitation, snowfall, and wind-gust bucket markets on Polymarket. It uses a **133-member NWP ensemble** (GEFS 31 + ECMWF IFS 51 + ECMWF AIFS 51) with EMOS calibration, isotonic tail correction, METAR resolution-day overrides, and Smoczynski-Tomkins multi-bucket Kelly allocation.

### Active Bots (5 of 14)
| Bot | P&L | Notes |
|-----|-----|-------|
| MirrorBot | +$15,051 realized | RTDS live, 103 open positions |
| WeatherBot | +$910 realized | ~400 open positions, 156/643 resolved |
| EsportsBot | -$22 realized | ~7 open positions, 62/72 resolved |
| EsportsLiveBot | Active | — |
| EsportsSeriesBot | Active | — |

---

## WEATHERBOT LIVE CONFIG (VPS — as deployed)
```
Capital:          $20,000
Kelly fraction:   0.25
Max bet:          $300
Max daily:        $10,000
Max positions:    500
Min edge (US):    0.08
Min edge (intl):  0.12
Forecast cache:   900s (15min)
Rate limit:       120 req/min (Open-Meteo)
Group concurrency: 12
Scan interval:    60s (ECMWF window) / 90s (GFS) / 120s (HRRR) / 300s (default)
```

---

## WEATHERBOT ARCHITECTURE

### Data Flow (per scan cycle)
```
1. DISCOVERY     → Gamma API tag_slug=temperature → WeatherMarketMapper groups by (city, date)
2. FORECASTING   → WeatherForecastClient → Open-Meteo GFS/GEFS/ECMWF/HRRR ensembles (133 members)
3. CALIBRATION   → EMOS (a + b·X̄, σ) per station/lead-time/regime + isotonic tail calibration
4. PROBABILITY   → WeatherProbabilityEngine → skew-normal CDF integration per bucket
5. EDGES         → model_prob - market_price, sorted by |edge|
6. REGIME        → ENSO (Nino 3.4), cross-city warm/cold detection, severe weather alerts, AFD uncertainty
7. SIZING        → Fractional Kelly (0.25) × regime boost × near-expiry boost × drawdown compression
                    × Smoczynski-Tomkins multi-bucket allocation × Baker-McHale uncertainty factor
8. RISK CHECKS   → group exposure cap, city exposure cap, daily loss limit, position count limit
9. EXECUTION     → place_order(side="YES"/"NO") → PaperTradingEngine → paper_trades + trade_events
10. RESOLUTION   → METAR T-group override (<6h) + WU scraping + resolution_backfill
11. PERSISTENCE  → Redis (exit cooldowns, 429 cooldowns), DB (exposure, P&L), daily_counters
```

### Key Algorithms
- **EMOS**: μ_emos = a + b·X̄, σ_emos — fitted via OLS on (forecast, actual) pairs, per-station, per-lead-time
- **Isotonic tail calibration**: Data-driven tail discount from ≥50 resolved events (replaces fixed 15%)
- **Baker-McHale factor**: `k* = 1/(1 + σ²)` — ensemble spread → sizing reduction
- **Smoczynski-Tomkins**: Optimal allocation across mutually exclusive temperature buckets
- **Fractional Kelly**: 0.25 × near_expiry_boost (up to 2.0×) × regime_boost (1.2×) × drawdown_compression
- **METAR resolution-day override**: Within 6h of resolution, running daily max from T-groups replaces ensemble
- **F→C→F rounding**: `_near_boundary()` → 50% confidence reduction when ensemble mean within 0.5°F of bucket edge
- **Climate blending**: 0-40% ramp at 72h-168h lead time (prevents overconfident long-range bets)
- **WU scraping**: `_fetch_wu_daily_high()` — 4-pattern regex, sanity-checked vs Open-Meteo

### File Map (WeatherBot-specific)
```
bots/weather_bot.py                              (3,669 lines) — Main bot: scan, analyze, trade, calibrate
base_engine/weather/station_registry.py          (1,447 lines) — 50+ city registry (ICAO, GHCND, aliases, models)
base_engine/weather/forecast_client.py           (1,340 lines) — Multi-model ensemble fetching (Open-Meteo)
base_engine/weather/market_mapper.py             (1,105 lines) — Market text → TemperatureBucket/Group parsing
base_engine/weather/probability_engine.py        (439 lines)   — Skew-normal CDF, EMOS, isotonic tail, Kelly
base_engine/weather/metar_client.py              (236 lines)   — Aviation Weather Center METAR API
base_engine/weather/precipitation_engine.py      (231 lines)   — Gamma distribution for precip/snow/wind
base_engine/weather/asos_onemin_client.py        (145 lines)   — IEM 1-minute ASOS data (US only)
```

### Shared Modules (WeatherBot depends on)
```
base_engine/base_engine.py                       (2,479 lines) — Core engine, market lookup, order routing
base_engine/risk/bankroll_manager.py             (varies)      — BotBankrollManager: Kelly sizing + caps
base_engine/data/database_lock.py                (97 lines)    — Advisory lock (asyncio.shield fix Session 90)
base_engine/data/ingestion_scheduler.py          (459 lines)   — Ingestion pipeline + master timeout
base_engine/data/resolution_backfill.py          (515 lines)   — Resolution P&L computation
base_engine/execution/paper_trading.py           (717 lines)   — Paper trade simulation
base_engine/execution/order_management_system.py (499 lines)   — Order state machine
base_engine/portfolio/reconciliation.py          (256 lines)   — Position reconciliation
base_engine/utils/dst_calendar.py                (128 lines)   — DST/timezone handling
base_engine/utils/shared_rate_limiter.py         (418 lines)   — Cross-bot API rate limiting
base_engine/features/market_router.py            (280 lines)   — Market → bot routing
base_engine/monitoring/dead_man_switch.py        (98 lines)    — Watchdog for silent failures
base_engine/monitoring/prometheus_exporter.py    (328 lines)   — Metrics export
config/settings.py                               (1,172 lines) — All WEATHER_* settings with defaults
```

### weather_bot.py Method Index (EVERY method)
```
SCAN & STRATEGY:
  scan_and_trade()                          — Main loop: discover, analyze, trade, re-eval positions
  analyze_opportunity(market_data)          — Single-market fallback analysis
  _analyze_group(group)                     — All buckets in city+date group → opportunities + probs
  _execute_weather_trade(opp, group)        — Execute single trade with risk checks
  _execute_group_trades(opps, group, boost) — S-T multi-bucket laddered sizing
  _reevaluate_open_positions(analyzed)      — Update predicted_prob on open positions

PRECIPITATION / SNOWFALL / WIND:
  _scan_precipitation_markets()             — Gamma API tag_slug discovery + ensemble + trade
  _analyze_precipitation_group(group)       — Gamma distribution precip analysis
  _scan_snowfall_markets()                  — Snowfall variant
  _analyze_snowfall_group(group)            — Snowfall analysis (reuses PrecipitationProbabilityEngine)
  _scan_wind_markets()                      — Wind gust discovery
  _analyze_wind_group(group)                — Normal CDF wind analysis

CALIBRATION & MONITORING:
  _maybe_reload_calibration()               — Load EMOS from DB every 6h
  _check_monitoring_thresholds()            — Brier/drawdown halt (MSE>25 or DD>20%)
  _check_emos_drift()                       — DDM/EDDM drift detection per station
  _log_weather_prediction(...)              — Prediction accuracy logging
  _backfill_weather_outcomes()              — Resolve predictions against settled markets

MARKET DISCOVERY:
  _fetch_weather_events_by_tag()            — Gamma API tag_slug=temperature
  _fetch_weather_markets_direct()           — Fallback DB + Gamma probe
  _enrich_with_live_prices(markets)         — CLOB /midpoint enrichment
  _check_weather_market_availability()      — Startup availability log

DATA PERSISTENCE:
  _handle_daily_boundary()                  — UTC day boundary reset
  _restore_daily_pnl_from_db()              — trade_events realized P&L
  _restore_exposure_from_db()               — Rebuild group/city exposure from paper_trades
  _restore_exits_from_redis()               — Exit cooldowns from Redis
  _save_exit_to_redis(market_id)            — Persist exit with 15-min TTL
  _save_forecast_to_db(station, date, fc)   — Ensemble snapshot + calibration row
  _maybe_update_calibration_actuals()       — Fill actual_temp, compute CRPS

TEMPERATURE UTILITIES:
  _apply_metar_resolution_day_override(...)  — METAR T-group daily max (<6h lead)
  _near_boundary(loc, bucket, threshold)     — Ensemble mean near bucket edge detection
  _close_stale_positions()                   — Close expired/resolved positions
  _get_running_daily_max(station, date, u)   — METAR daily high via AWC API

CALIBRATION & BIAS:
  _fit_emos(pairs)                           — OLS: actual = a + b*forecast → (a, b, sigma)
  _fetch_wu_daily_high(station, date)        — Weather Underground scraping (resolution)
  _compute_crps(db, station, date, actual)   — Continuous Ranked Probability Score

REGIME & WEATHER CONTEXT:
  _compute_regime_boost(analyzed)            — Cross-city warm/cold regime → 1.2x
  _get_enso_regime()                         — NOAA Nino 3.4 SST anomaly
  _prefetch_severe_weather_alerts(groups)    — Batch NWS alerts (30-min cache)
  _get_severe_weather_boost(station)         — Cached boost factor (1.0-2.0)
  _get_afd_spread_factor(station)            — NWS AFD uncertainty signals
  _get_station_wfo(station)                  — NWS Weather Forecast Office lookup
  _parse_afd_uncertainty(afd_text)           — AFD keyword scanning

SIZING & RISK:
  _get_min_edge(market_type, station)        — Per-category min edge (intl → higher)
  _get_station_reliability_factor(station_id) — MSE-based sizing multiplier
  _compute_weather_drawdown_factor(mkt_type) — Kelly reduction on losing streaks
  _record_weather_outcome(market_type, won)  — Update consecutive loss counter
  _load_category_params()                    — Per-market-type params from DB
  _smoczynski_tomkins_allocate(opps, budget) — S-T optimal multi-bucket allocation

SCAN INTERVALS:
  _in_model_window()                         — Check if in NWP model update window
  _get_scan_interval_seconds()               — Adaptive: 60s/90s/120s/300s

HELPERS:
  _precip_to_temp_group(group)               — Convert Precip/Snow/Wind groups
  stop()                                     — Cleanup resources
```

---

## STATE PERSISTENCE (ALL GAPS CLOSED)
| State | Mechanism | Status |
|-------|-----------|--------|
| `_daily_exposure_usd` | `daily_counters` 60s flush + SIGTERM + startup restore | Done |
| `_group/_city_exposure` | `_restore_exposure_from_db()` from open paper_trades | Done |
| Exit cooldowns | Redis TTL `_save/_restore_exits_from_redis()` | Done |
| Open positions | `order_gateway.seed_positions_from_db()` | Done |
| Forecast 429 cooldowns | Redis persistence in `forecast_client` | Done |
| Daily P&L | `_restore_daily_pnl_from_db()` from trade_events | Done |

---

## SESSION 90 COMPLETED WORK (ALL DEPLOYED)

### Fix 1: Advisory Lock Zombie — P0 (commit `46f565e`)
**Root cause**: Python 3.13 `CancelledError` is `BaseException`, not `Exception`. `asyncio.wait_for` cancellation propagated through `acquire_lock()` finally block, skipping `except Exception`, leaving zombie advisory locks that killed the scheduler for 11+ hours.
**Fix**: `asyncio.shield()` around unlock + `except BaseException` catch in `database_lock.py`.

### Fix 2: Master Timeout + Lifecycle Logging — P1 (commit `a39e0b5`)
**Fix**: `asyncio.wait_for(self._run_ingestion(), timeout=2400)` in `ingestion_scheduler.py`. Cycle counter + timing logs. Batch size 20→100.

### Fix 3: Resolution Backfill Logging — P3 (commit `6fe26e3`)
**Fix**: 7 silent `except Exception: pass` → logged warnings in `resolution_backfill.py`.

### Fix 4: 3 Missing Cities — (commit `e18529f`)
Added Lucknow (VILK), Munich (EDDM), Tel Aviv (LLBG) to `station_registry.py`.

### Deploys
- `20260314_220707` — Fixes 1-3
- `20260314_230246` — Fix 4 (cities)
- `20260314_233541` — Bankroll config sync (local→VPS)

---

## ARTICLE ANALYSIS: WEATHERBOT vs 3RD-PARTY RESEARCH

### Already Implemented (~90% of recommendations)
WeatherBot implements 22+ of the article's core recommendations:
- 133-member ensemble (GEFS + ECMWF IFS + ECMWF AIFS)
- EMOS post-processing (per-station, per-lead-time, regime-aware)
- Isotonic tail calibration (≥50 resolved events)
- METAR T-group parsing (0.1°C precision)
- WU scraping for resolution verification (`_fetch_wu_daily_high()`)
- Resolution-day front-running via running daily max
- F→C→F rounding risk awareness (`_near_boundary()`)
- ASOS 1-minute data (IEM, US K-prefix stations)
- Adaptive scan during model release windows (60-120s vs 300s)
- Ensemble spread as uncertainty signal (Baker-McHale)
- Climate normal blending at long lead times (0-40% ramp)
- Fractional Kelly with Smoczynski-Tomkins multi-bucket
- ENSO regime detection (Nino 3.4 SST)
- Precipitation via Gamma distribution + NWS NDFD PoP blend
- DST/timezone resolution handling (`dst_calendar.py`)
- Severe weather alert integration (NWS batch fetch)
- Penny-bet filter (skip <5¢ contracts)
- NWS AFD uncertainty adjustment (ahead of article)
- CRPS scoring for ensemble evaluation
- Local hi-res models per station (ahead of article)

### Gaps Worth Implementing (Prioritized)
| Priority | Gap | Alpha | Effort | Files |
|----------|-----|-------|--------|-------|
| **P1** | Model-run-to-model-run jump detection | HIGH | 2-3h | `forecast_client.py`, `weather_bot.py` |
| **P2** | NBM CDF benchmark signal | MEDIUM-HIGH | 1-2h | `probability_engine.py`, `weather_bot.py` |
| **P3** | Geographic expansion (Great Plains corridor) | MEDIUM | 1h | `station_registry.py` |
| **P4** | Lake-effect snow / wind gust market expansion | MEDIUM | 2h | `station_registry.py`, market discovery |
| **P5** | Kalshi cross-platform arbitrage | HIGH | 8-16h | New module, new API integration |
| **P6** | NAO/AO/PNA indices | LOW-MEDIUM | 1h | `weather_bot.py` config fetch |
| **P7** | Neural EMOS | MEDIUM (future) | 16-40h | New training pipeline |

### P1 Detail: Model-Run Jump Detection
When a new GFS/ECMWF run shows ≥3°F shift for same target, markets lag by minutes to hours. Store last-run forecast per (station, model, target_date), compare on new run, compute delta. Use delta magnitude as sizing multiplier. This is "the most well-documented edge mechanism" per the article.

### P2 Detail: NBM CDF Benchmark
NBM provides calibrated probabilistic forecasts. When NBM CDF disagrees with market-implied probabilities by ≥15pp, strong signal. NBM data already fetched — just need to compute CDF per bucket and compare vs market prices.

### Gaps to SKIP
- Neural EMOS (not enough calibration data yet, need 5000+ resolved markets)
- EVT/GPD tail corrections (isotonic already handles empirically)
- NAO/AO/PNA (only for weekly/monthly markets, not yet on Polymarket)
- Order flow signals (marginal vs complexity)
- Self-host Open-Meteo (premature, free tier sufficient)
- Urban heat island adjustments (ASOS station calibration already captures)
- Analog forecasting (ERA5 reanalysis, not forecast — better for backtesting)

---

## OUTSTANDING ITEMS (WEATHERBOT)
| Priority | Item | Status |
|----------|------|--------|
| **P2** | 604 markets still unresolved in traded_markets | Genuinely open — will resolve naturally |
| **P3** | `ingest_everything()` >600s timeout observed | Master timeout (40min) catches this now |
| **P5** | Remove diagnostic logging (session_factory warning) | Low priority |
| **Future** | Model-run jump detection (P1 from article analysis) | Not started |
| **Future** | NBM CDF benchmark (P2 from article analysis) | Not started |

### Resolved Items (do not re-open)
- ~~P0: Scheduler death (11h)~~ → Fixed: advisory lock shield + master timeout
- ~~P0: RESOLUTION dedup broken~~ → Fixed: atomic INSERT...SELECT
- ~~P0: False observation mode on restart~~ → Fixed: PatchDriftDetector
- ~~P1: Resolution backfill 3 root causes~~ → Fixed: 544 markets resolved
- ~~P1: MirrorBot P&L audit~~ → Fixed: 3238 dup RESOLUTION events deleted
- ~~P1: System-wide exposure cap blocks EsportsBot~~ → Resolved naturally

---

## P&L CALCULATION (MANDATORY)
- **NEVER invert formulas for NO positions** — prices are token-specific
- `cost = entry_price * size` (ALL sides)
- `uPnL = (current - entry) * size` (ALL sides)
- **Canonical script**: `python scripts/bot_pnl.py WeatherBot hours`
- **Data sources**: `trade_events` (realized), `positions.unrealized_pnl` (mark-to-market)

### WeatherBot P&L Snapshot (Session 90)
- **Realized**: +$910 (156/643 markets resolved)
- **Strategy**: Primarily bets NO (525/727 = 72%), exploiting favourite-longshot bias
- **NO win rate**: 71.7% | **YES win rate**: 16.7% (expected distribution)
- **Paris**: -$384 (poor performer, mostly YES-side losses)

---

## INFRASTRUCTURE

### VPS
- **Host**: Ubuntu-3 at 34.251.224.21 (16GB/4vCPU)
- **SSH key**: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **Service**: `polymarket-ai` (systemd)
- **Working dir**: `/opt/polymarket-ai-v2` (symlink to release)
- **Env file**: `/opt/pa2-shared/.env`
- **Python**: 3.13

### Deploy
```bash
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
# Rollback:
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```

### Post-Deploy Verification
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
sudo journalctl -u polymarket-ai -f | grep -iE "WeatherBot|weather|scan_cycle"
```

### Tests
```bash
pytest  # All 1676+ tests must pass
pytest tests/unit/test_weather_bot.py -v  # WeatherBot-specific
```

---

## DAILY COUNTERS WRITE PATTERN
- **ABSOLUTE-SET**: OrderGateway `daily_exposure_usd` — `counter_value = total` via `_flush_daily_exposure()`
- Do NOT use `asyncio.create_task()` for financial write-throughs — fire-and-forget means DB errors silently corrupt. Always `await`.

---

## MEMORY FILES (persistent across sessions)
Located at `C:\Users\samwa\.claude\projects\C--lockes-picks-polymarket-ai-v2\memory\`
- `MEMORY.md` — Master index (loaded into context automatically)
- `architecture.md` — Full system architecture reference
- `deep_dive_implementation.md` — Sessions 63-64 scaling playbook
- `feedback_bot_sessions.md` — Bot-scoped session rules
- `feedback_pnl_math.md` — P&L formula rules (NEVER invert NO)
- `feedback_scope_lock.md` — Zero unsolicited features
- `session_history.md` — Sessions 72-83 commit archive

---

## SESSION HISTORY (WEATHERBOT)
| Session | Date | Key Work |
|---------|------|----------|
| 61 | 2026-03-08 | Initial WeatherBot build |
| 62 | 2026-03-08 | Bankroll sizing, station registry |
| 67 | 2026-03-09 | Multi-model ensemble, EMOS |
| 69 | 2026-03-09 | Precipitation engine, Gamma distribution |
| 80 | 2026-03-12 | Resolution-day METAR override |
| 81 | 2026-03-12 | WU scraping, tail calibration |
| 85 | 2026-03-13 | 3 root cause fixes, 544 markets resolved |
| 87 | 2026-03-14 | RESOLUTION dedup fix, P&L correction |
| 90 | 2026-03-14 | Advisory lock fix, master timeout, 3 cities, article analysis |

---

## RECENT COMMITS (WeatherBot-relevant)
```
e18529f feat(weather): add Lucknow, Munich, Tel Aviv to station registry
6fe26e3 fix(resolution): log silent exception blocks in resolution_backfill
a39e0b5 fix(scheduler): master timeout + lifecycle logging for IngestionScheduler
46f565e fix(lock): shield advisory lock release from CancelledError
```

---

## WHAT THE NEXT SESSION SHOULD DO

The system is stable and profitable (+$910 realized). The two highest-value improvements from the article analysis are:

1. **P1: Model-run jump detection** — Store last-run forecast per (station, model, target_date) in `forecast_client.py`. Compare on new run. Compute delta. Pass delta to `weather_bot.py` as sizing multiplier. When models jump ≥3°F, markets lag — this is the #1 documented edge mechanism.

2. **P2: NBM CDF benchmark** — NBM data already fetched. In `probability_engine.py`, compute NBM's CDF for each temperature bucket. Compare against market prices. When NBM disagrees with market by ≥15pp, flag as high-conviction opportunity.

Both are additive (don't change existing logic) and low-risk.

**Or**: User may have other priorities. Follow their instructions. Scope lock applies.
