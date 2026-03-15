# Session 85 — P&L Data Overhaul Handoff

**Date**: 2026-03-14
**Commit**: `868a7e3` on `master`
**Scope**: ALL BOTS — data layer overhaul, 10 files changed, 178 additions / 528 deletions
**Tests**: 1653 passed, 0 new failures (2 pre-existing EsportsBot MagicMock failures)
**Status**: Committed locally. NOT YET DEPLOYED to VPS. Migration 052 must run before restart.

---

## What Changed and Why

The P&L infrastructure built in Sessions 83-84 (migrations 043-049) had **10 verified bugs** confirmed by 5 independent blind audits. Every P&L number the system produced was wrong. Session 85 fixes all of them.

**Core change**: `trade_events` is now the **sole P&L authority**. `paper_trades` is demoted to a legacy compatibility layer (still written to by 28 callers, but never read for P&L).

---

## Files Modified (10 files)

### 1. `schema/migrations/052_purge_dead_tables.sql` (NEW)
- Drops 5 dead tables: `position_snapshots`, `trade_model_linkage`, `model_registry`, `model_performance_daily`, `feature_sets`
- Adds missing `correlation_id` column + index to `paper_trades`
- **VPS**: Must run BEFORE restarting the service

### 2. `base_engine/data/database.py` (443 lines changed)
**Removed ORM classes**: `PositionSnapshot`, `TradeModelLinkage`
**Removed 7 methods**:
- `insert_trade_model_linkage()` — wrote to dropped table
- `aggregate_model_performance()` — wrote to dropped table
- `take_position_snapshot()` — wrote to dropped table, had inverted NO formula
- `register_model()` — wrote to dropped table
- `promote_model()` — wrote to dropped table
- `update_model_performance()` — wrote to dropped table
- `register_feature_set()` — wrote to dropped table

**Fixed `take_equity_snapshot()` — 3 bugs**:
- **Bug A**: Inverted NO unrealized P&L formula. OLD: `size * (entry_price - current_price)` for NO. NEW: uniform `size * (current_price - entry_price)` for ALL sides. Prices are token-specific — NEVER invert for NO.
- **Bug B**: Hardcoded `total_capital = 1000.0`. NEW: per-bot from config — WeatherBot=$5000, MirrorBot=$3000, EsportsBot/Live/Series=$5000.
- **Bug C**: Realized P&L read from `paper_trades` (missing all EXIT P&L). NEW: reads from `trade_events` (includes EXIT + RESOLUTION).
- Daily trades/win/loss counts also moved from paper_trades to trade_events.

**Fixed `run_reconciliation()`**:
- Check 1 now compares `positions` vs `trade_events` net size (ENTRY - EXIT), not paper_trades.

**Fixed RESOLUTION event emission** (`backfill_paper_trades_resolution`):
- Window widened from 1 hour to 24 hours
- Silent `except: pass` → `logger.warning()` (failures now visible in logs)

**Fixed `ensure_future_partitions()`**:
- Removed `position_snapshots` partition creation (table dropped)
- Now only creates `trade_events` partitions

### 3. `base_engine/execution/paper_trading.py`
- Removed `trade_model_linkage` hook (lines 513-518) — wrote to dropped table
- ENTRY event emission: `except: pass` → `except Exception as e: logger.warning("trade_event_entry_emit_failed", ...)`
- EXIT event emission: `except: pass` → `except Exception as e: logger.warning("trade_event_exit_emit_failed", ...)`

### 4. `base_engine/prediction/prediction_engine.py`
- Removed `register_model()` call block (lines 1440-1454) — wrote to dropped table

### 5. `config/settings.py`
- Added `WEATHER_TOTAL_CAPITAL` (default 5000) after WEATHER_MAX_TOTAL_EXPOSURE_USD
- Added `MIRROR_TOTAL_CAPITAL` (default 3000) after MIRROR_MAX_POSITIONS

### 6. `main.py`
- Removed `_last_model_perf` timer variable
- Position snapshot timer → equity-only snapshot timer (guard: `hasattr(db, 'take_equity_snapshot')`)
- Removed entire model performance aggregation timer block
- Updated partition timer comment to note position_snapshots removal

### 7. `bots/mirror_bot.py` — `_restore_state_on_startup()`
**OLD**: `SELECT SUM(size * price) FROM paper_trades WHERE side IN ('YES','NO') AND created_at >= CURRENT_DATE`
**NEW**: `SELECT SUM(CASE WHEN event_type='ENTRY' THEN size*price ELSE 0 END) - SUM(CASE WHEN event_type='EXIT' THEN size*price ELSE 0 END) FROM trade_events WHERE bot_name = :bot AND event_time >= CURRENT_DATE`
**Why**: paper_trades has no SELL/EXIT rows. MirrorBot was over-counting daily exposure on restart → blocking new trades.

### 8. `bots/weather_bot.py` — `_restore_daily_pnl_from_db()`
**OLD**: `SELECT SUM(realized_pnl) FROM paper_trades WHERE bot_name='WeatherBot' AND side IN ('YES','NO') AND realized_pnl IS NOT NULL AND created_at >= :today_start`
**NEW**: `SELECT SUM(CAST(realized_pnl AS DOUBLE PRECISION)) FROM trade_events WHERE bot_name='WeatherBot' AND event_type IN ('EXIT','RESOLUTION') AND realized_pnl IS NOT NULL AND event_time >= :today_start`
**Why**: paper_trades realized_pnl only has resolution P&L. Stop-loss EXIT P&L was invisible. `_check_daily_loss_limit` was making wrong decisions.

### 9. `bots/esports_bot.py` — `_restore_daily_pnl_from_db()`
**OLD**: Same as WeatherBot — read paper_trades.
**NEW**: Same pattern — read trade_events for EsportsBot/EsportsLiveBot/EsportsSeriesBot.
**Why**: Same bug. EXIT P&L invisible. `_check_daily_loss_limit()` was wrong.

### 10. `scripts/audit_pnl.py` (complete rewrite)
- Section 1: trade_events P&L by bot + event_type (AUTHORITY)
- Section 2: paper_trades P&L (comparison only — resolution only)
- Section 3: Open positions unrealized P&L (uniform formula)
- Section 4: ENTRY count cross-validation (trade_events vs paper_trades)
- Section 5: Split state detection (resolution set, realized_pnl NULL) with --fix
- Section 6: resolved_at NULL check
- Removed old broken Section 5 (paper_trades.realized_pnl vs positions.unrealized_pnl)

---

## What Was NOT Changed

- **paper_trades writes** — 28 callers still write to it. No changes to write path.
- **paper_trading SELL exclusion** — `paper_trading.py:446` still skips DB write for SELL. This is by design (UNIQUE constraint). EXIT events go to trade_events instead.
- **Bot trading logic** — No changes to scan loops, entry logic, risk checks, or position management.
- **position_manager 10s price updates** — Unchanged.
- **trade_events table schema** — No DDL changes. Migrations 043-050 stay as-is.
- **`rebuild_positions_from_events()`** — Still exists as fallback. Has a known P2 bug (EXIT zeros both sides), but only triggers if positions table is empty on startup.
- **`bot_pnl.py`** — Unchanged. Was already correct. All fixed methods now match its formulas.

---

## Table Inventory: 11 → 7

| Table | Status | Notes |
|-------|--------|-------|
| `paper_trades` | KEEP | Legacy writes only, demoted from P&L authority |
| `trade_events` | KEEP | **P&L AUTHORITY** — ENTRY, EXIT, RESOLUTION events |
| `positions` | KEEP | Open position tracking, 10s price updates |
| `daily_counters` | KEEP | Daily exposure write-through |
| `traded_markets` | KEEP | Resolution backfill |
| `equity_snapshots` | KEEP | Fixed (3 bugs), now correct |
| `reconciliation_breaks` | KEEP | Fixed source comparison |
| ~~`position_snapshots`~~ | **PURGED** | 594 rows, 0 readers |
| ~~`trade_model_linkage`~~ | **PURGED** | 0 rows, 0 readers |
| ~~`model_registry`~~ | **PURGED** | 0 rows, 0 readers |
| ~~`model_performance_daily`~~ | **PURGED** | 0 rows, 0 readers |
| ~~`feature_sets`~~ | **PURGED** | 0 rows, 0 readers |

---

## P&L Formula Reference (MANDATORY)

**UNIFORM for ALL sides (YES and NO)**:
```
cost_basis    = entry_price * size
unrealized    = (current_price - entry_price) * size
realized_exit = (exit_price - entry_price) * size - fees
realized_res  = (payout - entry_price) * size - fees   # payout = 1.0 or 0.0
```

**NEVER invert for NO positions.** Prices are token-specific. A NO token at $0.60 entry that drops to $0.55 is a LOSS of $0.05 * size.

**Canonical script**: `python scripts/bot_pnl.py BotName hours`
**Data sources**: `trade_events` (realized), `positions` (unrealized mark-to-market)

---

## Deploy Checklist

### Pre-deploy
```bash
# 1. Push to remote
git push origin master

# 2. Deploy
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
```

### Post-deploy (on VPS)
```bash
# 3. Run migration 052 BEFORE restarting
sudo -u polymarket psql polymarket -f /opt/polymarket-ai-v2/schema/migrations/052_purge_dead_tables.sql

# 4. Restart service
sudo systemctl restart polymarket-ai

# 5. Verify startup restore (watch for correct log lines)
journalctl -u polymarket-ai -f | grep -E "seeded _daily_exposure|restored daily_pnl|trade_events"

# 6. Verify trade_events has data
sudo -u polymarket psql polymarket -c "SELECT event_type, COUNT(*), ROUND(COALESCE(SUM(CAST(realized_pnl AS float)),0)::numeric,2) as pnl FROM trade_events GROUP BY event_type ORDER BY event_type;"

# 7. Verify equity snapshot uses correct capital
sudo -u polymarket psql polymarket -c "SELECT bot_name, total_capital, realized_pnl, unrealized_pnl, total_equity FROM equity_snapshots ORDER BY snapshot_date DESC LIMIT 10;"

# 8. Run P&L audit
cd /opt/polymarket-ai-v2 && python scripts/audit_pnl.py

# 9. Cross-check with bot_pnl.py
python scripts/bot_pnl.py WeatherBot 720
python scripts/bot_pnl.py MirrorBot 720
python scripts/bot_pnl.py EsportsBot 720

# 10. Verify purged tables are gone
sudo -u polymarket psql polymarket -c "\dt position_snapshots"
sudo -u polymarket psql polymarket -c "\dt trade_model_linkage"
```

---

## Known Limitations

1. **Historical gap**: `trade_events` only has data from Session 83+ (migration 043, ~2026-03-13). Pre-Session-83 trades exist only in `paper_trades`. All-time P&L from `trade_events` will undercount until enough time passes.

2. **`rebuild_positions_from_events()` P2 bug**: EXIT events zero BOTH YES and NO sides for a market. Should only decrement the exited side. Only affects the fallback path (positions table empty on startup). Normal operation uses `positions` table directly.

3. **Pre-existing test failures**: 2 EsportsBot tests fail due to MagicMock incompatibility in `risk_manager.py:321`. Pre-existing before Session 85. Not caused by these changes.

---

## Bot-Specific Impact

### MirrorBot
- `_restore_state_on_startup()` now reads trade_events for daily exposure (ENTRY - EXIT net)
- After restart, `_daily_exposure` will be correct (previously over-counted, blocking trades)
- No changes to RTDS, mirror logic, or position management

### WeatherBot
- `_restore_daily_pnl_from_db()` now reads trade_events for realized P&L
- `_check_daily_loss_limit` will now include stop-loss EXIT P&L (previously invisible)
- New config: `WEATHER_TOTAL_CAPITAL=5000` (for equity snapshots)
- No changes to scan loop, weather predictions, or calibration

### EsportsBot / EsportsLiveBot / EsportsSeriesBot
- `_restore_daily_pnl_from_db()` now reads trade_events for realized P&L
- `_check_daily_loss_limit()` will now include EXIT P&L
- No changes to PandaScore, game exposure, or match tracking

### All Other Bots (9 disabled)
- No bot-specific code changed
- Shared module changes (database.py, paper_trading.py, main.py) apply but are backwards-compatible
- `hasattr()` guards in main.py prevent crashes if methods don't exist

---

## Summary of 10 Bugs Fixed

| # | Bug | Location | Fix |
|---|-----|----------|-----|
| F1 | SELL trades invisible in paper_trades | paper_trading.py:446 | Accepted — trade_events captures exits |
| F2 | 3 bots restore wrong daily P&L | mirror/weather/esports | Read trade_events instead |
| F3 | Equity snapshot NO formula inverted | database.py:4830 | Uniform (current-entry)*size |
| F4 | Equity snapshot hardcodes $1000 | database.py:4876 | Per-bot from config |
| F5 | Equity snapshot reads paper_trades | database.py:4849 | Reads trade_events |
| F6 | correlation_id missing from schema | paper_trades | Migration 052 adds it |
| F7 | 5 dead tables (0 readers) | database.py | Purged via migration 052 |
| F8 | 0 RESOLUTION events (silent except) | database.py:3252 | logger.warning + 24h window |
| F9 | audit_pnl.py cross-validates broken sources | audit_pnl.py | Rewritten with trade_events authority |
| F10 | bot_pnl.py (only correct script) unused | — | All methods now match its formulas |
