# EsportsBot Agent Handoff — Session 76 (2026-03-11)

**Session type**: ESPORTS BOT ONLY
**Date**: 2026-03-11
**Git commit deployed**: `53f4760`
**VPS release**: `/opt/pa2-releases/20260310_205826`

---

## TL;DR

Four bugs found and fixed in EsportsBot. 152 corrupted prediction_log entries cleared. Bot went from 0 opportunities → 3 trades in first scan post-fix. P&L summary corrected from -$521/153 trades (fanout artifact) to -$146/31 trades (actual). ESPORTS_MIN_CONFIDENCE corrected on VPS from 0.55 → 0.52.

---

## Session 75 Verification (Completed at Session 76 Start)

All 4 Session 75 first-action checks passed:

1. ✅ `order_gateway_daily_exposure_restored count=0` in startup logs
2. ✅ `order_gateway_daily_exposure_flushed count=1` firing every 60s
3. ✅ `daily_counters` table has WeatherBot data ($23.11 for 2026-03-11)
4. ✅ EsportsBot scanning (main bot + live bot + series bot)

---

## Bugs Fixed This Session

### Bug 1: `compute_pnl_summary()` JOIN Fanout (CRITICAL)

**File**: `esports/data/esports_db.py`
**Symptom**: P&L summary reported 153 trades and -$521 when only 31 resolved trades exist at -$146 actual.
**Root cause**: `paper_trades LEFT JOIN esports_prediction_log ON market_id` — for markets with N entries in prediction_log, each paper_trade appears N times in the result → COUNT(*) and SUM(pnl) multiplied by N.
**Fix**: Added CTE with `DISTINCT ON (market_id) ORDER BY market_id, created_at DESC` before the join, reducing to exactly one prediction_log entry per market.

```sql
-- OLD (fanout):
FROM paper_trades pt
LEFT JOIN esports_prediction_log epl ON pt.market_id = epl.market_id
WHERE pt.bot_name LIKE 'Esports%' AND pt.realized_pnl IS NOT NULL

-- NEW (fixed):
WITH game_map AS (
    SELECT DISTINCT ON (market_id) market_id, game, edge
    FROM esports_prediction_log
    ORDER BY market_id, created_at DESC
)
FROM paper_trades pt
LEFT JOIN game_map gm ON pt.market_id = gm.market_id
WHERE pt.bot_name LIKE 'Esports%' AND pt.realized_pnl IS NOT NULL
```

---

### Bug 2: `_backfill_esports_outcomes()` Processing SELL Trades

**File**: `bots/esports_bot.py`
**Symptom**: Spurious `accuracy_below` warnings every 2 minutes — `accuracy=0.32 brier=0.2665 game=cs2`. Accuracy was firing on 152 corrupted `actual_outcome` entries.
**Root cause**: Old SELL paper_trades (pre-YES/NO mandate, 2025-era) were being processed by the outcome backfill. These SELL trades have `no_token_id` as their `token_id` — which is the YES direction — but the formula `outcome = 1 - int(won)` treated them as NO-direction, inverting all outcomes. 152 outcomes set backwards.
**Fix**: Added `AND pt.side IN ('YES', 'NO')` filter in the backfill SQL.

```python
# OLD:
WHERE pt.bot_name IN ('EsportsBot', 'EsportsSeriesBot', 'EsportsLiveBot')
  AND pt.realized_pnl IS NOT NULL
  AND pt.created_at > NOW() - INTERVAL '7 days'

# NEW:
WHERE pt.bot_name IN ('EsportsBot', 'EsportsSeriesBot', 'EsportsLiveBot')
  AND pt.realized_pnl IS NOT NULL
  AND pt.side IN ('YES', 'NO')
  AND pt.created_at > NOW() - INTERVAL '7 days'
```

**Data fix** (direct DB, already applied on VPS):
```sql
UPDATE esports_prediction_log
SET actual_outcome = NULL, resolved_at = NULL
WHERE actual_outcome IS NOT NULL;
-- Result: UPDATE 152
```

---

### Bug 3: Near-Resolved Markets Wasting Scan Cycles

**File**: `esports/markets/esports_market_service.py`
**Symptom**: 234 markets scanned per cycle, ~100 of which were LoL "Game N Winner" markets at prices 0.0005 or 0.9995 (already decided, but not yet `resolved=true` in DB). Each triggered Glicko-2 lookups, then was rejected by the max_edge=0.20 sanity cap.
**Fix**: Added `AND yes_price BETWEEN 0.03 AND 0.97` to the WHERE clause. Markets reduced from 234→182 (22% reduction).

---

### Bug 4: VPS ESPORTS_MIN_CONFIDENCE Override

**Target**: VPS `/opt/pa2-shared/.env`
**Symptom**: P6.1 (commit `26a9f50`) set `ESPORTS_MIN_CONFIDENCE` default to 0.52 in `settings.py`, but VPS env had `ESPORTS_MIN_CONFIDENCE=0.55` overriding it. Extra 3% confidence threshold blocking borderline opportunities.
**Fix**: Edited VPS env directly (env changes not deployed via git/deploy.sh):
```bash
ssh -i "~/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21
sudo nano /opt/pa2-shared/.env
# ESPORTS_MIN_CONFIDENCE=0.55 → ESPORTS_MIN_CONFIDENCE=0.52
sudo systemctl restart polymarket-ai
```

---

## Diagnostic Enhancement Added

Added `markets_by_game` dict to `esportsbot_scan_summary` log. Example output:
```
esportsbot_scan_summary markets=182 markets_by_game={'lol': 143, 'other': 7, 'dota2': 1, 'cs2': 22, 'valorant': 8, 'cod': 1} min_confidence=0.52 opportunities=3 skipped_has_position=4 trades=3
```

---

## Current EsportsBot State (End of Session 76)

### P&L Summary (post-fanout fix)
- **31 resolved trades**: -$146.38 total P&L, 19.35% win rate
- All 31 losses are OLD SELL-side trades from pre-YES/NO mandate era (2025)
- **47 YES/NO trades**: Unresolved (open or pending settlement)

### Open Positions
4 open positions tracked (`skipped_has_position=4` in scan):
- 3 new positions opened this session (Dota2×2, Valorant×1 at ~$100 each, correct YES/NO sides)
- 1 pre-existing position

### Monitoring State
- `actual_outcome` in `esports_prediction_log`: ALL NULL (152 corrupted entries cleared)
- First clean outcomes will appear when the 47 YES/NO trades resolve
- `accuracy_below` warnings: GONE (no outcomes to compute accuracy against)

### Market Universe
- 182 active priced markets (yes_price 0.03–0.97)
- By game: lol=143, cs2=22, valorant=8, other=7, dota2=1, cod=1
- LoL dominates but produces 0 opportunities (see Outstanding section)

---

## Outstanding Items

### 1. LoL 0 Opportunities (Not Investigated)

143 LoL markets scanned consistently → 0 opportunities every scan.

**Hypothesis**: Team name extraction failing for LCK/LEC/LPL/LCS tournaments. The Polymarket question format for LoL markets differs from CS2/Valorant (e.g., "Will [TeamA] beat [TeamB] in [Region] Week N?"). If `_extract_teams()` can't parse the team names, `_build_glicko2_game_state()` returns `None` and the market is skipped.

**Investigation commands** (next session):
```bash
# Look at a sample LoL market question
ssh -i "~/.ssh/..." ubuntu@34.251.224.21 \
  "psql -U polymarket -d polymarket -c \"SELECT question FROM markets WHERE active=true AND yes_price BETWEEN 0.03 AND 0.97 AND (question ILIKE '%lck%' OR question ILIKE '%lec%' OR question ILIKE '%lpl%') LIMIT 5;\""

# Check how many LoL markets get Glicko-2 state built vs skipped
# Look for debug logs:
journalctl -u polymarket-ai -f | grep -i "glicko2\|extract_teams\|lol\|league"
```

**In code**: `bots/esports_bot.py` `_extract_teams()` and `_build_glicko2_game_state()` — add debug logging to see what's being extracted from LoL questions.

### 2. YES/NO Trade Resolution Monitoring

47 unresolved YES/NO trades in `paper_trades` (all placed 2026-03-10 to 2026-03-11). These will resolve as Polymarket markets settle. After resolution:
- `_backfill_esports_outcomes()` (runs every 10 scans) will set `actual_outcome` in `esports_prediction_log`
- First real monitoring accuracy will appear (no corruption this time)
- Check with: `journalctl -u polymarket-ai -f | grep "accuracy\|brier\|esports_outcomes"`

**VPS query to check resolution status**:
```sql
SELECT COUNT(*), side, realized_pnl IS NOT NULL as resolved
FROM paper_trades
WHERE bot_name LIKE 'Esports%' AND side IN ('YES','NO')
GROUP BY side, resolved;
```

### 3. main.py Health Endpoint (Uncommitted)

Local working tree has uncommitted changes to `main.py` — a health server (`_health_server`) on port 8765 for Phase 5b. NOT deployed. Status: pre-feature, sitting in local git index. Either commit and deploy, or discard when starting next session.

Check status: `git diff main.py`

---

## VPS Verification for Next Session

Run these at start of next session to confirm EsportsBot is healthy:

```bash
# 1. Check latest scan summary
journalctl -u polymarket-ai --since "5 min ago" | grep "esportsbot_scan_summary"
# Expect: markets=182, markets_by_game shows lol/cs2/valorant, min_confidence=0.52

# 2. Check P&L summary (should be ~31 trades, not 153)
journalctl -u polymarket-ai --since "1 hour ago" | grep "esports_pnl_summary"
# Expect: total_trades ~31-50, total_pnl around -$146 (will improve as YES/NO trades resolve)

# 3. Check no spurious accuracy warnings
journalctl -u polymarket-ai --since "1 hour ago" | grep "accuracy_below"
# Expect: NO output (all actual_outcome = NULL)

# 4. Check open positions count (should have 3-4+)
journalctl -u polymarket-ai --since "5 min ago" | grep "skipped_has_position"
# Expect: skipped_has_position >= 4

# 5. Check ESPORTS_MIN_CONFIDENCE
journalctl -u polymarket-ai --since "1 hour ago" | grep "min_confidence"
# Expect: min_confidence=0.52 (NOT 0.55)
```

---

## Files Modified This Session

| File | Change | Lines |
|------|--------|-------|
| `esports/data/esports_db.py` | DISTINCT ON CTE for pnl_summary | ~15 |
| `bots/esports_bot.py` | SELL filter in backfill + markets_by_game diagnostic | ~8 |
| `esports/markets/esports_market_service.py` | yes_price BETWEEN 0.03 AND 0.97 | ~2 |
| VPS `.env` | ESPORTS_MIN_CONFIDENCE 0.55→0.52 | 1 |
| VPS DB (direct SQL) | Cleared 152 corrupted actual_outcome entries | — |

---

## Change Log

```
## CHANGE: 2026-03-11 (Session 76)
**Issue:** EsportsBot finding 0 opportunities; P&L summary overcounted 5×; spurious accuracy warnings from corrupted prediction data
**Root cause:** (1) JOIN fanout in compute_pnl_summary, (2) SELL trades feeding outcome backfill with inverted formula, (3) 50+ near-resolved markets not filtered, (4) VPS env overriding min_confidence to 0.55
**Files modified:** esports/data/esports_db.py, bots/esports_bot.py, esports/markets/esports_market_service.py
**Lines changed:** ~25 added/modified
**Blast radius:** EsportsBot, EsportsLiveBot, EsportsSeriesBot (all share esports_db.py); EsportsMarketService (shared by all 3 esports bots)
**Verification:** Post-deploy scan → opportunities=3, trades=3; P&L reports 31 trades; no accuracy warnings; markets reduced 234→182
**Rollback:** git revert 53f4760 + update VPS env back to ESPORTS_MIN_CONFIDENCE=0.55
```

---

## Historical Context (Quick Reference)

### EsportsBot Trade History
- **Pre-YES/NO mandate SELL trades**: 31 resolved, -$146 P&L (2025 era, irrelevant to current system)
- **YES/NO trades (current code)**: 47 unresolved, placed 2026-03-10 onward

### P7 Roadmap — ALL DONE
- P7.1 ✅ Freshness decay 120s→30s (`7a5cb8e`)
- P7.2 ✅ Series hedge log_prediction (`44a79e5`)
- P7.3 ✅ team_a/b_recent_form at inference (`44a79e5`)
- P7.4 ✅ BOT_ENABLED_ESPORTS_SERIES=true (live on VPS)
- P7.5 ✅ PandaScore hourly rate counter (`7a5cb8e`)

### Key Config (Live VPS)
```
ESPORTS_MIN_CONFIDENCE=0.52  ← corrected this session
ESPORTS_MIN_EDGE=0.08
ESPORTS_FRESHNESS_DECAY_SECONDS=30.0
ESPORTS_SERIES_HEDGE_ENABLED=true
BOT_ENABLED_ESPORTS_SERIES=true
EsportsBot:       capital=$5000, kelly=0.25, max_bet=$100, max_daily=$500
EsportsLiveBot:   capital=$1000, kelly=0.25, max_bet=$100, max_daily=$500
EsportsSeriesBot: capital=$1000, kelly=0.25, max_bet=$100, max_daily=$500
SIMULATION_MODE=true
```
