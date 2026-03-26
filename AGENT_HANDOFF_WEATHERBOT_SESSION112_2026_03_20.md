# AGENT HANDOFF — WeatherBot Session 112 (2026-03-20)

## READ THIS FIRST — AGENT INSTRUCTIONS

You are continuing work on the **WeatherBot** module of a 15-bot Polymarket automated trading system. You are **scope-locked to WeatherBot** — no cross-bot changes unless explicitly demanded by the user.

**Before you write ANY code:** Read `CLAUDE.md` in repo root. State the bug. List files you'll touch. Grep dependents. Read the entire file. This is mandatory, not optional.

**Key governance files:**
- `CLAUDE.md` — Prime directive, rules of engagement, forbidden patterns
- `memory/feedback_scope_lock.md` — NEVER add unsolicited features
- `memory/feedback_bot_sessions.md` — Bot-scoped session rules
- `memory/feedback_pnl_math.md` — P&L formula rules (NEVER invert for NO side)

---

## SESSION 112 SUMMARY

**What happened**: Continued S111's self-review audit. Deployed all Tier 1 fixes + Tier 2A visibility fix. 5 changes total to `weather_bot.py`.

**Code changes**: `bots/weather_bot.py` only (5 edits, 1 file)
**Tests**: 1642 passed, 0 failed, 8 skipped
**Deploy**: `20260319_234936` — LIVE on VPS, verified healthy

---

## ALL CHANGES DEPLOYED IN S111+S112

| Fix | Type | Lines | Description |
|-----|------|-------|-------------|
| 1B | Visibility | 690 | Silent `except: pass` on exposure DB write → `logger.warning(...)` |
| 1B | Visibility | 2988 | Silent `except: pass` on negative counter clamp → `logger.warning(...)` |
| 1A-p1 | Visibility | 694 | Cache miss `logger.debug` → `logger.warning` |
| 2A | Visibility | 1847 | Edge cap rejection `logger.debug` → `logger.info` |
| 1C | Bug fix | 2013-2019 | METAR renormalization guard — if ALL buckets <=0.001, skip renormalization and return original model_probs |
| 1A-p2 | Bug fix | 693-720 | Fallback DB lookup on PM exit cache miss — queries positions+markets table for group/city/cost, decrements exposure even when `_market_group_cache` missed |

### First scan data from deploy:
- **47 negative counters clamped** on startup (confirms 2E is real)
- **36 edge cap rejections** per scan (now visible at INFO)
- **194 positions rebuilt** into market_group_cache
- **4 new unmatched cities**: Chengdu, Chongqing, Shenzhen, Wuhan (station registry task)
- 0 exposure warnings, 0 METAR renorm skips (healthy baseline)

---

## COMPLETE SYSTEM ARCHITECTURE

### What Is This System?
A 15-bot automated Polymarket trading system. Currently in **paper trading mode** (`SIMULATION_MODE=true`). Paper trading IS production — the only difference from live is the final order submission. $20K capital allocated, $300 max bet per bot.

### Bot Roster
| Bot | Purpose | Active |
|-----|---------|--------|
| **WeatherBot** | Temperature/precip/snow/wind bucket markets via NOAA ensembles | YES — session focus |
| **MirrorBot** | Copy-trades elite Polymarket wallets via RTDS feed | YES |
| **EsportsBot** | Pre-match esports (LoL/CS2/Valorant/Dota2) via PandaScore + Glicko | YES |
| **EsportsLiveBot** | In-play esports odds | YES |
| **EsportsSeriesBot** | Series-level esports (Bo3/Bo5) | YES |
| 10 others | Sports, ensemble, arbitrage, etc. | Mostly inactive |

### Infrastructure
- **VPS**: Ubuntu at 34.251.224.21 (16GB/4vCPU), SSH key `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **Deploy**: `bash deploy/deploy.sh` — atomic symlink swap at `/opt/polymarket-ai-v2`
- **Service**: `systemctl` unit `polymarket-ai`, logs via `journalctl -u polymarket-ai -f`
- **DB**: PostgreSQL `polymarket` (asyncpg), Redis for caching
- **Python 3.13** — CRITICAL: `from X import Y` inside function body shadows module-level

---

## WEATHERBOT FILE MAP

### Primary Bot File
| File | Lines | Purpose |
|------|-------|---------|
| `bots/weather_bot.py` | ~4230 | Main bot — scan loop, analysis, trading, state, monitoring |

### Supporting Modules (under `base_engine/weather/`)
| Module | Lines | Purpose |
|--------|-------|---------|
| `forecast_client.py` | 1441 | Open-Meteo API wrapper, GFS/HRRR/GEFS/ECMWF ensemble fetching, Redis cache |
| `probability_engine.py` | 498 | Skew-normal CDF integration, bucket probability computation, EMOS calibration |
| `precipitation_engine.py` | 231 | Precipitation gamma-distribution model, NDFD POP integration |
| `market_mapper.py` | 1135 | Question parsing → 8 dataclasses (Temp/Precip/Snow/Wind buckets + groups), 22 regex patterns |
| `station_registry.py` | 1502 | 102 stations (US+intl), ICAO/WMO codes, aliases, health monitoring |
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

## WEATHERBOT STRATEGY (how it makes money)

1. **Fetch** GFS/HRRR/GEFS + ECMWF ensemble forecasts via Open-Meteo (free, no API key)
2. **Fit** skew-normal distribution to ensemble spread (with EMOS calibration if available)
3. **Integrate** CDF across each temperature bucket's bounds → model probabilities
4. **Compare** model probs vs market-implied probs (YES prices on Polymarket)
5. **Trade** when edge >= 8% US / 12% international, sized by fractional Kelly (0.25)
6. **Hold** until resolution, TP/SL exit, or model reversal

### Scan Loop Structure (`scan_and_trade()`)
1. Discover weather markets via tag-based API fetch
2. Group by city+date → `WeatherMarketGroup`
3. Analyze each group (ensemble fetch → probability → edge detection)
4. Also scan precipitation, snowfall, wind markets
5. Execute trades for opportunities passing all filters
6. Re-evaluate open positions for exits

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
```

### State Persistence
| State | Mechanism | Restore |
|-------|-----------|---------|
| `_group_exposure`, `_city_exposure` | daily_counters write-through | `_restore_exposure_from_db()` |
| `_market_group_cache` | populated on trade, rebuilt on startup | `_rebuild_market_group_cache()` |
| `_recently_exited` | Redis key per market_id with TTL | `_restore_exits_from_redis()` |
| `_consecutive_no_edge` | Redis key with 1h TTL | `_restore_backoff_from_redis()` |
| `_daily_pnl` | trade_events SUM | `_restore_daily_pnl_from_db()` |

---

## SIZING PIPELINE (how trade size is computed)

Located in `_execute_weather_trade()` (lines ~2240-2430):

1. **Short-term override**: If `_st_size_override` exists, use directly (capped)
2. **Kelly sizing**: `BotBankrollManager.calculate_kelly_bet()` with multiplicative boosts:
   - Expiry boost (1.0-2.0x based on lead time)
   - Regime boost (1.0-1.2x from cross-city consensus)
   - Jump boost (from model run / METAR boundary events)
   - NBM benchmark boost (1.15x when NBM agrees)
   - Baker-McHale factor (0.50-1.0x from ensemble spread, floored at `WEATHER_BM_FLOOR`)
3. **Min trade floor**: `WEATHER_MIN_TRADE_USD=5.0`
4. **Exposure locks**: Atomic reservation under `_exposure_lock` for group + city
5. **bestAsk pre-filter**: Skips trade if `confidence <= bestAsk` (no edge after depth)

---

## FILL PROBABILITY MODEL (paper_trading.py)

### 5 Multiplicative Factors
1. **Price-depth** (`0.3 + 0.7 * 4*p*(1-p)`): U-shaped — best at 0.50, worst at extremes
2. **Size-impact** (`1 - 0.4*(size/max_size)`): Larger orders fill worse
3. **Spread factor** (`max(0.1, 1 - spread*10)`): Wide spread = low fill
4. **Time-of-day** (US hours best, nights/weekends worst)
5. **Sqrt participation** (`sqrt(volume * participation / 10000)`)

### Additional Multipliers
- **Taker-side**: `PAPER_TAKER_SIDE_FACTOR=0.85`
- **Kyle's lambda**: ~0.7x adverse selection
- **Alpha decay**: BUY-only, exponential via `scan_start_mono` latency
- **Resolution proximity**: <30min = 0.5x fill, 3.0x slippage

---

## P&L DATA MODEL

### Authoritative source: `trade_events` table
- **NEVER** read `paper_trades` for P&L — it's legacy
- Event types: ENTRY, EXIT, RESOLUTION
- Partitioned by `event_time` (monthly)
- Immutability trigger: `trg_trade_events_immutable`

### P&L formulas (ALL sides, NEVER invert for NO)
```
cost = entry_price * size
unrealized_pnl = (current_price - entry_price) * size
```
Canonical script: `python scripts/bot_pnl.py WeatherBot 24`

---

## CURRENT SYSTEM STATE (as of S112 deploy)

- **Open positions**: ~193 ($6,301 deployed)
- **All-time realized P&L**: ~+$2,960
- **Fill rate**: ~14.7%
- **Deploy**: `20260319_234936` — LIVE, healthy
- **Scan metrics**: 112 groups analyzed, 32 with edge, 36 edge cap rejections, 1 trade/scan
- **26 active cities**, 4 unmatched (Chengdu, Chongqing, Shenzhen, Wuhan)

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
WEATHER_HOLD_HOURS_BEFORE_RESOLUTION=48  # Expiry boost window
PAPER_TAKER_SIDE_FACTOR=0.85             # Taker fill discount (all bots)
SIMULATION_MODE=true                     # Paper trading
PHASE_MAX_BET_USD=1000                   # Phase cap
ALL BOTS: capital=$20000, max_bet=$300, max_daily=$10000, kelly=0.25
```

---

## OUTSTANDING AUDIT FINDINGS (from S111 self-review)

### TIER 2 — Fix Soon (quality improvements)

**2B. Cache jitter inflates TTL** (`forecast_client.py` lines 512, 587, 677)
- Jitter is `+ random.uniform(0, ttl * 0.5)` making entries live 0-50% LONGER
- Should subtract to ensure freshness during model update windows
- Max impact: 7.5 extra stale minutes on 15-min TTL

**2C. Gamma shape clamped silently** (`precipitation_engine.py` lines 108-110)
- Alpha/beta hit boundaries without any logging
- Add `logger.warning` when clamping triggers

**2D. Baker-McHale post-cap ordering** (lines 2319-2333)
- BM factor (0.50-1.0) applied AFTER the 2.0 combined_boost cap
- Loses granularity above cap — monitor pre/post values before changing order

**2E. Negative daily counter restore** (lines 2964-2988)
- 47 negative counters clamped on startup (confirmed from deploy data)
- Negative counters skipped via `continue` instead of treated as 0
- In-memory doesn't get the 0 entry — should replace `continue` with assignment to 0

### TIER 3 — Backlog (separate sessions)

| Item | Effort | Description |
|------|--------|-------------|
| 3A | Multi-commit | ~15 hardcoded values need env var configs (expiry boost schedule, BM typical_spread=3.0, boundary risk discount 0.5x, fill prob coefficients, max edge caps by lead time, drawdown schedule) |
| 3B | Full session | Test coverage ~40-50%. Zero tests for: API failure recovery, concurrent trade races, extreme temps, exposure cap enforcement, METAR override, regime boost, drawdown, fill probability, daily loss limit |
| 3C | Feature | Brier score / calibration — no metric beyond MSE. Need per-city/lead-time/season Brier decomposition |
| 3D | Feature | Multi-city correlation — NYC+Boston treated independently despite ~0.6 temp correlation |
| 3E | Feature | Severe weather suspension — no halt when model inputs invalidated within 12h of resolution |
| 3F | Feature+script | Slippage monitoring — no estimated vs actual fill comparison |
| 3G | Refactor | Precip/snow/wind DRY — market fetching duplicated across 3 scan functions |

### TIER 4 — Monitor (need data before deciding)

| Item | Watch for | Action trigger |
|------|-----------|----------------|
| BM sizing distribution | Log pre/post BM combined_boost | >30% trades hit BM floor 0.50 |
| NBM >30pp disagreement | Pull outcomes where `nbm_high_conviction=True` | Win rate <40% on boosted trades |
| Discovery cache blackouts | Count `weatherbot_no_weather_markets` | >2 blackouts/day |
| Dallas/Wellington P&L | Track at 30+ resolutions | Still negative at 30+ samples |
| Munich station accuracy | 1 resolution so far | Wait for 30+ |
| New Chinese cities | 4 unmatched (Chengdu, Chongqing, Shenzhen, Wuhan) | Add to station_registry if markets persist |

---

## BROADER OUTSTANDING ITEMS (system-wide, WeatherBot-relevant)

| Priority | Item | Notes |
|----------|------|-------|
| P2 | ~479 markets still unresolved | Resolving naturally via backfill |
| P3 | NO vs YES asymmetry (72% vs 39% WR) | Confirmed, monitor before config change |
| P3 | `no_prediction: 12` per scan | EsportsBot team name parsing, not WeatherBot |
| P5 | Kalshi cross-platform arbitrage | Deferred, 8-16h effort |

---

## KEY DATA INSIGHTS (carry forward)

### Side Performance
- **NO side**: 72% WR, +$1,896 — primary profit driver (favourite-longshot bias)
- **YES side**: 39% WR, +$985
- Combined: 62% WR, profitable

### Hold Duration Sweet Spot
- **24-48h holds** are best: 73% WR, +$432 on exits, +$1,054 on resolutions
- **0-2h exits**: 42% WR, marginal — early exits lose
- **48h+ holds**: 62-73% WR, positive — weather signals converge over time

### Exit Triggers
- `take_profit (>+20%)`: 188 exits, +$624 — working well
- `stop_loss (<-20%)`: 41 exits, -$8 — losses contained
- Force exit timer: **DATA SAYS NO** — would destroy best-performing 24-48h bucket

### Fill Pipeline (post-S108 fixes)
- Fill rate improved from 8% to ~14.7%
- bestAsk pre-filter catching ~27% of would-fail trades
- Slippage model is accurate — thin weather markets genuinely cost more to fill
- Same-side dedup preventing 700+ duplicate entries

---

## SESSION HISTORY (WeatherBot only)

| Session | Date | Focus |
|---------|------|-------|
| S92 | 03-15 | P1 jump detection, P2 NBM benchmark |
| S95 | 03-16 | 4 paper trading elevations |
| S97 | 03-16 | 3 stations, P&L breakdown script |
| S100 | 03-17 | Alpha decay, canary persistence, SSH timeouts, backoff Redis |
| S104 | 03-18 | Fill quality logging, exposure leak fix, daily counter, alpha decay BUY-only |
| S108 | 03-19 | Fill pipeline: taker 0.85, bestAsk pre-filter, volume passthrough, same-side dedup, ghost fix |
| S111 | 03-19 | Full self-review audit: 25 findings, 6 invalidated, 4 log-level fixes |
| S112 | 03-20 | METAR renorm guard, fallback DB lookup on cache miss, deploy + verify |

---

## INVALIDATED FINDINGS (do NOT re-investigate)

These were false positives from exploration agents. Do not waste time on them:
1. ~~Precipitation engine not wired~~ — IS wired via `_scan_precipitation_markets()` at lines 924-927
2. ~~Wind/snow trading disconnected~~ — Both wired via scan functions
3. ~~NaN/Inf ZeroDivisionError~~ — `probability_engine.py` line 71 guards `len(clean) < 2`
4. ~~Confidence formula inverted~~ — `1.0 - model_prob` IS correct for NO-side probability
5. ~~Race condition in concurrent _analyze_group()~~ — asyncio is single-threaded cooperative
6. ~~Model cache serves week-old data~~ — 30-min TTL on model run cache prevents staleness

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
22. **`_market_meta_cache` in MirrorBot** is DIFFERENT — do not confuse
23. **Alpha decay is BUY-only** (S104b fix). DO NOT remove `side == "BUY"` gate
24. **`_close_stale_positions()` does direct DB UPDATE** — no trade_events EXIT record (by design)
25. **Exposure reserved BEFORE `place_order()` under `_exposure_lock`**, reverted on failure
26. **`event_data` dict mutated in-place** by paper_trading.py before DB write — do NOT copy before passing
27. **`WEATHER_SKIP_COORDINATOR_BUY=True`** — confirm_position() does direct INSERT
28. **trade_events JSONB column is `event_data`** — NOT `metadata_json`

---

## VERIFICATION COMMANDS

```bash
# WeatherBot scan health
journalctl -u polymarket-ai -f | grep weatherbot_scan_done

# New warning logs (S112 visibility)
journalctl -u polymarket-ai -f | grep -E "weatherbot_(pm_exit_no_cache|exposure_db_write_failed|metar_renorm_skip|edge_cap|negative_counter)"

# Edge cap rejection rate (36/scan expected)
journalctl -u polymarket-ai --since '1 hour ago' | grep -c "weatherbot_edge_cap"

# Exposure fallback firing (cache miss → DB lookup)
journalctl -u polymarket-ai -f | grep "weatherbot_exposure_decremented_fallback"

# Fill rate
journalctl -u polymarket-ai --since '30 min ago' | grep 'Order latency.*Weather'

# P&L
PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/bot_pnl.py WeatherBot 24

# Position count + sizes
sudo -u postgres psql -d polymarket -c "SELECT status, count(*), round(avg(entry_cost)::numeric, 2) FROM positions WHERE bot_id='WeatherBot' GROUP BY status;"

# No ghost positions
sudo -u postgres psql -d polymarket -c "SELECT count(*) FROM positions WHERE bot_id='WeatherBot' AND status='open' AND size=0;"

# Daily counters health
sudo -u postgres psql -d polymarket -c "SELECT counter_name, counter_value FROM daily_counters WHERE bot_id='WeatherBot' AND counter_date=CURRENT_DATE ORDER BY counter_name;"
```

---

## ROLLBACK

```bash
# Full S112 rollback
git revert <s112-sha>
bash deploy/deploy.sh

# Config-only rollback (no code change — edit .env then restart)
export WEATHER_EXIT_COOLDOWN_SECS=900      # 4hr → 15min
export WEATHER_MIN_TRADE_USD=1.0           # $5 → $1
export WEATHER_BM_FLOOR=0.0               # uncapped
export PAPER_TAKER_SIDE_FACTOR=0.55        # 0.85 → 0.55
sudo systemctl restart polymarket-ai
```

---

## NEXT SESSION PRIORITIES

1. **Fix 2E**: Negative daily counter restore — replace `continue` with `counter[key] = 0.0` (1 line)
2. **Fix 2B**: Cache jitter direction — subtract instead of add (3 lines in forecast_client.py)
3. **Fix 2C**: Gamma clamp logging — add `logger.warning` when alpha/beta hit boundaries (2 lines)
4. **Add 4 Chinese cities** to station_registry.py if markets persist (Chengdu, Chongqing, Shenzhen, Wuhan)
5. **Evaluate edge cap rate**: 36 rejections/scan — if these represent profitable trades being blocked, consider relaxing caps
6. **Review Tier 3 items** based on accumulated data
