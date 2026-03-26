# AGENT HANDOFF — MirrorBot Session 99 (2026-03-17)
## Carbon Copy Transfer Document — Complete Context for Continuation

---

## 1. WHAT THIS SYSTEM IS

**Polymarket AI V2** is a live 15-bot automated trading system for Polymarket prediction markets. Real capital is at risk ($20K deployed). Currently in **paper trading mode** (`SIMULATION_MODE=true`) — all trades simulated with realistic fill modeling. Going live is flipping a boolean.

**MirrorBot** is the highest-performing bot. It copy-trades elite whale traders in real-time via RTDS (Real-Time Data Socket) WebSocket firehose. It does NOT analyze markets itself — it piggybacks on whale intelligence with Kelly-optimal sizing.

### Architecture (Post-S96/S99)
```
RTDS WebSocket (wss://ws-live-data.polymarket.com)
  → streams ALL trades on Polymarket (global firehose, no auth)
  → EliteWatchlist does O(1) lookup: is trader in our 500-whale watchlist?
  → YES → _execute_mirror_trade() with full validation pipeline
  → NO → discard

Scan loop (45s interval) handles ONLY:
  - Stop-loss exits (15% default)
  - Housekeeping (dedup pruning, state persistence, elite refresh)
  - Stale RTDS detection + reconnect
  - Daily exposure reset at UTC midnight
  - Resolved position cleanup
```

**No consensus scan, no API polling for entries.** All entries are real-time via RTDS.

---

## 2. WHAT THIS SESSION DID (S99 + S99b)

### S99: Architecture Elevations (6 new filters)
Reviewed a 3rd-party architecture assessment. Elevated 6 of 13 recommendations, declined 7. Implemented:

1. **accepting_orders gate** — reject markets not accepting orders
2. **Price bounds** — tiered "Option C": hard reject at 5/95¢, gray zone 5-7¢/93-95¢ at 0.25x sizing
3. **Circuit breaker** — pause entries when portfolio bleeding (configurable threshold)
4. **Take-profit** — exit when position exceeds target gain
5. **Near-resolution filter** — reject markets resolving within 4h
6. **Graduated exit pressure** — time-based exit urgency

### S99b: Post-Deploy Diagnostic Fixes (3 issues found in 4h production data)

**Issue 1 (P1): RTDS Silent Stall**
- Root cause: `await self.ws.recv()` blocked forever on silently hung WebSocket
- Fix (two layers):
  - WebSocket level: `asyncio.wait_for(ws.recv(), timeout=120s)` + `asyncio.TimeoutError` handler
  - Scan level: Track dispatch count across scans. If 4 consecutive scans unchanged AND `last_recv_age > 120s` → disconnect + reconnect
- Files: `base_engine/data/rtds_websocket.py`, `bots/mirror_bot.py`

**Issue 2 (P3): Midnight Burst Spike**
- Root cause: UTC midnight reset clears daily exposure to $0 → 30+ queued RTDS trades fire in <5s → 12.4s scan spike
- Fix: 60s cooldown after daily reset (`_daily_reset_cooldown = time.monotonic() + 60`)
- File: `bots/mirror_bot.py` `_check_daily_reset()` and `_can_open_position()`

**Issue 3 (P4): Category Cap Too Tight**
- Was $4K, causing 8,654 rejections in 4h
- Fix: Raised to $10K (`MIRROR_MAX_CATEGORY_EXPOSURE_USD`)
- File: `config/settings.py`

### S99b Additional Changes
- **Position cap**: 1000 → 500 (`MIRROR_MAX_CONCURRENT_POSITIONS`)
- **Daily cap**: $10K → $20K (in `bankroll_manager.py` `_DEFAULT_BOT_CONFIGS`)
- **Tiered price bounds (Option C)**: Hard 5/95¢ + soft 7/93¢ at 0.25x dampening

---

## 3. CURRENT STATE (As of Deploy 20260317_133509)

### Live Metrics (last check)
- **Open positions**: 95-144 (fluctuating with entries/exits/resolutions)
- **Daily exposure**: $13,291 / $20,000
- **RTDS**: Connected, dispatching ~5K-6K events per scan interval
- **Scan latency**: 77ms (target <200ms)
- **Elites tracked**: 500

### 24h P&L (as of 17:38 UTC 2026-03-17)
| Metric | Value |
|--------|-------|
| Open positions | 95 |
| Cost basis | $6,060 |
| Market value | $6,254 |
| Unrealized P&L | +$194 |
| Realized (exits, 24h) | +$809 (41 exits) |
| Realized (resolutions, 24h) | +$917 (139 resolutions) |
| Net P&L (24h) | **+$1,920** |
| All-time realized | **+$20,376** |
| Entries (24h) | 613 |

### IMPORTANT: ALL P&L IS PAPER/SIMULATED
- Every position has `is_paper=True`
- Realistic fill modeling active (fill probability based on order book depth)
- 44% win rate on resolutions, avg +$16.84/resolution (winners larger than losers = asymmetric payoff)
- Going live requires only flipping `SIMULATION_MODE=false`

### Rejection Breakdown (2h sample)
| Gate | Count | Notes |
|------|-------|-------|
| Daily cap | 67,489 | Now raised to $20K |
| Price bounds | 6,852 | Hard 5/95¢ rejection working |
| Price dampened | 9 | Gray zone 5-7/93-95¢ at 0.25x |
| Category cap | 0 | $10K giving headroom |
| Position cap | 0 | At 144, well under 500 |
| Reset cooldown | 0 | Fires at UTC midnight only |

---

## 4. COMPLETE FILE INVENTORY

### Core Files (MirrorBot-specific)
| File | Lines | Purpose |
|------|-------|---------|
| `bots/mirror_bot.py` | ~1,378 | Main bot: scan loop, entry/exit logic, all validation |
| `bots/elite_watchlist.py` | ~300 | O(1) elite trader lookup, RTDS trade dispatch |
| `bots/mirror_calibration.py` | ~200 | FTS + conformal dampening (conformal DISABLED per S93) |
| `bots/mirror_adaptive_safety.py` | ~150 | Pearl-inspired adaptive constraints |
| `base_engine/data/rtds_websocket.py` | 209 | RTDS global trade feed WebSocket |
| `config/settings.py` | ~700 | All configuration (MIRROR_* keys at lines 326-368) |
| `base_engine/risk/bankroll_manager.py` | 261 | Per-bot Kelly sizing + daily caps |

### Shared Files (touch with extreme care)
| File | Purpose | Blast Radius |
|------|---------|-------------|
| `base_engine/base_bot.py` | Base class for all 14 bots | ALL bots |
| `base_engine/risk/risk_manager.py` | Risk limits (not sizing) | ALL bots |
| `base_engine/data/database.py` | DB operations | ALL bots |
| `base_engine/data/position_manager.py` | Position CRUD + price updates | ALL bots |
| `base_engine/learning/elite_reliability.py` | Bayesian elite trader scoring | MirrorBot only |

### Scripts
| File | Purpose |
|------|---------|
| `scripts/bot_pnl.py` | Canonical P&L report (run on VPS with venv) |
| `scripts/audit_mirror_pnl.py` | Deep P&L audit (dedup ratio, win rates) |
| `scripts/snapshot.py` | Position snapshot |
| `scripts/check_losers.py` | Losing position analysis |
| `scripts/win_rates.py` | Win rate analysis |

### Tests
| File | Purpose |
|------|---------|
| `tests/unit/test_mirror_bot_logic.py` | MirrorBot unit tests (~1100 lines) |
| `tests/unit/test_bankroll_manager.py` | BotBankrollManager tests |

---

## 5. COMPLETE CONFIGURATION (Live VPS Values)

### BotBankrollManager (`bankroll_manager.py` line 36)
```python
"MirrorBot": {"capital": 3000, "kelly_fraction": 0.25, "max_bet_usd": 250, "max_daily_usd": 20000}
```

### settings.py MIRROR_* Keys (All with env var overrides)
```python
MIRROR_MAX_DELAY_MINUTES = 30
MIRROR_MIN_CONSENSUS = 2           # Irrelevant post-S96 (no consensus scan)
MIRROR_MIN_CONFIDENCE = 0.55       # Elite must win >55% of trades
MIRROR_MAX_PER_MARKET = 500        # $500 absolute cap per market
MIRROR_MAX_PER_MARKET_PCT = 0.10   # 10% of capital per market
MIRROR_MAX_CATEGORY_EXPOSURE_USD = 10000  # S99b: per-category cap
MIRROR_MAX_TRACKED_TRADES = 10000  # Dedup dict size
MIRROR_EXIT_ENABLED = true         # Stop-loss + exit mirroring
MIRROR_MAX_CONCURRENT_POSITIONS = 500  # S99b: position cap
MIRROR_MAX_DAILY_EXPOSURE_PCT = 0.15   # DEPRECATED — use bankroll.max_daily_usd
MIRROR_HOT_TRADE_MAX_SECONDS = 900     # 15min freshness window
MIRROR_MIN_RELIABILITY = 0.52     # Bayesian posterior > coin flip
MIRROR_MIN_ELITE_TRADES = 100     # OR $10K volume gate
MIRROR_USE_CALIBRATION = false     # FTS calibration OFF
MIRROR_USE_CONFORMAL = false       # Conformal dampening OFF (S93)
MIRROR_ADAPTIVE_SAFETY = false     # Adaptive safety OFF
MIRROR_SKIP_LIQUIDITY_RTDS = true  # Skip liquidity check for RTDS trades
MIRROR_SKIP_COORDINATOR_BUY = true # Skip coordinator for RTDS BUY (saves 72-464ms)
MIRROR_RTDS_FAST_PATH = true       # Skip risk/drawdown/fill for RTDS (~20-30ms saved)
MIRROR_STOP_LOSS_PCT = 0.15       # 15% stop-loss
MIRROR_MAX_HOLD_HOURS = 99999     # Disabled (S96)
MIRROR_MAX_POSITIONS = 1000        # S96 max positions in DB
MIRROR_TOTAL_CAPITAL = 3000
MIRROR_CATEGORY_BLOCKLIST = "15-minute,speed"
MIRROR_MARKET_COOLDOWN_SECONDS = 1800  # 30min re-entry cooldown
MIRROR_MIN_TRADE_USD = 10.0       # Dust filter
MIRROR_MAX_SLIPPAGE_PCT = 0.08    # 8% max price drift
MIRROR_HARD_MIN_PRICE = 0.05      # S99b: hard floor
MIRROR_HARD_MAX_PRICE = 0.95      # S99b: hard ceiling
MIRROR_MIN_PRICE = 0.07           # S99b: soft floor (gray zone start)
MIRROR_MAX_PRICE = 0.93           # S99b: soft ceiling (gray zone start)
MIRROR_EXTREME_PRICE_DAMPENER = 0.25  # S99b: gray zone sizing multiplier
BOT_ENABLED_MIRROR = true
SCAN_INTERVAL_MIRROR = 45         # 45s scan interval
WATCHLIST_ENABLED = true
WATCHLIST_SIZE = 1000
```

---

## 6. _execute_mirror_trade() FULL VALIDATION PIPELINE

Order of checks (each returns False/skips if failed):

1. **Tier 0: In-memory blocklist** — O(1) set lookup
2. **Tier 0: Per-market cooldown** — 1800s re-entry cooldown
3. **Category resolution** — from cache or API
4. **Category blocklist** — "15-minute", "speed" substrings
5. **Post-reset cooldown** — 60s after midnight
6. **Hard price bounds** — reject <5¢ or >95¢
7. **Circuit breaker** — pause when portfolio bleeding
8. **Concurrent position cap** — 500 max
9. **Daily exposure cap** — $20K
10. **Per-category cap** — $10K
11. **Opposing-side dedup** — no YES+NO on same market
12. **SELL path** (if exit) — validate position exists
13. **Market validation** — active, accepting orders
14. **Near-resolution filter** — >4h to resolve
15. **Price correction** — use CURRENT market price, not trader's fill
16. **Slippage cap** — reject if >8% drift
17. **Elite reliability** — LR must be ≥1.0
18. **Domain drift penalty** — 0.5x if trader unfamiliar with category
19. **Calibration** (disabled) — FTS + conformal
20. **Kelly sizing** — BotBankrollManager
21. **Gray zone dampening** — 0.25x in 5-7¢/93-95¢ range
22. **Per-market cap** — min($500, 10% of capital)
23. **Daily cap enforcement** — cap by remaining daily USD
24. **Dust filter** — reject if <$10
25. **Place order** — paper trade with realistic fill model
26. **Post-execution bookkeeping** — update exposure, positions, cooldowns

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

## 9. INFRASTRUCTURE

- **VPS**: Ubuntu-3 at `34.251.224.21` (16GB/4vCPU)
- **SSH**: `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21`
- **Deploy**: `bash deploy/deploy.sh` — tar, upload, extract, atomic symlink, restart, 90s health check
- **Service**: `sudo systemctl restart polymarket-ai`
- **Logs**: `journalctl -u polymarket-ai -f | grep Mirror`
- **Python on VPS**: `cd /opt/polymarket-ai-v2 && source venv/bin/activate && PYTHONPATH=/opt/polymarket-ai-v2 python scripts/bot_pnl.py MirrorBot 24`
- **DB**: PostgreSQL on VPS, accessed via `Database()` class
- **Redis**: Used for dedup state persistence, exit cooldowns

---

## 10. FEEDBACK RULES (From Memory)

### Scope Lock (CRITICAL)
- **ONLY fix what the handoff or user explicitly requests**
- NEVER add unsolicited features, refactors, or "improvements"
- If you notice something, surface it verbally — do NOT fix it unless asked
- Session 90 incident: agent added `WEATHER_CITY_BLACKLIST` feature without being asked

### Bot Sessions
- Each session is scope-locked to a SINGLE bot
- No bleed-over to other bots unless explicitly requested
- Shared module changes only if they fix a bot-specific bug
- Cross-bot changes require explicit user approval

### User Preferences
- User is a senior developer who values speed, brevity, and surgical precision
- "Working code is sacred" — fix only what is broken
- Prefers short, direct communication
- Values data-driven decisions (show me the logs, show me the numbers)
- Paper trading IS production — never cut corners because "it's just paper"

---

## 11. OUTSTANDING ITEMS / ROADMAP

### Active
- **P2**: 604 markets still unresolved in traded_markets (genuinely open, backfill clearing them)
- **P3**: `no_prediction: 12` per scan — team name parsing failures (CS2/Valorant)
- **P5**: Remove diagnostic logging (session_factory warning, RTDS raw samples)

### Monitoring (S99b deploys)
- Watch for `rtds_recv_timeout` — confirms timeout detection if stall recurs
- Watch for `rtds_stale_dispatch` — confirms scan-level stale detection
- Watch for `mirror_reset_cooldown` — fires at next UTC midnight
- Category cap rejections should be near-zero with $10K cap
- Daily cap was binding at $10K, now raised to $20K — monitor if it fills again

### Resolved (keep for context)
- S99: RTDS silent stall → recv timeout + stale dispatch detector
- S99: Midnight burst → 60s post-reset cooldown
- S99: Category cap → $10K
- S96: Consensus scan deleted, API polling deleted
- S94: Latency 2967ms → 11.9ms
- S93: Conformal dampening disabled (Kelly 0.0625 → 0.25)
- S92: RTDS startup cache, realistic fills
- S90: Scheduler zombie advisory lock
- S88: False observation mode on restart
- S87: RESOLUTION dedup broken on partitioned tables
- S85: Resolution backfill 3 root causes + P&L data overhaul

---

## 12. HOW TO RUN SCRIPTS ON VPS

```bash
# SSH to VPS
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21

# Run any script
cd /opt/polymarket-ai-v2 && source venv/bin/activate && PYTHONPATH=/opt/polymarket-ai-v2 python scripts/bot_pnl.py MirrorBot 24

# Check logs
journalctl -u polymarket-ai --since "30 min ago" --no-pager | grep -i mirror

# Deploy from local Windows machine
bash deploy/deploy.sh

# Restart without deploy
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 'sudo systemctl restart polymarket-ai'

# Rollback
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 'ls -lt /opt/pa2-releases/ | head -5'
# Then: ln -sfn /opt/pa2-releases/PREVIOUS_TIMESTAMP /opt/polymarket-ai-v2
```

---

## 13. PREVIOUS SESSION CHAIN

| Session | Date | Focus | Key Outcome |
|---------|------|-------|-------------|
| S99 | 2026-03-17 | 6 architecture elevations + S99b diagnostic fixes | RTDS stall fix, price bounds, daily $20K, position cap 500 |
| S96 | 2026-03-16 | Strip consensus scan, API polling, streamline | RTDS-only architecture |
| S94 | 2026-03-16 | Latency 2967ms→11.9ms | Lock-free DB, RTDS fast-path, category keywords |
| S93 | 2026-03-15 | Conformal dampening fix | Kelly 0.0625→0.25, realistic P&L +$4.5k |
| S92 | 2026-03-15 | Realistic fills, RTDS cache | Kelly 0.30→0.25, backfill priority |
| S90 | 2026-03-14 | Scheduler zombie advisory lock | P0 fix, resolution batch 100 |
| S88 | 2026-03-14 | False observation mode | PatchDriftDetector fix |
| S87 | 2026-03-14 | RESOLUTION dedup | Atomic INSERT...SELECT |
| S86 | 2026-03-14 | Ingestion sync + P&L dedup | 3238 duplicate resolutions deleted |
| S85 | 2026-03-14 | Resolution backfill + P&L overhaul | 544 markets resolved, trade_events authority |
| S81 | 2026-03-12 | RTDS live + 6 fixes | Global trade feed operational |

---

## 14. FIRST SCAN INSTRUCTIONS FOR NEW AGENT

1. Read `CLAUDE.md` (project rules — non-negotiable)
2. Read this handoff document
3. Read `memory/MEMORY.md` (memory index)
4. Read `memory/feedback_scope_lock.md` + `memory/feedback_bot_sessions.md` + `memory/feedback_pnl_math.md`
5. Ask user what they want to work on
6. Before touching ANY file: state the bug, list files, grep for dependents, read the entire file
7. One fix per commit. Preserve every function signature. No scope creep.

**This is Session 99. Next session should be Session 100.**
