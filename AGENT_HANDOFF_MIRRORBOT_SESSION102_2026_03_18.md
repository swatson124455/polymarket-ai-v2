# AGENT HANDOFF — MirrorBot Session 102 (2026-03-18)
# FULL CARBON COPY — Everything needed to continue as if this conversation never ended

---

## 0. HOW TO USE THIS DOCUMENT

You are continuing MirrorBot development. This is **scope-locked to MirrorBot only** — no bleed-over to other bots unless explicitly requested. Read this entire document before writing any code. The system has 15 bots but you only touch MirrorBot files.

**Critical files to read first:**
1. `CLAUDE.md` — development rules (surgical fixes, zero collateral damage)
2. `bots/mirror_bot.py` — the bot (~1,400 lines)
3. `bots/elite_watchlist.py` — RTDS dispatch + whale logging (~604 lines)
4. `config/settings.py` — all config (~717 lines)
5. `tests/unit/test_mirror_bot_logic.py` — unit tests (~975 lines)
6. `tests/unit/test_book_walk.py` — book walk tests

**VPS access:**
```bash
SSH_KEY="~/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"
ssh -i $SSH_KEY -o ConnectTimeout=10 -o StrictHostKeyChecking=no $VPS
# Service: sudo systemctl restart polymarket-ai
# Logs: journalctl -u polymarket-ai -f
# DB: PGPASSWORD=polymarket_s46 psql -U polymarket -h localhost -p 6432 -d polymarket
```

**Deploy:**
```bash
cd /c/lockes-picks/polymarket-ai-v2
bash deploy/deploy.sh
# Rollback: bash deploy/rollback.sh
```

---

## 1. WHAT THIS SYSTEM IS

**Polymarket AI V2**: 15-bot automated trading system for Polymarket prediction markets. $20K capital deployed. Currently **paper trading** (`SIMULATION_MODE=true`). Going live = flipping one boolean.

**MirrorBot**: Copy-trades 500 elite whale traders in real-time. It does NOT analyze markets — it piggybacks on whale intelligence with Kelly-optimal sizing. Highest-performing bot: **+$20,312 realized all-time** (paper, with realistic fill model).

### How It Works
```
RTDS WebSocket (wss://ws-live-data.polymarket.com)
  → Global firehose: ALL trades on Polymarket, no auth needed
  → EliteWatchlist: O(1) set lookup — is trader in our 500-whale watchlist?
  → YES → log to whale_trades table + fire _execute_mirror_trade()
  → NO → discard

_execute_mirror_trade():
  → 23 validation checks (price bounds, caps, dampeners, Kelly sizing)
  → place_order() → paper trading engine (book walk + fill model)
  → Position tracking in positions table + trade_events ledger

Scan loop (45s interval):
  → Stop-loss exits only (15% default)
  → Housekeeping (position reconciliation, resolution backfill)
```

### Key Insight (from S102 data analysis)
The whales' edge is **picking longshots (10-30c) that hit more often than the market expects**. Asymmetric payoff: buy at 15c, win $0.85, lose $0.15. Even 25% WR is hugely profitable at 5:1 odds. Everything above 50c is marginal.

---

## 2. CURRENT LIVE CONFIG (Deploy 20260317_223335)

```
# Core
SIMULATION_MODE=true
capital=$20,000 (but BotBankrollManager uses $3,000 — see note)
kelly_fraction=0.25 (quarter-Kelly)
max_bet_usd=$250-300

# Entry filters
MIRROR_HARD_MIN_PRICE=0.10        # S102: was 0.05. Blocks penny longshots.
MIRROR_HARD_MAX_PRICE=0.95
MIRROR_MIN_CONFIDENCE=0.55
MIRROR_MIN_RELIABILITY=0.52
MIRROR_MIN_TRADE_USD=50.0         # S102: was 10. $50 minimum position size.

# Dampeners (S102: single non-stacking dampener replaced 3 stacking ones)
MIRROR_DEAD_ZONE_DAMPENER=0.50    # 30-50c: half size (breakeven zone)
MIRROR_FAVORITE_DAMPENER=0.40     # ≥70c: 40% size (whale impact eats edge)

# Caps
MIRROR_MAX_CONCURRENT_POSITIONS=500
MIRROR_MAX_PER_MARKET=400         # USD cap per market
MIRROR_MAX_ENTRIES_PER_MARKET=2   # S101: max 2 entries per market
MIRROR_MAX_CATEGORY_EXPOSURE_USD=40000
MIRROR_MAX_DAILY_EXPOSURE_PCT=0.15 → $20K daily with bankroll override

# Watchlist
WATCHLIST_ENABLED=true (on VPS, false in code default)
WATCHLIST_SIZE=500
WHALE_TRADE_LOG_ENABLED=true
```

**Note on capital**: BotBankrollManager is initialized with `capital=3000.0` on VPS (visible in startup logs). This is the Kelly sizing base. The $20K is the system-wide TOTAL_CAPITAL used for daily exposure caps.

---

## 3. P&L STATE (as of S102 deploy)

### All-Time (trade_events)
| Event | Trades | Realized P&L |
|-------|--------|-------------|
| ENTRY | 3,323 | $0 |
| EXIT | 683 | +$5,979 |
| RESOLUTION | 889 | +$14,333 |
| **Total** | | **+$20,312** |

### Open Positions
- 79 open, unrealized +$134, exposure $4,945

### whale_trades
- 33,648+ rows (S101 commit fix), 97+ copied flag set

### Price Bucket P&L (all-time, the data behind S102 decisions)
| Bucket | Trades | Win% | P&L | S102 Action |
|--------|--------|------|-----|-------------|
| <10c | 86 | 12.8% | -$2,845 | **BLOCKED** (hard floor) |
| 10-15c | 44 | 25.0% | +$3,218 | Full Kelly (sweet spot) |
| 15-20c | 39 | 30.8% | +$5,288 | Full Kelly (sweet spot) |
| 20-30c | 97 | 35.1% | +$5,729 | Full Kelly (sweet spot) |
| 30-40c | 116 | 37.1% | +$58 | Dampened 0.50x |
| 40-50c | 248 | 48.0% | +$1,452 | Dampened 0.50x |
| 50-70c | 225 | 60.0% | +$1,115 | Full Kelly |
| >70c | 34 | 74.3% | -$40 | Dampened 0.40x |

### Hold Time P&L (all-time)
| Hold Time | Trades | Win% | P&L |
|-----------|--------|------|-----|
| <6h | 8 | 37.5% | -$83 |
| 6-24h | 92 | 48.9% | -$1,286 |
| 1-3d | 229 | 46.7% | +$5,571 |
| 3-7d | 64 | 46.9% | +$6,945 |
| >7d | 28 | 64.3% | +$1,337 |

### Win Rate Trend (resolution date)
| Day | Resolutions | Win% | P&L |
|-----|------------|------|-----|
| Mar 14 | 429 | 48.0% | +$12,713 |
| Mar 15 | 173 | 42.8% | +$1,266 |
| Mar 16 | 182 | 37.4% | +$969 |
| Mar 17 | 97 | 38.1% | -$649 |

Pre-S85 P&L was fantasy (+$96/exit avg). Post-S85 is real (+$1.39/exit avg). The +$20K is real but inflated by the Mar 14 resolution backfill catching up 544 markets at once.

---

## 4. WHAT SESSION 102 CHANGED

### A. Hard price floor: 5c → 10c
`MIRROR_HARD_MIN_PRICE` in `mirror_bot.py` line ~878. Blocks <10c entries (worst bucket: -$2,845).

### B. Collapsed 3 stacking dampeners → 1
**Before** (could multiply): gray zone 0.25x × dead zone 0.50x × favorites 0.40x = 0.05x
**After** (single check, `mirror_bot.py` ~1268):
```python
_dampen = 1.0
if price >= 0.70:
    _dampen = MIRROR_FAVORITE_DAMPENER  # 0.40
elif price >= 0.30 and price < 0.50:
    _dampen = MIRROR_DEAD_ZONE_DAMPENER  # 0.50
```

### C. Min trade USD: $10 → $50
`MIRROR_MIN_TRADE_USD` in both `mirror_bot.py` (~1308) and `settings.py`. Skip any position under $50.

### D. Dead code deleted
- `bots/mirror_trade_selector.py` — d3rlpy RL, never wired
- `bots/mirror_chronos_filter.py` — Chronos-2, never wired
- `bots/mirror_adaptive_safety.py` — disabled since S94

### E. Dead code paths stripped from mirror_bot.py
- Adaptive safety: init, refresh, max_positions override, daily cap multiplier
- Conformal: fit call, interval code (was `None` since S93)

---

## 5. VALIDATION PIPELINE (S102 — 23 checks)

In `_execute_mirror_trade()` and `_can_open_position()`:

1. Hard price bounds [0.10, 0.95] — instant reject
2. Circuit breaker — pause when portfolio bleeding
3. Position cap (500)
4. Accepting orders gate
5. Near-resolution filter (4h before expiry)
6. Per-market entry cap (2) — S101
7. Category cap ($40K)
8. Opposing-side dedup (don't hold YES+NO same market)
9. Confidence ≥ 0.55
10. Reliability ≥ 0.52
11. Slippage check (8% max drift from whale's fill)
12. FTS calibration (domain + horizon aware)
13. Kelly sizing via BotBankrollManager (quarter-Kelly on $3K)
14. Single price dampener (30-50c: 0.50x, ≥70c: 0.40x)
15. Per-market USD cap ($400)
16. Daily exposure cap ($20K)
17. Min trade USD ($50) — skip dust
18. Market cooldown (1800s re-entry)
19. Category blocklist (15-minute, speed)
20. Market blocklist
21. Hot trade max seconds (900)
22. Tier 0 dedup (idempotency)
23. Paper fill model (book walk + heuristic, can reject on fill probability)

---

## 6. KEY FILES AND WHAT THEY DO

| File | Lines | Purpose |
|------|-------|---------|
| `bots/mirror_bot.py` | ~1,400 | Main bot: scan loop, entry/exit, all validation |
| `bots/elite_watchlist.py` | ~604 | Watchlist management, RTDS dispatch, whale_trades logging |
| `bots/mirror_calibration.py` | ~195 | FTS calibrator (active) + dead conformal code (strip in future) |
| `config/settings.py` | ~717 | All configuration with env var overrides |
| `base_engine/execution/paper_trading.py` | ~varies | Paper fill engine: book walk, slippage, fill probability |
| `base_engine/risk/bankroll_manager.py` | ~varies | BotBankrollManager: Kelly sizing per bot |
| `base_engine/data/database.py` | ~varies | Async DB: session factory, trade_events insert |
| `tests/unit/test_mirror_bot_logic.py` | ~975 | 64 MirrorBot unit tests |
| `tests/unit/test_book_walk.py` | ~varies | 17 book walk tests |
| `scripts/bot_pnl.py` | ~150 | Canonical P&L script |

---

## 7. CRITICAL TRAPS (DO NOT BREAK)

### Polymarket API
- `place_order()` requires `side="YES"/"NO"`. NEVER pass "BUY"/"SELL".
- MirrorBot entry price: Uses CURRENT market price from `get_market_from_index()`, NOT trader's historical fill price.
- CLOB volume=0 for most markets. Never use volume gates for MirrorBot.

### RTDS (Real-Time Data Socket)
- Envelope: Must unwrap `data.get("payload", data)` — trade data is NOT at top level.
- Dedup: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`.
- Stall detection: recv timeout + stale dispatch check (S99 fix).

### Database
- `trade_events` is P&L AUTHORITY — never read `paper_trades` for P&L.
- `asyncpg JSONB`: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
- `asyncpg timestamp`: `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`.
- `whale_trades` requires explicit `await session.commit()` (S101 fix).
- `trade_events` immutability trigger: Must `DISABLE TRIGGER` then re-enable for data cleanup.
- RESOLUTION idempotency: `ON CONFLICT` broken on partitioned tables. Uses `WHERE NOT EXISTS` instead.
- `trade_events` JSONB column is `event_data` — NOT `metadata_json`.
- `paper_trades` has NO `resolved_pnl` column (it's `resolved_at`), NO `metadata` JSONB column.
- Positions table: NO `closed_at`, NO `updated_at`, NO `bot_name`. Use `source_bot` or `bot_id`.

### Python 3.13
- `from X import Y` inside a function makes `Y` a local for the ENTIRE function. Any use before that import → `UnboundLocalError`.

### MirrorBot Internals
- `_market_meta_cache` is 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
- `_open_positions` clears on restart; re-enters via `_restore_state_on_startup()`.
- BotBankrollManager handles SIZING; risk_manager handles LIMITS. Both must pass.
- `risk_manager.calculate_position_size()` is DEPRECATED.
- Resolution backfill excludes SELL trades — SELL P&L computed by paper engine at exit time.
- `traded_markets.bot_names` is TEXT (not array) — use `LIKE '%BotName%'`.

### Testing
- S101 added settings that MagicMock can't handle. Tests that patch settings need:
  ```python
  ms.MIRROR_MAX_ENTRIES_PER_MARKET = 10
  ms.MIRROR_FAVORITE_PRICE_THRESHOLD = 0.70
  ms.MIRROR_FAVORITE_DAMPENER = 0.40
  ms.MIRROR_DEAD_ZONE_LOW = 0.30
  ms.MIRROR_DEAD_ZONE_HIGH = 0.50
  ms.MIRROR_DEAD_ZONE_DAMPENER = 0.50
  ```
- 81 tests must pass (64 mirror + 17 book walk).

---

## 8. OVER-ENGINEERING RETROSPECTIVE (S85-S102)

### The Pattern That Keeps Repeating
Build sophistication → discover it doesn't work → disable → add simpler things → accumulate again.

### Specific Failures
1. **Conformal prediction (S89-S93)**: 4 sessions, 3 rewrites, disabled. Wrong for binary outcomes. Dead code still in `mirror_calibration.py`.
2. **Cap whiplash**: Position cap 200→400→1000→500. Category cap $2.4K→$4K→$10K→$40K. None data-driven until S102.
3. **Stacking dampeners (S99-S101)**: 3 independent dampeners multiplied to zero. Fixed in S102.
4. **Fill model**: 8 penalty layers for paper trading. Possibly over-engineered but useful for live.

### What Worked
- S94: Latency 2967ms→11.9ms (real measurable win)
- S96: Architectural teardown (-218 lines, deleted consensus scan)
- S102: Data-driven bucket analysis → hard floor + single dampener

### Rule for Future Work
**Check the data before adding complexity.** If you can't show a P&L bucket or win-rate analysis justifying a change, don't make it. Simple > clever.

---

## 9. OPEN ITEMS (prioritized)

| Priority | Item | Notes |
|----------|------|-------|
| **P2** | Monitor S102 impact 48h | Did "size zero" spam decrease? Did sweet-spot (10-30c) entries increase? Are $50+ positions landing? |
| **P3** | Clean `mirror_calibration.py` | FTS calibrator is active and useful (logs `mirror_calibrated`). Conformal methods are dead code. Strip conformal, keep FTS. |
| **P3** | Stabilize caps from data | Position/category/daily caps should be justified by analysis, not vibes. |
| **P3** | NO vs YES asymmetry | 72% vs 39% WR on NO/YES sides. Confirmed but not acted on. Monitor. |
| **P4** | Hold-time filtering | <24h positions net -$1,370. Consider minimum hold time or entry-time filter. |
| **P4** | whale_trades retention | ~270K rows/day. May need 30-day purge cron. |
| **P5** | 1,987 unresolved markets | Growing naturally. Backfill resolving. Monitor only. |
| **P5** | Kalshi cross-platform arb | Deferred. 8-16h effort. |

---

## 10. DIAGNOSTIC QUERIES

```sql
-- P&L all-time
SELECT event_type, COUNT(*) as trades,
  ROUND(SUM(COALESCE(realized_pnl,0))::numeric,2) as pnl
FROM trade_events WHERE bot_name='MirrorBot'
GROUP BY event_type ORDER BY event_type;

-- Open positions
SELECT COUNT(*) as open, ROUND(SUM(COALESCE(unrealized_pnl,0))::numeric,2) as unrealized
FROM positions WHERE LOWER(status)='open' AND source_bot ILIKE '%Mirror%';

-- Price bucket analysis (all-time resolutions)
WITH res AS (
  SELECT r.market_id, r.side, r.realized_pnl,
    (SELECT MIN(e2.price) FROM trade_events e2
     WHERE e2.bot_name='MirrorBot' AND e2.event_type='ENTRY'
     AND e2.market_id=r.market_id AND e2.side=r.side) as entry_price
  FROM trade_events r WHERE r.bot_name='MirrorBot' AND r.event_type='RESOLUTION'
)
SELECT CASE
  WHEN entry_price < 0.10 THEN '<10c'
  WHEN entry_price < 0.30 THEN '10-30c'
  WHEN entry_price < 0.50 THEN '30-50c'
  WHEN entry_price < 0.70 THEN '50-70c'
  ELSE '>70c'
END as bucket, COUNT(*) as trades,
  COUNT(*) FILTER (WHERE realized_pnl > 0) as wins,
  ROUND(SUM(realized_pnl)::numeric,2) as pnl
FROM res GROUP BY bucket ORDER BY bucket;

-- whale_trades stats
SELECT COUNT(*) as total, COUNT(*) FILTER (WHERE copied) as copied,
  MIN(event_time)::text as first, MAX(event_time)::text as last
FROM whale_trades;

-- 24h trade events
SELECT event_type, COUNT(*) as c
FROM trade_events WHERE bot_name='MirrorBot' AND event_time > NOW() - INTERVAL '24 hours'
GROUP BY event_type ORDER BY event_type;

-- Win rate by day (resolution date)
SELECT event_time::date as day, COUNT(*) as res,
  COUNT(*) FILTER (WHERE realized_pnl > 0) as wins,
  ROUND(100.0 * COUNT(*) FILTER (WHERE realized_pnl > 0) / NULLIF(COUNT(*),0), 1) as win_pct,
  ROUND(SUM(realized_pnl)::numeric,2) as pnl
FROM trade_events WHERE bot_name='MirrorBot' AND event_type='RESOLUTION'
  AND event_time > NOW() - INTERVAL '7 days'
GROUP BY day ORDER BY day;
```

---

## 11. USER PREFERENCES

- **Hates over-engineering.** Explicitly said "tear it down" in S96.
- **Wants data-driven decisions.** S102 was the first session to justify filters with actual P&L buckets.
- **Expects direct answers.** No fluff, no hedging, no "let me explain what this means."
- **$50 minimum position size** — explicitly requested. Don't lower it.
- **Scope lock** — MirrorBot only. No touching other bots unless manually demanded.
- **CLAUDE.md rules are non-negotiable** — surgical fixes, zero collateral damage, one fix per commit.

---

## 12. DEPLOY HISTORY (recent)

| Deploy | Session | Key Changes |
|--------|---------|-------------|
| 20260317_223335 | S102 | Hard floor 10c, single dampener, $50 min, dead code purge |
| 20260317_211034 | S101 | Bucket filters, whale trade commit fix, copied flag |
| 20260317_193541 | S100 | L2 book walk, whale_trades table, category cap $40K |
| 20260317_152832 | S99 | Price bounds, circuit breaker, RTDS stall fix, pos cap 500 |
| 20260316_162845 | S96 | RTDS-only architecture, consensus scan deleted |
