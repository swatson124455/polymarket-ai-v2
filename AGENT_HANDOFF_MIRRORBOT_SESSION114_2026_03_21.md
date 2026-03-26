# AGENT HANDOFF — MirrorBot Session 114 (2026-03-21)
## Carbon Copy Transfer Document — Complete Context for Continuation

**Scope**: MirrorBot only. No cross-bot bleed. Single-bot session.
**Prior sessions**: S109 (5 P&L fixes), S110/S111 (multi-factor confidence + archive + dampeners), S112 (CLOB fallback + F3 conviction + calibration disable), S113 (F1/F3 data-layer fixes + market_categories table), S114 (this session)
**Status**: S114 IN PROGRESS — position cap raised, stale position purge pending

---

## 1. WHAT THIS SYSTEM IS

**Polymarket AI V2** is a live 15-bot automated trading system for Polymarket prediction markets. Real capital is at risk ($20K deployed). Currently in **paper trading mode** (`SIMULATION_MODE=true`). Going live is flipping a boolean. Paper trading IS production — every feature must work identically.

**MirrorBot** is the highest-performing bot (+$20,277 all-time realized → revised to ~$14,806 post-archive). It copy-trades elite whale traders in real-time via RTDS WebSocket firehose. It does NOT analyze markets — it piggybacks on whale intelligence with Kelly-optimal sizing.

---

## 2. WHAT WAS DONE THIS SESSION (S114)

### Change 1: Position Cap Raise
- **Issue**: 400 legacy positions restored from DB exceeded the 200 cap, blocking ALL new BUY entries.
- **Root cause**: Position restore from DB doesn't respect in-memory caps. Cap was 200 but 400 positions existed.
- **Fix**: `config/settings.py` line 350: `MIRROR_MAX_CONCURRENT_POSITIONS` default `200` → `600`
- **Blast radius**: Only `_can_open_position()` in `mirror_bot.py` reads this setting. No other bots affected.
- **Status**: Code change made locally. NOT YET DEPLOYED TO VPS.

### Discovery: 344 of 400 "Open" Positions Are Stale
Full audit of all 400 open MirrorBot positions against CLOB API revealed:
- **344 positions**: Closed on-chain but still marked `status='open'` in DB (resolution backfill hasn't caught up)
- **56 positions**: Genuinely open on-chain
- **0 API errors**: 100% coverage

By opened_at date (from sample of oldest 20):
- 2026-03-18: 78 positions (17/20 sampled were stale)
- 2026-03-19: 167 positions
- 2026-03-20: 134 positions
- 2026-03-21: 21 positions

### Pending Action: Purge 344 Stale Positions
**NOT YET DONE.** User approved the concept. The purge script should:
1. Check each of the 400 open positions against CLOB API
2. For closed-on-chain positions: `UPDATE positions SET status='closed', current_price=1.0/0.0` based on winner
3. This updates the `positions` table only — RESOLUTION events in `trade_events` are handled separately by the resolution backfill queue

**Important**: After purge, real open count drops to ~56, well within the 200 cap. The cap raise to 600 provides safety buffer for growth.

---

## 3. ARCHITECTURE (Post-S113, Current)

### MirrorBot Confidence Pipeline (3-Factor)
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

### Scan Loop (45s interval)
Handles ONLY:
- Stop-loss exits (15% default, graduated tightening at 48h/72h, force at 96h)
- Take-profit (25%)
- Housekeeping (position reconciliation, cache refresh, RTDS stale detection)

### Resolution Backfill Pipeline (shared code — critical for P&L)
```
Phase 2a: SELECT unresolved markets from traded_markets (WHERE resolved_at IS NULL)
Phase 3:  Check Polymarket API for resolution → UPDATE traded_markets SET resolved_at, resolution
Phase 4:  UPDATE paper_trades SET resolved_at, resolution, realized_pnl WHERE market matches
Phase 4b: Emit RESOLUTION event to trade_events (S109: gate widened to fire on Phase 3 OR Phase 4)
Phase 5:  Backfill positions resolution (unrealized_pnl + status)
```
- Mini backfill: every 30 minutes
- Full backfill: daily
- Batch size: 200 (S113: was 100)

### Validation Pipeline (26 checks — entry path)
1. Wash trader filter
2. Hard price bounds [0.10, 0.95]
3. Circuit breaker
4. Position cap (600 — S114 raised from 200)
5. Daily exposure cap ($10k)
6. Category cap ($40K)
7. Market blocklist (in-memory)
8. Per-market cooldown (30min)
9. Category blocklist ("15-minute", "speed")
10. Opposing-side dedup
11. Same-side dedup (S109)
12. Inactive market filter
13. Near-resolution filter (4h)
14. Current price correction (use market price, not trader fill)
15. Slippage cap (8%)
16. Reliability gate (LR >= 1.0)
17. F1: Category Bayesian base (S110)
18. F2: Price-implied edge (S110)
19. F3: Trade size conviction (S112)
20. Confidence gate (>= 0.45)
21. Kelly sizing via BotBankrollManager
22. Dead zone dampener (30-50c: 0.50x)
23. Favorite dampener (>=70c: 0.40x)
24. Per-market USD cap ($400)
25. Daily remaining cap
26. Min trade USD ($50)

---

## 4. KEY DATA STRUCTURES

### `_reliability_tracker._cat_cache` (in-memory, refreshed every scan ~45s)
- Type: `Dict[Tuple[str, str], Dict]` — `(address.lower(), category.lower())` → Beta params
- Source: `trades` table LEFT JOIN `markets` + LEFT JOIN `market_categories` (S113)
- Size: ~8,705 entries (up from 8,629 pre-S113, +76 from market_categories)
- Contains: `alpha_yes, beta_yes, alpha_no, beta_no, yes_total, no_total`
- Used by: `mean()`, `category_trade_count()`, `likelihood_ratio()`

### `_watchlist_data` (in-memory, refreshed ~6h)
- Type: `Dict[str, Dict]` — `addr_lower` → `{address, pnl, vol, efficiency, num_trades, rank, userName}`
- S112: `num_trades` field added for F3
- S113: `num_trades` supplemented from DB `trades` table (491/500 whales have real counts)
- Source: Data API leaderboard + DB trade counts
- Used by: F3 conviction signal (`vol / num_trades` = avg trade size)

### `_market_meta_cache` (in-memory, TTL-based)
- Type: `Dict[str, Tuple[str, str, float]]` — `market_id` → `(category, time_to_res, expiry_monotonic)`
- **CRITICAL**: 3-tuple. NEVER expand.
- Source: `markets` table via DB query → CLOB API fallback (S112) → persist to `market_categories` (S113)

### `market_categories` table (DB, created S113, migration 057)
- Type: `condition_id TEXT PK` → `{category, question, yes_token_id, no_token_id, resolved, resolution, created_at}`
- ~149+ rows, growing as CLOB fallback fires
- Indexed: `category`, `resolved WHERE TRUE`
- Used by: tracker LEFT JOIN for F1, ongoing CLOB persist

### `_whale_consensus` (in-memory, resets daily, added S113)
- Type: `Dict[str, int]` — `"market_id:SIDE"` → count of whales that attempted
- Populated when same-side dedup blocks: increment count + add whale to `_open_positions.traders`
- Available in `event_data.consensus` for retroactive analysis
- Future: basis for Factor 4 (multi-whale consensus boost)

### `trade_events.event_data` JSONB (S113 enrichment)
MirrorBot ENTRY events now contain:
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

### `trade_events_archive` (DB table, migration 056)
- Same schema as `trade_events` + `archive_reason` + `archived_at`
- Contains 1,322 archived rows from S110
- Indexed on `(bot_name, market_id, side)` and `archive_reason`
- Reversible: `INSERT INTO trade_events SELECT ... FROM trade_events_archive`

---

## 5. VPS DETAILS & DEPLOY PATTERN

### Connection
- **Host**: `ubuntu@34.251.224.21`
- **SSH key**: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **SSH opts**: `-o ConnectTimeout=10 -o StrictHostKeyChecking=no`
- **Service**: `sudo systemctl restart polymarket-ai`
- **Real .env**: `/opt/pa2-shared/.env` (loaded by systemd `EnvironmentFile`). NOT `/opt/polymarket-ai-v2/.env`.
- **DB access**: `sudo -u postgres psql -d polymarket` (direct connection). DO NOT use pgbouncer (port 6432) — it hangs.
- **Logs**: `sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager`
- **Symlink**: `/opt/polymarket-ai-v2` → `/opt/pa2-releases/<deploy_timestamp>`

### Deploy via SCP
```bash
KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem
VPS=ubuntu@34.251.224.21

# Copy file to VPS
scp -i $KEY <local_file> $VPS:/tmp/<file>
# Install + restart
ssh -i $KEY $VPS "sudo cp /tmp/<file> /opt/polymarket-ai-v2/<path> && sudo systemctl restart polymarket-ai"
```

### .env changes
```bash
ssh -i $KEY $VPS "sudo sed -i 's/OLD=val/NEW=val/' /opt/pa2-shared/.env && sudo systemctl restart polymarket-ai"
```

**WARNING**: `deploy.sh` and `systemctl restart` may overwrite `.env` changes. Config changes via sed WILL BE LOST on redeploy unless the default in `settings.py` is also changed. For persistent changes, update `config/settings.py` defaults AND the VPS `.env`.

**New tables require GRANT**:
```bash
ssh -i $KEY $VPS "sudo -u postgres psql -d polymarket -c 'GRANT SELECT, INSERT, UPDATE ON <table> TO polymarket;'"
```

---

## 6. CONFIG REFERENCE (Live VPS Values)

```
# MirrorBot-specific
MIRROR_MIN_CONFIDENCE=0.45
MIRROR_MIN_RELIABILITY=0.52
MIRROR_USE_CALIBRATION=false       ← S112: disabled (re-enable ~2026-04-03)
MIRROR_MAX_POSITIONS=200           ← VPS .env (settings.py default now 600 after S114)
MIRROR_MAX_CONCURRENT_POSITIONS=200 ← VPS .env (settings.py default now 600 after S114)
MIRROR_MAX_PER_MARKET=400
MIRROR_DEAD_ZONE_LOW=0.30
MIRROR_DEAD_ZONE_HIGH=0.50
MIRROR_DEAD_ZONE_DAMPENER=0.50
MIRROR_FAVORITE_PRICE_THRESHOLD=0.70
MIRROR_FAVORITE_DAMPENER=0.40
WATCHLIST_ENABLED=true
WATCHLIST_SIZE=1000
MIRROR_CATEGORY_BLOCKLIST=15-minute,speed
MIRROR_MARKET_COOLDOWN_SECONDS=1800
MIRROR_MIN_TRADE_USD=50.0
MIRROR_MAX_SLIPPAGE_PCT=0.08
MIRROR_STOP_LOSS_PCT=0.15
MIRROR_MAX_ENTRIES_PER_MARKET=2
MIRROR_HARD_MIN_PRICE=0.10 (S102: was 0.05)
MIRROR_HARD_MAX_PRICE=0.95

# System-wide
SIMULATION_MODE=true (paper trading)
capital=$20000, max_bet=$300, max_daily=$10000, kelly=0.25
RESOLUTION_QUEUE_BATCH_SIZE=200    ← S113: was 100
DATABASE_URL=postgresql://polymarket:polymarket_s46@localhost:6432/polymarket
```

**IMPORTANT**: The VPS `.env` may still have `MIRROR_MAX_CONCURRENT_POSITIONS=200`. The S114 change to settings.py default (600) won't take effect on VPS until either:
1. The `.env` value is updated: `sudo sed -i 's/MIRROR_MAX_CONCURRENT_POSITIONS=200/MIRROR_MAX_CONCURRENT_POSITIONS=600/' /opt/pa2-shared/.env`
2. OR the `.env` key is removed (settings.py default will apply)

---

## 7. P&L REFERENCE

### P&L Formula (MANDATORY — see memory/feedback_pnl_math.md)
- `cost = entry_price × size` (ALL sides — YES and NO identical)
- `uPnL = (current - entry) × size` (ALL sides — YES and NO identical)
- WIN: `pnl = (1.0 - entry) × size`
- LOSS: `pnl = -(entry × size)`
- **NEVER invert for NO positions** — prices are token-specific
- Canonical script: `python scripts/bot_pnl.py MirrorBot hours`
- Data sources: `trade_events` (realized), `positions.unrealized_pnl` (mark-to-market)

### S112 First 10 Positions (3-factor formula):
**4W / 6L = +$667.39 net** — positive despite 40% WR (contrarian wins at low entry prices)

### Cumulative MirrorBot P&L (post-archive):
```
NO:  631 trades, 43.1% WR, $7,952 P&L, $12.70 avg
YES: 378 trades, 41.8% WR, $6,854 P&L, $18.23 avg
```

### Historical Confidence × P&L Brackets (S103 diagnostic):
| Confidence | Resolved | Win Rate | P&L | $/position |
|---|---|---|---|---|
| 30-40% | 11 | 9% | -$1,725 | -$157 |
| 40-50% | 11 | 18% | -$585 | -$53 |
| 50-55% | 337 | 36% | +$8,696 | +$26 |
| 55%+ | 587 | 48% | +$7,415 | +$13 |

---

## 8. PENDING ACTIONS (Ordered by Priority)

### P0: Purge 344 Stale Positions (IMMEDIATE — THIS SESSION)
344 of 400 open positions are closed on-chain. Purge script needed:
```python
# Run on VPS
sudo /opt/polymarket-ai-v2/venv/bin/python3 << 'PYEOF'
import asyncio, httpx, asyncpg

async def purge():
    conn = await asyncpg.connect("postgresql://polymarket:polymarket_s46@localhost:6432/polymarket")
    rows = await conn.fetch(
        "SELECT market_id, side, entry_price, size FROM positions "
        "WHERE source_bot=$1 AND status=$2",
        "MirrorBot", "open"
    )
    print(f"Checking {len(rows)} open positions...")

    purged = 0
    kept = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for i, r in enumerate(rows):
            try:
                resp = await client.get(f"https://clob.polymarket.com/markets/{r['market_id']}")
                if resp.status_code == 429:
                    await asyncio.sleep(2)
                    resp = await client.get(f"https://clob.polymarket.com/markets/{r['market_id']}")
                if resp.status_code != 200:
                    kept += 1
                    continue
                d = resp.json()
                if not d.get("closed"):
                    kept += 1
                    continue
                tokens = d.get("tokens", [])
                winner_idx = next((j for j, t in enumerate(tokens) if t.get("winner")), None)
                if winner_idx is None:
                    kept += 1
                    continue
                resolved = "YES" if winner_idx == 0 else "NO"
                won = (r["side"] == resolved)
                await conn.execute(
                    "UPDATE positions SET status=$1, current_price=$2 "
                    "WHERE market_id=$3 AND source_bot=$4 AND side=$5 AND status=$6",
                    "closed", 1.0 if won else 0.0,
                    r["market_id"], "MirrorBot", r["side"], "open"
                )
                purged += 1
            except Exception as e:
                print(f"ERR {r['market_id'][:16]}: {e}")
                kept += 1
            if (i+1) % 50 == 0:
                print(f"  processed {i+1}/{len(rows)}...")
                await asyncio.sleep(1)

    final = await conn.fetchval(
        "SELECT COUNT(*) FROM positions WHERE source_bot=$1 AND status=$2",
        "MirrorBot", "open"
    )
    print(f"\nPurged: {purged}, Kept: {kept}, Final open count: {final}")
    await conn.close()

asyncio.run(purge())
PYEOF
```
**NOTE**: This only updates `positions` table. RESOLUTION events in `trade_events` are created separately by the resolution backfill queue. For full P&L accounting, the backfill must also run.

### P1: Deploy Cap Raise to VPS
After purge, deploy the settings.py change OR update VPS .env:
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo sed -i 's/MIRROR_MAX_CONCURRENT_POSITIONS=200/MIRROR_MAX_CONCURRENT_POSITIONS=600/' /opt/pa2-shared/.env && sudo systemctl restart polymarket-ai"
```

### P2: Validate F1+F3 Live Performance (HIGHEST ANALYTIC PRIORITY)
Once trades flow (post-purge + cap deploy):
```sql
-- Check cat_n > 0 trades (F1 active)
SELECT ROUND(confidence::numeric, 2) as conf, COUNT(*) as cnt
FROM trade_events WHERE bot_name='MirrorBot' AND event_type='ENTRY'
  AND event_time >= '2026-03-22'
  AND event_data->>'conf_base' IS NOT NULL
  AND (event_data->>'conf_base')::float != 0.50
GROUP BY 1 ORDER BY 1;
```
```bash
# Live log checks
journalctl -u polymarket-ai -f | grep 'mirror_multifactor' | grep -v 'cat_n=0'
journalctl -u polymarket-ai -f | grep 'mirror_multifactor' | grep -v 'conv_adj=0'
```
**Goal**: `cat_n > 0` for trades where category is known. `conv_adj != 0` for RTDS trades with whale in watchlist.

### P3: Confidence-Bucket P&L Analysis (~2026-03-25)
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

### P4: Factor 4 — Multi-Whale Consensus (BLOCKED)
- Same-side dedup prevents re-entry, but S113 now records consensus count in `_whale_consensus` dict and `event_data.consensus`
- After enough data, analyze: do multi-whale markets outperform?
- If yes, implement boost: `+0.03 × min(consensus_count - 1, 4)` applied after F1+F2+F3
- **Risk**: The first whale's trade is already placed before the second arrives. F4 would only boost confidence for the FIRST entry if dedup is restructured.

### P5: FTS Calibration Re-enable (~2026-04-03)
After 2 weeks of data at new confidence range [0.45-0.75], re-fit temperature T:
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
sudo sed -i 's/MIRROR_USE_CALIBRATION=false/MIRROR_USE_CALIBRATION=true/' /opt/pa2-shared/.env
sudo systemctl restart polymarket-ai
```

### P6: `_infer_category()` Coverage Monitoring
Check CLOB fallback logs for "unknown" categorization rate:
```bash
journalctl -u polymarket-ai --since '24 hours ago' | grep 'mirror_clob_category_resolve' | grep 'unknown' | wc -l
```
If > 10%, expand keywords for most common unknowns. Currently 10 categories with ~285 keywords.

### P7: Resolution Backfill → market_categories Sync
- `market_categories` has ~149+ rows, 24 resolved
- CLOB fallback persist (S113) populates resolved status for NEW trades
- For HISTORICAL rows, re-running backfill script will update: `sudo /opt/polymarket-ai-v2/venv/bin/python3 scripts/backfill_market_categories.py --limit 2000`

---

## 9. FILES MODIFIED ACROSS ALL MIRRORBOT SESSIONS (S109-S114)

| File | Changes | Session |
|------|---------|---------|
| `bots/mirror_bot.py` | Same-side dedup, multi-factor confidence (F1+F2+F3), CLOB fallback + persist, consensus counter, event_data enrichment, reap decrement, `whale_trade_usd` param | S109-S113 |
| `bots/elite_watchlist.py` | `num_trades` in watchlist data, `whale_trade_usd` passthrough, DB supplement for num_trades | S112-S113 |
| `base_engine/data/database.py` | condition_id enrichment, stale uPnL cleanup, LEFT JOIN tracker query, `upsert_market_category()`, `get_user_trade_counts()` | S109, S113 |
| `base_engine/data/data_ingestion.py` | Expanded keywords + healthcare/legal categories (10 total, ~285 keywords) | S113 |
| `base_engine/data/resolution_backfill.py` | Phase 4b gate widened, RESOLUTION size populated | S109 |
| `base_engine/learning/elite_reliability.py` | Log level bumps (debug→warning/info) | S110 |
| `config/settings.py` | `RESOLUTION_QUEUE_BATCH_SIZE` 100→200, `MIRROR_MAX_CONCURRENT_POSITIONS` 200→600 | S113, S114 |
| `tests/unit/test_mirror_bot_logic.py` | Same-side dedup test, mean() mock for multi-factor | S109, S110 |
| `schema/migrations/056_mirror_archive_bad_data.sql` | Archive table for bad trade_events | S110 |
| `schema/migrations/057_market_categories.sql` | New market_categories table | S113 |
| `scripts/backfill_market_categories.py` | Backfill script for market_categories | S113 |

### Git State (local, NOT committed)
```
Modified (unstaged):
  base_engine/data/data_ingestion.py
  base_engine/data/database.py
  base_engine/data/resolution_backfill.py
  base_engine/learning/elite_reliability.py
  bots/elite_watchlist.py
  bots/mirror_bot.py
  config/settings.py                          ← S114 cap raise HERE
  tests/unit/test_mirror_bot_logic.py

New (untracked):
  schema/migrations/057_market_categories.sql
  scripts/backfill_market_categories.py
```

---

## 10. KEY FILES REFERENCE

| File | Purpose |
|------|---------|
| `bots/mirror_bot.py` | Core MirrorBot logic (~1500 lines). Entry validation, RTDS dispatch, position management, 3-factor confidence |
| `bots/elite_watchlist.py` | RTDS trade matching + upstream confidence. 500-whale watchlist. num_trades supplement. |
| `bots/mirror_calibration.py` | FTS domain/horizon calibration (currently disabled) |
| `base_engine/base_engine.py` | Shared engine (market index, order gateway) |
| `base_engine/execution/order_gateway.py` | Order execution + risk checks |
| `base_engine/data/database.py` | DB models, `insert_paper_trade()`, `backfill_positions_resolution()`, `upsert_market_category()`, `get_user_trade_counts()`, tracker LEFT JOIN query |
| `base_engine/data/data_ingestion.py` | `_infer_category()` keyword matching (10 categories) |
| `base_engine/data/resolution_backfill.py` | Resolution pipeline (Phase 2a→5) |
| `base_engine/learning/elite_reliability.py` | `_cat_cache` refresh, `mean()`, `category_trade_count()`, `likelihood_ratio()` |
| `config/settings.py` | All config defaults including MIRROR_* |
| `tests/unit/test_mirror_bot_logic.py` | MirrorBot unit tests (65 tests) |
| `scripts/bot_pnl.py` | Canonical P&L script |
| `scripts/backfill_market_categories.py` | market_categories backfill from CLOB API |
| `deploy/deploy.sh` | Atomic deploy to VPS |

---

## 11. CRITICAL TRAPS (MirrorBot-Specific, DO NOT BREAK)

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
13. **`_infer_category()` is keyword-based**: Returns "unknown" for unmatched markets. 10 categories, ~285 keywords. Located in `base_engine/data/data_ingestion.py`.
14. **CLOB API variable name**: Uses `_hc` (not `h`) in `_get_market_meta()` to avoid shadowing.
15. **`whale_trade_usd` default=0.0**: All callers except `elite_watchlist.on_rtds_trade()` pass default. F3 produces `_conv_adj=0.0` when `whale_trade_usd=0`.
16. **New table permissions**: `market_categories` required explicit `GRANT ... TO polymarket`. Always GRANT after creating tables.
17. **Tracker LEFT JOIN**: `get_user_resolution_counts_by_category()` uses LEFT JOIN to both `markets` AND `market_categories`. If `market_categories` dropped, query falls through (mc columns NULL, COALESCE to markets).
18. **`get_user_trade_counts()` uses `LOWER(user_address)`**: Case-insensitive matching.
19. **Backfill script is idempotent**: `ON CONFLICT DO UPDATE` — safe to re-run.
20. **`_persist_market_category` is fire-and-forget**: Uses `asyncio.create_task()`. DB errors logged debug, don't block trades.
21. **`event_data` enrichment**: Only on BUY orders. EXIT/SELL orders don't pass event_data.
22. **P&L formula**: NEVER invert for NO. `cost = entry × size`, `uPnL = (current - entry) × size` for ALL sides.
23. **`trade_events` is P&L authority** — never read `paper_trades` for P&L. SELL/EXIT trades only exist in `trade_events`.
24. **RESOLUTION event idempotency**: `ON CONFLICT` broken on partitioned tables. Uses atomic INSERT...SELECT WHERE NOT EXISTS.
25. **Python 3.13 scoping**: `from X import Y` inside function makes `Y` local for ENTIRE function.
26. **`asyncpg JSONB`**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
27. **`asyncpg DATE columns`**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime.
28. **`asyncpg timestamp columns`**: `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`.
29. **`paper_trades` has NO `metadata` JSONB column**.
30. **Positions table**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`. Column is `source_bot` NOT `bot_name`.
31. **`prediction_log`**: NO `rejection_reason`. Use `trade_executed` (bool).
32. **`trade_events` JSONB column is `event_data`** NOT `metadata_json`.
33. **`traded_markets.bot_names`**: TEXT column (not array), use `LIKE '%BotName%'`.
34. **Resolution backfill excludes SELL trades**: SELL P&L computed by paper engine at exit time.
35. **VPS .env path**: `/opt/pa2-shared/.env` loaded by systemd. NOT `/opt/polymarket-ai-v2/.env`.

---

## 12. MANDATORY RULES (FROM USER FEEDBACK)

### Scope Lock (NON-NEGOTIABLE)
You may ONLY make changes that are:
1. Explicitly listed in this handoff as a fix/action item, OR
2. Explicitly requested by the user in this conversation

**Everything else is forbidden.** No "while I'm in here" improvements, no "quick wins", no observations turned into code.

### Bot-Scoped Sessions
- Only modify files owned by MirrorBot
- Shared infrastructure changes (base_engine, database) OK ONLY if directly fixing a MirrorBot bug
- Do NOT touch other bot files (esports_bot.py, weather_bot.py) unless user manually demands it

### P&L Math
- NEVER invert formulas for NO positions — prices are token-specific
- `cost = entry_price × size` (ALL sides)
- `uPnL = (current - entry) × size` (ALL sides)

---

## 13. SESSION HISTORY (RELEVANT CONTEXT)

| Session | Key Changes |
|---------|-------------|
| S77 | Phantom trade dedup, stale entry pricing fix, resolution backfill SELL overwrite fix |
| S79 | Selectivity tightening (MIN_CONFIDENCE 0.10→0.55, but never enforced!) |
| S81 | RTDS live, paper_trades DB persistence fix |
| S85 | Resolution backfill 3 root causes (Python 3.13 scoping, non-YES/NO outcomes, perf). 544 resolved. |
| S86 | Ingestion sync fix, RESOLUTION event dedup (3238 dupes deleted) |
| S87 | RESOLUTION dedup fix for partitioned tables (atomic INSERT...SELECT) |
| S90 | Scheduler zombie advisory lock fix, master timeout |
| S94 | Latency 2967ms→11.9ms, lock-free DB, RTDS fast-path |
| S100 | Alpha decay, canary persistence, SSH timeouts |
| S101 | Bucket filters, whale trade commit fix |
| S102 | Hard floor 10c, single dampener, dead code purge, $50 min trade |
| S103 | Confidence gate added (0.45 TRIAL), log spam demotion |
| S109 | 5 root-cause P&L fixes: condition_id enrichment, Phase 4b gate, same-side dedup, stale uPnL cleanup, RESOLUTION size. Cap 200→400. |
| S110 | Multi-factor confidence formula (F1 Bayesian + F2 price edge). Archive migration 056. Dampeners wired. Category lookup fix. |
| S111 | S110 deploy verification + handoff document |
| S112 | CLOB API category fallback, F3 trade conviction signal, calibration disabled. First 10 positions: 4W/6L +$667.39 |
| S113 | F1/F3 data-layer fixes: market_categories table (migration 057), tracker LEFT JOIN, num_trades DB supplement. Keywords expanded to 10 categories/285 keywords. Consensus counter, event_data enrichment, reap decrement. |
| **S114** | **Cap raise 200→600. Audit: 344/400 positions stale. Purge pending.** |

---

## 14. QUICK REFERENCE COMMANDS

```bash
# SSH to VPS
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o ConnectTimeout=10 -o StrictHostKeyChecking=no ubuntu@34.251.224.21

# DB access (ALWAYS use this, never pgbouncer)
sudo -u postgres psql -d polymarket

# MirrorBot logs
sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager | grep -i mirror

# Multi-factor confidence check
sudo journalctl -u polymarket-ai -f | grep mirror_multifactor

# Confidence gate firing
sudo journalctl -u polymarket-ai -f | grep mirror_low_confidence

# Same-side dedup firing
sudo journalctl -u polymarket-ai -f | grep mirror_same_side_blocked

# CLOB fallback working
sudo journalctl -u polymarket-ai -f | grep mirror_clob_category_resolve

# Position cap log
sudo journalctl -u polymarket-ai -f | grep "Mirror POSITION CAP"

# Service restart
sudo systemctl restart polymarket-ai

# P&L check
sudo -u postgres psql -d polymarket -c "SELECT event_type, COUNT(*), ROUND(SUM(COALESCE(realized_pnl,0))::numeric,2) FROM trade_events WHERE bot_name='MirrorBot' GROUP BY event_type ORDER BY event_type;"

# Open positions count
sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM positions WHERE source_bot='MirrorBot' AND status='open';"

# Stale uPnL check (should be 0)
sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM positions WHERE status='closed' AND unrealized_pnl <> 0;"

# Backfill status
sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM traded_markets WHERE resolved_at IS NULL AND bot_names LIKE '%Mirror%';"

# market_categories coverage
sudo -u postgres psql -d polymarket -c "SELECT COUNT(*), SUM(CASE WHEN resolved THEN 1 ELSE 0 END) as resolved FROM market_categories;"

# Run tests locally
pytest tests/ -x -q
```

---

## 15. ROLLBACK PLAN

| Change | Rollback |
|--------|----------|
| S114 cap raise | `MIRROR_MAX_CONCURRENT_POSITIONS=200` in .env or revert settings.py line 350 |
| S114 stale purge (if done) | `UPDATE positions SET status='open', current_price=<original> WHERE ...` — but purge is correct, rollback not expected |
| S113 F1 market_categories | `DROP TABLE market_categories;` — tracker query falls through to markets-only |
| S113 F1 LEFT JOIN | Revert `database.py` to `JOIN markets m ON t.market_id = m.id` |
| S113 CLOB persist | Remove `_persist_market_category()` and fire-and-forget block in mirror_bot.py |
| S113 F3 num_trades | Remove DB supplement block in elite_watchlist.py |
| S113 consensus counter | Remove `_whale_consensus` dict and tracking in mirror_bot.py |
| S113 keywords | Revert data_ingestion.py — remove healthcare/legal categories |
| S113 event_data | Remove `_event_data` dict and `event_data=` param in mirror_bot.py |
| S113 reap decrement | Remove exposure decrement in `_reap_resolved_positions()` |
| S113 batch size | `RESOLUTION_QUEUE_BATCH_SIZE=100` |
| S112 CLOB fallback | Remove CLOB API block in `_get_market_meta()` |
| S112 F3 conviction | Revert mirror_bot.py + elite_watchlist.py F3 blocks |
| S110 multi-factor | Revert mirror_bot.py to old domain drift penalty |
| S110 archive | `INSERT INTO trade_events SELECT ... FROM trade_events_archive` |
| S110 dampeners | Remove dampener blocks in mirror_bot.py |
| S109 same-side dedup | Remove lines 1119-1131 in mirror_bot.py |

---

## 16. FOUR-FACTOR DESIGN SPEC (Reference)

| Factor | Description | Status | Notes |
|--------|------------|--------|-------|
| F1 | Category-specific Bayesian base | **LIVE (S110+S113)** | tracker LEFT JOINs markets + market_categories |
| F2 | Price-implied edge (contrarian/consensus) | **LIVE (S110)** | YES<0.45 or NO>0.55 = contrarian boost |
| F3 | Trade size conviction | **LIVE (S112+S113)** | whale_trade_usd / avg_trade_size. +0.04 if >2x, -0.03 if <0.3x |
| F4 | Multi-whale consensus | **BLOCKED** | Consensus tracked (S113) but boost not implemented. Needs dedup restructure. |

---

## 17. SESSION LEARNINGS (Accumulated)

1. **Always check table permissions after CREATE TABLE.** polymarket user needs explicit GRANT.
2. **F1 was starved THREE levels deep**: S110 fixed formula, S112 fixed category input, S113 fixed historical data. Each fix revealed the next layer.
3. **Leaderboard APIs are unreliable for derived metrics.** Data API doesn't return trade counts. Always verify actual API fields.
4. **Fire-and-forget OK for non-critical persistence.** `_persist_market_category` uses `asyncio.create_task()` — acceptable because backfill catches up.
5. **Position cap overload is a latent blocker.** Position restore from DB doesn't respect caps. 400 restored into 200 cap → silent block.
6. **deploy.sh overwrites .env.** Config changes via sed are LOST on redeploy unless settings.py default is also changed.
7. **Always check the data layer before declaring the formula works.** S110 deployed correct formula, but 85% of inputs were empty.
8. **Variable naming matters.** `h` shadowed by httpx client → use `_hc`.
9. **CLOB API for condition_id lookups, Gamma API for numeric IDs.** RTDS trades use condition_ids → CLOB is correct.
10. **Resolution backfill queue can stall P&L visibility for days.** Manual resolution is the workaround.
11. **344/400 "open" positions were stale** — closed on-chain, DB hadn't caught up. Always audit before raising caps.
