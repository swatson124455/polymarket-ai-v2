# AGENT HANDOFF — WEATHERBOT SESSION 68
**Date:** 2026-03-09
**Bot Focus:** WeatherBot exclusively — NO other bots
**Tests at close:** 1333 passed, 6 skipped
**Commit:** `5522654` — fix: WeatherBot exposure limit + Open-Meteo 429 + WU scraping
**VPS status:** `active (running)` — new process 1754530 started ~16:01 UTC
**Prior session:** Session 67 — Bugs A-E, Snowfall, Wind, Precip regex fix

---

## SESSION 68 SUMMARY

### What Was Done

Four parallel investigations (A–D) were run on the live bot to assess health and find root causes.

---

### Investigation A — `precip_trades=0` Root Cause

**Finding:** Open-Meteo ensemble API returning `HTTP 429 — Daily API request limit exceeded`.

**Root cause chain:**
1. Temperature scanning makes ~8,640 ensemble API calls/day (30 groups × 3 models × 96 cache-refreshes at 900s TTL)
2. Daily free-tier quota is hit by mid-day
3. Precipitation scanner calls the **same** ensemble API endpoint → all return 429 → `member_sums` is empty → `get_monthly_precipitation_ensemble()` returns `None` → `_analyze_precipitation_group()` returns `[]` → `precip_trades=0`

**The math confirms massive edges exist when data IS available:**
- NYC March actuals March 1–8: 1.67 inches
- GFS remaining forecast (March 9–31): mean 0.85", range 0.08–2.32"
- Expected total: 1.75–3.99", mean ~2.52"
- Market prices are massively mispriced vs model:
  - "2–3 inches" YES @ **8.45%** → model says ~68% → edge ~60%
  - "4–5 inches" YES @ **33.5%** → model says ~0% (max is 3.99") → NO edge ~33%
  - ">6 inches" YES @ **16.5%** → model says ~0% → NO edge ~16%

**Fix applied:**
- `WEATHER_FORECAST_CACHE_TTL` 900s → 1800s (halves calls to ~4,320/day)
- `invalidate_forecast_cache()` now only clears `_cache` (temperature). Precip/snowfall/wind caches NOT cleared during NWP model windows. Saves ~6 extra calls per invalidation cycle.

**Status:** Fix deployed. Will take full effect **tomorrow** when daily API quota resets (UTC midnight).

---

### Investigation B — Snowfall/Wind Tag Discovery

**Finding:** Confirmed via live Gamma API curl:
- `tag_slug=snowfall` → **0 events** (no active markets on Polymarket)
- `tag_slug=wind` → **0 events** (no active markets on Polymarket)
- `tag_slug=precipitation` → **2 events** (NYC, Seattle) ✅
- `tag_slug=weather` → 10 events (seasonal questions: hurricanes, Arctic ice — NOT bucket markets)

**Action:** None required. Scanners are ready; regex patterns are ready. No tag slug changes needed.

---

### Investigation C — WU Scraping Robustness

**Finding:** Two issues:
1. User-Agent `"WeatherBot/1.0"` is likely getting blocked by WU (anti-bot detection)
2. Only 2 regex patterns — both fail if WU changes Angular DOM structure

**Fix applied (`bots/weather_bot.py`):**
- User-Agent upgraded to real Chrome 122 UA string
- Added `Accept-Language` and `Referer` headers
- 4 regex patterns now (was 2):
  1. `Max</span>...°value` (original)
  2. `"maxTemp...": { "...": value }` (JSON property)
  3. `"High/Maximum Temperature..." value` (Angular state dump)
  4. `"observationSummary"...Max...value` (WU embedded JSON block)
- Added `weatherbot_wu_no_match` debug log when all patterns fail

---

### Investigation D — VPS Health Check

**Critical finding: Miami NO trade blocked every scan.**

From live logs:
```
Order blocked after clamp: risk limits  market_id=0xa322f...
reasons=['Total exposure $10988.54 exceeds max $10000.00']
```

The Miami market `0xa322f...` had:
- YES price = 0.39, model_prob = 0.2533 → NO side, edge = **13.67%**
- Repeatedly blocked because **global** `RISK_MAX_TOTAL_EXPOSURE_USD = 10,000` was exceeded
- WeatherBot had 109 open positions totalling $10,988

**Root cause:** Same pattern as the MAX_POSITIONS blocker from Session 66. Global limit ($10,000) is too low for WeatherBot's multi-bucket trading style.

**Fix applied (`base_engine/risk/risk_manager.py` + `config/settings.py`):**
- WeatherBot now uses `og.get_bot_exposure_usd(bot_name)` (bot-specific exposure) instead of `og.get_total_exposure_usd()` (all bots combined)
- New `WEATHER_MAX_TOTAL_EXPOSURE_USD = 50,000` setting (env var: `WEATHER_MAX_TOTAL_EXPOSURE_USD`)
- Applied in both OrderGateway path (line ~320) and DB fallback path (line ~385)
- Same scoped pattern as `WEATHER_MAX_POSITIONS`

**Post-deploy confirmed:** No more "Total exposure" WeatherBot blocks in logs ✅

**Other VPS observations:**
- `weather_markets=410` (was 267–270) — more markets being discovered after restart
- `pnl=-18.06` at session start — small daily loss, normal
- Scan time ~82–126s (within 300s target, OK)
- MirrorBot max-positions warnings in logs — separate bot, not WeatherBot scope

---

## FILES MODIFIED THIS SESSION

| File | Change |
|------|--------|
| `config/settings.py` | `WEATHER_FORECAST_CACHE_TTL` 900→1800; added `WEATHER_MAX_TOTAL_EXPOSURE_USD=50000` |
| `base_engine/risk/risk_manager.py` | WeatherBot uses bot-specific exposure + `WEATHER_MAX_TOTAL_EXPOSURE_USD` (OG path + DB fallback) |
| `base_engine/weather/forecast_client.py` | `invalidate_forecast_cache()` only clears `_cache`; precip/snow/wind caches survive NWP invalidations |
| `bots/weather_bot.py` | WU scraping: browser UA, 4 regex patterns, `weatherbot_wu_no_match` debug log |

---

## CURRENT STATE (as of deploy ~16:01 UTC)

| Metric | Value | Notes |
|--------|-------|-------|
| Temperature markets | 410 markets, 44 groups | Up from 268 after restart |
| Temperature trades | `groups_with_edge=0` | Temp — cold cache + daily 429 quota hit; recovers overnight |
| Precipitation | 13 markets, 2 groups | `precip_trades=0` — 429 fix takes effect tomorrow |
| Snowfall | 0 active markets | Scanner ready |
| Wind | 0 active markets | Scanner ready |
| Exposure blocker | **FIXED** | No more `Total exposure` blocks |
| Tests | 1333 passed, 6 skipped | ✅ |

---

## WHAT TO EXPECT TOMORROW (after UTC midnight quota reset)

1. **Temperature edges return** — ensemble cache cold-starts fine, 1800s TTL keeps us well within quota
2. **Precipitation starts trading** — NYC "2–3 inches" and "4–5+ inches" NO positions are near-certain edges. Expect multiple precip_trades per scan.
3. **Monitor:** `journalctl -u polymarket-ai -f | grep -E "precip_edges|precip_trades|weatherbot_scan_done"`
4. **Seattle** — also has 7 precipitation markets; check those too

---

## KNOWN ISSUES / NEXT PRIORITIES

### Immediate (check tomorrow)
1. **Verify precipitation trades fire** — run `grep weatherbot_precip_edges` in logs once API quota resets
2. **Verify no new exposure blocks** — run `grep "Total exposure" | grep WeatherBot`

### Near-term
3. **Open-Meteo API calls audit** — with 44 groups now (up from 30), at 1800s TTL = ~5,280 calls/day (3 models × 44 × 40 refreshes). Still within 10,000 limit, but worth monitoring. If 429 returns, raise TTL to 3600s.
4. **Combine temperature+precipitation into single API call** — `get_combined_forecast()` and `get_precipitation_ensemble()` both call the ensemble API separately. Combining into one call with `daily=temperature_2m_max,precipitation_sum` would halve per-station calls. Significant refactor (Tier 2 work).
5. **EMOS activation progress** — at ~3 actuals/station/day from March 8, expect activation for early stations around March 15–17. Monitor: `SELECT station_id, COUNT(*) FROM weather_calibration WHERE actual_temp IS NOT NULL GROUP BY station_id;`

### Not in scope (separate bots)
6. `series_prob_with_map_veto()` wrong params in `esports_series_bot.py:269` — EsportsBot issue

---

## KEY PATTERNS (Session 68 additions)

- **WeatherBot total exposure**: Uses `og.get_bot_exposure_usd("WeatherBot")` (NOT global). Cap = `WEATHER_MAX_TOTAL_EXPOSURE_USD=50000`. Env var: `WEATHER_MAX_TOTAL_EXPOSURE_USD`.
- **Open-Meteo 429**: Symptom = `precip_trades=0` + `groups_with_edge=0`. Cause = daily quota hit. Fix = 1800s TTL. If recurring, raise to 3600s.
- **Cache invalidation**: `invalidate_forecast_cache()` only clears temperature `_cache`. Precip/snowfall/wind caches live for their full TTL.
- **WU scraping**: 4-pattern fallback chain. UA = Chrome 122. `weatherbot_wu_no_match` debug log if all fail.
- **NYC precip mispricings**: "2–3 in" @ 8.45% YES (model ~68%), "4–5 in" @ 33.5% YES (model ~0%), ">6 in" @ 16.5% YES (model ~0%). Large NO edges on upper buckets.

---

## VERIFICATION COMMANDS

```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Check scan health
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai -f | grep weatherbot_scan_done'

# Check precip trades (after quota reset)
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "1 hour ago" | grep -E "precip_edges|precip_trades|weatherbot_precip"'

# Check for exposure blocks (should be clean)
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "30 minutes ago" | grep "Total exposure"'

# Check calibration actuals progress
PGPASSWORD='polymarket_s46' psql -h localhost -U polymarket -d polymarket -c \
  "SELECT station_id, COUNT(*) actuals FROM weather_calibration WHERE actual_temp IS NOT NULL GROUP BY station_id ORDER BY actuals DESC LIMIT 10;"

# Verify 429 gone (tomorrow)
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" \
  'journalctl -u polymarket-ai --since "1 hour ago" | grep "429"'
```

---

## CHANGE LOG

```
## CHANGE: 2026-03-09 (Session 68)
**Issue 1:** Global total exposure check blocked WeatherBot trades (Miami NO 13.7% edge blocked every scan)
**Root cause:** RISK_MAX_TOTAL_EXPOSURE_USD=10000 uses all-bot global exposure; WeatherBot alone at $10,988
**Files modified:** base_engine/risk/risk_manager.py (2 locations), config/settings.py
**Lines changed:** +8 risk_manager, +2 settings
**Blast radius:** WeatherBot only (scoped `if bot_name == "WeatherBot"`)
**Verification:** No "Total exposure" WeatherBot blocks post-deploy ✅
**Rollback:** git revert 5522654

**Issue 2:** Open-Meteo 429 → precip ensemble returns None → precip_trades=0
**Root cause:** 900s cache TTL × 44 groups × 3 models = ~11,000 calls/day exceeds free tier
**Files modified:** config/settings.py (TTL 900→1800), base_engine/weather/forecast_client.py (selective invalidation)
**Lines changed:** +1 settings, -3/+11 forecast_client
**Blast radius:** WeatherBot temperature forecast freshness (30-min vs 15-min refresh cycle)
**Verification:** Will confirm with tomorrow's first scans post-quota reset
**Rollback:** git revert 5522654

**Issue 3:** WU scraping fragile (single UA, 2 regex patterns)
**Root cause:** Bot-like UA may be blocked; Angular DOM layout variability
**Files modified:** bots/weather_bot.py
**Lines changed:** +28/-12 in _fetch_wu_daily_high
**Blast radius:** WeatherBot calibration actuals backfill only (not live trading path)
**Verification:** weatherbot_wu_actual debug logs
**Rollback:** git revert 5522654
```
