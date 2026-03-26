# EsportsBot Session 132 — Data Integrity Fix

**Date**: 2026-03-25
**Scope**: EsportsBot only (no shared module changes)
**Deploy**: `20260325_202511` (Fix 1+4+5 code), manual SQL (Fix 2+3 data repair)
**Previous**: S131 (SQ formula root fix), S129 (handoff doc), S89 (feature session)

---

## What Was Done

### Root Problem
`_resolve_esports_from_clob()` — an S104 workaround for shared resolution queue starvation — was permanently corrupting EsportsBot P&L data. It set `paper_trades.resolution` WITHOUT `realized_pnl`, causing the shared backfill pipeline (Phase 4b) to skip those rows forever (`WHERE resolution IS NULL`). This created phantom RESOLUTION events with NULL P&L in `trade_events`, making all WR/P&L analysis unreliable. 115 of 222 resolved trades were orphaned from P&L computation.

The function became redundant after S125 added expired-first ordering to the shared resolution queue.

Separately, the bot was entering both sides (YES+NO) of the same market (7 Valorant markets confirmed), guaranteeing net loss from fees on those trades.

### Fixes Applied

| Fix | Description | Type | Status |
|-----|-------------|------|--------|
| **Fix 1** | Delete `_resolve_esports_from_clob()` (lines 1671-1806, ~136 lines) + call site in `_backfill_esports_outcomes()` | Code | DEPLOYED |
| **Fix 2** | Recompute `realized_pnl` for 115 paper_trades with resolution but NULL P&L | Data SQL | EXECUTED (115 rows updated) |
| **Fix 3** | Delete 85 phantom RESOLUTION events (NULL P&L or price=0.0), re-emit via shared backfill (32 inserted, 17 updated) | Data SQL | EXECUTED |
| **Fix 4** | Opposing-side guard — block YES entry when NO exists (and vice versa) in all 3 entry paths | Code | DEPLOYED |
| **Fix 5** | Persist `confidence` + `signal_quality` in `event_data` JSONB for future WR analysis | Code | DEPLOYED (not yet tested — no new entries since deploy) |
| **Fix 6** | Phase 4b EXIT P&L subtraction (shared) | Code | ALREADY IMPLEMENTED (lines 467-472, 498-500 in resolution_backfill.py) |

### Bugs Killed

| Bug ID | Description | Fix |
|--------|-------------|-----|
| SE-1 | paper_trades resolution set without realized_pnl → shared backfill skips forever | Fix 1 |
| EB-1 | Same as SE-1, esports-specific path | Fix 1 |
| EB-2 | Per-row RESOLUTION emission → only first row's P&L for multi-entry markets | Fix 1 |
| EB-3 | Opposing-side entries on same market (7 Valorant markets) | Fix 4 |
| EB-4 | Race condition: esports resolution vs shared Phase 4b/4b-alt (price=0.0 inconsistency) | Fix 1 |
| EB-5 | No confidence/signal_quality in event_data → can't do confidence-bucketed WR analysis | Fix 5 |

---

## Code Changes (esports_bot.py only)

### Fix 1: Deleted `_resolve_esports_from_clob()`
- **Removed**: Entire method (~136 lines) that queried CLOB API for resolution, set paper_trades.resolution directly, and emitted RESOLUTION events bypassing shared pipeline
- **Removed**: Call site in `_backfill_esports_outcomes()` (6 lines)
- **Effect**: Esports resolutions now flow exclusively through shared resolution queue (resolution_backfill.py Phase 4b), which correctly computes `realized_pnl` and emits proper RESOLUTION events

### Fix 4: Opposing-side guard
- **`__init__()`**: Added `_entered_market_sides: set` and `_entered_sides_restored: bool`
- **`scan_and_trade()`**: One-time restore from `trade_events` ENTRY records on first scan cycle
- **`_execute_esports_trade()`**: At top of method, checks order gateway + `_entered_market_sides` for opposing side → returns False with `esports_opposing_side_blocked` log
- **WS reactive path**: Simplified to block ANY entry where position exists (same or opposite side), plus explicit `_entered_market_sides` check for opposite side
- **Success tracking**: Both main path and S-T override path add `(market_id, side)` to set on successful entry
- **Pattern**: Identical to MirrorBot S117 (mirror_bot.py:1271-1293), proven in production 7+ days

### Fix 5: confidence + signal_quality in event_data
- **Main path** (before `place_order()`): `_event_data["confidence"] = round(confidence, 4)`, `_event_data["signal_quality"] = round(_sq_sizing, 4)`
- **S-T override path**: Same fields from `opp` dict
- **Note**: No new entries since deploy (CVaR exposure blocking). Will validate on next entry.

---

## Data Repair Results

### Fix 2: paper_trades P&L recomputation
```
Before: 115 paper_trades with resolution IN ('YES','NO') AND realized_pnl IS NULL
After:  0 (all 115 rows updated with correct realized_pnl + status='resolved')
```

### Fix 3: Phantom RESOLUTION event cleanup
```
Before: 85 RESOLUTION events with realized_pnl IS NULL OR price = 0.0
After:  0
Re-emitted: 32 new RESOLUTION events inserted, 17 paper_trades updated via shared backfill
```

---

## Post-Deploy Verification (2026-03-26 00:31 UTC)

### Service Health
- EsportsBot scan cycles completing in ~168-175ms
- Scan summary: 12 markets, 4 live matches, 2 opportunities, 0 trades (CVaR limiting)
- Waterfall: `no_prediction=6, low_edge=2, low_confidence=1, passed=3, reentry_rejected=1`
- No errors in logs

### Data Integrity
| Check | Result | Status |
|-------|--------|--------|
| Paper trades NULL realized_pnl | 0 | CLEAN |
| Phantom RESOLUTION events | 0 | CLEAN |
| Both-sides markets (historical) | 7 | EXPECTED (pre-Fix 4, cannot undo) |
| New both-sides entries post-deploy | 0 | CLEAN |
| Confidence in new entries | Not yet tested | PENDING (no entries since deploy) |
| Unresolved esports markets | 34 | RESOLVING (shared queue processing) |

### Corrected P&L (from trade_events RESOLUTION with non-NULL realized_pnl)

| Game | Resolutions | Wins | WR% | Resolution P&L |
|------|------------|------|-----|---------------|
| sc2 | 1 | 1 | 100.0% | +$40.61 |
| cod | 1 | 1 | 100.0% | +$4.24 |
| valorant | 12 | 6 | 50.0% | -$178.83 |
| cs2 | 62 | 27 | 43.5% | -$382.73 |
| dota2 | 28 | 16 | 57.1% | -$561.35 |
| unknown | 10 | 4 | 40.0% | -$889.70 |
| lol | 13 | 1 | 7.7% | -$951.94 |

**Previous (corrupt) dota2 numbers**: "50% WR, -$5,829" → **Corrected: 57.1% WR, -$561**

### All-Time P&L Summary
```
Entries:     327
Exits:       192  ($-1,448.35 realized)
Resolutions: 127  ($-2,919.69 realized)
All-time realized:  $-4,368.04
Open positions:      16 ($-105.54 unrealized)
Net P&L:            $-4,473.58
```

---

## S131 SQ Formula Fix (also deployed this session)

Moved Signal Quality from confidence multiplier to sizing multiplier:
- **Before**: `confidence = side_prob * SQ` (SQ crushed confidence → rejected at gate)
- **After**: `confidence = side_prob`, `size *= SQ` (SQ scales position size, not entry signal)
- **Effect**: Bot enters with true model confidence, SQ only affects bet size
- **CVaR note**: $12.5K/$10K exposure from 23 open positions blocked new entries. Self-clears as positions resolve (16 remain).

---

## Known Issues / Monitor Items

1. **CVaR exposure blocking**: 16 open positions at $4,378 cost basis. Bot will resume entries as positions resolve/exit. Not a bug — risk system working correctly.

2. **7 historical both-sides markets**: All Valorant, all pre-Fix 4. Cannot undo entries already made. Will resolve naturally. Fix 4 prevents new occurrences.

3. **10 "unknown" game resolutions**: `event_data->>'game'` is NULL for these older entries (pre-game-tagging). P&L is real ($-889.70), just unattributable to a specific game.

4. **LoL 7.7% WR (1/13)**: Genuinely bad performance, not a data bug. Model struggles with LoL. Consider minimum confidence gate per game or disabling LoL entirely.

5. **34 unresolved esports markets**: Now flowing through shared queue (Fix 1 ensures this). Monitor: `SELECT COUNT(*) FROM traded_markets WHERE bot_names LIKE '%Esports%' AND resolved = FALSE` — should decrease over next 24-48h.

6. **Fix 5 not yet validated**: No new entries since deploy. On next entry, verify: `SELECT event_data->>'confidence', event_data->>'signal_quality' FROM trade_events WHERE bot_name='EsportsBot' AND event_type='ENTRY' ORDER BY event_time DESC LIMIT 1` — both should be non-NULL.

---

## Files Modified

| File | Lines Changed | Scope |
|------|--------------|-------|
| `bots/esports_bot.py` | ~136 deleted (Fix 1), ~60 added (Fix 4+5) | EsportsBot only |

**No shared modules touched.** resolution_backfill.py was audited but Fix 6 was already implemented.

---

## Rollback

```bash
# Code rollback (reverts Fix 1, 4, 5):
cd /opt/polymarket-ai-v2 && git log --oneline -5  # find pre-S132 commit
git revert <S132-commit-sha>
sudo systemctl restart polymarket-ai

# Data rollback is NOT recommended — the pre-fix data was corrupt.
# If needed, the 115 paper_trades can be set back to realized_pnl=NULL,
# but this re-breaks all P&L reporting.
```

---

## Commit Status

Code changes deployed to VPS via `deploy.sh` (deploy `20260325_202511`). Local git commit pending — run:
```bash
git add bots/esports_bot.py
git commit -m "S132: EsportsBot data integrity — delete _resolve_esports_from_clob, add opposing-side guard, persist confidence in event_data"
```
