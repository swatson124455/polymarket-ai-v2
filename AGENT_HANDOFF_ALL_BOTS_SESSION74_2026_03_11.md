# AGENT HANDOFF — ALL BOTS — Session 74 → Session 75
**Date**: 2026-03-11  **Branch**: master  **Head**: `509ebb0`

---

## 1. WHAT THIS SYSTEM IS

**Polymarket AI V2** — 15 autonomous trading bots on Polymarket prediction markets. Real capital at risk. All bots run in a **single asyncio event loop** on one VPS (Ubuntu, 34.251.224.21) as a systemd service. No per-bot processes.

**Bot registry** (15 total, MomentumBot DELETED, EnsembleBot ARCHIVED −$5.6k):

| Bot | Class | Capital | Max Bet | Max Daily | Status |
|-----|-------|---------|---------|-----------|--------|
| WeatherBot | WeatherBot | $5,000 | $500 | $2,000 | ✅ Trading |
| MirrorBot | MirrorBot | $3,000 | $250 | $10,000 | ✅ Trading |
| EsportsBot | EsportsBot | $500 | $50 | $200 | ✅ Scanning |
| EsportsLiveBot | EsportsLiveBot | $500 | $100 | $500 | ✅ Scanning |
| EsportsSeriesBot | EsportsSeriesBot | $500 | $100 | $500 | ✅ Scanning |
| ArbitrageBot | ArbitrageBot | — | — | — | ✅ Active |
| CrossPlatformArbBot | CrossPlatformArbBot | — | — | — | ✅ Active |
| LogicalArbBot | LogicalArbBot | — | — | — | ✅ Active |
| OracleBot | OracleBot | — | — | — | ✅ Active |
| LLMForecasterBot | LLMForecasterBot | — | — | — | ✅ Active |
| SportsBot | SportsBot | — | — | — | ✅ Active |
| SportsInjuryBot | SportsInjuryBot | — | — | — | ✅ Active |
| SportsLiveBot | SportsLiveBot | — | — | — | ✅ Active |
| SportsArbBot | SportsArbBot | — | — | — | ✅ Active |

---

## 2. INFRASTRUCTURE

### VPS
```
Host:     ubuntu@34.251.224.21  (AWS Lightsail, eu-west-1, 16GB/4vCPU)
SSH key:  ~/.ssh/LightsailDefaultKey-eu-west-1.pem
Service:  sudo systemctl restart polymarket-ai
Logs:     journalctl -u polymarket-ai -f
Code dir: /opt/polymarket-ai-v2  (symlink → /opt/pa2-releases/TIMESTAMP/)
Shared:   /opt/pa2-shared/{.env, venv/, data/, saved_models/}
```

### Deploy (Session 73+ — rsync + atomic symlink swap)
```bash
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
# Rollback:
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```
**`migrate-to-releases.sh`: DO NOT RUN AGAIN — already done.**

**Old SCP fallback** (if deploy.sh unavailable):
```bash
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem <files> ubuntu@34.251.224.21:/tmp/
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo cp /tmp/<file> /opt/polymarket-ai-v2/<path>/ && sudo systemctl restart polymarket-ai"
```

### Database
```
Host: localhost  DB: polymarket  User: polymarket  PW: polymarket_s46
Key tables: paper_trades, positions, markets, prediction_log, esports_prediction_log,
            glicko2_ratings, weather_calibration, daily_counters, bot_heartbeats,
            schema_migrations (tracks applied migrations)
```

### Redis
```
PW: 78psiRhepTgrmWSoy3cgNEIr
Used for: Weather 429 cooldowns (weatherbot:429:*), WeatherBot exit cooldowns (weatherbot:exit:*)
```

### Migrations
```
Directory: schema/migrations/ (numbered 001–037)
Runner:    python scripts/run_migrations.py
Latest:    037_weather_calibration_regime.sql
           036_daily_counters.sql
Applied via: run_migrations.py on each deploy (idempotent, tracks in schema_migrations table)
```

---

## 3. ARCHITECTURE — CRITICAL FACTS

### Single asyncio loop
All 15 bots share one event loop, one BaseEngine, one DB pool (15 connections, max 20 with overflow). Any blocking call blocks ALL bots. 60s scan timeout enforced per bot.

### Sizing vs limits
- **BotBankrollManager** → handles SIZING (Kelly, max_bet_usd)
- **risk_manager** → handles LIMITS (daily caps, position limits)
- Both must pass. `risk_manager.calculate_position_size()` is **DEPRECATED** — BotBankrollManager is the real sizer.

### Order side
`place_order()` requires `side="YES"` or `side="NO"`. **NEVER "BUY"/"SELL"**.
Polymarket Data API returns BUY/SELL → resolve via `markets.yes_token_id` / `markets.no_token_id`.

### Paper trading
`SIMULATION_MODE=true` — all orders are paper trades stored in `paper_trades` table. Not on-chain yet.
Phase caps: `PHASE_MAX_BET_USD=$1000` but BotBankrollManager `max_bet_usd` is the real cap (lower).

### asyncpg type traps
- **JSONB**: Use `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **DATE columns**: Use `CURRENT_DATE` SQL literal NOT a Python strftime string — asyncpg requires `datetime.date` objects for DATE params; strings raise `DataError: 'str' has no toordinal`

### `paper_trades` schema fact
NO `metadata` JSONB column exists. Never assume it's available. Use JOINs to `esports_prediction_log` for game info.

---

## 4. STATE PERSISTENCE DECISION TREE (from CLAUDE.md)

| State type | Example | Correct mechanism |
|-----------|---------|------------------|
| Purely additive, resets daily | EsportsBot `_game_exposure` | `daily_counters` write-through |
| Net counter (up+down), resets daily | MirrorBot `_daily_exposure` | Query `paper_trades` SUM on startup |
| TTL-based cooldown | WeatherBot `_recently_exited` | Redis key with matching TTL |
| Open position set | MirrorBot `_open_positions` | `positions` table |
| Not needed across restarts | API caches, dedup | Leave in memory |

**Do NOT use `asyncio.create_task()` for financial write-throughs** — always `await`.

### Current implementations (all verified working):
- MirrorBot `_daily_exposure` → `paper_trades` SUM in `_restore_state_on_startup()` ✅
- MirrorBot `_open_positions` → `positions` table restore ✅
- EsportsBot `_game_exposure` → `daily_counters` write-through + `_restore_exposure_from_db()` ✅
- WeatherBot `_recently_exited` → Redis TTL `_save_exit_to_redis()` / `_restore_exits_from_redis()` ✅
- WeatherBot `_group_exposure`/`_city_exposure` → DB restore ✅
- OrderGateway `_daily_exposure_usd` → `daily_counters` flush every 60s + SIGTERM ✅

---

## 5. SESSION HISTORY — WHAT WAS BUILT (Sessions 72–74)

### Session 72 — State Persistence + EsportsBot P0–P2.2
**Commits**: `e3ba3f0`, `a1d20c9`, `47fe1bf`, `c2d798a`, `d3c8a0f`

**EsportsBot (`e3ba3f0`, `a1d20c9`)**:
- `_restore_exposure_from_db()` — seeds `_game_exposure` from `daily_counters` on first scan
- `_backfill_esports_outcomes()` — every 10 scans, resolves settled paper_trades into prediction_log
- Cross-game XGB: `team_a/b_recent_form` features added (feats 7→9), matching training
- `LearningScheduler` wired with `esports_trainer` slot; fires `train_cross_game()` on retrain cycle
- `_analyze_series()` calls `log_prediction()` for hedge opps

**WeatherBot (`47fe1bf`)**:
- `_save_exit_to_redis()` — on position exit, stores `weatherbot:exit:{market_id}` with 900s TTL
- `_restore_exits_from_redis()` — on startup, seeds `_recently_exited` from Redis remaining TTL
- `WEATHER_MAX_POSITIONS`: 200→500 (was blocking all trades at cap 201)

**Resolution backfill (`d3c8a0f`) — CRITICAL FIX**:
- Root cause: backfill Phase 1 only queried `trades` (on-chain), missing all WeatherBot `paper_trades`
- Fix: Phase 1 UNIONs `paper_trades.market_id`; `missing_limit` 200→500
- Result: 282 markets inserted, 90 resolved, **+$479.41 paper P&L confirmed**

### Session 73 — Zero-Downtime Deploy Infrastructure
**Commits**: `44a79e5`, `4f48100`, `498abaa`, `9775c7c`, `056133c`, `5417a76`

**Phase 3 — daily_counters (`44a79e5`)**:
- `schema/migrations/036_daily_counters.sql` — new table `(bot_id, counter_date, counter_name, counter_value)`
- `base_engine/data/daily_counter.py` — `increment_counter()` (ADDITIVE UPSERT) + `restore_counters()`
- `base_engine/execution/order_gateway.py` — `_restore_daily_exposure()` + `_flush_daily_exposure()` (ABSOLUTE-SET pattern)
- `base_engine/base_engine.py` — wires restore at `start()`, 60s periodic flush loop, SIGTERM flush
- `bots/esports_bot.py` — game exposure write-through + restore path
- `bots/mirror_bot.py` — exit-check limit 10→50

**Phase 2 — Graceful SIGTERM (`4f48100`)**:
- `bots/base_bot.py` — `_idle_event` asyncio.Event: cleared when scan starts, set in `finally`
- `bots/base_bot.py` — `wait_for_idle(timeout=25s)` — awaits idle event
- `main.py` — shutdown gathers `wait_for_idle()` across all bots before `stop()` calls

**Phase 1 — Deploy script (`5417a76`)**:
- `deploy/deploy.sh` — tar → upload → extract → symlinks → migrate → atomic swap → health check → auto-rollback
- `deploy/rollback.sh` — swaps symlink back to previous release
- `deploy/migrate-to-releases.sh` — ONE-TIME done, DO NOT rerun

**Bugfixes (`498abaa`, `9775c7c`, `056133c`)**:
- `saved_models/` → `/opt/pa2-shared/` (XGBoost persists across deploys)
- tar `--exclude='data'` was excluding `base_engine/data/` subpackage → fixed to `--exclude='./data'`
- Migration `PermissionError: .env` → fixed with `cd $NEW_RELEASE` before migrations

### Session 74 — EsportsBot P7 Complete + OrderGateway Bugfixes
**Commits**: `7a5cb8e`, `509ebb0`

**P7.1 (`7a5cb8e`)**: `_compute_confluence_score()` freshness decay for live markets: 120s→30s.
Predictions for live CS2/LoL age 4× faster → stale predictions don't inflate confluence.
Still overridable via `ESPORTS_FRESHNESS_DECAY_SECONDS` env var.

**P7.5 (`7a5cb8e`)**: `PandaScoreClient._get()` hourly request counter.
- Resets every 3600s window
- `pandascore_request_count` (INFO) every 100 req
- `pandascore_rate_limit_budget` (WARNING) at 500/750/900/950/990
- `pandascore_rate_limited` includes `requests_this_hour`
- Budget: 1000 req/hr free tier, baseline ~360/hr

**P7.4**: `BOT_ENABLED_ESPORTS_SERIES=true` was already live on VPS. EsportsSeriesBot scanning at 475ms.

**OrderGateway db=None fix (`509ebb0`)**: Two bugs:
1. `db=None` at construction (DB initialized after OG constructed) → `base_engine.start()` now late-binds `self.order_gateway.db = self.db` inside `if self.db is not None:` block
2. asyncpg DATE column got Python strftime string → replaced `:date` param with `CURRENT_DATE` SQL literal in `_restore_daily_exposure()` and `_flush_daily_exposure()`
Result: `order_gateway_daily_exposure_restored count=0` logs on startup ✅

**Also in `ec71aea` (MirrorBot tests)**: 48 unit tests added covering C1/C2/M1 and consensus logic.

---

## 6. CURRENT VPS STATE (2026-03-11 00:14 UTC)

```
Service: running (PID 1886137)
Open positions: 230
daily_counters: 0 rows (no trades placed since last restart — will populate on first trade)
EsportsLiveBot: detecting price moves, 5 active games
  market 0x578f: 0.31→0.64
  market 0x472: 0.98→0.016 (game resolved)
  market 0x6ed: 0.34→0.61
```

**Scan performance**:
- EsportsBot: alternating ~1-3s fast / ~5-8s slow (every other scan hits PandaScore API; normal)
- EsportsLiveBot: 0.3–56ms ✅
- EsportsSeriesBot: ~475ms ✅
- MirrorBot: ~8-10s (slow scan warning threshold = 5000ms — acceptable)

---

## 7. KEY CONFIG (live VPS .env values)

```bash
# WeatherBot
WEATHER_MAX_POSITIONS=500          # raised from 200 (was blocking all trades)
WEATHER_CAPITAL=5000
WEATHER_MAX_BET_USD=500
WEATHER_MAX_DAILY_USD=2000
WEATHER_KELLY_FRACTION=0.25

# MirrorBot
MIRROR_CAPITAL=3000
MIRROR_MAX_BET_USD=250
MIRROR_MAX_DAILY_USD=10000         # raised from 1500
MIRROR_KELLY_FRACTION=0.30
MIRROR_MAX_POSITIONS=200
MIRROR_MAX_PER_MARKET=400

# EsportsBot
ESPORTS_CAPITAL=500
ESPORTS_MAX_BET_USD=50
ESPORTS_MAX_DAILY_USD=200
ESPORTS_KELLY_FRACTION=0.20
ESPORTS_MIN_CONFIDENCE=0.52
ESPORTS_MIN_EDGE=0.08
ESPORTS_SERIES_HEDGE_ENABLED=true
BOT_ENABLED_ESPORTS=true
BOT_ENABLED_ESPORTS_LIVE=true
BOT_ENABLED_ESPORTS_SERIES=true    # enabled, scanning cleanly

# System
SIMULATION_MODE=true               # paper trading
PSEUDO_LABEL_ENABLED=false         # DO NOT enable
PHASE_MAX_BET_USD=1000             # paper phase cap (BotBankrollManager is real cap)
```

---

## 8. KEY FILES

### Core engine
```
base_engine/base_engine.py          — BaseEngine: starts/stops all services, wires everything
base_engine/execution/order_gateway.py — order execution, position tracking, daily exposure flush
base_engine/risk/bankroll_manager.py   — BotBankrollManager: Kelly sizing per bot
base_engine/risk/risk_manager.py       — limits enforcement (daily cap, position count)
base_engine/data/daily_counter.py      — increment_counter() + restore_counters() for additive state
base_engine/data/data_ingestion.py     — market ingestion + resolution_backfill runner
base_engine/data/resolution_backfill.py — resolves paper_trades P&L from market outcomes
```

### Bots
```
bots/base_bot.py              — BaseBot ABC: scan loop, _idle_event, wait_for_idle, place_order
bots/weather_bot.py           — WeatherBot: NOAA forecast → calibrated P(YES) → trade
bots/mirror_bot.py            — MirrorBot: mirrors elite traders' positions
bots/esports_bot.py           — EsportsBot: pre-game ML predictions (XGB + Glicko-2)
bots/esports_live_bot.py      — EsportsLiveBot: in-game price monitoring via WS
bots/esports_series_bot.py    — EsportsSeriesBot: series-level hedge opportunities
```

### Esports ML
```
esports/data/pandascore_client.py   — PandaScore API client (30s cache TTL, rate counter)
esports/models/                     — XGB model training per game + cross-game model
esports/glicko2/                    — Glicko-2 rating tracker per game
saved_models/cross_game_xgb.json   — (on VPS: /opt/pa2-shared/saved_models/) persists deploys
```

### Deploy
```
deploy/deploy.sh              — production deploy: rsync + atomic symlink swap
deploy/rollback.sh            — revert to previous release
deploy/migrate-to-releases.sh — ONE-TIME DONE, do not rerun
```

### Schema
```
schema/migrations/036_daily_counters.sql — daily_counters table (two write patterns documented)
schema/migrations/037_weather_calibration_regime.sql — regime column for WeatherBot calibration
```

---

## 9. CRITICAL TRAPS — DO NOT BREAK

1. **YES/NO mandate**: `place_order(side="YES"/"NO")`. NEVER "BUY"/"SELL". API returns BUY/SELL → map via `markets.yes_token_id`/`no_token_id`.

2. **asyncpg DATE**: Use `CURRENT_DATE` SQL literal. NOT strftime strings. asyncpg needs `datetime.date`, strings raise `DataError: 'str' has no toordinal`.

3. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.

4. **`paper_trades` has NO `metadata` column** — never query it. Join `esports_prediction_log` for game info.

5. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass. `risk_manager.calculate_position_size()` DEPRECATED.

6. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable. Only Location 1 (market resolution) labels are correct.

7. **`_market_meta_cache` in MirrorBot** — 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand to 4-tuple.

8. **CLOB volume=0** — Never use volume gates for MirrorBot. CLOB esports tokens always show 0 volume.

9. **No `asyncio.create_task()` for financial write-throughs** — fire-and-forget causes silent DB corruption. Always `await`.

10. **BOT_REGISTRY shared modules** — change to `base_bot.py`, `base_engine.py`, `order_gateway.py`, `risk_manager.py`, `bankroll_manager.py`, `database.py`, `main.py` affects ALL 15 bots. Run full pytest (1381 tests) before deploy.

11. **VPS deploys**: Working tree ≠ VPS ≠ git HEAD. Must run `deploy.sh` (or manual SCP) to push changes live.

12. **`websockets.exceptions`** must be imported explicitly (v15 lazy-loads).

13. **`position current_price`** auto-updated every 10s by `position_manager._update_current_prices()`.

---

## 10. OUTSTANDING WORK (priority order)

### High priority
1. **EsportsBot slow scan investigation** — alternating ~5-8s / ~1-3s every 120s scan cycle. Likely PandaScore API call taking 5-8s every other scan due to cache miss (30s TTL vs 120s scan interval). Could optimize: pre-warm PandaScore cache from EsportsLiveBot (which already refreshes every 15s) or increase PandaScore cache TTL to 60s.

2. **WeatherBot P&L check** — 175 pending paper_trades expected to resolve 2026-03-10 to 2026-03-12. Check ~2026-03-13: `SELECT ROUND(SUM(realized_pnl)::numeric,2) FROM paper_trades WHERE source_bot='WeatherBot';`

### Medium priority
3. **MirrorBot test coverage** — currently 12% (48 tests from `ec71aea`). C3/M2/M3 paths untested.

4. **`migrate-to-releases.sh` already run** — `deploy.sh` is the deploy path now. But VPS currently uses direct SCP for hotfixes (deploy.sh has a 90s health check which is good for production, slower for quick iterations). Consider when to standardize fully on deploy.sh.

### Low priority
5. **EsportsBot P8 roadmap** (not yet defined — define based on trade outcomes):
   - Monitor `daily_counters` table once first trades happen (verify game exposure write-through)
   - Monitor PandaScore hourly request count (goal: confirm <1000/hr)
   - Consider market cache explicit invalidation for live matches (P7.1 alt approach)

6. **ArbitrageBot / LogicalArbBot / OracleBot** — no recent improvements, no outstanding issues flagged.

---

## 11. HOW TO VERIFY SYSTEM HEALTH

```bash
# Bots scanning
journalctl -u polymarket-ai -f | grep scan_ms

# EsportsBot game exposure persisted (check after first trade)
sudo -u polymarket psql -d polymarket -c "SELECT * FROM daily_counters;"

# OrderGateway restore working (check after restart)
journalctl -u polymarket-ai -n 200 --no-pager | grep order_gateway_daily_exposure_restored

# WeatherBot P&L
sudo -u polymarket psql -d polymarket -c \
  "SELECT COUNT(*), ROUND(SUM(realized_pnl)::numeric,2) FROM paper_trades WHERE source_bot='WeatherBot' AND realized_pnl IS NOT NULL;"

# Open positions
sudo -u polymarket psql -d polymarket -c "SELECT status, COUNT(*) FROM positions GROUP BY status;"

# PandaScore rate limit (after some trades)
journalctl -u polymarket-ai | grep pandascore_request_count | tail -5

# MirrorBot state on restart
journalctl -u polymarket-ai | grep "MirrorBot startup"
# Expected: "seeded _daily_exposure=X from today's paper_trades"
#           "restored N open positions from DB"
```

---

## 12. DEVELOPMENT RULES (from CLAUDE.md)

1. **State the bug** in one sentence before touching any file.
2. **List files you will touch** — if >3, justify.
3. **Grep for dependents** before modifying any shared module.
4. **Git snapshot** before editing.
5. **Read the entire file** you're modifying.
6. **One fix per commit** — no "while I'm in here" changes.
7. **Preserve every function signature** — if you change one, update every caller.
8. **No silent behavior changes** — document what changed and why.
9. **Never delete code you don't understand.**
10. **No new dependencies** without justification.

**Forbidden patterns**: band-aid try/except, shotgun fixes (changing 4 things hoping one works), scope creep, silent migration, optimistic rewrites.

---

## 13. RECENT COMMIT LOG

```
509ebb0 fix(order_gateway): use CURRENT_DATE in SQL — asyncpg needs date not str
7a5cb8e feat(esports): P7.1/P7.5 freshness decay + PandaScore rate counter + OG db fix
ec71aea test(mirror_bot): 48 unit tests covering C1/C2/M1 and consensus logic
056133c fix(deploy): cd to release dir before migrations so pydantic-settings finds .env
9775c7c fix(deploy): anchor tar excludes to archive root to avoid excluding base_engine/data
498abaa fix(deploy): add saved_models to shared dir — prevent model loss on deploy
4f48100 feat(shutdown): graceful SIGTERM — wait for in-progress scans before stop
44a79e5 feat(phase3): daily counters write-through + exit-check limit 10→50
5417a76 feat(deploy): Phase 1 — rsync+symlink deploy script + rollback + one-time migration
a1d20c9 fix(esports): fix exposure restore SQL — join prediction_log for game info
936b435 perf(mirrorbot): per-trader activity cache 90s TTL — target 2s scans vs 14s
e3ba3f0 feat(esports): restore game/tournament exposure from DB on startup
47fe1bf fix: restore group/city exposure on restart from today's paper_trades
c280f89 perf(mirrorbot): increase elite fetch concurrency 5→20 — reduce ~15s to ~3.75s
420407c perf(mirrorbot): skip signal enhancements (WS latency 700-2000ms × 25 trades)
```

---

## 14. TEST SUITE

```bash
cd /c/lockes-picks/polymarket-ai-v2
python -m pytest tests/ -q --tb=short        # full suite (~5 min, 1381 passed)
python -m pytest tests/ -q -k "esports"     # esports only (~30s, 239 passed)
python -m pytest tests/ -q -k "mirror"      # mirrorbot only
python -m pytest tests/ -q -k "weather"     # weatherbot only
```

Current: **1381 passed, 6 skipped** (was 1333 before Session 74 — MirrorBot tests added 48).

---

*Handoff written 2026-03-11. Next agent: read CLAUDE.md first, then this file, then MEMORY.md.*
