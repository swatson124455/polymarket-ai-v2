# AGENT HANDOFF — Full System Session 111 (2026-03-19)
# CARBON COPY HANDOFF — ALL BOTS, ALL CONTEXT, ALL LEARNINGS

> **Purpose**: This document is designed so a NEW agent can pick up the ENTIRE project seamlessly with zero context loss. It covers all 14 bots, all infrastructure, all learnings, all rules, all P&L data, all outstanding items, and all behavioral directives accumulated across 110+ sessions.

---

## TABLE OF CONTENTS

1. [System Overview](#1-system-overview)
2. [Architecture & File Map](#2-architecture--file-map)
3. [Bot Registry & Status](#3-bot-registry--status)
4. [VPS & Infrastructure](#4-vps--infrastructure)
5. [Database Schema & Key Tables](#5-database-schema--key-tables)
6. [P&L — Current State (All Bots)](#6-pl--current-state-all-bots)
7. [Per-Bot Deep Dives](#7-per-bot-deep-dives)
   - 7a. EsportsBot
   - 7b. WeatherBot
   - 7c. MirrorBot
8. [Critical Traps — DO NOT BREAK](#8-critical-traps--do-not-break)
9. [Behavioral Directives — HOW TO WORK](#9-behavioral-directives--how-to-work)
10. [State Persistence Decision Tree](#10-state-persistence-decision-tree)
11. [Outstanding Items (All Bots)](#11-outstanding-items-all-bots)
12. [Session History Summary](#12-session-history-summary)
13. [Config Reference (Live VPS Values)](#13-config-reference-live-vps-values)
14. [Deploy & Rollback Procedures](#14-deploy--rollback-procedures)
15. [Verification Commands](#15-verification-commands)
16. [Key SQL Queries](#16-key-sql-queries)

---

## 1. SYSTEM OVERVIEW

**What is this?**: A live 14-bot Polymarket automated trading system. Currently in **paper trading** mode (`SIMULATION_MODE=true`). Paper trading IS production — the ONLY difference is whether orders go to the CLOB or the paper_trades table. Everything else runs identically.

**Platform**: Polymarket (prediction markets on Polygon). Bots analyze markets, compute edges using ML models + domain-specific signals, and place trades via the CLOB API.

**Owner**: Sam (user). Senior technical user, hands-on, expects surgical precision and zero scope creep.

**Current state**: 14 bots running on VPS. Three primary revenue bots (MirrorBot, WeatherBot, EsportsBot) are the active focus. Others are either dormant, have no market supply, or are awaiting live trading.

**Tech stack**: Python 3.13, asyncio, asyncpg (PostgreSQL), Redis, structlog, WebSockets (v15), PandaScore API (esports), NWS/NOAA APIs (weather), Polymarket CLOB/Gamma/WS APIs.

---

## 2. ARCHITECTURE & FILE MAP

### Top-Level Structure
```
polymarket-ai-v2/
├── main.py                    # Entry point, BOT_REGISTRY, preflight, bot lifecycle
├── config/
│   ├── settings.py            # Pydantic settings (94KB, ALL config lives here)
│   ├── config.yaml
│   ├── config_loader.py
│   └── geo_restrictions.py
├── bots/
│   ├── base_bot.py            # Base class for ALL bots
│   ├── esports_bot.py         # ~5600 lines — EsportsBot (Glicko, BetaCalibrator, PandaScore)
│   ├── esports_live_bot.py    # EsportsLiveBot
│   ├── weather_bot.py         # WeatherBot (NWS, NBM, probability engine)
│   ├── mirror_bot.py          # MirrorBot (RTDS copy trading, elite detector)
│   ├── ensemble_bot.py        # EnsembleBot (ML ensemble)
│   ├── arbitrage_bot.py       # ArbitrageBot
│   ├── cross_platform_arb_bot.py
│   ├── logical_arb_bot.py
│   ├── sports_bot.py
│   ├── sports_arb_bot.py
│   ├── sports_injury_bot.py
│   ├── sports_live_bot.py
│   ├── oracle_bot.py
│   ├── llm_forecaster_bot.py
│   ├── elite_watchlist.py     # Elite trader detection
│   ├── mirror_adaptive_safety.py
│   ├── mirror_calibration.py
│   ├── mirror_chronos_filter.py
│   └── mirror_trade_selector.py
├── base_engine/
│   ├── base_engine.py         # 130KB — main orchestration engine
│   ├── analysis/              # Market analysis
│   ├── cache/                 # Caching layer
│   ├── chain/                 # Blockchain integration
│   ├── coordination/          # Multi-bot coordination
│   ├── data/                  # Data management (daily_counter.py, ingestion)
│   ├── execution/             # Order execution (paper_trading_engine.py, order placement)
│   ├── exchanges/             # Exchange adapters
│   ├── learning/              # ML learning subsystem
│   ├── ml/                    # ML models
│   ├── monitoring/            # System monitoring
│   ├── portfolio/             # Position tracking (position_manager.py, bankroll_manager.py)
│   ├── prediction/            # Prediction engines (prediction_engine.py)
│   ├── risk/                  # Risk management (risk_manager.py)
│   ├── weather/               # Weather-specific features (forecast_client.py, probability_engine.py)
│   └── utils/
├── schema/migrations/         # 056 SQL migrations (PostgreSQL)
├── scripts/                   # 70+ diagnostic/utility scripts
│   ├── bot_pnl.py             # CANONICAL P&L script
│   ├── audit_pnl.py
│   ├── audit_mirror_pnl.py
│   ├── esports_diag.py
│   └── ...
├── tests/                     # 1090+ unit tests
├── deploy/                    # Deployment scripts (deploy.sh — atomic symlink swap)
├── ui/                        # Dashboard frontend (app.py, static/)
├── CLAUDE.md                  # Development directive (MUST READ — rules of engagement)
└── AGENT_HANDOFF_*.md         # Session handoff documents
```

### BOT_REGISTRY (from main.py)
```python
BOT_REGISTRY = {
    "ArbitrageBot": (ArbitrageBot, "BOT_ENABLED_ARBITRAGE"),
    "MirrorBot": (MirrorBot, "BOT_ENABLED_MIRROR"),
    "CrossPlatformArbBot": (CrossPlatformArbBot, "BOT_ENABLED_CROSS_PLATFORM_ARB"),
    "OracleBot": (OracleBot, "BOT_ENABLED_ORACLE"),
    "SportsBot": (SportsBot, "BOT_ENABLED_SPORTS"),
    "LLMForecasterBot": (LLMForecasterBot, "BOT_ENABLED_LLM_FORECASTER"),
    "WeatherBot": (WeatherBot, "BOT_ENABLED_WEATHER"),
    "SportsInjuryBot": (SportsInjuryBot, "BOT_ENABLED_SPORTS_INJURY"),
    "SportsLiveBot": (SportsLiveBot, "BOT_ENABLED_SPORTS_LIVE"),
    "SportsArbBot": (SportsArbBot, "BOT_ENABLED_SPORTS_ARB"),
    "EsportsBot": (EsportsBot, "BOT_ENABLED_ESPORTS"),
    "EsportsLiveBot": (EsportsLiveBot, "BOT_ENABLED_ESPORTS_LIVE"),
    "LogicalArbBot": (LogicalArbBot, "BOT_ENABLED_LOGICAL_ARB"),
    # EsportsSeriesBot exists but may be disabled/not in registry
}
```

### Key Shared Modules
| Module | Role | Touch with care |
|--------|------|----------------|
| `base_bot.py` | Base class for ALL bots. `place_order()`, scan loop, lifecycle | Affects ALL 14 bots |
| `bankroll_manager.py` | Position SIZING (Kelly criterion) | Affects all trading |
| `risk_manager.py` | Position LIMITS (exposure caps, drawdown) | Affects all trading |
| `position_manager.py` | Position tracking, price updates every 10s | Affects all bots |
| `database.py` | All DB operations, `insert_trade_event()` | Affects everything |
| `prediction_engine.py` | ML ensemble predictions | Affects EnsembleBot + learning |
| `paper_trading_engine.py` | Paper trade execution, fill modeling | Affects all paper trades |
| `settings.py` | ALL configuration | Affects everything |

---

## 3. BOT REGISTRY & STATUS

### Active Bots (as of 2026-03-19)

| Bot | Status | Markets | P&L (All-time) | Notes |
|-----|--------|---------|-----------------|-------|
| **MirrorBot** | Active, scanning | 200 cap | **+$18,469** | Copy trading via RTDS. 5 P&L fixes S109. |
| **WeatherBot** | Active, scanning | 500 cap | **+$2,881** | NWS/NBM forecasts. Fill pipeline fixed S108. |
| **EsportsBot** | Active, scanning | ~16-18 | **-$1,303** | Glicko+BetaCalibrator. Anti-churn fixed S109/S110. Would be +$222 without churn. |
| **EsportsLiveBot** | Active | Shared w/ Esports | Included above | Live match variant |
| **EnsembleBot** | Active, low volume | ~100 | **+$1** | ML ensemble, minimal trades |
| **ArbitrageBot** | Active, scanning | Variable | ~$0 | Cross-market arbitrage |
| **LogicalArbBot** | Active | Variable | ~$0 | Logical contradiction arb |
| Others | Running but dormant | 0 | $0 | No market supply or not configured |

### Corrected P&L (post-Session 87 dedup, as of 2026-03-19)
| Bot | Entries | Exits | Resolutions | Realized |
|-----|---------|-------|-------------|----------|
| MirrorBot | 511+ | 62+ | 376+ | **+$18,469** |
| WeatherBot | 708+ | 40+ | 156+ | **+$2,881** |
| EsportsBot | 165+ | 84+ | 117+ | **-$1,303** |
| EnsembleBot | 101 | 0 | 4 | **+$1** |

---

## 4. VPS & INFRASTRUCTURE

### VPS Details
- **Host**: Ubuntu-3 (AWS Lightsail) at `34.251.224.21`
- **Specs**: 16GB RAM, 4 vCPU
- **SSH**: `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21`
- **Service**: `sudo systemctl restart polymarket-ai`
- **Logs**: `sudo journalctl -u polymarket-ai -f`
- **Deploy path**: `/opt/polymarket-ai-v2/` (atomic symlink swap via `deploy.sh`)
- **Backups**: `.bak` files created pre-deploy

### Key Infrastructure
- **PostgreSQL**: `polymarket` database (partitioned `trade_events` by month)
- **Redis**: Cooldown persistence, caching, rate limiting
- **WebSockets**:
  - Polymarket WS for real-time price updates (token subscriptions)
  - RTDS WS (`wss://ws-live-data.polymarket.com`) for MirrorBot copy trading
- **PandaScore API**: Esports live match data (~400/hr vs 1000/hr budget, independent 15s time guard)
- **NWS/NOAA APIs**: Weather forecasts (NBM, GFS, HRRR)
- **Ingestion Scheduler**: Runs market data ingestion with advisory lock protection (fixed S90)

### Deploy Process
```bash
# From local machine — SCP files then restart
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem <file> ubuntu@34.251.224.21:/opt/polymarket-ai-v2/<path>
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo systemctl restart polymarket-ai"

# Full deploy (uses deploy.sh — atomic symlink swap)
# Creates .bak backups, swaps symlink, restarts service
```

---

## 5. DATABASE SCHEMA & KEY TABLES

### Core Tables
| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `trade_events` | **P&L AUTHORITY** (immutable audit trail) | `bot_name`, `market_id`, `event_type` (ENTRY/EXIT/RESOLUTION), `realized_pnl`, `price`, `size`, `side`, `event_data` (JSONB), `correlation_id`, `idempotency_key`, `event_time` |
| `positions` | Open position tracking | `market_id`, `bot_name`, `entry_price`, `size`, `side`, `unrealized_pnl`, `status`, `opened_at` |
| `paper_trades` | Legacy trade log (demoted) | `market_id`, `bot_name`, `side`, `price`, `shares` — NO `metadata` JSONB column, NO `resolved_pnl` |
| `traded_markets` | Market universe | `condition_id`, `bot_names` (TEXT, use LIKE not ANY), `resolved_at` |
| `daily_counters` | Additive daily state (exposure) | `counter_name`, `counter_date`, `value` |
| `system_kv` | Generic key-value store | `key`, `value` — used for canary stage persistence |
| `prediction_log` | Model predictions | `trade_executed` (bool), `model_name` — NO `rejection_reason` column |

### Critical Schema Facts
- `trade_events` is **partitioned by `event_time`** (monthly). `ON CONFLICT (idempotency_key, event_time)` is BROKEN on partitioned tables — use `INSERT...SELECT WHERE NOT EXISTS` for RESOLUTION events.
- `trade_events` has immutability trigger `trg_trade_events_immutable` — must `DISABLE TRIGGER` for data cleanup.
- `positions` table has NO `closed_at`, NO `updated_at`, NO `avg_entry_price`. Only `opened_at`, `status`, `entry_price`.
- `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`. `created_at` has NO DEFAULT.
- `traded_markets.bot_names` is TEXT column — use `LIKE '%BotName%'` not `= ANY()`.
- `trade_events` JSONB column is `event_data` — NOT `metadata_json`.

### Latest Migrations (056)
| Migration | Purpose |
|-----------|---------|
| 056 | Mirror archive bad data |
| 055 | Whale trade logging table |
| 054 | system_kv (canary stage persistence) |
| 053 | EsportsBot schema fixes |
| 052 | Purge 5 dead tables |
| 050 | trade_events monthly partitioning |

---

## 6. P&L — CURRENT STATE (All Bots)

### Corrected All-Time P&L (as of 2026-03-19 21:45 UTC)

| Bot | Realized P&L | Key Driver |
|-----|-------------|------------|
| MirrorBot | **+$18,469** | Elite copy trading. 5 P&L bookkeeping fixes in S109. |
| WeatherBot | **+$2,881** | NWS/NBM weather forecasts. Fill pipeline overhauled S108. |
| EsportsBot | **-$1,303** | Would be +$222 without churn. Mar 19 catastrophe (-$1,535 in one day). |
| EnsembleBot | **+$1** | Minimal activity. |
| **System Total** | **~+$20,048** | |

### P&L Math (MANDATORY — NEVER INVERT)
```python
# UNIFORM formula for ALL sides (YES and NO):
invested = entry_price * size
unrealized_pnl = (current_price - entry_price) * size
# entry_price and current_price are TOKEN-SPECIFIC (not YES probability)
# position_manager.py line 392 confirms: pos.unrealized_pnl = (new_price - entry) * size
```

### P&L Data Sources
- **trade_events**: `realized_pnl` — CANONICAL source for realized P&L
- **positions**: `unrealized_pnl` — mark-to-market on open positions (updated every 10s)
- **paper_trades**: LEGACY — NEVER use for P&L. SELL/EXIT trades only in trade_events.
- **Canonical script**: `python scripts/bot_pnl.py BotName hours` (requires greenlet on VPS; fallback: direct psql queries)

---

## 7. PER-BOT DEEP DIVES

### 7a. EsportsBot (Latest: Session 110)

**File**: `bots/esports_bot.py` (~5600 lines)

**How it works**:
1. **Scan loop** (every 2s during live matches): Phase A (housekeeping) → Phase B (parallel gather: retrain, patch drift, live matches, markets) → Phase C (parallel `_analyze_one()` per market)
2. **Prediction**: Glicko-2 ratings + BetaCalibrator (calibrated probabilities) per game. 4/8 games fitted (Valorant, LoL, CS2, SC2).
3. **Three entry paths**: (a) Scan path `_analyze_one()`, (b) WS reactive path (real-time price updates), (c) `_series_scan()` (series-level betting)
4. **Anti-churn system (S109+S110)**: All three paths gated by `_recently_exited` (900s cooldown, Redis-persisted) + `_market_entry_times` (2 entries per 12h, in-memory)
5. **Stop-loss**: 15% per position. Exit-failure paths now always set cooldown (S110 fix).

**Key S109/S110 Fixes**:
- **S109**: Anti-churn gates on scan + WS paths. Exit cooldown (900s), entry cap (2/12h), Redis persistence.
- **S110**:
  - **P0 ROOT CAUSE**: `_series_scan()` bypassed ALL anti-churn gates — unguarded backdoor. Fixed with `_churn_blocked()` helper.
  - **P0**: Exit-failure cooldown gaps — two paths where `_recently_exited` was never set on failed exits.
  - **OPT-4**: Retrain/accuracy parallelized into Phase B gather (4-branch).
  - **Timing instrumentation**: `timing_ms` dict in scan summary logs.
  - **Scan interval**: 10s → 2s. Stop-loss reactions 5x faster.

**Churn Impact Analysis (S110)**:
- 12 markets had churn (re-entry within 900s of exit)
- Churn market P&L: **-$1,525** (primarily 0x2ef64c43 at -$1,359 and 0x284aaa20 at -$481)
- Non-churn market P&L: **+$222**
- Post-S109/S110 deploy: **0 churn events observed**

**EsportsBot Outstanding Items**:
| Priority | Item | Status |
|----------|------|--------|
| P2 | RC4: Entry price inflation (positions stores requested price not fill) | Deferred — touches shared position_manager |
| P2 | Kelly degradation (needs all 8 games fitted) | Blocked on Dota2/CoD/R6/RL data |
| P3 | LoL Brier=0.2842 (near 0.30 halt) | Stable, monitoring |
| P3 | WS reconnect drops every ~40s-5min | Auto-reconnects working |
| P3 | `no_prediction: 12` per scan (team name matching) | Improve team name parser |
| P4 | Dota2 Brier=0.3002 (suspension active) | Self-governs when fitted |
| P4 | Phase B PandaScore refresh ~2s every 15s | Known, acceptable |
| P5 | taker_side dead code | No data source |

**BetaCalibrator Status**:
| Game | N | Status |
|------|---|--------|
| Valorant | 1,927 | FITTED |
| LoL | 365-367 | FITTED |
| CS2 | 229 | FITTED |
| SC2 | 52 | FITTED |
| Dota2 | ~40 | Not logging (time window issue) |
| CoD/R6/RL | 0 | No data |

**EsportsBot VPS Config**:
```
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 10000}}
ESPORTS_MIN_CONFIDENCE=0.48
ESPORTS_MIN_EDGE=0.05
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_STOP_LOSS_PCT=0.15
ESPORTS_EXIT_COOLDOWN_SECONDS=900
ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=2
ESPORTS_ENTRY_WINDOW_HOURS=12.0
SCAN_INTERVAL_ESPORTS_LIVE=2  # Changed from 10 in S110
```

---

### 7b. WeatherBot (Latest: Session 108)

**File**: `bots/weather_bot.py`

**How it works**:
1. Ingests NWS/NOAA weather forecasts (GFS, HRRR, NBM)
2. `probability_engine.py` computes temperature outcome probabilities
3. Compares model probabilities to market prices for edge detection
4. Alpha decay (5-min half-life) reduces edge for stale signals
5. Jump detection (3°F+ ensemble shift) boosts conviction 1.5x
6. NBM CDF benchmark flags 15pp+ disagreements for 1.3x boost

**Key S108 Fixes (Fill Pipeline Overhaul)**:
- **Taker factor 0.85**: Same-side taker trades penalized (you're crossing the spread)
- **bestAsk pre-filter**: Skip markets where best ask > model fair value
- **Volume passthrough**: Pass volume data through for Kyle lambda impact modeling
- **Same-side dedup**: Prevent duplicate entries on same side of same market
- **Ghost position fix**: Idempotent memory was creating size=0 positions

**Key S107 Fixes**:
- **Sizing units bug**: Was passing USD to `place_order()` instead of shares
- **4hr re-entry cooldown**: Prevents cycling in/out of same market
- **Ghost position bug**: Idempotent memory `setdefault()` created phantom positions

**WeatherBot Outstanding Items**:
| Priority | Item | Status |
|----------|------|--------|
| P3 | NO vs YES asymmetry (72% vs 39% WR) | Monitor before config change |
| P5 | Kalshi cross-platform arbitrage | Deferred, 8-16h effort |

**WeatherBot Config**:
```
WEATHER_MAX_POSITIONS=500
WEATHER_JUMP_THRESHOLD_F=3.0
WEATHER_JUMP_MAX_BOOST=1.5
WEATHER_NBM_DISAGREE_THRESHOLD=0.15
PAPER_REALISTIC_FILLS=true
PAPER_TAKER_SIDE_FILTER=true
PAPER_ALPHA_DECAY_HALF_LIFE_S=300
```

---

### 7c. MirrorBot (Latest: Session 109)

**File**: `bots/mirror_bot.py`

**How it works**:
1. **RTDS** (Real-Time Data Stream): Subscribes to `wss://ws-live-data.polymarket.com` global trade feed
2. **Elite detection**: Identifies high-performing traders (100+ trades OR $10k volume, 55%+ WR)
3. **Copy trading**: Mirrors elite trades with position sizing via BotBankrollManager
4. **Confidence scoring**: Bayesian posterior reliability, category-aware
5. Clears `_open_positions` on restart, re-enters by EOD UTC

**Key S109 Fixes (5 Root-Cause P&L Bookkeeping)**:
1. **condition_id enrichment**: Market lookup was failing silently
2. **Phase 4b gate**: Prevented bad data from entering trade pipeline
3. **Same-side dedup**: Prevented duplicate positions on same market side
4. **Stale uPnL cleanup**: Cleaned up unrealized P&L on closed positions
5. **RESOLUTION size**: Fixed size field in resolution events

**Cap change S109**: `MIRROR_MAX_PER_MARKET` 200 → 400

**MirrorBot Config**:
```
MIRROR_MIN_CONFIDENCE=0.55
MIRROR_MIN_RELIABILITY=0.52
MIRROR_MAX_POSITIONS=200
MIRROR_MAX_CONCURRENT_POSITIONS=200
MIRROR_MAX_PER_MARKET=400
MIRROR_MAX_CATEGORY_EXPOSURE_USD=40000
MIRROR_HOT_TRADE_MAX_SECONDS=900
WATCHLIST_ENABLED=true
WATCHLIST_SIZE=1000
```

**Critical MirrorBot Facts**:
- `_market_meta_cache` is 3-tuple `(cat, ttr, expiry_monotonic)` — NEVER expand
- Entry price uses CURRENT market price from `get_market_from_index()`, NOT trader's fill price
- RTDS envelope: Must unwrap `data.get("payload", data)` — trade data NOT at top level
- RTDS dedup: `on_rtds_trade()` handles own dedup, passes `transaction_hash=None`
- CLOB volume=0 — Never use volume gates for MirrorBot

---

## 8. CRITICAL TRAPS — DO NOT BREAK

These are hard-won lessons from 110+ sessions. Breaking any of these costs real money or causes silent failures:

### Data & Schema
- **trade_events is P&L AUTHORITY** — NEVER read paper_trades for P&L
- **trade_events JSONB column is `event_data`** — NOT `metadata_json`
- **paper_trades has NO `metadata` JSONB column** — never assume it exists
- **paper_trades has NO `resolved_pnl` column** — it's `resolved_at`
- **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time
- **RESOLUTION event idempotency BROKEN on partitioned tables** — use INSERT...SELECT WHERE NOT EXISTS
- **trade_events immutability trigger** exists — must DISABLE/ENABLE for data cleanup
- **positions table**: NO `closed_at`, NO `updated_at`, NO `avg_entry_price`. Only `opened_at`, `entry_price`, `status`
- **prediction_log**: NO `rejection_reason`. Use `trade_executed` (bool) + `model_name`
- **`traded_markets.bot_names`**: TEXT column — use `LIKE '%BotName%'` not `= ANY()`
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
- **asyncpg timestamps**: paper_trades uses `timestamp without time zone` — pass `.replace(tzinfo=None)`. `created_at` has NO DEFAULT.

### Trading Logic
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER pass "BUY"/"SELL"
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS** — Both must pass
- **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer
- **P&L formulas NEVER invert for NO** — prices are token-specific, uniform formula for all sides
- **MirrorBot entry price**: Uses CURRENT market price, NOT trader's fill price
- **CLOB volume=0** — Never use volume gates for MirrorBot
- **`PSEUDO_LABEL_ENABLED=false`** — DO NOT enable. Only market resolution labels are correct
- **Alpha decay requires `scan_start_mono` in event_data** — Only WeatherBot passes it

### Python / Runtime
- **Python 3.13 scoping**: `from X import Y` inside function body makes Y local for ENTIRE function. Use before import line → `UnboundLocalError`
- **`websockets.exceptions` must be imported explicitly** (v15 lazy-loads)
- **Do NOT use `asyncio.create_task()` for financial write-throughs** — Always `await`
- **PatchDriftDetector**: Only set `_patch_timestamps` on genuine patches (`old is not None`). Setting on first check falsely triggers 48h observation mode.

### Infrastructure
- **VPS deploys via `deploy.sh`**: Atomic symlink swap. Working tree ≠ VPS ≠ git HEAD
- **Position `current_price`**: Auto-updated every 10s by `position_manager._update_current_prices()`
- **`_open_positions` on restart**: MirrorBot clears in-memory positions; re-enters by EOD UTC
- **system_kv table**: Generic KV store (migration 054). Used for canary stage persistence. Key='canary_stage'
- **BOT_REGISTRY = 14 bots** — shared module change requires all 14 verified
- **`_market_meta_cache`**: 3-tuple `(cat, ttr, expiry_monotonic)` — NEVER expand

---

## 9. BEHAVIORAL DIRECTIVES — HOW TO WORK

### Scope Lock (NON-NEGOTIABLE)
You may ONLY make changes that are:
1. Explicitly listed in the handoff document as a fix/action item, OR
2. Explicitly requested by the user in this conversation

**Everything else is forbidden.** No exceptions.

- "I noticed X could be improved" → Mention it. Do NOT implement it.
- "The handoff mentions X as observational" → DO NOT TOUCH.
- "This would be a quick win" → Not your call. Ask the user first.
- "While fixing X, Y is related" → Fix X only.

**Origin**: Session 90. User added a `WEATHER_CITY_BLACKLIST` config unsolicited. User response: "NEVER fucking do that again."

### Bot-Scoped Sessions
- Each session is scoped to a single bot — do not modify other bots unless explicitly demanded
- Shared infrastructure changes OK ONLY if they directly fix a bug in the scoped bot
- **Esports sessions: PERMANENT SCOPE LOCK** — only touch esports files
- Cross-bot changes require explicit user approval

### CLAUDE.md Rules (enforced)
1. **One fix per commit** — no "while I'm in here" refactors
2. **Preserve every function signature** — unless the signature IS the bug
3. **Preserve every external interface** — DB columns, env vars, config keys are contracts
4. **No silent behavior changes** — state what changes from X to Y
5. **Never delete code you don't understand** — it may handle 3am edge cases
6. **No new dependencies without justification**
7. **No structural refactors during bug fixes**

### Before Editing ANY File
1. State the bug in one sentence
2. List files you will touch (if >3, justify)
3. Grep for dependents
4. Git snapshot (stash or commit)
5. Read the ENTIRE file

### Config Changes Are Behavioral Changes
- Tier 1 (thresholds): State change + expected impact
- Tier 2 (trade-universe gating): State what's blocked/allowed + rollback command
- Tier 3 (code changes): Full blast-radius protocol

### Forbidden Patterns
1. "While I'm in here" refactor
2. Band-aid `try/except` that hides real errors
3. Shotgun fix (changed 4 things hoping one works)
4. Scope creep
5. Silent migration
6. Optimistic rewrite

### Paper Trading IS Production
- NEVER skip features because "we're only paper trading"
- Going live is flipping a boolean. Everything else must already work.
- Test: "If we were live with $25K deployed, would I skip this?" → No? Implement it.

---

## 10. STATE PERSISTENCE DECISION TREE

| State type | Example | Correct mechanism |
|-----------|---------|------------------|
| Purely additive, resets daily | `_game_exposure[game] += size` | `daily_counters` write-through |
| Net counter (up+down), resets daily | `_daily_exposure` | Query paper_trades SUM on startup |
| TTL-based cooldown | `_recently_exited[market_id]` (15-min) | Redis key with matching TTL |
| Open position set | `_open_positions` | `positions` table; restore from DB on startup |
| Not needed across restarts | Live match tracking, API caches | Leave in memory |

### Current Implementations
- MirrorBot `_daily_exposure` → paper_trades SUM in `_restore_state_on_startup()`
- EsportsBot `_game_exposure` → `daily_counters` write-through + `_restore_exposure_from_db()`
- WeatherBot `_recently_exited` → Redis TTL in `_save_exit_to_redis()` / `_restore_exits_from_redis()`
- WeatherBot `_group_exposure`/`_city_exposure` → DB restore in `_restore_group_city_exposure_from_db()`
- MirrorBot `_open_positions` → `positions` table in `_restore_state_on_startup()`
- EsportsBot `_recently_exited` → Redis TTL (S109)
- EsportsBot `_market_entry_times` → in-memory only (resets on restart, mitigated by Redis cooldown)

---

## 11. OUTSTANDING ITEMS (ALL BOTS)

### System-Wide
| Priority | Item | Status |
|----------|------|--------|
| P2 | 479 markets still unresolved in traded_markets | Resolving naturally via backfill |
| P3 | NO vs YES asymmetry (72% vs 39% WR) | Monitor |
| P5 | Kalshi cross-platform arbitrage | Deferred (8-16h effort) |
| P5 | Remove diagnostic logging (session_factory warning, RTDS raw) | Low priority |

### EsportsBot
| Priority | Item | Status |
|----------|------|--------|
| P2 | RC4: Entry price inflation | Deferred — touches shared position_manager |
| P2 | Kelly degradation (needs 8 games) | Blocked on Dota2/CoD/R6/RL |
| P3 | LoL Brier=0.2842 (near 0.30 halt) | Stable |
| P3 | WS reconnect drops every ~40s-5min | Auto-reconnects working |
| P3 | `no_prediction: 12` (team name matching) | Ongoing |
| P4 | Dota2 Brier=0.3002 | Suspension active |

### WeatherBot
| Priority | Item | Status |
|----------|------|--------|
| P3 | NO/YES asymmetry | Monitor |

### MirrorBot
| Priority | Item | Status |
|----------|------|--------|
| (none critical) | All P0-P2 resolved through S109 | Healthy |

### Previously Resolved (DO NOT RE-OPEN)
- P0: RESOLUTION dedup broken → S87 atomic INSERT...SELECT
- P0: False observation mode on restart → S88 timestamp fix
- P0: Scheduler dead 11h (zombie advisory lock) → S90 shield + timeout
- P0: `_series_scan()` unguarded backdoor → S110 `_churn_blocked()` helper
- P0: Exit-failure cooldown gaps → S110 cooldown on all exit paths
- P1: Resolution backfill → S85 3 root causes fixed, 544 markets resolved
- P1: MirrorBot P&L audit → S86 3238 dup RESOLUTION events deleted
- P2: Scan loop speed → S110 OPT-4 + timing instrumentation + 2s interval
- P3: Alpha decay not firing → S100 `scan_start_mono` in event_data
- P3: Canary stage resets on restart → S100 DB persistence via system_kv
- P3: Deploy SSH orphan restart loop → S100 SSH timeouts
- P3: RTDS copy latency → S92 startup cache

---

## 12. SESSION HISTORY SUMMARY

### Session Timeline (key milestones)

| Session | Date | Scope | Key Changes |
|---------|------|-------|-------------|
| 72 | Mar 2026 | Multi | State persistence + EsportsBot P0-P2 + WeatherBot fixes |
| 73 | Mar 2026 | Multi | Zero-downtime deploy infrastructure |
| 74-76 | Mar 2026 | Multi | EsportsBot P7, VPS migrations, all-bots audit |
| 77 | Mar 11 | Multi | All-bots audit (12 items) + MirrorBot P1-P8 + TWO critical bugs |
| 79 | Mar 12 | Mirror | Selectivity tightening (confidence 0.10→0.55, elite 5→100 trades) |
| 81 | Mar 12 | Mirror | RTDS live + 6 fixes + paper_trades DB persistence bug (16h outage) |
| 83 | Mar 13 | Multi | Event-sourced trade ledger (trade_events table, 9 migrations) |
| 85 | Mar 14 | Multi | Resolution backfill fix (544 markets) + P&L data overhaul |
| 86 | Mar 14 | Multi | Ingestion sync fix + RESOLUTION event dedup |
| 87 | Mar 14 | Esports | RESOLUTION dedup fix (4878 dupes deleted) |
| 88 | Mar 14 | Esports | Observation mode false-positive fix |
| 89 | Mar 14 | Esports | E2-E5 features + 9 audit fixes |
| 90 | Mar 14 | Weather | P0 scheduler fix (zombie advisory lock + master timeout) |
| 91-92 | Mar 15 | Weather | Edge improvements + P1 jump detection + P2 NBM benchmark |
| 93-94 | Mar 15-16 | Mirror | Conformal dampening fix, Kelly 0.25, latency 2967→11.9ms |
| 95-97 | Mar 16 | Weather | 4 paper trading elevations, 3 stations, P&L breakdown |
| 98-100 | Mar 16-17 | Weather | Alpha decay, canary persistence, SSH timeouts |
| 101-103 | Mar 17-18 | Mirror | Bucket filters, whale trades, confidence gate, hard floor 10c |
| 104 | Mar 18 | Weather | Fill quality logging, exposure leak, daily counter |
| 106 | Mar 18 | Weather | Taker-side flat factor, probability engine fallback |
| 107 | Mar 18-19 | Weather | Fill pipeline, ghost position fix, sizing units bug |
| 108 | Mar 19 | Weather | Fill pipeline overhaul (taker 0.85, bestAsk, volume, dedup) |
| 109 | Mar 19 | Mirror | 5 root-cause P&L bookkeeping fixes |
| 109 | Mar 19 | Esports | Anti-churn: exit cooldown, cache invalidation, entry cap |
| **110** | **Mar 19** | **Esports** | **OPT-4 parallel retrain, timing instrumentation, 10s→2s scan, P0 series_scan backdoor fix, exit cooldown gaps** |

---

## 13. CONFIG REFERENCE (LIVE VPS VALUES)

```env
# === TRADING MODE ===
SIMULATION_MODE=true  # Paper trading. Flip to false for live.

# === BANKROLL (ALL BOTS) ===
BOT_BANKROLL_CONFIG={"EsportsBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 10000}, "WeatherBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 10000}, "MirrorBot": {"capital": 20000, "kelly_fraction": 0.25, "max_bet_usd": 300, "max_daily_usd": 10000}}

# === RISK LIMITS ===
RISK_MAX_POSITION_SIZE_USD=1000
RISK_MAX_TOTAL_EXPOSURE_USD=20000
RISK_MAX_DAILY_LOSS_USD=10000
RISK_MAX_DRAWDOWN_PCT=20

# === ESPORTS ===
ESPORTS_MIN_CONFIDENCE=0.48
ESPORTS_MIN_EDGE=0.05
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_STOP_LOSS_PCT=0.15
ESPORTS_EXIT_COOLDOWN_SECONDS=900
ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=2
ESPORTS_ENTRY_WINDOW_HOURS=12.0
SCAN_INTERVAL_ESPORTS_LIVE=2
ESPORTS_FRESHNESS_DECAY_SECONDS=30.0

# === MIRROR ===
MIRROR_MIN_CONFIDENCE=0.55
MIRROR_MIN_RELIABILITY=0.52
MIRROR_MAX_POSITIONS=200
MIRROR_MAX_CONCURRENT_POSITIONS=200
MIRROR_MAX_PER_MARKET=400
MIRROR_MAX_CATEGORY_EXPOSURE_USD=40000
MIRROR_HOT_TRADE_MAX_SECONDS=900
WATCHLIST_ENABLED=true
WATCHLIST_SIZE=1000

# === WEATHER ===
WEATHER_MAX_POSITIONS=500
WEATHER_JUMP_THRESHOLD_F=3.0
WEATHER_JUMP_MAX_BOOST=1.5
WEATHER_NBM_DISAGREE_THRESHOLD=0.15

# === PAPER TRADING FEATURES ===
PAPER_REALISTIC_FILLS=true
PAPER_KYLE_LAMBDA_ENABLED=true
PAPER_BOOK_WALK_ENABLED=true
PAPER_TAKER_SIDE_FILTER=true
PAPER_ALPHA_DECAY_HALF_LIFE_S=300

# === ENSEMBLE / LEARNING ===
ENSEMBLE_MIN_EDGE=0.02
ENSEMBLE_BLEND=1.0
MIN_CONFIDENCE_THRESHOLD=0.45
RETRAIN_INTERVAL_HOURS=6
AUTO_RETRAIN_ON_DEGRADATION=true
MODEL_MIN_TRAINING_SAMPLES=50

# === ELITE ===
ELITE_MIN_TRADES=100
ELITE_MIN_VOLUME_USD=10000
ELITE_MIN_WIN_RATE=0.55
ELITE_LOOKBACK_DAYS=365

# === PSEUDO LABELING ===
PSEUDO_LABEL_ENABLED=false  # DO NOT ENABLE
```

---

## 14. DEPLOY & ROLLBACK PROCEDURES

### Standard Deploy (single file)
```bash
# Backup + copy + restart
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo cp /opt/polymarket-ai-v2/<file> /opt/polymarket-ai-v2/<file>.bak"

scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem <local_file> \
  ubuntu@34.251.224.21:/opt/polymarket-ai-v2/<path>

ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo systemctl restart polymarket-ai"
```

### Rollback
```bash
# File-level rollback
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo cp /opt/polymarket-ai-v2/<file>.bak /opt/polymarket-ai-v2/<file> && sudo systemctl restart polymarket-ai"

# Config-only rollback (no restart needed if using env override)
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "export SCAN_INTERVAL_ESPORTS_LIVE=10 && sudo systemctl restart polymarket-ai"
```

### Post-Deploy Verification
Wait 60-90s for first scans to appear after restart.

---

## 15. VERIFICATION COMMANDS

```bash
SSH="ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem -o StrictHostKeyChecking=no ubuntu@34.251.224.21"

# === GENERAL ===
$SSH "sudo journalctl -u polymarket-ai --since '2 min ago' --no-pager | tail -50"
$SSH "sudo systemctl status polymarket-ai"

# === ESPORTS ===
$SSH "sudo journalctl -u polymarket-ai --since '2 min ago' --no-pager | grep esportsbot_scan_summary | tail -5"
$SSH "sudo journalctl -u polymarket-ai --since '1 min ago' --no-pager | grep 'Scan cycle starting.*EsportsBot' | tail -10"
$SSH "sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'esportsbot_ws_subscribed\|ws_trading'"
$SSH "sudo journalctl -u polymarket-ai --since '1 hour ago' --no-pager | grep pandascore_rate | tail -5"

# === WEATHER ===
$SSH "sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'WeatherBot' | tail -20"
$SSH "sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'weather_trade\|weather_scan' | tail -10"

# === MIRROR ===
$SSH "sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'MirrorBot' | tail -20"
$SSH "sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep 'rtds_\|mirror_trade' | tail -10"

# === ERRORS ===
$SSH "sudo journalctl -u polymarket-ai --since '10 min ago' --no-pager | grep -i 'error\|exception\|traceback' | tail -20"

# === P&L ===
$SSH "sudo -u postgres psql -d polymarket -c \"SELECT bot_name, event_type, COUNT(*), COALESCE(SUM(realized_pnl),0) as pnl FROM trade_events GROUP BY bot_name, event_type ORDER BY bot_name, event_type;\""
```

---

## 16. KEY SQL QUERIES

```sql
-- All-time P&L by bot
SELECT bot_name,
  SUM(CASE WHEN event_type='EXIT' THEN realized_pnl ELSE 0 END) as exit_pnl,
  SUM(CASE WHEN event_type='RESOLUTION' THEN realized_pnl ELSE 0 END) as res_pnl,
  SUM(realized_pnl) as total_pnl,
  COUNT(*) FILTER (WHERE event_type='ENTRY') as entries,
  COUNT(*) FILTER (WHERE event_type='EXIT') as exits,
  COUNT(*) FILTER (WHERE event_type='RESOLUTION') as resolutions
FROM trade_events
WHERE event_type IN ('ENTRY','EXIT','RESOLUTION')
GROUP BY bot_name ORDER BY total_pnl DESC;

-- Daily P&L for a specific bot
SELECT DATE(event_time) as day,
  COUNT(*) FILTER (WHERE event_type='ENTRY') as entries,
  COUNT(*) FILTER (WHERE event_type='EXIT') as exits,
  SUM(realized_pnl) as day_pnl
FROM trade_events
WHERE bot_name='EsportsBot' AND event_type IN ('EXIT','RESOLUTION')
GROUP BY DATE(event_time) ORDER BY day;

-- Open positions
SELECT bot_name, COUNT(*), SUM(unrealized_pnl) as total_upnl
FROM positions WHERE status='open'
GROUP BY bot_name;

-- Churn detection (re-entry within 900s of exit)
WITH ordered_events AS (
  SELECT market_id, event_type, event_time,
    ROW_NUMBER() OVER (PARTITION BY market_id ORDER BY event_time) as rn
  FROM trade_events
  WHERE bot_name='EsportsBot' AND event_type IN ('ENTRY','EXIT')
)
SELECT DISTINCT e.market_id
FROM ordered_events e
JOIN ordered_events x ON x.market_id = e.market_id
  AND x.event_type = 'EXIT' AND e.event_type = 'ENTRY'
  AND e.event_time > x.event_time
  AND e.event_time < x.event_time + INTERVAL '900 seconds';

-- Database: polymarket (NOT polymarket_ai)
-- Access: sudo -u postgres psql -d polymarket
```

---

## END OF HANDOFF

**Date**: 2026-03-19
**Session**: 111 (Full System Carbon Copy)
**Author**: Claude Agent (Session 110 context)
**Next agent**: Pick up from any bot-scoped or system-wide task. Read this document + CLAUDE.md + the bot-specific latest handoff for the scoped bot before writing any code.
