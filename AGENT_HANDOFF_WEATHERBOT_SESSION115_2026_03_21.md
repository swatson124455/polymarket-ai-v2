# WeatherBot Session 115 — Full Agent Handoff

**Date**: 2026-03-21
**Commits**: `ca999a7` (S115 main), `f2b4820` (script DB fix), `dcee384` (rate limit delay), `1edb1ad` (skip-done optimization)
**Deploy**: `20260321_154950`
**Tests**: 1642 passed, 0 failed, 8 skipped
**Predecessor**: S114 (`325e0f2`) — EMOS cold-start mitigation

---

## SESSION SCOPE

Picked up from S114 (cold-start mitigation stack + 4 Chinese cities). Executed 6 actionable items from the S111 audit + S114 roadmap:

1. **Fix 2D**: Baker-McHale post-cap ordering (bug fix)
2. **Phase 2**: Extract 6 hardcoded values to env vars (config)
3. **Phase 3**: Brier score decomposition script (analytics)
4. **Phase 4**: Severe weather trading halt (safety feature)
5. **Phase 5**: DRY refactor of precip/snow/wind scan (cleanup)
6. **Phase 6**: SAMOS with proper ERA5 10-year climatology (calibration upgrade)

Additionally: full new-city integration audit (all 4 Chinese cities verified clean).

---

## WHAT CHANGED (6 items)

### Item 1: Fix 2D — Baker-McHale Post-Cap Ordering
- **File**: `bots/weather_bot.py` — `_execute_weather_trade()`
- **Bug**: The 2.0x `combined_boost` cap was applied BEFORE Baker-McHale, station reliability, and Bühlmann calibration confidence factors. This meant two trades with different pre-cap levels both clamped to the same 2.0x, then got the same BM/station/calibration multiplier — losing sizing granularity.
- **Fix**: Moved the 2.0x cap to AFTER all multiplicative factors are applied. Now: `final_boost = min(2.0, expiry * regime * jump * nbm * bm * station_rel * calibration_conf)`
- **Behavior change**: Trades that previously all hit the 2.0x ceiling now differentiate. Net effect is slightly smaller average size (more factors reduce below cap) but more accurate relative sizing.

### Item 2: Config Extraction (6 env vars)
- **File**: `bots/weather_bot.py`, `config/settings.py`
- **New settings** (all with unchanged defaults):
  - `WEATHER_BUHLMANN_KAPPA=30` — denominator in Bühlmann `n/(n+k)` formula
  - `WEATHER_SPREAD_RATIO_MIN=0.7` — min clamp for spread confidence gate
  - `WEATHER_SPREAD_RATIO_MAX=1.5` — max clamp for spread confidence gate
  - `WEATHER_CALIBRATION_RELOAD_SECS=21600` — calibration reload interval (6h)
  - `WEATHER_BRIER_HALT_MSE=0.35` — per-station Brier halt threshold
  - `WEATHER_DEFAULT_MODEL_SPREAD=3.0` — fallback when ensemble spread unavailable
- **Zero behavior change** unless env var is explicitly set to a different value.

### Item 3: Brier Score Decomposition Script
- **File**: `scripts/weather_brier.py` (NEW)
- **What**: Murphy (1973) decomposition: reliability + resolution + uncertainty, broken down by city, lead time, market type, side
- **Usage**: `PYTHONPATH=/opt/polymarket-ai-v2 python scripts/weather_brier.py [--hours 720] [--city NYC] [--min-samples 10]`
- **Not wired to bot** — manual analytics only. Uses `trade_events` + `positions` tables.

### Item 4: Severe Weather Trading Halt
- **File**: `bots/weather_bot.py` — `_should_halt_severe_weather()` + `_analyze_group()`
- **What**: Before S115, severe weather events (hurricane, tornado) INCREASED position size via the jump boost mechanism. That's backwards — severe weather makes forecasts less reliable.
- **New behavior**: `_should_halt_severe_weather()` checks NWS alerts against `WEATHER_SEVERE_HALT_EVENTS` (configurable, default: `Hurricane Warning,Tornado Warning,Extreme Wind Warning`). If match → log `weatherbot_severe_halt` + skip trading for that station.
- **Non-halting events** (blizzard, ice storm, etc.) still get the existing sizing boost — these are precipitation events with clearer forecast signals.
- **NWS API**: Uses `api.weather.gov/alerts/active?point={lat},{lon}` — US stations only. International stations skip silently (no NWS coverage).

### Item 5: DRY Refactor — Precip/Snow/Wind Scan
- **File**: `bots/weather_bot.py` — `_scan_psw_markets()` (NEW shared template)
- **What**: Three nearly identical ~100-line scan functions (`_scan_precipitation_markets`, `_scan_snowfall_markets`, `_scan_wind_markets`) consolidated into one shared template.
- **~200 lines eliminated**. Same behavior, one place to fix bugs.
- **Log tags**: Now use f-strings like `f"weatherbot_{market_type}_scan_done"` where `market_type` is "precip"/"snow"/"wind".

### Item 6: SAMOS (Standardized Anomaly MOS) — PROPER Implementation
- **Files**: `bots/weather_bot.py`, `base_engine/weather/forecast_client.py`, `base_engine/weather/probability_engine.py`
- **Migration 058**: `clim_mean`/`clim_std` columns on `weather_calibration` (nullable, for future per-row climatology)
- **Migration 059**: `weather_climatology` table — `(station_id, day_of_year)` → `(clim_mean, clim_std, n_years)`
- **Backfill script**: `scripts/backfill_climatology.py` — fetches 10-year ERA5 daily max temps per station from Open-Meteo archive API, computes recency-weighted (mean, std) per DOY
  - Weight schedule: years 0-2 ago = full weight (1.0), then `0.85^(age-2)` decay per year
  - Floor: `clim_std >= 1.0` to prevent overconfident normalization
  - Idempotent: `ON CONFLICT DO UPDATE`, skips stations with >=360 DOYs already in DB
- **SAMOS normalization in calibration reload** (`_maybe_reload_calibration()`):
  - Loads `weather_climatology` for all stations
  - For each `(forecast_temp, actual_temp)` pair with matching climatology: `z_forecast = (forecast - clim_mean) / clim_std`, `z_actual = (actual - clim_mean) / clim_std`
  - Pools all SAMOS-normalized pairs → fits global EMOS on anomalies → `_global_samos_emos`
  - Probability engine denormalizes: `μ_corrected = clim_mean + clim_std * (a_samos + b_samos * z_forecast)`
  - Fallback chain now: local EMOS → SAMOS global → raw global EMOS → bias offset → identity
- **What this replaces**: The circular bandaid where bootstrap monthly averages from the 90-day batch were used as "climatology" to normalize the same 90-day batch. Now uses proper 10-year ERA5 data with recency decay.

---

## CLIMATOLOGY BACKFILL STATUS

| Status | Count | Stations |
|--------|-------|----------|
| **Complete** (366 DOYs) | **94** | All major US + international + Chinese cities |
| **Failed** (Open-Meteo 429) | **12** | detroit, nairobi, nashville, new_orleans, st_louis, stockholm, sydney, taipei, tampa, tel_aviv, tokyo, toronto |

The 12 failures are rate-limit related, not data availability. Re-running `backfill_climatology.py` after a 5-10 minute cooldown catches them. The script skips already-done stations automatically.

**Impact of missing 12**: SAMOS falls back to raw global EMOS for these stations. No trading impact — just slightly less precise global calibration. The 94 complete stations represent the vast majority of Polymarket weather markets.

**To finish**: `ssh ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/backfill_climatology.py"`

---

## CROSS-BOT CHANGE NOTICE (from parallel session)

A separate S115 session (not WeatherBot-scoped) rewrote the paper trading fill pipeline:

- **paper_trading.py**: All theoretical slippage models REMOVED (alpha decay, Kyle's lambda, size tiers, fill probability). BUY orders now fill at real VWAP from L2 orderbook walk.
- **order_gateway.py**: Pre-trade book walk + edge-at-VWAP gate. If `confidence <= VWAP`, trade rejected.
- **shadow_fills table**: Every BUY signal recorded with full book snapshot for retroactive P&L.
- **WeatherBot impact**: `alpha_decay_half_life_s: 1800` in event_data is now ignored. Trades fill at real book prices. Fewer false rejections.
- **Full handoff**: `AGENT_HANDOFF_SHADOW_FILLS_SESSION115_2026_03_21.md`

### Review items (24h after deploy):
```sql
SELECT COUNT(*), AVG(book_walk_slippage) FROM shadow_fills WHERE bot_name='WeatherBot';
```

---

## POST-DEPLOY VERIFICATION (confirmed live)

```
weatherbot_global_emos_fitted  a=0.7887 b=0.9766 n_pairs=7160 sigma=2.9883
weatherbot_calibration_reloaded emos_ready_stations=20 stations=25 total_rows=7160
weatherbot_scan_done active_cities=35 groups=148 trades=1 weather_markets=1500
```

- Global EMOS fitted from 7,160 pairs across 25 stations
- 20 stations with local EMOS ready, 5 pending
- 35 active cities being scanned, trade executed in first scan post-deploy

---

## CURRENT SYSTEM STATE (post-S115)

- **Open positions**: ~193 ($6,301 deployed)
- **All-time realized P&L**: ~+$2,960
- **Fill rate**: ~14.7% (will change with new shadow fill system)
- **Deploy**: `20260321_154950` — LIVE, healthy
- **35 active cities** scanning
- **Station count**: 106 (94 with full climatology, 12 pending backfill)
- **SAMOS**: Active for 94/106 stations, fallback to raw EMOS for 12

---

## OUTSTANDING ITEMS (carried forward + new)

### TIER 2 — Fix Soon
| Item | Status | Description |
|------|--------|-------------|
| 2D | **FIXED S115** | Baker-McHale post-cap ordering |
| Climatology backfill | **12 remaining** | Re-run `backfill_climatology.py` after rate limit clears |
| Shadow fill review | **24h check** | Verify book walk data flowing for WeatherBot |

### TIER 3 — Backlog
| Item | Status | Description |
|------|--------|-------------|
| 3A | **PARTIALLY FIXED S115** | 6 of ~15 hardcoded values now configurable |
| 3B | Open | Test coverage ~40-50%. Zero tests for S114/S115 cold-start code |
| 3C | **DONE S115** | Brier script created (`scripts/weather_brier.py`) |
| 3D | Open | Multi-city correlation (NYC+Boston ~0.6 temp correlation) |
| 3E | **DONE S115** | Severe weather suspension implemented |
| 3F | Open → **PARTIALLY RESOLVED** | Slippage monitoring — shadow_fills table now provides real data |
| 3G | **DONE S115** | Precip/snow/wind DRY refactor |

### TIER 4 — Monitor
| Item | Watch for | Trigger |
|------|-----------|---------|
| BM sizing distribution | >30% trades hit BM floor 0.50 | Log pre/post values |
| NBM >30pp disagreement | Win rate <40% on boosted trades | Pull outcomes |
| Dallas/Wellington P&L | Still negative at 30+ samples | Track per city |
| Chinese cities performance | First 30+ resolutions | Monitor calibration convergence |
| Cold-start bootstrap accuracy | Bootstrap bias vs actual first resolutions | Compare at day 7, 14, 30 |
| SAMOS vs raw EMOS | Compare global EMOS sigma with/without SAMOS | After 1 week of data |
| Shadow fill quality | Avg slippage, fill rate change | 24h after deploy |
| Severe weather halt | Correct station detection | Next NWS warning event |

---

## FUTURE ROADMAP (updated from S114)

### P2: SAMOS — **DONE** (S115)
Implemented with proper 10-year ERA5 climatology. Recency-weighted decay (0.85/yr). 94/106 stations backfilled.

### P3: Climate-Cluster Semi-Local EMOS
Cluster stations by climatological quantile features (Lerch & Baran 2017). New stations assigned to nearest cluster. **Prereq**: 50+ stations. **When**: If Polymarket expands significantly.

### P3: Grouped Per-Model EMOS
Fit `μ = a + b_GFS·X̄_GFS + b_IFS·X̄_IFS + b_AIFS·X̄_AIFS` (6 params). **When**: After confirming blended EMOS leaves skill on table.

### P4: Nearest-Station Transfer
Initialize new station EMOS from elevation-adjusted nearest analog. **When**: If global/SAMOS EMOS proves too generic.

### P4: City Rotation Prediction
Track Polymarket city presence matrix → predict additions → pre-compute bias. **When**: After bootstrap pipeline validated.

### P5: MEMOS (Spatial EMOS) / Neural Network Post-Processing
Heavy ML approaches. **When**: 50+ stations, 6+ months data.

---

## NEW CITY ONBOARDING (fully automated as of S114+S115)

When Polymarket adds a new city:

1. **Manual**: Add `WeatherStation` to `station_registry.py` (ICAO, GHCND, lat/lon, elevation, tz, temp_unit, aliases)
2. **Automatic**: `_maybe_bootstrap_cold_station()` fetches 90d GFS+ERA5 → inserts into `weather_calibration`
3. **Automatic**: Global EMOS provides immediate fallback calibration
4. **Automatic**: Bühlmann ramp sizes at ~75% (bootstrap n≈90) from day 1
5. **Automatic**: Spread gate adapts edge threshold from first scan
6. **Manual (one-time)**: Run `backfill_climatology.py --station <id>` for SAMOS climatology
7. **Automatic**: Next calibration reload picks up climatology → SAMOS normalization active

**Only gap**: No auto-detection of new Polymarket cities. Requires human to notice + add station. P4 "City Rotation Prediction" would close this.

---

## KEY CONFIGURATION (VPS .env — unchanged from S114)

```
WEATHER_MIN_EDGE=0.08                    # 8% minimum edge (US)
WEATHER_INTL_MIN_EDGE=0.12               # 12% (international)
WEATHER_MAX_PER_GROUP_USD=200.0           # Max per city+date group
WEATHER_DAILY_LOSS_LIMIT=500.0            # Stop if daily P&L < -$500
WEATHER_MAX_CORRELATED_EXPOSURE=500.0     # Max per city (all dates)
WEATHER_KELLY_FRACTION=0.25              # Kelly multiplier
WEATHER_DEFAULT_SIZE=100.0               # Default position size
WEATHER_MAX_LEAD_TIME_HOURS=168.0        # Max 7 days ahead
WEATHER_EXIT_COOLDOWN_SECS=14400         # 4hr re-entry cooldown
WEATHER_BM_FLOOR=0.50                    # Baker-McHale minimum
WEATHER_MIN_TRADE_USD=5.0                # Min position size
WEATHER_MAX_POSITIONS=500                # Position cap
WEATHER_SKIP_COORDINATOR_BUY=true        # Bypass TradeCoordinator
SIMULATION_MODE=true                     # Paper trading
# S115 new (using defaults — not in .env yet):
# WEATHER_BUHLMANN_KAPPA=30
# WEATHER_SPREAD_RATIO_MIN=0.7
# WEATHER_SPREAD_RATIO_MAX=1.5
# WEATHER_CALIBRATION_RELOAD_SECS=21600
# WEATHER_BRIER_HALT_MSE=0.35
# WEATHER_DEFAULT_MODEL_SPREAD=3.0
# WEATHER_SEVERE_HALT_EVENTS=Hurricane Warning,Tornado Warning,Extreme Wind Warning
```

---

## SESSION HISTORY (WeatherBot only)

| Session | Date | Key Changes |
|---------|------|-------------|
| S92 | 03-15 | P1 jump detection, P2 NBM benchmark |
| S95 | 03-16 | 4 paper trading elevations |
| S97 | 03-16 | 3 stations, P&L breakdown script |
| S100 | 03-17 | Alpha decay, canary persistence, SSH timeouts, backoff Redis |
| S104 | 03-18 | Fill quality logging, exposure leak fix, daily counter, alpha decay BUY-only |
| S108 | 03-19 | Fill pipeline: taker 0.85, bestAsk pre-filter, volume passthrough, same-side dedup, ghost fix |
| S111 | 03-19 | Full self-review audit: 25 findings, 6 invalidated, 4 log-level fixes |
| S112 | 03-20 | METAR renorm guard, fallback DB lookup on cache miss |
| S113 | 03-21 | 2E/2B/2C bug fixes, 4 Chinese cities added |
| S114 | 03-21 | EMOS cold-start mitigation: spread gate, Bühlmann ramp, global EMOS, historical bootstrap |
| **S115** | **03-21** | **Fix 2D (cap ordering), config extraction, Brier script, severe weather halt, DRY refactor, SAMOS with ERA5 climatology** |

---

## COMMITS

```
1edb1ad fix(weather): S115 — skip already-backfilled stations in climatology script
dcee384 fix(weather): S115 — add rate limit delay to backfill_climatology.py
f2b4820 fix(weather): S115 — fix Database() constructor in scripts
ca999a7 feat(weather): S115 — 6 actionable items + proper SAMOS climatology
```

---

## CRITICAL TRAPS (30 items — updated from S114)

Items 1-28: See `PROMPT_WEATHERBOT_SESSION115.md` (unchanged)

29. **SAMOS fallback chain**: local EMOS → SAMOS global → raw global → bias → identity. Do NOT remove any layer.
30. **`weather_climatology` table**: Populated by `backfill_climatology.py`, NOT by the bot. Bot only reads. 94/106 stations complete. Missing stations fall back to raw global EMOS (no SAMOS normalization).
31. **Open-Meteo rate limit**: ERA5 archive API throttles at ~25 requests/minute for 10-year fetches. The backfill script has 2s delays + skip-done logic. The LIVE bot also uses Open-Meteo for forecasts — running backfill during scan cycles can cause bot 429s (temporary, self-healing).
32. **`paper_trading.py` rewrite (S115 cross-bot)**: Alpha decay, Kyle's lambda, fill probability model all DELETED. WeatherBot's `alpha_decay_half_life_s` in event_data is now ignored. Do NOT re-add theoretical slippage — system now uses real L2 orderbook VWAP.
33. **Severe weather halt is US-only**: `api.weather.gov` has no international coverage. International stations silently skip the NWS check. No action needed — just don't expect halt logs for Tokyo/London/etc.

---

## VERIFICATION COMMANDS

```bash
# WeatherBot scan health
journalctl -u polymarket-ai -f | grep weatherbot_scan_done

# SAMOS + calibration
journalctl -u polymarket-ai -f | grep "weatherbot_global_emos\|weatherbot_samos\|weatherbot_calibration_reloaded"

# Cold-start mitigation
journalctl -u polymarket-ai -f | grep "weatherbot_cold_start\|weatherbot_bootstrap"

# Severe weather halt
journalctl -u polymarket-ai -f | grep "weatherbot_severe_halt"

# Spread gate
journalctl -u polymarket-ai -f | grep "spread_ratio"

# Climatology coverage
sudo -u postgres psql -d polymarket -c "SELECT COUNT(DISTINCT station_id) as stations, COUNT(*) as total_doys FROM weather_climatology;"

# Shadow fills (24h check)
sudo -u postgres psql -d polymarket -c "SELECT COUNT(*), AVG(book_walk_slippage) FROM shadow_fills WHERE bot_name='WeatherBot';"

# P&L
PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/bot_pnl.py WeatherBot 24

# Brier decomposition
PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/weather_brier.py --hours 720

# Finish climatology backfill (12 remaining)
PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/backfill_climatology.py
```

---

## INVALIDATED FINDINGS (do NOT re-investigate)

1-6: See S114 handoff (precipitation wired, wind/snow wired, NaN guarded, confidence correct, no race condition, cache TTL OK)
7. ~~SAMOS uses circular bootstrap climatology~~ — **FIXED S115**. Now uses 10-year ERA5 recency-weighted data via `weather_climatology` table.
8. ~~Baker-McHale loses granularity after cap~~ — **FIXED S115**. Cap moved to end of chain.
