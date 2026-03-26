# FULL SYSTEM HANDOFF — Session 93 (2026-03-15)
**Scope**: ENTIRE 14-bot Polymarket trading system
**Purpose**: Carbon-copy context transfer — new agent picks up seamlessly
**Date**: 2026-03-15
**VPS Deploy**: `20260315_175906` (latest)

---

## WHAT THIS IS

A **14-bot automated Polymarket prediction market trading system**. Currently in **paper trading mode** (`SIMULATION_MODE=true`). Real capital is NOT at risk — all trades execute through `PaperTradingEngine` which simulates fills with realistic slippage/fill-rate models. Paper trading IS production (see CLAUDE.md Prime Directive).

**Going live = flipping one boolean** (`SIMULATION_MODE=false`). Everything else must already work.

---

## HARD RULES (READ BEFORE DOING ANYTHING)

### 1. Scope Lock (NON-NEGOTIABLE)
- **ONLY make changes explicitly requested by the user or listed in a handoff doc**
- "I noticed X could be improved" → Mention it. Do NOT implement it.
- "While fixing X, Y is related" → Fix X only.
- "This would be a quick win" → Not your call. Ask first.
- **Origin**: Session 90 — agent added unsolicited `WEATHER_CITY_BLACKLIST`. User was furious. Zero tolerance since.

### 2. Bot-Scoped Sessions
- Each session is scoped to a single bot unless the user says otherwise
- Shared module changes OK only if they fix a bug directly affecting the scoped bot
- Cross-bot changes require explicit user approval

### 3. CLAUDE.md Prime Directive
Read `CLAUDE.md` in the repo root. Key rules:
- Working code is sacred. Fix only what is broken.
- One fix per commit. Preserve every function signature and external interface.
- No silent behavior changes. No "while I'm in here" refactors.
- Complete the pre-edit checklist (state bug, list files, grep dependents, read entire file).
- Paper trading IS production — never skip features because "we're only paper trading."

### 4. P&L Math (MANDATORY — WILL BREAK IF WRONG)
```python
# SAME formula for YES and NO — prices are token-specific, NEVER invert
cost = entry_price * size          # ALL sides
uPnL = (current_price - entry_price) * size  # ALL sides
# Canonical script: python scripts/bot_pnl.py BotName hours
# Source of truth: trade_events table (NOT paper_trades)
```

---

## CURRENT P&L (as of 2026-03-15 21:00 UTC)

### All-Time Realized (from trade_events)
| Bot | Entries | Exits (P&L) | Resolutions (P&L) | Total Realized |
|-----|---------|-------------|-------------------|----------------|
| **MirrorBot** | 1,370 | 491 (+$5,756) | 598 (+$14,110) | **+$19,866** |
| **WeatherBot** | 1,509 | 183 (+$570) | 252 (+$1,769) | **+$2,339** |
| **EsportsBot** | 93 | 28 (+$197) | 69 (-$207) | **-$10** |
| **EnsembleBot** | 101 | 0 | 4 (+$1) | **+$1** (ARCHIVED) |

### Open Positions
| Bot | Count | Unrealized P&L | Exposure |
|-----|-------|----------------|----------|
| MirrorBot | 156 | +$5,342 | $6,644 |
| WeatherBot | 316 | +$77 | $7,615 |
| EsportsBot | 9 | +$1 | $196 |

### 24h P&L (last 24 hours)
| Bot | Exits | Resolutions | 24h Net |
|-----|-------|-------------|---------|
| MirrorBot | +$45 (332 exits) | +$1,397 (169 resol) | **+$1,442** |
| WeatherBot | +$554 (101 exits) | +$465 (38 resol) | **+$1,020** |
| EsportsBot | +$137 (12 exits) | -$125 (4 resol) | **+$12** |
| **System 24h total** | | | **+$2,474** |

### P&L Caveats
- P&L is from paper trading with **estimated** slippage/fill models (NOT real orderbook)
- Slippage: fixed tiers (10/25/50/75 bps) with random jitter — does NOT read actual CLOB book depth
- Fill probability: heuristic based on price/size/spread — does NOT query actual volume
- MirrorBot P&L is fantasy at 100% fills (Session 92 added realistic fill model, Kelly 0.25)
- Actual live P&L will differ based on real liquidity

---

## BOT STATUS

### Active Bots (5 of 14)
| Bot | Strategy | Status | Key Config |
|-----|----------|--------|------------|
| **MirrorBot** | Copy-trade top Polymarket traders via RTDS feed | Active, RTDS live | kelly=0.25, min_conf=0.55, min_reliability=0.52, max_pos=200, watchlist=1000 |
| **WeatherBot** | 133-member NWP ensemble (GEFS+ECMWF+AIFS) for temp/precip markets | Active | kelly=0.25, max_pos=500, min_edge_us=0.08, min_edge_intl=0.12 |
| **EsportsBot** | Glicko-2 + PandaScore for pre-match esports | Active | kelly=0.25, min_conf=0.52, min_edge=0.08 |
| **EsportsLiveBot** | In-play esports (round-level) | Active | kelly=0.25 |
| **EsportsSeriesBot** | Series-level esports (merged into EsportsBot S91) | Active | kelly=0.25 |

### Disabled Bots (9)
BOT_ENABLED_* flags = false. MomentumBot DELETED. EnsembleBot ARCHIVED (-$5.6k).

### Global Config
```
SIMULATION_MODE=true (paper trading)
ALL BOTS: capital=$20000, max_bet=$300, max_daily=$10000
Kelly fraction: 0.25 (all active bots)
```

---

## ARCHITECTURE

### Core System Flow
```
1. INGESTION    → Gamma API / CLOB → markets table (62K+ markets)
2. DISCOVERY    → Each bot finds relevant markets (tags, categories, criteria)
3. PREDICTION   → Bot-specific model produces probability estimate
4. EDGE CALC    → model_prob - market_price → edge
5. SIZING       → BotBankrollManager (fractional Kelly) → bet size
6. RISK CHECKS  → risk_manager limits + bot-specific exposure caps
7. EXECUTION    → place_order(side="YES"/"NO") → PaperTradingEngine → paper_trades + trade_events
8. MONITORING   → position_manager updates current_price every 10s
9. EXIT LOGIC   → Edge decay, stop-loss, expiry → EXIT trade_events
10. RESOLUTION  → resolution_backfill → RESOLUTION trade_events with realized P&L
```

### File Structure (key files only)
```
bots/
  weather_bot.py          (~3,720 lines) — WeatherBot main
  esports_bot.py          (~2,000 lines) — EsportsBot main
  mirror_bot.py           (~2,500 lines) — MirrorBot main (RTDS copy-trading)

base_engine/
  base_bot.py             — Abstract base for all 14 bots
  bankroll_manager.py     — BotBankrollManager (sizing — THE sizer)
  risk_manager.py         — Risk limits (calculate_position_size is DEPRECATED)
  position_manager.py     — Position tracking, 10s price updates
  order_gateway.py        — Order routing, paper/live switch
  paper_trading.py        — PaperTradingEngine (fill simulation)
  prediction/
    prediction_engine.py  — Shared prediction infrastructure

base_engine/weather/
  forecast_client.py      (~1,380 lines) — Multi-model ensemble fetching + jump detection
  probability_engine.py   (~500 lines)  — CDF integration, EMOS, Kelly, NBM benchmark
  station_registry.py     (1,447 lines) — 50+ city registry
  market_mapper.py        (1,105 lines) — Market text → bucket parsing
  metar_client.py         (236 lines)   — METAR API
  precipitation_engine.py (231 lines)   — Gamma distribution

base_engine/data/
  database.py             — All DB operations (asyncpg)
  data_ingestion.py       — Market discovery + category classification
  ingestion_scheduler.py  — Scheduling ingestion runs
  database_lock.py        — Advisory lock management

esports/
  models/                 — Glicko-2, CatBoost, draft models
  pandascore_client.py    — PandaScore API
  esports_trainer.py      — Model training pipeline

config/
  settings.py             — All env-var-backed config

scripts/
  bot_pnl.py              — Canonical P&L script
  audit_pnl.py            — P&L audit tool

deploy/
  deploy.sh               — Atomic symlink deploy to VPS
  rollback.sh             — Rollback to previous release
```

### Database Tables
| Table | Role | Notes |
|-------|------|-------|
| `trade_events` | **P&L AUTHORITY** | ENTRY/EXIT/RESOLUTION, immutable trigger, partitioned by event_time |
| `positions` | Position tracking | 10s price updates, unrealized_pnl mark-to-market |
| `paper_trades` | Legacy compat | Still written (28 callers), NEVER read for P&L |
| `markets` | Market catalog | 62K+ markets, category classification |
| `daily_counters` | Exposure tracking | Write-through for game/daily exposure |
| `traded_markets` | Resolution backfill | Market status + resolution tracking |
| `equity_snapshots` | Daily equity | Per-bot capital, trade_events source |
| `reconciliation_breaks` | Audit trail | Compares positions vs trade_events |

---

## WEATHERBOT DEEP DIVE

### What It Does
Trades temperature, precipitation, snowfall, and wind-gust bucket markets using a 133-member NWP ensemble with EMOS calibration.

### Data Flow
```
DISCOVERY     → Gamma API tag_slug=temperature → WeatherMarketMapper groups by (city, date)
FORECASTING   → Open-Meteo GFS/GEFS/ECMWF/HRRR ensembles (133 members)
CALIBRATION   → EMOS (a + b*X_bar, sigma) per station/lead-time/regime + isotonic tail
PROBABILITY   → Skew-normal CDF integration per bucket
NBM BENCHMARK → N(nbm_high, sigma) CDF per bucket (US stations only)
EDGES         → model_prob - market_price, sorted by |edge|
REGIME        → ENSO (Nino 3.4), cross-city warm/cold, severe weather, AFD uncertainty
JUMP DETECT   → forecast_delta vs prior model run → sizing boost when |delta| >= 3F
SIZING        → Fractional Kelly (0.25) * regime * near-expiry * jump * NBM * drawdown * ST * Baker-McHale
RISK CHECKS   → group exposure cap, city exposure cap, daily loss limit, position count
EXECUTION     → place_order(side="YES"/"NO") → PaperTradingEngine
RESOLUTION    → METAR T-group override (<6h) + WU scraping + resolution_backfill
```

### Key Algorithms
- **EMOS**: mu_emos = a + b*X_bar, sigma_emos per station/lead-time
- **Isotonic tail calibration**: Data-driven tail discount from 50+ resolved events
- **Baker-McHale factor**: k* = 1/(1 + sigma^2) — ensemble spread to sizing reduction
- **Smoczynski-Tomkins**: Optimal allocation across mutually exclusive temperature buckets
- **Model-run jump detection** (Session 92): Prior ensemble mean vs current, boost when |delta| >= 3F
- **NBM CDF benchmark** (Session 92): N(nbm_high, sigma) disagrees with market by >= 15pp = high conviction

### WeatherBot Config
```
Capital=$20000, Kelly=0.25, Max bet=$300, Max daily=$10000
Max positions=500, Min edge US=0.08, Min edge intl=0.12
Forecast cache=900s, Rate limit=120 req/min (Open-Meteo)
Jump threshold=3.0F, Jump max boost=1.5x
NBM disagree threshold=0.15 (15pp)
```

---

## MIRRORBOT DEEP DIVE

### What It Does
Copy-trades top Polymarket traders identified by the RTDS (Real-Time Data Stream) global trade feed. Watches 1,000 traders, evaluates confidence via Bayesian reliability scoring.

### Key Components
- **RTDS WebSocket**: Global Polymarket trade feed, filters by watchlist
- **Elite Detector**: Identifies skilled traders (min 100 trades OR $10K volume)
- **Conformal Prediction**: Calibrated confidence bands (p_low, p_high)
- **Category Caps**: Per-category exposure limits to prevent concentration
- **Realistic Fill Model** (Session 92): Fill probability based on price/size/spread

### MirrorBot Config
```
Kelly=0.25, Min confidence=0.55, Min reliability=0.52
Max positions=200, Max per market=400
Watchlist size=1000, RTDS live
Category caps enforced (currently hitting "unknown" $2,400 cap)
```

### MirrorBot Known Issues
- **Category classifier**: 5,451 markets still "unknown" (down from 32,885 after Session 93 fix)
- Category cap on "unknown" blocking some trades — but this is protective, not a bug

---

## ESPORTSBOT DEEP DIVE

### What It Does
Trades pre-match esports markets (LoL, CS2, Dota 2, Valorant) using Glicko-2 ratings + PandaScore live data.

### Key Components
- **Glicko-2**: Player/team rating system with uncertainty
- **PandaScore API**: Live match data, team rosters, recent results
- **PatchDriftDetector**: 48h observation mode when game patches detected
- **CatBoost draft model**: Draft phase predictions (LoL)
- **Freshness decay**: 30s decay on prediction staleness

### EsportsBot Config
```
Kelly=0.25, Min confidence=0.52, Min edge=0.08
Freshness decay=30s, Live poll timeout=10s
```

---

## CRITICAL TRAPS (WILL BREAK THINGS IF IGNORED)

### Database
- **trade_events is P&L AUTHORITY** — NEVER read paper_trades for P&L
- **trade_events JSONB column is `event_data`** — NOT `metadata_json`
- **paper_trades has NO `metadata` JSONB column, NO `resolved_pnl` column** (it's `resolved_at`)
- **positions table**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`
- **prediction_log**: NO `rejection_reason`. Use `trade_executed` (bool) + `model_name`
- **trade_events immutability trigger**: `trg_trade_events_immutable` prevents DELETE/UPDATE. Must DISABLE TRIGGER then re-enable
- **RESOLUTION event idempotency**: ON CONFLICT broken on partitioned tables. Uses atomic INSERT...SELECT with WHERE NOT EXISTS
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **asyncpg DATE**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
- **asyncpg timestamps**: paper_trades uses `timestamp without time zone` — pass `.replace(tzinfo=None)`. `created_at` has NO DEFAULT
- **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time

### Bot Logic
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL"
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass
- **risk_manager.calculate_position_size() is DEPRECATED** — BotBankrollManager is the real sizer
- **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
- **CLOB volume=0** — Never use volume gates for MirrorBot
- **MirrorBot entry price**: Uses CURRENT market price, NOT trader's fill price
- **MirrorBot `_market_meta_cache`**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand
- **MirrorBot `_open_positions` on restart**: Clears in-memory; re-enters by EOD UTC
- **PatchDriftDetector**: `_patch_timestamps` ONLY set on genuine patch changes (`old is not None`)

### Python/Infrastructure
- **Python 3.13 scoping**: `from X import Y` inside function = Y is local for ENTIRE function. Any use before import = `UnboundLocalError`
- **websockets v15**: `websockets.exceptions` must be imported explicitly (lazy-loads)
- **BOT_REGISTRY=14 bots** — shared module change requires all 14 verified
- **RTDS envelope**: Must unwrap `data.get("payload", data)`
- **RTDS dedup**: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`
- **daily_counters write patterns**: ADDITIVE (EsportsBot game keys) vs ABSOLUTE-SET (OrderGateway exposure). Do NOT mix.
- **Do NOT use `asyncio.create_task()` for financial write-throughs** — always `await`

---

## STATE PERSISTENCE (ALL GAPS CLOSED)

| State | Mechanism | Status |
|-------|-----------|--------|
| `_daily_exposure_usd` (all bots) | `daily_counters` 60s flush + SIGTERM + startup restore | Done |
| `_game_exposure` (EsportsBot) | `daily_counters` write-through | Done |
| `_group/_city_exposure` (WeatherBot) | `_restore_exposure_from_db()` | Done |
| `_daily_exposure` (MirrorBot) | `_restore_state_on_startup()` paper_trades SUM | Done |
| Exit cooldowns (WeatherBot) | Redis TTL `_save/_restore_exits_from_redis()` | Done |
| Open positions (all bots) | `order_gateway.seed_positions_from_db()` | Done |
| Prior forecasts (jump detect) | In-memory only (loss = 1 scan re-seed) | Done |
| Forecast 429 cooldowns | Redis persistence in forecast_client | Done |
| Daily P&L (all bots) | `_restore_daily_pnl_from_db()` from trade_events | Done |

---

## INFRASTRUCTURE

### VPS
- **Host**: Ubuntu-3 at 34.251.224.21 (16GB/4vCPU, eu-west-1)
- **SSH**: `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21`
- **Service**: `polymarket-ai` (systemd)
- **Working dir**: `/opt/polymarket-ai-v2` → symlink to `/opt/pa2-releases/<timestamp>`
- **Shared**: `/opt/pa2-shared/{data,saved_models,venv}`
- **Env file**: `/opt/pa2-shared/.env`
- **Python**: 3.13
- **DB**: PostgreSQL, localhost, user=polymarket, db=polymarket

### Deploy
```bash
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
# Rollback:
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```
Atomic symlink swap. Health check 90s. VPS working tree != local git HEAD.

### Post-Deploy Verification
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
sudo journalctl -u polymarket-ai -f
# Per-bot:
journalctl -u polymarket-ai -f | grep -iE "WeatherBot|weather"
journalctl -u polymarket-ai -f | grep -iE "MirrorBot|mirror|RTDS"
journalctl -u polymarket-ai -f | grep -iE "EsportsBot|esports"
```

### Tests
```bash
pytest                                              # All 1622+ tests
pytest tests/unit/test_weather_bot.py -v            # WeatherBot (123 tests)
pytest tests/unit/test_mirror_bot_logic.py -v       # MirrorBot
pytest tests/ -k esports -v                         # EsportsBot
```

---

## UNCOMMITTED CHANGES (as of Session 93)

### Modified files:
```
M  AGENT_HANDOFF_MIRRORBOT_SESSION91_2026_03_14.md
M  base_engine/data/data_ingestion.py          ← Session 93: category classifier fix (27,453 reclassified)
M  base_engine/data/database.py
M  base_engine/weather/forecast_client.py      ← Session 92: jump detection
M  base_engine/weather/probability_engine.py   ← Session 92: NBM benchmark
M  bots/esports_bot.py
M  bots/weather_bot.py                         ← Session 92: jump + NBM boost integration
M  esports/models/esports_trainer.py
M  tests/unit/test_mirror_bot_logic.py
D  run_ui.py                                   ← Old UI deleted
D  ui/async_worker.py                          ← Old UI deleted
D  ui/dashboard.py                             ← Old UI deleted
```

### New files:
```
?? AGENT_HANDOFF_ESPORTS_SESSION92_2026_03_15.md
?? AGENT_HANDOFF_MIRRORBOT_SESSION92_2026_03_15.md
?? AGENT_HANDOFF_WEATHERBOT_SESSION92_2026_03_15.md
?? docs/UI_DESIGN_BRIEF.md                     ← New UI design doc
?? esports/models/catboost_draft_model.py
?? esports/models/draft_features.py
?? scripts/audit_mirror_pnl.py
?? scripts/check_losers.py
?? scripts/snapshot.py
?? scripts/win_rates.py
?? ui/app.py                                   ← New UI
?? ui/start.sh
?? ui/static/
```

**NOTE**: These changes span multiple bot sessions (WeatherBot S92, MirrorBot S92, EsportsBot S92). The deploy on VPS (`20260315_175906`) includes the category fix from this session.

---

## SESSION HISTORY (KEY SESSIONS)

| Session | Date | Scope | Key Work |
|---------|------|-------|----------|
| 77 | 2026-03-11 | MirrorBot | P1-P8 features, 2 critical bugs (stale pricing, SELL overwrite) |
| 79 | 2026-03-12 | MirrorBot | Selectivity tightening (conf 0.55, reliability 0.52) |
| 81 | 2026-03-12 | MirrorBot | RTDS live, 6 fixes, paper_trades DB persistence |
| 85 | 2026-03-14 | Cross-bot | Resolution backfill 3 root causes, P&L data overhaul, 544 resolved |
| 86 | 2026-03-14 | Cross-bot | Ingestion sync fix, RESOLUTION dedup (3238 dupes) |
| 87 | 2026-03-14 | EsportsBot | RESOLUTION dedup fix (atomic INSERT...SELECT) |
| 88 | 2026-03-14 | EsportsBot | Observation mode false-positive fix |
| 89 | 2026-03-14 | EsportsBot | E2-E5 features + 9 audit fixes |
| 90 | 2026-03-14 | WeatherBot | Advisory lock zombie fix, master timeout, 3 cities |
| 92 | 2026-03-15 | All bots | WeatherBot P1/P2, MirrorBot realistic fills, Esports draft model |
| 93 | 2026-03-15 | System | Category classifier fix (32,885 → 5,451 unknown), P&L review |

---

## HANDOFF DOCS INDEX

| Doc | Scope | Key Content |
|-----|-------|-------------|
| `AGENT_HANDOFF_SESSION85_DATA_OVERHAUL_2026_03_14.md` | Cross-bot | P&L authority, 10 bugs, migration 052 |
| `AGENT_HANDOFF_MIRRORBOT_SESSION92_2026_03_15.md` | MirrorBot | Realistic fills, Kelly 0.25, RTDS cache |
| `AGENT_HANDOFF_WEATHERBOT_SESSION92_2026_03_15.md` | WeatherBot | Jump detection, NBM benchmark |
| `AGENT_HANDOFF_ESPORTS_SESSION89_2026_03_14.md` | EsportsBot | E2-E5 features + 9 audit fixes |
| `AGENT_HANDOFF_ESPORTS_SESSION92_2026_03_15.md` | EsportsBot | Draft model, article-driven improvements |

---

## OUTSTANDING ITEMS (SYSTEM-WIDE)

### Active Issues
| Priority | Item | Bot | Notes |
|----------|------|-----|-------|
| **P2** | 604 markets still unresolved in traded_markets | All | Genuinely open, resolving naturally |
| **P3** | 5,451 markets still category "unknown" | MirrorBot | Down from 32,885. Remaining are genuinely hard to classify |
| **P3** | `no_prediction: 12` per scan — team name parsing failures | EsportsBot | CS2/Valorant team names |
| **P5** | Remove diagnostic logging (session_factory warning) | Shared | In prediction_engine.py line 637 |

### Future Roadmap
| Priority | Item | Bot | Effort |
|----------|------|-----|--------|
| P3 | Geographic expansion (Great Plains corridor) | WeatherBot | Station additions |
| P4 | Lake-effect snow / wind gust markets | WeatherBot | New market types |
| P5 | Kalshi cross-platform arbitrage | New module | 8-16h |
| — | Monitor P1/P2 impact (jump + NBM) | WeatherBot | Check logs after 24-48h |
| — | New UI completion | System | `ui/app.py` + `docs/UI_DESIGN_BRIEF.md` |

### Resolved Issues (do NOT re-open)
- P0: RESOLUTION dedup broken → Fixed S87 (atomic INSERT...SELECT)
- P0: Scheduler dead 11h → Fixed S90 (advisory lock shield + master timeout)
- P0: False observation mode on restart → Fixed S88 (PatchDriftDetector)
- P1: Resolution backfill → Fixed S85 (3 root causes, 544 markets)
- P1: MirrorBot P&L audit → Fixed S86 (3238 dupes deleted)
- P1: System exposure cap blocking EsportsBot → Resolved naturally
- P3: PandaScore timeout → Fixed S89 (adaptive polling)
- P3: RTDS cold-start latency → Fixed S92 (startup cache)

---

## MEMORY FILES

All persistent memory lives in:
`C:\Users\samwa\.claude\projects\C--lockes-picks-polymarket-ai-v2\memory\`

| File | Purpose |
|------|---------|
| `MEMORY.md` | Master index (200-line limit, first 200 loaded) |
| `feedback_scope_lock.md` | Scope lock rule + origin story |
| `feedback_pnl_math.md` | P&L formula rules (never invert NO) |
| `feedback_bot_sessions.md` | Bot-scoped session rules |
| `session_history.md` | Sessions 72-92 detail |

---

## PAPER TRADING FILL MODEL

The system uses **estimated** slippage and fill rates, NOT real orderbook data:

| Component | Source | Accuracy |
|-----------|--------|----------|
| Entry/exit prices | Real CLOB mid/bid/ask at scan time | Real |
| Slippage | Tiered model (10/25/50/75 bps) + jitter | Estimate |
| Fill probability | Price/size/spread heuristic | Estimate |
| Partial fills | Random draw | Estimate |
| Taker fee (1.5%) | Matches Polymarket schedule | Real |

**Gap**: `LiquidityGuardian` in WeatherBot reads actual orderbook depth for gating, but fill price still uses tiered bps model. Actual live slippage could differ.

---

## QUICK REFERENCE COMMANDS

```bash
# P&L check
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "cd /opt/polymarket-ai-v2 && /opt/pa2-shared/venv/bin/python scripts/bot_pnl.py WeatherBot 24"

# Service status
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "systemctl is-active polymarket-ai && journalctl -u polymarket-ai --since '5 min ago' --no-pager | tail -20"

# DB query
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo -u postgres psql -d polymarket -c 'SELECT source_bot, COUNT(*) FROM positions WHERE status='\''open'\'' GROUP BY source_bot;'"

# Deploy
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh

# Tests
pytest  # 1622+ must pass
```

---

## WHAT THE NEXT SESSION SHOULD DO

Follow the user's instructions. If none given, read the relevant bot handoff doc and present outstanding items for the user to choose from. Never auto-implement. Always scope-lock to the bot the user specifies.
