# AGENT HANDOFF — MirrorBot Session 85 (2026-03-13)

## CARBON COPY HANDOFF — COMPLETE SYSTEM STATE

**Date**: 2026-03-13
**Current VPS Release**: `20260313_161204` (deployed, active, >8h uptime)
**Latest Commit**: `b751c09`
**Test Status**: 1663 passed, 0 failures
**Branch**: `master` (PRs go to `main`)

---

## EXECUTIVE SUMMARY

MirrorBot is a **trade-copying bot** that mirrors elite Polymarket traders. It has NO ML predictions or LLMs—edge comes from **trader selection + execution speed**. The system has undergone three major sessions:
- **Session 82**: Calibration stack (FTS + conformal intervals + adaptive safety)
- **Session 83**: Database overhaul (event-sourced trade ledger, 9 migrations)
- **Session 84**: Diagnostic fixes (recon query, ML registry, partition auto-creation, orphan repair)

**Currently live and trading on VPS in paper mode.**

### Live Metrics
- **Open positions**: ~166-199 (varies with exits)
- **Position cap**: 200 (`MIRROR_MAX_CONCURRENT_POSITIONS`)
- **Capital**: $3,000 | **Kelly**: 0.30 | **Max bet**: $250 | **Max daily**: $10,000
- **Resolved trades**: ~14 (7W/7L, +$230.59 net P&L)
- **RTDS**: Connected and dispatching events
- **Paper trading**: Yes (`SIMULATION_MODE=true`)

---

## 1. ARCHITECTURE — TWO COPY PATHS

### Path 1: RTDS WebSocket (Primary)
- Endpoint: `wss://ws-live-data.polymarket.com`
- Handler: `EliteWatchlist.on_rtds_trade()` via `elite_watchlist.py:399`
- Triggers: `_execute_mirror_trade(source="rtds")`
- Target latency: <1s (currently 2-3s)
- Method: Global trade feed, O(1) address lookup against top-1000 monthly leaderboard

### Path 2: Consensus Scan Loop (Secondary)
- Frequency: Every ~45s scan cycle
- Method: API fetches from elite traders, aggregates by (market_id, token_id, side)
- Consensus gate: Requires N≥2 traders agreeing on same side (tunable per-category)
- Aggregation: Extremized geometric mean of odds (Satopää et al. 2014) when `MIRROR_USE_GEOMEAN_CONSENSUS=true`, else max-confidence
- Triggers: `scan_and_trade()` → `_collect_and_aggregate_elite_trades()` → `_execute_mirror_trade(source="consensus")`

### Core Components

| Component | File | Purpose |
|-----------|------|---------|
| **MirrorBot** | `bots/mirror_bot.py` (~1220 lines) | Main class, scan loop, exit monitoring, position tracking |
| **EliteWatchlist** | `bots/elite_watchlist.py` | RTDS feed management, watchlist sync, on_trade_event dispatch |
| **MirrorCalibrationStack** | `bots/mirror_calibration.py` (174 lines) | FTS + Le(2026) domain bias + conformal intervals |
| **MirrorAdaptiveSafety** | `bots/mirror_adaptive_safety.py` (144 lines) | Pearl-inspired dynamic position limits |
| **MirrorTradeSelector** | `bots/mirror_trade_selector.py` (255 lines, scaffold) | d3rlpy IQL offline RL (not wired) |
| **MirrorChronosFilter** | `bots/mirror_chronos_filter.py` (135 lines, scaffold) | Chronos-2 price trajectory (not wired) |
| **BotBankrollManager** | `base_engine/risk/bankroll_manager.py` | Kelly sizing per-bot (primary) |

---

## 2. EXECUTION CALL CHAIN

```
┌─ RTDS WebSocket Path (primary)
│  on_rtds_trade() [elite_watchlist.py:399]
│    → on_trade_event() [elite_watchlist.py:242]
│      → _execute_mirror_trade(source="rtds") [mirror_bot.py:1025]
│        → [if MIRROR_USE_CALIBRATION] calibrate_confidence()
│        → [if MIRROR_USE_CONFORMAL] get_conformal_interval()
│        → calculate_bot_position_size(conformal_interval=...)
│          → bankroll.get_bet_size(conformal_interval=...)
│        → set correlation_id = f"rtds:{addr[:10]}" for liquidity skip
│        → place_order() [base_bot.py:289]
│          → base_engine.place_order() → order_gateway → paper_trading_engine
│          → [if "rtds:" prefix] skip liquidity API call (save 100-300ms)
│          → write ENTRY event to trade_events
│
├─ Consensus Scan Loop (secondary)
│  scan_and_trade() [mirror_bot.py:285]
│    → fit calibration stack on first scan, re-fit daily
│    → refresh adaptive safety metrics
│    → _update_elite_traders() [with 10s timeout]
│    → _collect_and_aggregate_elite_trades() [mirror_bot.py:486]
│      → [per-trader activity cache] skip API if fresh
│      → [if MIRROR_USE_GEOMEAN_CONSENSUS] extremized geo-mean odds
│      → [else] max-confidence selection
│      → [per-category consensus threshold] filter by unique traders
│    → for each consensus trade:
│      → _can_open_position() [check caps]
│      → _execute_mirror_trade(source="consensus")
│
├─ Exit Monitoring (autonomous)
│  _check_and_execute_exits() [mirror_bot.py:753]
│    → Stop-loss (auto at 15%)
│    → Max hold time (auto at 72h)
│    → Trader exit mirroring (SELL when tracked traders close)
│    → write EXIT event to trade_events
```

---

## 3. SESSION 82 FEATURES — CALIBRATION STACK & ADAPTIVE SAFETY

### 3A. MirrorCalibrationStack (`bots/mirror_calibration.py`)
- Wraps `FocalTemperatureCalibrator` + `HorizonBiasCalibrator`
- Fits on first scan, re-fits daily (`_calibration_fit_date` tracking)
- `fit()` — FTS + Le(2026) from prediction_log/paper_trades (180-day window)
- `fit_conformal()` — split conformal residuals from resolved MirrorBot trades (requires 50+)
- `calibrate_confidence(raw, category, ttr_days)` — applies FTS then domain×horizon bias
- `get_conformal_interval(confidence, alpha=0.10)` — returns (p_low, p_high) at 90% coverage
- **Flags**: `MIRROR_USE_CALIBRATION=false`, `MIRROR_USE_CONFORMAL=false`, `MIRROR_CONFORMAL_MIN_RESOLVED=50`
- **Status**: ALL FLAGS OFF on VPS

### 3B. MirrorAdaptiveSafety (`bots/mirror_adaptive_safety.py`)
- Pearl-inspired dynamic position limits based on recent performance (last 50 resolved trades)
- `refresh(scan_count)` — queries every 20 scans
- `get_adjusted_max_positions()` — reduces on losing streak/drawdown, boosts on hot streak
- `get_adjusted_daily_cap_mult()` — returns 0.5-1.15x multiplier (wired at mirror_bot.py:1164-1167)
- **Triple-gate**: `self._adaptive_safety AND settings.MIRROR_ADAPTIVE_SAFETY AND self._adaptive_safety._fitted`
- **Flag**: `MIRROR_ADAPTIVE_SAFETY=false`

### 3C. Extremized Geometric Mean (`mirror_bot.py:636-670`)
- Satopää et al. (2014) aggregation for consensus trades
- Converts confidence → log-odds, geometric mean, extremize with d, convert back
- **Flags**: `MIRROR_USE_GEOMEAN_CONSENSUS=false`, `MIRROR_GEOMEAN_EXTREMIZE_D=2.0`

### 3D. RTDS Liquidity Skip (`order_gateway.py:515-519` + `mirror_bot.py:1177-1179`)
- RTDS trades tagged with `correlation_id="rtds:{trader_address[:10]}"`
- `order_gateway._liquidity_check()` skips API call when prefix detected
- **Flag**: `MIRROR_SKIP_LIQUIDITY_RTDS=false` (safe to enable now)

---

## 4. SESSION 83 — EVENT-SOURCED TRADE LEDGER

### 4A. Vision
Complete event-sourcing layer: never lose a trade, time-travel analysis, crash recovery via replay, ML attribution, daily snapshots with Sharpe/drawdown, automated 6h reconciliation.

### 4B. 9 SQL Migrations (043-051)

| Migration | Creates | Status |
|-----------|---------|--------|
| 043 | `trade_events` (immutable append-only, idempotency trigger) | LIVE |
| 044 | `traded_markets` enhancements (shares, status enum, question, resolution PnL) | LIVE |
| 045 | `position_snapshots` + `equity_snapshots` (daily Sharpe/drawdown) | LIVE |
| 046 | `reconciliation_breaks` (6h integrity check) | LIVE |
| 047 | BRIN indexes (0.1% size of B-tree) | LIVE |
| 048 | `trade_model_linkage` + `ml_features` (ML attribution) | WIRED |
| 049 | WAL/autovacuum tuning | LIVE |
| 050 | Monthly range partitioning (2026-01 through 2026-12) | LIVE |
| 051 | `model_registry` + `feature_sets` + `model_performance_daily` | WIRED |

### 4C. CQRS Dual Persistence

```
Trade happens → paper_trading.execute()
  ENTRY: insert_paper_trade() [mutable] + insert_trade_event("ENTRY") [immutable]
  EXIT:  insert_trade_event("EXIT") only [NO paper_trades for exits]
  RESOLUTION: insert_trade_event("RESOLUTION") [via resolution_backfill.py]
```

### 4D. Immutability Enforcement
```sql
CREATE TRIGGER trg_trade_events_immutable
    BEFORE UPDATE OR DELETE ON trade_events
    FOR EACH ROW EXECUTE FUNCTION prevent_trade_event_mutation();
-- GUC bypass for retention only:
SET LOCAL app.allow_retention_cleanup = 'true';
```

### 4E. 12 New Database Methods (`database.py`)

| Method | Purpose | Status |
|--------|---------|--------|
| `insert_trade_event()` | Append immutable event, `synchronous_commit=off` | LIVE |
| `insert_trade_model_linkage()` | Link trade → model via correlation_id | WIRED |
| `aggregate_model_performance()` | Daily model perf | **BUG: `mr.is_active`** |
| `mark_market_resolved()` | Update traded_markets on resolution | LIVE |
| `take_position_snapshot()` | Daily position capture | LIVE |
| `take_equity_snapshot()` | Daily per-bot equity w/ Sharpe | LIVE |
| `rebuild_positions_from_events()` | Crash recovery via replay | WIRED |
| `repair_orphaned_positions()` | Auto-repair positions missing paper_trades | LIVE |
| `run_reconciliation()` | 6h cross-validation | LIVE |
| `ensure_future_partitions()` | Auto-create monthly partitions 3mo ahead | LIVE |
| `register_model()` | Register trained model | **BUG: kwargs mismatch** |
| `promote_model()` | Promote model to production | WIRED |

### 4F. Write Hooks

**ENTRY** (`paper_trading.py:499-512`):
```python
_seq_num = await self.db.insert_trade_event(
    event_type="ENTRY", bot_name=bot_name, market_id=market_id,
    token_id=token_id, side=_db_side, size=size, price=price,
    confidence=confidence, correlation_id=correlation_id, order_id=trade_id,
)
if _seq_num and correlation_id:
    await self.db.insert_trade_model_linkage(
        trade_event_seq=_seq_num, correlation_id=correlation_id,
    )
```

**EXIT** (`paper_trading.py:455-470`):
```python
await self.db.insert_trade_event(
    event_type="EXIT", bot_name=bot_name, market_id=market_id,
    token_id=token_id, side="SELL", size=size, price=price,
    realized_pnl=realized_pnl, order_id=trade_id,
)
```

**RESOLUTION** (`resolution_backfill.py:376-406`): Emitted after Phase 4b P&L computation, deduped via NOT EXISTS.

### 4G. Watchdog Timers (main.py lines 299-470)

| Timer | Interval | Method |
|-------|----------|--------|
| Daily snapshots | 86400s | `take_position_snapshot()` + `take_equity_snapshot()` |
| Reconciliation | 21600s (6h) | `run_reconciliation()` |
| Model performance | 86400s | `aggregate_model_performance()` |
| Partition check | 86400s | `ensure_future_partitions()` |
| Retention cleanup | 86400s | GUC-bypassed deletion (30d decision_events, 365d trade_events) |

### 4H. Live Data

| Table | Rows | Notes |
|-------|------|-------|
| `trade_events` | ~1,212 (1127 ENTRY + 87 EXIT, 0 RESOLUTION) | Partitioned monthly |
| `traded_markets` | 843 (799 open, 44 resolved) | Enhanced |
| `position_snapshots` | 594 | Daily captures |
| `equity_snapshots` | 3 | WeatherBot $1,460, MirrorBot $1,757, EsportsBot $996 |
| `reconciliation_breaks` | 462 | Mostly RTDS orphans being auto-repaired |

---

## 5. SESSION 84 — DIAGNOSTIC FIXES

### Bugs Fixed (commits `b95acc6` through `b751c09`)

1. **Reconciliation query tightened**: Added `AND COALESCE(status, 'filled') != 'resolved'` — 2,643→449 false-positive breaks (83% reduction)
2. **Dead code removed**: `upsert_traded_market()` + `save_feature_snapshot()` — never called
3. **ML registry schema mismatches**: `register_model()`, `promote_model()`, `update_model_performance()` — all rewritten to match actual schema
4. **Partition auto-creation**: New `ensure_future_partitions()` + daily watchdog
5. **Crash recovery fallback**: trade_events replay in `base_engine.py` after `seed_positions_from_db()`
6. **Bias decomposition table name**: `esports_predictions` → `esports_prediction_log`
7. **MirrorBot orphan repair**: `repair_orphaned_positions()` auto-creates paper_trades rows for RTDS orphans

### Verified False Positives (NOT bugs)
- `%%` in SQLAlchemy `text()` → correct PostgreSQL LIKE wildcard
- `mark_market_resolved` `status` column — added by migration 044
- EXIT event `side="SELL"` — design choice, schema CHECK allows it
- `date.today()` on VPS — runs UTC timezone

---

## 6. CURRENT KNOWN BUGS

### P0: `aggregate_model_performance()` — Column Name
**File**: `database.py` ~line 4721
**Issue**: `mr.is_active = TRUE` but schema has `status`, not `is_active`
**Fix**: Change to `mr.status = 'production'`

### P0: `prediction_engine.py` — register_model() Kwargs
**File**: `prediction_engine.py` ~line 1445-1452
**Issue**: Passes `framework=...` and `is_active=True` — neither exists in fixed signature
**Fix**: Change to `status="production"`, put framework in `training_params`

### P0: Unstaged Changes Ready to Commit
**Files**: CLAUDE.md, ensemble_bot.py, tests/unit/test_session37_guardrails.py
**Change**: Ensemble politics profit-taking fix + PAPER TRADING IS PRODUCTION principle

### P1: RESOLUTION Events — 0 rows
**Status**: Not code bug — waiting for markets to resolve. Monitor after next resolution cycle.

---

## 7. OUTSTANDING ITEMS (Priority Order)

| Priority | Item | Status |
|----------|------|--------|
| P0 | Fix `mr.is_active` → `mr.status = 'production'` | 1-line fix |
| P0 | Fix `register_model()` kwargs in prediction_engine | 2-line fix |
| P0 | Commit 3 unstaged files | Ready |
| P1 | Monitor RTDS stability 24h | Ongoing |
| P1 | Fix `resolution_backfill.py` InvalidColumnReferenceError | Pre-existing |
| P2 | DB pool exhaustion (21/20 connections) | Increase DB_POOL_SIZE |
| P2 | Reconciliation break triage — 462 breaks | Auto-repairing |
| P3 | Reduce copy latency (2-16s → <1s) | Enable RTDS liquidity skip |
| P3 | RESOLUTION events verification | Monitor after resolution |
| P4 | MirrorBot P&L audit | Clean baseline needed |
| P4 | `feature_sets` table — no write path | Schema only |
| P5 | Remove diagnostic logging | — |
| P5 | WeatherBot `_log_weather_prediction()` not wired | — |
| P5 | EsportsBot LoL 0 opportunities | Team name extraction |

### Feature Flag Activation Timeline

| Milestone | Enable |
|-----------|--------|
| Now | `MIRROR_SKIP_LIQUIDITY_RTDS=true`, `MIRROR_USE_CALIBRATION=true` |
| 50+ resolved | `MIRROR_USE_CONFORMAL=true`, `MIRROR_ADAPTIVE_SAFETY=true` |
| 100+ resolved | Wire daily cap multiplier, focal temperature scaling |
| 500+ resolved | Install d3rlpy, train IQL, wire trade_selector |

---

## 8. CONFIGURATION (Live VPS Values)

```bash
# MirrorBot
MirrorBot:  capital=$3000, kelly=0.30, max_bet=$250, max_daily=$10000
MIRROR_MAX_POSITIONS=200, MIRROR_MAX_CONCURRENT_POSITIONS=200, MIRROR_MAX_PER_MARKET=400
MIRROR_MIN_CONFIDENCE=0.55, MIRROR_MIN_RELIABILITY=0.52
MIRROR_MIN_CONSENSUS=2, MIRROR_MIN_ELITE_TRADES=250
ELITE_MIN_VOLUME_USD=10000.0, ELITE_MIN_TRADES=100

# Watchlist & RTDS
WATCHLIST_ENABLED=true, WATCHLIST_SIZE=1000
RTDS_WS_URL=wss://ws-live-data.polymarket.com
RTDS_PING_INTERVAL=5, MIRROR_TRADER_CACHE_TTL=90

# Session 82 flags (ALL OFF)
MIRROR_USE_CALIBRATION=false, MIRROR_USE_CONFORMAL=false
MIRROR_USE_GEOMEAN_CONSENSUS=false, MIRROR_GEOMEAN_EXTREMIZE_D=2.0
MIRROR_ADAPTIVE_SAFETY=false, MIRROR_SKIP_LIQUIDITY_RTDS=false
MIRROR_CONFORMAL_MIN_RESOLVED=50

# Exit monitoring
MIRROR_EXIT_ENABLED=true, MIRROR_STOP_LOSS_PCT=0.15, MIRROR_MAX_HOLD_HOURS=72
MIRROR_HOT_TRADE_MAX_SECONDS=300, MIRROR_MAX_DELAY_MINUTES=30
MIRROR_SKIP_SIGNAL_ENHANCEMENTS=true

# System
SIMULATION_MODE=true, DB_POOL_SIZE=30
```

---

## 9. CRITICAL TRAPS — DO NOT BREAK

### asyncpg Compatibility
1. **NO `::type` casts**: Use `CAST(:x AS type)` NOT `:x::type`
2. **NO `INTERVAL $1`**: Use `:days_int * INTERVAL '1 day'`
3. **NO `DO $body$` blocks**: asyncpg breaks dollar-quoting
4. **NO `CONCURRENTLY`**: Can't create indexes concurrently in async transactions
5. **Timestamps**: `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`. `created_at` has NO DEFAULT.

### Database Schema
6. **`positions` uses `bot_id`/`source_bot`** — queries must use `COALESCE(source_bot, bot_id)`
7. **`paper_trades` has NO `metadata` JSONB column**
8. **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit
9. **`traded_markets` has `status` column** (migration 044): 'open' or 'resolved'
10. **Partitioned tables need partition key in unique indexes** — `trade_events` unique = `(idempotency_key, event_time)`

### MirrorBot-Specific
11. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
12. **RTDS envelope**: Must unwrap `data.get("payload", data)`
13. **RTDS dedup**: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`
14. **Entry price**: Uses CURRENT market price, NOT trader's fill price (Session 77 fix)
15. **`_market_meta_cache`**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
16. **CLOB volume=0**: Never use volume gates for MirrorBot
17. **Adaptive safety triple-gate**: checks `self._adaptive_safety AND settings.MIRROR_ADAPTIVE_SAFETY AND self._adaptive_safety._fitted`
18. **RTDS correlation_id prefix**: `"rtds:{addr[:10]}"` — order_gateway reads for liquidity skip

### Trading System
19. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
20. **`risk_manager.calculate_position_size()` DEPRECATED**
21. **`PSEUDO_LABEL_ENABLED=false`** — DO NOT enable
22. **BOT_REGISTRY=14 bots** — shared module change requires all 14 verified
23. **`websockets.exceptions` must be imported explicitly** (v15 lazy-loads)
24. **Position `current_price` auto-updated every 10s**
25. **`asyncio.create_task()` FORBIDDEN for financial writes** — always `await`

### Deploy
26. **VPS deploys via `deploy.sh`**: atomic symlink swap. Working tree ≠ VPS ≠ git HEAD
27. **Health check 90s timeout**
28. **Shared modules affect all 14 bots**

---

## 10. KEY FILES & LOCATIONS

### MirrorBot Core
- `bots/mirror_bot.py` (~1220 lines) — Main class
- `bots/elite_watchlist.py` — RTDS feed, watchlist
- `bots/mirror_calibration.py` (174 lines) — Calibration stack
- `bots/mirror_adaptive_safety.py` (144 lines) — Adaptive safety
- `bots/mirror_trade_selector.py` (255 lines, scaffold) — d3rlpy IQL
- `bots/mirror_chronos_filter.py` (135 lines, scaffold) — Chronos-2

### Database & Event-Sourcing
- `base_engine/data/database.py` (~5500 lines) — All DB ops
- `base_engine/execution/paper_trading.py` — ENTRY/EXIT event hooks
- `base_engine/data/resolution_backfill.py` — RESOLUTION events
- `schema/migrations/043-051_*.sql` — Trade ledger migrations
- `scripts/run_migrations.py` — Migration runner
- `scripts/backfill_trade_events.py` — Historical backfill

### Configuration & Core
- `config/settings.py` — Central settings
- `CLAUDE.md` — Development directive
- `main.py` — Bot orchestration, watchdog timers

---

## 11. DEPLOY & INFRASTRUCTURE

```bash
# Deploy
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh

# Rollback
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh

# SSH
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21

# Service
sudo systemctl restart polymarket-ai
journalctl -u polymarket-ai -f | grep -i mirror

# DB checks (on VPS)
sudo -u polymarket psql -d polymarket -c "SELECT event_type, COUNT(*) FROM trade_events GROUP BY event_type;"
sudo -u polymarket psql -d polymarket -c "SELECT * FROM equity_snapshots ORDER BY snapshot_date DESC;"
sudo -u polymarket psql -d polymarket -c "SELECT recon_type, severity, COUNT(*) FROM reconciliation_breaks GROUP BY recon_type, severity;"
```

**VPS**: Ubuntu-3, 34.251.224.21, 16GB/4vCPU, eu-west-1
**DB**: PostgreSQL localhost, user=polymarket, db=polymarket
**Symlink**: `/opt/polymarket-ai-v2` → `/opt/pa2-releases/<timestamp>`
**Shared**: `/opt/pa2-shared/{data,saved_models,venv}`

---

## 12. TESTS

```bash
python -m pytest --timeout=30 -x -q   # 1663 passed, 0 failures

# MirrorBot-specific
python -m pytest tests/unit/test_mirror_bot_logic.py -v
python -m pytest tests/unit/test_elite_watchlist.py -v
python -m pytest tests/unit/test_session37_guardrails.py -v
```

---

## 13. GIT STATE

```
Branch: master (PRs → main)
Latest commit: b751c09
Unstaged:
  M CLAUDE.md (PAPER TRADING IS PRODUCTION section)
  M bots/ensemble_bot.py (politics profit-taking SELL fix)
  M tests/unit/test_session37_guardrails.py (updated test mocks)
Untracked: AGENT_HANDOFF_*.md, memory/, test files, scaffold files
```

---

## 14. STATE PERSISTENCE — ALL GAPS CLOSED

| State | Mechanism | Status |
|-------|-----------|--------|
| `_daily_exposure_usd` (all bots) | `daily_counters` 60s flush + SIGTERM + startup restore | Done |
| `_game_exposure` (EsportsBot) | `daily_counters` write-through | Done |
| `_group/_city_exposure` (WeatherBot) | `_restore_exposure_from_db()` | Done |
| `_daily_exposure` (MirrorBot) | `_restore_state_on_startup()` paper_trades SUM | Done |
| Exit cooldowns (WeatherBot) | Redis TTL | Done |
| Open positions (all bots) | `seed_positions_from_db()` + trade_events fallback | Done |

---

## 15. daily_counters Write Patterns (DO NOT MIX)
- **ADDITIVE**: EsportsBot `game_{game}` keys — `counter_value += amount` via `increment_counter()`
- **ABSOLUTE-SET**: OrderGateway `daily_exposure_usd` — `counter_value = total` via `_flush_daily_exposure()`

---

## 16. BOT STATUS

| Bot | Status | Capital | P&L | Positions |
|-----|--------|---------|-----|-----------|
| **MirrorBot** | **Active (RTDS)** | $3,000 | +$230.59 | ~166-199 |
| WeatherBot | Active | $5,000 | +$461.74 | ~400 |
| EsportsBot | Active | $5,000 | — | — |
| EsportsLiveBot | Active | $1,000 | — | — |
| EsportsSeriesBot | Active | $1,000 | — | — |
| 9 others | Disabled | — | — | — |

**5 active bots**, 9 disabled, MomentumBot DELETED, EnsembleBot ARCHIVED (-$5.6k)

---

## 17. QUICK-START FOR NEXT SESSION

1. **Fix P0 bugs** (2 lines each):
   - `aggregate_model_performance()`: `mr.is_active = TRUE` → `mr.status = 'production'`
   - `prediction_engine.py`: remove `framework=` and `is_active=` kwargs

2. **Commit 3 unstaged files** (ensemble_bot, test, CLAUDE.md)

3. **Enable feature flags**:
   - Now: `MIRROR_SKIP_LIQUIDITY_RTDS=true`, `MIRROR_USE_CALIBRATION=true`
   - After 50 resolved: conformal + adaptive safety

4. **Monitor**: RTDS stability, RESOLUTION events after next market resolution

5. **Next work**: P1-P5 items per priority table above

---

**Generated**: 2026-03-13 | Session 85 Handoff
**System**: Live, stable, tests passing, 2 P0 bugs identified
**This document is a complete carbon copy of agent state for seamless continuation.**
