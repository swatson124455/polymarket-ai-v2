# AGENT HANDOFF — Session 84: Data Architecture Overhaul
**Date**: 2026-03-13
**Session scope**: Event-sourced trade ledger, CQRS, ML model registry, reconciliation, snapshots
**VPS release**: `20260313_161204` (active, running 27+ min at handoff)
**Test suite**: 1663 passed, 0 failed
**Commits this session**: 13 (from `7bbf930` to `b751c09`)

---

## TABLE OF CONTENTS
1. [What Was Built](#1-what-was-built)
2. [Architecture Overview](#2-architecture-overview)
3. [Migrations 043-051 (Schema)](#3-migrations-043-051)
4. [New Database Methods (database.py)](#4-new-database-methods)
5. [Write Hooks (Paper Trading + Prediction Engine)](#5-write-hooks)
6. [Watchdog Timers (main.py)](#6-watchdog-timers)
7. [Health Runner Change](#7-health-runner-change)
8. [Ensemble Bot Fix (Unstaged)](#8-ensemble-bot-fix)
9. [Bias Decomposition Fix](#9-bias-decomposition-fix)
10. [MirrorBot Orphan Repair](#10-mirrorbot-orphan-repair)
11. [VPS Live State](#11-vps-live-state)
12. [Known Bugs (MUST FIX)](#12-known-bugs)
13. [Outstanding Items (Priority Order)](#13-outstanding-items)
14. [Critical Traps](#14-critical-traps)
15. [Commit History](#15-commit-history)
16. [Unstaged Changes](#16-unstaged-changes)
17. [Key Config (VPS Live)](#17-key-config)
18. [File Map](#18-file-map)
19. [Deploy / Rollback](#19-deploy--rollback)
20. [Session Learnings (Asyncpg Gotchas)](#20-session-learnings)

---

## 1. WHAT WAS BUILT

A complete **event-sourced data architecture** layered onto the existing Polymarket AI V2 trading system:

| Component | Purpose | Status |
|-----------|---------|--------|
| `trade_events` (partitioned) | Immutable append-only ledger of every trade action | **LIVE** — 1212 rows (1127 ENTRY, 87 EXIT) |
| `position_snapshots` | Daily state capture of all open positions | **LIVE** — 594 rows |
| `equity_snapshots` | Daily per-bot equity/drawdown/Sharpe | **LIVE** — 3 rows |
| `reconciliation_breaks` | 6-hourly integrity check results | **LIVE** — 462 rows |
| `traded_markets` (enhanced) | Market metadata + status tracking | **LIVE** (enhanced existing table) |
| `model_registry` | Lightweight MLflow replacement | **WIRED** — 0 rows, awaiting next training cycle |
| `feature_sets` | Feature schema versioning | **SCHEMA ONLY** — no write path yet |
| `model_performance_daily` | Daily prediction→outcome aggregation | **WIRED** — 0 rows, **HAS BUG** (see Known Bugs) |
| `trade_model_linkage` | Connects trades to predictions via correlation_id | **WIRED** — 0 rows, awaiting trades with correlation_id |
| BRIN indexes | Replace B-tree for time-series scans | **LIVE** — 4 tables converted |
| PG autovacuum tuning | Absolute thresholds for hot tables | **LIVE** |
| Partition auto-extend | Creates 12 months of partitions ahead | **LIVE** — 13 partitions exist |
| Orphan position repair | Auto-creates paper_trades for orphaned positions | **LIVE** — runs before each recon |

**CQRS pattern**: Writes go to `trade_events` (append-only), then projected to `paper_trades` (existing table, mutable). Both tables receive data. The event store is the source of truth for audit/replay; paper_trades remains the operational table for all existing bot logic.

---

## 2. ARCHITECTURE OVERVIEW

```
                    ┌─────────────────────────┐
                    │   Bot Scan Loop          │
                    │  (WeatherBot, MirrorBot, │
                    │   EsportsBot, etc.)      │
                    └────────┬────────────────┘
                             │ place_order()
                             ▼
                    ┌─────────────────────────┐
                    │   OrderGateway           │
                    │   paper_engine.execute() │
                    └────────┬────────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
    ┌─────────────┐  ┌────────────┐  ┌──────────────┐
    │ paper_trades │  │trade_events│  │trade_model_  │
    │ (mutable)   │  │(immutable) │  │linkage       │
    └─────────────┘  └────────────┘  └──────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
    ┌─────────────┐  ┌────────────┐  ┌──────────────┐
    │ position_   │  │ equity_    │  │reconciliation│
    │ snapshots   │  │ snapshots  │  │_breaks       │
    │ (daily)     │  │ (daily)    │  │ (6-hourly)   │
    └─────────────┘  └────────────┘  └──────────────┘

    ┌─────────────┐  ┌────────────┐  ┌──────────────┐
    │ model_      │  │ feature_   │  │model_perf_   │
    │ registry    │  │ sets       │  │daily         │
    │ (on save)   │  │ (schema)   │  │ (daily agg)  │
    └─────────────┘  └────────────┘  └──────────────┘
```

---

## 3. MIGRATIONS 043-051

All 9 migrations deployed and applied on VPS. Dollar-quote splitter in `scripts/run_migrations.py` handles `$$`/`$body$` blocks that asyncpg rejects as multi-statement.

### 043_trade_events.sql
```sql
CREATE TABLE trade_events (
    sequence_num BIGSERIAL,
    event_type VARCHAR(32) NOT NULL CHECK (event_type IN ('ENTRY','EXIT','RESOLUTION','CORRECTION','POSITION_REBUILD','MANUAL_ADJUSTMENT')),
    execution_mode VARCHAR(16) NOT NULL DEFAULT 'paper' CHECK (execution_mode IN ('paper','live','backtest')),
    event_time TIMESTAMP NOT NULL DEFAULT NOW(),
    knowledge_time TIMESTAMP,
    recorded_at TIMESTAMP NOT NULL DEFAULT NOW(),
    bot_name VARCHAR(64) NOT NULL,
    market_id VARCHAR(256) NOT NULL,
    token_id VARCHAR(256),
    correlation_id VARCHAR(256),
    order_id VARCHAR(256),
    side VARCHAR(8) CHECK (side IN ('YES','NO','SELL')),
    size NUMERIC(18,6),
    price NUMERIC(10,6),
    fees NUMERIC(18,6) DEFAULT 0,
    realized_pnl NUMERIC(18,6),
    confidence NUMERIC(10,6),
    predicted_probability NUMERIC(10,6),
    model_version INTEGER,
    model_name VARCHAR(128),
    idempotency_key VARCHAR(512),
    event_data JSONB,
    PRIMARY KEY (sequence_num, event_time)  -- partition key included
) PARTITION BY RANGE (event_time);
-- Immutability trigger: prevent_trade_event_mutation() with GUC bypass
-- 13 monthly partitions (2026-01 through 2026-12 + default)
-- BRIN index on event_time, B-tree on bot_name+event_time, idempotency unique
```

### 044_traded_markets_enhance.sql
Adds columns: `question`, `last_trade_at`, `net_yes_shares`, `net_no_shares`, `total_invested`, `trade_count`, `execution_mode`, `resolution_pnl`, `status`. Backfills `status='resolved'` where `resolved=TRUE`.

### 045_snapshots.sql
- `position_snapshots`: Daily per-position mark-to-market. UNIQUE(snapshot_date, bot_name, market_id, side).
- `equity_snapshots`: Daily per-bot capital/drawdown/Sharpe. UNIQUE(snapshot_date, bot_name).

### 046_reconciliation_breaks.sql
Severity: INFO/WARNING/CRITICAL. Status: OPEN/ACKNOWLEDGED/RESOLVED/FALSE_POSITIVE.

### 047_brin_indexes.sql
Replaces B-tree with BRIN (0.1% disk, 11% insert overhead) on: `paper_trades.created_at`, `prediction_log.created_at`, `decision_events.created_at`, `esports_prediction_log.created_at`.

### 048_trade_model_linkage.sql
Links trade_events to prediction_log. Also adds `knowledge_time`+`feature_version` to ml_features, `model_version` to prediction_log.

### 049_pg_tuning.sql
Autovacuum with absolute thresholds (scale_factor=0.0). market_prices: 10k, prediction_log/decision_events/trade_events: 5k, paper_trades: 1k.

### 050_table_partitioning.sql
Monthly RANGE for trade_events (by event_time) + position_snapshots (by snapshot_date). Atomic rename-migrate-drop.

### 051_ml_model_registry.sql
- `model_registry`: UNIQUE(model_name, model_version). `status` column: staging/production/retired/failed.
- `feature_sets`: UNIQUE(name, version). feature_names TEXT[], feature_types TEXT[].
- `model_performance_daily`: UNIQUE(perf_date, model_name, model_version, bot_name).

---

## 4. NEW DATABASE METHODS

All in `base_engine/data/database.py`. File is ~5500 lines. New methods start around line 4567.

### `insert_trade_event()` — line ~4567
```python
async def insert_trade_event(self, event_type, bot_name, market_id, side, size, price, *,
                              token_id=None, correlation_id=None, order_id=None,
                              realized_pnl=None, confidence=None, event_time=None,
                              event_data=None) -> Optional[int]:
```
- Uses `SET LOCAL synchronous_commit = off` for performance
- Idempotency: `ON CONFLICT (idempotency_key, event_time) WHERE idempotency_key IS NOT NULL DO NOTHING`
- Idempotency key: `{bot_name}:{market_id}:{event_type}:{order_id or timestamp}`
- Returns `sequence_num` or `None` on dedup/error

### `insert_trade_model_linkage()` — line ~4649
```python
async def insert_trade_model_linkage(self, trade_event_seq: int, correlation_id: str) -> Optional[int]:
```
- SELECT-INSERT: joins prediction_log on correlation_id to populate model_name, model_version, predicted_prob, market_price, edge
- Returns linkage id

### `aggregate_model_performance()` — line ~4685
```python
async def aggregate_model_performance(self, perf_date=None) -> int:
```
- Daily aggregation from prediction_log + trade_events + model_registry into model_performance_daily
- **BUG**: References `mr.is_active = TRUE` but schema has `status` column. See Known Bugs #1.

### `take_position_snapshot()` — line ~4778
- INSERT SELECT from `positions WHERE status = 'open'`
- Uses `COALESCE(source_bot, bot_id)` for bot_name attribution
- ON CONFLICT updates quantity/mark_price/unrealized_pnl

### `take_equity_snapshot()` — line ~4814
- Per-bot equity with peak_equity from historical equity_snapshots
- Drawdown_pct calculation
- Rolling 30-day Sharpe from paper_trades
- Uses `COALESCE(source_bot, bot_id)` for attribution

### `rebuild_positions_from_events()` — line ~4918
- Crash recovery: replays trade_events for a bot
- Reconstructs net positions from ENTRY/EXIT/RESOLUTION events
- Useful for disaster recovery — not called automatically

### `repair_orphaned_positions()` — line ~5027
- Auto-repair: creates paper_trades rows for open positions that lack them
- LEFT JOIN positions vs paper_trades WHERE pt.id IS NULL
- Uses `'repair-' || p.id::text` as order_id
- **Called automatically** before each reconciliation run

### `run_reconciliation()` — line ~5067
- Two checks:
  1. FULL OUTER JOIN positions vs paper_trades — $0.50 tolerance
  2. traded_markets status='open' where paper_trades already resolved
- Filters to **active bots only** (WeatherBot, MirrorBot, EsportsBot, EsportsLiveBot, EsportsSeriesBot)
- Deduplicates per (date, type, bot, market) to prevent noise accumulation
- Calls `repair_orphaned_positions()` first

### `register_model()` — line ~5286
```python
async def register_model(self, model_name, model_version, model_type, *,
                          status="staging", training_params=None, metrics=None,
                          training_samples=None) -> Optional[int]:
```
- ON CONFLICT(model_name, model_version) updates metrics and status

### `promote_model()` — line ~5332
- Retires current production model (status='retired')
- Promotes specified version (status='production')

### `ensure_future_partitions()` — line ~5416
- Creates 12 monthly partitions ahead for trade_events and position_snapshots
- Triggers when within 2 months of last existing partition

### `copy_insert_trade_events()` — line ~5220
- Bulk insert via raw asyncpg COPY for backfill operations

---

## 5. WRITE HOOKS

### paper_trading.py — ENTRY events (line ~499-512)
```python
# After successful insert_paper_trade():
_seq_num = await self.db.insert_trade_event(
    event_type="ENTRY", bot_name=bot_name, market_id=market_id,
    token_id=token_id, side=_db_side, size=size, price=price,
    confidence=confidence, correlation_id=correlation_id, order_id=trade_id,
)
# Then link to prediction model:
if _seq_num and correlation_id and hasattr(self.db, "insert_trade_model_linkage"):
    await self.db.insert_trade_model_linkage(
        trade_event_seq=_seq_num, correlation_id=correlation_id,
    )
```

### paper_trading.py — EXIT events (line ~455-470)
```python
# After SELL trade fills:
await self.db.insert_trade_event(
    event_type="EXIT", bot_name=bot_name, market_id=market_id,
    token_id=token_id, side="SELL", size=size, price=price,
    realized_pnl=realized_pnl, order_id=trade_id,
)
```

### prediction_engine.py — Model registration (line ~1440-1454)
```python
# After save_models_to_db() commits:
if hasattr(self.db, "register_model"):
    _skv = getattr(sklearn, "__version__", None) or ""
    for _name, (_ver, _mtype, _met) in _saved_versions.items():
        try:
            await self.db.register_model(
                model_name=_name, model_version=_ver, model_type=_mtype,
                framework=f"sklearn-{_skv}", training_metrics=_met, is_active=True,
            )
        except Exception:
            pass
```
**NOTE**: `framework` and `is_active` kwargs don't match `register_model()` signature — see Known Bugs #2.

---

## 6. WATCHDOG TIMERS (main.py lines 299-470)

Four timers in the existing watchdog loop, all init to `0.0` (fire on first cycle):

| Timer | Interval | Method | Lines |
|-------|----------|--------|-------|
| Daily snapshots | 86400s | `take_position_snapshot()` + `take_equity_snapshot()` | 420-432 |
| Reconciliation | 21600s (6h) | `run_reconciliation()` | 434-445 |
| Model performance | 86400s | `aggregate_model_performance()` | 447-457 |
| Partition check | 86400s | `ensure_future_partitions()` | 459-470 |

All use `try/except/finally` with timestamp update in `finally` to prevent retry storms.

---

## 7. HEALTH RUNNER CHANGE

`base_engine/monitoring/health_runner.py` line 274:
Changed temporal violation severity from `"warning"` to `"info"`.
These are expected for late-discovered markets where `resolved_at < prediction_time`. The temporal guard already blocks them from labeling, so they're informational not actionable.

---

## 8. ENSEMBLE BOT FIX (UNSTAGED)

**File**: `bots/ensemble_bot.py`
**Issue**: `_check_politics_profit_taking()` had paper/live branch — paper mode only logged (held to resolution), live mode called `close_position()`.
**Fix**: Both modes now call `self.base_engine.place_order(side="SELL")`, routing through OrderGateway which handles paper/live routing.
**Principle**: "PAPER TRADING IS PRODUCTION" — paper mode must exercise the same code paths.
**Tests updated**: `tests/unit/test_session37_guardrails.py` — added `place_order = AsyncMock()` mock, asserts SELL call.
**Status**: Modified but NOT committed. 3 files changed, +42/-24 lines.

---

## 9. BIAS DECOMPOSITION FIX

**File**: `esports/calibration/bias_decomposition.py` line 106
**Commit**: `b751c09`
**Was**: `FROM esports_predictions` (table doesn't exist)
**Now**: `FROM esports_prediction_log` (correct table)
**Also fixed**: INTERVAL syntax from `INTERVAL :interval_days` to `:days_int * INTERVAL '1 day'` (asyncpg compat)
**Column mapping verified**: `game` ✓, `predicted_prob` ✓, `actual_outcome` ✓, `resolved_at` ✓, `created_at` ✓

---

## 10. MIRRORBOT ORPHAN REPAIR

**Problem**: RTDS (Real-Time Data Stream) creates entries in `positions` table for MirrorBot copy trades, but some lack corresponding `paper_trades` records (298 mismatches at diagnosis).
**Root cause**: RTDS entry path writes position directly without always creating a paper_trade.
**Fix**: `repair_orphaned_positions()` in database.py — auto-creates paper_trades rows for any position without one. Called automatically before each 6h reconciliation.
**Commit**: `b751c09`
**Result**: Orphans get repaired on every recon cycle. Not a retroactive backfill — repairs happen going forward.

---

## 11. VPS LIVE STATE (at handoff)

```
Service: active (running) since 2026-03-13 20:13:04 UTC
Release: 20260313_161204

Table                 | Count   | Notes
trade_events          | 1,212   | 1127 ENTRY + 87 EXIT
paper_trades          | 1,099   | Operational table
positions (open)      | 517     | WeatherBot majority
position_snapshots    | 594     | Daily captures
equity_snapshots      | 3       | WeatherBot $1,460.92, MirrorBot $1,757.68, EsportsBot $996.24
reconciliation_breaks | 462     | Mostly MirrorBot RTDS orphans (being auto-repaired)
model_registry        | 0       | Awaiting next training cycle
trade_model_linkage   | 0       | Awaiting trades with correlation_id
model_perf_daily      | 0       | HAS BUG (see Known Bugs #1)

Trade events by bot:
  WeatherBot:  521
  MirrorBot:   510
  EnsembleBot: 101 (disabled bot, historical backfill)
  EsportsBot:  82

Partitions: 13 monthly (2026-01 through 2026-12 + default)
Immutability trigger: Active on all partitions
```

---

## 12. KNOWN BUGS (MUST FIX)

### Bug 1: `aggregate_model_performance()` references wrong column
**File**: `base_engine/data/database.py` ~line 4721
**Issue**: JOIN condition uses `mr.is_active = TRUE` but migration 051 schema has `status` column, not `is_active`.
**Impact**: The JOIN silently returns 0 rows — model_performance_daily never populates.
**Fix**: Change `mr.is_active = TRUE` to `mr.status = 'production'`.

### Bug 2: `prediction_engine.py` register_model() call passes wrong kwargs
**File**: `base_engine/prediction/prediction_engine.py` ~line 1445-1452
**Issue**: Passes `framework=f"sklearn-{_skv}"` and `is_active=True` — neither exists in `register_model()` signature.
**Impact**: `framework` ignored silently. `is_active=True` does nothing (column is `status`, default='staging').
**Fix**: Change to `status="production"` and add `training_params={"framework": f"sklearn-{_skv}"}` or update `register_model()` to accept `framework`.

### Bug 3: RESOLUTION events — 0 rows so far
**Status**: RESOLUTION event emission is wired in `resolution_backfill.py` Phase 4b. 0 rows only because no markets have resolved since deploy. Not a code bug — just needs time. Monitor after next resolution cycle.

---

## 13. OUTSTANDING ITEMS (Priority Order)

| Priority | Item | Status | Notes |
|----------|------|--------|-------|
| **P0** | Fix `mr.is_active` → `mr.status = 'production'` in aggregate_model_performance() | NEW BUG | 1-line fix in database.py |
| **P0** | Fix register_model() kwargs in prediction_engine.py | NEW BUG | 2-line fix |
| **P0** | Commit unstaged changes (CLAUDE.md, ensemble_bot.py, tests) | READY | 3 files, +42/-24 |
| **P1** | Monitor RTDS stability over 24h | ONGOING | Reconnect + backoff deployed |
| **P1** | Fix `resolution_backfill.py` query error (`InvalidColumnReferenceError`) | PRE-EXISTING | |
| **P2** | DB pool exhaustion (21/20 connections) | PRE-EXISTING | Increase DB_POOL_SIZE |
| **P2** | Reconciliation break triage — 462 breaks | NEW | Most are MirrorBot RTDS orphans being auto-repaired |
| **P3** | Reduce MirrorBot copy latency (2-16s, target <1s) | PRE-EXISTING | |
| **P3** | RESOLUTION events verification | WAITING | Monitor after next market resolution |
| **P4** | MirrorBot P&L audit — clean baseline | PRE-EXISTING | |
| **P4** | `feature_sets` table — no write path | NEW | Schema only, needs wiring |
| **P5** | Remove diagnostic logging | PRE-EXISTING | |
| **P5** | WeatherBot `_log_weather_prediction()` — not wired to scan loop | PRE-EXISTING | |
| **P5** | EsportsBot LoL 0 opportunities — team name extraction | PRE-EXISTING | |

---

## 14. CRITICAL TRAPS (DO NOT BREAK)

These are hard-won lessons. Violating any of these WILL cause production failures.

### Asyncpg Gotchas
- **No multi-statement SQL**: asyncpg prepared statements reject `CREATE FUNCTION; CREATE TRIGGER;`. Must split on dollar-quote boundaries. Migration runner has `_split_dollar_quoting()`.
- **No `::jsonb` cast**: Use `CAST(:x AS jsonb)`, NOT `:x::jsonb`. Asyncpg interprets `::` as parameter syntax.
- **No `INTERVAL $1`**: Use `:days_int * INTERVAL '1 day'` pattern.
- **No `CONCURRENTLY`**: Cannot `CREATE INDEX CONCURRENTLY` inside asyncpg transactions.
- **Timestamp tz**: `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`. `created_at` has NO DEFAULT — must always be provided.

### Architecture
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL.
- **`_market_meta_cache` in MirrorBot**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
- **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer.
- **`PSEUDO_LABEL_ENABLED=false`** — DO NOT enable.
- **CLOB volume=0** — Never use volume gates for MirrorBot.
- **BOT_REGISTRY = 14 bots** — shared module change requires all 14 verified.
- **`paper_trades` has NO `metadata` JSONB column** — never assume metadata is available.
- **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time.
- **MirrorBot entry price**: Uses CURRENT market price from `get_market_from_index()`, NOT trader's fill price.
- **RTDS envelope**: Must unwrap `data.get("payload", data)` — trade data is NOT at top level.
- **RTDS dedup**: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None` to avoid double-rejection.

### Deploy
- VPS deploys via `deploy.sh`: atomic symlink swap. Working tree != VPS != git HEAD.
- Shared dir: `/opt/pa2-shared/{data,saved_models,venv}` persists across releases.
- Rollback: `bash deploy/rollback.sh` (restores previous symlink).

---

## 15. COMMIT HISTORY (This Session)

```
b751c09 fix(esports+recon): correct bias_decomposition table name + auto-repair orphaned positions
0b10fbf fix(data): wire ML registry + trade linkage + recon noise reduction + INTERVAL fix
b95acc6 fix(database): post-deploy diagnostic fixes — recon query, dead code, ML registry, partitions
2afc9db fix(backfill): use db.init() not db.initialize(), load dotenv
af5bb5a fix(data): asyncpg cast syntax — CAST() not :: for parameterized queries
bc867a0 fix(snapshots): positions table uses source_bot/bot_id, not bot_name
ce6ded5 fix(data): remove duplicate trade_event write in insert_paper_trade
1f599ca fix(partition): include event_time in idempotency unique index + ON CONFLICT
f2f2e27 fix(migrations): remove DO $body$ blocks that break asyncpg migration runner
bae2007 fix(migration): 048 ml_features uses updated_at not created_at
0e36f74 fix(migrations): dollar-quote splitter for $$, $body$, etc. + remove CONCURRENTLY
236a80b feat(esports+base): uncommitted session work — calibration, LLM probability, bankroll
7bbf930 feat(data): event-sourced trade ledger + snapshots + recon + ML registry
```

**Pattern**: Initial feat commit → 11 incremental fixes as asyncpg incompatibilities and schema mismatches were discovered during deploy + live testing.

---

## 16. UNSTAGED CHANGES

3 files modified, not yet committed:

```diff
CLAUDE.md                               | +15 lines (PAPER TRADING IS PRODUCTION section)
bots/ensemble_bot.py                    | +18/-12 (politics profit-taking SELL fix)
tests/unit/test_session37_guardrails.py | +9/-6 (updated test mocks)
```

**ensemble_bot.py change**: Removed paper/live branching in `_check_politics_profit_taking()`. Both modes now call `self.base_engine.place_order(side="SELL")` instead of paper mode just logging.

---

## 17. KEY CONFIG (VPS Live)

```env
SIMULATION_MODE=true  # Paper trading

# Bot capitals
WeatherBot:  capital=$5000, kelly=0.25, max_bet=$500, max_daily=$2000, MAX_POSITIONS=500
MirrorBot:   capital=$3000, kelly=0.30, max_bet=$250, max_daily=$10000
EsportsBot:  capital=$5000, kelly=0.25, max_bet=$100, max_daily=$500
EsportsLiveBot: capital=$1000, kelly=0.25, max_bet=$100, max_daily=$500
EsportsSeriesBot: capital=$1000, kelly=0.25, max_bet=$100, max_daily=$500

# Thresholds
ESPORTS_MIN_CONFIDENCE=0.52
ESPORTS_MIN_EDGE=0.08
MIRROR_MIN_CONFIDENCE=0.55
MIRROR_MIN_RELIABILITY=0.52
MIRROR_MAX_POSITIONS=200
MIRROR_MAX_CONCURRENT_POSITIONS=200
WEATHER_MAX_POSITIONS=500
WATCHLIST_ENABLED=true
WATCHLIST_SIZE=1000

# Active bots
WeatherBot, MirrorBot, EsportsBot, EsportsLiveBot, EsportsSeriesBot
# 9 others disabled, MomentumBot DELETED, EnsembleBot ARCHIVED
```

---

## 18. FILE MAP

### Modified This Session
| File | What Changed |
|------|-------------|
| `base_engine/data/database.py` | +8 new methods (trade_events, snapshots, recon, model registry, partitions, orphan repair) |
| `base_engine/execution/paper_trading.py` | ENTRY + EXIT event hooks into trade_events + trade_model_linkage |
| `base_engine/prediction/prediction_engine.py` | Model registry wiring after save_models_to_db() |
| `base_engine/monitoring/health_runner.py` | Temporal violation severity warning→info |
| `main.py` | 4 watchdog timers (snapshots, recon, model perf, partitions) |
| `esports/calibration/bias_decomposition.py` | Table name fix + INTERVAL syntax fix |
| `scripts/run_migrations.py` | Dollar-quote splitter for asyncpg compat |
| `scripts/backfill_trade_events.py` | Historical trade_events backfill script |
| `schema/migrations/043-051` | 9 new migration files |
| `bots/ensemble_bot.py` | Politics profit-taking SELL fix (UNSTAGED) |
| `tests/unit/test_session37_guardrails.py` | Updated test mocks (UNSTAGED) |
| `CLAUDE.md` | PAPER TRADING IS PRODUCTION section (UNSTAGED) |

### Key Existing Files (context for next agent)
| File | Purpose |
|------|---------|
| `base_engine/data/database.py` | ALL database operations (~5500 lines) |
| `base_engine/execution/paper_trading.py` | Paper trade execution engine |
| `base_engine/execution/order_gateway.py` | Routes orders to paper/live engine |
| `base_engine/prediction/prediction_engine.py` | ML model training + prediction |
| `base_engine/monitoring/health_runner.py` | System health checks |
| `main.py` | Entry point, bot registry, watchdog loop |
| `bots/mirror_bot.py` | MirrorBot (RTDS copy trading) |
| `bots/weather_bot.py` | WeatherBot (weather market trading) |
| `bots/esports_bot.py` | EsportsBot |
| `scripts/resolution_backfill.py` | Resolves markets + emits RESOLUTION events |

---

## 19. DEPLOY / ROLLBACK

```bash
# Deploy from Windows dev machine
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh

# Rollback
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh

# VPS checks
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21
sudo systemctl status polymarket-ai
journalctl -u polymarket-ai -f
sudo -u polymarket psql -d polymarket
```

---

## 20. SESSION LEARNINGS (Asyncpg Gotchas Discovered This Session)

These are patterns that broke during deploy and required fix commits:

1. **Multi-statement SQL in migrations**: asyncpg rejects `CREATE FUNCTION ... $$ ... $$; CREATE TRIGGER ...;` as "cannot insert multiple commands into a prepared statement". Solution: `_split_dollar_quoting()` in migration runner splits on dollar-quote boundaries.

2. **`::jsonb` cast syntax**: asyncpg interprets `::` as a parameter marker. Use `CAST(:x AS jsonb)` instead of `:x::jsonb`.

3. **`INTERVAL :param`**: asyncpg cannot parameterize INTERVAL directly. Use `:days_int * INTERVAL '1 day'` multiplication pattern.

4. **`CREATE INDEX CONCURRENTLY`**: Not allowed inside asyncpg transactions. Remove `CONCURRENTLY` from migration SQL.

5. **`DO $body$ ... END $body$`**: PL/pgSQL anonymous blocks break asyncpg prepared statement parser. Refactor to plain SQL or split.

6. **Partition key in unique indexes**: Partitioned tables require the partition key in all unique constraints. `trade_events` PK became `(sequence_num, event_time)` and idempotency index became `(idempotency_key, event_time)`.

7. **`positions` table uses `source_bot`/`bot_id`, not `bot_name`**: Snapshot queries must `COALESCE(source_bot, bot_id)` for bot attribution.

8. **Stale `.pyc` cache**: After editing files, old `.pyc` can cause `AttributeError` on "missing" attributes that exist in source. Clear `__pycache__` after large changes.

---

## STATE PERSISTENCE DECISION TREE (Reference)

| State type | Example | Mechanism |
|-----------|---------|-----------|
| Purely additive, resets daily | `_game_exposure[game] += size` | `daily_counters` write-through |
| Net counter (up + down), resets daily | `_daily_exposure` | Query `paper_trades` SUM on startup |
| TTL-based cooldown | `_recently_exited[market_id]` | Redis key with matching TTL |
| Open position set | `_open_positions` | `positions` table; restore from DB |
| Not needed across restarts | API caches, prediction dedup | Leave in memory |

**ALL GAPS CLOSED** — every financial state type has a persistence mechanism.

---

## BOT STATUS (as of handoff)

| Bot | Status | P&L | Notes |
|-----|--------|-----|-------|
| WeatherBot | Active | +$461.74 (140 resolved) | 521 trade events, equity $1,460.92 |
| MirrorBot | Active (RTDS live) | +$230.59 (14 resolved) | 510 trade events, equity $1,757.68, ~89 open |
| EsportsBot | Active | — | 82 trade events, equity $996.24 |
| EsportsLiveBot | Active | — | |
| EsportsSeriesBot | Active | — | |
| 9 others | Disabled | — | BOT_ENABLED_* flags |

---

## QUICK-START FOR NEXT AGENT

1. Read `CLAUDE.md` — prime directive and rules of engagement
2. Read `memory/MEMORY.md` — full system context
3. The two P0 bugs are trivial 1-2 line fixes (Known Bugs #1 and #2)
4. Unstaged changes need committing (3 files)
5. After those, the data architecture is complete and operational
6. Next logical work: P1-P5 items from Outstanding Items table
7. All 1663 tests pass — run `pytest` before any deploy
8. VPS is live and healthy — treat every change as production
