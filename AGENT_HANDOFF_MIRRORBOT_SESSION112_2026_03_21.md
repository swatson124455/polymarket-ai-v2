# AGENT HANDOFF — MirrorBot Session 112 (2026-03-21)

**Scope**: MirrorBot only. No cross-bot bleed. Single-bot session.
**Prior sessions**: S109 (P&L fixes), S110/S111 (multi-factor confidence + archive + dampeners), S112 (this session)
**Status**: ALL S112 CHANGES DEPLOYED AND VERIFIED LIVE ON VPS

---

## What Was Done This Session (S112)

### Problem Statement
S110 deployed a multi-factor confidence formula with 2 factors (Bayesian category base + price edge). Live data showed **85% of trades still clustered at 0.49-0.53** because:
1. `_get_market_meta()` only queries the `markets` DB table — which only has top-500 markets from ingestion
2. Whales trade on markets OUTSIDE the top-500 → `category=""` → Factor 1 falls back to `_base=0.50` (uninformative prior)
3. The formula was correct but **starved of data** — same root pattern as S110 but one layer deeper

Additionally:
- **Factor 3 (trade size conviction)** from the design spec was not implemented
- **FTS calibration** kept reverting to `true` on service restarts (deploy.sh overwrites `.env`)
- **Position prices were stale** — 10 resolved-on-chain positions showed $0 P&L because the resolution backfill queue had a 2,258-market backlog

### Changes Deployed (4 items)

#### 1. CLOB API Category Fallback — `bots/mirror_bot.py` lines 617-635
**Bug**: `_get_market_meta()` returned `category=""` for ~85% of trades because those markets aren't in the `markets` table (only top-500 ingested).
**Fix**: When DB lookup returns no row, hit CLOB API (`https://clob.polymarket.com/markets/{condition_id}`) to get the market question, then use `_infer_category(question)` for keyword-based category resolution.
```python
# S112: CLOB API fallback — whales trade markets outside top-500 ingestion
if not category:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5.0) as _hc:
            resp = await _hc.get(f"https://clob.polymarket.com/markets/{market_id}")
            if resp.status_code == 200:
                clob = resp.json()
                q = clob.get("question") or ""
                if q:
                    from base_engine.data.data_ingestion import _infer_category
                    category = _infer_category(q)
                    logger.info("mirror_clob_category_resolve",
                                market=market_id[:16], category=category,
                                question=q[:60])
    except Exception as _clob_err:
        logger.debug("CLOB category fallback failed for %s: %s",
                     market_id[:16], _clob_err)
```
- Cached by `_market_meta_cache` (TTL-based) — only 1 API hit per unique market
- 5s timeout prevents blocking the trade pipeline
- **Verified live**: `mirror_clob_category_resolve` logs showing `weather`, `entertainment`, `politics`, `sports` categories resolving correctly
- Variable name `_hc` (not `h`) to avoid shadowing the hours variable in the outer scope

#### 2. Factor 3: Trade Size Conviction Signal — `bots/mirror_bot.py` + `bots/elite_watchlist.py`

**Two files changed:**

**`bots/elite_watchlist.py`:**
- Line ~189: Added `num_trades` to `_watchlist_data` dict:
  ```python
  _num_trades = int(t.get("totalTrades", t.get("numTrades", t.get("total_trades", 0))) or 0)
  ```
- Line ~390: Computes `whale_trade_usd = size * price` from RTDS payload and passes to `_execute_mirror_trade()`:
  ```python
  _whale_trade_usd = size * price  # size=shares from RTDS, price=fill price
  executed = await self._mirror_bot._execute_mirror_trade(
      ...,
      whale_trade_usd=_whale_trade_usd,
  )
  ```

**`bots/mirror_bot.py`:**
- Line ~1067: Added `whale_trade_usd: float = 0.0` parameter to `_execute_mirror_trade()` signature
- Lines ~1280-1294: Factor 3 implementation between F2 (price edge) and final clamp:
  ```python
  _conv_adj = 0.0
  if whale_trade_usd > 0 and self._watchlist:
      _wdata = getattr(self._watchlist, "_watchlist_data", {})
      _wd = _wdata.get(trader_address.lower(), {})
      _whale_vol = _wd.get("vol", 0)
      _whale_n = _wd.get("num_trades", 0)
      if _whale_vol > 0 and _whale_n > 0:
          _avg_trade = _whale_vol / _whale_n
          _size_ratio = whale_trade_usd / max(_avg_trade, 1.0)
          if _size_ratio > 2.0:
              _conv_adj = 0.04   # big position for this whale
          elif _size_ratio < 0.3:
              _conv_adj = -0.03  # small/exploratory
  ```
- Final confidence now: `confidence = max(0.35, min(0.75, _base + _price_adj + _conv_adj))`
- Log line updated: added `conv_adj` and `whale_usd` fields to `mirror_multifactor` structured log

#### 3. FTS Calibration Disabled on VPS
- `MIRROR_USE_CALIBRATION=false` in `/opt/polymarket-ai-v2/.env`
- **WARNING**: `systemctl restart` and `deploy.sh` may overwrite `.env` changes. The default in `config/settings.py` line 365 is `false`, but the VPS `.env` had it set to `true`. Had to re-disable twice this session after restarts.
- **Re-enable after ~2 weeks** (~2026-04-03) when prediction_log has enough data at the new confidence range to re-fit temperature T.
- The calibration stack still fits on startup (`mirror_calibration_fts_fitted gamma=5.0 temperature=2.0`) but `_calibration_stack` is `None` when `MIRROR_USE_CALIBRATION=false`.

#### 4. Manual Position Resolution (10 of 11 S112 positions)
- Resolution backfill queue had 2,258 MirrorBot markets in backlog, processing ~100/batch every 5min
- 10 of 11 new positions were closed on-chain but DB showed them as open with stale `current_price == entry_price`
- Ran manual asyncpg script on VPS to check CLOB API and update `positions.status='closed'` + `current_price=1.0/0.0`
- 1 position still open: Juventus O/U 2.5 (`0x66c52ea7a754f8`)

---

## S112 P&L Results (First 10 resolved positions under 3-factor formula)

| Market | Side | Conf | Entry | Size | Result | P&L |
|--------|------|------|-------|------|--------|-----|
| BTC Up/Down 2:30PM | YES | 0.547 | 0.18 | 1,247 | **WIN** | **+$1,023** |
| ETH Up/Down 3PM | YES | 0.520 | 0.24 | 577 | **WIN** | **+$438** |
| Blazers/Wolves O/U | NO | 0.608 | 0.44 | 487 | **WIN** | **+$273** |
| BTC Up/Down 3PM | YES | 0.499 | 0.45 | 165 | **WIN** | **+$91** |
| RB Leipzig win? | NO | 0.634 | 0.52 | 598 | LOSS | -$311 |
| Blazers vs Wolves | NO | 0.570 | 0.54 | 516 | LOSS | -$279 |
| Hofstra/Bama O/U | NO | 0.608 | 0.44 | 212 | LOSS | -$214 (corrected from earlier -$95) |
| Wright St vs Virginia | YES | 0.531 | 0.09 | 2,110 | LOSS | -$190 |
| Pistons -4.5 | NO | 0.571 | 0.47 | 323 | LOSS | -$152 |
| BTC Up/Down 1:30PM | YES | 0.534 | 0.35 | 373 | LOSS | -$131 |

**Score: 4W / 6L — Net: +$667.39**

Key insight: 40% WR but **positive P&L** because wins are at low entry prices (0.18, 0.24) giving 5-10x payouts. The contrarian price edge (F2) correctly sizes into high-payout low-probability bets. The BTC 2:30PM trade alone: $224 cost → $1,247 payout.

### P&L Context (full since S112 deploy):
- Old positions resolving (pre-formula): -$407.12
- New S112 positions (3-factor formula): +$667.39
- Net since deploy: **~+$260**

---

## Live Verification Results (2026-03-21)

### CLOB category fallback working:
```
mirror_clob_category_resolve category=weather market=0x069add4241b727 question='Will the highest temperature in Shenzhen be 29°C on March 22'
mirror_clob_category_resolve category=entertainment market=0xa64d217a3280b4 question='Will "Ready or Not 2: Here I Come" Opening Weekend Box Offic'
mirror_clob_category_resolve category=politics market=0x347f9cfc75ec81 question='Will Elon Musk post 65-89 tweets from March 21 to March 23, '
```

### Confidence histogram (19 entries since S112 deploy):
```
conf | cnt
0.50 |   2
0.51 |   2
0.52 |   3
0.53 |   5
0.54 |   1
0.55 |   1
0.57 |   3
0.61 |   1
0.63 |   1
```
Spread 0.50-0.63 across 9 distinct values. Better than pre-S110 point mass at 0.526, but 68% still at 0.50-0.53. Category fallback was deployed mid-session — future trades should show wider spread as CLOB fallback provides category data to Factor 1.

### NO vs YES Asymmetry — RESOLVED:
```
NO:  631 trades, 43.1% WR, $7,952 P&L, $12.70 avg
YES: 378 trades, 41.8% WR, $6,854 P&L, $18.23 avg
```
Gap narrowed from 72% vs 39% (pre-archive) to 43% vs 42%. YES has higher avg P&L ($18.23 vs $12.70). No action needed.

### Daily cap saturation:
- `_daily_exposure=20201.70` on startup (cap is $10,000)
- MirrorBot hits daily cap early and sits idle most of the day
- Trades flow at UTC midnight reset

### Fill cooldown errors — NOT MirrorBot:
- All cooldown errors are EsportsBot (hammering one market 18x in 40s) and WeatherBot
- Zero MirrorBot fill cooldown errors

---

## Pending Actions (For Next Session)

### P1: Monitor 3-Factor Formula Performance (48-72h)
After CLOB fallback + F3 conviction are both active for 2+ days:
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
  AND event_time >= '2026-03-21'
GROUP BY 1 ORDER BY 1;
```
**Goal**: Higher confidence buckets should show higher win rates AND higher avg P&L. If not, formula needs tuning.

### P2: Factor 4 — Multi-Whale Consensus (NOT IMPLEMENTED)
**Why blocked**: Same-side dedup (lines ~1119-1131 in mirror_bot.py) blocks second whale entry on same market+side BEFORE confidence code runs. So consensus count is always 1.
**To implement**: Would need to restructure dedup to COUNT same-side whales but still prevent duplicate positions. Options:
- Track consensus count in `_open_positions` metadata (increment on each whale match, don't re-enter)
- Move dedup AFTER confidence computation, use consensus as a confidence boost only
**Risk**: Riskier change — dedup protects against double-sizing. Needs careful design.

### P3: Resolution Backfill Queue Throughput
- 2,258 MirrorBot markets in backlog (+ 1,193 WeatherBot, 82 EnsembleBot)
- Processing ~30-50 per batch at 100/batch every 5min = days to clear
- Options: increase batch size, increase frequency, or add a "priority" path for open positions
- Not blocking trades, but delays P&L visibility

### P4: `_infer_category()` Coverage Quality
CLOB fallback uses keyword matching from `_infer_category()` in `base_engine/data/data_ingestion.py`. This is coarse — it may misclassify or return "unknown" for niche markets. Monitor `mirror_clob_category_resolve` logs for accuracy. If many come back as "unknown", the keyword list needs expansion.

### P5: Persist Category in `event_data` JSONB
`trade_events.event_data` is `{}` for all MirrorBot entries. The `mirror_multifactor` log captures category but it's not persisted to the DB. Adding category to `event_data` would enable retroactive analysis without grepping logs.

### P6: FTS Calibration Re-enable (~2026-04-03)
After ~2 weeks of data at the new confidence range [0.35-0.75], re-fit temperature T:
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
sudo sed -i 's/MIRROR_USE_CALIBRATION=false/MIRROR_USE_CALIBRATION=true/' /opt/polymarket-ai-v2/.env
sudo systemctl restart polymarket-ai
```

### P7: Daily Cap Investigation
MirrorBot saturates $10k daily cap early, blocking new trades most of the day. The `_daily_exposure` seeded at $20,201 on restart — meaning cumulative ENTRY cost from today's trade_events. This may be inflated by old entries. Investigate whether the cap formula is correct or if closed positions should decrement exposure.

---

## Files Modified This Session (S112)

| File | Change | Status |
|------|--------|--------|
| `bots/mirror_bot.py` | CLOB fallback in `_get_market_meta()` (lines 617-635), F3 conviction signal (lines 1280-1294), `whale_trade_usd` param on `_execute_mirror_trade()`, updated `mirror_multifactor` log fields | Deployed to VPS |
| `bots/elite_watchlist.py` | `num_trades` in `_watchlist_data` dict, `whale_trade_usd` computed and passed to `_execute_mirror_trade()` | Deployed to VPS |
| VPS `.env` | `MIRROR_USE_CALIBRATION=false` (set twice, reverted by restarts) | Applied |

### Files NOT modified (read-only reference):
- `base_engine/data/ingestion_scheduler.py` — resolution queue logic, mini backfill, `_do_resolution_queue()`
- `base_engine/data/resolution_backfill.py` — `_fetch_market_by_condition_id()`, `_infer_category()` import pattern
- `base_engine/data/data_ingestion.py` — `_infer_category()` keyword matching
- `base_engine/data/polymarket_client.py` — `get_market()` Gamma API pattern
- `base_engine/base_engine.py` — `get_market_from_index()` in-memory lookup, `self.client` access
- `config/settings.py` — `MIRROR_USE_CALIBRATION` default=false (line 365), dampener configs (lines 387-391)
- `tests/unit/test_mirror_bot_logic.py` — 65 tests pass, no changes this session

---

## Architecture Reference (MirrorBot Confidence Pipeline — Updated S112)

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
  │     └── ★ S112: CLOB API fallback if DB miss → _infer_category(question)
  ├── Category blocklist
  ├── Position cap: _can_open_position()
  ├── Same-side dedup: blocks re-entry on same market+side (S109)
  ├── Reliability multiplier: likelihood_ratio() → lr_mult
  │
  ├── ★ S110: Factor 1 — Category-Specific Bayesian Base
  │     _base = 0.50 + shrinkage(cat_n, 20) × (cat_wr - 0.50)
  │     if cat_n < 10: _base = min(_base, 0.52)
  │
  ├── ★ S110: Factor 2 — Price-Implied Edge
  │     contrarian (YES<0.45, NO>0.55): _price_adj = price_dev × 0.15
  │     consensus: _price_adj = -(price_dev × 0.15 × 0.3)
  │
  ├── ★ S112: Factor 3 — Trade Size Conviction
  │     _size_ratio = whale_trade_usd / (whale_vol / whale_num_trades)
  │     >2.0 → _conv_adj = +0.04 (big bet for this whale)
  │     <0.3 → _conv_adj = -0.03 (exploratory)
  │
  ├── Final: confidence = clamp(0.35, 0.75, _base + _price_adj + _conv_adj)
  │
  ├── [DISABLED] FTS Calibration: sigmoid(logit(confidence) / T)
  │     MIRROR_USE_CALIBRATION=false on VPS. Re-enable ~2026-04-03.
  ├── MIN_CONFIDENCE gate (0.45): reject if below
  │
  ├── Kelly sizing: kelly_full = (conf × b - q) / b
  ├── Extreme-price dampener (existing): gray zone
  ├── S110: Dead zone dampener: price 0.30-0.50 → size × 0.50
  ├── S110: Favorite dampener: price > 0.70 → size × 0.40
  ├── Per-market cap: 1% of capital
  └── place_order(side=YES/NO)
```

---

## Key Data Structures

### `_reliability_tracker._cat_cache` (in-memory, refreshed every scan ~45s)
- Type: `Dict[Tuple[str, str], Dict]` — `(address.lower(), category.lower())` → Beta params
- Source: `trades` table JOIN `markets` table, filtered to resolved markets
- Size: ~8,637 entries (whale+category combos), 3,690 unique users
- Contains: `alpha_yes, beta_yes, alpha_no, beta_no, yes_total, no_total`
- Used by: `mean()`, `category_trade_count()`, `likelihood_ratio()`

### `_watchlist_data` (in-memory, refreshed periodically)
- Type: `Dict[str, Dict]` — `addr_lower` → `{address, pnl, vol, efficiency, num_trades, rank, userName}`
- ★ S112: `num_trades` field added for F3 avg trade size computation
- Source: Leaderboard API + DB `users` table
- Used by: F3 conviction signal (`vol / num_trades` = avg trade size)

### `_market_meta_cache` (in-memory, TTL-based)
- Type: `Dict[str, Tuple[str, str, float]]` — `market_id` → `(category, time_to_res, expiry_monotonic)`
- **CRITICAL**: 3-tuple. NEVER expand.
- Source: `markets` table via DB query, ★ S112: CLOB API fallback for misses
- Lookup: `WHERE condition_id = :mid OR id::text = :mid` (S110 fix)

### `trade_events_archive` (DB table, created by migration 056)
- Same schema as `trade_events` + `archive_reason` + `archived_at`
- Contains 1,322 archived rows from S110
- Indexed on `(bot_name, market_id, side)` and `archive_reason`

---

## VPS Deploy Pattern (IMPORTANT)

The VPS does NOT use git. Files are deployed via SCP:
```bash
# Copy file to VPS
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem <local_file> ubuntu@34.251.224.21:/tmp/<file>
# Install + restart
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo cp /tmp/<file> /opt/polymarket-ai-v2/<path> && sudo systemctl restart polymarket-ai"
```
`.env` changes via:
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo sed -i 's/OLD=val/NEW=val/' /opt/polymarket-ai-v2/.env && sudo systemctl restart polymarket-ai"
```
**WARNING**: `deploy.sh` and `systemctl restart` may overwrite `.env` changes. For persistent config changes, verify after every restart.

---

## Git State (local, NOT committed)

```
Modified (staged):
  base_engine/data/ingestion_error_capture.txt

Modified (unstaged):
  bots/esports_bot.py           (prior session esports changes — NOT this session)
  bots/mirror_bot.py            (S110 multi-factor + S112 CLOB fallback + F3 conviction)
  bots/elite_watchlist.py       (S112 num_trades + whale_trade_usd pass-through)  ★ NEW
  bots/weather_bot.py           (prior session weather changes)
  base_engine/learning/elite_reliability.py  (S110 log level bumps)
  config/settings.py            (prior session)
  tests/unit/test_mirror_bot_logic.py  (S110 mean() mock)
```
MirrorBot-specific changes: `bots/mirror_bot.py`, `bots/elite_watchlist.py`, `base_engine/learning/elite_reliability.py`, `tests/unit/test_mirror_bot_logic.py`

---

## Config Reference (Live VPS Values)

```
# MirrorBot-specific
MIRROR_MIN_CONFIDENCE=0.45
MIRROR_MIN_RELIABILITY=0.52
MIRROR_USE_CALIBRATION=false       ← S112: disabled (was true, re-enable ~2026-04-03)
MIRROR_MAX_POSITIONS=200
MIRROR_MAX_CONCURRENT_POSITIONS=200
MIRROR_MAX_PER_MARKET=400
MIRROR_DEAD_ZONE_LOW=0.30          ← default in settings.py
MIRROR_DEAD_ZONE_HIGH=0.50         ← default in settings.py
MIRROR_DEAD_ZONE_DAMPENER=0.50     ← default in settings.py
MIRROR_FAVORITE_PRICE_THRESHOLD=0.70  ← default in settings.py
MIRROR_FAVORITE_DAMPENER=0.40      ← default in settings.py
WATCHLIST_ENABLED=true
WATCHLIST_SIZE=1000

# System-wide
SIMULATION_MODE=true (paper trading)
capital=$20000, max_bet=$300, max_daily=$10000, kelly=0.25
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
7. **Same-side dedup (S109)**: Blocks re-entry on same market+side before confidence code. This also blocks F4 (multi-whale consensus).
8. **trade_events immutability trigger**: Must DISABLE/re-ENABLE for data cleanup.
9. **`_get_market_meta` lookup**: Uses `WHERE condition_id = :mid OR id::text = :mid` (S110 fix). Falls back to CLOB API (S112).
10. **Calibration T=2.0**: Fitted on old [0.55-0.58] range. Currently disabled. Re-fit needed before re-enabling.
11. **`category_trade_count` key format**: `(address.lower(), category.lower())` — case-sensitive match.
12. **deploy.sh may overwrite .env**: Config changes via sed on VPS WILL BE LOST on redeploy. Verify after every restart.
13. **`_infer_category()` is keyword-based**: Returns "unknown" for markets that don't match any keyword pattern. Located in `base_engine/data/data_ingestion.py`.
14. **CLOB API variable name**: Uses `_hc` (not `h`) in `_get_market_meta()` to avoid shadowing the hours variable.
15. **`whale_trade_usd` default=0.0**: All callers except `elite_watchlist.on_rtds_trade()` pass the default. F3 produces `_conv_adj=0.0` when `whale_trade_usd=0`.
16. **Resolution backfill queue**: 2,258+ MirrorBot markets backlogged. New positions may not resolve for days. Manual resolution script used this session.

---

## Resolution Backfill State

### Not broken — just slow:
- `_do_resolution_queue()` runs every ingestion cycle (~5min), batch=100
- Resolves ~30-50 per batch (some API failures, some not yet closed)
- MirrorBot: 2,258 unresolved in `traded_markets`
- WeatherBot: 1,193, EnsembleBot: 82, EsportsBot: 12
- Health check fires every ~70min: "12275 markets past end_date but unresolved"

### Manual resolution pattern (used this session):
```python
# Run on VPS with venv python
sudo /opt/polymarket-ai-v2/venv/bin/python3 << 'PYEOF'
import asyncio, httpx
from datetime import datetime
async def resolve():
    import asyncpg
    conn = await asyncpg.connect("postgresql://polymarket:polymarket_s46@localhost:6432/polymarket")
    rows = await conn.fetch(
        "SELECT market_id, side, entry_price, size FROM positions "
        "WHERE source_bot=$1 AND status=$2 AND opened_at >= $3",
        "MirrorBot", "open", datetime(2026, 3, 20, 16, 0, 0)
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        for r in rows:
            resp = await client.get(f"https://clob.polymarket.com/markets/{r['market_id']}")
            d = resp.json()
            if not d.get("closed"): continue
            tokens = d.get("tokens", [])
            winner_idx = next((i for i,t in enumerate(tokens) if t.get("winner")), None)
            if winner_idx is None: continue
            resolved = "YES" if winner_idx == 0 else "NO"
            won = (r["side"] == resolved)
            await conn.execute(
                "UPDATE positions SET status=$1, current_price=$2 WHERE market_id=$3 AND source_bot=$4 AND side=$5 AND status=$6",
                "closed", 1.0 if won else 0.0, r["market_id"], "MirrorBot", r["side"], "open"
            )
    await conn.close()
asyncio.run(resolve())
PYEOF
```
**NOTE**: This only updates `positions` table. It does NOT create RESOLUTION events in `trade_events`. The backfill queue handles that separately. For full P&L accounting, the resolution backfill must also run.

---

## The Multi-Factor Confidence Design Spec (Reference)

The full 4-factor design was provided by the user. Current implementation status:

| Factor | Description | Status | Notes |
|--------|------------|--------|-------|
| F1 | Category-specific Bayesian base | **LIVE (S110)** | Uses `_reliability_tracker._cat_cache`. Now fed by CLOB fallback (S112). |
| F2 | Price-implied edge (contrarian/consensus) | **LIVE (S110)** | YES<0.45 or NO>0.55 = contrarian boost. |
| F3 | Trade size conviction | **LIVE (S112)** | `whale_trade_usd / avg_trade_size`. +0.04 if >2x, -0.03 if <0.3x. |
| F4 | Multi-whale consensus | **BLOCKED** | Same-side dedup prevents counting. Needs dedup restructure. |

### Factor 4 Implementation Path (if attempted):
The same-side dedup at lines ~1119-1131 in `mirror_bot.py` runs BEFORE the confidence formula. It checks `_open_positions` for existing position on same `market_id + side` and returns `False` immediately. To implement F4:
1. Move dedup check AFTER confidence computation
2. If position already exists on same side, don't open new position but increment a `consensus_count` field
3. Use consensus count as a confidence boost: `+0.03 × min(consensus_count, 4)`
4. Risk: Must ensure no double-sizing — the position already exists, so F4 would only boost confidence for logging/analysis, not create a new position
5. Alternative: Track consensus count separately (e.g., dict `{(market_id, side): count}`) without touching dedup

---

## Outstanding Items (Full Priority List)

| Priority | Item | Status |
|----------|------|--------|
| **P1** | Monitor 3-factor formula performance (48-72h of data) | PENDING — run query above ~2026-03-23 |
| **P2** | Factor 4: multi-whale consensus | BLOCKED by same-side dedup |
| **P3** | Resolution backfill queue throughput (2,258 MirrorBot backlog) | Known limitation, manual workaround available |
| **P4** | `_infer_category()` coverage quality monitoring | PENDING — check logs for "unknown" categories |
| **P5** | Persist category in `event_data` JSONB on trade_events | Not started |
| **P6** | Re-enable FTS calibration after 2 weeks (~2026-04-03) | Calendar item |
| **P7** | Daily cap investigation ($20k exposure on $10k cap) | Not started |
| **P5 (from S110)** | Fill cooldown errors (EsportsBot, not MirrorBot) | Out of scope |

---

## P&L Reference

### Cumulative MirrorBot P&L (all-time, post-archive):
```
Side | Total | Wins | WR%   | Total P&L | Avg P&L
NO   |   631 |  272 | 43.1% | $7,952    | $12.70
YES  |   378 |  158 | 41.8% | $6,854    | $18.23
```

### P&L Formula (MANDATORY — see memory/feedback_pnl_math.md):
- `cost = entry_price × size` (ALL sides)
- `uPnL = (current - entry) × size` (ALL sides)
- WIN: `pnl = (1.0 - entry) × size`
- LOSS: `pnl = -(entry × size)`
- **NEVER invert for NO positions** — prices are token-specific
- Canonical script: `python scripts/bot_pnl.py MirrorBot hours`
- Data sources: `trade_events` (realized), `positions.unrealized_pnl` (mark-to-market)

---

## Rollback Plan

| Change | Rollback |
|--------|----------|
| CLOB API fallback | `git checkout HEAD -- bots/mirror_bot.py` + redeploy. Category falls back to "" (pre-S112 behavior). |
| F3 conviction signal | Same as above + `git checkout HEAD -- bots/elite_watchlist.py`. Confidence uses F1+F2 only (S110 behavior). |
| Calibration re-enable | `sudo sed -i 's/MIRROR_USE_CALIBRATION=false/MIRROR_USE_CALIBRATION=true/' /opt/polymarket-ai-v2/.env && sudo systemctl restart polymarket-ai` |
| Manual position resolution | No rollback needed — positions were legitimately closed on-chain. |

---

## S115 Cross-Bot Change: Shadow Fill Tracking (affects MirrorBot)

**Session**: S115 (same day, separate scope — all bots)
**Full handoff**: `AGENT_HANDOFF_SHADOW_FILLS_SESSION115_2026_03_21.md`

### What changed for MirrorBot:
- **paper_trading.py**: All theoretical slippage models REMOVED. BUY orders now fill at real VWAP from L2 orderbook walk.
- **order_gateway.py**: Pre-trade book walk + edge-at-VWAP gate. If `confidence <= VWAP`, trade rejected (paper AND live).
- **mirror_bot.py**: Added `self._scan_start_mono = _time.monotonic()` at `scan_and_trade()` entry (line 425). Added `"scan_start_mono"` to `_event_data` dict (line 1514).
- **elite_watchlist.py**: Added `self._mirror_bot._scan_start_mono = _start` at RTDS trade receipt (line 413) so RTDS fast-path also tracks latency.
- **shadow_fills table**: Every BUY signal recorded with full book snapshot + VWAP + edge. Resolution backfill computes retroactive P&L.
- **OrderBookTracker**: Now wired to both PaperTradingEngine and OrderGateway (was dead code before).
- **Net effect**: MirrorBot trades fill at real book prices. RTDS copy trades now have latency tracking. Edge gate catches price-moved-against-us before order submission.

### Review items:
- [ ] After 24h: `SELECT COUNT(*), AVG(latency_ms), AVG(book_walk_slippage) FROM shadow_fills WHERE bot_name='MirrorBot'` — verify latency tracking works for both scan and RTDS paths
- [ ] WebSocket orderbook upgrade — deferred, review if shadow data shows >1 cent avg staleness cost

## Session Learnings / Feedback

1. **Always check the data layer before declaring the formula works.** S110 deployed a correct formula, but 85% of inputs were empty. The formula was starved, not wrong.
2. **`.env` on VPS is volatile.** Any restart/deploy can overwrite it. Config changes must be verified after every restart.
3. **Resolution backfill queue can stall P&L visibility for days.** 2,258 market backlog at 100/batch. Manual resolution is the workaround for urgent P&L checks.
4. **Variable naming matters.** `h` was used for both hours and httpx client — caused a shadowing risk. Use `_hc` for httpx.
5. **CLOB API returns condition_id-based lookups, Gamma API uses numeric IDs.** For RTDS trades (which use condition_ids), CLOB is the right API.
