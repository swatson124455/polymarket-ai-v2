# WeatherBot — Bot Reference

## Status (as of 2026-03-06, Session 54)
| Field | Value |
|-------|-------|
| Enabled | YES (BOT_ENABLED_WEATHER=true) |
| Capital | $500 |
| Max bet | $50 (max_bet_usd in BotBankrollManager) |
| Kelly fraction | 0.25 (WEATHER_KELLY_FRACTION) |
| Max per city+date group | $200 (WEATHER_MAX_PER_GROUP_USD) |
| Max correlated city exposure | $500 (WEATHER_MAX_CORRELATED_EXPOSURE) |
| Daily loss limit | $500 (WEATHER_DAILY_LOSS_LIMIT) |
| VPS State | RUNNING — actively scanning ~16 groups/scan. Seasonal gap is OVER. |
| Last trade | 2026-03-06 14:38:22 UTC: São Paulo 32°C YES @ 0.0135, size=100, edge=0.9865 |
| Current state | São Paulo March 6 expired; 33 regex-matched markets in DB (all 2020 past-date). Trades when fresh markets with live CLOB prices appear. |
| Special overrides | RISK_MIN_VOL_WEATHERBOT=0, RISK_MIN_PRICE_WEATHERBOT=0.005 |

## Purpose & Strategy
Trades Polymarket temperature-bucket markets using Open-Meteo ensemble weather forecasts.

**Edge discovery flow:**
1. Fetch GFS + HRRR + GEFS (31 members) + ECMWF ENS (51 members) via Open-Meteo (free, no key)
2. Combine into 82-100 member ensemble per city+date query (6-hourly resolution, up to 7 days out)
3. Fit skew-normal distribution to ensemble spread
4. Integrate CDF across each temperature bucket's bounds → model probability per bucket
5. Compare model probability vs Polymarket YES price → edge = model_prob - market_price
6. Trade when edge ≥ 15% (WEATHER_MIN_EDGE), sized by fractional Kelly

**Multi-outcome group awareness:**
- Each city+date has ~7 bucket markets (e.g., "Will high temp in NYC exceed 85°F on July 4?")
- WeatherBot analyzes all buckets in a group to verify probabilities sum to ~1.0
- Per-group ($200) and per-city ($500) exposure caps prevent overconcentration

**Risk management layers (in order):**
1. Daily loss limit gate: skip if daily P&L ≤ -$500
2. Per-group cap: max $200 per city+date combination
3. Per-city cap: max $500 across all dates for same city
4. Lead time gate: skip markets >7 days out (WEATHER_MAX_LEAD_TIME_HOURS=168)
5. Near-expiry Kelly boosts: 2.0x (<12h), 1.5x (<24h), 1.2x (<WEATHER_HOLD_HOURS_BEFORE_RESOLUTION)
6. Cross-city regime boost: 1.2x when ≥3 US cities unanimously show warm or cold edge
7. Combined boost capped at 2.5x
8. Re-entry cooldown: 15-min block per market_id after each exit (via `_recently_exited`)

**Calibration feedback loop:**
- `weather_calibration` DB table stores forecast_temp per station+lead_bucket
- `actual_temp` filled on UTC day boundary via Open-Meteo historical archive API
- `bias = actual_temp - forecast_temp` computed and stored
- Bias per station+lead_bucket loaded every 6h and applied to model probabilities
- Cold start: calibration table empty until first markets resolve; trades without correction initially

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/weather_bot.py |
| Station registry (91 stations) | base_engine/weather/station_registry.py |
| Market mapper (question → station) | base_engine/weather/market_mapper.py |
| Forecast client (Open-Meteo) | base_engine/weather/forecast_client.py |
| Probability engine (skew-normal fit) | base_engine/weather/probability_engine.py |

## Critical Code Paths
| Stage | Method | Approx Line |
|-------|--------|-------------|
| Main scan | scan_and_trade() | ~95 |
| Single market fallback | analyze_opportunity() | ~184 |
| Group analysis (preferred path) | _analyze_group() | ~239 |
| Trade execution | _execute_weather_trade() | ~355 |
| Market discovery | _fetch_weather_markets_direct() | ~498 |
| Price enrichment via CLOB | _enrich_with_live_prices() | ~563 |
| Startup observability check | _check_weather_market_availability() | ~621 |
| UTC day boundary handler | _handle_daily_boundary() | ~650 |
| P&L restore from DB on restart | _restore_daily_pnl_from_db() | ~670 |
| Fill actual temps + bias | _maybe_update_calibration_actuals() | ~698 |
| Reload bias calibration | _maybe_reload_calibration() | ~776 |
| Persist forecast to DB | _save_forecast_to_db() | ~828 |
| Cross-city regime boost | _compute_regime_boost() (static) | ~465 |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| Open-Meteo API | YES | Free, no key; GFS/HRRR/GEFS/ECMWF ensemble |
| Open-Meteo Archive API | YES | Historical temps for calibration actuals; free |
| Polymarket API | YES | Weather market discovery (Gamma category filter unreliable) |
| CLOB /midpoint endpoint | YES | Live pricing for illiquid weather markets (no WS token) |
| PostgreSQL weather_forecasts | YES | Forecast persistence |
| PostgreSQL weather_calibration | YES | Bias calibration storage |
| PostgreSQL paper_trades | YES | Daily P&L restoration on restart |
| scipy.stats.skewnorm | YES | Distribution fitting; RuntimeWarning suppressed |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_WEATHER | true | true | Enable gate |
| WEATHER_MIN_EDGE | 0.15 | 0.15 | Minimum edge (15%) to trade |
| WEATHER_MAX_PER_GROUP_USD | 200.0 | 200.0 | Max $ per city+date group |
| WEATHER_DAILY_LOSS_LIMIT | 500.0 | 500.0 | Stop trading today if daily P&L ≤ -$500 |
| WEATHER_MAX_CORRELATED_EXPOSURE | 500.0 | 500.0 | Max $ per city across all dates |
| WEATHER_KELLY_FRACTION | 0.25 | 0.25 | Kelly multiplier (fractional Kelly) |
| WEATHER_DEFAULT_SIZE | 25.0 | 25.0 | Fallback bet size when Kelly unavailable |
| WEATHER_MAX_LEAD_TIME_HOURS | 168.0 | 168.0 | Skip if >7 days to target date |
| WEATHER_HOLD_HOURS_BEFORE_RESOLUTION | 48.0 | 48.0 | Near-expiry boost window boundary |
| WEATHER_FORECAST_CACHE_TTL | 900 | 900 | Forecast cache TTL (s) |
| SCAN_MARKET_LIMIT | 800 | 800 | Max weather markets per scan |
| RISK_MIN_VOL_WEATHERBOT | 0 | 0 | Bypass $5K volume gate for WeatherBot |
| RISK_MIN_PRICE_WEATHERBOT | not set | 0.005 | Per-bot price floor (0.5¢ min) — allows 1-2¢ weather markets |

## Market Detection Behavior
**Seasonal gap is OVER as of early March 2026. Bot is actively scanning and has traded.**

Startup log format (runs once per process start):
```
[WeatherBot] _check_weather_market_availability: DB total=800, DB weather-category=260, regex-matched=33
```
Normal scan log:
```
weatherbot_price_enriched enriched=10 skipped=40 total=50
weatherbot_scan_done best_edge=0.0 groups=15 groups_with_edge=0 regime_boost=1.0 trades=0
```

**Why groups_with_edge=0 right now (not a bug):**
- 33 regex-matched markets are all old 2020 markets with past target dates (Jan-Feb)
- `_parse_date()` infers current year (2026), so "February 15" → 2026-02-15 → already past
- `_analyze_group()` skips groups where `target_date < today`
- Bot trades when a genuinely new market appears with a future date AND live CLOB orderbook
- São Paulo March 6 was such a market and was traded successfully

**Diagnosis guide:**
- `regex-matched = 0` AND `DB weather-category = 0` → no markets at all, check ingestion
- `regex-matched = 0` AND `DB weather-category > 0` → regex needs expansion for new question phrasing
- `regex-matched > 0` AND `groups_with_edge = 0` → check if any groups have future target dates
- `groups > 0` AND `groups_with_edge = 0` → all target dates in past OR no CLOB prices for future markets
- `enriched=10 skipped=40` → 40 markets have empty orderbooks — expected for expired/stale markets
- `regex-matched > 0` AND trades still 0 with future dates → check edge gate, lead time, calibration

**Why Gamma API category=weather is unreliable:**
- Polymarket's Gamma API `?category=weather` returns ~500 markets that include politics, pop culture
- WeatherBot falls back to: DB query (min_liq=0, categories=["weather"]) + regex filter
- `_RE_WEATHER_QUICK`: matches "high/maximum temperature in {city}" patterns
- `_RE_WEATHER_ALT`: matches "degrees Fahrenheit/Celsius" patterns

**Price enrichment note:** Weather markets have `yes_price=NULL` in DB (token IDs not in WS 1000-token
subscription). `_enrich_with_live_prices()` fetches CLOB /midpoint for up to 50 markets per scan
using `yes_token_id` (numeric, not hex condition_id). Markets with empty orderbooks are skipped.

**Precipitation markets:** `market_mapper.parse_market()` requires degree symbol (°F/°C) in question.
Precipitation markets ("Will NYC have between 3-4 inches of rain?") are correctly filtered out (return None).

## Known Issues & Debug History
- **[Session 54 — FIXED]** `_recently_exited` dead code — re-entry loop bug:
  Dict was initialized but never populated. After auto-exit, same market re-entered on next scan.
  Observed: 4 complete BUY/SELL cycles in ~1h on São Paulo market.
  Fix: `self._recently_exited[opp["market_id"]] = time.monotonic()` added in `_execute_weather_trade()`.
  Commit: 31daf8e. File: bots/weather_bot.py.
- **[Session 54 — FIXED]** Wide-spread CLOB orderbook → `current_price=0.5` → fake P&L:
  `_update_current_prices()` CLOB fallback used `(best_bid+best_ask)/2` without spread-width check.
  Illiquid weather markets: bid≈0.001, ask≈0.999 → midpoint=0.5 → fake exit at 0.5 for 1.35¢ entry.
  Fix: Added `and (_best_ask - _best_bid) < 0.5` guard. Spreads ≥50% rejected; tight-spread markets unaffected.
  Commit: 00cae8a. File: base_engine/execution/position_manager.py.
- **[Session 53 — FIXED]** Volume gate blocked weather markets: `RISK_MIN_VOL_WEATHERBOT=0` added.
  `RISK_MIN_PRICE_WEATHERBOT=0.005` added to allow 1-2¢ weather markets. Commit: da6ce4d.
- **[Session 52 — ROOT CAUSE FOUND]** 0 weather markets: Confirmed seasonal gap (not a bug).
  Gamma API `category=weather` is unreliable. Bot handles 0 markets gracefully.
  Commits: d959a9c, 0e9ffb5.
- **[Session 51 — FIXED]** Station coverage: 13 → 91 stations (45 US + 46 intl). Commit: 626f8b1.
- **[Session 51 — FIXED]** Scipy RuntimeWarning on near-identical ensemble: Suppressed. Commit: 626f8b1.
- **[Session 51 — FIXED]** Calibration actuals not filling: archive API fix. Commit: 626f8b1.
- **[OPEN]** Calibration cold start: `weather_calibration` table empty until first markets resolve.
  Bot trades without bias correction initially; calibration improves over time.
- **[OPEN]** Price enrichment cap: CLOB /midpoint calls limited to 50 markets per scan.
  If >50 weather markets appear simultaneously, some may miss live pricing on first scan.
- **[OPEN]** Ensemble degenerate edge case: When ≥2 ensemble members nearly identical,
  scipy skewnorm fit degrades. RuntimeWarning suppressed; fallback uniform distribution used.

## Debugging Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Live logs
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai -f | grep WeatherBot"

# Check startup availability log (once per process)
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since 'today' | grep -i 'weather_market_avail\|DB total\|DB weather\|regex-matched'"

# Check weather markets in DB
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) as total,
         SUM(CASE WHEN active THEN 1 ELSE 0 END) as active_count
  FROM markets WHERE category='weather';\""

# Check CLOB enrichment rate in scan logs (how many markets get live prices)
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since 'today' | grep 'enrich\|enriched\|skipped' | grep -i weather | tail -20"

# Check calibration table
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT station_id, lead_bucket, COUNT(*) as rows,
         ROUND(AVG(bias)::numeric, 3) as avg_bias
  FROM weather_calibration
  WHERE actual_temp IS NOT NULL
  GROUP BY station_id, lead_bucket
  ORDER BY station_id, lead_bucket
  LIMIT 30;\""

# Check weather forecasts table (forecast persistence)
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT station_id, target_date, created_at
  FROM weather_forecasts
  ORDER BY created_at DESC LIMIT 10;\""

# Check recent WeatherBot paper trades
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT created_at, market_id, side, size, price, realized_pnl
  FROM paper_trades
  WHERE bot_name='WeatherBot'
  ORDER BY created_at DESC LIMIT 20;\""

# Check daily P&L
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT DATE(created_at) as day,
         COUNT(*) as trades,
         ROUND(SUM(realized_pnl)::numeric, 2) as daily_pnl
  FROM paper_trades
  WHERE bot_name='WeatherBot' AND realized_pnl IS NOT NULL
  GROUP BY day ORDER BY day DESC LIMIT 10;\""

# Run WeatherBot tests locally
pytest tests/ -k "weather" -v

# Check Open-Meteo is reachable from VPS
ssh -i "$KEY" "$VPS" "curl -s 'https://api.open-meteo.com/v1/forecast?latitude=40.71&longitude=-74.01&hourly=temperature_2m&forecast_days=1' | head -c 200"
```

## Next Steps / Monitoring
- [ ] When new markets appear with future dates: verify `_enrich_with_live_prices()` gets CLOB prices
- [ ] Verify `_recently_exited` 15-min cooldown fires correctly on next trade (fixed Session 54)
- [ ] Monitor calibration loop: at UTC day boundary, `weather_calibration` table should fill actual_temp for São Paulo March 6
- [ ] If >50 weather markets appear simultaneously: raise CLOB enrichment cap from 50
- [ ] Check `db_weather_regex_match` count in startup log after each ingestion cycle — new markets will increment it
