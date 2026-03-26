# AGENT HANDOFF — MirrorBot Session 101 (2026-03-18)
## Carbon Copy Transfer Document — Complete Context for Continuation

---

## 1. WHAT THIS SYSTEM IS

**Polymarket AI V2** is a live 15-bot automated trading system for Polymarket prediction markets. Real capital is at risk ($20K deployed). Currently in **paper trading mode** (`SIMULATION_MODE=true`) — all trades simulated with realistic fill modeling. Going live is flipping a boolean.

**MirrorBot** is the highest-performing bot. It copy-trades elite whale traders in real-time via RTDS (Real-Time Data Socket) WebSocket firehose. It does NOT analyze markets itself — it piggybacks on whale intelligence with Kelly-optimal sizing.

### Architecture (Post-S96/S99/S100/S101)
```
RTDS WebSocket (wss://ws-live-data.polymarket.com)
  -> streams ALL trades on Polymarket (global firehose, no auth)
  -> EliteWatchlist does O(1) lookup: is trader in our 500-whale watchlist?
  -> YES -> log to whale_trades table (S100, commit fix S101) + _execute_mirror_trade() with full validation pipeline
  -> NO -> discard

Paper Trading Fill Model (S100 upgrade):
  -> L2 Book Walk: fetches real order book, subtracts whale's consumed liquidity, walks remaining asks for VWAP
  -> Heuristic fallback: tiered slippage tiers + sqrt market impact (when book unavailable)
  -> 5 multiplicative fill probability factors: price-depth, size-impact, spread, time-of-day, participation
  -> Kyle's lambda adverse selection penalty
  -> Alpha decay (exponential signal deterioration)
  -> Resolution proximity penalty
  -> Cross-scan cumulative impact

Scan loop (45s interval) handles ONLY:
  - Stop-loss exits (15% default)
  - Housekeeping (dedup pruning, state persistence, elite refresh)
  - Stale RTDS detection + reconnect
  - Daily exposure reset at UTC midnight
  - Resolved position cleanup
```

**No consensus scan, no API polling for entries.** All entries are real-time via RTDS.

---

## 2. WHAT SESSION 101 DID (1 Deploy)

### Deploy 1 (`20260317_211034`): P1-P5 Sweep

#### P1: Entry Price Bucket Filters (3 new filters)
All-time P&L bucket analysis from S100 identified two problem buckets:
- **30-50c dead zone**: -$215 all-time despite 43.4% WR (near coin-flip, no asymmetric payoff)
- **70c+ favorites**: -$459 despite 69.7% WR (whale pushes price up, copier enters higher, losers catastrophic)

**Fix 1: Per-market entry cap** (`bots/mirror_bot.py` line 1124-1133)
- `MIRROR_MAX_ENTRIES_PER_MARKET` = 2 (default)
- Prevents stacking multiple entries on same cheap longshot (different whales trigger same market)
- Counts `_open_positions` keys matching `market_id:*` prefix

**Fix 2: Favorite dampening** (`bots/mirror_bot.py` line 1304-1310)
- `MIRROR_FAVORITE_PRICE_THRESHOLD` = 0.70, `MIRROR_FAVORITE_DAMPENER` = 0.40
- 0.40x sizing for entries above 70c
- Root cause: whale buys at 70c, pushes to 73c, MirrorBot enters at 73c. On loss, loses 73c/share.

**Fix 3: Dead zone dampening** (`bots/mirror_bot.py` line 1312-1320)
- `MIRROR_DEAD_ZONE_LOW` = 0.30, `MIRROR_DEAD_ZONE_HIGH` = 0.50, `MIRROR_DEAD_ZONE_DAMPENER` = 0.50
- 0.50x sizing in 30-50c range
- Confirmed firing in VPS logs: `mirror_dead_zone_dampened: 0.430 in [0.30, 0.50], size *= 0.50`

#### P2: Whale Trade Log Bug Fix (ROOT CAUSE FOUND)
**Bug**: `whale_trades` table had 0 rows after 17+ hours of deployment (S100).
**Root cause**: `_log_whale_trade()` in `elite_watchlist.py` was missing `await session.commit()`. SQLAlchemy `async_sessionmaker` defaults to `autocommit=False`. The INSERT executed but rolled back on context manager exit.
**Fix**: Added `await session.commit()` after the INSERT (line 566).
**Also**: Upgraded error log from `logger.debug` to `logger.warning` so failures are visible at INFO level.
**Result**: 700+ rows in first 2 minutes after deploy. Table now populating ~20 rows/second during active hours.

#### P2: Book Walk Verification
- `PAPER_BOOK_WALK_ENABLED=true` confirmed on VPS
- No book walk log output at 01:00 UTC (low activity, most trades rejected before reaching `place_order`)
- Config correct — will populate during US trading hours when copies actually execute

#### P3: Copied Flag Wired
**New method**: `_mark_whale_trade_copied()` in `elite_watchlist.py` (line 570-588)
- Called fire-and-forget after successful copy in `on_trade_event()` (line 439-445)
- Updates most recent `whale_trades` row matching `(trader_address, market_id, token_id)` via subquery
- **Verified**: 1 `copied=TRUE` row matching 1 successful copy at 01:15 UTC

#### P3: Unresolved Markets
- 1,987 unresolved in `traded_markets` (up from 604 at S100 — natural growth as new markets enter)
- Backfill resolving naturally. No action needed.

#### P5: Diagnostic Logging
- `session_factory created` — INFO level, fires only at startup, useful for debugging. No change.
- RTDS raw samples — already removed in S91. No action needed.
- P5 resolved: no noisy diagnostic logging remaining.

---

## 3. CURRENT STATE (As of Deploy 20260317_211034)

### Live Metrics (01:15 UTC)
- **Service**: Active, healthy
- **RTDS**: Connected, dispatching trades
- **whale_trades**: 700+ rows and growing (~20/sec active hours)
- **copied flag**: Working (1 TRUE / 699 FALSE at low-activity hour)
- **Dead zone dampener**: Firing on 30-50c entries (confirmed in logs)
- **Open positions**: ~85-91

### All-Time P&L (cumulative, paper)
| Bot | Realized |
|-----|----------|
| MirrorBot | ~+$18,400 |
| WeatherBot | ~+$3,000 |
| EsportsBot | ~-$22 |

---

## 4. KNOWN ISSUES / NEXT SESSION PRIORITIES

### P2: Whale Trade Volume Monitoring
- Table working now. Expected: ~270K rows/day at current watchlist activity.
- Monitor disk usage. May need 30-day retention policy (DELETE WHERE event_time < NOW() - INTERVAL '30 days').
- Run: `SELECT COUNT(*), pg_size_pretty(pg_total_relation_size('whale_trades')) FROM whale_trades;`

### P2: Book Walk Log Verification (Carry-forward)
- Config correct, but no book walk logs observed at 01:00 UTC (low activity)
- Verify during US hours: `grep "paper_book_walk" logs`

### P3: Bucket Filter Effectiveness
- S101 filters are live but need 24-48h of data to evaluate impact
- Run bucket analysis after 48h: compare win rates and P&L by bucket vs S100 baseline
- Script: `python scripts/win_rates.py` (or ad-hoc SQL query)

### P3: 1,987 Unresolved Markets
- Growing naturally. Backfill resolving. Monitor only.

---

## 5. COMPLETE FILE INVENTORY

### Files Modified This Session (S101)
| File | Lines | What Changed |
|------|-------|-------------|
| `bots/mirror_bot.py` | ~1,417 | Per-market entry cap (line 1124), favorite dampener (line 1304), dead zone dampener (line 1312) |
| `bots/elite_watchlist.py` | ~604 | `session.commit()` in `_log_whale_trade()`, `_mark_whale_trade_copied()` method, copied flag wiring |
| `config/settings.py` | ~717 | 7 new MIRROR_* config keys for bucket filters |
| `tests/unit/test_mirror_bot_logic.py` | ~975 | Added S101 settings to 3 MagicMock test patches |

### Core Files (MirrorBot-specific)
| File | Lines | Purpose |
|------|-------|---------|
| `bots/mirror_bot.py` | ~1,417 | Main bot: scan loop, entry/exit logic, all validation |
| `bots/elite_watchlist.py` | ~604 | O(1) elite trader lookup, RTDS trade dispatch, whale logging |
| `bots/mirror_calibration.py` | ~200 | FTS + conformal dampening (conformal DISABLED per S93) |
| `bots/mirror_adaptive_safety.py` | ~150 | Pearl-inspired adaptive constraints |
| `bots/mirror_chronos_filter.py` | ~135 | Time-based filtering |
| `bots/mirror_trade_selector.py` | ~255 | Trade selection logic |
| `base_engine/data/rtds_websocket.py` | 209 | RTDS global trade feed WebSocket |
| `base_engine/data/orderbook_tracker.py` | ~145 | L2 order book snapshots (used by book walk) |
| `config/settings.py` | ~717 | All configuration (MIRROR_* keys at lines 326-378) |
| `base_engine/risk/bankroll_manager.py` | 261 | Per-bot Kelly sizing + daily caps |

### Shared Files (touch with extreme care)
| File | Purpose | Blast Radius |
|------|---------|-------------|
| `base_engine/base_bot.py` | Base class for all 14 bots | ALL bots |
| `base_engine/base_engine.py` | Engine init, component wiring | ALL bots |
| `base_engine/risk/risk_manager.py` | Risk limits (not sizing) | ALL bots |
| `base_engine/data/database.py` | DB operations | ALL bots |
| `base_engine/execution/paper_trading.py` | Paper fill model | ALL bots |
| `base_engine/execution/order_gateway.py` | Order routing | ALL bots |
| `base_engine/data/position_manager.py` | Position CRUD + price updates | ALL bots |

---

## 6. _execute_mirror_trade() FULL VALIDATION PIPELINE (30 checks)

Order of checks (each returns False/skips if failed):

1. **Tier 0: In-memory blocklist** — O(1) set lookup
2. **Tier 0: Per-market cooldown** — 1800s re-entry cooldown
3. **Category resolution** — from cache or API, fallback "unknown" (S100)
4. **Category blocklist** — "15-minute", "speed" substrings
5. **Post-reset cooldown** — 60s after midnight
6. **Hard price bounds** — reject <5c or >95c
7. **Circuit breaker** — pause when portfolio bleeding
8. **Concurrent position cap** — 500 max
9. **Daily exposure cap** — $20K
10. **Per-category cap** — $40K (S100)
11. **Opposing-side dedup** — no YES+NO on same market
12. **Per-market entry cap** — max 2 entries per market (S101)
13. **SELL path** (if exit) — validate position exists
14. **Market validation** — active, accepting orders
15. **Near-resolution filter** — >4h to resolve
16. **Price correction** — use CURRENT market price, not trader's fill
17. **Slippage cap** — reject if >8% drift
18. **Elite reliability** — LR must be >= 1.0
19. **Domain drift penalty** — 0.5x if trader unfamiliar with category
20. **Calibration** (FTS on, conformal off) — mild 2-3pt dampening
21. **Kelly sizing** — BotBankrollManager
22. **Gray zone dampening** — 0.25x in 5-7c/93-95c range
23. **Favorite dampening** — 0.40x above 70c (S101)
24. **Dead zone dampening** — 0.50x in 30-50c range (S101)
25. **Per-market cap** — min($500, 10% of capital)
26. **Daily cap enforcement** — cap by remaining daily USD
27. **Dust filter** — reject if <$10
28. **Place order** — paper trade with realistic fill model (L2 book walk when available)
29. **Post-execution bookkeeping** — update exposure, positions, cooldowns
30. **Whale trade copied flag** — fire-and-forget UPDATE (S101)

---

## 7. CRITICAL TRAPS (DO NOT BREAK)

### MirrorBot-Specific
- **`_market_meta_cache`**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
- **Entry price**: Uses CURRENT market price from `get_market_from_index()`, NOT trader's historical fill.
- **RTDS envelope**: Must unwrap `data.get("payload", data)` — trade data NOT at top level.
- **RTDS dedup**: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`.
- **`_open_positions` on restart**: Clears in-memory; re-enters by EOD UTC.
- **Consensus scan path DELETED** (S96) — all entries via RTDS only.
- **API polling loop DELETED** (S96) — stop-loss is the only exit mechanism via scan.
- **Category fallback is "unknown"** (S100) — NOT empty string. Empty string bypasses category cap.
- **`whale_size_usd` default is 0.0** — backward compatible, book walk just gets 0 whale impact.
- **`_log_whale_trade` is fire-and-forget** — uses `asyncio.create_task()`, NEVER await it in hot path.
- **`_log_whale_trade` MUST `await session.commit()`** (S101 fix) — without it, INSERT rolls back silently.
- **`_mark_whale_trade_copied` is fire-and-forget** — uses `asyncio.create_task()`, non-financial metadata.

### System-Wide
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL.
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
- **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer.
- **trade_events is P&L AUTHORITY** — never read paper_trades for P&L.
- **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
- **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime string.
- **asyncpg timestamps**: `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`.
- **Python 3.13 scoping**: `from X import Y` inside function = local for ENTIRE function.
- **RESOLUTION event idempotency**: ON CONFLICT broken on partitioned tables. Uses atomic INSERT...SELECT.
- **trade_events immutability trigger**: Must DISABLE/re-enable for cleanup.
- **PatchDriftDetector**: Only set `_patch_timestamps` on genuine changes (`old is not None`).
- **positions table**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`.
- **prediction_log**: NO `rejection_reason`. Use `trade_executed` bool.
- **paper_trades**: NO `metadata` JSONB column. NO `resolved_pnl` column (it's `resolved_at`).
- **trade_events JSONB column is `event_data`** — NOT `metadata_json`.
- **CLOB volume=0**: Never use volume gates for MirrorBot.
- **`_market_index`**: Populated from API scan data. Does NOT have `bestBid`/`bestAsk`. Use tokens-based spread.
- **`_market_index_by_cid`**: Condition_id keyed index. MirrorBot uses 0x hashes, not numeric ids.
- **SQLAlchemy async sessions require explicit `await session.commit()`** — autocommit is OFF by default.

---

## 8. P&L MATH (MANDATORY)

**NEVER invert formulas for NO positions.** Prices are token-specific.

```
cost_basis = entry_price * size              # ALL sides (YES and NO)
unrealized_pnl = (current_price - entry) * size  # ALL sides
realized_pnl (exit) = (exit_price - entry) * size - fees
realized_pnl (resolution) = (resolution_value - entry) * size - fees
  where resolution_value = 1.0 if your side wins, 0.0 if it loses
```

Canonical script: `python scripts/bot_pnl.py MirrorBot 24`
Data sources: `trade_events` (realized), `positions.unrealized_pnl` (mark-to-market)

---

## 9. COMPLETE CONFIGURATION (Live VPS Values as of S101)

### BotBankrollManager (`bankroll_manager.py` line 36)
```python
"MirrorBot": {"capital": 3000, "kelly_fraction": 0.25, "max_bet_usd": 250, "max_daily_usd": 20000}
```

### VPS `.env` Overrides (these take precedence over settings.py defaults)
```
MIRROR_MAX_CONCURRENT_POSITIONS=500
MIRROR_MAX_CATEGORY_EXPOSURE_USD=40000
MIRROR_USE_CALIBRATION=true
MIRROR_USE_CONFORMAL=false
PAPER_BOOK_WALK_ENABLED=true
WHALE_TRADE_LOG_ENABLED=true
PAPER_REALISTIC_FILLS=true
SIMULATION_MODE=true
WATCHLIST_ENABLED=true
WATCHLIST_SIZE=1000
```

### settings.py MIRROR_* Keys (S101 additions marked)
```python
MIRROR_MAX_DELAY_MINUTES = 30
MIRROR_MIN_CONFIDENCE = 0.55
MIRROR_MAX_PER_MARKET = 500
MIRROR_MAX_PER_MARKET_PCT = 0.10
MIRROR_MAX_CATEGORY_EXPOSURE_USD = 40000
MIRROR_MAX_CONCURRENT_POSITIONS = 500
MIRROR_EXIT_ENABLED = true
MIRROR_MIN_RELIABILITY = 0.52
MIRROR_MIN_ELITE_TRADES = 100
MIRROR_USE_CALIBRATION = false              # VPS overrides to true
MIRROR_USE_CONFORMAL = false
MIRROR_SKIP_LIQUIDITY_RTDS = true
MIRROR_SKIP_COORDINATOR_BUY = true
MIRROR_RTDS_FAST_PATH = true
MIRROR_STOP_LOSS_PCT = 0.15
MIRROR_MAX_HOLD_HOURS = 99999
MIRROR_MARKET_COOLDOWN_SECONDS = 1800
MIRROR_MIN_TRADE_USD = 10.0
MIRROR_MAX_SLIPPAGE_PCT = 0.08
MIRROR_HARD_MIN_PRICE = 0.05
MIRROR_HARD_MAX_PRICE = 0.95
MIRROR_MIN_PRICE = 0.07
MIRROR_MAX_PRICE = 0.93
MIRROR_EXTREME_PRICE_DAMPENER = 0.25
MIRROR_CATEGORY_BLOCKLIST = "15-minute,speed"
PAPER_BOOK_WALK_ENABLED = false             # VPS overrides to true
WHALE_TRADE_LOG_ENABLED = true
# S101 additions:
MIRROR_MAX_ENTRIES_PER_MARKET = 2           # cap stacking on same market
MIRROR_FAVORITE_PRICE_THRESHOLD = 0.70      # favorite dampening starts here
MIRROR_FAVORITE_DAMPENER = 0.40             # 0.40x sizing above threshold
MIRROR_DEAD_ZONE_LOW = 0.30                 # dead zone lower bound
MIRROR_DEAD_ZONE_HIGH = 0.50                # dead zone upper bound
MIRROR_DEAD_ZONE_DAMPENER = 0.50            # 0.50x sizing in dead zone
```

---

## 10. INFRASTRUCTURE

- **VPS**: Ubuntu-3 at `34.251.224.21` (16GB/4vCPU)
- **SSH**: `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21`
- **Deploy**: `bash deploy/deploy.sh` — tar, upload, extract, atomic symlink, restart, 90s health check
- **Service**: `sudo systemctl restart polymarket-ai`
- **Logs**: `journalctl -u polymarket-ai -f | grep Mirror`
- **DB**: `PGPASSWORD=polymarket_s46 psql -h localhost -p 6432 -U polymarket -d polymarket`
- **Python on VPS**: `cd /opt/polymarket-ai-v2 && source venv/bin/activate && PYTHONPATH=/opt/polymarket-ai-v2 python scripts/bot_pnl.py MirrorBot 24`
- **Rollback**: `ssh ... 'ls -lt /opt/pa2-releases/ | head -5'` then `ln -sfn /opt/pa2-releases/PREV /opt/polymarket-ai-v2`

---

## 11. FEEDBACK RULES (From Memory — Non-Negotiable)

### Scope Lock (CRITICAL)
- **ONLY fix what the handoff or user explicitly requests**
- NEVER add unsolicited features, refactors, or "improvements"
- If you notice something, surface it verbally — do NOT fix it unless asked

### Bot Sessions
- Each session is scope-locked to a SINGLE bot
- No bleed-over to other bots unless explicitly requested
- Shared module changes only if they fix a bot-specific bug

### User Preferences
- Senior developer who values speed, brevity, and surgical precision
- "Working code is sacred" — fix only what is broken
- Prefers short, direct communication
- Values data-driven decisions (show me the logs, show me the numbers)
- Paper trading IS production — never cut corners

### P&L Math
- NEVER invert formulas for NO positions — prices are token-specific
- `cost = entry_price * size` (ALL sides)
- `uPnL = (current - entry) * size` (ALL sides)

---

## 12. SESSION CHAIN

| Session | Date | Focus | Key Outcome |
|---------|------|-------|-------------|
| **S101** | **2026-03-18** | **Bucket filters, whale trade commit fix, copied flag** | **3 P&L bucket filters, whale_trades 0→700+ rows, copied flag wired** |
| S100 | 2026-03-17 | L2 book walk, whale trade log, 5 config fixes | Real order book fills, whale persistence, category cap $40K |
| S99 | 2026-03-17 | 6 architecture elevations + S99b diagnostic fixes | RTDS stall fix, price bounds, daily $20K, position cap 500 |
| S96 | 2026-03-16 | Strip consensus scan, API polling, streamline | RTDS-only architecture |
| S94 | 2026-03-16 | Latency 2967ms -> 11.9ms | Lock-free DB, RTDS fast-path |
| S93 | 2026-03-15 | Conformal dampening fix | Kelly 0.0625 -> 0.25, realistic P&L +$4.5k |
| S92 | 2026-03-15 | Realistic fills, RTDS cache | Fill probability model |
| S90 | 2026-03-14 | Scheduler zombie advisory lock | P0 fix |
| S88 | 2026-03-14 | False observation mode | PatchDriftDetector fix |
| S87 | 2026-03-14 | RESOLUTION dedup | Atomic INSERT...SELECT |
| S85 | 2026-03-14 | Resolution backfill + P&L overhaul | 544 markets resolved |
| S81 | 2026-03-12 | RTDS live + 6 fixes | Global trade feed operational |

---

## 13. FIRST SCAN INSTRUCTIONS FOR NEW AGENT

1. Read `CLAUDE.md` (project rules — non-negotiable)
2. Read this handoff document (you are here)
3. Read `memory/MEMORY.md` (memory index — first 200 lines only loaded due to size)
4. Ask user what they want to work on
5. Before touching ANY file: state the bug, list files, grep for dependents, read the entire file
6. One fix per commit. Preserve every function signature. No scope creep.
7. Verify with VPS logs after every deploy.

**This is Session 101. Next session should be Session 102.**

---

## 14. DEPLOYS THIS SESSION (Rollback Reference)

| # | Timestamp | Changes | Rollback |
|---|-----------|---------|----------|
| 1 | `20260317_211034` | P1 bucket filters + P2 whale commit fix + P3 copied flag | `git revert` + redeploy. Bucket filters: set dampeners to 1.0 to disable without code change |
