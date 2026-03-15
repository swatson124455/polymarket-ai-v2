# AGENT HANDOFF — WeatherBot Session 81 (2026-03-12)

**Session scope**: WeatherBot ONLY. Do not modify other bot files unless manually demanded.
**Previous handoffs**: `AGENT_HANDOFF_WEATHERBOT_SESSION80_2026_03_12.md`, `WEATHERBOT_FULL_AGENT_HANDOFF.md` (Sessions 53-69)
**Continuation of**: Session 80 (same day, context ran out and was resumed)

---

## 0. SESSION CONTINUITY NOTE

This session is a direct continuation of Session 80. The context window filled up and was resumed from a compacted summary. All Session 80 items were completed plus new work. Treat this handoff as the authoritative current state — it supersedes Session 80's handoff.

---

## 1. WHAT THIS SYSTEM IS

A 14-bot automated Polymarket trading system. WeatherBot is one of 5 active bots. Currently in **paper trading mode** (`SIMULATION_MODE=true`). Real capital is NOT at risk yet — all trades are simulated via `PaperTradingEngine`.

### Active Bots
| Bot | Status | P&L |
|-----|--------|-----|
| **WeatherBot** | Active | +$426.56 paper (37 resolved as of deploy, 96 more now processing) |
| MirrorBot | Active | +$230.59 paper (14 resolved). Blocked by $20k exposure cap |
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

## 2. WHAT THIS SESSION (81) ACCOMPLISHED

### All commits this session (deployed to VPS):
```
b8f8b26 feat(weather): traded_markets table + prediction logging + backfill priority fix
e76df44 fix(backfill): add end_date_iso to SELECT DISTINCT for ORDER BY clause
```

### A. Resolution Backfill Root Cause Found + Fixed (THE BIG ONE)

**Problem**: 96 WeatherBot paper trades from March 6-12 were stuck unresolved despite Polymarket closing those markets days ago. P&L was frozen at 37 resolved trades.

**Root cause chain**:
1. Resolution backfill Phase 2 scanned 37,538 unresolved markets with `LIMIT 500`
2. Markets sorted by `end_date_iso ASC NULLS LAST`
3. Weather paper_trade markets had NULL `end_date_iso` → sorted to position ~16,000
4. With LIMIT 500, our markets NEVER got processed
5. Additionally: CLOB API returns `resolution=None, outcome=None` for some markets — winner is only in `tokens` array
6. `_clob_to_market_format()` correctly extracts winner from tokens, but Phase 2 never reached our markets

**Fix — `traded_markets` table (migration 042)**:
- New table with ~413 rows (seeded from paper_trades) tracking only markets we bet on
- Partial index on `resolved = FALSE` for fast lookups
- UPSERT on every paper trade insert (`database.py:insert_paper_trade()`)
- UPDATE on market resolution (`database.py:save_market_resolution()`)
- Resolution backfill Phase 2a now reads `SELECT market_id FROM traded_markets WHERE resolved = FALSE` — no joins, no limit, ~100 rows
- Fallback to old EXISTS subquery if table doesn't exist (try/except)

**Verified on VPS**: 413 markets seeded, 369 unresolved, 44 resolved. Backfill running clean: "fetching 8 missing markets", "backfilling resolution for 500 markets".

### B. `_log_weather_prediction()` Wired Into Trade Execution

**Problem**: Function existed since Session 77 but was only called during opportunity analysis, NOT at trade execution time. `prediction_log` table wasn't being populated for WeatherBot accuracy tracking.

**Fix**: Added call in `_execute_weather_trade()` at line ~2058, inside the `result.get("success")` block, after trade-filled log and before cooldown guard:
```python
await self._log_weather_prediction(
    opp["market_id"], opp["model_prob"], opp["price"],
    opp.get("confidence", opp["model_prob"]),
    opp.get("market_type", "temperature"),
)
```
Dedup logic inside `_log_weather_prediction` (skips if same market with delta < 0.01 within 600s) handles overlap with analysis-time calls.

### C. SELECT DISTINCT Fix (Phase 2b)

**Problem**: Phase 2b on-chain trades query had `SELECT DISTINCT m.id ... ORDER BY m.end_date_iso` — PostgreSQL requires ORDER BY columns in SELECT list with DISTINCT.

**Fix**: Changed to `SELECT DISTINCT m.id, m.end_date_iso FROM markets m ...`

### D. Resolution Backfill Architecture (Phase 2a/2b Split — done in Session 80, deployed this session)

Previous sessions already split Phase 2 into:
- **Phase 2a**: Paper trade markets (our markets) — unlimited, always processed first
- **Phase 2b**: On-chain trades — fill remaining slots from LIMIT

This session replaced Phase 2a's expensive EXISTS subquery with the fast `traded_markets` lookup.

### E. EMOS Drift Analysis (Read-Only Investigation)

Checked 5 flagged stations:
| Station | Drift % | Action |
|---------|---------|--------|
| KDFW | 86.7% | Needs recalibration |
| KLGA | 50.0% | Needs recalibration |
| KORD | 37.2% | Monitor |
| CYYZ | 29.8% | Monitor |
| KSEA | 5.2% | False alarm (good) |

EMOS drift is **advisory only** — `_check_emos_drift()` sends alerts but does NOT halt trading.

### F. WeatherBot Bottleneck Analysis (Read-Only Investigation)

**API bottlenecks**:
- NWS alerts: unbounded concurrency (no semaphore)
- CLOB midpoint: 200-market cap per call
- Open-Meteo forecast: 50 req/min rate limit, cache TTL=3600s on VPS

**DB bottlenecks**:
- Pool near exhaustion: 22/20 overflow (15 base + 5 overflow)
- Stale position cleanup: N+1 query pattern
- Exposure restore: complex JOIN

**Sequential execution**: Steps 1-6 in `scan_and_trade()` are sequential but could be parallel

**Top 3 quick wins**:
1. Increase DB_POOL_SIZE to 30
2. Add semaphore(5) to NWS alerts
3. Parallelize scan init steps 1-5

### G. Re-Entry Guard Verification (Read-Only)

5-layer position guard confirmed intact:
1. In-memory `_open_position_markets` set check (`weather_bot.py:1849-1852`)
2. 15-min `_recently_exited` cooldown (`weather_bot.py:1854-1858`)
3. `_close_stale_positions()` evicts from `order_gateway._open_position_markets` (`weather_bot.py:382-494`)
4. Redis TTL persistence for exit cooldowns (`_save_exit_to_redis`, `_restore_exits_from_redis`)
5. DB UNIQUE constraint `(bot_name, market_id, side)` with UPSERT

---

## 3. WEATHERBOT ARCHITECTURE

### Core File: `bots/weather_bot.py` (~1753 lines in coverage report)

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

Per-type min_edge stored in `bot_category_params` table (migration 040).

#### Multi-Bucket Groups
- Each city+date has ~7 temperature buckets (e.g., ≤42°F, 42-46°F, 46-50°F, etc.)
- Probabilities must sum to 1.0 across all buckets
- Smoczynski-Tomkins (2010) optimal Kelly for mutually exclusive outcomes (`_smoczynski_tomkins_allocate`, lines 1689-1750)

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
- `_log_weather_prediction()` (line 152): Logs to prediction_log for accuracy tracking (NOW WIRED to trade execution)

### Market Mapper: `base_engine/weather/market_mapper.py` (~800 lines)
- `TemperatureBucket`, `PrecipitationBucket`, `SnowfallBucket`, `WindBucket` dataclasses
- `WeatherMarketGroup`: city, target_date, station, buckets list, slug_prefix, temp_unit
- Regex parsing for "between 48-49°F", "42°F or below", "55°F or higher", "10°C on February 5"
- `_extract_city_and_date(question)`: Extracts (city_text, date_object) from any weather question
- `group_markets(weather_markets)`: Groups by (station_id:target_date) with parse cache

---

## 4. BUY/SELL vs YES/NO — How the System Works

Polymarket binary markets have two tokens: YES and NO. Sum to $1.00.

### Bot → Paper Engine Translation (`order_gateway.py:652`)
| Bot calls | Paper engine gets | DB record |
|-----------|-------------------|-----------|
| `side="YES"` | `paper_side="BUY"`, `original_side="YES"` | `side="YES"` in paper_trades |
| `side="NO"` | `paper_side="BUY"`, `original_side="NO"` | `side="NO"` in paper_trades |
| `side="SELL"` | `paper_side="SELL"`, `original_side="SELL"` | **NOT written to DB** (skipped at paper_trading.py line 446) |

### P&L at Market Resolution (`database.py:3078-3084`)
| Your bet | Resolved | Payout | P&L formula |
|----------|----------|--------|-------------|
| YES at $P | YES | $1.00 | `size × (1.00 - P) - fee` → WIN |
| YES at $P | NO | $0.00 | `size × (0.00 - P) - fee` → LOSE |
| NO at $P | NO | $1.00 | `size × (1.00 - P) - fee` → WIN |
| NO at $P | YES | $0.00 | `size × (0.00 - P) - fee` → LOSE |

Fee = 1.5% taker on `size × entry_price`. Backfill excludes SELL rows (`AND LOWER(pt.side) != 'sell'`).

### Why `traded_markets` Only Has Entry Markets
The UPSERT fires inside `insert_paper_trade()` which only runs for YES/NO entries (SELL excluded upstream in `paper_trading.py` line 454). So `traded_markets` naturally tracks only markets with open positions awaiting resolution.

---

## 5. `traded_markets` TABLE — COMPLETE REFERENCE

### Schema (migration 042)
```sql
CREATE TABLE traded_markets (
    market_id       TEXT PRIMARY KEY,
    condition_id    TEXT,
    bot_names       TEXT NOT NULL,       -- comma-separated: "WeatherBot,MirrorBot"
    first_trade_at  TIMESTAMP NOT NULL,
    resolved        BOOLEAN NOT NULL DEFAULT FALSE,
    resolution      TEXT,               -- "YES" or "NO"
    resolved_at     TIMESTAMP,
    last_checked_at TIMESTAMP
);
-- Partial index for fast unresolved lookups
CREATE INDEX idx_traded_markets_unresolved ON traded_markets (resolved) WHERE resolved = FALSE;
```

### Write Paths
1. **On trade insert** (`database.py:insert_paper_trade()`, ~line 2982): UPSERT appends bot_name if not already in comma list
2. **On resolution** (`database.py:save_market_resolution()`, ~line 1583): UPDATE sets resolved=TRUE
3. Both wrapped in try/except for pre-migration safety

### Read Path
- Resolution backfill Phase 2a (`resolution_backfill.py:226`): `SELECT market_id FROM traded_markets WHERE resolved = FALSE`
- Falls back to old EXISTS subquery if table doesn't exist

### Current State (VPS as of deploy)
| Bot | Total | Unresolved | Resolved |
|-----|-------|------------|----------|
| WeatherBot | 133 | 96 | 37 |
| MirrorBot | 143 | 139 | 4 |
| EnsembleBot | 86 | 83 | 3 |
| EsportsBot | 47 | 47 | 0 |
| Mixed (EsportsBot,MirrorBot) | 4 | 4 | 0 |
| **Total** | **413** | **369** | **44** |

---

## 6. RESOLUTION BACKFILL PIPELINE

**File**: `base_engine/data/resolution_backfill.py`

### Phases
1. **Phase 1** (lines ~180-218): Insert missing markets into `markets` table from paper_trades/trades
2. **Phase 2a** (lines 224-240): Get unresolved markets from `traded_markets` — OUR markets, no limit
3. **Phase 2b** (lines 243-257): On-chain trades markets — fill remaining LIMIT slots
4. **Phase 3** (lines 271-340): For each market, fetch from Gamma API → fallback CLOB API → extract resolution
5. **Phase 4** (line ~350): Update `markets` table with resolution
6. **Phase 5** (line ~370): `backfill_paper_trades_resolution()` — compute P&L on paper_trades
7. **Phase 6** (line ~390): Update positions P&L

### CLOB API Fallback (lines 276-308)
Gamma API returns nulls for `0x` condition_id markets. CLOB API has correct data in `tokens` array. `_clob_to_market_format()` extracts winner from tokens where `winner=True`.

---

## 7. STATE PERSISTENCE (CROSS-RESTART)

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

## 8. LEARNING ENGINES INVENTORY

WeatherBot has 6 learning/calibration systems:

### 1. EMOS (Ensemble Model Output Statistics)
- **File**: `base_engine/weather/emos_calibration.py`
- **What**: Post-processes NWP ensemble → calibrated μ, σ per station/lead-time
- **Formula**: `μ_cal = a + b·X̄`, `σ_cal = c + d·S²` where X̄ = ensemble mean, S² = ensemble spread
- **Status**: 13/15 stations EMOS READY (NZWN needs +3, RJTT needs +19 observations). Expected ~2026-03-15.
- **Drift detection**: DDM/EDDM in `_check_emos_drift()` — advisory only, does NOT halt trading

### 2. Station Reliability Factor
- **File**: `base_engine/weather/station_health.py`
- **What**: MSE-based station reliability → confidence multiplier
- **Source**: `weather_calibration` table, 1h cache TTL
- **Used in**: `_analyze_group()` to adjust confidence

### 3. Regime Boost
- **File**: `bots/weather_bot.py` → `_compute_regime_boost()`
- **What**: ≥3 US cities unanimous warm/cold → 1.2x Kelly boost
- **Resets**: Every scan cycle

### 4. METAR Resolution-Day Override
- **File**: `bots/weather_bot.py` → `_apply_metar_resolution_day_override()`
- **What**: On resolution day (<6h lead), override model probs with actual METAR observations
- **Trigger**: Lead time < 6 hours

### 5. Category-Specific Parameters
- **Source**: `bot_category_params` table (migration 040)
- **What**: Per market-type min_edge (temp=0.08, precip=0.10, snow=0.12, wind=0.10)
- **Loaded**: `_load_category_params()` on first scan

### 6. Prediction Logging (NOW ACTIVE)
- **File**: `bots/weather_bot.py` → `_log_weather_prediction()`
- **What**: Writes to `prediction_log` table for post-hoc accuracy analysis
- **Wired**: At trade execution time + during analysis (5 call sites total)
- **Dedup**: Skips if same market with delta < 0.01 within 600s

---

## 9. KEY CONFIG (VPS .env values)

```bash
# WeatherBot
WEATHER_FORECAST_CACHE_TTL=3600          # Raised from 1800 to reduce Open-Meteo 429s
WEATHER_HOLD_HOURS_BEFORE_RESOLUTION=48.0
WEATHER_MAX_POSITIONS=500
WEATHER_MIN_EDGE=0.08                    # Global fallback; per-type in bot_category_params
WEATHER_MAX_PER_GROUP_USD=1000
WEATHER_DAILY_LOSS_LIMIT=2000
WEATHER_MAX_CORRELATED_EXPOSURE=2000
WEATHER_KELLY_FRACTION=0.25
WEATHER_DEFAULT_SIZE=25
WEATHER_MAX_LEAD_TIME_HOURS=168
SCAN_INTERVAL_WEATHER=300                # Overridden dynamically by NWP model windows

# Shared
SIMULATION_MODE=true
PAPER_TRADING=true
RISK_MAX_POSITION_SIZE_USD=100
CATEGORY_KELLY_FRACTIONS={"weather":0.25,"crypto":0.125,"politics":0.20,"sports":0.15}

# Bankroll
WeatherBot:  capital=$5000, kelly=0.25, max_bet=$500, max_daily=$2000
```

---

## 10. CRITICAL TRAPS (DO NOT BREAK)

1. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER pass BUY/SELL.
2. **SELL paper_trades eliminated at source**: paper_trading.py skips DB persist for SELL. Do NOT re-enable.
3. **paper_trades UNIQUE constraint**: `(bot_name, market_id, side)`. insert_paper_trade uses UPSERT. Do NOT use ORM `session.add()`.
4. **paper_trades CHECK constraint**: side must be YES, NO, or SELL.
5. **`_open_position_markets` eviction**: `_close_stale_positions()` must evict from `order_gateway._open_position_markets` or positions block re-entry forever.
6. **PSEUDO_LABEL_ENABLED=false**: DO NOT enable. Only Location 1 (market resolution) labels are correct.
7. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
8. **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer.
9. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
10. **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime string.
11. **`paper_trades` has NO `metadata` JSONB column** — never assume metadata is available.
12. **Resolution backfill excludes SELL trades**: `AND LOWER(pt.side) != 'sell'` in `backfill_paper_trades_resolution()`.
13. **BOT_REGISTRY=14 bots** — shared module changes require all 14 verified.
14. **Open-Meteo rate limit**: Free tier ~10,000 req/day. Cache TTL=3600s on VPS.
15. **traded_markets try/except**: Both write paths (insert_paper_trade, save_market_resolution) have try/except for pre-migration safety. Do NOT remove these guards.
16. **MirrorBot entry price**: Uses CURRENT market price from `get_market_from_index()`, NOT trader's fill price.
17. **Exit learning**: Uses `positions` table (`status='closed'`, `unrealized_pnl`), NOT paper_trades SELL records.
18. **Stop-loss tracking**: `risk_manager.record_trade_outcome()` called directly by position_manager, independent of paper_trades.
19. **CLOB volume=0**: Never use volume gates for MirrorBot.
20. **`_market_meta_cache` in MirrorBot**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.

---

## 11. OUTSTANDING ITEMS / ROADMAP

### P0 — Immediate (WeatherBot)
1. **Monitor resolution backfill**: 96 unresolved WeatherBot markets now in queue. Check over next few hours that `realized_pnl IS NOT NULL` count increases from 37 toward 133+.
   ```bash
   ssh -i "..." ubuntu@34.251.224.21 "sudo -u polymarket psql -d polymarket -c \"SELECT COUNT(*) FILTER (WHERE realized_pnl IS NOT NULL) as resolved, COUNT(*) FILTER (WHERE realized_pnl IS NULL) as pending FROM paper_trades WHERE bot_name='WeatherBot' AND side IN ('YES','NO');\""
   ```

### P1 — Short-term (WeatherBot)
2. **EMOS recalibration**: KDFW (86.7% drift) and KLGA (50% drift) stations need recalibration. KORD/CYYZ monitoring only. KSEA is fine.
3. **DB_POOL_SIZE increase**: Currently 15 base, overflow at 22/20. Recommend increasing to 30. Change in `config/settings.py` or VPS env.
4. **NWS alerts semaphore**: Add `asyncio.Semaphore(5)` to bound concurrent NWS API calls.
5. **Parallelize scan init**: Steps 1-5 in `scan_and_trade()` are independent and can run concurrently.

### P2 — Medium-term (WeatherBot)
6. **paper_trades status field**: Never transitions from 'filled' — cosmetic fix. Add `status = 'resolved'` to `backfill_paper_trades_resolution()`.
7. **Stale position N+1 fix**: `_close_stale_positions()` queries each position individually. Batch query would reduce DB load.

### Deferred (not WeatherBot scope)
- **P0 MirrorBot**: Deploy WebSocket copy system — set `WATCHLIST_ENABLED=true`, commit, deploy
- **P1 MirrorBot**: Verify WS events include `user.address` field
- **EsportsBot**: LoL 0 opportunities (team name extraction issue)
- **H2**: PositionReconciler CLOB API — meaningless in SIMULATION_MODE
- **H3**: Redis shared rate limiter — needed before Stage 1 (5% live)

---

## 12. FILES TOUCHED THIS SESSION (81)

| File | Changes |
|------|---------|
| `schema/migrations/042_traded_markets.sql` | **NEW** — table + partial index + seed from paper_trades |
| `base_engine/data/database.py` | +16 lines: UPSERT traded_markets in `insert_paper_trade()`, +12 lines: UPDATE in `save_market_resolution()` |
| `base_engine/data/resolution_backfill.py` | Phase 2a reads `traded_markets` with fallback; Phase 2b SELECT DISTINCT fix |
| `bots/weather_bot.py` | +6 lines: `_log_weather_prediction()` wired into `_execute_weather_trade()` |

---

## 13. FILES TOUCHED IN SESSION 80 (same day, earlier context)

| File | Changes |
|------|---------|
| `bots/weather_bot.py` | `_close_stale_positions()` rewrite (date-aware), `_restore_daily_pnl_from_db()` YES/NO filter, `_load_category_params()`, `_get_min_edge()` |
| `base_engine/execution/paper_trading.py` | SELL trades skip DB persist, UPSERT duplicate guard |
| `base_engine/execution/position_manager.py` | `_refresh_exit_learning()` migrated to positions table |
| `base_engine/data/database.py` | `insert_paper_trade()` → UPSERT, P&L query YES/NO filters |
| `base_engine/monitoring/alerting.py` | `check_daily_pnl_summary()` YES/NO filter |
| `esports/data/esports_db.py` | `compute_pnl_summary()` YES/NO filter |
| `base_engine/data/resolution_backfill.py` | Phase 2a/2b split + scoring YES/NO filter |
| `schema/migrations/040_weather_category_params_seed.sql` | Per-type min_edge seeds |
| `schema/migrations/041_paper_trades_constraints.sql` | UNIQUE + CHECK constraints |

---

## 14. DIAGNOSTIC COMMANDS

```bash
# SSH to VPS
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21

# WeatherBot logs (real-time)
sudo journalctl -u polymarket-ai -f | grep weatherbot

# Resolution backfill logs
sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager -o cat | grep -i 'backfill\|resolution'

# P&L check (resolved vs pending)
sudo -u polymarket psql -d polymarket -c "SELECT COUNT(*) FILTER (WHERE realized_pnl IS NOT NULL) AS resolved, COUNT(*) FILTER (WHERE realized_pnl IS NULL) AS pending, ROUND(SUM(COALESCE(realized_pnl,0))::numeric,2) AS total_pnl FROM paper_trades WHERE bot_name='WeatherBot' AND side IN ('YES','NO');"

# traded_markets status
sudo -u polymarket psql -d polymarket -c "SELECT bot_names, COUNT(*) AS total, COUNT(*) FILTER (WHERE resolved = FALSE) AS unresolved FROM traded_markets GROUP BY bot_names ORDER BY total DESC;"

# Open positions
sudo -u polymarket psql -d polymarket -c "SELECT status, COUNT(*), ROUND(SUM(unrealized_pnl)::numeric,2) FROM positions WHERE source_bot='WeatherBot' GROUP BY status;"

# Data integrity
sudo -u polymarket psql -d polymarket -c "SELECT side, COUNT(*) FROM paper_trades GROUP BY side;"

# Recent trades
sudo -u polymarket psql -d polymarket -c "SELECT created_at, side, size, price, market_id FROM paper_trades WHERE bot_name='WeatherBot' ORDER BY created_at DESC LIMIT 10;"
```

---

## 15. SESSION SCOPE RULE

**This is a WeatherBot-only session.** Hardcoded scope:
- Only modify: `bots/weather_bot.py`, `base_engine/weather/**`, WeatherBot tests
- Shared modules (`base_engine/`, `database.py`, `config/`) ONLY if directly fixing a WeatherBot bug
- NEVER commit changes to `mirror_bot.py`, `esports_bot.py`, or other non-weather files
- Cross-bot changes require explicit user approval
- If prior sessions left uncommitted non-weather changes, leave them alone

---

## 16. TEST SUITE

- **1446 passed, 6 skipped, 0 failures** (as of this session's final run)
- Run: `python -m pytest tests/ -x -q --tb=short`
- Timeout: ~6 minutes
- WeatherBot-specific tests in `tests/` (search for `weather` or `WeatherBot`)

---

## 17. DEPLOY PATTERN

```bash
# From local machine (Windows, Git Bash):
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh

# Rollback:
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```

Atomic symlink swap: `/opt/polymarket-ai-v2` → `/opt/pa2-releases/YYYYMMDD_HHMMSS`.
Shared state: `/opt/pa2-shared/{data,saved_models,venv}`.
Migrations run automatically during deploy step 4.
Health check: 90s timeout, looks for bot scanning activity.
