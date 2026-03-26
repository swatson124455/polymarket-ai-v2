# AGENT HANDOFF — WeatherBot Session 95 (2026-03-16)
**Date**: 2026-03-16
**Bot Scope**: WeatherBot-only session (scope lock: non-negotiable)
**Prior Code Session**: Session 92 (model-run jump detection + NBM CDF benchmark)
**Prior Shared Session**: Session 94 (MirrorBot latency, lock-free DB, RTDS fast-path)

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

## SESSION 95 COMPLETED WORK (DEPLOYED)

### Deploy `20260316_104320` — All changes live on VPS
### Commit: `bd54cdc`

### What Was Built: 4 Paper Trading Execution Realism Elevations

All 4 elevations are BUY-only, feature-flagged (default ON), additive to existing slippage/fill calculations. SELL always fills unchanged. Shared code in `paper_trading.py` — benefits ALL 14 bots.

---

### Elevation 1: Alpha Decay (replaces S91 linear latency drift)

**What it does**: Exponential signal deterioration. Old model: linear 10 bps/sec above 500ms threshold. New model: `decay = exp(-ln2 * latency_s / half_life_s)`. No threshold — applies proportionally to ALL latencies.

**Implementation** (`paper_trading.py`):
- Module-level function `_alpha_decay_factor(latency_ms, half_life_s=300.0)` -> float [0, 1]
- Replaces the old `if latency_ms > 500` block (lines 444-453 pre-edit)
- `decay_slip_bps = int((1.0 - decay_factor) * 100)` — max 100 bps at full decay
- Per-bot override via `event_data["alpha_decay_half_life_s"]`
- **Default half-life**: 300s (5 min)
- **Setting**: `PAPER_ALPHA_DECAY_HALF_LIFE_S` (default: 300)

**Current status**: NOT firing for WeatherBot (passes `latency_ms=None`). Would fire for any bot that passes latency_ms in event_data.

---

### Elevation 2: Kyle's Lambda Adverse Selection Penalty

**What it does**: Wires existing `MarketImpactEstimator` (at `base_engine/features/market_impact.py`) into fill decisions. High Kyle's lambda = market historically adversely selected our fills = reduce fill probability + add slippage.

**Implementation** (`paper_trading.py`):
- Import `MarketImpactEstimator, DEFAULT_LAMBDA` at top of file
- `_market_impact_estimator` and `_kyle_lambda_cache` (1h TTL) added to `__init__`
- Async method `_get_kyle_lambda(market_id)` — cached lookup, DEFAULT_LAMBDA (0.5) on miss
- In `_place_order_locked` (BUY only):
  - Slippage: `_lambda_slip_bps = int(kyle_lambda * 15)` added to `slippage_bps` (lambda=0.5 -> +7bps)
  - Fill prob: `_as_penalty = max(0.3, 1.0 - kyle_lambda * 0.3)` multiplied into `_fill_prob`
- **Setting**: `PAPER_KYLE_LAMBDA_ENABLED` (default: true)

**Current status**: Active. Falls back to DEFAULT_LAMBDA=0.5 when `fill_analysis` table lacks data for a market (most WeatherBot markets). Adds ~7 bps slippage.

---

### Elevation 3: Cross-Scan Cumulative Impact

**What it does**: Tracks cumulative price drift from own prior orders within 60s. Second+ BUY on the same `market_id` gets worse fills.

**Implementation** (`paper_trading.py`):
- `_scan_impact: Dict[str, Tuple[float, float]]` in `__init__` — key=market_id, value=(cumulative_bps, monotonic_ts)
- Before `_apply_slippage()`: lookup cumulative impact, add to `slippage_bps` if within 60s (capped at 200 bps)
- After BUY fill: update tracker with this order's sqrt_impact_bps contribution
- Lazy cleanup when dict exceeds 100 entries
- **Setting**: `PAPER_CROSS_SCAN_IMPACT_ENABLED` (default: true)

**Current status**: Active. Fired 1 event in first 10 min (200 bps on MirrorBot repeat-buy). Rarely fires for WeatherBot (different markets each scan).

---

### Elevation 4: Resolution-Proximity Adverse Selection

**What it does**: Near market resolution, informed flow dominates. Escalating slippage multiplier + fill probability reduction.

**Implementation**:
- Module-level `_resolution_proximity_penalty(hours)` -> `(slip_mult, fill_mult)`:
  - `>6h`: (1.0, 1.0) — no penalty
  - `2-6h`: (1.5, 0.9)
  - `0.5-2h`: (2.0, 0.7)
  - `<0.5h`: (3.0, 0.5)
- Reads `hours_to_resolution` from `event_data["lead_time_hours"]` in `_place_order_locked`
- Applies `slip_mult` to `slippage_bps` and `fill_mult` to `_fill_prob`
- **weather_bot.py line 2122**: Added `"lead_time_hours": lead_time` to event_data dict (1-line change)
- **Setting**: `PAPER_RESOLUTION_PROXIMITY_ENABLED` (default: true)

**Current status**: NOT firing. WeatherBot trades with short lead_time get blocked upstream by liquidity gate before reaching paper engine.

---

### Integration Order in `_place_order_locked` (BUY path)

```
1. [S95] Alpha decay           -> modifies price (replaces S91 latency drift)
2. [PRE]  Base slippage calc   -> computes slippage_bps (_size_dependent_slippage_bps)
3. [S95] Sqrt market impact    -> adds sqrt_impact_bps to slippage_bps
4. [S95] Kyle's lambda slip    -> adds _lambda_slip_bps to slippage_bps
5. [S95] Cross-scan impact     -> adds cumulative_bps to slippage_bps
6. [S95] Resolution proximity  -> multiplies slippage_bps by slip_mult
7. [PRE]  _apply_slippage()    -> applies total slippage to price
8. [PRE]  Fill probability     -> computes _fill_prob
9. [S95] Time-of-day mult      -> multiplies _fill_prob
10.[S95] Kyle's lambda penalty  -> multiplies _fill_prob
11.[S95] Resolution proximity   -> multiplies _fill_prob
12.[PRE]  Fill/no-fill decision -> random vs _fill_prob
13.[S95] Cross-scan update      -> record impact on fill
```

---

## FILES MODIFIED THIS SESSION

| File | Changes |
|------|---------|
| `base_engine/execution/paper_trading.py` | 4 elevations: alpha decay function, resolution proximity function, Kyle's lambda cache+method, cross-scan tracker, integration in `_place_order_locked` |
| `config/settings.py` | 4 new settings: `PAPER_KYLE_LAMBDA_ENABLED`, `PAPER_CROSS_SCAN_IMPACT_ENABLED`, `PAPER_ALPHA_DECAY_HALF_LIFE_S`, `PAPER_RESOLUTION_PROXIMITY_ENABLED` |
| `bots/weather_bot.py` | 1-line: added `"lead_time_hours": lead_time` to event_data dict (line ~2122) |
| `tests/unit/test_paper_fill_probability.py` | 4 new test classes (17 tests): TestAlphaDecay, TestKyleLambda, TestCrossScanImpact, TestResolutionProximity. Updated 2 existing tests for alpha decay. |

---

## POST-DEPLOY ELEVATION EVENT COUNTS

### 10-minute window (immediately after deploy):
- `paper_fill_as_baseline`: 47 events — Kyle's lambda active
- `paper_no_fill`: 124 — fill model rejecting
- `paper_partial_fill`: 47 — partial fills active
- `paper_cross_scan_impact`: 1 event (200 bps, MirrorBot)
- `paper_alpha_decay`: 0 (expected — no latency_ms passed by any bot currently)
- `paper_resolution_proximity`: 0 (expected — blocked upstream)

### 6-hour overnight total: 415 elevation events

### Overnight errors (6 total, all transient — NO ACTION NEEDED):
- 3 Gamma circuit breaker trips (auto-recover 60s — by design)
- 1 SQLAlchemy pool cleanup (normal connection lifecycle)
- 1 signal ingestion traceback (test artifact, not production)

---

## WEATHERBOT P&L (as of 15:56 UTC 2026-03-16)

### Last 12 hours:
- Realized (exits): +$37.85
- Realized (resolutions): +$20.67
- Unrealized: -$18.13
- **Net P&L (12h)**: +$40.39

### Last 5 hours (post-deploy only):
- Realized (exits): +$10.55
- Realized (resolutions): -$9.46
- Unrealized: -$17.32
- **Net P&L (5h)**: -$16.22

### All-time:
- **Realized**: +$2,724.96 (ENTRY=1807, EXIT=233, RESOLUTION=425)
- **Open positions**: 330, cost basis $6,540, mkt value $6,523
- **Strategy**: Primarily NO-side (72%), exploiting favourite-longshot bias

### P&L Script
```bash
# On VPS:
source /opt/pa2-shared/venv/bin/activate && cd /opt/polymarket-ai-v2 && PYTHONPATH=. python3 scripts/bot_pnl.py WeatherBot <hours>
```
- Cannot run locally (DB password mismatch on Windows)
- **Canonical script**: `scripts/bot_pnl.py` — reads `trade_events` (realized) + `positions` (unrealized)

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

### Key Algorithms
- **EMOS**: mu_emos = a + b*X_bar, sigma_emos — fitted via OLS, per-station, per-lead-time
- **Isotonic tail calibration**: Data-driven tail discount from >=50 resolved events
- **Baker-McHale factor**: `k* = 1/(1 + sigma^2)` — ensemble spread -> sizing reduction
- **Smoczynski-Tomkins**: Optimal allocation across mutually exclusive temperature buckets
- **Fractional Kelly**: 0.25 x near_expiry_boost (up to 2.0x) x regime_boost (1.2x) x drawdown_compression
- **METAR resolution-day override**: Within 6h of resolution, running daily max from T-groups replaces ensemble
- **F->C->F rounding**: `_near_boundary()` -> 50% confidence reduction when ensemble mean within 0.5 deg F of bucket edge
- **Climate blending**: 0-40% ramp at 72h-168h lead time
- **WU scraping**: `_fetch_wu_daily_high()` — 4-pattern regex, sanity-checked vs Open-Meteo
- **Model-run jump detection** (S92): Store prior ensemble mean per (station, date), boost sizing when |delta| >= 3 deg F
- **NBM CDF benchmark** (S92): N(nbm_high, sigma) where sigma scales with lead time. Flag when |NBM_prob - market| >= 15pp
- **Alpha decay** (S95): `exp(-ln2 * t / half_life)` — exponential signal deterioration
- **Kyle's lambda** (S95): Market impact from fill_analysis OLS regression -> slippage + fill penalty
- **Cross-scan impact** (S95): Cumulative own-order drift within 60s window -> worse fills on repeat buys
- **Resolution proximity** (S95): Escalating slippage/fill penalty as market approaches resolution

### File Map (WeatherBot-specific)
```
bots/weather_bot.py                              (~3,720 lines) — Main bot: scan, analyze, trade, calibrate
base_engine/weather/station_registry.py          (1,447 lines) — 50+ city registry (ICAO, GHCND, aliases, models)
base_engine/weather/forecast_client.py           (~1,380 lines) — Multi-model ensemble fetching + jump detection
base_engine/weather/market_mapper.py             (1,105 lines) — Market text -> TemperatureBucket/Group parsing
base_engine/weather/probability_engine.py        (~500 lines)  — Skew-normal CDF, EMOS, isotonic tail, Kelly, NBM benchmark
base_engine/weather/metar_client.py              (236 lines)   — Aviation Weather Center METAR API
base_engine/weather/precipitation_engine.py      (231 lines)   — Gamma distribution for precip/snow/wind
base_engine/weather/asos_onemin_client.py        (145 lines)   — IEM 1-minute ASOS data (US only)
```

### Shared Execution Pipeline (modified in S95)
```
base_engine/execution/paper_trading.py           (~1,017 lines) — Paper engine with S95 elevations
base_engine/execution/order_gateway.py           — OrderGateway (RTDS fast-path from S94)
base_engine/features/market_impact.py            (155 lines)   — Kyle's lambda + sqrt impact estimator
config/settings.py                               — All config including 4 new S95 PAPER_ settings
```

---

## LIVE CONFIG (VPS — as deployed post-S95)

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
PAPER_LATENCY_DRIFT_BPS_PER_SEC=10       # Legacy, replaced by alpha decay
PAPER_KYLE_LAMBDA_ENABLED=true            # S95
PAPER_CROSS_SCAN_IMPACT_ENABLED=true      # S95
PAPER_ALPHA_DECAY_HALF_LIFE_S=300         # S95 (5 min)
PAPER_RESOLUTION_PROXIMITY_ENABLED=true   # S95
```

### Global
```
SIMULATION_MODE=true (paper trading)
ALL BOTS: capital=$20000, max_bet=$300, max_daily=$10000
MIRROR_ADAPTIVE_SAFETY=false              # Disabled — drawdown formula bugged (S94)
MIRROR_SKIP_COORDINATOR_BUY=true          # S94
MIRROR_RTDS_FAST_PATH=true                # S94
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

## ACTIVE BOTS (post-S95)
| Bot | P&L | Notes |
|-----|-----|-------|
| MirrorBot | +$18,469 realized (fantasy @ 100% fills) | RTDS fast-path, 183 open, Kelly=0.25 |
| WeatherBot | +$2,725 realized | 330 open, 425/1807 resolved, S95 elevations active |
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
| **P3** | Alpha decay not firing for WeatherBot | Needs `latency_ms` in event_data — low priority, WeatherBot has no meaningful signal latency |
| **P3** | Resolution proximity not firing | Blocked upstream by liquidity gate — consider lowering gate or adding lead_time check before gate |
| **P5** | Remove diagnostic logging (session_factory warning) | Shared module — not in WeatherBot scope |
| **Future** | Geographic expansion (Great Plains corridor) | P3 from article analysis |
| **Future** | Lake-effect snow / wind gust market expansion | P4 from article analysis |
| **Future** | Kalshi cross-platform arbitrage | P5 from article analysis (8-16h effort) |

### Resolved Items (do not re-open)
- ~~P1: Model-run jump detection~~ -> DONE S92
- ~~P2: NBM CDF benchmark~~ -> DONE S92
- ~~P0: Scheduler death~~ -> Fixed S90
- ~~P0: RESOLUTION dedup~~ -> Fixed S87
- ~~P1: Resolution backfill~~ -> Fixed S85
- ~~S95 Plan: 4 execution realism elevations~~ -> DONE, deployed `20260316_104320`

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

# Paper trading tests (S95):
pytest tests/unit/test_paper_fill_probability.py -v

# WeatherBot-specific:
pytest tests/unit/test_weather_bot.py -v
```

### Test counts: 1616 passed, 0 failed (ignoring pre-existing ui.dashboard failures)

---

## RECENT COMMITS (relevant)
```
2a87cdc S96: raise MirrorBot position caps 200->400 to clear bottleneck
bd54cdc feat(paper): S95 four execution realism elevations — alpha decay, Kyle's lambda, cross-scan impact, resolution proximity
dcce8c5 feat(paper): S95 five paper trading realism elevations
c9ee43a fix(paper): S95 restore realistic fills for RTDS trades — S94 bypass inflated P&L
cec1712 feat(weather): S92 model-run jump detection + NBM CDF benchmark
0443d33 perf(risk): S94 CVaR MC optimization
1a120a7 S94: RTDS fast-path
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
| **95** | **2026-03-16** | **4 paper trading execution realism elevations (alpha decay, Kyle's lambda, cross-scan, resolution proximity)** |

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

The S95 plan is fully complete. Remaining WeatherBot-scoped work:

1. **Monitor S95 elevation impact** (24-48h) — Check if Kyle's lambda diverges from DEFAULT_LAMBDA as `fill_analysis` accumulates data. Check if resolution proximity ever fires (may need to lower upstream liquidity gate).

2. **P3: Geographic expansion** — Add Great Plains corridor stations (Oklahoma City, Wichita, Omaha) to `station_registry.py`. High thermal volatility = more edge.

3. **P4: Lake-effect / wind markets** — Expand snowfall and wind gust market discovery. Station coverage for lake-effect zones (Buffalo, Cleveland, Erie).

4. **P5: Kalshi cross-platform arbitrage** — New module, new API integration. 8-16h effort.

5. **P&L analysis** — Deep dive on which market types (city, bucket, lead_time) are profitable vs losing. Paris historically -$384.

**Or**: Follow user instructions. Scope lock applies.

---

## ROLLBACK

```bash
# Revert S95 elevations:
git revert bd54cdc
bash deploy/deploy.sh

# Disable individual elevations via env (no deploy needed):
ssh ubuntu@34.251.224.21 "sudo bash -c 'echo PAPER_KYLE_LAMBDA_ENABLED=false >> /opt/pa2-shared/.env' && sudo systemctl restart polymarket-ai"
ssh ubuntu@34.251.224.21 "sudo bash -c 'echo PAPER_CROSS_SCAN_IMPACT_ENABLED=false >> /opt/pa2-shared/.env' && sudo systemctl restart polymarket-ai"
ssh ubuntu@34.251.224.21 "sudo bash -c 'echo PAPER_RESOLUTION_PROXIMITY_ENABLED=false >> /opt/pa2-shared/.env' && sudo systemctl restart polymarket-ai"
ssh ubuntu@34.251.224.21 "sudo bash -c 'echo PAPER_ALPHA_DECAY_HALF_LIFE_S=999999 >> /opt/pa2-shared/.env' && sudo systemctl restart polymarket-ai"
```
