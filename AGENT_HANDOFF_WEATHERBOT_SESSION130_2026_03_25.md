# AGENT HANDOFF — WeatherBot Session 130 (2026-03-25)

## STATUS: PENDING DEPLOY | SPREAD INFLATION DAY ~2 | +$4,780 ALL-TIME (2,101 resolutions, 61.9% WR) | +$3,132 LAST 24H (74.5% WR)

---

## READ THESE BEFORE DOING ANYTHING

1. `CLAUDE.md` — Prime directive, rules of engagement, architecture facts, critical traps
2. `MEMORY.md` — Session history, P&L data, outstanding items
3. This handoff doc — everything you need

**This is a WeatherBot-only session. No bleed to other bots unless explicitly demanded.**

---

## WHAT WAS DONE THIS SESSION (S130)

### 1. GEFS Subsampling Removed (REVIEW NEEDED)

**File**: `base_engine/weather/forecast_client.py:1227-1232`
**Was**: At 48h+ lead time, GEFS members subsampled (keep 24/16/8 of 31 depending on lead). Rationale was ECMWF beats GFS on accuracy at long range.
**Now**: All 133 ensemble members (31 GEFS + ~102 ECMWF) kept at every lead time.

**Why**: GEFS's value isn't mean accuracy (ECMWF already wins by 3:1 count advantage). GEFS's value is **cross-model disagreement** — when GFS and ECMWF diverge, that's real atmospheric uncertainty. Subsampling threw away 10-20% of spread diversity, causing overconfidence. Spread inflation (S126) + Platt calibration (S123) handle remaining overconfidence without destroying member diversity.

**REVIEW**: Monitor 48h for WR impact. If 72-120h bucket (best performer, +$2,222 all-time) degrades, revert is one-line. Expected outcome: wider spreads → smaller bets on uncertain situations → fewer losses on busted forecasts.

### 2. `_consecutive_losses` Restore on Restart (BUG FIX)

**File**: `bots/weather_bot.py` — new method `_restore_consecutive_losses()` (~35 lines)
**Bug**: Per-market-type losing streak counter (`Dict[str, int]`) reset to zero on every service restart. Kelly compression (0.75x at 3+ losses, 0.50x at 5+, 0.25x at 8+) was lost.
**Fix**: On startup, queries `prediction_log` (last 200 entries per market_type) and walks backwards from most recent until first win to reconstruct the active streak. Called from the startup block alongside other `_restore_*` methods.
**Current streak**: 0 (most recent 5 predictions are wins). Restore will be a no-op until a streak forms, but the mechanism is now protected.

### 3. City Auto-Onboarding (Semi-Auto)

**Files**: `base_engine/weather/station_registry.py` (new `suggest_station_for_city()`), `bots/weather_bot.py` (wired into unmatched city alert)
**What**: When WeatherBot detects a new unmatched city from Polymarket, it now auto-calls Open-Meteo geocoding API to get lat/lon/timezone/country/elevation and logs a structured suggestion at WARNING level with all `WeatherStation` fields pre-filled.
**Human gate**: `station_id` (ICAO code), `ghcnd_id`, `resolution_source`, and `local_model` are set to `MANUAL_LOOKUP_REQUIRED` / `MANUAL_VERIFICATION_REQUIRED`. Engineer must verify these before adding to registry.
**No new pip dependency** — uses `aiohttp` (already imported in station_registry.py) and Open-Meteo geocoding (free, no key).

### 4. BUG-19: GEFS Member Count Warning

**File**: `base_engine/weather/forecast_client.py:1193-1199`
**What**: Logs WARNING when ensemble member count is outside expected 100-150 range. Detects if Open-Meteo changes GEFS/IFS/AIFS member counts.
**Impact**: Logging only, no behavioral change.

### 5. YES-Side WR Analysis (TABLE — no code change)

**All-time by side:**
| Side | N | Wins | WR | P&L |
|---|---|---|---|---|
| NO | 1,381 | 1,161 | 84.1% | +$3,964 |
| YES | 720 | 140 | 19.4% | +$815 |

**5 compounding causes:**
1. **Tail discount** (`probability_engine.py:188-189`) — 0.90x multiplier on tail buckets ("62°F or higher", "35°F or below"), which are predominantly YES-side
2. **Calibrator compression** — Platt T=2.271 pushes confidence toward 0.50, causing more YES trades to fail the negative-EV gate (confidence < price) because YES prices are higher
3. **Kelly blocks extreme tails** — `probability_engine.py:325-326` returns Kelly=0 for prices <0.02 or >0.98, hitting YES tail trades
4. **Tight ensemble** — ECMWF members agree internally, understating true uncertainty on tails (partially addressed by removing GEFS subsampling)
5. **No YES price cap** — NO has `WEATHER_NO_MAX_ENTRY_PRICE` guard, no equivalent for YES

**Assessment**: YES is net profitable (+$815) due to payoff asymmetry (cheap YES bets pay big). No code change now — monitor whether GEFS subsampling removal improves YES WR by widening tail distributions.

---

## P&L SNAPSHOT (as of S130)

| Metric | Value |
|--------|-------|
| All-time realized | **+$4,779.74** |
| Last 24h | **+$3,131.71** (145 res, 74.5% WR) |
| Total resolutions | 2,101 |
| Win rate | 61.9% |
| Open positions | 155 |
| Cost basis | $33,398.52 |
| Unrealized | +$8.67 |

**Last 24h by side:**
| Side | N | WR | P&L |
|---|---|---|---|
| NO | 102 | 93.1% | +$2,686 |
| YES | 43 | 30.2% | +$446 |

**Last 24h by lead-time:**
| Bucket | N | WR | P&L |
|---|---|---|---|
| 48-72h | 3 | 100% | +$73 |
| 72-120h | 130 | 75.4% | +$1,753 |

---

## FILES MODIFIED

| File | Change | Lines |
|------|--------|-------|
| `base_engine/weather/forecast_client.py` | Removed GEFS subsampling (19 lines → 6 line comment), added BUG-19 warning (7 lines) | -13, +13 |
| `bots/weather_bot.py` | Added `_restore_consecutive_losses()` (~35 lines), wired into startup. Added `suggest_station_for_city` import + call in unmatched city handler (~4 lines). | +42 |
| `base_engine/weather/station_registry.py` | Added `suggest_station_for_city()` async function (~45 lines) | +45 |

**Blast radius**: All changes are WeatherBot-only. No shared module signatures changed. No DB schema changes.

---

## PENDING PRIORITIES

| Pri | Item | Status | Notes |
|-----|------|--------|-------|
| **P1** | Monitor GEFS subsampling removal impact | **48h window** | Check 72-120h WR + YES-side WR by Mar 27 |
| **P1** | Monitor spread inflation decay | Active day ~2 | Check at day 7 (Mar 31). Hard zero Apr 16. |
| **P3** | YES-side WR 19.4% | Monitor | May improve with wider spreads from subsampling removal |
| **P3** | `_market_group_cache` not persisted | Startup rebuild covers it | Redis if leaks observed |
| **P4** | Platt T=2.271 is static | Compensated by spread inflation | Future: auto-refit? |
| **P4** | INEFF-4/5, LOG-3, LOG-REDIS, BUG-10 | Low priority P4/P5 items | |

---

## CRITICAL TRAPS (new this session)

28. **S130: GEFS subsampling removed** — all 133 ensemble members flow at every lead time. `_gefs_count` still tracked for model-used logging only.
29. **S130: `_consecutive_losses` restored on startup** — queries `prediction_log` last 200 entries, walks backwards until first win. No write-through needed.
30. **S130: `suggest_station_for_city()`** — auto-geocodes unmatched cities via Open-Meteo. ICAO/GHCND/resolution_source require MANUAL verification. Do not auto-add to registry.
31. **S130: BUG-19 warning** — logs `forecast_unexpected_ensemble_count` when outside 100-150 range. Logging only.

---

## VERIFIED FALSE ALARMS (cumulative)

All from S129 — no new false alarms this session.

---

## DEPLOY CHECKLIST

```bash
# 1. Run tests
python -m pytest tests/unit/test_weather_bot.py tests/unit/test_weather_cold_start.py -x -q

# 2. Deploy
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh

# 3. Post-deploy verification
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai -f" | grep -E "WeatherBot|weatherbot"

# 4. Check consecutive losses restore
# Look for: weatherbot_consecutive_losses_restored (if streak > 0)

# 5. Check ensemble count warnings
# Look for: forecast_unexpected_ensemble_count (should NOT appear if Open-Meteo unchanged)

# 6. Check city suggestions (only if new cities appear)
# Look for: station_suggest_candidate
```

---

## SHARED ENGINE FIXES (Cross-Bot, S132)

These fixes were applied to shared infrastructure. WeatherBot is the most affected bot.

### SE-4: Phase 4b Partial Exit P&L Double-Count (P1)
- **File**: `base_engine/data/resolution_backfill.py`
- **Bug**: Phase 4b used `SUM(pt.realized_pnl)` from paper_trades without subtracting EXIT P&L already captured in trade_events. Partial exits were double-counted.
- **Fix**: Added `exit_pnl_already` subquery (matching Phase 4b-alt pattern). RESOLUTION P&L now = raw_pnl - exit_pnl.
- **WeatherBot impact**: 40 exits — highest exposure to double-count after MirrorBot.
- **Monitor**: `grep "phase4b_exit_pnl_subtracted" | grep WeatherBot`

### SE-2: Re-Entry Guard Race Condition (P2)
- **File**: `base_engine/execution/paper_trading.py`
- **Bug**: Between position close (`del self.positions[pos_key]`) and DB write, a scan could pass re-entry guards and create a duplicate position. 2-second race window.
- **Fix**: Added `_recently_closed` cooldown dict in PaperTradingEngine. BUY blocked for 2s after position close.
- **WeatherBot impact**: HIGHEST RISK — 60s scan interval with 1,500 markets. 2 markets had 9 entries each in 1 hour on 2026-03-25 from this race.
- **Monitor**: `grep "paper_reentry_blocked" | grep WeatherBot`

### SE-3: DB Write Error Escalation (P3)
- **File**: `base_engine/execution/paper_trading.py`
- **Bug**: Post-lock DB write failures logged at WARNING, invisible to monitoring. Escalated to ERROR with cumulative counter.
- **Monitor**: `grep "post_lock_db_write_failed"` — should be zero.

### WeatherBot-Specific Bugs Still Open (from audit)
- **WB-1 (P0)**: No minimum lead time — Tokyo entered at lead_time=0.0 → -$10,207 loss
- **WB-2 (P1)**: Kelly graduation blind to P&L — graduated to 0.35 while losing money
- **WB-3 (P3)**: Consecutive loss tracker per-type only — correlated cross-type losses evade circuit breaker
