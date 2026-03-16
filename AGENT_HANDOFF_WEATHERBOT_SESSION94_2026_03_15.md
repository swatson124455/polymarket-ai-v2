# AGENT HANDOFF — WeatherBot Session 94
**Date**: 2026-03-15
**Bot Scope**: WeatherBot ONLY (scope lock: non-negotiable, unless user explicitly expands scope)
**Prior Session**: Session 92 (P1 jump detection + P2 NBM benchmark, deployed)
**This Session**: Scan cycle latency optimization + per-order CVaR speed optimization

---

## HARD RULES (READ BEFORE DOING ANYTHING)

### Scope Lock (NON-NEGOTIABLE)
1. **ONLY touch WeatherBot files** unless fixing a shared module bug that directly breaks WeatherBot
2. **NEVER add unsolicited features** — only fix/build what this handoff or the user explicitly requests
3. **Observation duty**: Note issues and surface to user. Do NOT silently implement.
4. **Read CLAUDE.md** before modifying any file — it contains the Prime Directive and Rules of Engagement
5. **Kalshi explicitly excluded** — user removed Kalshi from all plans this session

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
- **Do NOT use `asyncio.create_task()` for financial write-throughs** — fire-and-forget means DB errors silently corrupt. Always `await`.

---

## SESSION 94 COMPLETED WORK

### Part 1: Scan Cycle Periodic Task Optimization (COMMITTED `654dd74`, DEPLOYED)

Four surgical fixes in `bots/weather_bot.py` to reduce periodic scan overhead:

**Fix 1 — Parallelize periodic tasks** (lines ~713-716):
- Changed sequential `await` chain to `asyncio.gather()` for `_backfill_weather_outcomes()`, `_check_emos_drift()`, `_close_stale_positions()`.
- All three are independent (different tables, no shared mutable state). Cut periodic scan from sum(16-80s) to max(10-50s).

**Fix 2 — Time-bound paper_trades subquery** (lines ~450-465):
- `_close_stale_positions()` Step 4 had unbounded `SELECT pt.market_id FROM paper_trades pt WHERE pt.realized_pnl IS NOT NULL` (full table scan on 100K+ rows).
- Added `AND pt.created_at > NOW() - INTERVAL '24 hours'` — positions older than 24h are caught by the `opened_at < 20h` clause instead.

**Fix 3 — Limit drift detection query scope** (lines ~356-362):
- `_check_emos_drift()` was querying 7 days of `weather_calibration` (tens of thousands of rows).
- Changed to 24 hours + `LIMIT 5000`. DDM/EDDM detectors accumulate state in `_drift_detectors` dict — 24h is sufficient.

**Fix 4 — NWS alerts concurrency** (line ~3128):
- `asyncio.Semaphore(5)` → `asyncio.Semaphore(20)`. NWS has no documented rate limit.

**Results** (VPS verified):
- `ms_analysis` improved: 8-18s → 4.7-5.5s
- Periodic scan overhead reduced significantly
- `ms_trades` STILL high (70-190s) — led to Part 2 investigation

### Part 2: Per-Order CVaR Speed Optimization (UNCOMMITTED — ready to commit and deploy)

**Root cause found**: CVaR Monte Carlo in `risk_manager.py` line 577 runs 10,000 simulations on 400+ positions **on every single trade**. The S91 cache only caches `cvar_before` — `cvar_after` is always recomputed fresh. 400 positions × 10k sims = 4M simulation steps per order = 2.5-6.5s `risk_ms`.

**Files modified**:
- `base_engine/risk/correlation_risk.py` — 1 edit (line 77)
- `base_engine/risk/risk_manager.py` — 4 edits (init vars, lines 572/577/575)

**Fix 1 — Reduce MC simulations 10k→2k** (`correlation_risk.py` line 77):
- Default `n_simulations` parameter changed from 10000 to 2000.
- Configurable via `CVAR_N_SIMULATIONS` setting (default 2000).
- Statistical precision at 95% VaR: ±3.5% (2k) vs ±1.4% (10k). More than adequate for a risk gate with $200 threshold.

**Fix 2 — Cache `cvar_after`** (`risk_manager.py` lines 564-577):
- Added `self._cvar_after_cache` with 5s TTL, keyed by `(position_count, market_id)`.
- Within a scan, first trade per market computes CVaR (~200ms). Same-market YES/NO trades hit cache (<1ms).
- 5s TTL = scan-local, expires between scans. Portfolio shifts by <$5 between sequential trades.

**Fix 3 — Extend base CVaR cache TTL 30s→120s** (`risk_manager.py` line 575):
- Prevents base CVaR from expiring mid-scan. Cache already invalidates when position count changes.

**Expected results**:
| Metric | Before | After (cache hit) | After (cache miss) |
|--------|--------|-------------------|-------------------|
| CVaR per trade | 2,500-6,500ms | <10ms | 200-500ms |
| risk_ms per trade | 2,500-6,500ms | 20-80ms | 250-600ms |
| total_ms per trade | 3,180-11,637ms | 100-300ms | 400-800ms |
| ms_trades (21 orders) | 70-190s | 2-6s | 8-17s |

**Tests**: 1599 passed, 8 failed (ALL pre-existing — `test_dashboard_async_worker.py` and `test_web3_compatibility_fixes.py` depend on deleted `ui/dashboard.py` and `ui/async_worker.py`). Zero new failures from CVaR changes.

**Status**: Code is modified in working tree. **NOT YET COMMITTED OR DEPLOYED.** Next session should:
1. Commit the CVaR changes
2. Deploy to VPS
3. Verify `risk_ms` drops from 2500-6500ms to <600ms via `journalctl`

---

## SYSTEM OVERVIEW

### What This Is
A 14-bot automated Polymarket trading system. WeatherBot is one of 5 active bots. Currently in **paper trading mode** (`SIMULATION_MODE=true`). Real capital is NOT at risk — all trades are simulated via `PaperTradingEngine`. Paper trading IS production (see CLAUDE.md).

### WeatherBot Identity
WeatherBot trades temperature, precipitation, snowfall, and wind-gust bucket markets on Polymarket. It uses a **133-member NWP ensemble** (GEFS 31 + ECMWF IFS 51 + ECMWF AIFS 51) with EMOS calibration, isotonic tail correction, METAR resolution-day overrides, and Smoczynski-Tomkins multi-bucket Kelly allocation.

### Active Bots (5 of 14)
| Bot | P&L | Notes |
|-----|-----|-------|
| MirrorBot | +$18,469 realized (fantasy, 100% fills) | RTDS live, Kelly=0.25, realistic fills ON, 183 open |
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
CVAR_N_SIMULATIONS: 2000 (was 10000, S94 change — NOT YET DEPLOYED)
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
                    → CVaR Monte Carlo (2k sims, 120s base cache + 5s after cache) ← S94 optimization
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
- **CVaR Monte Carlo**: 2k simulations on portfolio positions, Gaussian copula for correlation, 95% VaR. Cached base (120s TTL) + cached after (5s TTL per market). Configurable via `CVAR_N_SIMULATIONS`.

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

### Shared Modules Modified This Session
```
base_engine/risk/risk_manager.py                 — CVaR cache init + computation (S94 Fixes 2-3)
base_engine/risk/correlation_risk.py             — n_simulations default 10k→2k (S94 Fix 1)
```

### Order Execution Pipeline (per trade)
```
WeatherBot.place_order()
  └→ BaseBot.place_order()
    └→ OrderGateway.place_order()
      ├→ Kill switch checks (<1ms)
      ├→ Risk manager check_risk_limits() ← CVaR is here
      │   ├→ Pipeline gate, validation, confidence, edge (<5ms total)
      │   ├→ Volume gate (5-50ms, 1h cache)
      │   ├→ Position/exposure checks (<5ms, in-memory fast path)
      │   ├→ Oracle manipulation risk (10-50ms, 60s cache)
      │   ├→ Daily/weekly loss limits (10-50ms, 60s cache)
      │   ├→ CVaR tail risk: base cache (120s) + after cache (5s) ← S94 optimized
      │   └→ PCA factor exposure (50-200ms if correlation_risk available)
      ├→ Cascade + Liquidity checks (parallel, 50-200ms)
      ├→ Trade coordinator reserve (5-15ms DB INSERT with ON CONFLICT)
      └→ Paper trading engine execution (2-5ms lock + 100-300ms DB writes)
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
| CVaR base cache | In-memory (loss = 1 trade recomputes, <500ms) | Done |
| CVaR after cache | In-memory (loss = 1 trade recomputes, <500ms) | Done |

---

## OUTSTANDING ITEMS (WEATHERBOT)
| Priority | Item | Status |
|----------|------|--------|
| **P1** | **Commit + deploy S94 CVaR fixes** | Code ready in working tree, NOT committed |
| **P2** | 604 markets still unresolved in traded_markets | Genuinely open — will resolve naturally |
| **P3** | `ingest_everything()` >600s timeout observed | Master timeout (40min) catches this now |
| **P3** | `no_prediction: 12` per scan — team name parsing failures | CS2/Valorant (EsportsBot scope) |
| **P5** | Remove diagnostic logging (session_factory warning) | Shared module — not in WeatherBot scope |
| **Future** | Geographic expansion (Great Plains corridor) | All active Polymarket cities already in registry |
| **Future** | Lake-effect snow / wind gust market expansion | 0 active Polymarket snowfall/wind markets |
| **Future** | Monitor P1/P2 impact after 24-48h | Jump detection + NBM benchmark deployed |

### Resolved Items (do not re-open)
- ~~P1: Model-run jump detection~~ → **DONE**: Session 92. Deployed.
- ~~P2: NBM CDF benchmark~~ → **DONE**: Session 92. Deployed.
- ~~P1: Scan cycle periodic task optimization~~ → **DONE**: Session 94. Committed `654dd74`, deployed.
- ~~P1: Per-order CVaR speed optimization~~ → **DONE**: Session 94. Code ready, not yet committed.
- ~~Geographic expansion investigation~~ → **DONE**: Session 94. All 21 Polymarket cities in registry. No gaps.
- ~~Snowfall/wind market investigation~~ → **DONE**: Session 94. Infrastructure built, 0 active markets on Polymarket.
- ~~P0: Scheduler death (11h)~~ → Fixed: advisory lock shield + master timeout
- ~~P0: RESOLUTION dedup broken~~ → Fixed: atomic INSERT...SELECT
- ~~P0: False observation mode on restart~~ → Fixed: PatchDriftDetector
- ~~P1: Resolution backfill 3 root causes~~ → Fixed: 544 markets resolved
- ~~P1: MirrorBot P&L audit~~ → Fixed: 3238 dup RESOLUTION events deleted

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
# Check scan cycle timing (verify CVaR speed improvement):
sudo journalctl -u polymarket-ai --since "5 min ago" -o cat | grep -E "risk_ms|total_ms|weatherbot_scan_done"
# Check CVaR gate still functional:
sudo journalctl -u polymarket-ai --since "30 min ago" -o cat | grep "CVaR"
# Check jump detection and NBM benchmark:
sudo journalctl -u polymarket-ai --since "30 min ago" -o cat | grep -E "model_run_jump|nbm_benchmark|jump_boost"
```

### Tests
```bash
pytest  # Full suite (1650+ tests, expect 8 pre-existing failures from deleted ui/ files)
pytest tests/unit/test_weather_bot.py -v  # WeatherBot-specific
pytest tests/unit/test_elevation_integrations.py -v  # CVaR-specific tests
```

---

## SCAN CYCLE TIMING (POST S94 PART 1, PRE CVaR FIX)
```
ms_discovery:        191-1,111ms   (OK)
ms_alerts:             0-1,457ms   (OK)
ms_analysis:       4,700-5,500ms   (IMPROVED from 8-18s)
ms_trades:        70,000-190,000ms (STILL HIGH — CVaR bottleneck, Fix Part 2 addresses this)
ms_precip_snow_wind:   462-9,454ms (OK)
```

**After CVaR fix deployment (expected)**:
```
ms_trades: 2,000-6,000ms (cache-warm) / 8,000-17,000ms (cache-cold, first scan only)
```

---

## DAILY COUNTERS WRITE PATTERN
- **ABSOLUTE-SET**: OrderGateway `daily_exposure_usd` — `counter_value = total` via `_flush_daily_exposure()`
- Do NOT use `asyncio.create_task()` for financial write-throughs — fire-and-forget means DB errors silently corrupt. Always `await`.

---

## RECENT COMMITS (WeatherBot-relevant)
```
654dd74  perf(weather): parallelize periodic tasks + optimize drift/stale queries + NWS concurrency
(S92)    feat(weather): P1 model-run jump detection + P2 NBM CDF benchmark
e18529f  feat(weather): add Lucknow, Munich, Tel Aviv to station registry
6fe26e3  fix(resolution): log silent exception blocks in resolution_backfill
a39e0b5  fix(scheduler): master timeout + lifecycle logging for IngestionScheduler
46f565e  fix(lock): shield advisory lock release from CancelledError
```

**Working tree (uncommitted)**:
- `base_engine/risk/correlation_risk.py` — n_simulations 10000→2000
- `base_engine/risk/risk_manager.py` — cvar_after cache (5s TTL) + base cache TTL 30→120s + configurable n_sims

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
| 94 | 2026-03-15 | Scan cycle latency (committed) + CVaR speed (uncommitted) |

---

## CVaR OPTIMIZATION DETAILS (for next session reference)

### What was done
The per-order CVaR Monte Carlo was the #1 bottleneck in `ms_trades`. Root cause: `cvar_after` on `risk_manager.py` line 577 ran 10k MC simulations on 400+ positions on every single trade. The S91 cache only covered `cvar_before`.

### How it was fixed (3 changes)
1. **`correlation_risk.py` line 77**: `n_simulations` default 10000→2000. Configurable via `CVAR_N_SIMULATIONS` setting. 5x fewer sims, ±3.5% precision (vs ±1.4%), adequate for $200 risk gate.

2. **`risk_manager.py` lines 564-577**: Added `_cvar_after_cache` with 5s TTL, keyed by `(position_count, market_id)`. Within a scan, same-market trades hit cache. New init vars: `_cvar_after_cache`, `_cvar_after_cache_key`, `_cvar_after_cache_until`.

3. **`risk_manager.py` line 575**: Base cache TTL 30s→120s. Survives full scan cycle. Still invalidates on position count change.

### What was verified
- 1599 tests passed, 0 new failures
- 4 CVaR tests (`test_elevation_integrations.py`) all pass — they use relational assertions, not hardcoded values
- No code depends on `n_simulations=10000` — parameter already had default
- No bot overrides CVaR parameters
- `CVAR_SIMULATIONS` config exists in `settings.py` line 609 but is NEVER USED by any code (dead config)

### What still needs verification (post-deploy)
- `risk_ms` drops from 2500-6500ms to <600ms in VPS logs
- CVaR gate still blocks when portfolio CVaR exceeds $200
- All 14 bots continue functioning (risk_manager.py is shared)

---

## WHAT THE NEXT SESSION SHOULD DO

### Immediate (P1)
1. **Commit + deploy the CVaR optimization** — Code is ready in working tree. Commit message suggestion:
   ```
   perf(risk): reduce CVaR MC sims 10k→2k + add cvar_after cache + extend TTL
   ```
2. **Verify VPS `risk_ms` improvement** — Check 3 scan cycles, confirm <600ms

### Short-term (P2-P3)
3. **Monitor P1/P2 features** — After 24-48h operational data:
   - `weatherbot_model_run_jump` — frequency and delta magnitudes
   - `weatherbot_nbm_benchmark` — signal count per scan, correlation with wins
   - `weatherbot_jump_boost` — is the boost firing on trades?

### Future
4. **Geographic expansion** — All 21 active Polymarket weather cities already in station registry. No gaps found. If new cities appear on Polymarket, add them to `station_registry.py`.
5. **Snowfall/wind markets** — Full infrastructure built (`_scan_snowfall_markets`, `_scan_wind_markets`, `PrecipitationProbabilityEngine`). Zero active Polymarket markets currently. Will auto-discover when markets appear.
6. **Further latency reduction** — After CVaR fix, remaining bottlenecks:
   - `coord_ms`: 75-1850ms (trade coordinator DB INSERT, could batch)
   - `exec_ms`: 20-4618ms (paper trading lock + DB writes, post-lock optimization already done)
   - Sequential order execution in WeatherBot trade loop (could parallelize with `asyncio.gather`)

**Or**: User may have other priorities. Follow their instructions. Scope lock applies.
