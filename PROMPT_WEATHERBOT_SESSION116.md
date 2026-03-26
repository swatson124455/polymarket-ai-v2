# WeatherBot Session 116 — Full Agent Handoff Prompt

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
2. **Fit** skew-normal distribution to ensemble spread (with EMOS/SAMOS calibration if available)
3. **Integrate** CDF across each temperature bucket's bounds → model probabilities
4. **Compare** model probs vs market-implied probs (YES prices on Polymarket)
5. **Trade** when edge >= 8% US / 12% international, sized by fractional Kelly (0.25)
6. **Hold** until resolution, TP/SL exit, or model reversal

### Scan Loop Structure (`scan_and_trade()`)
1. Discover weather markets via tag-based API fetch
2. Group by city+date → `WeatherMarketGroup`
3. Analyze each group (ensemble fetch → EMOS/SAMOS calibration → probability → edge detection)
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
| `market_mapper.py` | ~1135 | Question parsing → 8 dataclasses (Temp/Precip/Snow/Wind buckets + groups), 22 regex patterns |
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
_group_exposure: Dict[str, float]       # "city:date" → USD deployed (lock-protected)
_city_exposure: Dict[str, float]        # city → total USD deployed
_recently_exited: Dict[str, float]      # market_id → monotonic time (4hr cooldown)
_fill_fail_tracker: Dict[str, Tuple]    # market_id → (consec_fails, last_mono)
_market_group_cache: Dict[str, Tuple]   # market_id → (group_key, city, cost_usd)
_daily_pnl: float                       # today's realized P&L
_scan_start_mono: float                 # monotonic time at scan start
_consecutive_no_edge: int               # adaptive backoff counter (Redis-persisted)
# S114 additions:
_spread_history: Dict[str, deque]       # station_id → 14-day model_spread rolling window
_station_n_resolved: Dict[str, int]     # station_id → resolved forecast-actual pairs
_bootstrapped_stations: Set[str]        # stations already bootstrapped this session
# S115 additions:
_climatology: Dict[str, Dict[int, Tuple]]  # station_id → {doy: (clim_mean, clim_std)}
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
   - Bühlmann calibration confidence (0.0-1.0x from `n/(n+k)`, k=`WEATHER_BUHLMANN_KAPPA`)
   - **S115 fix**: 2.0x cap now applied AFTER all factors (was before BM/station/calibration)
3. **Min trade floor**: `WEATHER_MIN_TRADE_USD=5.0`
4. **Exposure locks**: Atomic reservation under `_exposure_lock` for group + city
5. **bestAsk pre-filter**: Skips trade if `confidence <= bestAsk` (no edge after depth)
6. **Severe weather halt**: Blocks trade if NWS hurricane/tornado/extreme wind warning active (US only)

---

## EMOS/SAMOS CALIBRATION SYSTEM (S114+S115 architecture)

### How EMOS Works
- **Keys**: `(station_id, lead_time_hours)` where lead_time bucketed in 6h intervals
- **Formula**: `μ_corrected = a + b·X̄`, `σ_emos = sigma`
- **Fitting**: OLS regression on (forecast_temp, actual_temp) pairs via `_fit_emos()`
- **Minimum**: 20 pairs per (station, lead_bucket) to activate local EMOS
- **Reload**: Every `WEATHER_CALIBRATION_RELOAD_SECS` (default 6h) from `weather_calibration` table

### SAMOS (S115)
- **What**: Standardized Anomaly MOS — normalizes forecasts/actuals by ERA5 climatological (mean, std) before global EMOS fitting
- **Why**: Chengdu 30°C and Helsinki 5°C become comparable anomalies. Global fit captures cross-station bias structure without station-specific effects.
- **Data source**: `weather_climatology` table (populated by `backfill_climatology.py`)
- **Formula**: `z = (temp - clim_mean) / clim_std`, then `μ_corrected = clim_mean + clim_std * (a + b * z_forecast)`
- **Recency weighting**: Years 0-2 = full weight, then 0.85 decay per year. `clim_std` floored at 1.0.

### Cold-Start Mitigation (4-layer stack from S114)

**Layer 1 — Spread Confidence Gate** (`_get_min_edge()`):
- `effective_min_edge = base_min_edge * clamp(σ_current / σ_typical_14d, 0.7, 1.5)`
- Zero training data needed, works from first scan

**Layer 2 — Bühlmann Sizing Ramp** (`_calibration_confidence()`):
- `w = n/(n+k)` where n = resolved pairs, k = `WEATHER_BUHLMANN_KAPPA` (default 30)
- n<5 blocks trading; n=15: 33% size; n=30: 50%; n=120: 80%

**Layer 3 — Global EMOS Baseline** (+ SAMOS from S115):
- Fallback chain: local EMOS → SAMOS global → raw global → bias offset → identity
- `load_global_emos()` / `load_samos_emos()` on `WeatherProbabilityEngine`

**Layer 4 — Historical Bias Bootstrap** (`_maybe_bootstrap_cold_station()`):
- First encounter of cold station (n<5) → fetches 90d GFS+ERA5 from Open-Meteo
- Gives n≈90 immediately → w=0.75 (75% sizing) from day 1

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

## CURRENT SYSTEM STATE (post-S115 deploy)

- **Open positions**: ~193 ($6,301 deployed)
- **All-time realized P&L**: ~+$2,960
- **Deploy**: `20260321_154950` — LIVE, healthy
- **35 active cities** scanning, 148 market groups
- **Station count**: 106 (94 with full SAMOS climatology, 12 pending backfill)
- **Global EMOS**: `a=0.79, b=0.98, σ=2.99` from 7,160 pairs
- **Local EMOS**: 20 stations ready, 5 pending

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
# S115 new env vars (all using defaults — not in .env):
# WEATHER_BUHLMANN_KAPPA=30              # Bühlmann formula denominator
# WEATHER_SPREAD_RATIO_MIN=0.7           # Spread gate min clamp
# WEATHER_SPREAD_RATIO_MAX=1.5           # Spread gate max clamp
# WEATHER_CALIBRATION_RELOAD_SECS=21600  # Calibration reload interval (6h)
# WEATHER_BRIER_HALT_MSE=0.35            # Per-station Brier halt threshold
# WEATHER_DEFAULT_MODEL_SPREAD=3.0       # Fallback when spread unavailable
# WEATHER_SEVERE_HALT_EVENTS=Hurricane Warning,Tornado Warning,Extreme Wind Warning
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

---

## NEW CITY ONBOARDING (fully automated as of S114+S115)

1. **Manual**: Add `WeatherStation` to `station_registry.py` (ICAO, GHCND, lat/lon, elevation, tz, temp_unit, aliases)
2. **Automatic**: Bootstrap → global EMOS → Bühlmann ramp → spread gate (all from first scan)
3. **Manual (one-time)**: `backfill_climatology.py --station <id>` for SAMOS climatology
4. **Automatic**: Next calibration reload picks up climatology

**Only gap**: No auto-detection of new Polymarket cities.

---

## OUTSTANDING ITEMS

### Immediate
| Item | Description |
|------|-------------|
| Climatology backfill | 12/106 stations still pending (Open-Meteo 429). Re-run `backfill_climatology.py`. |
| Shadow fill 24h check | `SELECT COUNT(*), AVG(book_walk_slippage) FROM shadow_fills WHERE bot_name='WeatherBot'` |

### Backlog
| Item | Effort | Description |
|------|--------|-------------|
| 3A (remaining) | Multi-commit | ~9 more hardcoded values need env var configs |
| 3B | Full session | Test coverage ~40-50%. Zero tests for S114/S115 cold-start + SAMOS code |
| 3D | Feature | Multi-city correlation (NYC+Boston ~0.6 temp correlation) |

### Monitor
| Item | Watch for | Trigger |
|------|-----------|---------|
| SAMOS vs raw EMOS | Compare sigma, WR | 1 week |
| Chinese cities | First 30+ resolutions | Calibration convergence |
| Severe weather halt | Correct detection | Next NWS warning |
| Shadow fill quality | Avg slippage, fill rate | 24h |

### Future Roadmap
| Priority | Item | Description |
|----------|------|-------------|
| P3 | Climate-Cluster Semi-Local EMOS | Cluster by quantile features (50+ stations) |
| P3 | Grouped Per-Model EMOS | Fit per-model coefficients (6 params) |
| P4 | Nearest-Station Transfer | Elevation-adjusted analog initialization |
| P4 | City Rotation Prediction | Auto-detect new Polymarket cities |
| P5 | MEMOS / Neural Network | Heavy ML (50+ stations, 6+ months) |

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

---

## CRITICAL TRAPS (33 items — DO NOT BREAK)

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
15. **`paper_trades` has NO `metadata` JSONB column**
16. **Resolution backfill excludes SELL trades** — SELL P&L computed at exit time
17. **trade_events immutability trigger**: Must DISABLE/ENABLE for data cleanup
18. **RESOLUTION event idempotency**: ON CONFLICT broken on partitioned tables → atomic INSERT...SELECT
19. **Ghost positions fixed**: Idempotent memory returns `success: False`, order gateway guards `_filled_size > 0`
20. **`_market_group_cache`**: 3-tuple `(group_key, city, cost_usd)` — NEVER expand without updating all consumers
21. **`_close_stale_positions()` does direct DB UPDATE** — no trade_events EXIT record (by design)
22. **Exposure reserved BEFORE `place_order()` under `_exposure_lock`**, reverted on failure
23. **`event_data` dict mutated in-place** by paper_trading.py before DB write
24. **`WEATHER_SKIP_COORDINATOR_BUY=True`** — confirm_position() does direct INSERT
25. **trade_events JSONB column is `event_data`** — NOT `metadata_json`
26. **`_calibration_confidence()` returns 1.0 when `_station_n_resolved` is empty** — prevents false blocks pre-first-reload
27. **SAMOS fallback chain**: local EMOS → SAMOS global → raw global → bias → identity. Do NOT remove any layer.
28. **`weather_climatology` table**: Populated by `backfill_climatology.py`, NOT by the bot. Bot only reads.
29. **Open-Meteo rate limit**: ERA5 archive throttles at ~25 req/min. Backfill has 2s delays + skip-done. Running backfill during scan causes bot 429s (temporary).
30. **paper_trading.py rewrite (S115 cross-bot)**: Alpha decay, Kyle's lambda, fill probability all DELETED. Uses real L2 orderbook VWAP now. Do NOT re-add theoretical slippage.
31. **Severe weather halt is US-only**: `api.weather.gov` has no international coverage. International stations skip silently.
32. **`scan_start_mono` in event_data**: WeatherBot passes it. Now ignored by paper_trading (alpha decay deleted) but kept for logging. Do NOT remove.
33. **DRY scan template `_scan_psw_markets()`**: Shared by precip/snow/wind. Log tags use f-strings: `f"weatherbot_{market_type}_scan_done"`.

---

## VERIFICATION COMMANDS

```bash
# WeatherBot scan health
journalctl -u polymarket-ai -f | grep weatherbot_scan_done

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

# Brier decomposition
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
| S114 | 03-21 | EMOS cold-start: spread gate, Bühlmann ramp, global EMOS, historical bootstrap |
| S115 | 03-21 | Fix 2D cap ordering, config extraction, Brier script, severe weather halt, DRY refactor, SAMOS ERA5 climatology |

---

## LATEST COMMITS

```
1edb1ad fix(weather): S115 — skip already-backfilled stations in climatology script
dcee384 fix(weather): S115 — add rate limit delay to backfill_climatology.py
f2b4820 fix(weather): S115 — fix Database() constructor in scripts
ca999a7 feat(weather): S115 — 6 actionable items + proper SAMOS climatology
325e0f2 feat(weather): S114 — EMOS cold-start mitigation: 4 changes
32daca8 fix(weather): S113 — 2E negative counter, 2B cache jitter, 2C gamma log, 4 Chinese cities
```
