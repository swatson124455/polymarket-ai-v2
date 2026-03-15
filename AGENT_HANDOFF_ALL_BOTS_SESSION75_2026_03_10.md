# AGENT HANDOFF — ALL BOTS — SESSION 75 — 2026-03-10

**Branch:** master
**VPS:** ubuntu@34.251.224.21 (SSH key: `C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem`)
**Service:** `sudo systemctl restart polymarket-ai` / logs: `sudo journalctl -u polymarket-ai -f`

---

## CURRENT GIT HEAD

```
509ebb0 fix(order_gateway): use CURRENT_DATE in SQL — asyncpg needs date not str
7a5cb8e feat(esports): P7.1/P7.5 freshness decay + PandaScore rate counter + OG db fix
ec71aea test(mirror_bot): 48 unit tests covering C1/C2/M1 and consensus logic
056133c fix(deploy): cd to release dir before migrations so pydantic-settings finds .env
9775c7c fix(deploy): anchor tar excludes to archive root to avoid excluding base_engine/data
498abaa fix(deploy): add saved_models to shared dir
4f48100 feat(shutdown): graceful SIGTERM — wait for in-progress scans before stop
44a79e5 feat(phase3): daily counters write-through + exit-check limit 10→50
5417a76 feat(deploy): Phase 1 — rsync+symlink deploy script + rollback
```

---

## WHAT THIS SESSION DID (Session 75)

This session was a continuation after a context limit. The prior session had already committed the zero-downtime architecture (Sessions 73–74). This session:

### 1. Wrote and validated the zero-downtime plan (plan mode)

Rebuttal was raised about the WeatherBot checkmark in the "What Already Persists" table. Full audit performed:
- **Q1**: `_restore_exposure_from_db()` restores BOTH `_group_exposure` AND `_city_exposure` — confirmed at lines 2031-2032 in `weather_bot.py`
- **Q2**: Queries `paper_trades JOIN markets` with `resolution IS NULL` guard. Safe post-`d3c8a0f` (all market_ids now in `markets` table)
- **Q3**: Per-category attribution N/A — WeatherBot limits are per `city:date` group (not per market type). All categories accumulate to same key correctly.
- **Verdict**: Checkmark valid ✅

Plan also addressed two improvements over original:
- **SIGKILL gap**: Added periodic 60s flush loop in `start()` so hard kills lose at most 60s of exposure state
- **Semantic documentation**: Added two-pattern comment to `036_daily_counters.sql` migration explaining ADDITIVE (EsportsBot) vs ABSOLUTE-SET (OrderGateway) write semantics

### 2. Found all Phase 1 already committed (commit `44a79e5`)

The previous session had already committed:
- `schema/migrations/036_daily_counters.sql` — `daily_counters` table with two-pattern semantics comment
- `schema/migrations/037_weather_calibration_regime.sql` — renamed from `035_` (migration `035_positions_trader_addresses.sql` stays unchanged)
- `base_engine/data/daily_counter.py` — `increment_counter()` and `restore_counters()` helpers
- `base_engine/execution/order_gateway.py` — `db=None` param + `_restore_daily_exposure()` + `_flush_daily_exposure()`
- `base_engine/base_engine.py` — wires restore + 60s periodic flush loop + shutdown flush
- `bots/esports_bot.py` — `_restore_exposure_from_db()` reads `daily_counters`; `_execute_esports_trade()` calls `increment_counter()` write-through
- `bots/mirror_bot.py` — `_check_and_execute_exits()` limit raised 10→50

### 3. Deployed Phase 1 to VPS

Steps performed this session:
1. SCP'd all 7 files to VPS `/tmp/` then `sudo cp` to deployment dir
2. Ran `python scripts/run_migrations.py --check` — saw 3 pending: `033_weather_tail_calibration.sql`, `036_daily_counters.sql`, `037_weather_calibration_regime.sql`
3. **Bug found**: `weather_tail_calibration` table owned by `postgres` not `polymarket` — `ALTER TABLE weather_tail_calibration` in migration 033 failed with `InsufficientPrivilegeError`
4. **Fixed**: `sudo -u postgres psql -d polymarket -c "ALTER TABLE weather_tail_calibration OWNER TO polymarket;"`
5. Re-ran migrations — all 3 applied: `[run] 033`, `[run] 036`, `[run] 037`
6. Restarted service: `sudo systemctl restart polymarket-ai`
7. Confirmed bots scanning: `MirrorBot startup: seeded _daily_exposure=1988.15`, `EsportsBot`, `WeatherBot` all started

### 4. Verification partially complete

The `order_gateway_daily_exposure_restored` log message was NOT seen in logs. Context limit hit before root cause was confirmed.

**Expected behavior**: On first startup after `daily_counters` table creation with no data, the restore runs but finds zero rows and logs `order_gateway_daily_exposure_restored count=0`. The log IS at `info` level but may appear before `Starting base engine services` in the init phase.

**Next session FIRST ACTION**: Run this on VPS to confirm:
```bash
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21 \
  'sudo journalctl -u polymarket-ai --since "2026-03-10 21:25:00" --no-pager | grep -E "daily_exposure_restored|daily_exposure_restore_fail|daily_exposure_flushed"'
```

If nothing appears: the `_restore_daily_exposure()` may be silently failing. Next check:
```bash
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21 \
  'sudo journalctl -u polymarket-ai --since "2026-03-10 21:25:00" --no-pager | grep -i "debug.*daily\|daily.*debug"'
```

If still nothing: compare `/opt/polymarket-ai-v2/base_engine/execution/order_gateway.py` lines 190-216 against local repo to verify the right file was deployed. The VPS may still have the old file from before commit `509ebb0` (which also touches `order_gateway.py` — CURRENT_DATE fix).

---

## FULL ARCHITECTURE STATE

### VPS Structure (as of Session 73+)
```
/opt/polymarket-ai-v2/          ← symlink → /opt/pa2-releases/TIMESTAMP/
/opt/pa2-releases/
  20260310_173856/              ← current release
  (previous releases kept up to 5)
/opt/pa2-shared/
  .env                          ← shared across releases
  data/                         ← shared: resolution_backfill.py, ingestion_error_capture.txt
  saved_models/                 ← shared: cross_game_xgb.json, rl_qtable.pkl, esports_*.pkl
  venv/                         ← shared Python venv
```

**IMPORTANT**: The Session 75 VPS deploy used `scp` to `/opt/polymarket-ai-v2/` (the SYMLINK). This works because the symlink resolves to the release directory. The new deploy script (`deploy.sh`) was NOT used — it requires one-time VPS bootstrap first (see `deploy/migrate-to-releases.sh`).

**VPS symlink state**: `/opt/polymarket-ai-v2` → `/opt/pa2-releases/20260310_173856/`

### Deploy Pattern (TWO valid approaches)

**Approach A — Legacy scp (currently in use)**:
```bash
# No bootstrap needed, works on existing flat structure
scp -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" <file> ubuntu@34.251.224.21:/tmp/
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21 "sudo cp /tmp/<file> /opt/polymarket-ai-v2/<path>/ && sudo systemctl restart polymarket-ai"
```

**Approach B — New deploy.sh (preferred for code changes)**:
```bash
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
# Rollback:
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```

---

## BOT STATUS MATRIX

| Bot | Status | Key Config | Outstanding |
|-----|--------|------------|-------------|
| **WeatherBot** | ✅ Active, trading | capital=$5000, kelly=0.25, max_bet=$500, max_daily=$2000, MAX_POSITIONS=500 | Check P&L 2026-03-13 after bulk resolution. EMOS activation ~2026-03-15 (20 actuals/station) |
| **MirrorBot** | ✅ Active | capital=$3000, kelly=0.30, max_bet=$250, max_daily=$10000 | test coverage 12% |
| **EsportsBot** | ✅ Active, write-through daily counters live | capital=$5000, kelly=0.25, max_bet=$100, max_daily=$500 | P7 roadmap DONE |
| **EsportsLiveBot** | ✅ Active | same | — |
| **EsportsSeriesBot** | ✅ Active | `BOT_ENABLED_ESPORTS_SERIES=true` | — |
| **ArbitrageBot** | ❌ Disabled | `BOT_ENABLED_ARBITRAGE=false` | — |
| **CrossPlatformArbBot** | ❌ Disabled | `BOT_ENABLED_CROSS_PLATFORM_ARB=false` | — |
| **OracleBot** | ❌ Disabled | `BOT_ENABLED_ORACLE=false` | — |
| **SportsBot/InjuryBot/LiveBot/ArbBot** | ❌ Disabled | respective flags | — |
| **LLMForecasterBot** | ❌ Disabled | `BOT_ENABLED_LLM_FORECASTER=false` | — |
| **LogicalArbBot** | ❌ Disabled | `BOT_ENABLED_LOGICAL_ARB=false` | — |

**Active bots: MirrorBot, WeatherBot, EsportsBot, EsportsLiveBot, EsportsSeriesBot (5 of 14)**

---

## STATE PERSISTENCE MATRIX (COMPLETE — all gaps closed)

| State | Bot | Mechanism | Status |
|-------|-----|-----------|--------|
| Open positions | All | `order_gateway.seed_positions_from_db()` | ✅ |
| Total `_total_exposure_usd` | All | Derived from position seed | ✅ |
| `_daily_exposure_usd` | All 14 bots | `daily_counters` table — 60s flush + SIGTERM flush + startup restore | ✅ `44a79e5` |
| `_game_exposure` | EsportsBot | `daily_counters` write-through via `increment_counter()` | ✅ `44a79e5` |
| `_group_exposure` + `_city_exposure` | WeatherBot | `_restore_exposure_from_db()` from paper_trades JOIN markets | ✅ `47fe1bf` |
| `_daily_exposure` | MirrorBot | `_restore_state_on_startup()` from paper_trades SUM | ✅ prior session |
| Exit cooldowns (`_recently_exited`) | WeatherBot | Redis TTL via `_save_exit_to_redis()` / `_restore_exits_from_redis()` | ✅ `3f6f2de` |
| 429 cooldowns | WeatherBot | Redis via `restore_state()` | ✅ |
| XGB cross-game model | EsportsBot | `saved_models/cross_game_xgb.json` in `/opt/pa2-shared/saved_models/` | ✅ |
| RL Q-table | All | `data/rl_qtable.pkl` in `/opt/pa2-shared/data/` | ✅ |
| Calibration | WeatherBot/EsportsBot | `calibration` DB table | ✅ |
| Prediction log | All | `prediction_log` DB table | ✅ |

---

## KEY FILES MODIFIED THIS SESSION (SESSION 75 + PRIOR SESSIONS)

### `base_engine/execution/order_gateway.py` (commit `44a79e5` + `509ebb0`)
- `__init__`: `db=None` kwarg added (line 42), `self.db = db` (line 58)
- `_restore_daily_exposure()` (lines 190-216): reads `daily_counters WHERE counter_date = CURRENT_DATE AND counter_name = 'daily_exposure_usd'`, seeds `_daily_exposure_usd`
- `_flush_daily_exposure()` (lines 218-247): absolute-set UPSERT to `daily_counters` with `CURRENT_DATE` literal (NOT Python strftime string — asyncpg requires `datetime.date` objects for DATE params or use SQL `CURRENT_DATE`)

### `base_engine/base_engine.py` (commit `44a79e5` + `4f48100`)
- Line 1035: `db=self.db` passed to `OrderGateway()` constructor
- Lines 1079-1083: `await self.order_gateway._restore_daily_exposure()` after `seed_positions_from_db()`
- Lines 1434-1447: Periodic 60s flush loop `_periodic_exposure_flush()` launched via `asyncio.ensure_future()`
- Line 1606: `await self.order_gateway._flush_daily_exposure()` in `stop()` before `cache.close()`
- `_idle_event` + `wait_for_idle()` (Session 73, `4f48100`): all bot scans set idle event; `main.py` awaits with 25s timeout before SIGTERM teardown

### `base_engine/data/daily_counter.py` (commit `44a79e5`)
- `increment_counter(db, bot_id, name, amount)` — additive UPSERT (EsportsBot pattern)
- `restore_counters(db, bot_id)` → `Dict[str, float]` — reads today's counters

### `schema/migrations/036_daily_counters.sql` (commit `44a79e5`)
```sql
CREATE TABLE IF NOT EXISTS daily_counters (
    bot_id        TEXT        NOT NULL,
    counter_date  DATE        NOT NULL DEFAULT CURRENT_DATE,
    counter_name  TEXT        NOT NULL,
    counter_value NUMERIC     NOT NULL DEFAULT 0,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (bot_id, counter_date, counter_name)
);
CREATE INDEX IF NOT EXISTS idx_daily_counters_bot_date ON daily_counters (bot_id, counter_date);
```
**Two write patterns documented in migration comment** — do NOT mix for same `(bot_id, counter_name)`:
1. ADDITIVE — EsportsBot `game_{game}` keys via `increment_counter()`
2. ABSOLUTE-SET — OrderGateway `daily_exposure_usd` key via `_flush_daily_exposure()`

### `schema/migrations/037_weather_calibration_regime.sql`
Renamed from `035_weather_calibration_regime.sql` (duplicate prefix fix). DDL is `IF NOT EXISTS` — safe to re-run on VPS.

### `bots/esports_bot.py` (commit `44a79e5`)
- `_restore_exposure_from_db()`: reads `daily_counters WHERE bot_id='EsportsBot' AND counter_name LIKE 'game_%'` and seeds `_game_exposure`. Guard: `_exposure_restored` bool prevents double-run.
- `_execute_esports_trade()`: calls `await _inc_daily(db, "EsportsBot", f"game_{game}", size)` write-through after every successful trade.

### `bots/mirror_bot.py` (commit `44a79e5`)
- `_check_and_execute_exits()` limit: 10 → 50 (reduces stale-traders-set miss window after restart)

### `deploy/deploy.sh` (commit `5417a76`)
Full rsync+symlink atomic deploy with 90s health check and auto-rollback. Uses `/opt/pa2-releases/` directory structure.

---

## CRITICAL TRAPS — DO NOT BREAK

1. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL". Polymarket Data API returns BUY/SELL → resolve via `markets.yes_token_id/no_token_id`.
2. **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT a Python `strftime` string. asyncpg requires `datetime.date` objects for DATE params — strings raise `DataError: 'str' has no toordinal`. Fixed in `509ebb0`.
3. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
4. **`daily_counters` write semantics**: ADDITIVE for EsportsBot (`counter_value + amount`), ABSOLUTE-SET for OrderGateway (`counter_value = total`). NEVER mix patterns for same `(bot_id, counter_name)`.
5. **`_restore_daily_exposure()`**: uses `CURRENT_DATE` SQL literal (not Python date string). No `_daily_exposure_date` is set because `get_daily_exposure_usd()` handles the date boundary itself on first read.
6. **`_market_meta_cache` in MirrorBot**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
7. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
8. **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer.
9. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
10. **CLOB volume=0** — Never use volume gates for MirrorBot.
11. **`_open_positions` on restart**: MirrorBot clears in-memory; re-enters by EOD UTC.
12. **BOT_REGISTRY=14 bots** — shared module change requires all 14 verified.
13. **`websockets.exceptions` must be imported explicitly** (v15 lazy-loads).
14. **`asyncio.create_task()` for financial write-throughs**: FORBIDDEN — fire-and-forget masks DB errors. Always `await`.

---

## VPS DATABASE STATE

- **`daily_counters` table**: CREATED this session (migration 036 applied 2026-03-10 21:25 UTC)
- **`weather_tail_calibration` table**: Ownership changed from `postgres` → `polymarket` this session (was blocking migration 033)
- **`glicko2_ratings`** (031), **`weather_calibration_crps`** (032), **`weather_tail_calibration`** (033): All applied this session
- **Applied migrations total**: 001–033, 036, 037

---

## OUTSTANDING VERIFICATION

### IMMEDIATE (next session first action):

**1. Confirm `order_gateway_daily_exposure_restored` in logs:**
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
ssh -i "$KEY" ubuntu@34.251.224.21 \
  'sudo journalctl -u polymarket-ai --since "2026-03-10 21:25:00" --no-pager | grep -E "daily_exposure_restored|daily_exposure_flushed|daily_exposure_restore_fail"'
```
Expected: `order_gateway_daily_exposure_restored count=0` (no data yet, day just started)

**2. Confirm 60s periodic flush is firing:**
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
ssh -i "$KEY" ubuntu@34.251.224.21 \
  'sudo journalctl -u polymarket-ai --since "2026-03-10 21:25:00" --no-pager | grep daily_exposure_flushed'
```
Expected after trades: `order_gateway_daily_exposure_flushed count=N`

**3. Confirm `daily_counters` has data after bots trade:**
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
ssh -i "$KEY" ubuntu@34.251.224.21 \
  'PGPASSWORD=polymarket_s46 psql -h localhost -U polymarket -d polymarket -c "SELECT * FROM daily_counters ORDER BY updated_at DESC LIMIT 20;"'
```

**4. Confirm EsportsBot write-through (game exposure):**
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
ssh -i "$KEY" ubuntu@34.251.224.21 \
  'sudo journalctl -u polymarket-ai -n 200 --no-pager | grep -i "esports.*exposure_restored\|esportsbot.*exposure"'
```

### UPCOMING:
- **2026-03-12**: Check WeatherBot P&L — remaining 175 paper_trades should resolve (march 10-12 weather markets settling)
- **2026-03-13**: Full P&L audit of WeatherBot
- **2026-03-15–17**: EMOS activation threshold (~20 actuals/station)
- **Sizing audit**: Kelly + S-T sizing — check VPS env values, verify `combined_boost` doesn't breach per-group cap
- **MirrorBot tests**: 12% coverage, 0 tests for C1/C2/M1 paths

---

## KEY CONFIG (LIVE VPS VALUES)

```bash
# WeatherBot
WEATHER_MIN_EDGE=0.08
WEATHER_MAX_PER_GROUP_USD=200.0
WEATHER_MAX_CORRELATED_EXPOSURE=500.0
WEATHER_DAILY_LOSS_LIMIT=500.0
WEATHER_MAX_POSITIONS=500              # raised from 200 in c2d798a
WEATHER_KELLY_FRACTION=0.25
WEATHER_MAX_BET_USD=500
WEATHER_MAX_DAILY_USD=2000

# MirrorBot
MIRROR_MAX_POSITIONS=200
MIRROR_MAX_PER_MARKET=400
MIRROR_KELLY_FRACTION=0.30
MIRROR_MAX_BET_USD=250
MIRROR_MAX_DAILY_USD=10000            # raised from 1500 in prior session

# EsportsBot
ESPORTS_MIN_CONFIDENCE=0.52
ESPORTS_MIN_EDGE=0.08
ESPORTS_SERIES_HEDGE_ENABLED=true
BOT_ENABLED_ESPORTS_SERIES=true
ESPORTS_FRESHNESS_DECAY_SECONDS=30.0  # reduced from 120.0 in 7a5cb8e (live match freshness)

# Phase limits
PHASE_MAX_BET_USD=1000   # not the real cap — BotBankrollManager is
SIMULATION_MODE=true     # paper trading active
```

---

## INFRASTRUCTURE

- **VPS**: Ubuntu-3, 34.251.224.21, 16GB/4vCPU, eu-west-1
- **SSH Key**: `C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **Service**: `sudo systemctl restart polymarket-ai`
- **Logs**: `sudo journalctl -u polymarket-ai -f`
- **DB**: PostgreSQL localhost, user=polymarket, db=polymarket, pw=polymarket_s46
- **Redis**: pw=78psiRhepTgrmWSoy3cgNEIr
- **Service file**: `/etc/systemd/system/polymarket-ai.service`
- **Venv**: `/opt/pa2-shared/venv/bin/activate` (or `/opt/polymarket-ai-v2/venv/bin/activate` on VPS — both resolve to same via symlink)
- **Shared data**: `/opt/pa2-shared/{data,saved_models,venv}`

---

## HISTORICAL CONTEXT (Sessions 53–75 Summary)

### WeatherBot (Sessions 53–75)
- Session 53–60: Built from scratch. EMOS calibration, multi-city, multi-type.
- Session 62: 429 backoff + TTL jitter (`2dc8073`), Redis 429 persistence + DB warm-up (`3f6f2de`)
- Session 70: Resolution backfill fix — `paper_trades` not in Phase 1 SQL (`d3c8a0f`). +$479.41 P&L confirmed from 90 resolved trades.
- Session 70: Exposure restore (`47fe1bf`) — `_group_exposure` + `_city_exposure` restored from `paper_trades JOIN markets` on startup
- Session 73: `WEATHER_MAX_POSITIONS` 200→500 (`c2d798a`), regime column migration (`035_weather_calibration_regime.sql`)
- Session 75: VPS deployment + migration fixes (this session)

### MirrorBot (Sessions 60–75)
- Session 60: C1/C2/M1 trading logic built
- Session 69: `_restore_state_on_startup()` — seeds `_daily_exposure` + `_open_positions` from DB (`AGENT_HANDOFF_MIRRORBOT_SESSION69_FULL_2026_03_09.md`)
- Session 70: 48 unit tests (`ec71aea`), coverage 12%
- Session 75 (`44a79e5`): Exit check limit 10→50

### EsportsBot (Sessions 63–75)
- Session 63–64: Deep dive implementation (see `memory/deep_dive_implementation.md`)
- Session 72: P0–P2.2 — Glicko2 recent form features, log_prediction for series hedge, backfill outcomes, calibration, LearningScheduler cross-game retrain
- Session 73 (`44a79e5`): Game exposure write-through + restore from `daily_counters`
- Session 74 (`7a5cb8e`): P7.1 freshness decay 120s→30s, P7.5 hourly PandaScore request counter
- Session 74 (`509ebb0`): OrderGateway `db=None` at construction → late-bind `self.order_gateway.db = self.db` in `base_engine.start()`; asyncpg DATE fix → `CURRENT_DATE`

### Zero-Downtime Architecture (Sessions 73–75)
- Session 73 (`44a79e5`): Phase 1 — `daily_counters` table, OrderGateway persistence, SIGTERM flush, idle-event wait
- Session 73 (`5417a76`, `498abaa`, `9775c7c`, `056133c`, `4f48100`): Phase 2 — `deploy.sh`, `rollback.sh`, `migrate-to-releases.sh`, shared dir for models/data
- Session 75: VPS migration deployment + table ownership fix

---

## HANDOFF FILES REFERENCE

| File | Coverage |
|------|----------|
| `WEATHERBOT_FULL_AGENT_HANDOFF.md` | WeatherBot sessions 53-69 full detail |
| `AGENT_HANDOFF_WEATHERBOT_SESSION69_2026_03_09.md` | WeatherBot session 69 |
| `AGENT_HANDOFF_MIRRORBOT_SESSION69_FULL_2026_03_09.md` | MirrorBot full carbon-copy |
| `AGENT_HANDOFF_ESPORTS_SESSION71_2026_03_09.md` | EsportsBot session 71 |
| `memory/deep_dive_implementation.md` | EsportsBot sessions 63-64 Tiers 1-3 |
| `memory/session_history.md` | Sessions 55-71 detail |
| `AGENT_HANDOFF_ALL_BOTS_SESSION75_2026_03_10.md` | **THIS FILE** |
