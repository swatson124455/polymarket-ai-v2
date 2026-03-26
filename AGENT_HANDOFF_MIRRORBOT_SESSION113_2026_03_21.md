# AGENT HANDOFF — MirrorBot Session 113 (2026-03-21)

**Scope**: MirrorBot only. No cross-bot bleed. Single-bot session.
**Prior sessions**: S109 (P&L fixes), S110/S111 (multi-factor confidence + archive + dampeners), S112 (CLOB fallback + F3 conviction + calibration disable), S113 (this session)
**Status**: ALL S113 CHANGES DEPLOYED AND VERIFIED LIVE ON VPS

---

## What Was Done This Session (S113)

### Two Major Workstreams

**Workstream A (Early Session)**: Reviewed all 7 pending items (P1-P7) from S112, implemented P1-P5 + P7. Discovered F1 and F3 were completely dead.

**Workstream B (Late Session)**: Root-caused F1 and F3 failures, designed + implemented + deployed fixes.

---

## Workstream A: P1-P7 Review + Fixes

### P1: Confidence-Bucket SQL Query (VPS diagnostic — no code change)
**Query results (2026-03-21)**:
- ENTRY histogram (219 trades since 3/20): 35% at 0.50, range 0.49-0.63
- RESOLUTION data: Only 0.70+ bucket resolved (20 trades, 35% WR, +$371.80, avg +$20.66)
- Today's actual exposure: $3,659 ENTRY - $418 EXIT = $3,241 (under $10k cap)
- **Verdict**: Too early for definitive results. Recheck when lower buckets resolve.

### P2: Multi-Whale Consensus Counter — `bots/mirror_bot.py`
- Added `_whale_consensus: Dict[str, int]` dict tracking `"market_id:side" → whale_count`
- Same-side dedup (lines 1138-1150) now records additional whale in counter AND `_open_positions.traders` set before blocking
- Counter resets daily in `_check_daily_reset()`
- **Zero behavioral change** — research-only tracking for future Factor 4

### P3: Resolution Batch Size — `config/settings.py`
- `RESOLUTION_QUEUE_BATCH_SIZE` default: 100 → **200**
- Halves backlog clearance time

### P4: `_infer_category()` Keyword Expansion — `base_engine/data/data_ingestion.py`
- **Crypto**: +17 keywords (Cardano, Dogecoin, Ripple, Polkadot, Avalanche, Polygon, Chainlink, Uniswap, Aave, staking, altcoin, memecoin, web3)
- **Weather**: +10 keywords (humidity, wind speed, heat wave, tornado, wildfire, flood, highest/lowest temp)
- **NEW healthcare category**: 20 keywords (FDA, clinical trial, vaccine, pharmaceutical, Pfizer, Moderna, CDC, WHO, disease, medication, therapy)
- **NEW legal category**: 18 keywords (lawsuit, settlement, court ruling, indictment, jury, antitrust, SEC, FTC, DOJ)
- Total categories: 8 → **10**. Total keywords: ~220 → ~285

### P5: Persist Metadata in `event_data` — `bots/mirror_bot.py`
- BUY `place_order()` now passes `event_data` dict with:
  - `category`, `source`, `whale_trade_usd`, `trader`
  - Confidence components: `conf_base`, `conf_price_adj`, `conf_conv_adj`, `conf_upstream`
  - `consensus` count from P2
- Previously `event_data={}` for all MirrorBot entries

### P7: Daily Cap Inflation Fix — `bots/mirror_bot.py`
- `_reap_resolved_positions()` now decrements `_daily_exposure` and `_category_exposure` for each reaped position
- Previously, resolved positions stayed in the daily counter forever (no EXIT event created for resolved markets)
- Logs freed amount: `mirror_reap_resolved: removed N stale positions, freed $X.XX daily exposure`

### P6: FTS Calibration Re-enable — DEFERRED to ~2026-04-03
Calendar item. No action this session.

---

## Workstream B: Factor 1 + Factor 3 Root-Cause Fix

### Discovery: Both F1 and F3 Were 100% Dead

**Live log evidence (pre-fix)**:
```
mirror_multifactor base=0.5 cat_n=0 cat_wr=0.5 category=crypto conv_adj=0.0 final=0.494
mirror_multifactor base=0.5 cat_n=0 cat_wr=0.5 category=sports conv_adj=0.0 final=0.531
```
- `cat_n=0` for 100% of trades → F1 always `_base=0.50`
- `conv_adj=0.0` for 100% of trades → F3 never fires
- Only F2 (price edge) was active, producing the [0.49-0.63] range

### F1 Root Cause: Tracker Query Drops Non-Top-1000 Markets

**Query** (`database.py:2189`): `FROM trades t JOIN markets m ON t.market_id = m.id`
- `markets` table only has top-1000 markets from ingestion
- Whales trade outside top-1000 → JOIN drops those trades → `cat_n=0`
- The CLOB fallback (S112) categorized the CURRENT trade, but the tracker's HISTORICAL data had no category match

### F1 Fix: market_categories Table + LEFT JOIN

#### Migration 057 — `schema/migrations/057_market_categories.sql`
```sql
CREATE TABLE IF NOT EXISTS market_categories (
    condition_id    TEXT PRIMARY KEY,
    category        TEXT NOT NULL DEFAULT 'unknown',
    question        TEXT,
    yes_token_id    TEXT,
    no_token_id     TEXT,
    resolved        BOOLEAN DEFAULT FALSE,
    resolution      TEXT,              -- 'YES' or 'NO'
    created_at      TIMESTAMP DEFAULT NOW()
);
```
Lightweight supplement to `markets` — stores category + resolution + token IDs for markets outside top-1000.

#### CLOB Fallback Persist — `bots/mirror_bot.py`
When CLOB fallback resolves a category in `_get_market_meta()`, also:
1. Extracts `yes_token_id`, `no_token_id` from CLOB `tokens` array
2. Extracts `resolved` (from `closed` field) and `resolution` (from `tokens[].winner`)
3. Fire-and-forget `asyncio.create_task(self._persist_market_category(...))` → upserts to `market_categories`

New method `_persist_market_category()` calls `db.upsert_market_category()`.

#### Tracker Query Modified — `base_engine/data/database.py`
`get_user_resolution_counts_by_category()` changed from:
```sql
FROM trades t
JOIN markets m ON t.market_id = m.id
WHERE m.resolved = TRUE AND m.resolution IN ('YES', 'NO')
```
To:
```sql
FROM trades t
LEFT JOIN markets m ON t.market_id = m.id
LEFT JOIN market_categories mc ON t.market_id = mc.condition_id
WHERE (m.resolved = TRUE OR mc.resolved = TRUE)
  AND COALESCE(m.resolution, mc.resolution) IN ('YES', 'NO')
GROUP BY t.user_address, LOWER(COALESCE(m.category, mc.category, 'unknown'))
```
Token ID resolution also uses COALESCE: `t.token_id = m.yes_token_id OR t.token_id = mc.yes_token_id`

#### New DB Methods — `base_engine/data/database.py`
- `upsert_market_category(condition_id, category, question, yes_token_id, no_token_id, resolved, resolution)` — ON CONFLICT DO UPDATE with COALESCE to preserve existing data
- `get_user_trade_counts(addresses, lookback_days=90)` — counts trades per whale from `trades` table

#### Backfill Script — `scripts/backfill_market_categories.py`
- Finds all `market_id` in `trades` NOT in `markets` AND NOT in `market_categories`
- Batch-fetches from CLOB API with rate limiting
- Infers category via `_infer_category(question)`
- Extracts token IDs + resolution status
- **Run on VPS**: `sudo /opt/polymarket-ai-v2/venv/bin/python3 scripts/backfill_market_categories.py --limit 2000`

### F3 Root Cause: Data API Doesn't Return Trade Counts

**Data API leaderboard** (`elite_watchlist.py:117-132`) returns `{pnl, vol, rank, userName}` — **no `totalTrades` field**.
- `num_trades` defaults to 0 for all whales (line 189)
- F3 guard `if _whale_vol > 0 and _whale_n > 0` fails at `_whale_n=0`

### F3 Fix: Supplement from DB

#### Watchlist Refresh — `bots/elite_watchlist.py`
After Data API fetch, before inactivity decay:
```python
# S113 F3: Supplement num_trades from DB
_counts = await self._db.get_user_trade_counts(_addrs_for_counts, lookback_days=90)
for row in _counts:
    new_data[_addr_l]["num_trades"] = row["num_trades"]
```
Runs once per watchlist refresh (~6h), single aggregate query.

---

## Deploy Results (2026-03-21)

### Permission Issue Found + Fixed
- Migration 057 ran as postgres user → `polymarket` user couldn't read `market_categories`
- Fixed: `GRANT SELECT, INSERT, UPDATE ON market_categories TO polymarket`
- Required second restart to pick up

### Verified Live

| Metric | Before S113 | After S113 |
|--------|-------------|------------|
| F1 tracker `n_category_entries` | 0 (permission error) then 8,629 | **8,705** (+76 from market_categories) |
| F3 `num_trades` supplemented | 0 whales | **491 / 500 whales** |
| `market_categories` table | Didn't exist | **149 rows**, 24 resolved |
| Uncovered markets in `trades` | Unknown | **0** (100% coverage) |
| CLOB persist | Not wired | **22 resolves** in first 3 minutes |
| `_infer_category()` categories | 8 | **10** (+healthcare, +legal) |

### Why No Multifactor Impact Yet
`open_positions=400` vs cap `MIRROR_MAX_CONCURRENT_POSITIONS=200` — pre-existing overload from position restore. All BUY entries blocked by `_can_open_position()` before reaching confidence code. Trades will flow as positions resolve and count drops below 200.

---

## Files Modified This Session (S113)

| File | Change | Deployed |
|------|--------|----------|
| `bots/mirror_bot.py` | P2 consensus counter, P5 event_data, P7 reap decrement, F1 CLOB persist + `_persist_market_category()` | Yes |
| `bots/elite_watchlist.py` | F3 num_trades supplement from DB after API fetch | Yes |
| `base_engine/data/database.py` | F1 LEFT JOIN tracker query, `upsert_market_category()`, `get_user_trade_counts()` | Yes |
| `base_engine/data/data_ingestion.py` | P4 expanded keywords + healthcare/legal categories | Yes |
| `config/settings.py` | P3 `RESOLUTION_QUEUE_BATCH_SIZE` 100→200 | Yes |
| `schema/migrations/057_market_categories.sql` | New table + indexes | Executed on VPS |
| `scripts/backfill_market_categories.py` | New backfill script | Deployed + run |

### Files NOT Modified (read-only reference):
- `base_engine/learning/elite_reliability.py` — no changes needed (consumes `_cat_cache` from DB query)
- `tests/unit/test_mirror_bot_logic.py` — 65 tests pass, no changes this session (S110 mean() mock still applies)
- `tests/unit/test_elite_watchlist.py` — 23 tests pass, no changes

---

## Architecture Reference (MirrorBot Confidence Pipeline — Updated S113)

```
RTDS Trade Event (wss://ws-live-data.polymarket.com)
  ↓
elite_watchlist.on_rtds_trade()
  ├── Fast-reject: proxyWallet not in watchlist → skip
  ├── Dedup: transactionHash or composite key
  ├── Wash detection: 3+ round-trips in 24h → block
  ├── Position cap: _can_open_position()
  ├── Base confidence: 0.55 + efficiency_bonus (capped 0.70)  ← UPSTREAM (unchanged)
  ├── ★ S112: Compute whale_trade_usd = size × price from RTDS payload
  └── _execute_mirror_trade(category=None, source="rtds", whale_trade_usd=X)
        ↓
mirror_bot._execute_mirror_trade()
  ├── Tier 0: blocklist, cooldown (in-memory, <0.01ms)
  ├── Category resolve: _get_market_meta(condition_id) → category
  │     ├── DB lookup: WHERE condition_id = :mid OR id::text = :mid
  │     ├── ★ S112: CLOB API fallback if DB miss → _infer_category(question)
  │     └── ★ S113: Persist CLOB result to market_categories (fire-and-forget)
  ├── Category blocklist
  ├── Position cap: _can_open_position()
  ├── Opposing-side dedup: blocks if holding opposite side
  ├── Same-side dedup: blocks re-entry on same market+side (S109)
  │     └── ★ S113: Records consensus count + adds whale to traders set
  ├── Reliability multiplier: likelihood_ratio() → lr_mult
  │
  ├── ★ S110: Factor 1 — Category-Specific Bayesian Base
  │     _cat_wr = tracker.mean(trader, side, category)
  │     _cat_n = tracker.category_trade_count(trader, category)
  │     _base = 0.50 + shrinkage(cat_n, 20) × (cat_wr - 0.50)
  │     if cat_n < 10: _base = min(_base, 0.52)
  │     ★ S113 FIX: tracker now LEFT JOINs market_categories → cat_n > 0
  │
  ├── ★ S110: Factor 2 — Price-Implied Edge
  │     contrarian (YES<0.45, NO>0.55): _price_adj = price_dev × 0.15
  │     consensus: _price_adj = -(price_dev × 0.15 × 0.3)
  │
  ├── ★ S112: Factor 3 — Trade Size Conviction
  │     _size_ratio = whale_trade_usd / (whale_vol / whale_num_trades)
  │     >2.0 → _conv_adj = +0.04 (big bet for this whale)
  │     <0.3 → _conv_adj = -0.03 (exploratory)
  │     ★ S113 FIX: num_trades now supplemented from DB trades table
  │
  ├── Final: confidence = clamp(0.35, 0.75, _base + _price_adj + _conv_adj)
  │
  ├── [DISABLED] FTS Calibration: sigmoid(logit(confidence) / T)
  │     MIRROR_USE_CALIBRATION=false on VPS. Re-enable ~2026-04-03.
  ├── MIN_CONFIDENCE gate (0.45): reject if below
  │
  ├── ★ S113: Build event_data dict (category, source, confidence components, consensus)
  ├── Kelly sizing: kelly_full = (conf × b - q) / b
  ├── Extreme-price dampener: gray zone
  ├── S110: Dead zone dampener: price 0.30-0.50 → size × 0.50
  ├── S110: Favorite dampener: price > 0.70 → size × 0.40
  ├── Per-market cap: 5% of capital
  ├── Daily cap: remaining_daily_usd / price
  │     ★ S113: _reap_resolved_positions() now decrements daily exposure
  └── place_order(side=YES/NO, event_data=_event_data)
```

---

## Key Data Structures

### `_reliability_tracker._cat_cache` (in-memory, refreshed every scan ~45s)
- Type: `Dict[Tuple[str, str], Dict]` — `(address.lower(), category.lower())` → Beta params
- Source: `trades` table LEFT JOIN `markets` + LEFT JOIN `market_categories` (S113)
- Size: **8,705 entries** (up from 8,629 pre-S113, +76 from market_categories)
- Contains: `alpha_yes, beta_yes, alpha_no, beta_no, yes_total, no_total`
- Used by: `mean()`, `category_trade_count()`, `likelihood_ratio()`

### `_watchlist_data` (in-memory, refreshed ~6h)
- Type: `Dict[str, Dict]` — `addr_lower` → `{address, pnl, vol, efficiency, num_trades, rank, userName}`
- ★ S112: `num_trades` field added for F3
- ★ S113: `num_trades` supplemented from DB `trades` table (491/500 whales have real counts)
- Source: Data API leaderboard + DB trade counts
- Used by: F3 conviction signal (`vol / num_trades` = avg trade size)

### `_market_meta_cache` (in-memory, TTL-based)
- Type: `Dict[str, Tuple[str, str, float]]` — `market_id` → `(category, time_to_res, expiry_monotonic)`
- **CRITICAL**: 3-tuple. NEVER expand.
- Source: `markets` table via DB query → CLOB API fallback (S112) → persist to `market_categories` (S113)

### `market_categories` table (DB, created S113, migration 057)
- Type: `condition_id TEXT PK` → `{category, question, yes_token_id, no_token_id, resolved, resolution, created_at}`
- **149 rows** after initial backfill, growing as CLOB fallback fires
- Indexed: `category`, `resolved WHERE TRUE`
- Used by: tracker LEFT JOIN for F1, ongoing CLOB persist

### `_whale_consensus` (in-memory, resets daily, added S113)
- Type: `Dict[str, int]` — `"market_id:SIDE"` → count of whales that attempted
- Populated when same-side dedup blocks: increment count + add whale to `_open_positions.traders`
- Available in `event_data.consensus` for retroactive analysis
- Future: basis for Factor 4 (multi-whale consensus boost)

### `trade_events.event_data` JSONB (S113 enrichment)
- MirrorBot ENTRY events now contain:
  ```json
  {
    "category": "sports",
    "source": "rtds",
    "whale_trade_usd": 1523.50,
    "conf_base": 0.623,
    "conf_price_adj": 0.034,
    "conf_conv_adj": 0.04,
    "conf_upstream": 0.553,
    "trader": "0x204f72f3",
    "consensus": 2
  }
  ```

---

## VPS Deploy Pattern (IMPORTANT)

The VPS does NOT use git. Files are deployed via SCP:
```bash
KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem
VPS=ubuntu@34.251.224.21

# Copy file to VPS
scp -i $KEY <local_file> $VPS:/tmp/<file>
# Install + restart
ssh -i $KEY $VPS "sudo cp /tmp/<file> /opt/polymarket-ai-v2/<path> && sudo systemctl restart polymarket-ai"
```

**CRITICAL**: New tables need `GRANT` to `polymarket` user:
```bash
ssh -i $KEY $VPS "sudo -u postgres psql -d polymarket -c 'GRANT SELECT, INSERT, UPDATE ON <table> TO polymarket;'"
```

**WARNING**: `deploy.sh` and `systemctl restart` may overwrite `.env` changes. For persistent config changes, verify after every restart.

---

## Git State (local, NOT committed)

```
Modified (unstaged):
  base_engine/data/data_ingestion.py         (+20 lines: P4 keywords + healthcare/legal)
  base_engine/data/database.py               (+259 lines: LEFT JOIN query, upsert_market_category, get_user_trade_counts)
  base_engine/learning/elite_reliability.py   (+6 lines: S110 log level bumps)
  bots/elite_watchlist.py                     (+24 lines: S112 num_trades + S113 DB supplement)
  bots/mirror_bot.py                          (+202 lines: S110-S113 all changes)
  config/settings.py                          (+2 lines: P3 batch size + prior)
  tests/unit/test_mirror_bot_logic.py         (+4 lines: S110 mean() mock)

New (untracked):
  schema/migrations/057_market_categories.sql
  scripts/backfill_market_categories.py
```

MirrorBot-specific changes: `bots/mirror_bot.py`, `bots/elite_watchlist.py`, `base_engine/data/database.py`, `base_engine/data/data_ingestion.py`, `config/settings.py`

---

## Config Reference (Live VPS Values)

```
# MirrorBot-specific
MIRROR_MIN_CONFIDENCE=0.45
MIRROR_MIN_RELIABILITY=0.52
MIRROR_USE_CALIBRATION=false       ← S112: disabled (re-enable ~2026-04-03)
MIRROR_MAX_POSITIONS=200
MIRROR_MAX_CONCURRENT_POSITIONS=200
MIRROR_MAX_PER_MARKET=400
MIRROR_DEAD_ZONE_LOW=0.30
MIRROR_DEAD_ZONE_HIGH=0.50
MIRROR_DEAD_ZONE_DAMPENER=0.50
MIRROR_FAVORITE_PRICE_THRESHOLD=0.70
MIRROR_FAVORITE_DAMPENER=0.40
WATCHLIST_ENABLED=true
WATCHLIST_SIZE=1000

# System-wide
SIMULATION_MODE=true (paper trading)
capital=$20000, max_bet=$300, max_daily=$10000, kelly=0.25
RESOLUTION_QUEUE_BATCH_SIZE=200    ← S113: was 100
DATABASE_URL=postgresql://polymarket:polymarket_s46@localhost:6432/polymarket
```

---

## Critical Traps (MirrorBot-Specific, DO NOT BREAK)

1. **`_market_meta_cache`**: 3-tuple `(cat, ttr, expiry)`. NEVER expand.
2. **Entry price**: Uses CURRENT market price from `get_market_from_index()`, NOT trader's fill.
3. **`_open_positions` on restart**: Clears in-memory; re-enters by EOD UTC.
4. **CLOB volume=0**: Never use volume gates for MirrorBot.
5. **RTDS envelope**: Must unwrap `data.get("payload", data)`.
6. **RTDS dedup**: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`.
7. **Same-side dedup (S109)**: Blocks re-entry on same market+side before confidence code. S113 now tracks consensus + adds whale to traders set.
8. **trade_events immutability trigger**: Must DISABLE/re-ENABLE for data cleanup.
9. **`_get_market_meta` lookup**: `WHERE condition_id = :mid OR id::text = :mid` (S110 fix). Falls back to CLOB API (S112). Persists to `market_categories` (S113).
10. **Calibration T=2.0**: Fitted on old [0.55-0.58] range. Currently disabled. Re-fit needed before re-enabling.
11. **`category_trade_count` key format**: `(address.lower(), category.lower())` — case-sensitive.
12. **deploy.sh may overwrite .env**: Config changes via sed on VPS WILL BE LOST on redeploy.
13. **`_infer_category()` is keyword-based**: Returns "unknown" for markets that don't match. Now 10 categories with ~285 keywords. Located in `base_engine/data/data_ingestion.py`.
14. **CLOB API variable name**: Uses `_hc` (not `h`) in `_get_market_meta()` to avoid shadowing.
15. **`whale_trade_usd` default=0.0**: All callers except `elite_watchlist.on_rtds_trade()` pass default. F3 produces `_conv_adj=0.0` when `whale_trade_usd=0`.
16. **New table permissions**: `market_categories` required explicit `GRANT ... TO polymarket`. Always GRANT after creating tables.
17. **Tracker LEFT JOIN**: `get_user_resolution_counts_by_category()` now uses LEFT JOIN to both `markets` AND `market_categories`. If `market_categories` table is dropped, query still works (mc columns are all NULL, COALESCE falls through to markets).
18. **`get_user_trade_counts()` uses `LOWER(user_address)`**: Case-insensitive matching to watchlist addresses.
19. **Backfill script is idempotent**: `ON CONFLICT DO UPDATE` — safe to re-run.
20. **`_persist_market_category` is fire-and-forget**: Uses `asyncio.create_task()`. DB errors logged at debug level, don't block trade pipeline.
21. **`event_data` enrichment**: Only on BUY orders (line ~1443). EXIT/SELL orders don't pass event_data.
22. **Position cap 400/200**: Pre-existing overload from position restore. Blocks all BUY entries. Will resolve as positions close/resolve.

---

## Pending Actions (For Next Session)

### P1: Validate F1 + F3 Live Performance (HIGHEST PRIORITY)
Once position count drops below 200 and trades start flowing:
```sql
-- Check cat_n > 0 trades
SELECT ROUND(confidence::numeric, 2) as conf, COUNT(*) as cnt
FROM trade_events WHERE bot_name='MirrorBot' AND event_type='ENTRY'
  AND event_time >= '2026-03-22'
  AND event_data->>'conf_base' IS NOT NULL
  AND (event_data->>'conf_base')::float != 0.50
GROUP BY 1 ORDER BY 1;
```

```bash
# Live check
journalctl -u polymarket-ai -f | grep 'mirror_multifactor' | grep -v 'cat_n=0'
journalctl -u polymarket-ai -f | grep 'mirror_multifactor' | grep -v 'conv_adj=0'
```

**Goal**: `cat_n > 0` for trades where category is known. `conv_adj != 0` for RTDS trades with whale in watchlist.

### P2: Factor 4 — Multi-Whale Consensus (BLOCKED)
- Same-side dedup prevents counting (S109), but S113 now records consensus in `_whale_consensus` dict and `event_data.consensus`
- After enough data, analyze: do multi-whale markets outperform?
- If yes, implement boost: `+0.03 × min(consensus_count - 1, 4)` applied after F1+F2+F3
- **Risk**: Dedup restructure needed to apply boost to FIRST entry. Currently, second whale is recorded but first whale's trade is already placed.

### P3: Position Cap Overload (400/200)
- 400 open positions restored from DB, but cap is 200
- All BUY entries blocked by `_can_open_position()`
- Will resolve naturally as positions resolve/exit
- If urgent: run `_reap_resolved_positions()` manually or increase cap temporarily

### P4: Resolution Backfill → market_categories Sync
- `market_categories` has 24 resolved out of 149 rows
- As resolution backfill processes markets, the resolved status in `market_categories` may lag
- CLOB fallback persist (S113) populates resolved status for NEW trades
- For HISTORICAL market_categories rows, re-running backfill script will update resolution status

### P5: FTS Calibration Re-enable (~2026-04-03)
After 2 weeks of data at new confidence range [0.45-0.75], re-fit temperature T:
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
sudo sed -i 's/MIRROR_USE_CALIBRATION=false/MIRROR_USE_CALIBRATION=true/' /opt/polymarket-ai-v2/.env
sudo systemctl restart polymarket-ai
```

### P6: Confidence-Bucket P&L Analysis (~2026-03-25)
After 3-5 days of live data with F1+F3 active:
```sql
SELECT
  CASE WHEN confidence < 0.50 THEN '<0.50'
       WHEN confidence < 0.55 THEN '0.50-0.55'
       WHEN confidence < 0.60 THEN '0.55-0.60'
       WHEN confidence < 0.65 THEN '0.60-0.65'
       WHEN confidence < 0.70 THEN '0.65-0.70'
       ELSE '0.70+' END as bucket,
  COUNT(*) as trades,
  SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
  ROUND(100.0 * SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) / NULLIF(COUNT(*),0), 1) as wr_pct,
  ROUND(SUM(realized_pnl)::numeric, 2) as total_pnl,
  ROUND(AVG(realized_pnl)::numeric, 2) as avg_pnl
FROM trade_events
WHERE bot_name='MirrorBot' AND event_type='RESOLUTION'
  AND event_time >= '2026-03-22'
GROUP BY 1 ORDER BY 1;
```
**Goal**: Higher confidence buckets → higher win rates AND avg P&L.

### P7: `_infer_category()` Coverage Monitoring
Check CLOB fallback logs for "unknown" categorization rate:
```bash
journalctl -u polymarket-ai --since '24 hours ago' | grep 'mirror_clob_category_resolve' | grep 'unknown' | wc -l
```
If > 10%, expand keywords for most common unknowns.

---

## P&L Reference

### S112 First 10 Positions (3-factor formula):
**4W / 6L = +$667.39 net** — positive despite 40% WR (contrarian wins at low entry prices)

### Cumulative MirrorBot P&L (post-archive):
```
NO:  631 trades, 43.1% WR, $7,952 P&L, $12.70 avg
YES: 378 trades, 41.8% WR, $6,854 P&L, $18.23 avg
```

### P&L Formula (MANDATORY):
- `cost = entry_price × size` (ALL sides)
- `uPnL = (current - entry) × size` (ALL sides)
- WIN: `pnl = (1.0 - entry) × size`
- LOSS: `pnl = -(entry × size)`
- **NEVER invert for NO positions** — prices are token-specific
- Canonical script: `python scripts/bot_pnl.py MirrorBot hours`

---

## Rollback Plan

| Change | Rollback |
|--------|----------|
| F1 market_categories table | `DROP TABLE market_categories;` — tracker query falls through to markets-only (pre-S113 behavior, cat_n=0) |
| F1 LEFT JOIN query | Revert `database.py` to `JOIN markets m ON t.market_id = m.id` |
| F1 CLOB persist | Revert `mirror_bot.py` — remove `_persist_market_category()` and CLOB persist block |
| F3 num_trades supplement | Revert `elite_watchlist.py` — remove DB supplement block |
| P2 consensus counter | Revert `mirror_bot.py` — remove `_whale_consensus` dict and tracking |
| P4 keywords | Revert `data_ingestion.py` — remove healthcare/legal categories |
| P5 event_data | Revert `mirror_bot.py` — remove `_event_data` dict and `event_data=` param |
| P7 reap decrement | Revert `mirror_bot.py` — remove exposure decrement in `_reap_resolved_positions()` |
| P3 batch size | `RESOLUTION_QUEUE_BATCH_SIZE=100` in settings.py or env var |

---

## Session Learnings

1. **Always check table permissions after CREATE TABLE**. The `polymarket` DB user needs explicit GRANT — new tables default to postgres-only.
2. **F1 was starved TWO levels deep**: S110 fixed the formula, S112 fixed the category input to the formula, S113 fixed the historical data that feeds the category cache. Each fix revealed the next layer.
3. **Leaderboard APIs are unreliable for derived metrics**. Data API doesn't return trade counts. Always verify what fields the API actually returns before building features on them.
4. **Fire-and-forget for non-critical persistence is OK**. `_persist_market_category` uses `asyncio.create_task()` — DB write failures are logged but don't block the trade pipeline. This is acceptable because the data eventually arrives via backfill.
5. **Position cap overload (400/200) is a latent blocker**. Position restore from DB doesn't respect in-memory caps. When all 400 positions are restored, no new trades can enter. This silently blocks all the formula improvements from being tested.
