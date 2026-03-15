# AGENT HANDOFF — WeatherBot Session 80 (2026-03-12)

**Session scope**: WeatherBot ONLY. Do not modify other bot files unless manually demanded.
**Previous handoffs**: `WEATHERBOT_FULL_AGENT_HANDOFF.md` (Sessions 53-69), `AGENT_HANDOFF_WEATHERBOT_SESSION69_2026_03_09.md`

---

## 1. WHAT THIS SYSTEM IS

A 14-bot automated Polymarket trading system. WeatherBot is one of 5 active bots. Currently in **paper trading mode** (`SIMULATION_MODE=true`). Real capital is NOT at risk yet — all trades are simulated via `PaperTradingEngine`.

### Active Bots
| Bot | Status | P&L |
|-----|--------|-----|
| **WeatherBot** | Active | +$426.56 paper (37 resolved), +$880.23 position MTM |
| MirrorBot | Active | +$93.87 paper, +$6,336 position MTM |
| EsportsBot | Active | $0 paper (0 resolved), -$40 position MTM |
| EsportsLiveBot | Active | — |
| EsportsSeriesBot | Active | — |
| 9 others | Disabled | BOT_ENABLED_* flags off |

### VPS
- **Host**: Ubuntu-3, 34.251.224.21, 16GB/4vCPU, eu-west-1 (Dublin)
- **SSH**: `ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21`
- **Service**: `sudo systemctl restart polymarket-ai`
- **Logs**: `journalctl -u polymarket-ai -f | grep weatherbot`
- **DB**: PostgreSQL, localhost, user=polymarket, db=polymarket
- **Deploy**: `cd /c/lockes-picks/polymarket-ai-v2 && KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh`
- **Rollback**: same with `deploy/rollback.sh`

---

## 2. WEATHERBOT ARCHITECTURE

### Core File: `bots/weather_bot.py` (~1090 lines)

#### Initialization (`__init__`, lines 56-149)
- Sub-components: `_forecast_client` (Open-Meteo), `_metar_client` (METAR obs), `_prob_engine` (skew-normal CDF), `_precip_engine` (Gamma CDF), `_market_mapper`, `_station_health`
- Config: `WEATHER_MIN_EDGE=0.08`, `WEATHER_MAX_PER_GROUP_USD=1000`, `WEATHER_DAILY_LOSS_LIMIT=2000`, `WEATHER_MAX_CORRELATED_EXPOSURE=2000`, `WEATHER_KELLY_FRACTION=0.25`, `WEATHER_DEFAULT_SIZE=25`, `WEATHER_MAX_LEAD_TIME_HOURS=168`, `WEATHER_MAX_POSITIONS=500`
- Risk state: `_daily_pnl`, `_group_exposure` (city:date → USD), `_city_exposure` (city → total USD), `_recently_exited` (market_id → mono time, 900s TTL)

#### Main Scan Loop (`scan_and_trade`, lines 525-713)
1. Increment `_scan_count`; handle daily boundary, reload calibration, load category params
2. Restore daily P&L from DB
3. Check monitoring thresholds (W4 halt)
4. Detect PM exits (compare `_open_position_markets` delta), add to `_recently_exited` + Redis
5. One-time startup: restore forecast cache, exits from Redis, exposure from DB, close stale positions
6. Fetch weather markets via tag_slug=temperature (Gamma API), fallback to DB + CLOB
7. Group by (city, date)
8. Prefetch NWS severe weather alerts
9. Analyze all groups in parallel (5-semaphore)
10. Compute regime_boost (cross-city warm/cold detection)
11. Execute trades: ≥2 buckets → Smoczynski-Tomkins laddering; single → independent Kelly
12. Re-evaluate open positions
13. Every 10 scans: backfill outcomes, check EMOS drift, close stale positions
14. Parallel: precip, snowfall, wind scanning
15. Log diagnostics

#### Market Types
| Type | Min Edge | Model | Distribution |
|------|----------|-------|-------------|
| Temperature | 0.08 | 133-member GEFS+ECMWF+AIFS | Skew-normal CDF |
| Precipitation | 0.10 | Ensemble + NDFD PoP (US daily) | Gamma |
| Snowfall | 0.12 | Ensemble only | Gamma |
| Wind | 0.10 | Ensemble | Normal CDF |

Per-type min_edge stored in `bot_category_params` table (migration 040). Read by `_get_min_edge(market_type)` with global fallback.

#### Multi-Bucket Groups
- Each city+date has ~7 temperature buckets (e.g., ≤42°F, 42-46°F, 46-50°F, etc.)
- Probabilities must sum to 1.0 across all buckets
- Smoczynski-Tomkins (2010) optimal Kelly allocation for mutually exclusive outcomes (`_smoczynski_tomkins_allocate`, lines 1689-1750)
- Single-bucket groups use independent Kelly

#### Lead-Time-Dependent Edge Caps
```
< 6h:  max_edge = 0.70  (same day, high ensemble convergence)
< 12h: max_edge = 0.50
< 24h: max_edge = 0.40
< 48h: max_edge = 0.30
else:  max_edge = 0.25  (week-long, low cap)
```

#### Adaptive Scan Interval (NWP Model Windows)
- ECMWF 00Z (07:00-08:00 UTC): 60s scan, cache invalidate
- ECMWF 12Z (18:00-19:00 UTC): 60s scan, cache invalidate
- GFS 00Z (05:15-06:00 UTC): 90s scan, cache invalidate
- GFS 12Z (17:15-18:00 UTC): 90s scan, cache invalidate
- HRRR window (≥:40 min): 120s scan
- Default: 300s (`SCAN_INTERVAL_WEATHER`)

#### Key Analysis Methods
- `_analyze_group(group)` (lines 1308-1441): Fetch combined forecast, fit skew-normal, compute bucket probabilities, apply METAR override if <6h lead
- `_apply_metar_resolution_day_override()` (lines 1593-1684): Override model probs with METAR running daily max on resolution day
- `_execute_group_trades()` (lines 1799-1885): S-T sizing + trade execution with exposure caps
- `_execute_weather_trade()` (lines 1912-2055): Single-market trade flow with validation chain
- `_compute_regime_boost()` (lines 2065-2130): ≥3 US cities unanimous warm/cold → 1.2x Kelly boost

### Market Mapper: `base_engine/weather/market_mapper.py` (~800 lines)

#### Key Classes
- `TemperatureBucket`: market_id, token_id, no_token_id, yes_price, bucket_type (range/at_or_below/at_or_higher/exact), low/high_bound, temp_unit
- `WeatherMarketGroup`: city, target_date, station, buckets list, slug_prefix, temp_unit
- Similar: `PrecipitationBucket/Group`, `SnowfallBucket/Group`, `WindBucket/Group`

#### Regex Patterns (lines 141-298)
- `_RE_RANGE`: "between 48-49°F"
- `_RE_AT_OR_BELOW`: "42°F or below"
- `_RE_AT_OR_HIGHER`: "55°F or higher"
- `_RE_EXACT`: "10°C on February 5"
- `_RE_PRECIP_RANGE/_V2`, `_RE_SNOW_*`, `_RE_WIND_*`: similar for other types
- Quick pre-filters for fast rejection

#### Key Methods
- `_extract_city_and_date(question)` (line 487): Extracts (city_text, date_object) from any weather question
- `_parse_date(date_str)` (line 302): Handles "January 22", "Feb 3, 2026"; L2 next-year inference if >180 days past
- `parse_market(market_data)` (line 403): Parses single temperature market → `TemperatureBucket`
- `group_markets(weather_markets)` (line 498): Groups by (station_id:target_date) with parse cache
- `is_weather_market(market_data)` (line 397): Fast regex check

---

## 3. STATE PERSISTENCE (CROSS-RESTART)

| State | Mechanism | Restore Method |
|-------|-----------|----------------|
| Exit cooldowns (`_recently_exited`) | Redis `weatherbot:exit:{mid}` with `expire_at`, 900s TTL | `_restore_exits_from_redis()` on first scan |
| Group/city exposure | Query `paper_trades` (filled_at ≥ today 00:00 UTC) | `_restore_exposure_from_db()` on startup |
| Daily P&L | Query `paper_trades.realized_pnl` SUM for today | `_restore_daily_pnl_from_db()` on day boundary |
| Forecast cache | `weather_forecasts` DB + Redis PTTL | `_forecast_client.warm_cache_from_db()` + `restore_state()` |
| Station MSE | `weather_calibration` avg MSE, 1h cache | `_get_station_reliability_factor()` |
| Category params | `bot_category_params` table | `_load_category_params()` on first scan |
| Daily exposure counter | `daily_counters` table, 60s flush + SIGTERM | `order_gateway._restore_daily_exposure()` on startup |
| Open positions | `positions` table | `order_gateway.seed_positions_from_db()` |

---

## 4. WHAT THIS SESSION (80) ACCOMPLISHED

### Session scope: WeatherBot + full P&L audit (cross-bot shared infrastructure changes authorized by user)

### Commits This Session
```
88e34ee fix(db): UPSERT paper_trades on re-entry instead of failing on UNIQUE
fc54a85 fix(paper): UNIQUE constraint + duplicate guard on paper_trades
07c8266 fix(paper+pm): eliminate SELL paper_trades at source + migrate exit learning
c453fe0 fix(pnl): exclude SELL exit trades from all P&L queries
fc7a242 feat(weather): date-aware stale cleanup + seed bot_category_params
5b686f1 fix(weather): zombie position cleanup — 20h age + resolved paper_trade check
```

### Fix 1: SELL Paper Trades Eliminated at Source
**Problem**: Position manager exits (stop-loss, take-profit, model reversal) created SELL paper_trades in DB. These corrupted ALL bots' P&L queries — `SUM(realized_pnl)` included exit losses alongside entry trade P&L.

**Root cause chain**:
1. `position_manager._execute_exit()` → `order_gateway.place_order(side="SELL")` → `paper_trading._place_order_locked()` → `db.insert_paper_trade(side="SELL")`
2. All P&L queries (`get_paper_trade_summary`, `check_daily_pnl_summary`, `compute_pnl_summary`, etc.) did `SUM(realized_pnl)` without filtering out SELL

**Fix (3 layers)**:
1. **Source**: `paper_trading.py` — SELL orders skip `db.insert_paper_trade()`. In-memory state (cash, PerformanceTracker, RL callback) still updated. Logs "Paper exit executed (no DB record)".
2. **Query layer**: Added `AND side IN ('YES', 'NO')` to 9 queries across `database.py`, `alerting.py`, `esports_db.py`, `resolution_backfill.py`, `weather_bot.py`.
3. **Exit learning**: `position_manager._refresh_exit_learning()` migrated from `paper_trades WHERE side='SELL'` to `positions WHERE status='closed'` (lines 90-141).

**Stop-loss tracking unaffected**: `risk_manager.record_trade_outcome()` called directly by `position_manager._execute_exit/stop_loss/take_profit`, independent of paper_trades.

### Fix 2: Full P&L Audit + Data Cleanup
**Purged from VPS DB**:
- 934 SELL paper_trades (all bots)
- 776 EnsembleBot duplicates (same market re-bought every scan)
- 255 WeatherBot correlation_id duplicates (retry/restart reinserts)
- 55 CrossPlatformArbBot dead trades (disabled bot, single market)
- 19 MirrorBot BUY→YES/NO conversions (pre-mandate side values)
- 51 remaining per-(bot,market,side) duplicates

### Fix 3: DB Constraints (migration 041)
- `UNIQUE(bot_name, market_id, side)` — prevents duplicate paper_trades at DB level
- `CHECK(side IN ('YES','NO','SELL'))` — rejects invalid side values
- `insert_paper_trade()` changed to UPSERT (`ON CONFLICT DO UPDATE`) so re-entries after position close overwrite old row instead of failing

### Fix 4: Date-Aware Stale Position Cleanup
- `_close_stale_positions()` rewritten: parses target date from `markets.question` via `_extract_city_and_date()`, closes positions where `target_date < today`
- Fallback: 20h age + resolved paper_trade check
- Also evicts from `order_gateway._open_position_markets` to unblock re-entry

### Fix 5: Per-Market-Type Min Edge (migration 040)
- `bot_category_params` seeded: temperature=0.08, precipitation=0.10, snowfall=0.12, wind=0.10
- `_load_category_params()` reads on first scan
- `_get_min_edge(market_type)` returns per-type value with global fallback

---

## 5. CURRENT WEATHERBOT STATE (2026-03-12 ~02:50 UTC)

### P&L
- **Paper trades**: 138 trades, 37 resolved, **+$426.56**
- **Positions closed**: 327, **+$721.19** mark-to-market
- **Positions open**: 146 (42 YES, 104 NO), **+$159.04** unrealized, $6,792.87 exposure
- **Daily exposure**: $160.44 (2026-03-12), $343.54 (2026-03-11)

### Category Params (DB)
| Market Type | min_edge |
|-------------|----------|
| temperature | 0.08 |
| precipitation | 0.10 |
| snowfall | 0.12 |
| wind | 0.10 |

### Data Integrity
- 0 SELL records in paper_trades
- 0 duplicate paper_trades
- 0 invalid side values
- UNIQUE + CHECK constraints enforced at DB level
- UPSERT handles re-entry gracefully

---

## 6. FILES TOUCHED THIS SESSION

| File | Changes |
|------|---------|
| `bots/weather_bot.py` | `_close_stale_positions()` rewrite (date-aware), `_restore_daily_pnl_from_db()` YES/NO filter, `_load_category_params()`, `_get_min_edge()` |
| `base_engine/execution/paper_trading.py` | SELL trades skip DB persist, UPSERT duplicate guard, `_db_side` simplification |
| `base_engine/execution/position_manager.py` | `_refresh_exit_learning()` migrated from paper_trades to positions table |
| `base_engine/data/database.py` | `insert_paper_trade()` → UPSERT with ON CONFLICT, P&L query YES/NO filters |
| `base_engine/monitoring/alerting.py` | `check_daily_pnl_summary()` YES/NO filter |
| `esports/data/esports_db.py` | `compute_pnl_summary()` YES/NO filter |
| `base_engine/data/resolution_backfill.py` | Phase 6 scoring YES/NO filter |
| `schema/migrations/040_weather_category_params_seed.sql` | New: seeds per-type min_edge |
| `schema/migrations/041_paper_trades_constraints.sql` | New: UNIQUE + CHECK constraints |
| `config/settings.py` | `WEATHER_FORECAST_CACHE_TTL` default 1800 |
| `deploy/env.vps` | `WEATHER_FORECAST_CACHE_TTL=3600` (VPS override for 429 mitigation) |

---

## 7. KEY CONFIG (VPS .env values)

```bash
# WeatherBot uses defaults from config/settings.py except:
WEATHER_FORECAST_CACHE_TTL=3600          # Raised from 1800 to reduce Open-Meteo 429s
WEATHER_HOLD_HOURS_BEFORE_RESOLUTION=48.0
WEATHER_MAX_POSITIONS=500                # Default in settings.py (raised from 200 Session 72)

# Shared
SIMULATION_MODE=true
PAPER_TRADING=true
SCAN_INTERVAL_WEATHER=300                # Overridden dynamically by NWP model windows
RISK_MAX_POSITION_SIZE_USD=100
CATEGORY_KELLY_FRACTIONS={"weather":0.25,"crypto":0.125,"politics":0.20,"sports":0.15}
```

---

## 8. CRITICAL TRAPS (DO NOT BREAK)

1. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER pass BUY/SELL.
2. **SELL paper_trades eliminated**: paper_trading.py no longer persists SELL to DB. Do NOT re-enable.
3. **paper_trades UNIQUE constraint**: `(bot_name, market_id, side)`. insert_paper_trade uses UPSERT. Do NOT use ORM `session.add()` for paper_trades.
4. **paper_trades CHECK constraint**: side must be YES, NO, or SELL.
5. **`_open_position_markets` eviction**: `_close_stale_positions()` must evict from `order_gateway._open_position_markets` or positions block re-entry forever.
6. **PSEUDO_LABEL_ENABLED=false**: DO NOT enable. Only Location 1 (market resolution) labels are correct.
7. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
8. **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer.
9. **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime string.
10. **`paper_trades` has NO `metadata` JSONB column** — never assume metadata is available.
11. **Stop-loss tracking**: `risk_manager.record_trade_outcome()` called directly by position_manager, independent of paper_trades table.
12. **Exit learning**: Uses `positions` table (`status='closed'`, `unrealized_pnl`), NOT paper_trades SELL records.
13. **Resolution backfill excludes SELL trades**: `AND LOWER(pt.side) != 'sell'` in `backfill_paper_trades_resolution()`.
14. **BOT_REGISTRY=14 bots** — shared module changes require all 14 verified.
15. **Open-Meteo rate limit**: Free tier ~10,000 req/day. Cache TTL=3600s on VPS. 42 groups × 3-5 models per scan.

---

## 9. OUTSTANDING ITEMS / ROADMAP

### WeatherBot-Specific
1. **EMOS calibration**: 13/15 stations EMOS READY (NZWN needs +3, RJTT needs +19 observations). Expected ~2026-03-15.
2. **`_log_weather_prediction()` NOT wired to scan loop**: Function exists but is NOT called during `scan_and_trade()`. Needs to be wired in at trade execution to populate `prediction_log` for WeatherBot accuracy tracking.
3. **Bulk resolution March 11-12**: ~175 unresolved paper_trades for weather markets settling these dates. Check P&L after resolution backfill runs.
4. **WeatherBot re-entry after position close**: UPSERT handles DB side, but WeatherBot should also check `_recently_exited` and `order_gateway._open_position_markets` before re-entering.
5. **Position-guard in scan loop**: WeatherBot re-enters same market on successive scans (10 entries for one market found in audit). The `_open_position_markets` check in `_execute_weather_trade()` should prevent this — verify it's working.

### Shared Infrastructure (done this session)
- [x] SELL paper_trades eliminated at source
- [x] Query-layer YES/NO filters (9 queries)
- [x] Exit learning migrated to positions table
- [x] UNIQUE + CHECK constraints on paper_trades
- [x] UPSERT for re-entry handling
- [x] Full data audit + cleanup (1090 records purged)

### Deferred (not WeatherBot scope)
- **H2**: PositionReconciler CLOB API — meaningless in SIMULATION_MODE
- **H3**: Redis shared rate limiter — needed before Stage 1 (5% live)
- **Health endpoint**: `main.py` port 8765 — uncommitted local changes

---

## 10. DEPLOY PATTERN

```bash
# From local machine (Windows, Git Bash):
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh

# Rollback:
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```

Atomic symlink swap: `/opt/polymarket-ai-v2` → `/opt/pa2-releases/YYYYMMDD_HHMMSS`.
Shared state: `/opt/pa2-shared/{data,saved_models,venv}`.
Migrations run automatically during deploy.

---

## 11. DIAGNOSTIC COMMANDS

```bash
# SSH to VPS
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21

# WeatherBot logs (real-time)
sudo journalctl -u polymarket-ai -f | grep weatherbot

# P&L check
sudo -u polymarket psql -d polymarket -c "SELECT bot_name, COUNT(*), ROUND(SUM(COALESCE(realized_pnl,0))::numeric,2) FROM paper_trades WHERE bot_name='WeatherBot' GROUP BY bot_name;"

# Open positions
sudo -u polymarket psql -d polymarket -c "SELECT status, COUNT(*), ROUND(SUM(unrealized_pnl)::numeric,2) FROM positions WHERE bot_id='WeatherBot' GROUP BY status;"

# Data integrity check
sudo -u polymarket psql -d polymarket -c "SELECT side, COUNT(*) FROM paper_trades GROUP BY side;"

# Daily counters
sudo -u polymarket psql -d polymarket -c "SELECT * FROM daily_counters WHERE bot_id='WeatherBot' ORDER BY counter_date DESC LIMIT 5;"

# Recent trades
sudo -u polymarket psql -d polymarket -c "SELECT created_at, side, size, price, market_id FROM paper_trades WHERE bot_name='WeatherBot' ORDER BY created_at DESC LIMIT 10;"
```

---

## 12. SESSION SCOPE RULE

**This is a WeatherBot-only session.** Hardcoded scope:
- Only modify: `bots/weather_bot.py`, `base_engine/weather/**`, WeatherBot tests
- Shared modules (`base_engine/`, `database.py`, `config/`) ONLY if directly fixing a WeatherBot bug
- NEVER commit changes to `mirror_bot.py`, `esports_bot.py`, or other non-weather files
- Cross-bot changes require explicit user approval
- If prior sessions left uncommitted non-weather changes, leave them alone

See `memory/feedback_bot_sessions.md` for the persistent rule.
