# AGENT HANDOFF — MirrorBot Session 111 (2026-03-20)

**Scope**: MirrorBot only. No cross-bot bleed. Single-bot session.
**Prior sessions**: S109 (P&L fixes), S110 (this session: multi-factor confidence + archive + dampeners)
**Status**: ALL S110 CHANGES DEPLOYED AND VERIFIED LIVE

---

## What Was Done This Session (S110)

### Problem Statement
56% of ALL MirrorBot trades (1,500 of 2,690) clustered at confidence 0.526 because:
1. `EliteWatchlist` assigns flat 0.55 base confidence (efficiency bonus ≈ 0 for most whales)
2. FTS calibration compresses 0.55 → 0.526 (sigmoid with T=2.0)
3. All 1,500 trades got IDENTICAL confidence → identical Kelly sizing → 32% win rate

Root cause: Pipeline was starved — one input dimension (efficiency score) feeding a compressive calibrator. The entire confidence system was a point mass, not a distribution.

### Changes Deployed (4 items)

#### 1. Archive Migration 056 — Run on VPS
- **File**: `schema/migrations/056_mirror_archive_bad_data.sql`
- Archived 1,322 rows of bad data from `trade_events` → `trade_events_archive`
  - 630 below-gate trades (avg confidence < 0.45)
  - 648 duplicate ENTRY events
  - 26 orphan RESOLUTION events (no matching ENTRY)
  - 14 null-P&L RESOLUTION events
  - 4 EXIT events for below-gate positions
- Closed 73 below-gate open positions in `positions` table
- Fully reversible: `INSERT INTO trade_events SELECT ... FROM trade_events_archive`

#### 2. Multi-Factor Confidence Formula — `bots/mirror_bot.py` lines 1225-1270
**Replaced** the old domain drift penalty (lines 1225-1236) with a 2-factor formula:

**Factor 1 — Category-Specific Bayesian Base**:
```python
_cat_wr = self._reliability_tracker.mean(trader_address, side, category=category)
_cat_n = self._reliability_tracker.category_trade_count(trader_address, category)
_shrinkage = _cat_n / (_cat_n + 20)  # pseudocount=20
_base = 0.50 + _shrinkage * (_cat_wr - 0.50)
if category and _cat_n < 10:
    _base = min(_base, 0.52)  # safety net for unfamiliar categories
```

**Factor 2 — Price-Implied Edge**:
```python
_is_contrarian = (YES and price < 0.45) or (NO and price > 0.55)
if _is_contrarian:
    _price_adj = _price_dev * 0.15      # boost contrarian
else:
    _price_adj = -(_price_dev * 0.15 * 0.3)  # slight penalty consensus
```

**Final**: `confidence = clamp(0.35, 0.75, _base + _price_adj)`

Logs as `mirror_multifactor` with all components.

**Why it works**: `_reliability_tracker._cat_cache` has 8,629 whale+category entries from `trades` table. Refreshed every scan cycle (~45s). Per-whale per-category Bayesian posterior win rate. Zero latency cost — all data already in memory.

**Skipped factors** (infrastructure not available):
- Factor 3 (multi-whale consensus): Same-side dedup (lines 1119-1131) blocks second whale entry BEFORE confidence code runs
- Factor 4 (conviction/whale trade size): `_execute_mirror_trade()` doesn't receive whale's trade size

#### 3. Dampeners Wired — `bots/mirror_bot.py` lines 1336-1351
```python
# Dead zone dampener: price 0.30-0.50 → size *= 0.50
# Favorite dampener: price > 0.70 → size *= 0.40
```
Config keys already existed in `config/settings.py` lines 387-391. This session wired the consumers.

#### 4. Category Lookup Fix — `bots/mirror_bot.py` line 600
**Bug found live**: `_get_market_meta()` used `WHERE id = :mid` but RTDS passes condition_ids.
**Fix**: `WHERE condition_id = :mid OR id::text = :mid` (matching `_get_token_side` pattern).
**Impact**: Without this fix, `category=""` for ~80% of trades → Factor 1 always fell back to base=0.50 (defeating the entire multi-factor formula).

#### 5. Diagnostic Logging — `base_engine/learning/elite_reliability.py` lines 75, 77
- `Category reliability load failed` bumped from debug → warning
- `Elite reliability refreshed` bumped from debug → info (shows `n_users=X, n_category_entries=Y`)

#### 6. Test Updates — `tests/unit/test_mirror_bot_logic.py`
- Added `bot._reliability_tracker.mean = MagicMock(return_value=0.60)` to 2 test functions
- Lines 758 and 778

### VPS Config State
```
MIRROR_USE_CALIBRATION=true      ← STILL TRUE (see Pending Actions)
MIRROR_MIN_CONFIDENCE=0.45       ← unchanged
MIRROR_DEAD_ZONE_LOW=0.30        ← default in settings.py
MIRROR_DEAD_ZONE_HIGH=0.50       ← default in settings.py
MIRROR_DEAD_ZONE_DAMPENER=0.50   ← default in settings.py
MIRROR_FAVORITE_PRICE_THRESHOLD=0.70  ← default in settings.py
MIRROR_FAVORITE_DAMPENER=0.40    ← default in settings.py
```

---

## Live Verification Results (2026-03-20 ~03:48 UTC)

### Multi-factor confidence producing REAL variance:
| Trader | Category | cat_n | cat_wr | base | price_adj | final |
|--------|----------|-------|--------|------|-----------|-------|
| 0x507E52ef | sports | 7,240 | 0.658 | 0.658 | -0.013 | 0.644 |
| 0xdE17f714 | crypto | 3,991 | 0.729 | 0.728 | -0.008 | 0.720 |
| 0x204f72f3 | sports | 18,686 | 0.623 | 0.623 | +0.034 | 0.657 |
| 0x161A7F66 | sports | 287 | 0.732 | 0.717 | -0.002 | 0.714 |
| 0xDe0463Ea | sports | 371 | 0.764 | 0.750 | +0.015 | 0.750 |
| 0x6ac5BB06 | sports | 6,162 | 0.644 | 0.643 | +0.037 | 0.681 |

**Old**: Point mass at 0.526 for ALL trades
**New**: Real spread [0.48-0.75] based on per-whale per-category performance

### Dampeners firing:
- `mirror_dead_zone_dampened: price=0.390, size *= 0.50` ✓
- `mirror_favorite_dampened: price=0.820, size *= 0.40` ✓

### Reliability tracker populated:
- `Elite reliability refreshed n_category_entries=8629 n_users=3687` ✓

### Some trades still show `category=""` (~30-40%):
- Markets not yet ingested into `markets` table → `_get_market_meta()` returns empty
- These trades default to base=0.50 (uninformative prior) — correct behavior
- Data coverage issue, not a code bug

---

## Pending Actions (For Next Session)

### P1: Disable FTS Calibration on VPS
**Why**: FTS temperature (T=2.0) was fitted against the OLD [0.55-0.58] input range. With new [0.35-0.75] range, it's still compressing (sigmoid(logit(x)/2.0)). The live results showed good variance even with calibration on, but disabling it will produce cleaner output until T is re-fitted.
**How**:
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
sudo sed -i 's/MIRROR_USE_CALIBRATION=true/MIRROR_USE_CALIBRATION=false/' /opt/polymarket-ai-v2/.env
sudo systemctl restart polymarket-ai
```
**Re-enable after ~2 weeks** when prediction_log has enough data at the new confidence range to re-fit T.
**NOTE**: The prior session's sed change was LOST — likely overwritten by deploy.sh which copies `.env` from the release package. To make it persistent, ALSO change it in the local repo's `.env` or add to deploy script.

### P2: Confidence Histogram After 24h
```bash
sudo -u postgres psql -d polymarket -c "
  SELECT ROUND(confidence::numeric, 2) as conf, COUNT(*)
  FROM trade_events WHERE bot_name='MirrorBot' AND event_type='ENTRY'
    AND event_time >= '2026-03-20'
  GROUP BY 1 ORDER BY 1;"
```
Expected: spread across [0.45, 0.75], NOT a spike at any single value.

### P3: Improve Market Category Coverage
~30-40% of RTDS trades have `category=""` because those markets aren't in the `markets` table. The ingestion pipeline only pulls top-500 markets every 5 minutes. Whales trade on markets outside the top-500.
**Options**:
- Expand ingestion to top-1000 or all active markets
- Cache category from Gamma API on first miss in `_get_market_meta()`
- Accept the ~30-40% fallback to base=0.50 (current behavior)

### P4: Monitor Win Rate by Confidence Bucket
After 48-72h of data:
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
  ROUND(AVG(realized_pnl)::numeric, 2) as avg_pnl
FROM trade_events
WHERE bot_name='MirrorBot' AND event_type='RESOLUTION'
  AND event_time >= '2026-03-20'
GROUP BY 1 ORDER BY 1;
```
**Goal**: Higher confidence buckets should show higher win rates. If not, the formula needs tuning.

### P5: Fill Cooldown Errors (Pre-existing)
```
Fill cooldown: 3 consecutive failures
```
Paper trade circuit breaker firing on specific market IDs. Pre-existing issue from before S110. Not blocking trades broadly.

### P3 (from MEMORY.md): NO vs YES Asymmetry
72% WR on NO positions vs 39% on YES. Confirmed pattern. Monitor whether multi-factor formula changes this asymmetry. If persistent, consider weighting NO confidence higher.

---

## Files Modified This Session

| File | Change | Status |
|------|--------|--------|
| `bots/mirror_bot.py` | Multi-factor formula (lines 1225-1270), dampeners (lines 1336-1351), category lookup fix (line 600) | Deployed to VPS |
| `base_engine/learning/elite_reliability.py` | Log levels: debug→info (refresh), debug→warning (cat failure) | Deployed to VPS |
| `tests/unit/test_mirror_bot_logic.py` | Added `mean()` mock to 2 tests (lines 758, 778) | Local only (tests pass) |
| `schema/migrations/056_mirror_archive_bad_data.sql` | Run on VPS (already existed) | Executed |
| VPS `.env` | `MIRROR_USE_CALIBRATION` → still `true` (change was lost) | PENDING |

### Files NOT modified (read-only reference):
- `bots/elite_watchlist.py` — RTDS handler, base confidence assignment (line 385), category=None passed (line 398)
- `base_engine/learning/elite_reliability.py` — `mean()`, `category_trade_count()`, `_cat_cache` structure
- `config/settings.py` — dampener config keys (lines 387-391), calibration default (line 365)

---

## Architecture Reference (MirrorBot Confidence Pipeline)

```
RTDS Trade Event (wss://ws-live-data.polymarket.com)
  ↓
elite_watchlist.on_rtds_trade()
  ├── Fast-reject: proxyWallet not in watchlist → skip
  ├── Dedup: transactionHash or composite key
  ├── Wash detection: 3+ round-trips in 24h → block
  ├── Position cap: _can_open_position()
  ├── Base confidence: 0.55 + efficiency_bonus (capped 0.70)  ← UPSTREAM (unchanged)
  └── _execute_mirror_trade(category=None, source="rtds")
        ↓
mirror_bot._execute_mirror_trade()
  ├── Tier 0: blocklist, cooldown (in-memory, <0.01ms)
  ├── Category resolve: _get_market_meta(condition_id) → category
  │     └── S110 FIX: WHERE condition_id = :mid OR id::text = :mid
  ├── Category blocklist
  ├── Position cap: _can_open_position()
  ├── Same-side dedup: blocks re-entry on same market+side (S109)
  ├── Reliability multiplier: likelihood_ratio() → lr_mult
  │
  ├── ★ S110: Multi-factor confidence (REPLACES domain drift) ★
  │     Factor 1: _base = 0.50 + shrinkage(cat_n, 20) × (cat_wr - 0.50)
  │     Factor 2: price_adj = contrarian boost / consensus penalty
  │     Final:    confidence = clamp(0.35, 0.75, _base + price_adj)
  │
  ├── [OPTIONAL] FTS Calibration: sigmoid(logit(confidence) / T)
  │     Currently ON (T=2.0). Plan: disable for ~2 weeks.
  ├── MIN_CONFIDENCE gate (0.45): reject if below
  │
  ├── Kelly sizing: kelly_full = (conf × b - q) / b
  ├── Extreme-price dampener (existing): gray zone
  ├── ★ S110: Dead zone dampener: price 0.30-0.50 → size × 0.50 ★
  ├── ★ S110: Favorite dampener: price > 0.70 → size × 0.40 ★
  ├── Per-market cap: 1% of capital
  └── place_order(side=YES/NO)
```

---

## Key Data Structures

### `_reliability_tracker._cat_cache` (in-memory, refreshed every scan ~45s)
- Type: `Dict[Tuple[str, str], Dict]` — `(address.lower(), category.lower())` → Beta params
- Source: `trades` table JOIN `markets` table, filtered to resolved markets
- Size: ~8,629 entries (whale+category combos)
- Contains: `alpha_yes, beta_yes, alpha_no, beta_no, yes_total, no_total`
- Used by: `mean()`, `category_trade_count()`, `likelihood_ratio()`

### `_market_meta_cache` (in-memory, TTL-based)
- Type: `Dict[str, Tuple[str, str, float]]` — `market_id` → `(category, time_to_res, expiry_monotonic)`
- **CRITICAL**: 3-tuple. NEVER expand.
- Source: `markets` table via `_get_market_meta()`
- Lookup: `WHERE condition_id = :mid OR id::text = :mid` (S110 fix)

### `trade_events_archive` (new table, created by migration 056)
- Same schema as `trade_events` + `archive_reason` + `archived_at`
- Indexed on `(bot_name, market_id, side)` and `archive_reason`
- Contains 1,322 archived rows from this session

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
**WARNING**: `deploy.sh` may overwrite `.env` changes. For persistent config, also update the local repo's `.env` template.

---

## Git State (local, NOT committed)
```
Modified (staged):
  base_engine/data/ingestion_error_capture.txt
Modified (unstaged):
  bots/esports_bot.py           (295 lines, S109 esports changes — NOT this session)
  bots/mirror_bot.py            (73 lines added — S110 multi-factor + dampeners + category fix)
  bots/weather_bot.py           (50 lines, prior session weather changes)
  base_engine/learning/elite_reliability.py  (6 lines, log level bumps)
  config/settings.py            (2 lines, prior session)
  tests/unit/test_mirror_bot_logic.py  (4 lines, mean() mock)
```
MirrorBot-specific changes: `bots/mirror_bot.py`, `base_engine/learning/elite_reliability.py`, `tests/unit/test_mirror_bot_logic.py`

---

## Config Reference (Live VPS Values)

```
# MirrorBot-specific
MIRROR_MIN_CONFIDENCE=0.45
MIRROR_MIN_RELIABILITY=0.52
MIRROR_USE_CALIBRATION=true        ← SHOULD BE false (pending)
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
```

---

## Critical Traps (MirrorBot-Specific, DO NOT BREAK)

1. **`_market_meta_cache`**: 3-tuple `(cat, ttr, expiry)`. NEVER expand.
2. **Entry price**: Uses CURRENT market price from `get_market_from_index()`, NOT trader's fill.
3. **`_open_positions` on restart**: Clears in-memory; re-enters by EOD UTC.
4. **CLOB volume=0**: Never use volume gates for MirrorBot.
5. **RTDS envelope**: Must unwrap `data.get("payload", data)`.
6. **RTDS dedup**: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`.
7. **Same-side dedup (S109)**: Blocks re-entry on same market+side before confidence code.
8. **trade_events immutability trigger**: Must DISABLE/re-ENABLE for data cleanup.
9. **`_get_market_meta` lookup**: Uses `WHERE condition_id = :mid OR id::text = :mid` (S110 fix).
10. **Calibration T=2.0**: Fitted on old [0.55-0.58] range. With new [0.35-0.75], re-fit needed.
11. **`category_trade_count` key format**: `(address.lower(), category.lower())` — case-sensitive match.
12. **deploy.sh may overwrite .env**: Config changes via sed on VPS can be lost on redeploy.

---

## Outstanding Items (Full System, MirrorBot Relevant)

| Priority | Item | Status |
|----------|------|--------|
| **P1** | Disable FTS calibration (`MIRROR_USE_CALIBRATION=false`) | PENDING — do this first |
| **P2** | Confidence histogram after 24h | PENDING — run query above |
| **P3** | Market category coverage (~30-40% empty) | Known limitation |
| **P3** | NO vs YES asymmetry (72% vs 39% WR) | Monitor |
| **P4** | Win rate by confidence bucket after 48-72h | PENDING |
| **P5** | Fill cooldown errors (pre-existing) | Low priority |
| **P5** | Re-enable calibration after 2 weeks with new data | Calendar: ~2026-04-03 |

---

## P&L Reference

**Pre-archive**: MirrorBot realized +$20,247 (fantasy, 100% fills)
**Post-archive (S110)**: ~+$21,665 (1,322 bad rows archived, 73 positions closed)

**P&L formula (MANDATORY — see memory/feedback_pnl_math.md)**:
- `cost = entry_price × size` (ALL sides)
- `uPnL = (current - entry) × size` (ALL sides)
- NEVER invert for NO positions — prices are token-specific
- Canonical script: `python scripts/bot_pnl.py MirrorBot hours`

---

## Rollback Plan

| Change | Rollback |
|--------|----------|
| Multi-factor formula | `git checkout HEAD -- bots/mirror_bot.py` + redeploy |
| Dampeners | Same as above (in same file) |
| Category lookup fix | Same as above |
| Calibration disable | `MIRROR_USE_CALIBRATION=true` in VPS `.env` + restart |
| Archive migration | `INSERT INTO trade_events SELECT <all cols except archive_reason, archived_at> FROM trade_events_archive; DROP TABLE trade_events_archive;` |
| Reliability log levels | `git checkout HEAD -- base_engine/learning/elite_reliability.py` + redeploy |
