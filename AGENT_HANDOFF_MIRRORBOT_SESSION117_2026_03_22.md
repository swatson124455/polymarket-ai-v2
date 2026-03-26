# AGENT HANDOFF — MirrorBot Session 117 (2026-03-22)
## SCOPE: MirrorBot ONLY — no other bot changes

---

## SYSTEM OVERVIEW

This is a **15-bot Polymarket automated trading system**. MirrorBot copies elite traders via RTDS (Real-Time Data Stream) WebSocket feed. It's one of 3 active revenue-generating bots (MirrorBot, WeatherBot, EsportsBot). The system runs on an Ubuntu VPS at `34.251.224.21`, paper trading with `SIMULATION_MODE=true`.

### MirrorBot Architecture
- **RTDS feed**: WebSocket connection to Polymarket global trade stream. Receives ALL trades, filters for 500 elite traders from `EliteWatchlist`.
- **Elite Watchlist** (`bots/elite_watchlist.py`): Maintains top 500 traders ranked by P&L. Refreshes every 6h. Caches reliability scores per (trader, category) in Redis.
- **Multi-factor confidence formula** (`_execute_mirror_trade()` in `bots/mirror_bot.py`): `final = base(cat_wr) + price_adj + conv_adj`, capped at 0.75. Factors: F1 (category win rate), F2 (price adjustment from book depth), F3 (convergence of multiple traders).
- **Paper trading engine** (`base_engine/execution/paper_trading.py`): Simulates order execution with L2 book walk for realistic fills.
- **Position management**: `_open_positions` dict (in-memory), backed by `positions` table (DB). Restored on startup via `_restore_state_on_startup()`.
- **Trade events** (`trade_events` table): P&L authority. Partitioned by month. Immutability trigger `trg_trade_events_immutable` must be disabled/re-enabled for any DELETE/UPDATE.

### Key Config (LIVE VPS as of this session)
```
SIMULATION_MODE=true (paper trading)
MIRROR_USE_CALIBRATION=false (DISABLED this session — do NOT re-enable until ~Apr 5)
MIRROR_MIN_CONFIDENCE=0.45
MIRROR_MIN_RELIABILITY=0.52
MIRROR_MAX_POSITIONS=1000
MIRROR_MAX_CONCURRENT_POSITIONS=600
BOT_BANKROLL_CONFIG: MirrorBot capital=20000, kelly=0.25, max_bet=300, max_daily=999999 (UNCAPPED)
WATCHLIST_ENABLED=true, WATCHLIST_SIZE=1000 (top 500 used)
```

---

## WHAT WAS DONE THIS SESSION

### Phase 1: Data Purge (DB cleanup on VPS)
Bot was dead since ~Mar 19. Root cause: restart flood created 294 junk positions + calibration T=2.0 compressed all confidence to ~0.50 + dead zone dampener blocked the highest-volume winner tier (crypto 0.30-0.50).

| Action | Count | SQL |
|--------|-------|-----|
| Close flood positions | 294 | `UPDATE positions SET status='closed' WHERE opened_at >= '2026-03-21 20:00' AND opened_at < '2026-03-22 00:00'` |
| Delete flood ENTRY events | 240 | `DELETE FROM trade_events WHERE confidence < 0.52 AND event_time IN flood window` |
| Delete orphan RESOLUTION events | 352 + 325 + 325 | Resolutions with no matching ENTRY (kept regenerating from backfill) |
| Delete NULL P&L resolutions | 14 | `realized_pnl IS NULL` |

### Phase 2: Calibration Disabled
- Changed `MIRROR_USE_CALIBRATION=true` → `false` in `/opt/pa2-shared/.env`
- **Why**: Calibration fitted on wrong confidence range. The old dead zone dampener + calibration T=2.0 compressed all signals to ~0.50, creating a self-fulfilling prophecy of 50/50 outcomes. Need 2+ weeks of clean data before re-enabling.
- **When to re-enable**: ~April 5, after 713+ clean entries resolve and provide proper calibration training data.

### Phase 3: Code Changes (3 files)

#### `bots/mirror_bot.py`
1. **`_state_restored` guard** (line 194): Prevents `_restore_state_on_startup()` from running more than once. Was allowing restart floods when RTDS trades arrived before state was loaded.
2. **`_entered_market_sides` set** (line 94): New `set()` of `(market_id, side)` tuples, populated from ALL historical `trade_events` ENTRY records on startup. Used by opposing-side guard.
3. **Opposing-side guard hardened** (line ~1224): Now checks BOTH `_open_positions` (in-memory, fast) AND `_entered_market_sides` (historical, survives restarts). Logs `mirror_opposing_side_blocked_historical` for the new path.
4. **`_entered_market_sides.add()` on trade execution** (line ~1585): Adds new entries to the set when trades are placed, so the guard works within the same session too.

#### `bots/elite_watchlist.py`
5. **`_state_restored` check before RTDS dispatch** (early in `on_rtds_trade()`): If `mirror_bot._state_restored` is False, drops the trade. Prevents blind trading before position/exposure state is loaded.

#### `base_engine/learning/elite_reliability.py`
6. **Dead zone dampener REMOVED**: The 0.30-0.50 crypto price range dampener was fighting alpha. Historical data shows crypto up/down markets at 43% WR but 2.6x win/loss ratio = profitable. Dampener was halving sizes on the most profitable tier.

### Phase 4: Data Quality Cleanup (DB)

#### Category Backfill
- 398 entries backfilled with categories from `traded_markets.question` using keyword matching
- Categories: crypto (120), sports (143), esports (8), geopolitical (9), politics (2), economics (2), weather (3), unknown (204→classified as sports via `vs`/`O/U`/`Spread:` patterns)

#### Calibration Exclusion Flags
Every entry with bad data now has `event_data->>'calibration_exclude'` set:

| Flag value | Count | Reason |
|-----------|-------|--------|
| `true` | 328 | Both-sides entries (YES+NO on same market) |
| `no_category` | 2,285 | No category — can't be sliced, old data |
| `null_confidence` | 56 | Missing confidence value |
| `confidence_outlier` | 67 | Confidence >0.85 (pre-formula-cap era) |
| `duplicate_reentry` | 15 | Same market+side entered on different days |
| **CLEAN** | **713** | Usable for calibration |

**Calibration query filter**: `WHERE COALESCE(event_data->>'calibration_exclude', '') = ''`

### Phase 5: Daily Cap Uncapped
- `max_daily_usd` changed from $20,000 → $999,999
- **Why**: User wants to collect maximum data before making tuning decisions. Paper trading has zero cost.
- **Result**: Bot places every trade that passes confidence gate. Expect 50-100+ trades/day.

### Deploys
| Deploy | Content |
|--------|---------|
| `20260322_124042` | Phase 1-3 (purge + calibration disable + code fixes) |
| `20260322_200009` | Phase 4 (opposing-side guard + data quality) |

---

## CURRENT STATE (post-deploy)

### MirrorBot Health
- **Status**: ALIVE, trading actively
- **Open positions**: ~167 (was 403 before purge, 109 after purge, growing with new trades)
- **Daily exposure**: Uncapped. Will reset to $0 at midnight UTC.
- **RTDS**: Connected, dispatching 7000+ trades per scan cycle
- **Scan cycle time**: ~1s (healthy)
- **Elite refresh**: 500 traders loaded (first refresh timed out 10s, subsequent ones succeed)
- **`mirror_entered_sides_restored n=3326`**: Opposing-side guard loaded on startup

### P&L Snapshot (all-time MirrorBot)
```
Total entries: 3,341 (713 clean for calibration)
Crypto all-time: 141 resolved, 43.3% WR, +$7,291 (avg win $239, avg loss $91 = 2.6x ratio)
Post-deploy today: 31 resolved, 38.7% WR, -$1,638 (small sample, early)
```

### Data Integrity (verified clean)
- 0 duplicate entries (same-session dedup working)
- 0 orphan resolutions (entries exist for all)
- 0 ghost positions
- 0 wrong-sign P&L
- 0 future timestamps
- 0 stale open positions (reaper working)
- All new entries get category, confidence, and single-side per market

---

## WHAT COMES NEXT (ordered by priority)

### P0: Monitor Data Collection (now through ~Mar 29)
- Let the bot run uncapped for 5-7 days
- Collect resolution outcomes across all confidence buckets and categories
- **Do NOT tune thresholds yet** — data first, decisions second

### P1: Data Analysis (after ~500 new clean resolutions, ~Mar 29)
Run analysis queries to decide tuning:
```sql
-- Win rate + P&L by confidence bucket
SELECT
  CASE WHEN confidence < 0.50 THEN '<0.50'
       WHEN confidence < 0.55 THEN '0.50-0.55'
       WHEN confidence < 0.60 THEN '0.55-0.60'
       WHEN confidence >= 0.60 THEN '0.60+' END as bucket,
  COUNT(*),
  SUM(CASE WHEN r.realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
  ROUND(SUM(r.realized_pnl)::numeric, 2) as pnl
FROM trade_events e
JOIN trade_events r ON r.market_id = e.market_id AND r.bot_name = e.bot_name AND r.side = e.side AND r.event_type = 'RESOLUTION'
WHERE e.bot_name = 'MirrorBot' AND e.event_type = 'ENTRY'
  AND COALESCE(e.event_data->>'calibration_exclude', '') = ''
  AND e.event_time >= '2026-03-22 16:42:00'
GROUP BY 1 ORDER BY 1;

-- Same by category
-- Same by trader tier (cat_n buckets)
```

### P2: Re-enable Calibration (~Apr 5)
- Requires 500+ clean resolved entries with proper confidence distribution
- Retrain with `calibration_exclude` filter
- Set temperature based on reliability curve, not arbitrary T=2.0
- **Gate**: Do not re-enable if win rate by bucket doesn't show clear confidence→outcome correlation

### P3: Evaluate Rate Limiting
After data collection, decide if trades-per-minute pacing is needed:
- If 50+ trades/day produces good P&L → keep uncapped
- If low-confidence bucket (<0.52) is net negative → raise `MIRROR_MIN_CONFIDENCE`
- If too many trades in dead markets → add minimum volume gate (but CLAUDE.md says "CLOB volume=0 — Never use volume gates for MirrorBot")

### P4: Orphan Resolution Regeneration
Backfill keeps creating RESOLUTION events for deleted flood entries. Options:
- Add the ~240 flood market_ids to a backfill exclusion list
- Or just accept they're harmless (calibration ignores them — no matching ENTRY)

### P5: Elite Watchlist Timeout
First elite refresh times out (10s) on every restart. The stale list is used as fallback. Could increase timeout or pre-cache the list in Redis.

---

## CRITICAL TRAPS (MirrorBot-specific, DO NOT VIOLATE)

1. **`MIRROR_USE_CALIBRATION=false`** — Do NOT re-enable until ~Apr 5 with 500+ clean data points
2. **`max_daily_usd=999999`** — Intentionally uncapped for data collection. Do not reduce without user approval.
3. **`_entered_market_sides`** — New set in `__init__`. Must be populated from `trade_events` on startup AND updated on trade execution. If you touch `_restore_state_on_startup()`, preserve this.
4. **`calibration_exclude` in event_data** — All queries for calibration MUST filter: `WHERE COALESCE(event_data->>'calibration_exclude', '') = ''`
5. **`trade_events` immutability trigger** — Must disable on ALL partition tables (2026_01 through 2026_12 + default) before any UPDATE/DELETE, then re-enable. Transaction must COMMIT.
6. **Orphan resolutions regenerate** — Backfill runs every 30min. Deleting orphans is temporary. They're harmless for calibration (no matching ENTRY to join on).
7. **`_state_restored` guard** — Line 194. If this is False when RTDS trades arrive, they are dropped. Do not remove or weaken.
8. **Dead zone dampener is GONE** — Do not re-add. Crypto 0.30-0.50 price range is profitable (43% WR, 2.6x win/loss). The dampener was fighting alpha.
9. **Paper trading IS production** — Per CLAUDE.md. Every feature matters. Don't cut corners because "it's just paper."

---

## FILES MODIFIED THIS SESSION

| File | Changes |
|------|---------|
| `bots/mirror_bot.py` | `_entered_market_sides` set init, startup restore from trade_events, opposing-side guard hardened (historical check), set updated on trade execution |
| `bots/elite_watchlist.py` | `_state_restored` check before RTDS dispatch |
| `base_engine/learning/elite_reliability.py` | Dead zone dampener removed |

## VPS CONFIG CHANGES

| Config | Old | New |
|--------|-----|-----|
| `MIRROR_USE_CALIBRATION` | `true` | `false` |
| `BOT_BANKROLL_CONFIG.MirrorBot.max_daily_usd` | `20000` | `999999` |

## DB CHANGES (VPS)

| Action | Count |
|--------|-------|
| Positions closed (flood) | 294 |
| ENTRY events deleted (flood) | 240 |
| RESOLUTION events deleted (orphan) | 1,002 total |
| NULL P&L resolutions deleted | 14 |
| Categories backfilled | 398 |
| `calibration_exclude` flagged | 2,628 entries |
| Clean entries for calibration | 713 |

## TESTS
- **1668 passed, 0 failed** (full suite)
- Mirror-specific: 65 passed

## DEPLOYS
- `20260322_124042` — Code fixes + calibration disable
- `20260322_200009` — Opposing-side guard + data quality cleanup
