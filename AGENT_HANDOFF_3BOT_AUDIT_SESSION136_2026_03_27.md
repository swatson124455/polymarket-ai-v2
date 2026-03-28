# AGENT HANDOFF — 3-Bot Exhaustive Audit Session 136 (2026-03-27)

## CARBON-COPY CONTINUATION PROMPT

> **Scope**: ALL THREE BOTS — MirrorBot, EsportsBot, WeatherBot. Cross-bot audit session.
> **What this session was**: User requested exhaustive line-by-line code audit of ALL data logging, reading, ingesting, reviewing, storing, and channels across all 3 bots. Every code line verified for bugs, inefficiencies, and errors.
> **What was accomplished**: Partial audit completed. Background agents ran but hit context limits on the larger files. All three latest handoff docs read in full. WeatherBot pathway review document (16 sections) read in full. Prior session (S135) findings integrated. NO CODE WAS CHANGED.
> **What needs to happen**: Complete the line-by-line audit and compile findings into actionable bug list. Then fix P0 bugs.
> **Git**: No changes. HEAD unchanged from S135 (`387775b`).

---

## 1. SYSTEM OVERVIEW (FOR NEW AGENT)

This is a **15-bot automated Polymarket trading system** running on a VPS (Ubuntu, 34.251.224.21, 16GB/4vCPU). Currently in **paper trading mode** (`SIMULATION_MODE=true`) — real architecture, $0 execution. **Paper trading IS production** per CLAUDE.md (the ONLY difference is whether the final order goes to CLOB or paper trade table).

### The 3 Bots Being Audited

| Bot | File | ~Lines | Role | All-Time P&L |
|-----|------|--------|------|---------------|
| **MirrorBot** | `bots/mirror_bot.py` | ~1800 | Copies whale trades from RTDS (Real-Time Data Stream). Bayesian confidence + reliability scoring. | -$159,442 (39.3% WR) |
| **EsportsBot** | `bots/esports_bot.py` | ~6000 | Trades esports match-winner markets using Glicko-2 ratings + per-game ML models (XGBoost/CatBoost). | -$4,368 |
| **WeatherBot** | `bots/weather_bot.py` | ~4700 | Trades temperature prediction markets using 133-member NWP ensemble forecasts + CDF integration. | ~-$15K |

### Key Architecture Facts
- **15 bots** in BOT_REGISTRY (MomentumBot DELETED)
- **BotBankrollManager** handles SIZING; **risk_manager** handles LIMITS. Both must pass.
- `risk_manager.calculate_position_size()` is DEPRECATED — BotBankrollManager used instead
- **trade_events** is P&L AUTHORITY — never read paper_trades for P&L
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL"
- Python 3.13, asyncio, asyncpg (PostgreSQL), Redis, WebSockets
- VPS: Ubuntu-3, 34.251.224.21, SSH key: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- Deploy via `deploy/deploy.sh` — atomic symlink swap

---

## 2. LATEST HANDOFF DOCS (READ THESE)

| Bot | Latest Handoff | Key Content |
|-----|---------------|-------------|
| **MirrorBot** | `AGENT_HANDOFF_MIRRORBOT_SESSION134_2026_03_27.md` | S134: Per-trader P&L blacklist, spread gate, ML fail-open. 20-gate chain. All MIRROR_ config. |
| **EsportsBot** | `AGENT_HANDOFF_ESPORTS_SESSION134_2026_03_26.md` | S134: Git sync, exit hemorrhage fixes (dead-market guard + stop-loss floor), calibration review, WS review. 10 config tables. |
| **WeatherBot** | `AGENT_HANDOFF_WEATHERBOT_SESSION135_2026_03_27.md` | S135: Platt+Isotonic → LogisticRegression calibrator (4 features). NOT DEPLOYED. Full audit bugs list. |
| **WeatherBot Pathway** | `WEATHERBOT_TRADING_PATHWAY_REVIEW.md` | Complete 16-section trading flow: discovery → parsing → forecast → probability → calibration → edge → sizing → execution → exits → resolution. |

**IMPORTANT**: Read these 4 files BEFORE making any changes. They contain the complete state of all 3 bots.

---

## 3. KNOWN BUGS FROM S135 AUDIT (NOT YET FIXED)

### P0 — Trade-Breaking Bugs

| ID | Bot | File:Line | Finding | Impact |
|----|-----|-----------|---------|--------|
| **M1** | MirrorBot | `mirror_bot.py:956` | Python 3.13 scoping crash — `_t` imported conditionally inside function, used unconditionally. Every manual exit crashes with `UnboundLocalError`. | All exits fail silently |
| **M2** | MirrorBot | `elite_watchlist.py:465` | `_track_open_position()` was deleted (S134) but is still called from RTDS copy path. RTDS trades lose position tracking entirely. | Positions not monitored |
| **E1** | EsportsBot | `esports_bot.py:5918,5941` | `_series_on_price_update` doesn't convert NO token prices. When NO token fires WS update, `current_price` is `1 - actual_price`, producing fake 30-45% edges. | Ghost trades on bad prices |
| **E2** | EsportsBot | `esports_bot.py:5914-5986` | `_series_on_price_update` bypasses ALL safety gates: no opposing-side guard, no exposure check, no entry cap, no disabled-games check. | Unprotected entries |

### P1 — Financial Impact

| ID | Bot | File:Line | Finding |
|----|-----|-----------|---------|
| **W1** | WeatherBot | `weather_bot.py:1135` | `date.today()` uses LOCAL time, not UTC. Daily digest timing wrong on UTC+1 VPS. |
| **W2** | WeatherBot | `weather_bot.py:1792` | `date.today()` uses LOCAL time in `_analyze_group()`. Skips today's markets after 23:00 UTC on UTC+1 VPS. |
| **W5** | WeatherBot | `weather_bot.py:2737` | Division by zero: `size / opp["price"]` — PSW markets can bypass price filter with price=0.0. |

### P2 — Correctness

| ID | Bot | File:Line | Finding |
|----|-----|-----------|---------|
| **W3** | WeatherBot | `weather_bot.py:1720` | `no_edge` is algebraically identical to `-yes_edge`. Dead logic, no functional impact. |
| **W4** | WeatherBot | `weather_bot.py:3918` | Return type annotation mismatch on `_bounded_fetch`. |
| **W6** | WeatherBot | `weather_bot.py:1855` | Fragile nested ternary for `_mid` — works by accident. |
| **W12** | WeatherBot | `weather_bot.py:428` | Silent `except Exception: pass` on prediction log insert. |
| **W13** | WeatherBot | `weather_bot.py:2477` | Printf-style `%` formatting in structlog — garbles output. |

### P3 — Inefficiency

| ID | Bot | File:Line | Finding |
|----|-----|-----------|---------|
| **W7-W9** | WeatherBot | `weather_bot.py:3786,4004,4062` | Three methods create new `aiohttp.ClientSession` per call instead of reusing forecast client session. |
| **W10** | WeatherBot | `weather_bot.py:1362` | `import httpx` inside function runs every scan cycle. |
| **W11** | WeatherBot | `weather_bot.py:1067` | Unnecessary `deepcopy` on read-only `weather_markets`. |

---

## 4. WHAT THIS SESSION INTENDED (AUDIT SCOPE)

The user requested:
> "review mirror, esports, weather bot — verify all data logging, reading, ingesting, reviewing, storing, channels etc are pristine... Read every single code line to each and verify inefficiencies and bugs/errors. exhaustive search."

### Audit Dimensions
1. **Data Logging** — Are all trades, predictions, exits, resolutions logged correctly? Missing fields? Silent failures?
2. **Data Reading** — Are DB queries correct? Wrong column names? Missing WHERE clauses? Stale cache reads?
3. **Data Ingesting** — Are API responses parsed correctly? Missing null checks? Wrong field names?
4. **Data Storing** — Are writes to trade_events, paper_trades, positions, prediction_log correct? Idempotent? Partition-safe?
5. **Channels** — Are WebSocket subscriptions correct? RTDS feed parsing? Price update propagation?
6. **State Consistency** — Do in-memory caches match DB? Are restarts safe? Race conditions between scan loop and WS callbacks?

### Files That Need Line-by-Line Audit

**MirrorBot ecosystem:**
- `bots/mirror_bot.py` (~1800 lines) — main bot
- `bots/mirror_calibration.py` — FTS confidence calibration (shadow-only)
- `bots/mirror_ml_selector.py` — ML trade selector (shadow-only)
- `base_engine/learning/elite_reliability.py` — Bayesian win rate tracker
- `base_engine/learning/elite_watchlist.py` — RTDS watchlist management
- `base_engine/learning/elite_detector.py` — Elite trader detection

**EsportsBot ecosystem:**
- `bots/esports_bot.py` (~6000 lines) — main bot
- `bots/esports_live_bot.py` — live in-match trading
- `bots/esports_series_bot.py` — series/BO3/BO5 trading
- `esports/` directory — Glicko-2, ML models, calibrators

**WeatherBot ecosystem:**
- `bots/weather_bot.py` (~4700 lines) — main bot
- `base_engine/weather/probability_engine.py` (~500 lines) — forecast → probability
- `base_engine/weather/forecast_client.py` (~1300 lines) — Open-Meteo API
- `base_engine/weather/market_mapper.py` (~1050 lines) — question parsing
- `base_engine/weather/station_registry.py` (~1500 lines) — 106 cities

**Shared modules (affect all 3):**
- `bots/base_bot.py` — base class
- `bots/bankroll_manager.py` / `base_engine/risk/bankroll_manager.py` — Kelly sizing
- `bots/paper_trading.py` / `base_engine/execution/paper_trading.py` — VWAP fill sim
- `bots/risk_manager.py` — risk limits
- `bots/position_manager.py` — position tracking
- `database.py` — DB connection pool, queries
- `base_engine/data/resolution_backfill.py` — resolution pipeline

### What Was Completed
- All 3 latest handoff docs read in full
- WeatherBot pathway review (16 sections) read in full
- S135 audit bugs catalog imported (13 WeatherBot + 2 MirrorBot + 2 EsportsBot)
- Background audit agents launched for all 3 bots + shared modules — hit context limits before completing full reads

### What Was NOT Completed
- Full line-by-line read of `mirror_bot.py` (1800 lines)
- Full line-by-line read of `esports_bot.py` (6000 lines)
- Full line-by-line read of `weather_bot.py` (4700 lines)
- Supporting module reads (elite_reliability, forecast_client, probability_engine, etc.)
- Shared module reads (base_bot, paper_trading, database, resolution_backfill)
- Final consolidated findings document

---

## 5. MIRRORBOT CURRENT STATE (S134)

### Architecture
- **RTDS (Real-Time Data Stream)** feeds whale trades via WebSocket
- 67 tracked traders (only 16 profitable, 76% unprofitable)
- Bayesian confidence formula: conviction + reliability weighting
- 20-gate entry chain (see S134 handoff §3)
- Exit via: stop-loss (15%), trader-exit mirroring, max-hold, alpha decay

### Key S132-S134 Changes (LIVE)
- `rel_mult` CAPPED at 1.0 (was 2.0, data showed 1.05+ = anti-signal)
- `_price_adj` ZEROED (contrarian boost killed — 32.9% WR, -$84K)
- $50 min whale trade gate (`MIRROR_MIN_WHALE_TRADE_USD=50`)
- NO-side 0.5x dampener (`MIRROR_NO_SIDE_DAMPENER=0.5`)
- Crypto category BLOCKED (`MIRROR_CATEGORY_BLOCKLIST=crypto,15-minute,speed`)
- Per-trader P&L blacklist (S134): WR<0.35 + 20+ resolved → reject
- Spread gate (S134): 20c+ spread → reject
- ML selector fail-open (S134): ML errors don't crash trade

### Config (see S134 handoff §4 for full list)
```
MIRROR_MIN_CONFIDENCE=0.60 (VPS)
MIRROR_MAX_SPREAD=0.20
MIRROR_TRADER_MIN_WIN_RATE=0.35
MIRROR_TRADER_MIN_RESOLVED=20
MIRROR_USE_ML_SELECTOR=false (shadow-only)
MIRROR_MAX_POSITIONS=1000
MIRROR_STOP_LOSS_PCT=0.15
```

### P&L
- All-time resolved: -$159,442, 39.3% WR
- Non-crypto 0.70+ confidence: 56.9% WR, +$2,243 (only profitable bucket)
- Canonical: `python scripts/bot_pnl.py MirrorBot 720`

---

## 6. ESPORTSBOT CURRENT STATE (S134)

### Architecture
- 8 games: CS2, Dota2, Valorant, LoL, CoD, R6, SC2, RL
- Glicko-2 ratings → per-game ML models → BetaCalibrator + OnlinePlatt → RFLB
- Signal Quality (SQ) system: 5-component composite, scales BET SIZE not confidence
- Series tracking for BO3/BO5 correlated bets
- WebSocket reactive path + 2-second scan path

### Key S131-S134 Changes (LIVE)
- SQ sizing multiplier (not confidence multiplier)
- Opposing-side guard (`_entered_market_sides` set)
- Penny/extreme price guard (0.05-0.95)
- Dead-market guard widened (S134): `current < 0.10 AND entry >= 0.20` → skip exit
- Stop-loss floor (S134): `current < 0.10` → skip stop-loss
- Max-hold reduced to 48h (was 96h)
- `ESPORTS_DISABLED_GAMES` mechanism exists but is INERT (user rejected disabling)

### Calibrator Status
| Game | BetaCalibrator | OnlinePlatt | Brier |
|------|---------------|-------------|-------|
| CS2 | FITTED (n=64) | Approaching | 0.322 (WORSE than random) |
| Dota2 | FITTED (n=20) | Approaching | 0.249 (borderline) |
| LoL | 8/10 | No | — |
| Valorant | 8/10 | No | — |
| CoD | 1/10 | No | — |

### P&L
- All-time realized: -$4,368
- Valorant: +$4,132 (ONLY profitable game)
- CS2: -$2,896, LoL: -$1,858, Dota2: -$1,404, CoD: -$1,151
- EXIT losses 2.5x worse than RESOLUTION losses
- Canonical: `python scripts/bot_pnl.py EsportsBot 720`

---

## 7. WEATHERBOT CURRENT STATE (S135)

### Architecture
- 106 registered cities (35 active on Polymarket)
- 133-member NWP ensemble (GEFS+IFS+AIFS) from Open-Meteo
- Probability via skew-normal CDF integration
- EMOS calibration (24/106 stations fitted)
- LogisticRegression confidence calibrator (4 features: conf, side, lead_time, price) — S135, NOT YET DEPLOYED
- Kelly sizing with combined boost (expiry, regime, severe, jump, NBM)
- Alpha decay exits (30-min half-life)
- METAR override for <12h lead time (US only)

### Key S132-S135 Changes
- S132: 8 dampeners documented as removed (6 NOT actually removed until S134)
- S134: 6 dampeners ACTUALLY removed, sa_text fix, Phase 4b trade_events source, phantom cleanup (1,061 events/$37K removed), NO cap 0.75
- S135: Platt+Isotonic → LogisticRegression calibrator (NOT DEPLOYED)

### Config
```
WEATHER_CONFIDENCE_CAL_ENABLED=true
WEATHER_CONFIDENCE_CAL_WINDOW_DAYS=30
WEATHER_CONFIDENCE_CAL_MIN_SAMPLES=200
WEATHER_YES_MIN_CONFIDENCE=0.35
WEATHER_YES_BOOST_ENABLED=false
WEATHER_MIN_EDGE=0.08
WEATHER_INTL_MIN_EDGE=0.12
WEATHER_NO_MAX_ENTRY_PRICE=0.75
WEATHER_KELLY_FRACTION=0.25 (auto-graduates to 0.35)
```

### P&L
- 24h clean: +$3,017 (ex-Tokyo)
- All-time: ~-$15K (YES losses dominate)
- YES at 0.95+ conf: 18.8% WR (catastrophic). NO at 0.95+ conf: 87.6% WR (accurate)
- Canonical: `python scripts/bot_pnl.py WeatherBot 720`

---

## 8. WEATHERBOT COMPLETE TRADE FLOW (SUMMARY)

```
[1] MARKET DISCOVERY — Gamma API tag_slug=temperature → 100-500 raw markets
[2] PARSING & MATCHING — Regex → city/date/bucket. Station lookup → ICAO code.
[3] FORECAST ACQUISITION — 133 ensemble members (GEFS+IFS+AIFS) + NBM + local
[4] PROBABILITY — Skew-normal fit → EMOS calibration → climate prior blend → CDF integration → METAR override
[5] EDGE & FILTERING — edge = model_prob - market_price. 16 pre-trade gates.
[6] CONFIDENCE CALIBRATION — LogisticRegression(raw_conf, side, lead_time, price) → calibrated_confidence
[7] TRADE SIZING — Kelly: f* = (conf × odds - loss_prob) / odds × kelly_fraction × boosts
[8] RISK GATES — Daily loss, group/city exposure, fill probability, slippage, negative EV
[9] EXECUTION — place_order(side="YES"/"NO") → PaperTradingEngine → VWAP fill
[10] POSITION MANAGEMENT — Alpha decay (30-min half-life), resolution backfill (30-min + daily)
```

Full 16-section detailed review: `WEATHERBOT_TRADING_PATHWAY_REVIEW.md`

---

## 9. SHARED INFRASTRUCTURE

### Database Tables
| Table | Purpose | Notes |
|-------|---------|-------|
| `trade_events` | P&L AUTHORITY, immutable event log | Partitioned by event_time. Immutability trigger. |
| `paper_trades` | Paper trading state (mutable) | NO `metadata` JSONB column. Uses `price` NOT `entry_price`. |
| `positions` | Open position tracking | `current_price` auto-updated every 10s. |
| `traded_markets` | Market metadata + resolution | `bot_names` is TEXT, use LIKE. |
| `daily_counters` | Exposure write-through | EsportsBot game exposure persistence. |
| `esports_prediction_log` | Calibrator training | ON CONFLICT (match_id, bot_name) UPDATE. |
| `weather_calibration` | EMOS parameters | Per-station per-lead-bucket. |

### Resolution Pipeline
- Mini backfill: every 30 min (recently resolved)
- Full backfill: daily (all open positions)
- Phase 4b sources from `trade_events ENTRY` (NOT paper_trades — S134 fix)
- RESOLUTION event idempotency: INSERT...SELECT WHERE NOT EXISTS (ON CONFLICT broken on partitions)
- Resolution backfill excludes SELL trades (SELL P&L computed at exit time)

### State Persistence
| State | Mechanism | Bot |
|-------|-----------|-----|
| `_daily_exposure` | paper_trades SUM on startup | MirrorBot |
| `_game_exposure` | daily_counters write-through | EsportsBot |
| `_recently_exited` | Redis TTL keys | WeatherBot |
| `_open_positions` | positions table | MirrorBot |
| `_entered_market_sides` | trade_events ENTRY on startup | EsportsBot |

---

## 10. CRITICAL TRAPS (COMPLETE LIST — DO NOT BREAK)

1. **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
2. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL"
3. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
4. **Python 3.13 scoping**: `from X import Y` inside function → Y is local for ENTIRE function
5. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
6. **asyncpg DATE**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
7. **trade_events immutability trigger**: Must `DISABLE TRIGGER` per-partition for cleanup
8. **RESOLUTION idempotency**: ON CONFLICT broken on partitions → INSERT...SELECT WHERE NOT EXISTS
9. **BotBankrollManager handles SIZING; risk_manager handles LIMITS**
10. **MirrorBot S132**: rel_mult CAPPED 1.0, _price_adj ZEROED, $50 whale gate, NO 0.5x dampener
11. **S120 P&L was FALSE** — orphan RESOLUTION events
12. **S135 LogisticRegression calibrator**: NOT YET DEPLOYED. Rollback: `WEATHER_CONFIDENCE_CAL_ENABLED=false`
13. **paper_trades has NO metadata JSONB column**
14. **paper_trades.market_id != trade_events.market_id** — Gamma ID vs condition_id mismatch for older entries
15. **_market_meta_cache** in MirrorBot: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
16. **RTDS envelope**: Must unwrap `data.get("payload", data)`
17. **MirrorBot entry price**: Uses CURRENT market price, NOT trader's fill price
18. **S134 daily P&L restore uses EXIT-only** — RESOLUTION events corrupted
19. **S134 Phase 4b sources from trade_events ENTRY** — never revert to paper_trades
20. **S134: 6 dampeners removed from WeatherBot** — do NOT re-add
21. **deploy.sh blocked by 2 flaky tests** — use manual SCP deploy
22. **NEVER disable esports games without explicit user instruction**
23. **std_floor = 0.5 degrees** in probability_engine — minimum ensemble spread
24. **EMOS coverage**: 24/106 stations have local EMOS, others use global fallback

---

## 11. TASK FOR NEXT AGENT

### Primary: Complete Exhaustive 3-Bot Audit

Read EVERY line of the following files. Report ALL findings with exact line numbers.

**Priority order** (fix P0 bugs from S135 audit FIRST, then continue fresh audit):

#### Phase 1: Fix Known P0 Bugs
1. **M1**: `mirror_bot.py:956` — Python 3.13 scoping crash on exit
2. **M2**: `elite_watchlist.py:465` — deleted method still called
3. **E1**: `esports_bot.py:5918,5941` — NO token price not converted in series WS
4. **E2**: `esports_bot.py:5914-5986` — series WS bypasses all safety gates

#### Phase 2: Complete Line-by-Line Audit
For each file, read in 500-line chunks. Check for:
- Inverted conditions, off-by-one errors
- Variables used before assignment
- Dict keys that may not exist (KeyError risk)
- Async operations that should be awaited but aren't
- DB queries with wrong column names or missing WHERE clauses
- Log statements missing critical context
- Silent exception swallowing (`except Exception: pass`)
- Unbounded data structures (memory leaks)
- Redundant computations
- Python 3.13 scoping issues
- Race conditions between scan loop and WS/RTDS callbacks
- State inconsistency between in-memory and DB

#### Phase 3: Compile Final Report
Consolidated table of ALL findings across all 3 bots, sorted by severity.

---

## 12. KEY COMMANDS

```bash
# Run all tests
python -m pytest tests/ -x -q

# Run bot-specific tests
python -m pytest tests/unit/test_mirror_bot_logic.py -v
python -m pytest tests/unit/test_esports_bot.py -v
python -m pytest tests/unit/test_weather_bot.py -v

# Check P&L
python scripts/bot_pnl.py MirrorBot 720
python scripts/bot_pnl.py EsportsBot 720
python scripts/bot_pnl.py WeatherBot 720

# Check bot health on VPS
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "journalctl -u polymarket-ai --since '5 min ago' | grep -E '(MirrorBot|EsportsBot|WeatherBot)' | tail -30"

# Deploy
cd C:/lockes-picks/polymarket-ai-v2 && KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
```

---

## 13. MEMORY FILES TO READ

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Prime directive, rules of engagement, architecture facts |
| `memory/MEMORY.md` | Master index (loaded in system prompt) |
| `memory/feedback_scope_lock.md` | NEVER add unsolicited features |
| `memory/feedback_pnl_math.md` | P&L formula rules (NEVER invert for NO) |
| `memory/feedback_audit_self_validation.md` | Self-validate before reporting findings |
| `memory/feedback_bot_sessions.md` | Bot-scoped session rules |

---

## 14. SESSION LEARNINGS / RULES

1. **Read CLAUDE.md first** — prime directive: working code is sacred
2. **Paper trading IS production** — treat every change as if $25K is deployed
3. **One fix per commit** — no "while I'm in here" refactors
4. **Preserve function signatures** — search all callers before changing any
5. **No structural refactors during bug fixes**
6. **Self-validate findings** — re-read code, trace paths, check tests, rate confidence, remove false positives before reporting
7. **NEVER disable esports games without explicit user instruction**
8. **Don't commit other bots' files during a bot-scoped session** (learned S134)
9. **Verify data exists before building features** (learned S134)
10. **S120 P&L was FALSE** — always verify P&L claims against ground truth query

---

## 15. EXHAUSTIVE FILE LIST (WHAT TO AUDIT)

### MirrorBot (6 files)
```
bots/mirror_bot.py                          # ~1800 lines — main bot
bots/mirror_calibration.py                  # FTS calibration (shadow-only)
bots/mirror_ml_selector.py                  # ML trade selector (shadow-only)
bots/mirror_adaptive_safety.py              # Adaptive drawdown (DISABLED)
base_engine/learning/elite_reliability.py   # Bayesian win rate
base_engine/learning/elite_watchlist.py     # RTDS watchlist
base_engine/learning/elite_detector.py      # Elite trader detection
```

### EsportsBot (4+ files)
```
bots/esports_bot.py                         # ~6000 lines — main bot
bots/esports_live_bot.py                    # Live in-match trading
bots/esports_series_bot.py                  # Series/BO3/BO5
esports/                                    # Glicko-2, ML models, calibrators
```

### WeatherBot (5 files)
```
bots/weather_bot.py                         # ~4700 lines — main bot
base_engine/weather/probability_engine.py   # ~500 lines — forecast → probability
base_engine/weather/forecast_client.py      # ~1300 lines — Open-Meteo API
base_engine/weather/market_mapper.py        # ~1050 lines — question parsing
base_engine/weather/station_registry.py     # ~1500 lines — 106 cities
```

### Shared modules (7 files)
```
bots/base_bot.py                            # Base class for all bots
base_engine/risk/bankroll_manager.py        # Kelly sizing
base_engine/execution/paper_trading.py      # VWAP fill simulation
base_engine/execution/order_gateway.py      # Order routing
bots/risk_manager.py                        # Risk limits
bots/position_manager.py                    # Position tracking
database.py                                 # DB pool, queries
base_engine/data/resolution_backfill.py     # Resolution pipeline
```

**Total**: ~22 files, ~25,000+ lines to audit.
