# AGENT HANDOFF — Session 84 (2026-03-13)
# Trade Ledger Database Overhaul: Full System Context

## PURPOSE
This document is a **complete carbon-copy handoff** for a new agent to continue building the Polymarket AI v2 trading system. It covers the ENTIRE tool — not just one module — including all files, logic, vision, plans, learnings, functions, traps, and live state needed to pick up seamlessly.

---

## 1. SYSTEM OVERVIEW

### What This Is
A **15-bot automated Polymarket prediction market trading system**. Real capital at risk. Paper trading phase (`SIMULATION_MODE=true`). Bots scan markets, generate predictions, size positions via Kelly criterion, and execute trades through a unified paper trading engine.

### Architecture Stack
```
Bots (15 registered, 5 active) → BaseEngine (50+ components, 11 dependency levels)
  → OrderGateway (kill switch → risk manager → cascade detection → liquidity guardian → trade coordinator → execution)
    → PaperTrading engine → Database (PostgreSQL, asyncpg via SQLAlchemy async)
```

### Active Bots (5)
| Bot | Capital | Kelly | Max Bet | Max Daily | Status |
|-----|---------|-------|---------|-----------|--------|
| WeatherBot | $5,000 | 0.25 | $500 | $2,000 | Active. P&L +$461.74 (140 resolved) |
| MirrorBot | $3,000 | 0.30 | $250 | $10,000 | Active (RTDS live). +$230.59 (14 resolved). ~199 open positions |
| EsportsBot | $5,000 | 0.25 | $100 | $500 | Active |
| EsportsLiveBot | $1,000 | 0.25 | $100 | $500 | Active |
| EsportsSeriesBot | $1,000 | 0.25 | $100 | $500 | Active |

9 bots disabled via `BOT_ENABLED_*` flags. MomentumBot DELETED. EnsembleBot ARCHIVED (-$5.6k).

### Infrastructure
- **VPS**: Ubuntu-3 at 34.251.224.21, 16GB/4vCPU, eu-west-1
- **DB**: PostgreSQL localhost, user=polymarket, db=polymarket
- **Service**: `sudo systemctl restart polymarket-ai`
- **Logs**: `journalctl -u polymarket-ai -f`
- **Deploy**: `KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh`
- **Rollback**: Same with `deploy/rollback.sh`
- **VPS path**: `/opt/polymarket-ai-v2` → symlink to latest in `/opt/pa2-releases/`
- **Shared**: `/opt/pa2-shared/{data,saved_models,venv}`

---

## 2. SESSION 83-84: TRADE LEDGER DATABASE OVERHAUL

### What Was Built
A comprehensive audit/event-sourcing layer on top of the existing paper_trades system:

1. **7 SQL migrations (043-049)** + migration 050 (table partitioning)
2. **5 ORM model classes** in database.py
3. **8 database methods** for trade events, snapshots, reconciliation
4. **Write hooks** in paper_trading.py (ENTRY/EXIT events)
5. **Resolution event emission** in resolution_backfill.py
6. **Watchdog scheduling** in main.py (daily snapshots + 6h reconciliation)
7. **Backfill script** for historical data migration
8. **Retention cleanup** with GUC-bypassed immutability trigger

### Live Table Status (VPS, 2026-03-13 14:20 UTC)
| Table | Rows | Status |
|-------|------|--------|
| `trade_events` | ~1000 (997 ENTRY + 64 EXIT, 0 RESOLUTION) | Working. Partitioned by event_time (monthly) |
| `position_snapshots` | 419 | Working. Partitioned by snapshot_date (monthly) |
| `equity_snapshots` | 3 (one per active bot) | Working |
| `reconciliation_breaks` | ~1400 (all POSITION/WARNING) | Working. Expected first-run divergence |
| `trade_model_linkage` | 0 | Schema ready, no ML attribution wired yet |
| `traded_markets` | Enhanced with status, shares, investment tracking | Working |

### Migration Summary
| Migration | Purpose |
|-----------|---------|
| 043 | `trade_events` — immutable append-only event store with bi-temporal tracking, idempotency, ML attribution |
| 044 | `traded_markets` enhancement — question, shares, invested, status, resolution_pnl |
| 045 | `position_snapshots` + `equity_snapshots` — daily state capture with peak/drawdown/Sharpe |
| 046 | `reconciliation_breaks` — automated integrity check results |
| 047 | BRIN indexes on append-only timestamp columns (0.1% size of B-tree) |
| 048 | `trade_model_linkage` — ML attribution connecting trades to predictions |
| 049 | PostgreSQL autovacuum tuning for high-write tables |
| 050 | Monthly RANGE partitioning for trade_events and position_snapshots |

---

## 3. KEY CODE LOCATIONS

### Database Methods (`base_engine/data/database.py`)
| Method | Lines | Purpose |
|--------|-------|---------|
| ORM Models (5 classes) | ~438-560 | TradeEvent, PositionSnapshot, EquitySnapshot, ReconciliationBreak, TradeModelLinkage |
| `insert_trade_event()` | ~4567-4647 | Append immutable event with idempotency. `synchronous_commit=off` |
| `upsert_traded_market()` | ~4650-4702 | ON CONFLICT upsert for traded_markets with bot_names concatenation |
| `mark_market_resolved()` | ~4704-4729 | Set traded_markets status='resolved' |
| `take_position_snapshot()` | ~4731-4769 | INSERT SELECT from positions with ON CONFLICT upsert |
| `take_equity_snapshot()` | ~4771-4912 | Per-bot equity with peak/drawdown/Sharpe via nested queries |
| `rebuild_positions_from_events()` | ~4918-4978 | Crash recovery: replay trade_events to reconstruct positions |
| `run_reconciliation()` | ~4984-5086 | FULL OUTER JOIN positions vs paper_trades + traded_markets status check |
| `copy_insert_trade_events()` | ~5090-5150 | Bulk insert via raw asyncpg COPY (10x faster) |

### Write Hooks (`base_engine/execution/paper_trading.py`)
| Hook | Lines | Trigger |
|------|-------|---------|
| EXIT event | ~454-470 | After SELL trade logging (fire-and-forget) |
| ENTRY event | ~498-514 | After successful `insert_paper_trade()` (fire-and-forget) |
| Trade-model linkage | ~507-512 | After ENTRY event, links to prediction via correlation_id |

### Resolution Events (`base_engine/data/resolution_backfill.py`)
| Code | Lines | Trigger |
|------|-------|---------|
| Phase 4b RESOLUTION emission | ~376-406 | After paper_trades resolution P&L computed. Queries unmatched resolved trades, emits events with NOT EXISTS dedup |

### Watchdog (`main.py`)
| Feature | Lines | Schedule |
|---------|-------|----------|
| State variables | ~299-301 | `_last_snapshot`, `_last_reconciliation` init to 0.0 |
| Daily snapshots | ~418-430 | Every 86400s (fires on startup since init=0.0). Calls `take_position_snapshot()` + `take_equity_snapshot()` |
| 6h reconciliation | ~432-441 | Every 21600s. `finally:` block always updates `_last_reconciliation` to prevent thrashing |
| Retention cleanup | ~408-416 | Every 86400s. decision_events 30d, trade_events 365d via GUC bypass |

### Backfill Script (`scripts/backfill_trade_events.py`)
One-time migration: reads all paper_trades, creates ENTRY events + RESOLUTION events for resolved trades. Validates PnL match. Run after migrations 043-050 applied.

### Retention Cleanup (`base_engine/monitoring/event_bus.py`)
`_retention_cleanup()` method: Sets `app.allow_retention_cleanup = 'true'` GUC to bypass immutability trigger, deletes old decision_events (30d) and trade_events (365d), then resets GUC.

### Migration Runner (`scripts/run_migrations.py`)
`_split_sql()`: Parses SQL migration files respecting PostgreSQL dollar-quoting (`$$`, `$body$`, etc.). Splits on `;` only outside dollar-quoted blocks.

---

## 4. CRITICAL TRAPS AND LEARNINGS

### asyncpg Compatibility (MUST FOLLOW)
- **NO `::type` casts**: Use `CAST(:param AS type)` instead of `:param::type`
- **NO `$` dollar-quoting on same line as `;`**: asyncpg's prepared statement splitter breaks. Put `;` on a separate line after closing `$` tag, OR use `DROP IF EXISTS + CREATE` pattern instead of `DO $$ ... $$ LANGUAGE plpgsql;`
- **JSONB**: Use `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **DATE columns**: Pass Python `date` objects directly, or use `CAST(:param AS date)`. Do NOT pass strftime strings.
- **Timestamps**: `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`. `created_at` has NO DEFAULT — must always be provided.
- **`INTERVAL $1`**: asyncpg can't parameterize INTERVAL — use `INTERVAL '1 day' * :param` instead
- **Partitioned tables**: Unique indexes MUST include partition key. `trade_events` unique index is `(idempotency_key, event_time)` not just `(idempotency_key)` because table is partitioned by `event_time`.

### Database Schema Traps
- **`positions` table uses `bot_id` NOT `bot_name`**: Always use `COALESCE(source_bot, bot_id)` when querying for per-bot attribution
- **`paper_trades` has NO `metadata` JSONB column** — never assume metadata is available
- **Resolution backfill excludes SELL trades**: `AND LOWER(pt.side) != 'sell'`. SELL P&L computed by paper engine at exit time.
- **`traded_markets` has `status` column** (added by migration 044): values are 'open' or 'resolved'
- **`trade_events` is partitioned** (migration 050): Monthly RANGE by event_time. ON CONFLICT must use `(idempotency_key, event_time) WHERE idempotency_key IS NOT NULL`

### Trading System Traps
- **YES/NO mandate**: `place_order()` requires `side="YES"` or `side="NO"`. NEVER "BUY"/"SELL".
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
- **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer
- **MirrorBot entry price**: Uses CURRENT market price from `get_market_from_index()`, NOT trader's fill price
- **RTDS envelope**: Must unwrap `data.get("payload", data)` — trade data is NOT at top level
- **RTDS dedup**: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None` to avoid double-rejection
- **`_market_meta_cache` in MirrorBot**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
- **`PSEUDO_LABEL_ENABLED=false`** — DO NOT enable. Only Location 1 (market resolution) labels are correct.
- **CLOB volume=0** — Never use volume gates for MirrorBot
- **`websockets.exceptions` must be imported explicitly** (v15 lazy-loads)
- **Position `current_price` auto-updated every 10s** by `position_manager._update_current_prices()`
- **`asyncio.create_task()` FORBIDDEN for financial writes** — always `await` persistence calls

### Deploy Traps
- **VPS deploys via `deploy.sh`**: atomic symlink swap. Working tree (local) != VPS != git HEAD
- **14 bots share base modules**: Any change to base_bot.py, database.py, etc. requires all 14 verified
- **Health check 90s timeout**: Service must show scanning bots within 90s of restart
- **SSH key**: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`

---

## 5. BUGS FIXED IN SESSION 84

### Bug 1 (HIGH — Fixed): Resolution backfill missing args
**File**: `resolution_backfill.py:396-402`
**Problem**: `insert_trade_event()` requires positional args `side`, `size`, `price`. The resolution call omitted `size` and `price` → TypeError silently caught → 0 RESOLUTION events ever emitted.
**Also**: `side=row[2]` was `pt.resolution` (YES/NO outcome), fixed to `side=row[4]` (pt.side, position direction).
**Fix**: Added `size=0.0, price=0.0` and corrected column index.

### Bug 2 (MEDIUM — Fixed): Watchdog reconciliation retry thrashing
**File**: `main.py:438-440`
**Problem**: `_last_reconciliation` only updated on success. On failure, condition re-triggered every 30s watchdog cycle.
**Fix**: Moved timestamp update to `finally:` block.

### Bug 3 (FALSE POSITIVE — Reverted): ON CONFLICT clause
**Attempted fix**: Changed `ON CONFLICT (idempotency_key, event_time) WHERE idempotency_key IS NOT NULL` to `ON CONFLICT (idempotency_key)`.
**Why it broke**: Table is partitioned (migration 050). Unique indexes on partitioned tables MUST include partition key (`event_time`). Original code was correct.
**Learning**: Always check `\d table_name` on VPS for actual schema before "fixing" SQL.

---

## 6. VERIFIED FALSE POSITIVES (NOT BUGS)

| Claim | Verdict | Why |
|-------|---------|-----|
| `upsert_traded_market` LIKE `%%` wrong | Not a bug | `%%` in SQLAlchemy `text()` → PostgreSQL LIKE `%%` = wildcard. Correct. |
| `mark_market_resolved` uses nonexistent `status` column | Not a bug | Column added by migration 044. |
| EXIT event `side="SELL"` wrong | Design choice | Schema CHECK allows 'YES', 'NO', 'SELL'. SELL is valid for EXIT events. |
| Watchdog `date.today()` not UTC | Not an issue | VPS runs UTC timezone. |
| ORM model `default=dict` anti-pattern | Cosmetic | Models not used for inserts (raw SQL). |

---

## 7. OUTSTANDING ITEMS

### From This Session
- **RESOLUTION events**: Fix deployed but awaits next market resolution cycle to verify events appear in trade_events
- **Reconciliation 1400 POSITION breaks**: Expected first-run divergence (stale positions, unreconciled historical data). Not a code bug.
- **`trade_model_linkage`**: Schema ready, wiring exists in paper_trading.py (line 507-512), but `insert_trade_model_linkage()` method may need verification

### Carried Forward (P0-P5)
- **P0**: Monitor RTDS stability over 24h — reconnect + backoff should handle disconnects
- **P1**: Fix `resolution_backfill.py` query error (`InvalidColumnReferenceError`) — pre-existing
- **P2**: DB pool exhaustion (21/20 connections) — increase `DB_POOL_SIZE` or reduce queries
- **P3**: Reduce MirrorBot copy latency (currently 2-16s, target <1s)
- **P4**: MirrorBot P&L audit — clean baseline after all fixes + new trades
- **P5**: Remove diagnostic logging (session_factory warning, RTDS raw samples)
- **WeatherBot**: `_log_weather_prediction()` NOT wired to scan loop yet
- **EsportsBot**: LoL 0 opportunities (team name extraction issue)

### Pre-existing Non-Critical Errors (in logs, not from this session)
- `bias_decomposition`: `INTERVAL $1` asyncpg syntax error
- `bulk_insert_markets`: slug conflict fallback to per-row merge (harmless)
- 432 temporal ordering violations in prediction_log (clock skew)

---

## 8. STATE PERSISTENCE — ALL GAPS CLOSED

| State | Mechanism | Status |
|-------|-----------|--------|
| `_daily_exposure_usd` (all bots) | `daily_counters` 60s flush + SIGTERM + startup restore | Done |
| `_game_exposure` (EsportsBot) | `daily_counters` write-through | Done |
| `_group/_city_exposure` (WeatherBot) | `_restore_exposure_from_db()` | Done |
| `_daily_exposure` (MirrorBot) | `_restore_state_on_startup()` paper_trades SUM | Done |
| Exit cooldowns (WeatherBot) | Redis TTL `_save/_restore_exits_from_redis()` | Done |
| Open positions (all bots) | `order_gateway.seed_positions_from_db()` | Done |

### daily_counters Write Patterns (DO NOT MIX)
- **ADDITIVE**: EsportsBot `game_{game}` keys — `counter_value += amount` via `increment_counter()`
- **ABSOLUTE-SET**: OrderGateway `daily_exposure_usd` — `counter_value = total` via `_flush_daily_exposure()`

---

## 9. KEY CONFIG (Live VPS Values)

```
SIMULATION_MODE=true (paper trading)
PHASE_MAX_BET_USD=$1000 (but BotBankrollManager max_bet_usd=$100 is real cap)

WeatherBot:  capital=$5000, kelly=0.25, max_bet=$500, max_daily=$2000, MAX_POSITIONS=500
MirrorBot:   capital=$3000, kelly=0.30, max_bet=$250, max_daily=$10000, MAX_POSITIONS=200
EsportsBot:  capital=$5000, kelly=0.25, max_bet=$100, max_daily=$500
EsportsLiveBot: capital=$1000, kelly=0.25, max_bet=$100, max_daily=$500
EsportsSeriesBot: capital=$1000, kelly=0.25, max_bet=$100, max_daily=$500

ESPORTS_MIN_CONFIDENCE=0.52, ESPORTS_MIN_EDGE=0.08
MIRROR_MIN_CONFIDENCE=0.55, MIRROR_MIN_RELIABILITY=0.52
MIRROR_MAX_POSITIONS=200, MIRROR_MAX_CONCURRENT_POSITIONS=200
WATCHLIST_ENABLED=true, WATCHLIST_SIZE=1000
WEATHER_MAX_POSITIONS=500
```

---

## 10. FILE MAP — KEY FILES

### Core Engine
| File | Purpose |
|------|---------|
| `base_engine/data/database.py` | ~5000+ lines. All DB operations, ORM models, trade ledger methods |
| `base_engine/execution/paper_trading.py` | Paper trade execution with ENTRY/EXIT event hooks |
| `base_engine/data/resolution_backfill.py` | Resolution discovery + P&L computation + RESOLUTION event emission |
| `base_engine/risk/bankroll_manager.py` | Kelly criterion position sizing (BotBankrollManager) |
| `base_engine/prediction/prediction_engine.py` | AIA ensemble predictions |
| `base_engine/monitoring/event_bus.py` | Async pub/sub + retention cleanup with GUC bypass |
| `main.py` | Bot orchestration + watchdog (snapshots, recon, retention) |

### Bot-Specific
| File | Purpose |
|------|---------|
| `bots/weather_bot.py` | WeatherBot — NWP ensemble + temperature market trading |
| `bots/mirror_bot.py` | MirrorBot — RTDS copy trading from elite Polymarket traders |
| `bots/esports_bot.py` | EsportsBot — PandaScore API pre-match esports trading |
| `bots/esports_live_bot.py` | EsportsLiveBot — live in-play esports |
| `bots/esports_series_bot.py` | EsportsSeriesBot — series-level esports |

### Schema & Scripts
| File | Purpose |
|------|---------|
| `schema/migrations/043-050_*.sql` | Trade ledger schema (events, snapshots, recon, linkage, partitioning) |
| `scripts/run_migrations.py` | Migration runner with `_split_sql()` for dollar-quoting |
| `scripts/backfill_trade_events.py` | One-time historical paper_trades → trade_events migration |

### Config
| File | Purpose |
|------|---------|
| `config/settings.py` | Central settings (env vars, defaults) |
| `.env` on VPS | Live environment variables |
| `CLAUDE.md` | Development directive — surgical fixes, zero collateral damage |

---

## 11. VISION & ROADMAP

### What's Done
- Trade ledger event sourcing (ENTRY/EXIT events flowing live)
- Position + equity snapshots (daily, on restart)
- Automated reconciliation (6h cycle)
- Retention cleanup (30d decision_events, 365d trade_events)
- Table partitioning for performance (monthly ranges)
- Backfill script for historical data

### What's Next (Trade Ledger)
1. **RESOLUTION events verification** — confirm they emit after next market resolution
2. **Trade-model linkage wiring** — `insert_trade_model_linkage()` called in paper_trading but method may need testing
3. **Reconciliation break triage** — investigate 1400 POSITION breaks, determine which are real vs stale data
4. **Equity snapshot enhancements** — per-bot capital should come from config, not hardcoded $1000
5. **Dashboard/reporting** — query equity_snapshots for P&L curves, drawdown charts

### What's Next (System-Wide)
1. **RTDS latency optimization** (P3): 2-16s copy delay → target <1s
2. **DB pool tuning** (P2): 21/20 connections seen
3. **WeatherBot EMOS integration**: Expected ~2026-03-15
4. **EsportsBot LoL fix**: Team name extraction issue → 0 LoL opportunities
5. **Diagnostic logging cleanup** (P5): Remove session_factory warning, RTDS raw samples

---

## 12. SESSION HISTORY (for context)

| Session | Date | Focus | Key Changes |
|---------|------|-------|-------------|
| 77 | 2026-03-11 | MirrorBot P1-P8 | Phantom trade dedup, stale entry pricing fix, resolution SELL overwrite fix |
| 78 | 2026-03-12 | Esports calibration | ONNX inference, config-driven EGM |
| 79 | 2026-03-12 | MirrorBot selectivity | MIN_CONFIDENCE 0.10→0.55, purged 51 stale positions |
| 80 | 2026-03-12 | Multi-bot fixes | Various |
| 81 | 2026-03-12 | RTDS + DB persistence | RTDS live, paper_trades tz-naive fix, 6 critical fixes |
| 82 | 2026-03-13 | MirrorBot calibration | Calibration stack, adaptive safety, RTDS latency |
| 83 | 2026-03-13 | Trade ledger (SQL+Python) | Migrations 043-050, DB methods, write hooks, watchdog |
| 84 | 2026-03-13 | Trade ledger diagnostic | Blind review, 2 real bugs fixed, 1 false positive reverted |

---

## 13. RULES OF ENGAGEMENT (from CLAUDE.md)

1. **Working code is sacred.** Fix only what is broken.
2. **One fix per commit.** No "while I'm in here" refactors.
3. **Preserve every function signature** unless the signature IS the bug.
4. **No silent behavior changes.** State what changed from X to Y.
5. **Never delete code you don't understand.**
6. **No new dependencies without justification.**
7. **No structural refactors during bug fixes.**
8. **Bot-scoped sessions**: Each session focuses on a single bot unless explicitly told otherwise.
9. **Shared module changes require all 14 bots verified.**
10. **Always `\d table_name` on VPS** before "fixing" SQL — the live schema may differ from migration files due to later migrations (partitioning, etc.).

---

## 14. QUICK COMMANDS

```bash
# Deploy
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh

# Rollback
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh

# SSH to VPS
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21

# Check logs
sudo journalctl -u polymarket-ai -f

# Check trade_events
sudo -u polymarket psql -d polymarket -c "SELECT event_type, COUNT(*) FROM trade_events GROUP BY event_type;"

# Check equity snapshots
sudo -u polymarket psql -d polymarket -c "SELECT * FROM equity_snapshots ORDER BY snapshot_date DESC;"

# Check reconciliation
sudo -u polymarket psql -d polymarket -c "SELECT recon_type, severity, COUNT(*) FROM reconciliation_breaks GROUP BY recon_type, severity;"

# Check active bots
sudo -u polymarket psql -d polymarket -c "SELECT bot_name, last_scan_at FROM bot_heartbeats WHERE last_scan_at > NOW() - INTERVAL '5 minutes';"

# Run tests locally
python -m pytest --timeout=300 -q
```

---

*Generated by Session 84 (2026-03-13). Trade ledger DB overhaul diagnostic + 2 bug fixes deployed.*
