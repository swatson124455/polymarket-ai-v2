# AGENT HANDOFF — WeatherBot Session 92
**Date**: 2026-03-15
**Bot Scope**: WeatherBot ONLY (scope lock: non-negotiable)
**Prior Session**: Session 91 (handoff doc written, no code changes)
**Prior Code Session**: Session 90 (advisory lock fix, master timeout, 3 cities)

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

## SESSION 92 COMPLETED WORK (DEPLOYED)

### Deploy `20260315_145118` — All changes live on VPS

### Feature 1: Model-Run Jump Detection (P1)
**Files**: `forecast_client.py`, `weather_bot.py`, `settings.py`

**What it does**: When a new GFS/ECMWF model run shifts the ensemble mean by ≥3°F for the same (station, date), markets lag by minutes-to-hours. This detects the shift and applies a sizing boost.

**Implementation**:
- `CombinedForecast` dataclass: added `forecast_delta: Optional[float]` field
- `WeatherForecastClient._prior_forecasts`: dict storing previous ensemble mean per `(station, date)` with 24h TTL pruning
- `get_combined_forecast()`: computes delta vs prior cache cycle, logs `weatherbot_model_run_jump` when |delta| ≥ 1°F
- `_execute_weather_trade()`: applies sizing boost when |delta| ≥ `WEATHER_JUMP_THRESHOLD_F` (default 3°F). Linear scale up to `WEATHER_JUMP_MAX_BOOST` (default 1.5×). Integrated into `combined_boost` at 0.5× diminishing returns, capped at 2.0×
- First scan seeds `_prior_forecasts` — jump detection activates on the second+ cache cycle (by design)

**Settings**:
- `WEATHER_JUMP_THRESHOLD_F=3.0` — °F shift to trigger boost
- `WEATHER_JUMP_MAX_BOOST=1.5` — max sizing multiplier from jump

### Feature 2: NBM CDF Benchmark Signal (P2)
**Files**: `probability_engine.py`, `weather_bot.py`, `settings.py`

**What it does**: For US stations where NBM is available, computes NBM-implied bucket probabilities and compares against market prices. When NBM disagrees with the market by ≥15pp, flags the opportunity as high-conviction and applies a 1.3× sizing boost.

**Implementation**:
- `WeatherProbabilityEngine.compute_nbm_benchmark()`: models NBM as N(nbm_high, sigma) where sigma scales with lead time (1.5°F day-1 → 5.0°F day-4+). Computes CDF per bucket, normalizes, compares vs market prices
- `_analyze_group()`: runs NBM benchmark for US stations (when `"nbm" in forecast.models_used`), logs `weatherbot_nbm_benchmark` with signal count and best edge
- `_execute_weather_trade()`: applies 1.3× `nbm_boost` for high-conviction markets, integrated into `combined_boost` at 0.5× diminishing returns

**Settings**:
- `WEATHER_NBM_DISAGREE_THRESHOLD=0.15` — 15pp disagreement threshold

### P5: Diagnostic Logging — SKIPPED
The `session_factory` warning is in `prediction_engine.py` (shared module). Per scope lock, not touched.

### Post-Deploy Verification
- NBM benchmark firing: Chicago (58°F, 2 signals), Miami (82°F, 2 signals, 65pp edge)
- Jump detection seeded `_prior_forecasts` on first scan — will fire on next model run change
- 1622 tests passed, 0 failures

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
Jump threshold:   3.0°F (sizing boost on model-run shift)
Jump max boost:   1.5× (max sizing multiplier from jump)
NBM disagree:     15pp (high-conviction threshold)
```

---

## WEATHERBOT ARCHITECTURE

### Data Flow (per scan cycle)
```
1. DISCOVERY     → Gamma API tag_slug=temperature → WeatherMarketMapper groups by (city, date)
2. FORECASTING   → WeatherForecastClient → Open-Meteo GFS/GEFS/ECMWF/HRRR ensembles (133 members)
3. CALIBRATION   → EMOS (a + b·X̄, σ) per station/lead-time/regime + isotonic tail calibration
4. PROBABILITY   → WeatherProbabilityEngine → skew-normal CDF integration per bucket
5. NBM BENCHMARK → compute_nbm_benchmark() → N(nbm_high, σ) CDF per bucket (US stations only)
6. EDGES         → model_prob - market_price, sorted by |edge|
7. REGIME        → ENSO (Nino 3.4), cross-city warm/cold detection, severe weather alerts, AFD uncertainty
8. JUMP DETECT   → forecast_delta vs prior model run → sizing boost when |delta| ≥ 3°F
9. SIZING        → Fractional Kelly (0.25) × regime boost × near-expiry boost × jump boost × NBM boost
                    × drawdown compression × Smoczynski-Tomkins multi-bucket × Baker-McHale uncertainty
10. RISK CHECKS  → group exposure cap, city exposure cap, daily loss limit, position count limit
11. EXECUTION    → place_order(side="YES"/"NO") → PaperTradingEngine → paper_trades + trade_events
12. RESOLUTION   → METAR T-group override (<6h) + WU scraping + resolution_backfill
13. PERSISTENCE  → Redis (exit cooldowns, 429 cooldowns), DB (exposure, P&L), daily_counters
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
- **Model-run jump detection**: Store prior ensemble mean per (station, date), compare on new fetch, boost sizing when |delta| ≥ 3°F
- **NBM CDF benchmark**: N(nbm_high, σ) where σ scales with lead time. Flag when |NBM_prob - market| ≥ 15pp

### File Map (WeatherBot-specific)
```
bots/weather_bot.py                              (~3,720 lines) — Main bot: scan, analyze, trade, calibrate
base_engine/weather/station_registry.py          (1,447 lines) — 50+ city registry (ICAO, GHCND, aliases, models)
base_engine/weather/forecast_client.py           (~1,380 lines) — Multi-model ensemble fetching + jump detection
base_engine/weather/market_mapper.py             (1,105 lines) — Market text → TemperatureBucket/Group parsing
base_engine/weather/probability_engine.py        (~500 lines)  — Skew-normal CDF, EMOS, isotonic tail, Kelly, NBM benchmark
base_engine/weather/metar_client.py              (236 lines)   — Aviation Weather Center METAR API
base_engine/weather/precipitation_engine.py      (231 lines)   — Gamma distribution for precip/snow/wind
base_engine/weather/asos_onemin_client.py        (145 lines)   — IEM 1-minute ASOS data (US only)
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
| Prior forecasts (jump detect) | In-memory only (loss = 1 scan re-seed, not financial risk) | Done |

---

## OUTSTANDING ITEMS (WEATHERBOT)
| Priority | Item | Status |
|----------|------|--------|
| **P2** | 604 markets still unresolved in traded_markets | Genuinely open — will resolve naturally |
| **P3** | `ingest_everything()` >600s timeout observed | Master timeout (40min) catches this now |
| **P5** | Remove diagnostic logging (session_factory warning) | Shared module — not in WeatherBot scope |
| **Future** | Geographic expansion (Great Plains corridor) | P3 from article analysis |
| **Future** | Lake-effect snow / wind gust market expansion | P4 from article analysis |
| **Future** | Kalshi cross-platform arbitrage | P5 from article analysis (8-16h effort) |

### Resolved Items (do not re-open)
- ~~P1: Model-run jump detection~~ → **DONE**: Session 92. `forecast_client.py` + `weather_bot.py`
- ~~P2: NBM CDF benchmark~~ → **DONE**: Session 92. `probability_engine.py` + `weather_bot.py`
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

### WeatherBot P&L Snapshot (Session 92)
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
sudo journalctl -u polymarket-ai -f | grep -iE "WeatherBot|weather|scan_cycle|nbm_benchmark|model_run_jump"
```

### Tests
```bash
pytest  # All 1622+ tests must pass
pytest tests/unit/test_weather_bot.py -v  # WeatherBot-specific (123 tests)
```

---

## DAILY COUNTERS WRITE PATTERN
- **ABSOLUTE-SET**: OrderGateway `daily_exposure_usd` — `counter_value = total` via `_flush_daily_exposure()`
- Do NOT use `asyncio.create_task()` for financial write-throughs — fire-and-forget means DB errors silently corrupt. Always `await`.

---

## RECENT COMMITS (WeatherBot-relevant)
```
(working tree) feat(weather): P1 model-run jump detection + P2 NBM CDF benchmark
e18529f feat(weather): add Lucknow, Munich, Tel Aviv to station registry
6fe26e3 fix(resolution): log silent exception blocks in resolution_backfill
a39e0b5 fix(scheduler): master timeout + lifecycle logging for IngestionScheduler
46f565e fix(lock): shield advisory lock release from CancelledError
```

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
| 92 | 2026-03-15 | Model-run jump detection + NBM CDF benchmark (deployed) |

---

## WHAT THE NEXT SESSION SHOULD DO

All article-identified alpha improvements are now implemented. Remaining items are:

1. **P3: Geographic expansion** — Add Great Plains corridor stations (Oklahoma City, Wichita, Omaha) to `station_registry.py`. High thermal volatility = more edge.

2. **P4: Lake-effect / wind markets** — Expand snowfall and wind gust market discovery. Station coverage for lake-effect zones (Buffalo, Cleveland, Erie).

3. **P5: Kalshi cross-platform arbitrage** — New module, new API integration. 8-16h effort. High alpha but high complexity.

4. **Monitor P1/P2 impact** — After 24-48h, check logs for:
   - `weatherbot_model_run_jump` — how often are jumps detected? What delta magnitudes?
   - `weatherbot_nbm_benchmark` — how many high-conviction signals per scan? Do they correlate with wins?
   - `weatherbot_jump_boost` — is the boost firing on trades?

**Or**: User may have other priorities. Follow their instructions. Scope lock applies.
