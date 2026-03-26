# AGENT HANDOFF — MirrorBot Session 100 (2026-03-17)
## Carbon Copy Transfer Document — Complete Context for Continuation

---

## 1. WHAT THIS SYSTEM IS

**Polymarket AI V2** is a live 15-bot automated trading system for Polymarket prediction markets. Real capital is at risk ($20K deployed). Currently in **paper trading mode** (`SIMULATION_MODE=true`) — all trades simulated with realistic fill modeling. Going live is flipping a boolean.

**MirrorBot** is the highest-performing bot. It copy-trades elite whale traders in real-time via RTDS (Real-Time Data Socket) WebSocket firehose. It does NOT analyze markets itself — it piggybacks on whale intelligence with Kelly-optimal sizing.

### Architecture (Post-S96/S99/S100)
```
RTDS WebSocket (wss://ws-live-data.polymarket.com)
  -> streams ALL trades on Polymarket (global firehose, no auth)
  -> EliteWatchlist does O(1) lookup: is trader in our 500-whale watchlist?
  -> YES -> log to whale_trades table (S100) + _execute_mirror_trade() with full validation pipeline
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

## 2. WHAT SESSION 100 DID (5 Deploys)

### Deploy 1 (`20260317_142446`): VPS Config + Category Cap + Empty Category Fix
**3 anomalies found during VPS health check and fixed:**

1. **Position cap mismatch** — VPS `.env` had `MIRROR_MAX_CONCURRENT_POSITIONS=200` (S79 legacy), code default was 500 (S99b). Fixed: `sudo sed` to 500 in `.env`.

2. **Category cap raised $10K -> $40K** — Per user directive "40k in all areas do not miss one to create a bug."
   - `config/settings.py`: default `10000` -> `40000`
   - `bots/mirror_bot.py` line 940: fallback `4000` -> `40000`
   - VPS `.env`: Already had `MIRROR_MAX_CATEGORY_EXPOSURE_USD=10000` (overrides code). Updated to `40000`.

3. **Empty category bypass fix** — `_get_market_meta()` returned `""` for NULL categories. `if category:` is falsy for empty string, so uncategorized markets bypassed the per-category cap entirely.
   - Fix: `bots/mirror_bot.py` line 1082/1084: `category = _meta_cat or ""` -> `category = _meta_cat or "unknown"`
   - Now ALL markets get category-capped, even if the API returns no category.

### Deploy 2 (`20260317_143853`): Volume Fallback + Conformal Cleanup
1. **Paper fill model volume fallback 1000 -> 50000** — `MIRROR_SKIP_LIQUIDITY_RTDS=true` means volume=0 passed to fill model. Fallback of 1000 made every $100+ order look like 10-30% of daily volume -> 100% rejection rate. Median Polymarket market does ~$50K/day.
   - Files: `base_engine/execution/paper_trading.py` (2 instances via replace_all)

2. **VPS `.env` MIRROR_USE_CONFORMAL=false** — S89 added `true` to `.env`, S93 disabled conformal in code (hardcoded `None` at mirror_bot.py line 1267). `.env` flag was cosmetic but misleading. Cleaned up.
   - Note: `MIRROR_USE_CALIBRATION=true` kept intentionally — FTS is working and bot has been profitable with it.

### Deploy 3 (`20260317_150230`): Book Depth Data for Fill Model
Wired real `bestBid`/`bestAsk` from `_market_index` into paper trading `place_order()` call.

**Problem**: `_market_index` is populated from API scan data which has `tokens` array but NOT `bestBid`/`bestAsk` keys. Also, MirrorBot passes condition_id (0x hash) but `_market_index` is keyed by numeric id.

**Fixes** (`base_engine/execution/order_gateway.py`):
- Added `_market_index_by_cid` lookup (falls back when numeric id misses)
- Added tokens-based spread derivation: `spread = abs(1.0 - p0 - p1)`, then `bid = price - spread/2`, `ask = price + spread/2`

**Wiring** (`base_engine/base_engine.py`):
- `self.order_gateway._market_index_by_cid = self._market_index_by_cid`

### Deploy 4 (`20260317_195826`): L2 Book Walk + Whale Impact Subtraction
**The big one.** Replaces heuristic slippage with real order book data when available.

#### New function: `_vwap_from_book()` (`paper_trading.py`)
Pure function, 17 unit tests. Algorithm:
1. Parse + sort asks ascending by price
2. Phase 1: Subtract whale's consumed shares from each level (top-down)
3. Phase 2: Walk remaining book to fill copier's order
4. Return `(vwap_price, fill_fraction, slippage_vs_best_ask)` or None

#### Integration in `_place_order_locked()`:
- Feature-flagged: `PAPER_BOOK_WALK_ENABLED` (default `false`)
- When enabled + `_orderbook_tracker` available + `token_id` present:
  - Fetches L2 snapshot via `orderbook_tracker.snapshot_order_book()`
  - Computes whale_shares from `event_data["whale_size_usd"]`
  - Calls `_vwap_from_book(asks, copier_shares, whale_shares)`
  - If successful: uses VWAP as fill price, skips heuristic slippage tiers
  - If failed: falls back to existing heuristic path
- Book walk fill_fraction < 1.0 overrides random partial fill logic
- Kyle's lambda, resolution proximity, alpha decay, cross-scan impact still apply on top

#### Threading whale data:
- `bots/mirror_bot.py`: Added `whale_size_usd: float = 0.0` param to `_execute_mirror_trade()`
- `bots/mirror_bot.py`: `place_order()` now passes `event_data={"whale_size_usd": ..., "scan_start_mono": time.monotonic()}`
- `scan_start_mono` also fixes MirrorBot alpha decay (was never firing before — field was missing)
- `bots/elite_watchlist.py` line 400: Already passes `whale_size_usd=size * price`

#### Wiring:
- `base_engine/base_engine.py`: `self.paper_trading._orderbook_tracker = self.orderbook_tracker`
- `base_engine/execution/order_gateway.py`: Init `self._market_index_by_cid = {}`

#### Logs to watch:
- `paper_book_walk` — vwap, fill_frac, slippage_cents, whale_shares, ask_levels
- `paper_book_walk_failed` — graceful fallback to heuristic

### Deploy 5 (`20260317_201300`): Whale Trade Logging
**New table `whale_trades`** (migration 055) — persists every trade from the 500-wallet elite watchlist.

#### Schema:
```sql
whale_trades (
  id BIGSERIAL PRIMARY KEY,
  event_time TIMESTAMP NOT NULL DEFAULT NOW(),
  trader_address TEXT NOT NULL,
  market_id TEXT NOT NULL,       -- condition_id (0x hash)
  token_id TEXT NOT NULL,
  side TEXT NOT NULL,             -- BUY / SELL
  outcome TEXT,                   -- Yes / No / team name
  price NUMERIC(10,6) NOT NULL,
  size NUMERIC(16,4) NOT NULL,   -- shares
  size_usd NUMERIC(16,4),        -- price * size
  tx_hash TEXT,
  copied BOOLEAN DEFAULT FALSE,  -- not yet wired (future: track if we copied)
  slug TEXT
)
```

#### Implementation:
- `bots/elite_watchlist.py`: `_log_whale_trade()` async method
- Called via `asyncio.create_task()` (fire-and-forget, never blocks copy path)
- `try/except` wraps everything — DB failure = silent `debug` log
- Feature flag: `WHALE_TRADE_LOG_ENABLED` (default `true`)
- Expected volume: ~270K rows/day at current watchlist activity

#### Future uses:
- Calibrate book walk model (compare whale size vs actual slippage)
- Audit which whales are profitable vs not
- Track whale activity patterns over time
- Wire `copied=True` flag when copy attempt is made

---

## 3. CURRENT STATE (As of Deploy 20260317_201300)

### Live Metrics (last check ~00:20 UTC)
- **Open positions**: 85-91
- **RTDS**: Connected, dispatching ~5K events per scan interval
- **Elites tracked**: 500
- **Scan latency**: ~165ms
- **whale_trades table**: 0 rows (deployed at midnight UTC, low activity — will populate during US hours)
- **PAPER_BOOK_WALK_ENABLED**: `true` on VPS

### P&L Since Deploys (14:00 UTC - 00:20 UTC, ~10 hours)
| Bot | Entries | Exits | Resolutions | Exit P&L | Res P&L | Total |
|-----|---------|-------|-------------|----------|---------|-------|
| MirrorBot | 787 | 6 | 26 | +$16.86 | -$97.11 | **-$80.25** |
| WeatherBot | 76 | 0 | 240 | — | +$117.73 | **+$117.73** |
| EsportsBot | 9 | 0 | 1 | — | +$4.24 | **+$4.24** |

### MirrorBot Resolution Breakdown
- **9 wins, 17 losses** (35% WR)
- Avg win: +$103.59, avg loss: -$60.56
- Losers: avg entry 24.9c (cheap longshots), avg 574 shares (massive positions)
- Winners: avg entry 44.1c (mid-range), avg 157 shares (smaller positions)
- **Pattern**: Multiple entries on same cheap longshot market (different whales or repeat trades) -> all zero out on resolution

### All-Time P&L (cumulative, paper)
| Bot | Realized |
|-----|----------|
| MirrorBot | ~+$18,400 |
| WeatherBot | ~+$3,000 |
| EsportsBot | ~-$22 |

---

## 4. KNOWN ISSUES / NEXT SESSION PRIORITIES

### P1: Entry Price Bucket Analysis (All-Time, Identified This Session)
Today's 26-resolution sample (9W/17L, -$97) initially looked like a longshot problem. All-time data tells a different story:

| Bucket | Wins | Losses | Win Rate | Total P&L | Verdict |
|--------|------|--------|----------|-----------|---------|
| 0-15c (longshot) | 22 | 95 | 18.8% | **+$1,945** | Low WR but huge wins. Profitable. |
| 15-30c | 44 | 83 | 34.6% | **+$10,175** | **Best bucket.** Sweet spot. |
| 30-50c | 165 | 215 | 43.4% | **-$215** | Near coin-flip, slightly negative. Dead zone. |
| 50-70c | 130 | 92 | 58.6% | **+$2,187** | High WR, solid. |
| 70c+ (favorite) | 23 | 10 | 69.7% | **-$459** | Win often but losers catastrophic. |

**Real problem buckets**: 30-50c (dead zone, -$215) and 70c+ (negative despite 70% WR).
**Root cause for 70c+**: Whale buys at 70c, pushes price to 73c, MirrorBot enters at 73c. On loss, loses 73c/share instead of 70c. The post-whale price impact eats the edge. This is exactly what the book walk was built to measure.

**Potential fixes (not yet implemented, user was consulted):**
1. **Per-market entry cap** — max 2 entries per market (stop stacking on same signal)
2. **Favorite dampening** — reduce Kelly fraction for entries above 70c
3. **Dead zone filter** — reduce exposure in 30-50c range or require higher confidence
4. Book walk data (once accumulated) will reveal whether post-whale slippage is the primary driver

### P2: Whale Trade Log Verification
- Table is created and code deployed, but 0 rows at midnight UTC
- Verify data is flowing during US trading hours: `SELECT COUNT(*) FROM whale_trades;`
- Expected: thousands of rows after 8 hours of US market activity

### P2: Book Walk Verification
- `PAPER_BOOK_WALK_ENABLED=true` is live but no trades fired at midnight
- Verify logs during active hours: `grep "paper_book_walk" logs`
- Compare fill prices with/without book walk (expect more realistic = slightly worse entry prices)

### P3: `copied` Flag Not Wired
- `whale_trades.copied` defaults to `FALSE` for all rows
- Future: update to `TRUE` when `_execute_mirror_trade()` succeeds for that whale trade

### P3: 604 Unresolved Markets
- Backfill chipping away naturally (was 604 at S99, now lower)
- No action needed, just monitoring

### P5: Diagnostic Logging Cleanup
- session_factory warning, RTDS raw samples — low priority

---

## 5. COMPLETE FILE INVENTORY

### Files Modified This Session (S100)
| File | Lines | What Changed |
|------|-------|-------------|
| `bots/mirror_bot.py` | ~1,382 | `whale_size_usd` param + `event_data` with `scan_start_mono` + category fallback "unknown" + category cap fallback 40000 |
| `bots/elite_watchlist.py` | ~340 | `_log_whale_trade()` method + fire-and-forget call in `on_rtds_trade()` |
| `base_engine/execution/paper_trading.py` | ~870 | `_vwap_from_book()` function + book walk integration in `_place_order_locked()` + `_orderbook_tracker` attr |
| `base_engine/execution/order_gateway.py` | ~960 | `_market_index_by_cid` init + lookup + tokens-based spread fallback |
| `base_engine/base_engine.py` | ~1,100 | Wire `_market_index_by_cid` + `orderbook_tracker` to order_gateway/paper_trading |
| `config/settings.py` | ~710 | `PAPER_BOOK_WALK_ENABLED`, `WHALE_TRADE_LOG_ENABLED`, category cap 40000 |
| `schema/migrations/055_whale_trades.sql` | 23 | New table + indexes |
| `tests/unit/test_book_walk.py` | 140 | 17 tests for `_vwap_from_book()` |

### Core Files (MirrorBot-specific)
| File | Lines | Purpose |
|------|-------|---------|
| `bots/mirror_bot.py` | ~1,382 | Main bot: scan loop, entry/exit logic, all validation |
| `bots/elite_watchlist.py` | ~340 | O(1) elite trader lookup, RTDS trade dispatch, whale logging |
| `bots/mirror_calibration.py` | ~200 | FTS + conformal dampening (conformal DISABLED per S93) |
| `bots/mirror_adaptive_safety.py` | ~150 | Pearl-inspired adaptive constraints |
| `bots/mirror_chronos_filter.py` | ~135 | Time-based filtering |
| `bots/mirror_trade_selector.py` | ~255 | Trade selection logic |
| `base_engine/data/rtds_websocket.py` | 209 | RTDS global trade feed WebSocket |
| `base_engine/data/orderbook_tracker.py` | ~145 | L2 order book snapshots (used by book walk) |
| `config/settings.py` | ~710 | All configuration (MIRROR_* keys at lines 326-370) |
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

## 6. _execute_mirror_trade() FULL VALIDATION PIPELINE (28 checks)

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
10. **Per-category cap** — $40K (S100: raised from $10K)
11. **Opposing-side dedup** — no YES+NO on same market
12. **SELL path** (if exit) — validate position exists
13. **Market validation** — active, accepting orders
14. **Near-resolution filter** — >4h to resolve
15. **Price correction** — use CURRENT market price, not trader's fill
16. **Slippage cap** — reject if >8% drift
17. **Elite reliability** — LR must be >= 1.0
18. **Domain drift penalty** — 0.5x if trader unfamiliar with category
19. **Calibration** (FTS on, conformal off) — mild 2-3pt dampening
20. **Kelly sizing** — BotBankrollManager
21. **Gray zone dampening** — 0.25x in 5-7c/93-95c range
22. **Per-market cap** — min($500, 10% of capital)
23. **Daily cap enforcement** — cap by remaining daily USD
24. **Dust filter** — reject if <$10
25. **Place order** — paper trade with realistic fill model (S100: L2 book walk when available)
26. **Post-execution bookkeeping** — update exposure, positions, cooldowns
27. **Whale trade logging** — fire-and-forget to whale_trades table (S100)
28. **event_data** — whale_size_usd + scan_start_mono for book walk + alpha decay (S100)

---

## 7. PAPER TRADING FILL MODEL (S100 Architecture)

### Two paths (feature-flagged):

**Path A: L2 Book Walk (`PAPER_BOOK_WALK_ENABLED=true`)**
1. Fetch L2 order book snapshot via `orderbook_tracker.snapshot_order_book(token_id, condition_id)`
2. Subtract whale's consumed shares from ask levels (top-down)
3. Walk remaining asks to compute copier's VWAP fill price
4. Returns (vwap, fill_fraction, slippage)
5. On failure: falls through to Path B

**Path B: Heuristic Slippage (fallback)**
1. Tiered slippage: <$50=35bps, $50-200=50bps, $200-500=75bps, >$500=120bps
2. Boundary multiplier: books thin at extremes (2-5x at <5c or >95c)
3. Square-root market impact: Y*sigma*sqrt(Q/V) where Y=2.0, sigma=0.05
4. Random jitter: 50-150% of base slippage

**Both paths then apply:**
- Alpha decay: exp(-ln2 * latency / half_life) — requires `scan_start_mono` in event_data
- Kyle's lambda adverse selection: +7-30 bps based on market maturity
- Cross-scan cumulative impact: 2nd+ BUY on same market within 60s gets worse fills
- Resolution proximity: 1.5-3x slippage near resolution
- Fill probability: 5 multiplicative factors, reject if random > prob
- Partial fill: book walk uses deterministic fill_fraction, heuristic uses random draw
- Taker fee: 0 bps for most markets (PAPER_TAKER_FEE_BPS)

### Key data flow:
```
EliteWatchlist.on_rtds_trade()
  -> whale trade: size, price (whale's fill)
  -> whale_size_usd = size * price
  -> _execute_mirror_trade(whale_size_usd=...)
    -> place_order(event_data={"whale_size_usd": ..., "scan_start_mono": ...})
      -> order_gateway: enriches bid/ask from _market_index (tokens spread fallback)
        -> paper_trading._place_order_locked():
          1. Alpha decay from scan_start_mono
          2. Book walk OR heuristic slippage
          3. Kyle's lambda + cross-scan + resolution proximity
          4. Fill probability + partial fill
          5. Execute paper trade
```

---

## 8. CRITICAL TRAPS (DO NOT BREAK)

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
- **`orderbook_tracker.snapshot_order_book()`**: Returns `{"asks": [{"price": float, "size": float}]}`. One REST call per invocation (~100ms).

---

## 9. P&L MATH (MANDATORY)

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

## 10. COMPLETE CONFIGURATION (Live VPS Values as of S100)

### BotBankrollManager (`bankroll_manager.py` line 36)
```python
"MirrorBot": {"capital": 3000, "kelly_fraction": 0.25, "max_bet_usd": 250, "max_daily_usd": 20000}
```

### VPS `.env` Overrides (these take precedence over settings.py defaults)
```
MIRROR_MAX_CONCURRENT_POSITIONS=500        # S100: was 200
MIRROR_MAX_CATEGORY_EXPOSURE_USD=40000     # S100: was 10000
MIRROR_USE_CALIBRATION=true                # FTS on (mild 2-3pt dampening, profitable)
MIRROR_USE_CONFORMAL=false                 # S100: cleaned up (was true but dead-coded)
PAPER_BOOK_WALK_ENABLED=true               # S100: L2 book walk
WHALE_TRADE_LOG_ENABLED=true               # S100: whale trade persistence (default)
PAPER_REALISTIC_FILLS=true
SIMULATION_MODE=true
WATCHLIST_ENABLED=true
WATCHLIST_SIZE=1000
```

### settings.py MIRROR_* Keys
```python
MIRROR_MAX_DELAY_MINUTES = 30
MIRROR_MIN_CONFIDENCE = 0.55
MIRROR_MAX_PER_MARKET = 500
MIRROR_MAX_PER_MARKET_PCT = 0.10
MIRROR_MAX_CATEGORY_EXPOSURE_USD = 40000   # S100: code default raised from 10K
MIRROR_MAX_CONCURRENT_POSITIONS = 500      # S99b default
MIRROR_EXIT_ENABLED = true
MIRROR_MIN_RELIABILITY = 0.52
MIRROR_MIN_ELITE_TRADES = 100
MIRROR_USE_CALIBRATION = false             # VPS overrides to true
MIRROR_USE_CONFORMAL = false               # S100: dead-coded in mirror_bot.py line 1267
MIRROR_SKIP_LIQUIDITY_RTDS = true
MIRROR_SKIP_COORDINATOR_BUY = true
MIRROR_RTDS_FAST_PATH = true
MIRROR_STOP_LOSS_PCT = 0.15
MIRROR_MAX_HOLD_HOURS = 99999             # Disabled
MIRROR_MARKET_COOLDOWN_SECONDS = 1800
MIRROR_MIN_TRADE_USD = 10.0
MIRROR_MAX_SLIPPAGE_PCT = 0.08
MIRROR_HARD_MIN_PRICE = 0.05
MIRROR_HARD_MAX_PRICE = 0.95
MIRROR_MIN_PRICE = 0.07                   # Gray zone start
MIRROR_MAX_PRICE = 0.93                   # Gray zone start
MIRROR_EXTREME_PRICE_DAMPENER = 0.25
MIRROR_CATEGORY_BLOCKLIST = "15-minute,speed"
PAPER_BOOK_WALK_ENABLED = false            # VPS overrides to true
WHALE_TRADE_LOG_ENABLED = true
```

---

## 11. INFRASTRUCTURE

- **VPS**: Ubuntu-3 at `34.251.224.21` (16GB/4vCPU)
- **SSH**: `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21`
- **Deploy**: `bash deploy/deploy.sh` — tar, upload, extract, atomic symlink, restart, 90s health check
- **Service**: `sudo systemctl restart polymarket-ai`
- **Logs**: `journalctl -u polymarket-ai -f | grep Mirror`
- **DB**: `PGPASSWORD=polymarket_s46 psql -h localhost -p 6432 -U polymarket -d polymarket`
- **Python on VPS**: `cd /opt/polymarket-ai-v2 && source venv/bin/activate && PYTHONPATH=/opt/polymarket-ai-v2 python scripts/bot_pnl.py MirrorBot 24`
- **Rollback**: `ssh ... 'ls -lt /opt/pa2-releases/ | head -5'` then `ln -sfn /opt/pa2-releases/PREV /opt/polymarket-ai-v2`

---

## 12. FEEDBACK RULES (From Memory — Non-Negotiable)

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
- Pushback patterns: will call out dismissive responses ("so we arent going to resolve #3?"), will challenge heuristics ("100% fail rate seems like a flaw/bug"), will demand real data over guesses ("is the probability based on real data or youre just guessing?")

### P&L Math
- NEVER invert formulas for NO positions — prices are token-specific
- `cost = entry_price * size` (ALL sides)
- `uPnL = (current - entry) * size` (ALL sides)

---

## 13. SESSION CHAIN

| Session | Date | Focus | Key Outcome |
|---------|------|-------|-------------|
| **S100** | **2026-03-17** | **L2 book walk, whale trade log, 5 config fixes** | **Real order book fills, whale persistence, category cap $40K** |
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

## 14. FIRST SCAN INSTRUCTIONS FOR NEW AGENT

1. Read `CLAUDE.md` (project rules — non-negotiable)
2. Read this handoff document (you are here)
3. Read `memory/MEMORY.md` (memory index — first 200 lines only loaded due to size)
4. Ask user what they want to work on
5. Before touching ANY file: state the bug, list files, grep for dependents, read the entire file
6. One fix per commit. Preserve every function signature. No scope creep.
7. Verify with VPS logs after every deploy.

**This is Session 100. Next session should be Session 101.**

---

## 15. DEPLOYS THIS SESSION (Rollback Reference)

| # | Timestamp | Changes | Rollback |
|---|-----------|---------|----------|
| 1 | `20260317_142446` | Position cap 500, category cap $40K, empty category->"unknown" | `git revert` + redeploy |
| 2 | `20260317_143853` | Volume fallback 50K, MIRROR_USE_CONFORMAL=false in .env | `git revert` + redeploy |
| 3 | `20260317_150230` | bestBid/bestAsk from _market_index (tokens fallback) | `git revert` + redeploy |
| 4 | `20260317_195826` | L2 book walk + whale impact subtraction | `PAPER_BOOK_WALK_ENABLED=false` instant disable |
| 5 | `20260317_201300` | Whale trade logging (migration 055) | `WHALE_TRADE_LOG_ENABLED=false` instant disable |
