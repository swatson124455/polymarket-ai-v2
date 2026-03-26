# Agent Handoff — MirrorBot Session 96 FULL (2026-03-16)

## SESSION IDENTITY
- **Bot**: MirrorBot ONLY. Do not touch other bots.
- **Session number**: 96 (continuation)
- **User**: sam (Windows dev machine → VPS at 34.251.224.21)
- **Repo**: `C:\lockes-picks\polymarket-ai-v2` (local), `/opt/pa2-shared/` (VPS)
- **Latest commit**: `83c8b88` — S96 MirrorBot streamline

## USER'S CORE PHILOSOPHY (VERBATIM)
> "Speed of trade placement is paramount. Bot being error free is next."
> "Why can't we just act when they trade? I don't care about whole history. Why can't we cache who is elite and scan for moves? That seems so simple?"
> "we are tearing this thing down"

The user wants MirrorBot stripped to its absolute essentials: RTDS fires → copy trade instantly. No consensus, no API polling, no max hold timer. Stop-loss is the only autonomous exit. Everything else cached/front-loaded at startup.

## USER PERSONALITY & WORKING STYLE
- **Hates overengineering**. Will rage if you add features not asked for.
- **Hates excessive questions**. Make decisions, present results. Ask only when truly ambiguous.
- **Hates scope creep**. Fix ONLY what's requested. Never "while I'm in here" anything.
- **Loves bullet points**. Hates walls of text.
- **Loves speed**. Both in bot execution and in your responses.
- **Explicit instruction**: Read `CLAUDE.md` — it IS the law. "Working code is sacred."

## WHAT WAS DONE IN S96

### Phase 1: Overnight Operational Review (commit `2a87cdc`, DEPLOYED)
- Position cap raised 200→400 (was blocking trades)
- Investigated: 10 restarts (all deploy-triggered, not crashes), WS disconnects (server-side, not blocking), 74s scan cycles (API polling bottleneck), AS logging gap (diagnostic only)

### Phase 2: Architectural Streamline (commit `83c8b88`, NOT YET DEPLOYED)

**User's decisions (from exhaustive decision tree):**

| Decision | Choice |
|----------|--------|
| Entry path | RTDS only. One list of top 500 elites. Copy any elite trade instantly. No consensus requirement. |
| Exit path | RTDS handles trader-driven exits. Stop-loss 15% only. No max hold. No take-profit. Delete API polling entirely. |
| Position limits | Cap 1000. Max $100/trade. Per-market $500. Category $4,000. Daily $10,000. |
| Trader selection | Keep existing thresholds (MIN_CONFIDENCE=0.55, MIN_RELIABILITY=0.52) |
| Calibration | Keep as-is |
| Elite refresh | Every 6 hours (was every 40 scans ≈ 30min) |
| Infrastructure | Front-load ALL caching at startup. Scan loop should be minimal. |

**Files modified (5 files, +53/-218 lines):**

1. **`config/settings.py`** — 6 config changes:
   - `MIRROR_MAX_CONCURRENT_POSITIONS`: 400→1000
   - `MIRROR_MAX_POSITIONS`: 400→1000
   - `MIRROR_MAX_PER_MARKET`: 800→500
   - `MIRROR_MAX_HOLD_HOURS`: 72→99999 (effectively disabled)
   - `WATCHLIST_SIZE`: 1000→500
   - `_elite_refresh_every_n_scans`: 40→480 (in mirror_bot.py line 71)

2. **`bots/mirror_bot.py`** — 3 deletions:
   - `scan_and_trade()`: Deleted consensus block (`_collect_and_aggregate_elite_trades()` call + consensus trade loop). Replaced with diagnostic log showing elite count, open positions, RTDS dispatched count.
   - `_check_and_execute_exits()`: Deleted max-hold check AND API polling loop (sequential `get_user_activity()` calls). Now stop-loss only.
   - `_elite_refresh_every_n_scans`: 40→480

3. **`bots/elite_watchlist.py`** — Refresh interval daily→6h:
   - `needs_refresh()` changed from date comparison to `time.monotonic() - self._last_refresh >= 21600`

4. **`tests/unit/test_mirror_bot_logic.py`** — Test updates:
   - `TestTraderSellExitDetection`: Rewritten for stop-loss only (3 tests)
   - `TestDailyExposureDecrement`: Removed `mock_client_ctx` and `MIRROR_MAX_HOLD_HOURS` refs
   - `_make_bot` helper: Removed `MIRROR_MAX_HOLD_HOURS` setting
   - **Consensus tests left intact** — methods still exist in mirror_bot.py, just not called from scan_and_trade

5. **`tests/unit/test_elite_watchlist.py`** — Updated `test_needs_refresh_on_new_day` to use monotonic time instead of date string

**Test results**: 1629 passed, 10 failed (all pre-existing: 8 dashboard_async_worker + 2 web3_compatibility_fixes)

## WHAT IS NOT YET DONE (PENDING TASKS)

### Must Do (from user's explicit decisions):
1. **Deploy `83c8b88` to VPS**:
   ```bash
   bash deploy.sh
   # Update VPS .env:
   export MIRROR_MAX_CONCURRENT_POSITIONS=1000
   sudo systemctl restart polymarket-ai
   ```

2. **Category exposure cap: $4,000 absolute** — Currently `MIRROR_MAX_CATEGORY_EXPOSURE_PCT = 0.80` (80% of $3k capital = $2,400). User wants $4,000 absolute. Options:
   - Add new `MIRROR_MAX_CATEGORY_EXPOSURE_USD` setting (absolute)
   - Or change `_execute_mirror_trade()` to use absolute value
   - Affects `_execute_mirror_trade()` category check around line ~1100-1150

3. **Daily exposure cap: $10,000 absolute** — Currently `MIRROR_MAX_DAILY_EXPOSURE_PCT = 0.15` (15% of $10k capital = $1,500). User wants $10,000 absolute. Options:
   - Already have `bankroll.max_daily_usd` in production (set via BotBankrollManager)
   - May just need `.env` update rather than code change
   - Check what `_can_open_position()` and `_execute_mirror_trade()` actually use

### P5 Enhancements (lower priority):
- Reduce market WS `ping_interval` 30s→10s
- AS logging volume/spread data (populate `_market_index` for MirrorBot)
- Clean up 10 pre-existing test failures
- Delete dead consensus code from mirror_bot.py (methods still exist: `_collect_and_aggregate_elite_trades`, `_get_consensus_min`, `_category_consensus_min`)

## ARCHITECTURE: HOW MIRRORBOT WORKS (POST-S96)

### Entry Flow (RTDS real-time, ~100ms total):
1. **RTDS WebSocket** (`rtds_websocket.py`) connects to `wss://ws-live-data.polymarket.com`
2. Receives ALL trades on Polymarket (global firehose, no per-trader subscription available)
3. O(1) set lookup: is `trader_address` in `elite_addresses` set? (500 addresses)
4. If elite → `on_rtds_trade()` callback → `_execute_mirror_trade()`
5. Position sizing: Kelly 0.25 → BotBankrollManager → capped at $100/trade, $500/market
6. `place_order()` → `order_gateway` → `paper_trading.py` (RTDS fast-path: skip risk/CVaR/drawdown)
7. DB write: `paper_trades` + `trade_events` (parallel, lock-free since S94)

### Exit Flow (two paths):
1. **RTDS-detected SELL**: Elite trader sells → RTDS catches it → `_execute_mirror_trade(side='SELL')` → close position
2. **Stop-loss** (every scan cycle, ~45s): `_check_and_execute_exits()` iterates `_open_positions`, computes `(current - entry) / entry`, closes if ≤ -15%

### Scan Loop (`scan_and_trade()`, every ~45s):
1. Startup: `_restore_state_on_startup()` (DB → memory)
2. Calibration check (if enabled)
3. RTDS WebSocket start/health check
4. Watchlist refresh (every 6h)
5. Daily reset check
6. Reap resolved markets
7. **Exit checks** (stop-loss only)
8. Diagnostic log: elite count, open positions, RTDS dispatched count
9. That's it. No consensus scan. No API polling.

### Key Components:
| Component | File | Purpose |
|-----------|------|---------|
| MirrorBot | `bots/mirror_bot.py` (1571 lines) | Main bot class |
| Elite Watchlist | `bots/elite_watchlist.py` | Monthly leaderboard → top 500 elites |
| RTDS WebSocket | `base_engine/data/rtds_websocket.py` | Global trade firehose |
| Order Gateway | `base_engine/execution/order_gateway.py` | Order routing (RTDS fast-path) |
| Paper Trading | `base_engine/execution/paper_trading.py` | Simulated fills (lock-free DB) |
| Settings | `config/settings.py` | All config (env-var backed) |
| Adaptive Safety | `bots/mirror_adaptive_safety.py` | DISABLED (drawdown formula bugged) |
| Calibration | `bots/mirror_calibration.py` | Conformal calibration (optional) |
| Trade Selector | `bots/mirror_trade_selector.py` | Signal enhancement (optional) |
| Chronos Filter | `bots/mirror_chronos_filter.py` | Time-based filtering (optional) |

### Two WebSocket Connections:
1. **RTDS** (trade feed): Rock solid, 0 disconnects, manual 5s ping loop
2. **Market Price WS** (`websocket_manager.py`): Flaky (229 disconnects/8h), server-side issue, has fallbacks

## CONFIG (LIVE VPS VALUES POST-S96)
```
# Position limits
MIRROR_MAX_CONCURRENT_POSITIONS=1000  # ← needs VPS .env update (still 400)
MIRROR_MAX_POSITIONS=1000
MIRROR_MAX_PER_MARKET=500
MIRROR_MAX_HOLD_HOURS=99999           # disabled

# Risk
MIRROR_STOP_LOSS_PCT=0.15
MIRROR_MIN_CONFIDENCE=0.55
MIRROR_MIN_RELIABILITY=0.52
MIRROR_TOTAL_CAPITAL=3000
MIRROR_MAX_CATEGORY_EXPOSURE_PCT=0.80  # ← user wants $4,000 absolute
MIRROR_MAX_DAILY_EXPOSURE_PCT=0.15     # ← user wants $10,000 absolute

# Performance
MIRROR_RTDS_FAST_PATH=true
MIRROR_SKIP_COORDINATOR_BUY=true
MIRROR_SKIP_LIQUIDITY_RTDS=true
MIRROR_ADAPTIVE_SAFETY=false

# Watchlist
WATCHLIST_ENABLED=true
WATCHLIST_SIZE=500

# Global
TOTAL_CAPITAL=20000
SIMULATION_MODE=true  # paper trading
kelly=0.25
```

## CRITICAL TRAPS (DO NOT BREAK)

### From CLAUDE.md (non-negotiable):
- **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL (except exit uses SELL)
- **`_market_meta_cache`**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS**
- **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **Python 3.13 scoping**: Local imports shadow top-level. NEVER use local imports that shadow.
- **RTDS envelope**: Must unwrap `data.get("payload", data)`
- **trade_events immutability trigger**: Must DISABLE TRIGGER for data cleanup

### From S94:
- **`_pending_db_writes`**: Populated under `_trade_lock`, drained AFTER release. NEVER `create_task()`.
- **`_pending_correlation_ids`**: In-memory idempotency during lock→DB gap.
- **`MIRROR_SKIP_COORDINATOR_BUY`**: Set false when other bots re-enabled.
- **`MIRROR_RTDS_FAST_PATH`**: Bypasses ALL risk for RTDS BUY. MirrorBot's own limits are safety net.

### From S96:
- **Consensus methods still exist** in mirror_bot.py (`_get_consensus_min`, `_collect_and_aggregate_elite_trades`, `_category_consensus_min`). They're just not called from scan_and_trade. Tests still cover them.
- **`_elite_refresh_every_n_scans = 480`** (line 71). At 45s/scan = ~6h refresh.
- **`needs_refresh()` in elite_watchlist.py** now uses `time.monotonic()` not date strings.

## LATENCY PROFILE (POST-S94)
| Metric | Value |
|--------|-------|
| RTDS broadcast delay | ~80ms (Polymarket, uncontrollable) |
| Our code (RTDS→order) | ~10-15ms |
| Total entry latency | ~100ms |
| exec_ms | 3-115ms |
| coord_ms | 0.1-49ms |
| risk_ms | 0.0ms (fast-path) |

## P&L STATUS
| Metric | Value |
|--------|-------|
| Realized P&L | +$18,469 (fantasy, 100% fills) |
| Open positions | ~400+ |
| Paper mode | Yes (SIMULATION_MODE=true) |

Note: P&L is inflated due to 100% fill assumption in paper trading. S95 added realistic fills but RTDS fast-path bypasses some fill realism.

## TEST STATUS
- **1629 passed, 10 failed (pre-existing), 8 skipped**
- Pre-existing failures: 8 `test_dashboard_async_worker.py` + 2 `test_web3_compatibility_fixes.py` (deleted UI modules)
- MirrorBot-specific tests: 87 tests in `tests/unit/test_mirror_bot_logic.py`

## GIT STATUS (as of end of S96)
```
Latest commit: 83c8b88 (S96 streamline — NOT YET DEPLOYED)
Previous:      2a87cdc (S96 position cap — DEPLOYED)
Branch: master
```

Unstaged changes exist from other sessions (esports_bot.py, database.py, deleted UI files) — these are NOT S96 and should not be touched.

## KEY FILES TO READ FIRST
1. `CLAUDE.md` — THE LAW. Read entirely before any change.
2. `bots/mirror_bot.py` — The bot (1571 lines)
3. `config/settings.py` — All config
4. `memory/MEMORY.md` — Cross-session memory index
5. This file — Session context

## PREVIOUS SESSION HANDOFFS (for deep context)
- **S94**: `memory/AGENT_HANDOFF_MIRRORBOT_SESSION94_2026_03_16.md` — Latency reduction (2967ms→11.9ms)
- **S93**: `memory/AGENT_HANDOFF_MIRRORBOT_SESSION93_2026_03_15.md` — Conformal dampening, Kelly 0.25
- **S92**: `AGENT_HANDOFF_MIRRORBOT_SESSION92_2026_03_15.md` — Realistic fills, backfill priority
- **S85**: `memory/AGENT_HANDOFF_SESSION85_DATA_OVERHAUL_2026_03_14.md` — P&L authority (trade_events)

## ROLLBACK COMMANDS
```bash
# Revert S96 streamline:
git revert 83c8b88
bash deploy/deploy.sh

# Revert position cap to 400:
export MIRROR_MAX_CONCURRENT_POSITIONS=400
export MIRROR_MAX_POSITIONS=400
sudo systemctl restart polymarket-ai

# Full revert to pre-S96:
git revert 83c8b88 2a87cdc
bash deploy/deploy.sh
```

## VPS ACCESS
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
# Service
sudo systemctl restart polymarket-ai
journalctl -u polymarket-ai -f | grep "MirrorBot"
# Env
sudo nano /opt/pa2-shared/.env
# Deploy
cd /opt/pa2-shared && bash deploy/deploy.sh
```
