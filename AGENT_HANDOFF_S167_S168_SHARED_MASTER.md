# S167/S168 SHARED MASTER HANDOFF — Infrastructure Root Cause Fixes + System Elevation

**Sessions:** 167 (shared infra) + 168 (root cause fixes)
**Date:** 2026-04-08/10
**Scope:** ALL BOTS — shared infrastructure. No single-bot strategy work.
**Commits (this agent):** 18b3ac6, 608d1bf, 1bba1d3, 938b395, b731611, e1364bc, 229f790 (7 commits)
**Deploys:** 20260408_231146, 20260409_000555, 20260409_103826, 20260410_095355, 20260410_102310, 20260410_103244, 20260410_113406 (8 deploys, 1 rolled back + retried)
**Tests:** 1807 passed, 0 failed (final)
**Branch:** master
**Current live release:** `/opt/pa2-releases/20260410_113406`

---

## SESSION NARRATIVE

This session started as S167 shared infrastructure (continuing S166 backlog) and evolved into S168 root cause investigation when bots kept stalling. The session had three phases:

**Phase 1 (S167): Planned infrastructure improvements.** RESOLUTION dedup fix, EXIT over-size guard, FK validation, 2 new audit checks, illiquidity exit trigger, bulk-ack violations, resolution_backfill size zeroing, APScheduler audit removal. All planned, reviewed, tested, deployed.

**Phase 2: System elevation plan + honest assessment.** Comprehensive documentation of all 15 bots' shared decision paths, learning loops, data flows. Led to critical realization: the system has no verified edge. P&L is negative across all bots (from bot_pnl.py canonical data). The -$149K all-time figure is contaminated by bugs S163-S167 fixed (frozen prices, duplicate resolutions, stale exits). Post-fix P&L window too short to determine if edge exists. P0 action: calibration analysis on clean data.

**Phase 3 (S168): Root cause investigation and fixes.** Both bots were stalling (EsportsBot dead 20/24 hours). Deep failure analysis revealed 5 root causes. Fixed the top 2: idle-in-transaction sessions never being killed (setting configured since S152 but never applied), and permanently-unpriced tokens retrying forever (Redis-backed persistent blacklist). Result: semaphore timeouts 135/10min → 0, both bots scanning continuously.

---

## WHAT WAS DONE

### S167 Code Changes

**database.py:**
- RESOLUTION dedup: removed `side` from NOT EXISTS guard — one RESOLUTION per (bot, market)
- EXIT over-size guard: side-agnostic `COALESCE(SUM(CASE...))` rejects exits exceeding total entries
- FK validation: ENTRY/EXIT events on markets not in DB return None (with deviation documentation)
- `::text[]` → `CAST(:miss_ids AS text[])` — eliminates asyncpg parsing ambiguity

**position_manager.py:**
- Illiquidity exit trigger: ratio-based (`liquidity < N × cost_basis`), two-stage (cached pre-filter + CLOB confirmation), gated by `ILLIQUIDITY_EXIT_ENABLED=false`, conservative hold on timeout
- Unpriced position exponential backoff: after 6 cycles → 5min/10min/20min/.../60min cap
- Redis-backed persistent blacklist wiring (4 insertion points)
- `redis_client` constructor parameter (from BaseEngine)
- Liquidity cache cleared each monitoring cycle

**base_engine.py:**
- Pass `cache.redis` to AutomatedPositionManager constructor

**health_scheduler.py:**
- Removed `_run_daily_audit()` method and job registration (systemd timer replaces)

**factory.py:**
- Registered FrozenPriceCheck + PricesCoverageCheck (21→23 audit checks)

**resolution_backfill.py:**
- Phase 4b-alt: `SET status='closed'` → `SET status='closed', size=0`

**backfill_entry_metadata.py:**
- Silent `except: pass` → print warning

### S168 Code Changes

**database.py (_SemaphoreSession.__aenter__):**
- Added `SET idle_in_transaction_session_timeout` per session
- Added `await result.rollback()` in except block (prevents session poisoning)

**settings.py:**
- `DB_IDLE_IN_TXN_TIMEOUT_MS`: 120000 → 60000

**New files:**
- `base_engine/execution/unpriced_token_blacklist.py` — Redis-backed persistent blacklist
- `base_engine/audit/checks/frozen_price_check.py` — open positions with >6h stale prices
- `base_engine/audit/checks/prices_coverage_check.py` — open positions with no price data
- `scripts/bulk_ack_violations.py` — one-time bulk-ack (2 classes)
- `scripts/seed_unpriced_blacklist.py` — seed Redis with known bad tokens

### Tests Added (24 new)
- `test_trade_event_guards.py`: +5 (RESOLUTION dedup, EXIT oversize, FK validation)
- `test_illiquidity_exit.py`: +7 (pre-filter, CLOB override/timeout, no-lg)
- `test_unpriced_backoff.py`: +5 (backoff logic, reset, filter)
- Updated existing EXIT test for new FK+size guard call sequence

### VPS Operations
- `ALTER SYSTEM SET idle_in_transaction_session_timeout = '60s'` — server-side backstop
- Killed existing idle-in-transaction connections
- Bulk-acked 4,593 historical violations (16,174 → 11,581 OPEN)
- Zeroed size on 5,651 closed positions
- Seeded Redis blacklist (5 tokens, truncated IDs — self-healed with real IDs)
- 3 manual EsportsBot restarts during investigation

---

## ROOT CAUSES FOUND

### Root Cause #1: idle_in_transaction_session_timeout NEVER APPLIED (FIXED)
- Setting existed in settings.py since S152 (120s default) but was never passed to connections
- 9 connections sat idle-in-transaction permanently, holding locks
- Caused: lock waits → 30s statement timeouts → pool exhaustion → scan stalls
- Evidence: 8,107 semaphore timeouts/day, 3 lock waiters, bots dead 20/24h
- Fix: `SET idle_in_transaction_session_timeout` in `_SemaphoreSession.__aenter__` + rollback on failure
- Result: 0 semaphore timeouts, 0 lock waiters

### Root Cause #2: Permanently Unpriced Tokens Retry Forever (FIXED)
- 5+ tokens with no price data retried 4 fallback queries every 10s, every cycle
- 2,605 Fallback 2b timeouts/day = 78,150 connection-seconds wasted
- In-memory backoff (S167 P0-C) resets on restart
- Fix: Redis-backed persistent blacklist + in-memory exponential backoff
- Result: tokens auto-blacklisting after 5 failures, surviving restarts

### Root Cause #3: SET Failure Poisons Session (FIXED)
- If `SET statement_timeout` fails mid-operation, session enters invalid transaction state
- `Can't reconnect until invalid transaction is rolled back` cascades to all downstream ops
- Fix: `await result.rollback()` in except block
- Result: 0 set_statement_timeout_failed errors

---

## ERRORS MADE AND SELF-CORRECTED

1. **Combined `SET x; SET y`** — asyncpg rejects multi-statement. Deployed, caught from logs, reverted to separate SETs. Should have known asyncpg's restriction.
2. **Wrong Redis attribute path** — guessed `execution_engine.cache` and `db._redis`, both wrong. Redis lives at `base_engine.cache.redis`. Fixed by passing explicitly via constructor.
3. **Narrow time window comparison** — compared 70-min quiet period (0 errors) to post-deploy (27 errors), declared "smoking gun." Full journal range showed pre-deploy had 128 errors. Self-corrected after checking wider range.
4. **Deploy rolled back** — health check hit restart transient pool exhaustion. Redeployed successfully.
5. **Seed script used truncated token IDs** — seeded `2104889894785831` but real tokens are 78 digits. Blacklist self-healed via `record_failure()` catching real IDs.

---

## DAILY P&L (from bot_pnl.py — canonical source)

| Date | MirrorBot | EsportsBot |
|------|-----------|------------|
| Apr 2 | +$372.50 | -$1,181.94 |
| Apr 3 | -$1,701.40 | -$2,240.56 |
| Apr 4 | -$556.54 | -$507.08 |
| Apr 5 | -$68.17 | -$399.75 |
| Apr 6 | +$114.01 | $0.00 |
| Apr 7 | +$219.07 | -$701.12 |
| Apr 8 | -$2,594.98 | -$522.07 |

**S163 deployed April 8 ~16:01 UTC.** Post-fix window too short to determine edge. Pre-fix daily rate: combined -$1,395/day. The -$149K all-time is contaminated by bugs these sessions fixed.

---

## CRITICAL OPEN QUESTIONS (next session MUST address)

### P0-A: Does any bot have edge after fees?
- Run calibration analysis on all resolved predictions (prediction_log WHERE resolution IS NOT NULL)
- Compute per-bot Brier score, calibration curve, average edge at entry, net edge after 1.5% taker fee
- Per-category breakdown: which categories/horizons have positive net edge?
- **Decision gate:** If no category/bot has net edge > 0 after fees across 100+ resolved trades, pause all bots.

### P0-B: Freeze learning loops until data integrity clean
- Set `RETRAIN_INTERVAL_HOURS=999999` and `AUTO_RETRAIN_ON_DEGRADATION=false`
- S163-S167 found: duplicate resolutions, wrong EXIT side, orphan trade_events, disposal-exceeds-entry, 678 FK-orphaned events
- Learning engine trains on this corrupted data every 6 hours
- Only re-enable after 7 consecutive days with 0 new CRITICAL audit violations

### P1: Unreviewed code in b9e2ae7
- Commit contains mirror_bot.py (+42 lines), weather_bot.py (+16), config/settings.py changes
- Nobody in this session authored them. Deployed to production without review.
- Must audit: who wrote it, verify each change is intentional

### P1: MirrorBot edge analysis
- All-time realized: -$112,643.57 (bot_pnl.py canonical)
- Is the mirror signal actually predictive? Per-trader P&L attribution needed.
- If no trader cohort has positive edge after fees, pause MirrorBot.

---

## ARCHITECTURAL KNOWLEDGE (carry forward)

### System Overview
- **15 bots** in BOT_REGISTRY. Only 3 active: MirrorBot (running), EsportsBot (running), WeatherBot (paused since S166)
- **12 inactive:** ArbitrageBot, CrossPlatformArbBot, OracleBot, SportsBot, LLMForecasterBot, SportsInjuryBot, SportsLiveBot, SportsArbBot, EsportsLiveBot, LogicalArbBot, EnsembleBot, EliteWatchlistBot
- **VPS:** Ubuntu at 34.251.224.21, 16GB/4vCPU, 3 bots × ~1GB RSS + Postgres workers

### Entry Decision Chain (12 gates, all must pass)
1. Kill Switch → 2. Multi-Layer Kill Switch → 3. Canary Stage → 4. NegRisk Defense → 5. Drawdown Controller → 6. Adverse Selection → 7. RL Timing (optional) → 8. Risk Manager (13 sub-checks) → 9. Liquidity Guardian → 10. Trade Coordinator → 11. BotBankrollManager Kelly → 12. Paper Trading Engine

### Exit Decision Chain (6 triggers, every 10s)
1. Illiquidity Exit (gated off) → 2. Model Reversal (5 sub-paths) → 3. Dynamic Stop-Loss → 4. Dynamic Take-Profit → Grace period (20min)

### Prediction Pipeline
Features (50-100) → Ensemble (8-10 models) → Weighted blend (LASSO) → Extremization (1.8x) → 4-layer calibration (isotonic, domain, focal temp, horizon bias) → Elite direction boost → Output [0.01, 0.99]

### Price Fallback Chain (position_manager)
1. market_prices_latest (O(1)) → 2. Historical LATERAL JOIN (7-day, SAVEPOINT) → 2b. Market ID cross-join (SAVEPOINT) → 3. CLOB API orderbook → 4. markets.yes_price/no_price → Seed results back to market_prices_latest

### Key Config
- `DB_IDLE_IN_TXN_TIMEOUT_MS=60000` (S168: was 120000, now applied)
- `DB_STATEMENT_TIMEOUT_MS=30000` (per-session via _SemaphoreSession)
- `ILLIQUIDITY_EXIT_ENABLED=false` (gated, not yet enabled)
- `SIMULATION_MODE=true` (paper trading)
- `TAKER_FEE_BPS=150` (1.5%)

### VPS Schema (canonical, differs from local)
See S166 handoff for full column mapping. Key differences:
- `positions.bot_name` → `source_bot` on VPS
- `trade_events.fee` → `fees`
- `bot_health_states.status` → `state`
- `traded_markets.bot_names` is TEXT not TEXT[]
- `Database.initialize()` → `Database.init()` on VPS

### SSH Access
```
SSH_KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem
VPS=ubuntu@34.251.224.21
CURRENT=/opt/polymarket-ai-v2  # symlink to latest release
SHARED=/opt/pa2-shared
VENV=/opt/pa2-shared/venv/bin/activate
```

---

## FEEDBACK RULES (MANDATORY — learned through corrections)

1. **RULE ZERO:** NEVER present financial figures without citing bot_pnl.py or labeling UNVERIFIED. No napkin math. No disclaimers as workaround. Stop hook enforces this.
2. **Paper trading IS production.** Every feature matters identically in paper and live modes.
3. **No asyncio.wait_for on DB calls.** Corrupts asyncpg connections. Use SET statement_timeout.
4. **Pre/post deploy split first.** Always split log data by deploy timestamp before analyzing.
5. **EsportsBot stays in PM_EXCLUDE.** Removal causes semaphore exhaustion.
6. **Bot-scoped sessions.** No bleedover between bots unless explicit demand.
7. **Read the entire file before modifying.** Complete CLAUDE.md checklist before every edit.
8. **One fix per commit.** No "while I'm in here" refactors.
9. **Equal time windows for pre/post comparison.** Narrow windows produce false conclusions.
10. **Don't conclude "no edge" from contaminated data.** The P&L includes bugs the fixes addressed.

---

## WHAT'S MISSING (flagged by user, not yet done)

1. **Backtest validation** — `base_engine/backtesting/backtest_engine.py` exists. Nobody has run it.
2. **Position-level attribution** — which categories/horizons/confidence buckets win vs lose?
3. **Cost analysis** — average edge at entry vs 1.5% taker fee. Is net edge positive?
4. **Risk control integration** — CVaR $10K cap never verified against real positions.
5. **Correlation_id propagation** — audit trail has gaps. Some paths lose correlation_id.
6. **Entry-time liquidity check** — Gate 9 should reject markets where exit liquidity < 5× position size. Currently only checks slippage %.
7. **Bot registry cleanup** — 12 inactive bots create false impression of diversification.

---

## FILES MODIFIED THIS SESSION

| File | Change |
|------|--------|
| `base_engine/data/database.py` | RESOLUTION dedup, EXIT guard, FK validation, idle-in-txn SET, rollback, `::text[]` fix |
| `base_engine/execution/position_manager.py` | Illiquidity exit, unpriced backoff, blacklist wiring, Redis param, cache clear |
| `base_engine/base_engine.py` | Pass Redis to position_manager |
| `config/settings.py` | DB_IDLE_IN_TXN_TIMEOUT_MS 120000→60000 |
| `base_engine/monitoring/health_scheduler.py` | Removed APScheduler daily audit |
| `base_engine/audit/factory.py` | +2 checks (23 total) |
| `base_engine/data/resolution_backfill.py` | size=0 on close |
| `scripts/backfill_entry_metadata.py` | except pass → warning |
| `base_engine/execution/unpriced_token_blacklist.py` | NEW: Redis blacklist |
| `base_engine/audit/checks/frozen_price_check.py` | NEW: stale price check |
| `base_engine/audit/checks/prices_coverage_check.py` | NEW: no-price check |
| `scripts/bulk_ack_violations.py` | NEW: bulk-ack |
| `scripts/seed_unpriced_blacklist.py` | NEW: seed Redis |
| `tests/unit/test_trade_event_guards.py` | +12 tests |
| `tests/unit/test_illiquidity_exit.py` | NEW: 7 tests |
| `tests/unit/test_unpriced_backoff.py` | NEW: 5 tests |

---

## ROLLBACK

```bash
# Revert all S167/S168 commits
git revert 229f790 e1364bc b731611 938b395 1bba1d3 608d1bf 18b3ac6

# Remove server-side idle-in-txn (optional — it's a safety net)
sudo -u postgres psql -d polymarket -c "ALTER SYSTEM RESET idle_in_transaction_session_timeout; SELECT pg_reload_conf();"

# Clear Redis blacklist
redis-cli -a $REDIS_PASSWORD KEYS "unpriced_blacklist:*" | xargs redis-cli -a $REDIS_PASSWORD DEL
redis-cli -a $REDIS_PASSWORD KEYS "unpriced_failures:*" | xargs redis-cli -a $REDIS_PASSWORD DEL
```

---

## HOW TO CONTINUE

The next agent reads MEMORY.md first, sees this handoff. The immediate priorities are:

1. **P0-A: Calibration analysis** — determine if any bot has positive expected value after fees on clean (post-S163) data. This gates everything else.
2. **P0-B: Freeze learning loops** — two env var changes, zero code risk.
3. **Verify blacklist is reducing fallback errors** — check after 24h: `journalctl | grep 'permanently_blacklisted'` count should match unpriced token count, and `price_fallback` errors should approach zero.
4. **Verify audit timer** — `polymarket-audit.timer` should have fired at 03:04 UTC. Check `audit_runs` table.

All context, feedback rules, architectural knowledge, and open questions carry forward through MEMORY.md and this handoff document.
