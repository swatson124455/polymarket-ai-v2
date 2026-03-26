# WeatherBot Session 115 — Full Agent Handoff Prompt

You are continuing work on the **WeatherBot** module of a 15-bot Polymarket automated trading system. You are **scope-locked to WeatherBot** — no cross-bot changes unless explicitly demanded by the user.

**Before you write ANY code:** Read `CLAUDE.md` in repo root. State the bug. List files you'll touch. Grep dependents. Read the entire file. This is mandatory, not optional.

**Key governance files:**
- `CLAUDE.md` — Prime directive, rules of engagement, forbidden patterns
- `memory/feedback_scope_lock.md` — NEVER add unsolicited features
- `memory/feedback_bot_sessions.md` — Bot-scoped session rules
- `memory/feedback_pnl_math.md` — P&L formula rules (NEVER invert for NO side)

---

## WHAT IS THIS SYSTEM?

A 15-bot automated Polymarket trading system. Currently in **paper trading mode** (`SIMULATION_MODE=true`). Paper trading IS production — the only difference from live is the final order submission. $20K capital allocated, $300 max bet per bot.

### Bot Roster
| Bot | Purpose | Active |
|-----|---------|--------|
| **WeatherBot** | Temperature/precip/snow/wind bucket markets via NOAA ensembles | YES — YOUR FOCUS |
| MirrorBot | Copy-trades elite Polymarket wallets via RTDS feed | YES |
| EsportsBot | Pre-match esports (LoL/CS2/Valorant/Dota2) via PandaScore + Glicko | YES |
| EsportsLiveBot / EsportsSeriesBot | In-play and series-level esports | YES |
| 10 others | Sports, ensemble, arbitrage, etc. | Mostly inactive |

### Infrastructure
- **VPS**: Ubuntu at 34.251.224.21 (16GB/4vCPU), SSH key `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **Deploy**: `bash deploy/deploy.sh` — atomic symlink swap at `/opt/polymarket-ai-v2`
- **Service**: `systemctl` unit `polymarket-ai`, logs via `journalctl -u polymarket-ai -f`
- **DB**: PostgreSQL `polymarket` (asyncpg), Redis for caching
- **Python 3.13** — CRITICAL: `from X import Y` inside function body shadows module-level

---

## WEATHERBOT STRATEGY (how it makes money)

1. **Fetch** GFS/HRRR/GEFS + ECMWF ensemble forecasts via Open-Meteo (free, no API key) — 133 ensemble members total (31 GEFS + 51 IFS + 51 AIFS)
2. **Fit** skew-normal distribution to ensemble spread (with EMOS calibration if available)
3. **Integrate** CDF across each temperature bucket's bounds → model probabilities
4. **Compare** model probs vs market-implied probs (YES prices on Polymarket)
5. **Trade** when edge >= 8% US / 12% international, sized by fractional Kelly (0.25)
6. **Hold** until resolution, TP/SL exit, or model reversal

### Scan Loop Structure (`scan_and_trade()`)
1. Discover weather markets via tag-based API fetch
2. Group by city+date → `WeatherMarketGroup`
3. Analyze each group (ensemble fetch → EMOS calibration → probability → edge detection)
4. Also scan precipitation, snowfall, wind markets
5. Execute trades for opportunities passing all filters
6. Re-evaluate open positions for exits

---

## WEATHERBOT FILE MAP

### Primary Bot File
| File | Lines | Purpose |
|------|-------|---------|
| `bots/weather_bot.py` | ~4300 | Main bot — scan loop, analysis, trading, state, monitoring, calibration |

### Supporting Modules (under `base_engine/weather/`)
| Module | Lines | Purpose |
|--------|-------|---------|
| `forecast_client.py` | ~1540 | Open-Meteo API wrapper, GFS/HRRR/GEFS/ECMWF ensemble fetching, historical bias bootstrap, Redis cache |
| `probability_engine.py` | ~520 | Skew-normal CDF integration, bucket probability computation, EMOS calibration, global EMOS fallback |
| `precipitation_engine.py` | ~231 | Precipitation gamma-distribution model, NDFD POP integration |
| `market_mapper.py` | ~1135 | Question parsing → 8 dataclasses (Temp/Precip/Snow/Wind buckets + groups), 22 regex patterns |
| `station_registry.py` | ~1550 | 106 stations (US+intl+4 Chinese), ICAO/WMO codes, aliases, health monitoring |
| `model_run_monitor.py` | ~200 | GFS/ECMWF/HRRR model run tracking, priority queue |
| `metar_monitor.py` | ~150 | Real-time METAR observation polling, boundary crossing detection |
| `metar_client.py` | ~100 | METAR API client |

### Shared Modules (changes affect ALL 15 bots — REQUIRE full blast-radius analysis)
| Module | WeatherBot-specific notes |
|--------|--------------------------|
| `base_engine/execution/paper_trading.py` | Taker-side factor, alpha decay via `scan_start_mono`, fill probability model |
| `base_engine/execution/order_gateway.py` | Volume fallback from event_data, ghost position guard |
| `config/settings.py` | All WEATHER_* settings, PAPER_TAKER_SIDE_FACTOR |
| `base_engine/risk/bankroll_manager.py` | `BotBankrollManager` handles SIZING |
| `base_engine/risk/risk_manager.py` | Handles LIMITS (deprecated for sizing) |

---

## KEY STATE DICTIONARIES

```python
_group_exposure: Dict[str, float]       # "city:date" → USD deployed (lock-protected)
_city_exposure: Dict[str, float]        # city → total USD deployed
_recently_exited: Dict[str, float]      # market_id → monotonic time (4hr cooldown)
_fill_fail_tracker: Dict[str, Tuple]    # market_id → (consec_fails, last_mono)
_market_group_cache: Dict[str, Tuple]   # market_id → (group_key, city, cost_usd)
_daily_pnl: float                       # today's realized P&L
_scan_start_mono: float                 # monotonic time at scan start (for alpha decay)
_consecutive_no_edge: int               # adaptive backoff counter (Redis-persisted)
# S114 additions:
_spread_history: Dict[str, deque]       # station_id → 14-day model_spread rolling window
_station_n_resolved: Dict[str, int]     # station_id → resolved forecast-actual pairs
_bootstrapped_stations: Set[str]        # stations already bootstrapped this session
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
   - **S114: Bühlmann calibration confidence** (0.0-1.0x from `n/(n+30)`)
3. **Min trade floor**: `WEATHER_MIN_TRADE_USD=5.0`
4. **Exposure locks**: Atomic reservation under `_exposure_lock` for group + city
5. **bestAsk pre-filter**: Skips trade if `confidence <= bestAsk` (no edge after depth)

---

## EMOS CALIBRATION SYSTEM (S114 architecture)

### How EMOS Works
- **Keys**: `(station_id, lead_time_hours)` where lead_time bucketed in 6h intervals
- **Formula**: `μ_corrected = a + b·X̄`, `σ_emos = sigma`
- **Fitting**: OLS regression on (forecast_temp, actual_temp) pairs via `_fit_emos()`
- **Minimum**: 20 pairs per (station, lead_bucket) to activate local EMOS
- **Reload**: Every 6 hours from `weather_calibration` table in `_maybe_reload_calibration()`
- **Regime-aware**: Optionally splits by ENSO regime (el_nino/la_nina/neutral) when ≥20 regime-specific pairs

### S114 Cold-Start Mitigation (4-layer stack)

**Layer 1 — Spread Confidence Gate** (`_get_min_edge()`):
- `effective_min_edge = base_min_edge * clamp(σ_current / σ_typical_14d, 0.7, 1.5)`
- Zero training data needed, works from first scan
- `_spread_history` dict tracks rolling 14-day model_spread per station

**Layer 2 — Bühlmann Sizing Ramp** (`_calibration_confidence()`):
- `w = n/(n+30)` where n = resolved pairs per station
- n<5 blocks trading entirely; n=15: 33% size; n=30: 50%; n=120: 80%
- Returns 1.0 if `_station_n_resolved` is empty (pre-first-reload)
- `_station_n_resolved` refreshed from `weather_calibration` table every 6h

**Layer 3 — Global EMOS Baseline** (`probability_engine._global_emos`):
- Pool ALL stations' data → fit single global EMOS (a,b,σ)
- Fallback chain in `_get_emos_params()`: local → global → bias offset → identity
- `load_global_emos()` on `WeatherProbabilityEngine`
- ~10% worse than local EMOS but infinitely better than no calibration

**Layer 4 — Historical Bias Bootstrap** (`_maybe_bootstrap_cold_station()`):
- On first encounter of cold station (n<5), fetches 90d GFS forecasts + ERA5 actuals from Open-Meteo
- `fetch_historical_bias()` in `WeatherForecastClient`
- Inserts into `weather_calibration` table with `model_name='bootstrap_gfs'`
- Forces `_calibration_last_loaded = 0.0` for immediate EMOS reload
- Gives n≈90 immediately → w=0.75 (75% sizing) from day 1

### DB Tables
- `weather_calibration` (station_id, target_date, forecast_temp, actual_temp, lead_time_hours, bias, model_name, crps, regime)
- `weather_tail_calibration` (bucket_type, lead_time_bucket, model_prob, actual_outcome, station_id)
- `weather_forecasts` (station_id, target_date, ensemble_members JSONB, deterministic_high, model_spread, models_used)

---

## FILL PROBABILITY MODEL (paper_trading.py)

### 5 Multiplicative Factors
1. **Price-depth** (`0.3 + 0.7 * 4*p*(1-p)`): Best at 0.50, worst at extremes
2. **Size-impact** (`1 - 0.4*(size/max_size)`): Larger = worse fill
3. **Spread factor** (`max(0.1, 1 - spread*10)`): Wide spread = low fill
4. **Time-of-day** (US hours best, nights/weekends worst)
5. **Sqrt participation** (`sqrt(volume * participation / 10000)`)

### Additional: Taker-side 0.85x, Kyle's lambda ~0.7x, alpha decay (BUY-only), resolution proximity penalties

---

## P&L DATA MODEL

- **Authoritative source**: `trade_events` table (NEVER `paper_trades`)
- **Event types**: ENTRY, EXIT, RESOLUTION
- **Formulas** (ALL sides, NEVER invert for NO):
  - `cost = entry_price * size`
  - `uPnL = (current_price - entry_price) * size`
- **Canonical script**: `python scripts/bot_pnl.py WeatherBot 24`

---

## CURRENT SYSTEM STATE (as of S114 deploy)

- **Open positions**: ~193 ($6,301 deployed)
- **All-time realized P&L**: ~+$2,960
- **Fill rate**: ~14.7%
- **Deploy**: `20260321_120217` — LIVE, healthy
- **26 active cities** + 4 new Chinese cities (Chengdu, Chongqing, Shenzhen, Wuhan)
- **Station count**: 106 (25 US, 8 international pre-S113, +4 Chinese in S113)

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
PAPER_TAKER_SIDE_FACTOR=0.85             # Taker fill discount (all bots)
SIMULATION_MODE=true                     # Paper trading
ALL BOTS: capital=$20000, max_bet=$300, max_daily=$10000, kelly=0.25
```

---

## KEY DATA INSIGHTS

### Side Performance
- **NO side**: 72% WR, +$1,896 — primary profit driver (favourite-longshot bias)
- **YES side**: 39% WR, +$985
- Combined: 62% WR, profitable

### Hold Duration Sweet Spot
- **24-48h holds** are best: 73% WR
- **0-2h exits**: 42% WR — early exits lose
- **48h+ holds**: 62-73% WR — weather signals converge over time

### Fill Pipeline (post-S108)
- Fill rate improved from 8% to ~14.7%
- bestAsk pre-filter catching ~27% of would-fail trades
- Same-side dedup preventing 700+ duplicate entries

---

## REVIEW: NEW CITY ONBOARDING PROCESS (S113/S114)

When Polymarket adds new cities, the following must happen:

### Step 1: Station Registry Entry (`station_registry.py`)
Add a `WeatherStation` entry with: city_name, ICAO station_id, GHCND id, lat/lon, elevation_m, timezone, temp_unit (F/C), aliases tuple, resolution_source, has_asos_1min, local_model (if international with hi-res model available).

### Step 2: Market Matching
`market_mapper.py` parses question text → extracts city name → `lookup_station()` matches via aliases. If no match → logged as `weatherbot_unmatched_city` and skipped.

### Step 3: Cold-Start Mitigation (automatic via S114)
1. `_maybe_bootstrap_cold_station()` triggers on first encounter (n<5) → fetches 90d historical bias from Open-Meteo → inserts into `weather_calibration`
2. Global EMOS provides immediate fallback calibration
3. Bühlmann ramp starts sizing at ~75% (with bootstrap n≈90) instead of blocking
4. Spread gate adapts edge threshold from first scan

### Step 4: Verify Post-Deploy
```bash
journalctl -u polymarket-ai -f | grep "weatherbot_bootstrap\|weatherbot_cold_start"
```

### Known Gap: Historical bias bootstrap uses deterministic GFS (not ensemble members), so EMOS sigma is approximated. Full ensemble calibration still needs 20+ resolved market days per station.

---

## OUTSTANDING AUDIT FINDINGS

### TIER 2 — Fix Soon
| Item | Status | Description |
|------|--------|-------------|
| 2D | Open | Baker-McHale post-cap ordering — BM applied AFTER 2.0 combined_boost cap loses granularity |
| 2E | **FIXED S113** | Negative daily counter restore |
| 2B | **FIXED S113** | Cache jitter direction |
| 2C | **FIXED S113** | Gamma clamp logging |

### TIER 3 — Backlog
| Item | Effort | Description |
|------|--------|-------------|
| 3A | Multi-commit | ~15 hardcoded values need env var configs |
| 3B | Full session | Test coverage ~40-50%. Zero tests for many edge cases |
| 3C | Feature | Brier score / calibration decomposition per-city/lead-time/season |
| 3D | Feature | Multi-city correlation (NYC+Boston ~0.6 temp correlation) |
| 3E | Feature | Severe weather suspension |
| 3F | Feature+script | Slippage monitoring |
| 3G | Refactor | Precip/snow/wind DRY — market fetching duplicated |

### TIER 4 — Monitor
| Item | Watch for | Trigger |
|------|-----------|---------|
| BM sizing distribution | >30% trades hit BM floor 0.50 | Log pre/post values |
| NBM >30pp disagreement | Win rate <40% on boosted trades | Pull outcomes |
| Dallas/Wellington P&L | Still negative at 30+ samples | Track per city |
| Chinese cities performance | First 30+ resolutions | Monitor calibration convergence |
| Cold-start bootstrap accuracy | Bootstrap bias vs actual first resolutions | Compare at day 7, 14, 30 |

---

## FUTURE ROADMAP (from deep research, S114)

### P2: SAMOS (Standardized Anomaly EMOS)
Target architecture upgrade for global EMOS. Normalize forecasts/actuals by ERA5 climatological mean/std before fitting. Eliminates station-specific effects — Austrian Weather Service runs this operationally on 1km grid. **Prereq**: ERA5 climatology lookup per station. **When**: After global EMOS validated (1-2 weeks).

### P3: Climate-Cluster Semi-Local EMOS
Cluster stations by climatological quantile features (Lerch & Baran 2017). New stations assigned to nearest cluster. **Prereq**: 50+ stations. **When**: If Polymarket expands.

### P3: Grouped Per-Model EMOS
Fit `μ = a + b_GFS·X̄_GFS + b_IFS·X̄_IFS + b_AIFS·X̄_AIFS` (6 params). Learns relative model skill. **Prereq**: Per-member model tags in CombinedForecast. **When**: After confirming blended EMOS leaves skill on table.

### P4: Nearest-Station Transfer
Initialize new station EMOS from elevation-adjusted nearest analog (100m vertical ≈ 15km horizontal). **When**: After global EMOS, if too generic.

### P4: City Rotation Prediction
Track Polymarket city presence matrix → predict additions → pre-compute bias. Historical pattern: ~7 core persistent + ~9 rotating cities. **When**: After bootstrap pipeline is validated.

### P5: MEMOS (Spatial EMOS)
Full Gaussian Markov Random Field. Needs 50+ stations + R-INLA. **When**: If station count exceeds 100.

### P5: Neural Network Post-Processing
Station embeddings (Rasp & Lerch 2018). **When**: 6+ months data across 50+ stations.

---

## INVALIDATED FINDINGS (do NOT re-investigate)

1. ~~Precipitation engine not wired~~ — IS wired via `_scan_precipitation_markets()`
2. ~~Wind/snow trading disconnected~~ — Both wired via scan functions
3. ~~NaN/Inf ZeroDivisionError~~ — `probability_engine.py` guards `len(clean) < 2`
4. ~~Confidence formula inverted~~ — `1.0 - model_prob` IS correct for NO-side
5. ~~Race condition in concurrent `_analyze_group()`~~ — asyncio is single-threaded cooperative
6. ~~Model cache serves week-old data~~ — 30-min TTL prevents staleness

---

## CRITICAL TRAPS (28 items — DO NOT BREAK)

1. **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
2. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL
3. **VPS deploys via `deploy.sh`**: atomic symlink swap. Working tree != VPS != git HEAD
4. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
5. **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager used
6. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
7. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
8. **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
9. **Python 3.13 scoping**: `from X import Y` inside function body → entire function shadows Y
10. **websockets v15**: `import websockets.exceptions` must be explicit
11. **Paper trading IS production** — never skip features because "we're only paper trading"
12. **positions table**: NO `closed_at`, NO `updated_at` columns
13. **prediction_log columns**: NO `rejection_reason` — use `trade_executed` flag
14. **`traded_markets.bot_names`**: TEXT column (not array), use `LIKE '%BotName%'`
15. **Alpha decay requires `scan_start_mono` in event_data**: Only WeatherBot passes it
16. **`paper_trades` has NO `metadata` JSONB column**
17. **Resolution backfill excludes SELL trades** — SELL P&L computed at exit time
18. **trade_events immutability trigger**: Must DISABLE/ENABLE for data cleanup
19. **RESOLUTION event idempotency**: ON CONFLICT broken on partitioned tables → atomic INSERT...SELECT
20. **Ghost positions fixed**: Idempotent memory returns `success: False`, order gateway guards `_filled_size > 0`
21. **`_market_group_cache`**: 3-tuple `(group_key, city, cost_usd)` — NEVER expand without updating all consumers
22. **Alpha decay is BUY-only** (S104b fix). DO NOT remove `side == "BUY"` gate
23. **`_close_stale_positions()` does direct DB UPDATE** — no trade_events EXIT record (by design)
24. **Exposure reserved BEFORE `place_order()` under `_exposure_lock`**, reverted on failure
25. **`event_data` dict mutated in-place** by paper_trading.py before DB write
26. **`WEATHER_SKIP_COORDINATOR_BUY=True`** — confirm_position() does direct INSERT
27. **trade_events JSONB column is `event_data`** — NOT `metadata_json`
28. **`_calibration_confidence()` returns 1.0 when `_station_n_resolved` is empty** — prevents false blocks pre-first-reload

---

## VERIFICATION COMMANDS

```bash
# WeatherBot scan health
journalctl -u polymarket-ai -f | grep weatherbot_scan_done

# Cold-start mitigation (S114)
journalctl -u polymarket-ai -f | grep "weatherbot_cold_start\|weatherbot_bootstrap\|weatherbot_global_emos"

# Calibration confidence in trades
journalctl -u polymarket-ai -f | grep "weatherbot_trade_signal"

# Edge cap rejection rate
journalctl -u polymarket-ai --since '1 hour ago' | grep -c "weatherbot_edge_cap"

# Fill rate
journalctl -u polymarket-ai --since '30 min ago' | grep 'Order latency.*Weather'

# P&L
PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/bot_pnl.py WeatherBot 24

# Positions
sudo -u postgres psql -d polymarket -c "SELECT status, count(*), round(avg(entry_cost)::numeric, 2) FROM positions WHERE bot_id='WeatherBot' GROUP BY status;"

# Daily counters
sudo -u postgres psql -d polymarket -c "SELECT counter_name, counter_value FROM daily_counters WHERE bot_id='WeatherBot' AND counter_date=CURRENT_DATE ORDER BY counter_name;"
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
| S114 | 03-21 | EMOS cold-start mitigation: spread gate, Bühlmann ramp, global EMOS, historical bootstrap |

---

## LATEST COMMITS

```
325e0f2 feat(weather): S114 — EMOS cold-start mitigation: 4 changes
32daca8 fix(weather): S113 — 2E negative counter, 2B cache jitter, 2C gamma log, 4 Chinese cities
4f178e1 fix(esports): S112 — disable Brier halt so all games trade
85e3ba1 perf(esports): S110 — OPT-4 retrain parallelization
454e616 fix(weather): S109 — sizing units bug: pass shares not USD to place_order
```
