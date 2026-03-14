# AGENT HANDOFF — Session 87: MirrorBot P&L Dedup + Full System State
**Date**: 2026-03-14
**Scope**: MirrorBot-focused session (data fix touches all bots)
**Deploy**: Pending (fix committed, tests running)

---

## 1. WHAT THIS SYSTEM IS

A **15-bot automated Polymarket trading system** running in paper trading mode (`SIMULATION_MODE=true`). Real infrastructure, fake money. The system:
- Runs on Ubuntu VPS (34.251.224.21, 16GB/4vCPU, eu-west-1)
- Uses PostgreSQL for all state persistence
- Deploys via `deploy.sh` atomic symlink swap to `/opt/polymarket-ai-v2`
- 5 bots active, 9 disabled, 1 deleted (MomentumBot), 1 archived (EnsembleBot)

### Active Bots
| Bot | Strategy | Capital | Status |
|-----|----------|---------|--------|
| **MirrorBot** | Copy elite Polymarket traders via RTDS feed | $3,000 | Main revenue driver |
| **WeatherBot** | Weather prediction markets | $5,000 | Steady positive |
| **EsportsBot** | Esports match betting | $5,000 | Near breakeven |
| **EsportsLiveBot** | Live esports betting | $1,000 | Active |
| **EsportsSeriesBot** | Series-level esports | $1,000 | Active |

### Architecture
```
main.py → BOT_REGISTRY → each bot inherits BaseBot → BaseEngine
  ├── BotBankrollManager (sizing)
  ├── risk_manager (limits)
  ├── order_gateway → paper_engine (paper mode) or CLOB API (live)
  ├── position_manager (10s price updates)
  ├── Database (PostgreSQL via asyncpg/SQLAlchemy)
  └── ingestion_scheduler (market data, resolution backfill)
```

### Key Data Tables
| Table | Role |
|-------|------|
| `trade_events` | **P&L AUTHORITY** — ENTRY/EXIT/RESOLUTION, immutable, partitioned by month |
| `positions` | Open position tracking, 10s price updates, unrealized PnL |
| `paper_trades` | Legacy compat layer — still written by 28 callers, NEVER read for P&L |
| `traded_markets` | Market status + resolution tracking |
| `daily_counters` | Exposure tracking (write-through for game/daily exposure) |
| `equity_snapshots` | Daily equity curves |
| `reconciliation_breaks` | Audit trail |

---

## 2. WHAT WAS DONE THIS SESSION (Session 87)

### Bug: Duplicate RESOLUTION Events (3,339 total across all bots)

**Symptom**: `bot_pnl.py` showed MirrorBot +$71k realized. Real P&L is +$14,935.

**Root Cause**: `database.py:backfill_paper_trades_resolution()` (line 3204-3234) emitted RESOLUTION events to `trade_events` with:
- `event_time = now()` (different on every call)
- `correlation_id` from paper_trades (not deterministic)
- `ON CONFLICT (idempotency_key, event_time)` — since event_time differed, conflict never fired
- Called from 3 places: `base_engine.py` scan loop, `ingestion_scheduler.py`, `resolution_backfill.py`
- Result: ~7x duplication per market (7 backfill runs within 3 hours)

**Fix**: Removed the RESOLUTION emission code from `backfill_paper_trades_resolution()`. The emission is already handled properly by `resolution_backfill.py Phase 4b` which uses `NOT EXISTS` dedup + deterministic `correlation_id=resolution:{market_id}` + `event_time=resolved_at`.

**Data Cleanup**:
- Disabled `trg_trade_events_immutable` trigger on `trade_events_2026_03`
- Deleted duplicates keeping MIN(sequence_num) per (market_id, bot_name, side)
- MirrorBot: 2621 → 381 (deleted 2240)
- WeatherBot: 880 → 159 (deleted 721)
- EsportsBot: 434 → 62 (deleted 372)
- EnsembleBot: 10 → 4 (deleted 6)
- Re-enabled trigger

**File Modified**: `base_engine/data/database.py` — removed lines 3204-3234, replaced with comment.

---

## 3. CORRECTED P&L (as of Session 87)

| Bot | Entries | Exits | Resolutions | Exit P&L | Resolution P&L | **Total Realized** | Unrealized |
|-----|---------|-------|-------------|----------|----------------|-------------------|------------|
| MirrorBot | 578 | 67 | 381 | +$5,107.52 | +$9,827.15 | **+$14,934.67** | +$967.98 |
| WeatherBot | 861 | 47 | 159 | +$11.15 | +$914.48 | **+$925.63** | — |
| EsportsBot | 75 | 16 | 62 | +$59.62 | -$78.41 | **-$18.79** | — |
| EnsembleBot | 101 | 0 | 4 | $0 | +$1.24 | **+$1.24** | — |

### MirrorBot Details
- **143 open positions** (50 YES, 93 NO)
- **Total exposure**: $13,150 (cap: $20,000)
- **Win rate**: 46.5% (177W / 204L) but winners avg +$192 vs losers avg -$118
- **Avg bet**: $121 median $28, max $996
- **Trading actively**: 113 entries today

---

## 4. PRIOR SESSION CHAIN (Sessions 77-86)

### Session 85-86 (2026-03-14) — Resolution Backfill + P&L Data Overhaul
- **3 root causes** for resolution backfill producing 0: Python 3.13 datetime scoping (2 imports), non-YES/NO esports outcomes, no LIMIT on queries
- **trade_events promoted to P&L authority** — paper_trades demoted
- 10 bugs fixed, 5 dead tables purged (migration 052)
- 544 markets resolved (from 58)
- Ingestion sync_log fix (orphaned "running" state blocked all ingestion)
- First RESOLUTION dedup (3238 events from Session 85 runs)
- Commits: `6b6c6c5` through `4c56349`

### Session 81 (2026-03-12) — RTDS Live
- RTDS global trade feed live, 1000 trader watchlist
- Fixed paper_trades DB persistence (16h outage — asyncpg tz-naive)
- Position cap 200, Up/Down mapping, transactionHash dedup

### Session 79 (2026-03-12) — MirrorBot Selectivity
- MIRROR_MIN_CONFIDENCE: 0.10 → 0.55
- MIRROR_MIN_RELIABILITY: 0.45 → 0.52
- ELITE_MIN_TRADES: 5 → 100

### Session 77 (2026-03-11) — MirrorBot P1-P8 + Critical Bugs
- Stale entry pricing fixed (uses current market price, not trader fill)
- Resolution backfill SELL overwrite fixed
- Phantom trade dedup, daily cap, exposure logging

---

## 5. CRITICAL TRAPS (DO NOT BREAK)

### Data Layer
- **trade_events is P&L AUTHORITY** — NEVER read paper_trades for P&L
- **trade_events immutability trigger**: Must `ALTER TABLE trade_events_2026_03 DISABLE TRIGGER trg_trade_events_immutable` before DELETE/UPDATE, then re-enable
- **RESOLUTION event idempotency**: Must use deterministic `correlation_id` + `event_time` (not `now()`) for ON CONFLICT to work
- **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time
- **paper_trades has NO metadata JSONB column**
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **asyncpg DATE**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
- **asyncpg timestamps**: paper_trades uses `timestamp without time zone` — pass `.replace(tzinfo=None)`

### Python/Code
- **Python 3.13 scoping**: `from X import Y` inside a function makes `Y` local for the ENTIRE function. Any use before that import → `UnboundLocalError`. NEVER shadow top-level imports.
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL.
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
- **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer.
- **MirrorBot entry price**: Uses CURRENT market price from `get_market_from_index()`, NOT trader's fill price.
- **`_market_meta_cache` in MirrorBot**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
- **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
- **websockets.exceptions** must be imported explicitly (v15 lazy-loads)
- **BOT_REGISTRY=14 bots** — shared module change requires all verified.

### Infrastructure
- **VPS deploys via `deploy.sh`**: atomic symlink swap. Working tree ≠ VPS ≠ git HEAD.
- **CLOB volume=0** — Never use volume gates for MirrorBot.
- **RTDS envelope**: Must unwrap `data.get("payload", data)`.
- **RTDS dedup**: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`.

---

## 6. KEY CONFIG (live VPS values)
```
SIMULATION_MODE=true (paper trading)
MirrorBot:   capital=$3000, kelly=0.30, max_bet=$250, max_daily=$10000
WeatherBot:  capital=$5000, kelly=0.25, max_bet=$500, max_daily=$2000, MAX_POSITIONS=500
EsportsBot:  capital=$5000, kelly=0.25, max_bet=$100, max_daily=$500
EsportsLiveBot: capital=$1000, kelly=0.25, max_bet=$100, max_daily=$500
EsportsSeriesBot: capital=$1000, kelly=0.25, max_bet=$100, max_daily=$500
MIRROR_MIN_CONFIDENCE=0.55, MIRROR_MIN_RELIABILITY=0.52
MIRROR_MAX_POSITIONS=200, MIRROR_MAX_CONCURRENT_POSITIONS=200
ESPORTS_MIN_CONFIDENCE=0.52, ESPORTS_MIN_EDGE=0.08
WATCHLIST_ENABLED=true, WATCHLIST_SIZE=1000
WEATHER_MAX_POSITIONS=500
```

---

## 7. STATE PERSISTENCE — ALL GAPS CLOSED

| State | Mechanism | Status |
|-------|-----------|--------|
| `_daily_exposure_usd` (all bots) | `daily_counters` 60s flush + SIGTERM + startup restore | ✅ |
| `_game_exposure` (EsportsBot) | `daily_counters` write-through | ✅ |
| `_group/_city_exposure` (WeatherBot) | `_restore_exposure_from_db()` | ✅ |
| `_daily_exposure` (MirrorBot) | `_restore_state_on_startup()` paper_trades SUM | ✅ |
| Exit cooldowns (WeatherBot) | Redis TTL | ✅ |
| Open positions (all bots) | `order_gateway.seed_positions_from_db()` | ✅ |

---

## 8. DEPLOY PATTERN
```bash
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
# Rollback:
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```
VPS: `/opt/polymarket-ai-v2` → symlink to latest in `/opt/pa2-releases/`. Shared: `/opt/pa2-shared/{data,saved_models,venv}`. Health check 90s.

---

## 9. OUTSTANDING ITEMS

| Priority | Item | Details |
|----------|------|---------|
| **P1** | Deploy Session 87 fix | RESOLUTION emitter removed from database.py, tests need to pass |
| **P2** | 604 markets unresolved | Genuinely still open, will resolve over time via automatic backfill |
| **P3** | Reduce RTDS copy latency | Currently 2-16s, target <1s |
| **P5** | Remove diagnostic logging | session_factory warning, RTDS raw samples |
| **P6** | Exposure cap tuning | Hit $20k cap earlier today blocking trades. Consider raising or purging resolved positions faster |

---

## 10. P&L CALCULATION RULES (MANDATORY)

- **NEVER invert formulas for NO positions** — prices are token-specific
- `cost = entry_price * size` (ALL sides)
- `uPnL = (current_price - entry_price) * size` (ALL sides)
- Canonical script: `python scripts/bot_pnl.py BotName hours`
- Data sources: `trade_events` (realized), `positions.unrealized_pnl` (mark-to-market)
- Resolution P&L: `(resolution_value - entry_price) * size - fees`
  - YES wins: resolution_value = 1.0
  - YES loses: resolution_value = 0.0
  - NO wins: resolution_value = 1.0
  - NO loses: resolution_value = 0.0

---

## 11. KEY FILE MAP

| File | Purpose |
|------|---------|
| `main.py` | Entry point, BOT_REGISTRY, bot lifecycle |
| `base_engine/base_engine.py` | BaseEngine scan loop, all bots inherit |
| `base_engine/base_bot.py` | BaseBot with place_order, common logic |
| `base_engine/data/database.py` | All DB operations (4800+ lines) |
| `base_engine/data/resolution_backfill.py` | 7-phase resolution pipeline |
| `base_engine/data/ingestion_scheduler.py` | Market data ingestion + backfill scheduling |
| `base_engine/data/polymarket_client.py` | Gamma/CLOB API client |
| `base_engine/execution/order_gateway.py` | Order routing (paper vs live) |
| `base_engine/execution/paper_engine.py` | Paper trade execution |
| `base_engine/risk/risk_manager.py` | Position limits, exposure caps |
| `base_engine/risk/bankroll_manager.py` | Kelly criterion sizing |
| `bots/mirror_bot.py` | MirrorBot — RTDS copy trading |
| `bots/weather_bot.py` | WeatherBot |
| `bots/esports_bot.py` | EsportsBot + EsportsLiveBot + EsportsSeriesBot |
| `config/settings.py` | All config via env vars |
| `deploy/deploy.sh` | Atomic deploy to VPS |
| `scripts/bot_pnl.py` | Canonical P&L reporting |
| `scripts/audit_pnl.py` | P&L audit/reconciliation |
| `memory/MEMORY.md` | Persistent memory across sessions |
| `CLAUDE.md` | Development directive (surgical fixes) |

---

## 12. daily_counters WRITE PATTERNS (DO NOT MIX)
- **ADDITIVE**: EsportsBot `game_{game}` keys — `counter_value += amount` via `increment_counter()`
- **ABSOLUTE-SET**: OrderGateway `daily_exposure_usd` — `counter_value = total` via `_flush_daily_exposure()`

---

## 13. RESOLUTION BACKFILL PIPELINE (7 phases)

1. **Phase 1**: Find missing markets from paper_trades not in traded_markets
2. **Phase 2a**: Query unresolved traded_markets (with LIMIT)
3. **Phase 2b**: Check if markets are closed via CLOB API (skip Gamma for condition_id markets)
4. **Phase 3**: Fetch resolution from markets table
5. **Phase 4a**: Update paper_trades with resolution, realized_pnl (`backfill_paper_trades_resolution()`)
6. **Phase 4b**: Emit RESOLUTION events to trade_events (with NOT EXISTS dedup)
7. **Phase 5**: Update positions with unrealized_pnl
8. **Phase 6**: Score via PerformanceTracker
9. **Phase 7**: Online learning update

---

## 14. GIT STATE

Latest commits this session chain:
```
4c56349  fix(data): RESOLUTION event dedup — deterministic correlation_id+event_time
4491c89  fix(ingestion): clear orphaned sync_log on startup
d5a1c9f  fix(resolution): remove second shadowed datetime import in Phase 7
1280b33  fix(resolution): remove shadowed datetime import causing Python 3.13 scoping error
d34abb0  fix(resolution+equity): add debug stats to backfill + fix equity total_capital upsert
c243c55  fix(resolution): skip Gamma for condition_id markets + add LIMIT to Phase 2a
f334b72  fix(resolution): handle non-YES/NO outcomes + add closed key in CLOB format
```

Session 87 (pending commit):
- `database.py`: Removed RESOLUTION emission from `backfill_paper_trades_resolution()`
- DB: 3,339 duplicate RESOLUTION events deleted

VPS running: `20260314_011254` (all Session 86 fixes)
