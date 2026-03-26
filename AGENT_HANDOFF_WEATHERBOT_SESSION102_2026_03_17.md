# WeatherBot Session 102 Complete Handoff — Full System Context for Agent Continuation

**Date**: 2026-03-17 (UTC late evening)
**Branch**: master
**Commits this session**: `163f6e6` (S102 features), `c3f15f8` (S102b bug fixes)
**Prior Sessions**: S101 (6 elevations, pre-screening hotfix, city discovery, Milan), S100 (alpha decay, canary persistence)
**Deploy**: Not yet deployed — both commits ready for deploy.sh

---

## WHAT THIS BOT IS

WeatherBot is one of 15 bots in `polymarket-ai-v2`. It trades **temperature prediction markets** on Polymarket:

1. **Discovers** weather markets via Gamma API (`tag_slug=temperature`, paginated up to 500 events)
2. **Groups** markets by (city, date) — each group has ~11 temperature buckets ("42F or below", "between 43-44F", etc.)
3. **Forecasts** using NOAA GFS/ECMWF/HRRR ensemble data via Open-Meteo API (133 total members: GEFS 31 + IFS 51 + AIFS 51)
4. **Fits** EMOS (Ensemble Model Output Statistics) + skew-normal distribution to compute bucket probabilities
5. **Compares** model probabilities vs market prices to find edges
6. **Sizes** bets via Kelly criterion with multiple adjustments (Baker-McHale uncertainty damper, regime boost, NBM benchmark, expiry boost, tail discount)
7. **Executes** via paper trading engine (SIMULATION_MODE=true) — uses realistic CLOB order book simulation with alpha decay, Kyle lambda slippage, resolution proximity penalties

**P&L**: +$2,881 realized (932 closed trades, 62% win rate, 578W/354L). NO-side trades are primary profit driver (+$1,696, 81.3% WR). YES profitable (+$586) despite 15.5% WR due to asymmetric payouts.

---

## SESSION 102 CHANGES

### Commit 1: `163f6e6` — 3 P3-P4 Features

| # | Change | File | Detail |
|---|--------|------|--------|
| 1 | HRRR model run detection | model_run_monitor.py | Probes AWS S3 for 00z/06z/12z/18z extended runs. US-only refresh on HRRR-only events. |
| 2 | MetarMonitor Redis persistence | metar_monitor.py + weather_bot.py | Daily max observations saved to Redis with 24h TTL. `restore_from_redis()` on startup. |
| 3 | GEFS lead-time subsampling | forecast_client.py | Subsample GEFS: 48-72h keep 24/31, 72-120h keep 16/31, 120h+ keep 8/31. Biases toward higher-skill ECMWF. |

### Commit 2: `c3f15f8` — 5 Silent Bug Fixes (Deep Audit)

| # | Bug | File:Line | Impact |
|---|-----|-----------|--------|
| 1 | **Ensemble member sort: lexicographic → numeric** | forecast_client.py:1065 | `sorted(daily.keys())` put `member100` before `member20`. 11 of first 31 "GEFS" slots were AIFS. GEFS subsampling selected 50% wrong members. **Every forecast affected.** |
| 2 | **Lead time: 18:00 UTC → station timezone** | forecast_client.py:1108 | Tokyo lead time off by +15h, Sydney +16h. Wrong EMOS buckets, wrong climate blending, wrong subsampling thresholds for 13 international cities. |
| 3 | **Exposure revert race: snapshot → locked decrement** | weather_bot.py:2432 | Failed `place_order()` reverted exposure outside lock with `= snapshot`. With 8 concurrent coroutines, could overwrite another's reservation. Now `async with _exposure_lock: -= size`. |
| 4 | **BotBankrollManager defaults: 50K/0.30 → 20K/0.25** | bankroll_manager.py:40 | Stale defaults (capital=$50K, kelly=0.30, max_bet=$1000). Masked by RISK_MAX=$100 downstream, but would detonate on cap increase. |
| 5 | **_prior_forecasts pruning** | model_run_monitor.py:258 | Dict grew ~2560 keys/day unbounded. Now prunes entries for past dates after each refresh cycle. |

---

## ANALYSIS COMPLETED THIS SESSION

### S101b Impact — Validated
- Markets: 1139 (was 800 capped). Universe expansion working.
- Cities: 25 active (was 12). All new cities scanned.
- Groups: 114 (was 82). +39% coverage.
- Trades/scan: 14-21 (was 5-7). ~3x improvement.

### City P&L
- **Top**: Atlanta (+$160), Seoul (+$37), Chicago (+$35)
- **Worst**: Dallas (-$185), Wellington (-$108)
- **Data caveat**: 905/932 closed trades are "unknown" (pre-metadata). Maturing.

### NO vs YES Deep Dive
- **NO**: +$1,696, 81.3% WR (487 trades). Primary profit driver.
- **YES**: +$586, 15.5% WR (445 trades). Profitable via asymmetric payouts.
- **Problem zone**: 10-20c YES bucket = 0% WR (-$127). Monitor.

### Critical Retrospective (S92-S101)
- **S98 worst session**: Infinite cache bugs, 7+ hours stale data trading.
- **S95 over-engineered**: Alpha decay/resolution proximity never fire for WeatherBot.
- **Calibration fragmentation**: Alpha decay went through 3 values across 3 sessions.

---

## COMPLETE FILE MAP

### Core Bot
- `bots/weather_bot.py` (~4000 lines) — Main bot: scan_and_trade, _analyze_group, trade execution, discovery, sizing
- `config/settings.py` — All WEATHER_* config keys

### Forecasting Pipeline
- `base_engine/weather/forecast_client.py` — Open-Meteo API client, ensemble fetching, caching, GEFS subsampling, lead-time computation
- `base_engine/weather/probability_engine.py` — EMOS calibration, skew-normal fit, bucket probability integration
- `base_engine/weather/station_registry.py` — 102 WeatherStation entries, lookup_station(), STATION_REGISTRY dict
- `base_engine/weather/market_mapper.py` — Regex parsing, grouping by (city, date), `_last_unmatched_cities`

### Monitoring
- `base_engine/weather/model_run_monitor.py` — Background task: GFS/ECMWF/HRRR detection, jump detection (>=3F shift), parallel sweep, prior_forecasts pruning
- `base_engine/weather/metar_monitor.py` — METAR polling, batched (3x20 stations), daily max tracking, Redis persistence

### Execution
- `base_engine/execution/paper_trading.py` — Realistic fill model, alpha decay (1800s half-life for WeatherBot), resolution proximity, Kyle lambda
- `base_engine/execution/order_gateway.py` — Routes orders, extracts scan_start_mono, risk_manager clamping (RISK_MAX=100)

### Risk
- `base_engine/risk/bankroll_manager.py` — Kelly sizing per bot. WeatherBot: capital=$20K, kelly=0.25, max_bet=$300 (S102b corrected)
- `base_engine/risk/liquidity_guardian.py` — CLOB order book depth checks

### Shared
- `base_engine/base_engine.py` — Base class for all bots
- `base_engine/data/database.py` — asyncpg database layer
- `base_engine/data/redis_cache.py` — Redis wrapper (decode_responses=True — keys are strings, not bytes)

---

## ARCHITECTURE DEEP DIVE

### Scan Flow (`scan_and_trade()`)
1. **Discovery** — Gamma API `tag_slug=temperature` (paginated up to 5 pages of 100 events)
2. **Grouping** — `market_mapper.group_markets()` → List[WeatherMarketGroup]
3. **Pre-fetch alerts** — NWS severe weather for US stations
4. **Parallel analysis** — `asyncio.Semaphore(12)` bounded concurrency per group
5. **Per-group analysis** (`_analyze_group`):
   - Fetch ensemble forecast (GFS+ECMWF via Open-Meteo)
   - EMOS calibration (station-specific bias correction)
   - Skew-normal fit → bucket probabilities
   - Edge = model_prob - market_price (YES) or (1-model_prob) - (1-market_price) (NO)
   - Min edge gate: 0.08 (US), 0.12 (intl without local model)
   - Pre-screening rough estimate (fast-reject if no bucket has edge potential)
6. **Sizing** — Kelly × Baker-McHale × combined_boost (expiry + regime + NBM + severe + jump)
7. **Execution** — Paper trading via OrderGateway → paper_trading.py fill model → risk_manager clamp at $100
8. **Post-scan** — Adaptive backoff, PSW every-other-scan, fill cooldown tracking

### Sizing Path (end-to-end, verified this session)
1. `BotBankrollManager.get_bet_size()` — Kelly formula with capital=$20K, fraction=0.25 (or 0.25 via CATEGORY_KELLY_FRACTIONS)
2. `min(size, max_bet_usd=$300)` — BotBankrollManager per-bet cap
3. Back in weather_bot: `size = kelly_shares * price * combined_boost`
4. `combined_boost` = expiry + regime + NBM + severe + jump, capped at 2.0, then × Baker-McHale factor
5. Baker-McHale: `1/(1+σ²)` where σ = model_spread/3.0°F. At typical spread: factor=0.5 (intentional conservative damper)
6. `min(size, group_cap=$200, city_cap=$500, slippage_cap)` — local exposure caps
7. OrderGateway risk_manager: `min(size, RISK_MAX_POSITION_SIZE_USD=$100)` — final hard cap

**Net effect**: Most trades hit the $100 risk cap. Baker-McHale halves boosts at typical spread. This is intentional.

### Key Sizing Factors
- **Baker-McHale** (0.2-1.0): `1/(1+σ²)` uncertainty damper. Intentional. 0.5 at typical 3°F spread.
- **expiry_boost** (1.0-2.0): Graduated by lead time (<1h: 1.2x, 1-6h: 1.5x, 6-12h: 2.0x, 12-24h: 1.5x)
- **regime_boost** (1.0-1.3): Cross-city convergence detection
- **nbm_boost** (1.0-1.5): NBM benchmark agreement
- **tail_discount** (0.5-1.0): Reduce sizing on extreme tail buckets

### Paper Trading Realism
- **Alpha decay**: 1800s half-life for WeatherBot (weather signal valid ~6h, not 5min). Uses scan_start_mono from event_data.
- **Kyle lambda slippage**: Order book impact model.
- **Resolution proximity**: <30min: 3.0x slippage, 0.5x fill probability.
- **Realistic fills**: Walk actual order book.

### Ensemble Pipeline
- Open-Meteo returns GEFS (31) + IFS (51) + AIFS (51) = 133 members
- Members merged with `{:02d}` format keys → keys go to 3 digits at member100+
- **S102b fix**: Extraction now uses numeric sort (not lexicographic)
- Lead time computed from station's IANA timezone (not hardcoded 18:00 UTC)
- At 48h+: GEFS subsampled (24/31 at 48-72h, 16/31 at 72-120h, 8/31 at 120h+)
- Skew-normal fit in probability_engine → bucket probabilities

---

## CURRENT CONFIG (verified live values)

```
WeatherBot:  capital=$20000, kelly=0.25, max_bet=$300, max_daily=$10000
             MAX_POSITIONS=500, MIN_EDGE=0.08 (US), 0.12 (intl w/o local model)
             FILL_FAIL_COOLDOWN_SCANS=2, FILL_FAIL_COOLDOWN_SECS=120
             MIN_FILL_PROB_ESTIMATE=0.15
             SCAN_MARKET_LIMIT=1500
             alpha_decay_half_life_s=1800 (via event_data)
             penny_bet_floor=0.04, penny_bet_ceiling=0.97
             expiry_boost=graduated (1.0-2.0x by lead time)
Paper:       REALISTIC_FILLS=true, KYLE_LAMBDA=true, CROSS_SCAN=true
             RESOLUTION_PROXIMITY=true
Risk:        RISK_MAX_POSITION_SIZE_USD=100 (hard cap, final arbiter)
             CATEGORY_KELLY_FRACTIONS={"weather":0.25,...}
BotBankroll: capital=20000, kelly_fraction=0.25, max_bet_usd=300, max_daily_usd=10000
```

---

## KNOWN ISSUES

### Resolved This Session
- ~~HRRR model run detection (P3)~~ — `163f6e6`
- ~~MetarMonitor Redis persistence (P3)~~ — `163f6e6`
- ~~GEFS lead-time subsampling (P4)~~ — `163f6e6`
- ~~Ensemble member sort bug (P1)~~ — `c3f15f8`
- ~~Lead time hardcoded 18:00 UTC (P2)~~ — `c3f15f8`
- ~~Exposure revert race condition (P2)~~ — `c3f15f8`
- ~~BotBankrollManager stale defaults (P3)~~ — `c3f15f8`
- ~~_prior_forecasts unbounded growth (P4)~~ — `c3f15f8`

### Still Open
| Priority | Item | Notes |
|----------|------|-------|
| P2 | ~479 markets unresolved | Resolving naturally via backfill |
| P3 | NO vs YES asymmetry | 10-20c YES bucket = 0% WR. Monitor before config change. |
| P3 | Dallas -$185 P&L | Worst city. Investigate station calibration. |
| P3 | City/lead-time P&L sparse | 905/932 closed are "unknown" (pre-metadata). Maturing. |
| P3 | probability_engine fallback returns uniform | If scipy fails, fallback silently enables "doom loop" behavior the fix was designed to prevent. Latent risk. |
| P5 | Lower penny-bet to 0.03 | Only after live fill rate verification at 0.04 |
| P5 | Kalshi cross-platform arbitrage | 8-16h effort, separate session |

---

## P&L STATE

```
Realized P&L:   +$2,881.13
Unrealized:     $0.00
Open positions: 0
Entries:        2,002
Closed:         932 (578W / 354L = 62%)
```

---

## WHAT THE NEXT SESSION SHOULD DO

1. **Deploy S102 + S102b** — `deploy.sh` from VPS. Both commits ready. Verify via journalctl.
2. **Re-run city/lead-time P&L in 3-5 days** — S102 trades will resolve with metadata. Lead time fix changes international station behavior.
3. **Investigate Dallas -$185** — Worst city by P&L. Check station calibration, EMOS fit quality.
4. **Monitor 10-20c YES bucket** — If still 0% WR after 50+ more trades, raise YES min_edge in that range.
5. **Monitor GEFS subsampling + lead time fix impact** — Compare pre/post S102 calibration at >72h lead.
6. **Fix probability_engine fallback** (P3) — Change fallback to return `{}` instead of uniform distribution on degenerate case.

**Or**: Follow user instructions. Scope lock applies.

---

## CRITICAL TRAPS (from CLAUDE.md + session experience)

- **trade_events is P&L AUTHORITY** — never paper_trades
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL.
- **Alpha decay requires `scan_start_mono` in event_data**: Only WeatherBot passes it.
- **`_market_meta_cache` in MirrorBot**: 3-tuple. NEVER expand.
- **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
- **Python 3.13 scoping**: Local imports shadow top-level. Never import inside function if name used before.
- **`system_kv` table**: Generic KV store (migration 054). Canary stage persistence.
- **`traded_markets.bot_names`**: TEXT column, use `LIKE '%BotName%'`.
- **Paper trading IS production** — every feature must work identically when SIMULATION_MODE flips.
- **`group_markets()` now has `_last_unmatched_cities`** — read by WeatherBot for alerting.
- **SCAN_MARKET_LIMIT=1500** — Universe is 1139 markets currently.
- **Gamma API pagination**: `_fetch_weather_events_by_tag()` loops up to 5 pages of 100.
- **RISK_MAX_POSITION_SIZE_USD=100** — final sizing arbiter in OrderGateway. BotBankrollManager's $300 cap is upstream.
- **Baker-McHale factor is INTENTIONAL** — `1/(1+σ²)` at line 2263. Halves boosts at typical spread. NOT a bug.
- **Redis uses decode_responses=True** — keys come back as str, not bytes.
- **Ensemble member keys**: Now sorted numerically (S102b fix). Prior code used lexicographic sort which scrambled members 100+.
- **Lead time**: Now uses station timezone (S102b fix). Prior code hardcoded 18:00 UTC for all stations.
- **Exposure revert**: Now under `_exposure_lock` with decrement (S102b fix). Prior code used snapshot assignment outside lock.
- **BotBankrollManager WeatherBot defaults**: Now capital=$20K, kelly=0.25, max_bet=$300 (S102b). Were $50K/0.30/$1000.

---

## VERIFICATION COMMANDS

```bash
# HRRR detection:
journalctl -u polymarket-ai --since '6h ago' | grep model_run_new_hrrr

# MetarMonitor Redis save/restore:
journalctl -u polymarket-ai --since '30 min ago' | grep metar_redis
journalctl -u polymarket-ai --since '5 min ago' | grep metar_redis_restored

# Scan health:
journalctl -u polymarket-ai --since '10 min ago' | grep weatherbot_scan_done

# City universe (25 cities):
journalctl -u polymarket-ai --since '30 min ago' | grep weatherbot_city_universe

# All bot health:
journalctl -u polymarket-ai --since '5 min ago' | grep -E 'scan_done|scan_ms'

# Service stability:
journalctl -u polymarket-ai --since '30 min ago' | grep -oP 'polymarket-ai\[\d+\]' | sort -u
```

---

## ROLLBACK

```bash
# Revert all S102:
git revert c3f15f8 163f6e6

# Revert bug fixes only:
git revert c3f15f8

# Revert features only:
git revert 163f6e6
```

---

## TESTS

479 passed, 1 pre-existing failure (deleted dashboard test), 0 new failures. Both commits.
