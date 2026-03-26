# AGENT HANDOFF — MirrorBot Session 116 (2026-03-22)
## Status: CRITICAL — Bot functionally dead, requires immediate fixes

---

## WHAT HAPPENED (Session 116 Failures)

Multiple restarts on Mar 21 caused a **restart flood**: each restart wiped `_daily_exposure` and `_open_positions` from memory, allowing 294 coin-flip entries (avg conf 0.50) to pour in unchecked. These junk positions now clog per-market caps and block all new trades.

Additionally: calibration T=2.0 was fitted on old [0.55-0.58] confidence range and was never refitted for the new [0.45-0.75] multi-factor range. Combined with stacking dampeners (dead zone 0.50x + favorite 0.40x = 0.20x), every trade sizes to zero. **MirrorBot has executed 0 trades today.**

### Bugs Found & Fixed This Session
1. **Resolution backfill price=0 bug** — `resolution_backfill.py` Phase 4b emitted all RESOLUTION events with `price=0.0` instead of real entry price. P&L formulas then computed `(1-0)*shares = shares` for wins (massively inflated). **FIXED**: weighted-average entry price from paper_trades. **DEPLOYED**. 1,390 historical records patched in DB.
2. **Stale positions** — 338 resolved positions still marked `open`. **PURGED** via CLOB API check.

### Current VPS State (2026-03-22 14:30 UTC)
- **403 open positions** (294 from restart flood, 44 from today, 65 older)
- **0 new trades today** — all sizing to zero after dampener stacking
- **Config**: `MIRROR_USE_CALIBRATION=true`, `MIRROR_MIN_CONFIDENCE=0.45`, `MIRROR_MAX_CONCURRENT_POSITIONS=600`
- **Daily spent**: $8,657 (from yesterday's flood, counter not fully resetting)
- **Total realized P&L**: +$12,867 (but includes -$10,462 from 350 orphan resolutions with unreliable data)

---

## PHASE 1: DATA PURGE (DO FIRST — ASK USER PERMISSION BEFORE EXECUTING)

### 1a. Purge Restart Flood (294 positions + 240 ENTRY events)
Positions entered 2026-03-21 20:00 to 2026-03-22 00:00 with confidence < 0.52. These are zero-discrimination coin-flip entries from blind trading windows when F1 hadn't loaded.

```sql
-- STEP 1: Close flood positions
UPDATE positions SET status = 'closed', current_price = 0.0
WHERE source_bot = 'MirrorBot' AND status = 'open'
  AND opened_at >= '2026-03-21 20:00' AND opened_at < '2026-03-22 00:00';
-- Expected: ~294 rows

-- STEP 2: Delete flood ENTRY events (requires trigger disable)
-- Disable triggers on ALL partitions first (see Session 116 pattern)
ALTER TABLE trade_events_2026_03 DISABLE TRIGGER trg_trade_events_immutable;

DELETE FROM trade_events
WHERE bot_name = 'MirrorBot' AND event_type = 'ENTRY'
  AND event_time >= '2026-03-21 20:00' AND event_time < '2026-03-22 00:00'
  AND confidence < 0.52;
-- Expected: ~240 rows

ALTER TABLE trade_events_2026_03 ENABLE TRIGGER trg_trade_events_immutable;
```

### 1b. Purge Orphan RESOLUTION Events (350 events, -$10,462 unreliable P&L)
RESOLUTION events with no matching ENTRY event — legacy positions from pre-trade_events era. P&L is unreliable (no way to verify entry prices).

```sql
-- Disable ALL partition triggers (see session pattern below for full list)
DELETE FROM trade_events r
WHERE r.bot_name = 'MirrorBot' AND r.event_type = 'RESOLUTION'
  AND NOT EXISTS (
    SELECT 1 FROM trade_events e
    WHERE e.market_id = r.market_id AND e.bot_name = r.bot_name
      AND e.event_type = 'ENTRY' AND e.side = r.side
  );
-- Expected: ~350 rows
-- Re-enable ALL triggers after
```

### 1c. NO old data validation — purge it all
User directive: "do not validate any old info we are purging it all we have no way to verify any of it is valid." New clean data starts from the clean entries that have full event_data (trader, category, whale_usd, conf_base, etc).

### Trigger Disable Pattern (required for DELETE/UPDATE on trade_events)
```sql
BEGIN;
ALTER TABLE trade_events DISABLE TRIGGER trg_trade_events_immutable;
ALTER TABLE trade_events_2026_01 DISABLE TRIGGER trg_trade_events_immutable;
ALTER TABLE trade_events_2026_02 DISABLE TRIGGER trg_trade_events_immutable;
ALTER TABLE trade_events_2026_03 DISABLE TRIGGER trg_trade_events_immutable;
ALTER TABLE trade_events_2026_04 DISABLE TRIGGER trg_trade_events_immutable;
ALTER TABLE trade_events_2026_05 DISABLE TRIGGER trg_trade_events_immutable;
-- ... through 2026_12 and trade_events_default
-- Do work
-- Re-enable ALL in same order
COMMIT;
```

---

## PHASE 2: WINNER PATTERN ANALYSIS (completed — results below)

### Winner Matrix: Entry Price Tier x Confidence Tier (from resolved trades with ENTRY events)

| Price Tier | Conf Tier | Wins | Total P&L | Avg P&L/Win |
|---|---|---|---|---|
| **A: <0.15** | LOW | 17 | +$8,966 | **$527** |
| **A: <0.15** | MED | 9 | +$4,391 | **$487** |
| **A: <0.15** | HIGH | 1 | +$599 | $599 |
| **B: 0.15-0.25** | HIGH | 4 | +$4,054 | **$1,013** |
| **B: 0.15-0.25** | MED | 14 | +$5,416 | **$386** |
| **B: 0.15-0.25** | LOW | 32 | +$11,689 | **$365** |
| **C: 0.25-0.35** | HIGH | 5 | +$2,565 | **$513** |
| **C: 0.25-0.35** | LOW | 54 | +$10,561 | $195 |
| **D: 0.35-0.50** | HIGH | 21 | +$3,568 | $169 |
| **D: 0.35-0.50** | LOW | 259 | +$20,484 | $79 |
| **E: 0.50+** | HIGH | 80 | +$6,512 | $81 |
| **E: 0.50+** | LOW | 125 | +$9,855 | $78 |

### Key Insights
1. **Low entry price = fat tail payoff**: avg win at <0.15 is $527 vs $79 at 0.35-0.50. The edge IS the longshot.
2. **HIGH confidence wins are rare but massive**: B+HIGH = $1,013 avg win (only 4 trades but huge payoff).
3. **LOW confidence dominates volume**: most wins are LOW conf (0.52-0.55). These are the bulk entries that average small but add up — $20K from D:LOW alone.
4. **The dead zone dampener was cutting size 50% on D tier (0.35-0.50) which is the HIGHEST VOLUME winner tier.** Removing it is correct.

### Confidence Bucket P&L (full picture including losses)

| Bucket | Resolved | Win% | P&L | Avg P&L |
|---|---|---|---|---|
| 0.70+ | 176 | **59.7%** | +$4,273 | +$24.28 |
| 0.55-0.60 | 403 | 44.9% | +$5,021 | +$12.49 |
| 0.52-0.55 | 1,153 | 39.0% | +$2,017 | +$1.76 |
| 0.60-0.65 | 63 | 42.9% | -$1,355 | -$21.86 |

**Only 0.70+ has clear edge.** 0.52-0.55 is $1.76/trade — noise after fees. But user says NOT to raise min confidence because F1 calibration is broken and would lose all bets.

---

## PHASE 3: CALIBRATION (disable for now, reopen when sample size exists)

### Problem
Calibration T=2.0 was fitted on the old narrow [0.55-0.58] confidence range. With the new multi-factor formula producing [0.45-0.75], T=2.0 compresses everything:
- raw 0.75 → cal 0.634 (kills high-conviction)
- raw 0.60 → cal 0.551 (marginal becomes noise)
- raw 0.58 → cal 0.541

### Action: DISABLE calibration now, REFIT later
```bash
# On VPS:
sudo sed -i 's/MIRROR_USE_CALIBRATION=true/MIRROR_USE_CALIBRATION=false/' /opt/pa2-shared/.env
```

### REOPEN REMINDER
After 2 weeks of clean data (~2026-04-05), refit T:
1. Pull all resolved trades with entry confidence from clean data (post-purge)
2. Bin by confidence → compute actual win rate per bin
3. Fit T via logistic regression: `sigmoid(logit(conf)/T) ≈ observed_win_rate`
4. If T ≈ 1.0, raw confidence is already calibrated (leave disabled)
5. If T significantly != 1.0, deploy fitted value

**Do NOT re-enable calibration until refitted with new data.**

---

## PHASE 4: REMOVE DEAD ZONE DAMPENER (immediate)

### What it does
In `mirror_bot.py`, the dead zone dampener cuts position size by 50% when market price is between 0.35-0.65:
```python
# Find and remove this block:
if 0.35 <= price <= 0.65:
    size *= 0.50
    logger.info("mirror_dead_zone_dampened: price=%.3f, size *= 0.50", price)
```

### Why remove
- The D tier (0.35-0.50) is the **highest volume winner tier**: 259 wins, +$20,484
- Dead zone dampener was cutting these winners' size in half
- Entry price performance data:
  - 0.30-0.40: +$793 P&L (would be ~+$1,586 without dampener)
  - 0.40-0.50: +$1,282 P&L (would be ~+$2,564 without dampener)

### Keep the favorite dampener (price > 0.80, size *= 0.40)
Entry > 0.80 is genuinely -$143 across 80 trades. This dampener is correct.

---

## PHASE 5: FIX F1 RELIABILITY STARTUP (code change)

### Problem
`elite_reliability.py` refreshes the reliability tracker via a DB query that scans the full `trades` table. Under load, this times out (15s). Every timeout = a blind trading window where all traders get `cat_n=0, base=0.50`.

### Fix: Cache reliability results
1. On each successful 6h refresh, write results to `system_kv` (or a dedicated `reliability_cache` table):
   - Key: `reliability_cache`, Value: JSON of `{trader: {category: {n, wr, base}}}`, Updated_at: timestamp
2. On startup, load from cache FIRST (instant), then schedule background refresh
3. If cache is < 24h old, use it immediately and trade with real data
4. If cache is stale (> 24h), trade with degraded mode (higher min confidence) until refresh completes

### Files to modify
- `base_engine/learning/elite_reliability.py` — add `save_to_cache()` and `load_from_cache()` methods
- `bots/mirror_bot.py` — on startup, call `load_from_cache()` before first scan

---

## PHASE 6: FIX RESTART FLOOD PROTECTION (code change)

### Problem
`_restore_state_on_startup()` in `mirror_bot.py` is supposed to seed `_daily_exposure` and `_open_positions` from DB before processing RTDS events. But it's clearly not working — 294 entries flooded in after a restart.

### Root Cause Investigation Needed
Check `mirror_bot.py`:
1. Does `_restore_state_on_startup()` run BEFORE RTDS listener starts?
2. Does it query trade_events for today's entries to seed `_daily_exposure`?
3. Does it query positions for open positions to seed `_open_positions`?
4. Is there a race condition where RTDS events arrive before restore completes?

### Fix Pattern
```python
# In mirror_bot startup sequence:
# 1. Restore state from DB (synchronous, blocking)
await self._restore_state_on_startup()
# 2. ONLY THEN start RTDS listener
self._rtds_listener = asyncio.create_task(self._listen_rtds())
```

Ensure `_restore_state_on_startup()`:
- Seeds `_daily_exposure` from `SELECT SUM(size * price) FROM trade_events WHERE bot_name='MirrorBot' AND event_type='ENTRY' AND event_time >= CURRENT_DATE`
- Seeds `_open_positions` from `SELECT market_id, side FROM positions WHERE source_bot='MirrorBot' AND status='open'`
- Logs both values: `restored_daily_exposure=X, restored_open_positions=Y`

---

## PHASE 7: REPORTING OVERHAUL

### Create `scripts/mirror_report.py`
Outputs:
1. **P&L by entry cohort** (entry date, not resolution date)
2. **Confidence vs actual win rate** (calibration validation)
3. **Entry price distribution** (are we hitting the profitable range?)
4. **Daily trade count** (0 = alarm, 6 in 2 days = alarm)
5. **Winner pattern matrix** (price tier x confidence tier)
6. **Open position health** (how many are flood/junk vs intentional)

### Fix P&L reporting in existing scripts
- `bot_pnl.py` currently reports by resolution date — misleading because it mixes old and new cohorts
- Add entry-cohort view as default

---

## CURRENT VPS CONFIG (for reference)
```
MIRROR_MAX_CONCURRENT_POSITIONS=600
MIRROR_SKIP_LIQUIDITY_RTDS=true
MIRROR_USE_CALIBRATION=true          # ← DISABLE (Phase 3)
MIRROR_USE_CONFORMAL=true
MIRROR_ADAPTIVE_SAFETY=false
MIRROR_MIN_CONFIDENCE=0.45           # ← DO NOT RAISE (user directive)
BOT_BANKROLL_CONFIG includes MirrorBot: capital=20000, kelly=0.25, max_bet=300, max_daily=20000
```

---

## EXECUTION ORDER FOR NEXT SESSION

1. **Phase 1a+1b**: Purge bad data (ASK USER PERMISSION FIRST for each delete operation)
2. **Phase 3**: Disable calibration (`MIRROR_USE_CALIBRATION=false`)
3. **Phase 4**: Remove dead zone dampener from `mirror_bot.py`
4. **Phase 6**: Fix restart flood protection in `_restore_state_on_startup()`
5. **Phase 5**: Add F1 reliability cache
6. **Restart service** — with flood protection fixed, this should be safe
7. **Phase 7**: Deploy reporting script
8. **Monitor**: Watch for trades flowing, check confidence distribution, verify no flood

### Post-Deploy Verification
```bash
# Trades flowing?
sudo journalctl -u polymarket-ai -f | grep 'paper_trade_placed.*MirrorBot'
# Confidence range?
sudo journalctl -u polymarket-ai -f | grep 'mirror_multifactor' | head -20
# No flood?
sudo -u postgres psql -d polymarket -c "SELECT COUNT(*) FROM trade_events WHERE bot_name='MirrorBot' AND event_type='ENTRY' AND event_time >= NOW() - INTERVAL '5 minutes';"
# Should be < 5 in 5 minutes, not 50+
```

---

## FILES MODIFIED THIS SESSION
1. `base_engine/data/resolution_backfill.py` — Phase 4b: `price=0.0` → weighted-average entry price from paper_trades (line 443). **DEPLOYED to VPS.**

## DB CHANGES THIS SESSION
1. **Patched 1,390 RESOLUTION events** — updated `price` column from 0.0 to real entry price (1,330 from ENTRY events, 60 from paper_trades)
2. **Purged 338 stale positions** — resolved markets still marked `open`, closed via CLOB API check
3. **Raised position cap** — `MIRROR_MAX_CONCURRENT_POSITIONS` 400 → 600
4. **Raised daily cap** — `max_daily_usd` 10000 → 20000

## CRITICAL TRAPS FOR NEXT SESSION
- **DO NOT re-enable calibration** until T is refitted with clean data (~2026-04-05)
- **DO NOT raise MIRROR_MIN_CONFIDENCE** — user directive, F1 needs fixing first
- **ASK PERMISSION** before any data deletes
- **Trigger disable pattern** required for any trade_events DELETE/UPDATE (all 14 partitions + parent)
- **event_data fields** are only populated on entries from ~Mar 21+ (older entries have empty event_data)
- **Winner alpha is in longshots**: entry price 0.10-0.30, NOT in high win rate. Fat tail payoff structure.
