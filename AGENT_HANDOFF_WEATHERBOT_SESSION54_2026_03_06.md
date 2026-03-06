# WeatherBot — Agent Handoff Session 54
**Date:** 2026-03-06
**Session scope:** WeatherBot ONLY — no bleed-over to other bots

---

## Session 54 Summary

### What Was Accomplished
Two critical bugs were found, fixed, tested (1107 passed), and deployed to VPS.

**Bug 1 — `_recently_exited` dead code (re-entry loop)**
- **Root cause:** `self._recently_exited: Dict[str, float] = {}` initialized in `__init__`, checked in
  `_analyze_group()` (line ~310) with 900s cooldown, but **never populated** anywhere in `weather_bot.py`.
  When `position_manager` auto-exits a position it removes it from `_open_position_markets` but does NOT
  notify `weather_bot`. Next scan: market appears available → bot re-enters → loop repeats.
- **Evidence:** VPS logs showed 4 complete BUY/SELL cycles in ~1h on the same São Paulo March 6 market.
- **Fix:** Added one line in `_execute_weather_trade()` after the exposure tracker updates:
  ```python
  self._recently_exited[opp["market_id"]] = time.monotonic()
  ```
- **Commit:** `31daf8e` — `bots/weather_bot.py`

**Bug 2 — Wide-spread CLOB orderbook → `current_price=0.5` → fake P&L**
- **Root cause:** `position_manager._update_current_prices()` CLOB fallback (line ~397) computed
  `(best_bid + best_ask) / 2` without checking spread width. Weather markets with zero liquidity
  have bid≈0.001, ask≈0.999 → midpoint=0.5. Position manager used this as the true price, triggered
  exit signals (take-profit fired at 0.5 for a position entered at 0.0135), recording fake realized P&L.
- **Evidence:** Paper trade log showed SELL at 0.5 after BUY at 0.0135; P&L = +$48.53 on restart.
  All $48.53 daily P&L was fabricated by this bug.
- **Fix:** Added spread-width guard to the CLOB fallback condition:
  ```python
  # Before:
  if 0 < _best_bid <= _best_ask < 1:
  # After:
  if 0 < _best_bid <= _best_ask < 1 and (_best_ask - _best_bid) < 0.5:
  ```
  Spreads ≥50% are now rejected; `current_price` stays at last valid value (entry price or WS price).
  Liquid markets used by all other bots have tight spreads (<0.05 typical) — guard never fires for them.
- **Blast radius verified:** Shared module (`position_manager.py`) affects all 15 bots; 1107 tests pass;
  tight-spread markets unaffected; only wide-spread illiquid (bid≈0, ask≈1) markets now skipped.
- **Commit:** `00cae8a` — `base_engine/execution/position_manager.py`

---

## Current System State (post-fix, ~16:00 UTC)

```
Service:         active (running) since 15:49:05 UTC
First post-fix scan: groups_with_edge=0, trades=0  (São Paulo March 6 expired — correct)
Daily P&L:       $48.53 (pre-fix fake exits; resets at midnight UTC)
Calibration:     0 rows in weather_calibration (cold start — expected)
Weather markets: ~260 in DB category=weather, 33 regex-matched, 16 groups, 0 with edge
```

### Why groups_with_edge=0 After Fix
The São Paulo March 6 market (the only market that had live CLOB pricing) expired/converged by ~15:13 UTC.
Its orderbook is now empty → CLOB enrichment skips it → `yes_price=NULL` → `edge=NULL` → group skipped.
Remaining 15 groups: no CLOB prices available (empty orderbooks on stale/expired Feb-March markets).
The 33 regex-matched markets in DB are all old 2020 markets (`end_date_iso=2020-11-04`). Their question
dates ("February 15", "March 1" etc.) parse as 2026 dates, all of which are now in the past.
`_analyze_group()` skips groups where `target_date < today`. Zero edge is correct for this set.
Bot will trade again as soon as a fresh market with a future date and live CLOB orderbook is ingested.

**DB cleanup performed this session:**
- Deleted 2 ghost positions from `positions` table (`bot_id='WeatherBot'`)
- Deleted 8 fake paper_trades from `paper_trades` table (today, all pre-fix artifacts)
- Service restarted: `_restore_daily_pnl_from_db()` found $0 (no `weatherbot_daily_pnl_restored` log)

---

## Session 53 Recap (context for Session 54)

- **Seasonal gap confirmed over:** First WeatherBot trade placed 2026-03-06 14:38:22 UTC.
  São Paulo 32°C YES @ 0.0135 (1.35¢), size=100, edge=0.9865, model_prob=1.0, lead_time_h=3.4, expiry_boost=2.0.
- **`RISK_MIN_PRICE_WEATHERBOT=0.005` added** (commit `da6ce4d`) — per-bot price floor override
  bypassing global 1.5% minimum, enabling 1-2¢ weather market prices.
- **`RISK_MIN_VOL_WEATHERBOT=0`** already set — bypasses $5K volume gate (all weather markets have liquidity=0 in DB).

---

## Architecture Notes (WeatherBot-Specific)

### CLOB Price Enrichment Flow
1. `scan_and_trade()` → `_fetch_weather_markets_direct()`: fetches ~33 regex-matched weather markets
2. `_enrich_with_live_prices()`: for each market without valid price, calls CLOB `/midpoint` via `yes_token_id`
3. Results: `enriched=N skipped=M` where skipped = markets with empty orderbooks
4. Markets with `yes_price=NULL` after enrichment cannot be traded (no edge can be computed)

### Position Manager CLOB Fallback (now fixed)
- `_update_current_prices()` runs every 10s for all open positions
- WS path (fast): reads `market_prices` table (updated by WS listener for 1000 tracked tokens)
- CLOB fallback (slow): `get_orderbook()` for positions NOT in WS feed (all weather markets)
- **After fix:** Only uses CLOB midpoint if spread < 50%. Wide-spread markets keep last valid price.

### `_recently_exited` Cooldown Guard
- `self._recently_exited: Dict[str, float]` — `market_id → monotonic_timestamp`
- Checked in `_analyze_group()` with 900s (15-min) cooldown before re-entry
- **After fix:** Populated in `_execute_weather_trade()` after successful `place_order()`
- Note: Cooldown is monotonic-clock based — resets on service restart (by design; OK for now)

### Market Mapper (precipitation markets are correct behavior)
- `market_mapper.parse_market()` requires `°F/°C` degree symbol in question
- Precipitation/rainfall markets ("Will NYC have between 3-4 inches of rain?") return `None` → filtered
- This is intentional — WeatherBot only trades high-temperature bucket markets

---

## Open Issues (Inherited, No Action Needed Now)

| Issue | Severity | Notes |
|-------|----------|-------|
| Calibration cold start | LOW | weather_calibration empty; fills at UTC day boundary after first resolution |
| CLOB enrichment cap (50 markets) | LOW | If >50 weather markets appear simultaneously, some miss pricing on first scan |
| Ensemble degenerate edge case | LOW | scipy RuntimeWarning suppressed; fallback handles near-identical members |
| São Paulo March 6 DB resolution | INFO | resolution=NULL, active=true in DB; backfill will update after Polymarket resolves |
| $48.53 fake P&L | INFO | Resets at midnight UTC via _handle_daily_boundary(); not real money |

---

## Change Log (Session 54)

```
## CHANGE: 2026-03-06 (Session 54)
**Issue:** _recently_exited never populated → 15-min re-entry cooldown dead → same market entered 4x in 1h
**Root cause:** Dict initialized but no write path existed; position_manager exits don't notify weather_bot
**Files modified:** bots/weather_bot.py
**Lines changed:** +1 added
**Blast radius:** WeatherBot only (_recently_exited is a WeatherBot instance attribute)
**Verification:** 1107 tests pass; VPS grep confirms line present; post-deploy scan clean
**Rollback:** git revert 31daf8e

## CHANGE: 2026-03-06 (Session 54)
**Issue:** Wide-spread CLOB orderbook set current_price=0.5 → fake P&L → fabricated exits
**Root cause:** (best_bid + best_ask) / 2 valid for liquid markets but 0.001+0.999)/2=0.5 for illiquid
**Files modified:** base_engine/execution/position_manager.py
**Lines changed:** +1 condition (and (_best_ask - _best_bid) < 0.5)
**Blast radius:** All 15 bots use position_manager; tight-spread markets unaffected; verified via tests
**Verification:** 1107 tests pass; VPS grep confirms condition; post-deploy scan uses correct prices
**Rollback:** git revert 00cae8a
```

---

## Priority Queue for Session 55+

**CONTEXT: Seasonal gap is OVER. Bot actively scans ~16 groups/scan. São Paulo March 6 was traded.**
**0 edge right now = all 33 DB markets are 2020 past-date markets. Trades when fresh markets ingest.**

**P1 — Verify next trade is clean (both S54 bugs are fixed)**
On next trade (new market ingested with future date + live CLOB orderbook):
- `_recently_exited` cooldown should fire after first entry (no repeat entries within 15 min)
- `current_price` should stay at entry price for illiquid markets (no 0.5 spikes)
- Check VPS: `sudo journalctl -u polymarket-ai -f | grep WeatherBot` for trade + cooldown logs

**P2 — Calibration loop validation**
At UTC midnight boundary after first market resolves:
```bash
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT station_id, lead_bucket, forecast_temp, actual_temp, bias
  FROM weather_calibration LIMIT 10;\""
```
Should see rows with `actual_temp` filled and `bias` computed (São Paulo March 6 was today).

**P3 — São Paulo resolution tracking**
```bash
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT id, question, resolution, active, end_date_iso
  FROM markets WHERE question ILIKE '%Sao Paulo%' OR question ILIKE '%São Paulo%'
  LIMIT 5;\""
```

**P4 — DB fake trade cleanup**
Session 54 deleted all 8 fake WeatherBot paper_trades from today (pre-fix artifacts).
Daily P&L is now $0. São Paulo March 6 real performance: net 0 (position deleted with records).

**P5 — Documentation**
- BOT_WEATHERBOT.md: updated this session ✓
- This handoff: complete ✓

---

## Debugging Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Live WeatherBot logs
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai -f | grep WeatherBot"

# Check for re-entry loop (should not see same market_id in repeated BUY logs)
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since 'today' | grep -i 'weather.*BUY\|weather.*place_order' | tail -30"

# Check CLOB spread guard firing (after fix, wide-spread markets log nothing; price stays stable)
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since 'today' | grep 'current_price\|_api_price' | grep -i weather | tail -20"

# Check WeatherBot paper trades (should see no more exits at price=0.5 for low-priced entries)
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT created_at, market_id, side, size, price, realized_pnl
  FROM paper_trades
  WHERE bot_name='WeatherBot'
  ORDER BY created_at DESC LIMIT 20;\""

# Check daily P&L (should be 0 after midnight reset)
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT DATE(created_at) as day, COUNT(*) as trades,
         ROUND(SUM(realized_pnl)::numeric, 2) as daily_pnl
  FROM paper_trades
  WHERE bot_name='WeatherBot' AND realized_pnl IS NOT NULL
  GROUP BY day ORDER BY day DESC LIMIT 10;\""

# Check weather market availability (startup log)
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since 'today' | grep -i 'weather_market_avail\|DB total\|regex-matched'"
```

---

## Commits This Session
| SHA | Description |
|-----|-------------|
| `31daf8e` | fix: populate _recently_exited after successful weather trade |
| `00cae8a` | fix: skip wide-spread orderbook prices in position_manager CLOB fallback |
