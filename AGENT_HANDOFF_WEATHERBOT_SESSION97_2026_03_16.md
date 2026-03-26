# AGENT HANDOFF — WeatherBot Session 97 (2026-03-16)
**Date**: 2026-03-16
**Bot Scope**: WeatherBot-only session (scope lock: non-negotiable)
**Prior Code Session**: Session 95 (4 paper trading execution realism elevations)
**Prior Shared Session**: Session 96 (MirrorBot cap raise + EsportsBot conformal fix)

---

## HARD RULES (READ BEFORE DOING ANYTHING)

### Scope Lock (NON-NEGOTIABLE)
1. **ONLY touch WeatherBot files** unless fixing a shared module bug that directly breaks WeatherBot
2. **NEVER add unsolicited features** — only fix/build what this handoff or the user explicitly requests
3. **Observation duty**: Note issues and surface to user. Do NOT silently implement.
4. **Read CLAUDE.md** before modifying any file — it contains the Prime Directive and Rules of Engagement

### Critical Traps (WILL BREAK THINGS IF IGNORED)
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL"
- **trade_events is P&L AUTHORITY** — never read `paper_trades` for P&L. SELL/EXIT trades only exist in trade_events
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass
- **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer
- **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
- **Python 3.13 scoping**: `from X import Y` inside a function makes Y a local for the ENTIRE function. Any use of Y BEFORE that import line -> `UnboundLocalError`. NEVER use local imports that shadow top-level names
- **trade_events immutability trigger**: `trg_trade_events_immutable` prevents DELETE/UPDATE. Must `DISABLE TRIGGER` then re-enable for data cleanup
- **RESOLUTION event idempotency**: `ON CONFLICT (idempotency_key, event_time)` is BROKEN on partitioned tables. `insert_trade_event()` uses atomic INSERT...SELECT with WHERE NOT EXISTS for RESOLUTION events instead
- **trade_events JSONB column is `event_data`** — NOT `metadata_json`. `paper_trades` has NO `resolved_pnl` column (it's `resolved_at`)
- **positions table columns**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`
- **prediction_log columns**: NO `rejection_reason`. Use `trade_executed` (bool) + `model_name`
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime string
- **asyncpg timestamps**: `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`. `created_at` has NO DEFAULT
- **BOT_REGISTRY=14 bots** — shared module change requires all 14 verified
- **`paper_trades` has NO `metadata` JSONB column**
- **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time
- **P&L formula**: `cost = entry_price * size` (ALL sides), `uPnL = (current - entry) * size` (ALL sides). NEVER invert for NO positions
- **`_market_meta_cache` in MirrorBot**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
- **`_pending_db_writes` list**: Populated under `_trade_lock`, drained AFTER lock release in `place_order()`. NEVER use `asyncio.create_task()` — must be `await`ed.
- **`_pending_correlation_ids` set**: In-memory idempotency during lock->DB gap. Cleaned up in `finally` blocks.

---

## SESSION 97 COMPLETED WORK (DEPLOYED)

### Deploy via SCP+restart at ~16:50 UTC 2026-03-16
### Commit: `8c71dfb`

### What Was Built

#### 1. Geographic Expansion — 3 New Stations in `station_registry.py`

Added 3 stations to cover Great Plains thermal volatility and lake-effect zones:

| Station | ICAO | GHCND | Region | Rationale |
|---------|------|-------|--------|-----------|
| **Wichita, KS** | KICT | USW00003928 | Great Plains | High thermal volatility corridor |
| **Cleveland, OH** | KCLE | USW00014820 | Great Lakes | Lake-effect temperature swings |
| **Erie, PA** | KERI | USW00014860 | Great Lakes | Lake-effect snow/temp zone |

- All 3 have `has_asos_1min=True`, proper timezone, elevation, aliases
- OKC, Omaha, Buffalo already existed — verified before adding
- Registry: 98 → 101 stations
- GHCND IDs verified via web search against NOAA records

#### 2. P&L Breakdown Script — `scripts/weather_pnl_breakdown.py` (NEW)

Analyzes WeatherBot profitability across 3 dimensions:
- **By city**: Which cities generate the most P&L
- **By side**: YES vs NO performance
- **By lead time bucket**: <6h, 6-24h, 24-48h, 48-72h, 72-120h, 120h+

Usage:
```bash
# On VPS:
source /opt/pa2-shared/venv/bin/activate && cd /opt/polymarket-ai-v2
PYTHONPATH=. python3 scripts/weather_pnl_breakdown.py          # all-time
PYTHONPATH=. python3 scripts/weather_pnl_breakdown.py 168      # last 7 days
```

Reads `trade_events` (realized P&L from EXIT/RESOLUTION) and `positions` (unrealized). City and lead_time extracted from ENTRY event_data.

#### 3. S95 Elevation Monitoring — All Confirmed Working

Verified on VPS logs (1-hour windows):
- **Kyle's lambda**: ~79 events/hr — active, adding ~7bps slippage via DEFAULT_LAMBDA=0.5
- **Fill model rejections**: ~129/hr — realistic fills working
- **Resolution proximity**: **19 events/hr** — NOW FIRING (was 0 at S95 deploy). Markets naturally approaching resolution. **S95 P3 resolved — no code change needed.**
- **Alpha decay**: 0 events — expected (WeatherBot passes `latency_ms=None`)
- **Cross-scan impact**: Sporadic — fires on repeat-buy same market within 60s

---

## P&L ANALYSIS (from `weather_pnl_breakdown.py`)

### All-Time Summary
```
Realized P&L:   +$2,727.58
Unrealized:       -$46.12
Open positions:      347 (cost basis: $7,139)
Entries:           1,841
Closed:              667 (409W / 258L = 61% win rate)
```

### By Side (KEY FINDING)
| Side | P&L | Closed | Win Rate |
|------|-----|--------|----------|
| **NO** | **+$1,798** | 444 | **72%** |
| YES | +$930 | 223 | 41% |

NO-side trades are the primary profit driver at 72% win rate. Confirms favourite-longshot bias exploitation is working.

### By City (limited — city tracking added recently)
| City | P&L | Closed | Win Rate |
|------|-----|--------|----------|
| unknown (pre-tracking) | +$2,610 | 649 | 61% |
| Tel Aviv | +$116 | 3 | 100% |
| Miami | +$0.57 | 2 | 100% |
| Paris | +$0.50 | 3 | 100% |
| Ankara | +$0.40 | 4 | 100% |
| London | -$0.00 | 1 | 0% |

**Note**: Most closed trades predate city metadata in event_data. 20+ named cities have open positions with 0 closed trades — will populate as they resolve. Paris historically -$384 (from S95 handoff) may still be an issue once older positions resolve.

### By Lead Time
Almost entirely "unknown" — lead_time_hours only recently added to event_data. One closed trade at 72-120h (-$0.11). Data will improve as recent entries resolve.

---

## FILES MODIFIED THIS SESSION

| File | Changes |
|------|---------|
| `base_engine/weather/station_registry.py` | +3 stations: Wichita (KICT), Cleveland (KCLE), Erie (KERI). Inserted before `"honolulu"` entry. |
| `scripts/weather_pnl_breakdown.py` | NEW — P&L breakdown by city, side, lead time |

---

## WEATHERBOT P&L (as of 16:57 UTC 2026-03-16)

### Current:
- **Realized**: +$2,727.58 (ENTRY=1841, EXIT=~233, RESOLUTION=~434)
- **Unrealized**: -$46.12
- **Open positions**: 347, cost basis $7,139
- **Daily P&L**: +$388.90
- **Win rate**: 61% (409W / 258L)
- **Strategy**: Primarily NO-side (72% win rate), exploiting favourite-longshot bias

### P&L Scripts
```bash
# On VPS:
source /opt/pa2-shared/venv/bin/activate && cd /opt/polymarket-ai-v2

# Standard P&L:
PYTHONPATH=. python3 scripts/bot_pnl.py WeatherBot 24

# City/side/lead-time breakdown:
PYTHONPATH=. python3 scripts/weather_pnl_breakdown.py        # all-time
PYTHONPATH=. python3 scripts/weather_pnl_breakdown.py 168    # last 7 days
```

---

## WEATHERBOT ARCHITECTURE (COMPLETE)

### Data Flow (per scan cycle)
```
1. DISCOVERY      -> Gamma API tag_slug=temperature -> WeatherMarketMapper groups by (city, date)
2. FORECASTING    -> WeatherForecastClient -> Open-Meteo GFS/GEFS/ECMWF/HRRR ensembles (133 members)
3. CALIBRATION    -> EMOS (a + b*X_bar, sigma) per station/lead-time/regime + isotonic tail calibration
4. PROBABILITY    -> WeatherProbabilityEngine -> skew-normal CDF integration per bucket
5. NBM BENCHMARK  -> compute_nbm_benchmark() -> N(nbm_high, sigma) CDF per bucket (US stations only)
6. EDGES          -> model_prob - market_price, sorted by |edge|
7. REGIME         -> ENSO (Nino 3.4), cross-city warm/cold detection, severe weather alerts, AFD uncertainty
8. JUMP DETECT    -> forecast_delta vs prior model run -> sizing boost when |delta| >= 3 deg F
9. SIZING         -> Fractional Kelly (0.25) x regime boost x near-expiry boost x jump boost x NBM boost
                     x drawdown compression x Smoczynski-Tomkins multi-bucket x Baker-McHale uncertainty
10. RISK CHECKS   -> group exposure cap, city exposure cap, daily loss limit, position count limit
11. EXECUTION     -> place_order(side="YES"/"NO") -> OrderGateway -> PaperTradingEngine
12. PAPER ENGINE  -> S95 elevations: alpha decay -> base slippage -> kyle lambda -> cross-scan -> resolution proximity -> fill model
13. RESOLUTION    -> METAR T-group override (<6h) + WU scraping + resolution_backfill
14. PERSISTENCE   -> Redis (exit cooldowns, 429 cooldowns), DB (exposure, P&L), daily_counters
```

### File Map (WeatherBot-specific)
```
bots/weather_bot.py                              (~3,720 lines) — Main bot: scan, analyze, trade, calibrate
base_engine/weather/station_registry.py          (1,500+ lines) — 101 city registry (ICAO, GHCND, aliases, models)
base_engine/weather/forecast_client.py           (~1,380 lines) — Multi-model ensemble fetching + jump detection
base_engine/weather/market_mapper.py             (1,105 lines)  — Market text -> TemperatureBucket/Group parsing
base_engine/weather/probability_engine.py        (~500 lines)   — Skew-normal CDF, EMOS, isotonic tail, Kelly, NBM benchmark
base_engine/weather/metar_client.py              (236 lines)    — Aviation Weather Center METAR API
base_engine/weather/precipitation_engine.py      (231 lines)    — Gamma distribution for precip/snow/wind
base_engine/weather/asos_onemin_client.py        (145 lines)    — IEM 1-minute ASOS data (US only)
scripts/weather_pnl_breakdown.py                 (189 lines)    — P&L by city/side/lead-time
```

---

## LIVE CONFIG (VPS — unchanged from S95)

### WeatherBot
```
Capital:            $20,000
Kelly fraction:     0.25
Max bet:            $300
Max daily:          $10,000
Max positions:      500
Min edge (US):      0.08
Min edge (intl):    0.12
Forecast cache:     900s (15min)
Rate limit:         120 req/min (Open-Meteo)
Group concurrency:  12
Scan interval:      60s (ECMWF) / 90s (GFS) / 120s (HRRR) / 300s (default)
Jump threshold:     3.0 deg F
Jump max boost:     1.5x
NBM disagree:       15pp
```

### Paper Trading Engine (ALL bots)
```
PAPER_REALISTIC_FILLS=true
PAPER_DEFAULT_SPREAD=0.04
PAPER_TAKER_FEE_BPS=0
PAPER_KYLE_LAMBDA_ENABLED=true            # S95
PAPER_CROSS_SCAN_IMPACT_ENABLED=true      # S95
PAPER_ALPHA_DECAY_HALF_LIFE_S=300         # S95 (5 min)
PAPER_RESOLUTION_PROXIMITY_ENABLED=true   # S95
```

---

## STATE PERSISTENCE (ALL GAPS CLOSED)
| State | Mechanism | Status |
|-------|-----------|--------|
| `_daily_exposure_usd` | `daily_counters` 60s flush + SIGTERM + startup restore | Done |
| `_group/_city_exposure` | `_restore_exposure_from_db()` from open paper_trades | Done |
| Exit cooldowns | Redis TTL `_save/_restore_exits_from_redis()` | Done |
| Open positions | `order_gateway.seed_positions_from_db()` | Done |
| Forecast 429 cooldowns | Redis persistence in `forecast_client` | Done |
| Daily P&L | `_restore_daily_pnl_from_db()` from trade_events | Done |
| Prior forecasts (jump detect) | In-memory only (loss = 1 scan re-seed, not financial risk) | Done |

---

## ACTIVE BOTS (post-S97)
| Bot | P&L | Notes |
|-----|-----|-------|
| MirrorBot | +$18,469 realized (fantasy @ 100% fills) | RTDS fast-path, Kelly=0.25 |
| WeatherBot | +$2,728 realized | 347 open, ~434/1841 resolved, 61% WR, S95 elevations active |
| EsportsBot | -$22 realized | ~7 open, 62/72 resolved |
| EsportsLiveBot | Active | — |
| EsportsSeriesBot | Active | — |
| 9 others | Disabled | BOT_ENABLED_* flags |

---

## OUTSTANDING ITEMS (WEATHERBOT-SCOPED)

| Priority | Item | Status |
|----------|------|--------|
| **P2** | ~600 markets still unresolved in traded_markets | Genuinely open — resolving naturally |
| **P3** | `ingest_everything()` >600s timeout observed | Master timeout (40min) catches this |
| **P3** | Alpha decay not firing for WeatherBot | Needs `latency_ms` in event_data — low priority |
| **P3** | City/lead-time metadata sparse in closed trades | Most closed trades predate event_data tracking; will improve as recent entries resolve |
| **P5** | Remove diagnostic logging (session_factory warning) | Shared module — not in WeatherBot scope |
| **Future** | Paris market deep-dive | Historically -$384 per S95; current data shows +$0.50 but only 3 closed. Watch as older positions resolve |
| **Future** | Kalshi cross-platform arbitrage | P5 from S95 article analysis (8-16h effort) |

### Resolved Items (do not re-open)
- ~~P3: Resolution proximity not firing~~ -> **NOW FIRING** — 19 events/hr confirmed S97. Markets naturally approaching resolution.
- ~~P3: Geographic expansion~~ -> DONE S97 (Wichita, Cleveland, Erie)
- ~~P4: Lake-effect station coverage~~ -> DONE S97 (Cleveland, Erie)
- ~~P1: Model-run jump detection~~ -> DONE S92
- ~~P2: NBM CDF benchmark~~ -> DONE S92
- ~~P0: Scheduler death~~ -> Fixed S90
- ~~P0: RESOLUTION dedup~~ -> Fixed S87
- ~~P1: Resolution backfill~~ -> Fixed S85
- ~~S95: 4 execution realism elevations~~ -> DONE, deployed `20260316_104320`

---

## INFRASTRUCTURE

### VPS
- **Host**: Ubuntu-3 at 34.251.224.21 (16GB/4vCPU)
- **SSH key**: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **Service**: `polymarket-ai` (systemd)
- **Working dir**: `/opt/polymarket-ai-v2` (symlink to release)
- **Env file**: `/opt/pa2-shared/.env`
- **Venv**: `/opt/pa2-shared/venv` (must activate for scripts)
- **Python**: 3.13

### Deploy
```bash
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
# Rollback:
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```

**Note**: `deploy.sh` may fail with `tar: file changed as we read it` (exit code 1 + `set -euo pipefail`). Workaround: SCP individual files to `/tmp/`, then `sudo cp` to target + `sudo systemctl restart polymarket-ai`.

### Post-Deploy Checks
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21

# WeatherBot health:
sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager | grep -iE "WeatherBot|weather|scan_cycle"

# S95 elevation events:
sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager | grep -iE "paper_fill_as_baseline|paper_no_fill|paper_alpha_decay|paper_cross_scan|paper_resolution_prox"

# Errors:
sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep -iE "error|exception|traceback" | grep -v DEBUG

# P&L:
source /opt/pa2-shared/venv/bin/activate && cd /opt/polymarket-ai-v2 && PYTHONPATH=. python3 scripts/bot_pnl.py WeatherBot 12
```

### Tests
```bash
# Full suite (must pass):
pytest --ignore=tests/unit/test_dashboard_async_worker.py --ignore=tests/unit/test_web3_compatibility_fixes.py

# WeatherBot-specific:
pytest tests/unit/test_weather_bot.py -v
```

---

## RECENT COMMITS (relevant)
```
8c71dfb S97: geographic expansion (Wichita/Cleveland/Erie) + P&L breakdown script
8a36e98 fix(esports): S96 kill 0.50 fallback + conformal sizing fix + market-price-as-prior
2a87cdc S96: raise MirrorBot position caps 200->400 to clear bottleneck
bd54cdc feat(paper): S95 four execution realism elevations — alpha decay, Kyle's lambda, cross-scan impact, resolution proximity
dcce8c5 feat(paper): S95 five paper trading realism elevations
```

---

## SESSION HISTORY (WEATHERBOT)
| Session | Date | Key Work |
|---------|------|----------|
| 61 | 2026-03-08 | Initial WeatherBot build |
| 62 | 2026-03-08 | Bankroll sizing, station registry |
| 67 | 2026-03-09 | Multi-model ensemble, EMOS |
| 69 | 2026-03-09 | Precipitation engine, Gamma distribution |
| 80 | 2026-03-12 | Resolution-day METAR override |
| 81 | 2026-03-12 | WU scraping, tail calibration |
| 85 | 2026-03-13 | 3 root cause fixes, 544 markets resolved |
| 87 | 2026-03-14 | RESOLUTION dedup fix, P&L correction |
| 90 | 2026-03-14 | Advisory lock fix, master timeout, 3 cities |
| 92 | 2026-03-15 | Model-run jump detection + NBM CDF benchmark |
| 95 | 2026-03-16 | 4 paper trading execution realism elevations |
| **97** | **2026-03-16** | **Geographic expansion (3 stations) + P&L breakdown script + S95 monitoring verification** |

---

## FEEDBACK RULES (CRITICAL — READ AND OBEY)

### 1. Scope Lock (`memory/feedback_scope_lock.md`)
NEVER add features, configs, or code changes not explicitly requested. Origin: Session 90, user caught unsolicited `WEATHER_CITY_BLACKLIST` and was furious. Zero tolerance.

### 2. P&L Math (`memory/feedback_pnl_math.md`)
P&L formulas are UNIFORM for YES and NO. `cost = entry_price * size`, `uPnL = (current - entry) * size`. NEVER use `(1 - entry_price)` for NO positions. Prices are token-specific.

### 3. Bot Sessions (`memory/feedback_bot_sessions.md`)
Each session is scoped to a single bot. Shared infra changes OK only if they fix a scoped-bot bug. Cross-bot changes require explicit user approval.

---

## WHAT THE NEXT SESSION SHOULD DO

1. **Wait for city/lead-time P&L data to mature** — Re-run `weather_pnl_breakdown.py` in 2-3 days when more recent entries (with city metadata) have resolved. This will reveal which cities and lead times are profitable vs losing.

2. **Paris market investigation** — Once older Paris positions resolve, check if it's consistently negative. May need special handling (wider min_edge for international cities, or Paris-specific calibration).

3. **NO-side edge analysis** — 72% win rate on NO vs 41% on YES is a significant asymmetry. Investigate whether to lean harder into NO-side or fix YES-side calibration.

4. **P5: Kalshi cross-platform arbitrage** — New module, new API integration. 8-16h effort.

5. **Slow scan investigation** — Scans running 25-85s (flagged as "Slow scan cycle"). Not critical but may indicate room for optimization in forecast fetching or trade execution.

**Or**: Follow user instructions. Scope lock applies.

---

## ROLLBACK

```bash
# Revert S97:
git revert 8c71dfb
bash deploy/deploy.sh

# S97 is additive-only (new stations + new script), so rollback has no risk.
# Stations can also be removed individually from station_registry.py.
```
