# AGENT HANDOFF — MirrorBot Session 119 (2026-03-23)
## SCOPE: MirrorBot ONLY — no other bot changes
## SESSION TYPE: Full audit + 5 bug fixes + dead code purge + production readiness plan

---

## QUICK CONTEXT FOR NEW AGENT

You are continuing work on **MirrorBot**, one bot in a 15-bot Polymarket automated trading system. MirrorBot copies elite traders in real-time via RTDS WebSocket feed. The system is paper trading (`SIMULATION_MODE=true`) on an Ubuntu VPS at `34.251.224.21`. Real capital is NOT at risk yet but paper trading IS treated as production per CLAUDE.md.

**Read these files first:**
- `CLAUDE.md` — Prime directive, rules of engagement, critical traps
- `AGENT_HANDOFF_MIRRORBOT_SESSION117_2026_03_22.md` — Prior session context (S117 purge, calibration disable, data collection phase)
- This file — Everything done in S119

**Do NOT read/modify other bot files** (weather_bot.py, esports_bot.py, etc.) unless explicitly asked. This is a MirrorBot-scoped session.

---

## SYSTEM ARCHITECTURE (MirrorBot only)

### Runtime Data Flow
```
RTDSWebSocket (base_engine/data/rtds_websocket.py)
  → receives ALL Polymarket trades globally
  → EliteWatchlist.on_rtds_trade() (bots/elite_watchlist.py)
    → O(1) watchlist lookup (top 500 traders from monthly leaderboard)
    → dedup by composite key
    → wash trader detection (M6)
    → fast pre-filter: _can_open_position(price) WITHOUT category
    → confidence from efficiency score
    → MirrorBot._execute_mirror_trade() (bots/mirror_bot.py)
      → 16 rejection gates (blocklist, cooldown, category, position cap, opposing-side, same-side dedup, market active, near resolution, slippage, reliability LR, confidence, sizing, dust)
      → multi-factor confidence: F1(category win rate) + F2(price edge) + F3(whale conviction)
      → position sizing: Kelly via BotBankrollManager → reliability_mult → dampeners → per-market/daily caps
      → BaseBot.place_order() (bots/base_bot.py)
        → BaseEngine.place_order() (base_engine/base_engine.py)
          → OrderGateway.place_order() (base_engine/execution/order_gateway.py)
            → RTDS fast-path: skip drawdown, adverse sizing, risk_manager, liquidity, coordinator
            → L2 book walk for VWAP fill price
            → edge-at-VWAP gate (reject if edge eroded)
            → PaperTradingEngine.place_order() (base_engine/execution/paper_trading.py)
              → cash check, position update, fee calc, realized_pnl on SELL
              → persist to paper_trades + trade_events (ENTRY event)
```

### Periodic Housekeeping (scan_and_trade, every ~45s)
```
scan_and_trade()
  ├── _restore_state_on_startup() [scan 1: seed exposure, positions, sides, caches]
  ├── calibration fit [daily, DISABLED: MIRROR_USE_CALIBRATION=false]
  ├── adaptive safety refresh [DISABLED: MIRROR_ADAPTIVE_SAFETY=false]
  ├── leader reconciliation [scan 3, background, 60s timeout]
  ├── dedup flush to Redis [every 100 scans]
  ├── elite refresh [every 480 scans ~6h, 10s timeout]
  ├── reliability refresh [independent 30s timeout]
  ├── WebSocket + RTDS connect [scan 1]
  ├── daily watchlist refresh [once per UTC day]
  ├── daily reset [UTC boundary: zero exposure, category, consensus]
  ├── reap resolved positions [every 20 scans]
  ├── check & execute exits [every scan]
  │   ├── take-profit: >= 25%
  │   ├── force exit: >= 96h hold
  │   ├── graduated stop-loss: 15% (<48h) → 10% (48-72h) → 5% (>72h)
  │   └── circuit breaker: unrealized < -20% capital → 15min pause
  ├── prune dedup set
  └── RTDS stale detection + reconnect
```

### Resolution Pipeline (runs independently, every 30min)
```
resolution_backfill.py
  ├── Phase 1: Fetch missing markets (Gamma + CLOB API)
  ├── Phase 2: Backfill resolution status
  ├── Phase 3: Backfill prediction_log
  ├── Phase 4: Backfill paper_trades P&L (excludes SELL trades)
  ├── Phase 4b: Emit RESOLUTION events (WHERE NOT EXISTS + EXISTS ENTRY guard)
  ├── Phase 5: Backfill positions P&L
  └── Phase 6-7: PerformanceTracker + online learning
```

### Key Files (MirrorBot scope)
| File | Lines | Purpose |
|------|-------|---------|
| `bots/mirror_bot.py` | ~1600 | Main bot: 16 gates, confidence, sizing, exits |
| `bots/elite_watchlist.py` | 561 | RTDS handler, watchlist refresh, wash detection |
| `base_engine/learning/elite_reliability.py` | ~195 | Bayesian Beta reliability scoring |
| `bots/mirror_calibration.py` | 88 | FTS + horizon calibration (DISABLED) |
| `bots/mirror_adaptive_safety.py` | 147 | Dynamic position limits (DISABLED) |
| `base_engine/data/rtds_websocket.py` | 209 | WebSocket to Polymarket global trade feed |
| `base_engine/execution/paper_trading.py` | 833 | Paper trade execution, VWAP fills, P&L |
| `base_engine/execution/order_gateway.py` | ~960 | Kill switch, risk, liquidity, routing |
| `base_engine/data/resolution_backfill.py` | 547 | Market resolution + RESOLUTION event emission |
| `base_engine/data/database.py` | ~5000 | All DB operations (shared, MirrorBot-relevant functions noted below) |
| `config/settings.py` | all MIRROR_* | Configuration values |
| `bots/base_bot.py` | ~900 | BaseBot: scan loop, place_order, sizing |

### Key Database Functions (MirrorBot-relevant)
- `insert_trade_event()` (line 4617) — RESOLUTION uses atomic INSERT...SELECT WHERE NOT EXISTS
- `insert_paper_trade()` (line 3086) — UPSERT on (bot_name, market_id, side)
- `backfill_paper_trades_resolution()` (line 3271) — P&L formula, excludes SELL
- `backfill_positions_resolution()` (line 3488) — S119 fix: added condition_id join
- `mark_market_resolved()` (line 4874) — Updates traded_markets

---

## LIVE VPS CONFIG (as of this session)
```
SIMULATION_MODE=true (paper trading)
MIRROR_USE_CALIBRATION=false (disabled S117, re-enable ~Apr 5)
MIRROR_MIN_CONFIDENCE=0.45
MIRROR_MIN_RELIABILITY=0.52
MIRROR_MAX_CONCURRENT_POSITIONS=600
MIRROR_FAVORITE_DAMPENER=1.0 (S119: no-op for data collection)
MIRROR_EXTREME_PRICE_DAMPENER=1.0 (S119: no-op, code default)
MIRROR_TOTAL_CAPITAL=20000
BOT_BANKROLL_CONFIG: MirrorBot capital=20000, kelly=0.25, max_bet=300, max_daily=999999 (UNCAPPED)
WATCHLIST_ENABLED=true, WATCHLIST_SIZE=500
```

---

## WHAT WAS DONE THIS SESSION (S119)

### VPS Health Check + P1 Analysis
- 303 open positions, ~420 entries/day, bot healthy
- All-time corrected P&L: **+$26,986** (exits +$6,778, resolutions +$20,208)
- P1 analysis on 333 clean resolved entries:

**By confidence:** All buckets profitable. 0.60+ has 55.7% WR.
**By category:** Crypto is alpha engine (+$72/trade). Sports profitable but lower.
**By price:** <0.20 has massive win/loss asymmetry (20% WR but huge payoffs). 0.80+ is net negative.
**By side:** YES +$63/trade vs NO +$4/trade (asymmetry partly explained by Bug 1 below).

### Bug Fixes (5 bugs found and fixed)

**Bug 1 (P2) — NO-side contrarian logic inverted** (`mirror_bot.py:1389`)
- WAS: `NO and price > 0.55` (WRONG — expensive NO = consensus, was getting contrarian boost)
- NOW: `NO and price < 0.45` (correct — cheap NO = contrarian)
- Impact: Partly explains YES +$63/trade vs NO +$4/trade asymmetry

**Bug 2 (P3) — Category exposure not restored on startup** (`mirror_bot.py:_restore_state_on_startup`)
- `_category_exposure` was `{}` after restart, allowing $40k cap to be exceeded
- FIX: Added category seed query from today's ENTRY event_data->>'category', own try/except block
- Verified: logs `seeded _category_exposure: {'crypto': 21168, 'sports': 6584, ...}`

**Bug 3 (P4) — Circuit breaker fallback capital $3k** (`mirror_bot.py:826`)
- Fallback was 3000 when bankroll=None, should be MIRROR_TOTAL_CAPITAL (20000)
- FIX: Changed to read from settings

**Bug 4 (P3) — Position resolution backfill join misses MirrorBot** (`database.py:3519`)
- `WHERE p.market_id = m.id` — markets.id is INT, MirrorBot uses condition_id (0x hash)
- FIX: Added `OR p.market_id = m.condition_id` (shared infrastructure fix)

**Bug 5 (P1) — Orphan RESOLUTION events from deleted ENTRY events** (`resolution_backfill.py:429`)
- 778 orphan paper_trades (S117 flood purge deleted ENTRY events but left paper_trades)
- Backfill kept regenerating RESOLUTION events → 367 phantom events, -$9,237 phantom P&L
- FIX (3 layers):
  1. Phase 4b: Added `EXISTS (... ENTRY)` guard — only emit RESOLUTION when matching ENTRY exists
  2. Deleted 367 orphan RESOLUTION events (immutability trigger disabled/re-enabled)
  3. Marked 778 orphan paper_trades as `status='orphan'`
- Impact: Corrected P&L from $17,749 → **$26,986** (orphans were net negative, suppressing real P&L)

### Dead Code Removal

**Files deleted:**
- `bots/mirror_chronos_filter.py` (136 lines) — Amazon Chronos scaffold, never imported
- `bots/mirror_trade_selector.py` (256 lines) — d3rlpy RL scaffold, never imported, SQL broken

**Dead functions removed from `elite_reliability.py`:**
- `log_likelihood_ratio()`, `equivalent_samples()`, `beta_mean()` standalone, `import math`

**Dead imports/state removed from `mirror_bot.py`:**
- `import math`, `defaultdict`, `_signal_cache` + `_SIGNAL_CACHE_TTL`

**Redundant code removed:**
- `_reliability_tracker.refresh()` inside `_update_elite_traders()` — scan loop already refreshes independently

**11 dead configs removed from `settings.py`:**
- Consensus-era: `MIRROR_MAX_DELAY_MINUTES`, `MIRROR_MIN_CONSENSUS`, `MIRROR_HOT_TRADE_MAX_SECONDS`
- Dead zone (removed S117): `MIRROR_DEAD_ZONE_LOW/HIGH/DAMPENER`
- Never wired: `MIRROR_USE_CONFORMAL`, `MIRROR_USE_GEOMEAN_CONSENSUS`, `MIRROR_GEOMEAN_EXTREMIZE_D`, `MIRROR_CONFORMAL_MIN_RESOLVED`, `MIRROR_CONFORMAL_ALPHA`
- Deprecated: `MIRROR_MAX_CATEGORY_EXPOSURE_PCT`, `MIRROR_MAX_ENTRIES_PER_MARKET`, `MIRROR_MAX_HOLD_HOURS`

**Stale documentation updated:**
- Class docstring: "consensus" → RTDS architecture
- `MIRROR_FAVORITE_DAMPENER` default synced: 0.40 → 1.0 in settings.py

### Dampener Neutralization (S119 early)
- `MIRROR_EXTREME_PRICE_DAMPENER` code default: 0.25 → 1.0 (no-op)
- `MIRROR_FAVORITE_DAMPENER` code default: 0.40 → 1.0 (no-op)
- Settings.py default synced to 1.0
- Code preserved (if/log blocks intact) — re-enable by setting env var < 1.0

---

## CURRENT STATE (post all deploys)

| Metric | Value |
|--------|-------|
| Service | active |
| Open positions | 303 |
| Daily exposure | $30,281 (uncapped) |
| Category exposure | crypto $21k, sports $6.5k, esports $530 |
| Entry rate | ~420/day |
| All-time P&L | **+$26,986** (exits +$6,778, resolutions +$20,208) |
| Clean entries since S117 | 217+ (need 500 for calibration) |
| Orphan resolutions | **0** (root-fixed) |
| Orphan paper_trades | 778 marked `status='orphan'` (won't regenerate) |

---

## ITEMS TO REVISIT (user decision pending)

### R1: RL Trade Selector (HIGH potential, full rewrite needed)
Offline RL to learn which trades to copy vs skip. Implementation was non-functional (deleted). Concept viable now with 333+ resolved entries. Gate: 1000+ clean resolutions (~Mar 29-30). Effort: full rewrite of state encoding, SQL, training pipeline.

### R2: `equivalent_samples()` for Sizing (MEDIUM potential, quick wire)
Beta posterior width as sizing multiplier. ~10 lines: `min(1.0, eq_samples / 50)` ramp. Reduces variance on low-data traders. Low risk.

### R3: Conformal Prediction Intervals (MEDIUM potential)
Conservative Kelly using prediction interval lower bound. Infrastructure exists in `base_engine/features/calibration.py`. Gate: calibration re-enable (~Apr 5). Wire `conformal_interval` param in `calculate_bot_position_size()`.

### R4: Price Direction Pre-Filter (LOW-MED potential)
Simpler alternative to deleted Chronos: check `_ws_price_cache` for recent momentum exhaustion. ~15 lines, no dependencies. Skip if price moved >5% toward trade direction in last cycle.

### R5: Controlled Averaging-Up (LOW potential)
Relax same-side dedup to allow N entries per market. Currently one entry per (market, side) is final. Decision: is averaging-up desirable during data collection?

---

## PRODUCTION READINESS PLAN (SIMULATION_MODE=false)

### What's Production-Ready Now
- All 16 rejection gates in `_execute_mirror_trade`
- Multi-factor confidence formula (with Bug 1 contrarian fix)
- Position sizing (Kelly, dampeners, caps)
- RTDS trade detection + elite watchlist
- All dedup guards (opposing-side, same-side, transaction hash)
- Stop-loss / take-profit / force exit / circuit breaker
- Exposure tracking (daily, category, per-market) with restart restore
- trade_events audit trail with immutability trigger
- Resolution backfill with orphan guard

### What's NOT Production-Ready

**P0: CLOB Order Flow (BLOCKING)**
- `execution_engine.place_order()` never exercised with MirrorBot
- Need: API key test, condition_id format validation, token ID mapping, side mapping
- Test: Place $1 limit order, verify on CLOB, cancel

**P1: Fill Confirmation (BLOCKING)**
- Paper = instant fill. Live CLOB = async fill with polling
- Need: fill polling/WebSocket, timeout+cancel, partial fill handling
- `_open_positions[pos_key]["size"] += size` must use actual filled size

**P2: Balance Guards (BLOCKING)**
- Paper tracks `self.cash` in memory. Live needs on-chain USDC balance
- Need: pre-trade balance query, periodic reconciliation, startup seed from chain

**P3: Fee Alignment (HIGH)**
- `PAPER_TAKER_FEE_BPS=0` inflates paper P&L by ~1.5% notional
- Need: set to 150 for realistic paper, verify live fee passthrough, Kelly fee adjustment

**P4: Risk Pipeline Decision (HIGH)**
- RTDS fast-path skips drawdown/adverse/risk_manager. Safe in paper (own caps). Decision for live.
- Need: lower `max_daily_usd` from $999k to realistic live value

**P5: Canary Deployment (HIGH)**
- `CANARY_STAGE` already exists in order_gateway (5%→25%→50%→100%)
- Stage 1: `CANARY_STAGE=1`, $15 effective max bet, 48h validation
- Rollback: `SIMULATION_MODE=true` at any stage

**P6: Monitoring (MEDIUM)**
- Need: balance alert, daily P&L alert, fill rate, latency, position/balance reconciliation

**P7: Kill Switch (MEDIUM)**
- Need: test engagement, bot-level vs system-level, manual position close script

### Pre-Live Checklist
```
[ ] P0: $1 test order placed + cancelled via execution_engine
[ ] P0: condition_id format accepted by CLOB API
[ ] P1: Fill confirmation polling implemented
[ ] P1: Order timeout + cancel implemented
[ ] P2: Pre-trade balance check implemented
[ ] P3: PAPER_TAKER_FEE_BPS set to 150
[ ] P4: max_daily_usd set to live value
[ ] P5: CANARY_STAGE=1 tested 48h
[ ] P6: Alerts configured
[ ] P7: Kill switch tested
[ ] All tests passing
[ ] USDC deposited on VPS wallet
[ ] Operator available first 4h
```

**Timeline: ~2 weeks from start to full live**

---

## UPCOMING MILESTONES

| Date | Milestone | Action |
|------|-----------|--------|
| ~Mar 29 | 500+ clean resolutions | Run P1 analysis on S117+ cohort. Decide tuning. |
| ~Apr 5 | 500+ clean resolved with proper confidence distribution | Re-enable calibration (`MIRROR_USE_CALIBRATION=true`). Retrain with `calibration_exclude` filter. |
| ~Apr 5 | Dampener re-evaluation | Review 0.80+ price tier P&L. If still negative, set dampeners back < 1.0. |
| TBD | Production readiness | Execute P0-P7 plan above. |

---

## CRITICAL TRAPS (DO NOT VIOLATE)

1. **`MIRROR_USE_CALIBRATION=false`** — Do NOT re-enable until ~Apr 5
2. **`max_daily_usd=999999`** — Intentionally uncapped for data collection
3. **`_entered_market_sides`** — Must be populated from trade_events on startup AND updated on execution
4. **`calibration_exclude` filter** — `WHERE COALESCE(event_data->>'calibration_exclude', '') = ''`
5. **`trade_events` immutability trigger** — Disable on ALL partitions (not archive) before DELETE/UPDATE, re-enable after
6. **`_state_restored` guard** — If False, RTDS trades dropped
7. **Dead zone dampener is GONE** — Do not re-add (S117 analysis proved it fights alpha)
8. **Dampeners at 1.0** — `MIRROR_FAVORITE_DAMPENER=1.0`, `MIRROR_EXTREME_PRICE_DAMPENER=1.0`. Re-evaluate ~Mar 29
9. **NO contrarian fix** — `NO and price < 0.45` is contrarian. Do NOT revert to `> 0.55`
10. **Category exposure restored on startup** — Own try/except to not break position restore
11. **`backfill_positions_resolution` condition_id join** — `OR p.market_id = m.condition_id` (S119)
12. **Phase 4b ENTRY guard** — Resolution events only emitted when matching ENTRY exists
13. **Orphan paper_trades** — 778 marked `status='orphan'`. Don't re-process.
14. **Paper trading IS production** — Per CLAUDE.md
15. **YES/NO mandate** — `place_order()` requires `side="YES"/"NO"`. Never BUY/SELL.
16. **trade_events is P&L authority** — Never read paper_trades for P&L
17. **SELL trades excluded from resolution backfill** — SELL P&L computed by paper engine at exit
18. **MirrorBot entry price** — Uses CURRENT market price, NOT trader's fill price
19. **`_market_meta_cache`** — 3-tuple `(cat, ttr, expiry_monotonic)`. Never expand.
20. **asyncpg JSONB** — `CAST(:x AS jsonb)` NOT `:x::jsonb`

---

## FILES MODIFIED THIS SESSION

| File | Changes |
|------|---------|
| `bots/mirror_bot.py` | NO contrarian fix, category exposure seed, circuit breaker fallback, dead imports/state removed, redundant refresh removed, stale docstring updated, dampener defaults to 1.0 |
| `base_engine/learning/elite_reliability.py` | 3 dead functions + `import math` removed |
| `base_engine/data/database.py` | `backfill_positions_resolution` join fix (condition_id) |
| `base_engine/data/resolution_backfill.py` | Phase 4b ENTRY existence guard added |
| `config/settings.py` | 11 dead configs removed, `MIRROR_FAVORITE_DAMPENER` default 0.40→1.0 |
| `bots/mirror_chronos_filter.py` | DELETED |
| `bots/mirror_trade_selector.py` | DELETED |
| `tests/unit/test_mirror_bot_logic.py` | Test fixture: category exposure mock + dead config refs removed |

## DB CHANGES (VPS)

| Action | Count |
|--------|-------|
| Orphan RESOLUTION events deleted | 367 |
| Orphan paper_trades marked status='orphan' | 778 |
| P&L correction | +$17,749 → **+$26,986** (+$9,237 phantom removed) |

## TESTS
- **65 passed, 0 failed** (MirrorBot suite)
- **1613 passed, 1 failed** (full suite — pre-existing WeatherBot test, unrelated)

## DEPLOYS
- `20260322_204000` — Dampener defaults to 1.0
- `20260322_233531` — 4 bug fixes + dead code cleanup
- `20260323_001152` — Orphan resolution root fix (Phase 4b ENTRY guard)
