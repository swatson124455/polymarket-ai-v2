# EsportsBot Ecosystem — Complete Agent Handoff (Session 63)

> **Purpose**: Carbon-copy handoff for a new agent to continue EsportsBot ecosystem development seamlessly.
> **Scope**: Single-bot ecosystem session (3 bots: EsportsBot, EsportsLiveBot, EsportsSeriesBot). No bleed to other modules.
> **Date**: 2026-03-08
> **Branch**: `master` (main branch is `main`)
> **HEAD commit**: `567f7e7` (WeatherBot doom loop fix — not esports-related)
> **Test count**: 1242 passed (system-wide). ~288 esports-specific tests across 7 files.

---

## TABLE OF CONTENTS
1. [System Overview](#1-system-overview)
2. [Architecture & File Map](#2-architecture--file-map)
3. [The Three Esports Bots](#3-the-three-esports-bots)
4. [Data Pipeline](#4-data-pipeline)
5. [Models & ML](#5-models--ml)
6. [Live Infrastructure](#6-live-infrastructure)
7. [Bankroll & Sizing](#7-bankroll--sizing)
8. [Market Discovery](#8-market-discovery)
9. [Configuration Reference](#9-configuration-reference)
10. [All Prior Fixes (Sessions 53-62)](#10-all-prior-fixes-sessions-53-62)
11. [Uncommitted Changes](#11-uncommitted-changes)
12. [Known Issues & Next Steps](#12-known-issues--next-steps)
13. [Testing](#13-testing)
14. [Critical Patterns & Traps](#14-critical-patterns--traps)
15. [Deploy Protocol](#15-deploy-protocol)
16. [CLAUDE.md Rules Summary](#16-claudemd-rules-summary)

---

## 1. SYSTEM OVERVIEW

This is a 14-bot (was 15; EnsembleBot archived Session 61) automated Polymarket trading system. Real capital at risk. The esports ecosystem is 3 of those 14 bots, sharing a common `esports/` package.

### The 3 Esports Bots
| Bot | Purpose | Scan Interval | Key File |
|-----|---------|---------------|----------|
| **EsportsBot** | Pre-match & live match winner prediction | 120s (10s during live) | `bots/esports_bot.py` |
| **EsportsLiveBot** | In-game event-driven trading (baron, economy breaks) | 60s idle / 10s active | `bots/esports_live_bot.py` |
| **EsportsSeriesBot** | BO3/BO5 series outcome trading | 300s idle / 30s active | `bots/esports_series_bot.py` |

### Supported Games
- **League of Legends (LoL)** — Full ML model (XGBoost + Glicko-2 blend)
- **Counter-Strike 2 (CS2)** — XGBoost economy model (graduated, Brier 0.2473)
- **Dota 2** — Glicko-2 heuristic only (no dedicated model)
- **Valorant** — Glicko-2 heuristic only (no dedicated model)

### How It Fits in the System
```
main.py → BOT_REGISTRY → BaseBot subclasses
                           ├── EsportsBot        (uses esports/ package)
                           ├── EsportsLiveBot    (uses esports/ package)
                           ├── EsportsSeriesBot  (uses esports/ package)
                           ├── WeatherBot        (uses weather/ package)
                           ├── SportsBot, SportsArbBot, SportsLiveBot, SportsInjuryBot
                           ├── ArbitrageBot, LogicalArbBot, CrossPlatformArbBot
                           ├── MirrorBot, OracleBot, LLMForecasterBot
                           └── (EnsembleBot — ARCHIVED Session 61)
```

All bots inherit from `BaseBot` (`bots/base_bot.py`), which provides:
- `start()`/`stop()` lifecycle
- `_scan_loop()` — main polling loop with kill switch, backoff, burst scan
- `place_order()` — delegates to `base_engine.place_order()` (expects `side="YES"` or `side="NO"`, NEVER "BUY"/"SELL")
- `calculate_bot_position_size()` — uses `BotBankrollManager`
- `on_price_update()` — WebSocket price handler
- `apply_signal_enhancements()` — signal/flow/trend multipliers
- Whale alert listener via Redis pubsub
- Latency tracking, heartbeat recording, metrics

---

## 2. ARCHITECTURE & FILE MAP

### Complete esports/ Package (22 Python files)
```
esports/
├── __init__.py
├── data/
│   ├── __init__.py
│   ├── esports_data_collector.py    # 606 lines — PandaScore → DB training data + Glicko-2
│   ├── esports_db.py                # DB helpers for esports training data
│   ├── pandascore_client.py         # 422 lines — Async HTTP client for PandaScore REST API
│   ├── riot_api_client.py           # Riot Games API client (LoL live data)
│   └── hltv_scraper.py             # HLTV scraper for CS2 data
├── kelly/
│   ├── __init__.py
│   └── esports_bankroll_manager.py  # Separate Kelly pool for esports ($5K capital)
├── live/
│   ├── __init__.py
│   ├── esports_event_detector.py    # Game state → betting signals (baron, economy break, etc.)
│   ├── esports_game_monitor.py      # PandaScore live match polling → asyncio.Queue
│   └── esports_live_trigger.py      # Signal + market → trade execution for EsportsLiveBot
├── markets/
│   ├── __init__.py
│   ├── esports_market_scanner.py    # 269 lines — Keyword-based market discovery + classification
│   └── esports_market_service.py    # 425 lines — DB-backed discovery + CLOB price refresh
└── models/
    ├── __init__.py
    ├── glicko2.py                   # Glicko-2 rating system (Glickman 2013)
    ├── lol_win_model.py             # LoL XGBoost classifier with Glicko-2 metadata features
    ├── cs2_economy_model.py         # CS2 XGBoost round economy model
    ├── series_model.py              # BO3/BO5 conditional probability math
    ├── esports_trainer.py           # Training orchestrator: collect → train → validate → graduate
    └── patch_drift.py               # Patch change detection + observation mode + Brier monitoring
```

### Bot Files (in bots/)
```
bots/
├── base_bot.py              # 893 lines — ABC base class all bots inherit
├── esports_bot.py           # 1166 lines — Main EsportsBot
├── esports_live_bot.py      # ~600 lines — Event-driven live trading
└── esports_series_bot.py    # ~500 lines — Series outcome trading
```

### Test Files (in tests/)
```
tests/unit/
├── test_esports_bot.py           # 505 lines, ~49 tests
├── test_esports_live_bot.py      # 859 lines, ~43 tests
├── test_esports_series_bot.py    # 1120 lines, ~60 tests
├── test_esports_series_model.py  # 297 lines, ~40 tests
├── test_esports_bankroll.py      # 380 lines, ~22 tests
├── test_patch_drift.py           # 392 lines, ~34 tests
└── test_bankroll_manager.py      # 597 lines, ~40 tests (shared, not esports-only)
```

### Shared Dependencies (DO NOT MODIFY without full blast-radius analysis)
```
base_engine/
├── base_engine.py           # Core engine — market fetching, order placement
├── execution/
│   ├── paper_trading.py     # Paper trade simulator (side=YES/NO for entries, SELL for exits)
│   └── position_manager.py  # Position tracking, price updates every 10s
├── risk/
│   ├── risk_manager.py      # Risk limits (DEPRECATED for sizing — use BotBankrollManager)
│   └── bankroll_manager.py  # BotBankrollManager — per-bot Kelly sizing
└── data/
    └── database.py          # Async DB layer (PostgreSQL via SQLAlchemy)

config/
└── settings.py              # All env-based settings (ESPORTS_* block at lines ~898-967)
```

---

## 3. THE THREE ESPORTS BOTS

### 3A. EsportsBot (`bots/esports_bot.py`, 1166 lines)

**Purpose**: Pre-match and live match winner predictions on Polymarket esports markets.

**Key Methods**:
| Method | Line | Purpose |
|--------|------|---------|
| `__init__` | ~35 | Init PandaScore client, Glicko-2 trackers, market token map |
| `start` | ~100 | Init Glicko-2 from DB, start market service, load models |
| `stop` | ~130 | Save Glicko-2 ratings, close market service |
| `scan_and_trade` | ~170 | Main loop: discover markets → analyze → trade |
| `analyze_opportunity` | ~250 | Per-market: detect game, classify type, get prediction, check edge |
| `_get_model_prediction` | ~380 | Dispatch to LoL/CS2 model or Glicko-2 heuristic |
| `on_price_update` | ~500 | WS reactive: token-aware pricing, edge check, cooldown |
| `_refresh_live_matches` | ~600 | PandaScore polling for live match data |
| `_inject_glicko2_metadata` | ~650 | Add Glicko-2 features to model input at inference time |
| `_detect_game` | ~700 | Keyword-based game classification (lol/cs2/dota2/valorant) |
| `_classify_market_type` | ~750 | match_winner / map_winner / tournament_winner / props |
| `_compute_confluence_score` | ~800 | Multi-signal scoring (model, Glicko-2, smart money, etc.) |
| `_execute_esports_trade` | ~850 | Bankroll sizing → place_order with correlation_id |
| `_init_glicko2_trackers` | ~950 | Load Glicko-2 ratings from DB |
| `_save_glicko2_ratings` | ~1000 | Persist Glicko-2 to DB |
| `_get_glicko2_prediction` | ~1050 | Raw Glicko-2 expected score |
| `_clean_team_names` | ~1100 | Normalize team names for matching |
| `_match_team_name` | ~1130 | Fuzzy team name matching |

**Critical State**:
- `_market_token_map: Dict[str, Dict[str, str]]` — maps market_id → {yes: token_id, no: token_id}. Populated during `analyze_opportunity()`. Used by `on_price_update()` for token-aware WS pricing.
- `_ws_last_prices: Dict[Tuple[str, str], float]` — keyed by `(market_id, token_id)` for per-token WS pricing.
- `_ws_cooldowns: Dict[str, float]` — per-market cooldown timestamps.
- `_live_matches: Dict[str, Dict]` — PandaScore live match data, refreshed every 15s.
- `_glicko2_trackers: Dict[str, Glicko2Tracker]` — per-game Glicko-2 rating systems.
- `_patch_drift: PatchDriftDetector` — monitors model calibration drift.
- `_market_service: EsportsMarketService` — DB-backed market discovery + CLOB price refresh.

**Prediction Flow**:
```
analyze_opportunity(market_data)
  → _detect_game(question_text) → "lol" / "cs2" / "dota2" / "valorant"
  → _classify_market_type(question_text) → "match_winner" / "map_winner" / etc.
  → _get_model_prediction(game, market_type, teams, match_data)
      → IF lol AND model graduated:
          predict_with_glicko2() → blend XGBoost + Glicko-2 metadata
      → IF cs2 AND model graduated:
          cs2_model.predict()
      → ELSE (dota2, valorant, or ungrouped):
          _get_glicko2_prediction() → heuristic from team ratings
  → check edge = |model_prob - market_price| > ESPORTS_MIN_EDGE (0.08)
  → check confidence = model_prob > ESPORTS_MIN_CONFIDENCE (0.55)
  → _compute_confluence_score() → multi-signal validation
  → _execute_esports_trade() → bankroll sizing → place_order()
```

**WS Reactive Flow** (Session 55 fix):
```
on_price_update(event)
  → extract market_id, token_id, new_price from event
  → lookup _market_token_map[market_id] to identify YES vs NO token
  → convert NO token price to YES-equivalent (1 - no_price)
  → check price_change > ESPORTS_WS_PRICE_CHANGE_PCT (0.01)
  → check cooldown (ESPORTS_WS_COOLDOWN_SECONDS = 10s)
  → check existing position (skip if already holding)
  → get cached prediction, check edge
  → _execute_esports_trade()
```

### 3B. EsportsLiveBot (`bots/esports_live_bot.py`, ~600 lines)

**Purpose**: Event-driven live in-game trading. Detects game events (baron takes, economy breaks, team wipes) and trades on them.

**Architecture**: Producer-consumer with asyncio.Queue (maxsize=200).
```
PandaScore live API → EsportsGameMonitor (producer) → Queue → EsportsLiveBot (consumer)
                                                                    ↓
                                                          EsportsEventDetector
                                                                    ↓
                                                          EsportsLiveTrigger → trade
```

**Key Components**:
- `EsportsGameMonitor` — polls PandaScore running matches, pushes game states to queue
- `EsportsEventDetector` — classifies game states into `EsportsLiveEvent` signals
- `EsportsLiveTrigger` — converts events to trade decisions + execution

**Event Types**:
| Game | Events |
|------|--------|
| LoL | baron_take, elder_dragon, team_wipe, gold_lead, tower_advantage |
| CS2 | economy_break, round_streak, map_clinch |
| General | comeback_threshold, blowout |

**Scan Flow**:
```
scan_and_trade()
  → drain up to 20 game_states from queue
  → for each: event_detector.detect(game_state) → list of EsportsLiveEvent
  → for each event: live_trigger.evaluate(event, markets) → trade or skip
```

### 3C. EsportsSeriesBot (`bots/esports_series_bot.py`, ~500 lines)

**Purpose**: BO3/BO5 series outcome trading. Uses conditional probability math to find edges when market misprices series outcomes based on map score.

**Key Methods**:
- `scan_and_trade()` — refresh active series → analyze each
- `_refresh_series()` — PandaScore live matches filtered to BO3+ only
- `analyze_series(series)` — core logic: get map score, compute series prob, compare to market
- `_simple_series_prob(map_rate, a_wins, b_wins, best_of)` — recursive conditional probability
- `_execute_series_trade()` — bankroll + place_order

**Series Math** (`esports/models/series_model.py`):
- `bo3_match_prob(map_rate, a_wins, b_wins)` — recursive: prob of team A winning BO3 from current score
- `bo5_match_prob(map_rate, a_wins, b_wins)` — same for BO5
- `map_veto_adjusted_prob(team_a_rates, veto_order)` — adjusts per-map win rates for veto sequence
- `series_prob_with_map_veto(...)` — full series prob incorporating map veto
- `momentum_fallacy_edge(...)` — detects market overreaction to comebacks

**WS Reactive**: Similar to EsportsBot. On significant price moves, checks cached series prediction against new price for edge.

---

## 4. DATA PIPELINE

### 4A. PandaScore Client (`esports/data/pandascore_client.py`, 422 lines)

Async HTTP client for PandaScore REST API. Handles:
- Rate limiting with retry/backoff
- Bounded LRU cache for match data
- Match parsing across 8 game slugs: `lol`, `cs-2` (also `csgo`, `cs-go`), `dota2`, `valorant`, `rl`, `codmw`, `fifa`, `r6siege`
- Endpoints: `/matches/running`, `/matches/past`, `/matches/upcoming`, `/teams`, `/players`

**API Key**: `PANDASCORE_API_KEY` env var (required — bots fail fast without it).

### 4B. Data Collector (`esports/data/esports_data_collector.py`, 606 lines)

Orchestrates historical data collection from PandaScore → DB:
- `collect_training_data(game, db)` — fetch past matches, extract features, store in `esports_training_data` table
- Game-specific processing: LoL (gold diff, towers, dragons, barons), CS2 (rounds, economy, plant/defuse)
- `compute_team_strengths(game, db)` — compute Glicko-2 ratings from match history
- Stores Glicko-2 metadata in `game_state_json` column for model feature enrichment

**Session 58 Enhancement**: `_get_glicko2_metadata()` helper stores per-team uncertainty/volatility alongside match data for model training.

### 4C. Riot API Client (`esports/data/riot_api_client.py`)

Optional (requires `RIOT_API_KEY`). Used for real-time LoL match data when available. Falls back to PandaScore if missing.

### 4D. HLTV Scraper (`esports/data/hltv_scraper.py`)

Web scraper for HLTV.org CS2 data. Supplements PandaScore for CS2-specific stats (round-level economy, player ratings).

### 4E. Esports DB (`esports/data/esports_db.py`)

DB helpers for the `esports_training_data` table:
- `insert_training_row(session, game, features, label, match_id, ...)`
- `get_training_data(session, game, min_date=None)`
- `get_recent_matches(session, game, limit=100)`

---

## 5. MODELS & ML

### 5A. Glicko-2 Rating System (`esports/models/glicko2.py`)

Implementation of Glickman 2013 algorithm. Per EsportsBench (2024): 63.1% accuracy on CS:GO.

**Key Classes**:
- `Glicko2Rating` — dataclass with `mu` (rating), `phi` (deviation), `sigma` (volatility)
- `Glicko2Tracker` — processes matches, maintains per-team ratings
  - `process_match(team_a_id, team_b_id, winner)` — update ratings
  - `expected_score(team_a_id, team_b_id)` → float 0-1
  - `strength_diff(team_a_id, team_b_id)` → rating difference

**Constants**: `_MU_DEFAULT=1500`, `_PHI_DEFAULT=350`, `_SIGMA_DEFAULT=0.06`, `_TAU=0.5`

**VPS State**: 629 teams tracked across all games. Ratings persist to DB between restarts.

### 5B. LoL Win Model (`esports/models/lol_win_model.py`)

XGBoost binary classifier for LoL match outcomes.

**Session 58 Enhancement**: Replaced 4 dead features (dragon_soul, herald, inhib, baron — always zero in pre-match) with Glicko-2 metadata:
- `matchup_uncertainty` — RD asymmetry between teams
- `rd_asymmetry` — rating deviation difference
- `team_a_volatility`, `team_b_volatility` — Glicko-2 σ values

**Prediction Blend**: `predict_with_glicko2(features, glicko2_meta)`:
1. XGBoost raw prediction
2. Glicko-2 expected score
3. Weighted blend (70% XGBoost, 30% Glicko-2 when uncertainty high)
4. Heuristic dampening: when `matchup_uncertainty > threshold`, shrink toward 0.5

### 5C. CS2 Economy Model (`esports/models/cs2_economy_model.py`)

XGBoost classifier for CS2 round outcomes based on team economy state.

**Features**: Round diff, economy diff, equipment value, loss bonus state, consecutive losses, side (T/CT), map.

**Status**: Graduated (Brier 0.2473, improved from 0.2507 in Session 55). The old degenerate model was deleted.

### 5D. Series Model (`esports/models/series_model.py`)

Pure math — no ML. Recursive conditional probability for BO3/BO5 outcomes.

**Key Functions**:
- `bo3_match_prob(map_rate, a_wins, b_wins)` — P(team_a wins BO3 | current score)
- `bo5_match_prob(map_rate, a_wins, b_wins)` — P(team_a wins BO5 | current score)
- `map_veto_adjusted_prob(team_a_rates, veto_order)` — per-map win rates
- `momentum_fallacy_edge(margin, market_price, ...)` — detects overreaction to comebacks

### 5E. Patch Drift Detector (`esports/models/patch_drift.py`)

Monitors model calibration over time. Per-game:
- Tracks predictions in sliding window (max 100)
- Computes running Brier score
- Detects game patches → triggers 48h observation mode (no trading for that game)
- Can halt a game entirely if calibration degrades past threshold
- `should_retrain(game)` → True if Brier degrades or recent patch detected

### 5F. Trainer (`esports/models/esports_trainer.py`)

Orchestrates the full training pipeline:
```
train_game(game, db)
  → EsportsDataCollector.collect_training_data() — fetch from PandaScore if needed
  → Load from esports_training_data table
  → 80/20 train/validation split
  → Train model (XGBoost with early stopping)
  → Evaluate: accuracy + Brier score on holdout
  → Graduation gate: accuracy > 55% AND Brier < 0.24
  → Save model if graduated
```

**Graduation Gate**: Dual criteria. Model must achieve BOTH:
- Accuracy > `ESPORTS_MODEL_MIN_ACCURACY` (0.55)
- Brier < `ESPORTS_MODEL_MAX_BRIER` (0.24) — no-skill baseline is 0.25

**Auto-retrain**: Every `ESPORTS_RETRAIN_INTERVAL_HOURS` (24h default) or when `PatchDriftDetector.should_retrain()` fires.

---

## 6. LIVE INFRASTRUCTURE

### 6A. Event Detector (`esports/live/esports_event_detector.py`)

Classifies `EsportsGameState` snapshots into `EsportsLiveEvent` signals.

**Output**: `EsportsLiveEvent` dataclass with:
- `match_id`, `game`, `event_type`, `description`
- `confidence` (0-1), `edge_estimate` (0-0.30), `market_side` ("YES"/"NO")

**Deduplication**: `_triggered: Dict[str, Set[str]]` — prevents re-firing same event for same match.

**Per-Game Thresholds** (from settings):
- LoL: gold_diff > `ESPORTS_LOL_GOLD_DIFF_THRESHOLD` (5000), tower_diff > `ESPORTS_LOL_TOWER_DIFF_THRESHOLD` (3)
- CS2: round_diff > `ESPORTS_CS2_ROUND_DIFF_THRESHOLD` (5), economy_break > `ESPORTS_CS2_ECONOMY_BREAK_THRESHOLD` (10000)

### 6B. Game Monitor (`esports/live/esports_game_monitor.py`)

Producer side of the EsportsLiveBot architecture:
- Polls PandaScore `/matches/running` endpoint
- Parses game-specific state (LoL gold/towers/dragons, CS2 rounds/economy)
- Pushes `EsportsGameState` dicts to asyncio.Queue (maxsize=200)
- Refresh interval: `ESPORTS_PANDASCORE_REFRESH_INTERVAL` (15s)

### 6C. Live Trigger (`esports/live/esports_live_trigger.py`)

Converts `EsportsLiveEvent` → trade decision:
- Matches event to active Polymarket market
- Checks edge against `ESPORTS_MIN_EDGE`
- Uses `EsportsBankrollManager` for sizing
- Executes via `BaseBot.place_order()`

---

## 7. BANKROLL & SIZING

### 7A. EsportsBankrollManager (`esports/kelly/esports_bankroll_manager.py`)

Separate Kelly pool for all 3 esports bots (shared daily cap):

| Setting | Value | Env Var |
|---------|-------|---------|
| Total capital | $5,000 | `ESPORTS_TOTAL_CAPITAL` |
| Max bet per trade | $100 | `ESPORTS_MAX_BET_USD` |
| Max daily exposure | $500 | `ESPORTS_MAX_DAILY_USD` |
| Kelly fraction | 0.25 (quarter Kelly) | `ESPORTS_KELLY_DEFAULT_FRACTION` |
| Min bet | $1 | hardcoded |

**Daily spent calculation**: Sums `_daily_exposure_usd` across all 3 esports bot names (`EsportsBot`, `EsportsLiveBot`, `EsportsSeriesBot`).

**Formula**: `size = kelly_fraction * capital * edge / odds`, capped at `min(max_bet, daily_remaining)`.

### 7B. Integration with BaseBot

`BaseBot.calculate_bot_position_size()` at line ~515 uses `BotBankrollManager` (the general per-bot system). EsportsBot overrides this by using `EsportsBankrollManager` directly in `_execute_esports_trade()`.

---

## 8. MARKET DISCOVERY

### 8A. Market Scanner (`esports/markets/esports_market_scanner.py`, 269 lines)

Keyword-based market discovery (Polymarket category tagging is unreliable):

**Keywords**: `esports`, `league of legends`, `lol`, `lck`, `lpl`, `lec`, `lcs`, `msi`, `worlds`, `counter-strike`, `cs2`, `csgo`, `esl`, `blast`, `pgl`, `iem`, `major`, `dota`, `the international`, `dpc`, `valorant`, `vct`, `champions tour`, etc.

**Market Type Classification**:
- `match_winner` — "who will win", "vs", team names
- `map_winner` — "map 1", "map 2", etc.
- `tournament_winner` — "win the tournament", "champion"
- `total_maps` — "total maps", "over/under"
- `first_blood` — "first blood", "first kill"
- `props` — everything else

### 8B. Market Service (`esports/markets/esports_market_service.py`, 425 lines)

DB-backed market discovery that bypasses the broken Gamma API:
- Queries `markets` table with keyword filters
- Caches results with TTL
- Background CLOB price refresh every 5 minutes
- Price enrichment: up to 200 markets max (was 50, increased Session 59)
- `start_background_refresh()` → asyncio.Task for continuous price updates
- `refresh_market_prices()` → fetches from `https://clob.polymarket.com/markets/{condition_id}`

---

## 9. CONFIGURATION REFERENCE

All esports settings in `config/settings.py` (lines ~898-967):

```python
# Bot enable flags
BOT_ENABLED_ESPORTS = false          # Master switch for EsportsBot
BOT_ENABLED_ESPORTS_LIVE = false     # Master switch for EsportsLiveBot
BOT_ENABLED_ESPORTS_SERIES = false   # Master switch for EsportsSeriesBot

# Scan intervals (seconds)
SCAN_INTERVAL_ESPORTS = 120          # EsportsBot normal scan
SCAN_INTERVAL_ESPORTS_LIVE = 10      # EsportsLiveBot with active games
SCAN_INTERVAL_ESPORTS_SERIES = 30    # EsportsSeriesBot with active series

# Edge / confidence thresholds
ESPORTS_MIN_EDGE = 0.08              # Minimum edge to trade (8%)
ESPORTS_MIN_CONFIDENCE = 0.55        # Minimum model confidence
ESPORTS_SERIES_MIN_EDGE = 0.10       # Higher bar for series trades
ESPORTS_SERIES_REVERSE_SWEEP_FLOOR = 0.05  # Minimum reverse sweep edge

# Bankroll / sizing
ESPORTS_TOTAL_CAPITAL = 5000.0       # Shared Kelly pool
ESPORTS_MAX_BET_USD = 100.0          # Per-trade cap
ESPORTS_MAX_DAILY_USD = 500.0        # Daily exposure cap (all 3 bots combined)
ESPORTS_KELLY_DEFAULT_FRACTION = 0.25  # Quarter Kelly

# Execution
ESPORTS_MAKER_FALLBACK_TIMEOUT_S = 3.0  # Maker order timeout before taker fallback
ESPORTS_OBSERVATION_HOURS = 48       # Post-patch observation period

# Model training
ESPORTS_MODEL_MIN_ACCURACY = 0.55    # Graduation gate: accuracy
ESPORTS_MODEL_MAX_BRIER = 0.24       # Graduation gate: Brier score
ESPORTS_RETRAIN_INTERVAL_HOURS = 24  # Auto-retrain frequency
ESPORTS_MIN_ACCURACY_TO_TRADE = 0.52 # Minimum accuracy to place any trade
ESPORTS_LOL_HEURISTIC_ENABLED = true # Use Glicko-2 heuristic for LoL

# Signal confluence
ESPORTS_CONFLUENCE_MIN = 0.60        # Minimum confluence score
ESPORTS_WHALE_SMART_MONEY_THRESHOLD = 0.60  # Whale signal threshold

# WebSocket reactive
ESPORTS_WS_PRICE_CHANGE_PCT = 0.01   # 1% price move triggers re-evaluation
ESPORTS_WS_COOLDOWN_SECONDS = 10     # Cooldown between WS reactive trades
ESPORTS_LIVE_WS_PRICE_CHANGE_PCT = 0.005  # 0.5% for live bot (more sensitive)
ESPORTS_LIVE_WS_COOLDOWN_SECONDS = 5
ESPORTS_SERIES_WS_PRICE_CHANGE_PCT = 0.01
ESPORTS_SERIES_WS_COOLDOWN_SECONDS = 10

# Latency tracking
ESPORTS_PANDASCORE_REFRESH_INTERVAL = 15  # Live match data refresh (seconds)
ESPORTS_SERIES_REFRESH_INTERVAL = 30

# API keys
PANDASCORE_API_KEY = <required>      # PandaScore API key (bots fail fast without)
RIOT_API_KEY = <optional>            # Riot Games API key (LoL live data)

# Per-game thresholds (LoL)
ESPORTS_LOL_GOLD_DIFF_THRESHOLD = 5000
ESPORTS_LOL_TOWER_DIFF_THRESHOLD = 3

# Per-game thresholds (CS2)
ESPORTS_CS2_ROUND_DIFF_THRESHOLD = 5
ESPORTS_CS2_ECONOMY_BREAK_THRESHOLD = 10000

# Phase 2 (deferred)
ESPORTS_PINNACLE_ENABLED = false     # Cross-market signal (not implemented)
```

---

## 10. ALL PRIOR FIXES (Sessions 53-62)

### Session 53 (2026-03-06) — Data Pipeline Audit
- **EsportsBot sleep fix**: `asyncio.sleep(4.0)` per match → `sleep(0)`. Commit `f85e2d1`.
- CS2 degenerate model deleted. XGBoost deprecated param removed.

### Session 55 (2026-03-06) — WS Fix + Model Fixes
**4 commits**: `8a385b2`, `8472936`, `9f2cceb`, `ae0c4e1`

1. **WS YES/NO Token Confusion** (commit `8a385b2`):
   - **Root cause**: `on_price_update()` stored prices keyed by `market_id` only. YES token (0.84) and NO token (0.06) alternately overwrote → fake 44-63% edges → 50 reactive trades in 2 hours.
   - **Fix**: Added `_market_token_map` mapping market_id → {yes: token_id, no: token_id}. `on_price_update()` now looks up which side the token_id belongs to, uses per-token pricing via `(market_id, token_id)` key.

2. **WS Position Guard + Cooldown** (commit `8a385b2`):
   - Added `has_open_position(market_id)` check before WS reactive trades.
   - Cooldown: 10s per market (configurable via `ESPORTS_WS_COOLDOWN_SECONDS`).

3. **WS Edge Cap** (commit `8a385b2`):
   - Capped WS reactive edge at 30% (0.30). Edges above this are almost certainly data errors.

4. **Label Leakage Fix** (commit `8472936`):
   - LoL model had `result` column in features → perfect accuracy on training, random on holdout.
   - Removed `result` and all post-game features from training data.

5. **CS2 Model Graduation** (commit `8472936`):
   - After label leak fix, CS2 model retrained: Brier 0.2507→0.2473.
   - Old degenerate model deleted.

6. **Prediction Logging** (commit `ae0c4e1`):
   - Added structured logging at prediction time for debugging.

### Session 58 (2026-03-07) — EsportsBot Dedicated Session (All 3 Tiers)
**4 commits**: `956c521`, `2e1a11a`, `41c06d1`, `1483f20`

1. **paper_trading side fix** (commit `956c521`):
   - `paper_trading.py` now stores `YES`/`NO` for entry trades (was `BUY`).
   - `_db_side` logic: entries use `original_side` (YES/NO), exits use "SELL".

2. **LoL Model — Glicko-2 Metadata Blend** (commit `1483f20`):
   - `esports_bot.py`: Live LoL predictions use `predict_with_glicko2()` blend instead of raw `predict()`.
   - `_inject_glicko2_metadata()`: Adds per-team uncertainty/volatility at inference time.
   - `lol_win_model.py`: Replaced 4 dead features (dragon_soul, herald, inhib, baron) with Glicko-2 metadata (matchup_uncertainty, rd_asymmetry, team_a/b_volatility).
   - `esports_data_collector.py`: Stores Glicko-2 metadata in `game_state_json`.

3. **Test Coverage** (commit `41c06d1`):
   - EsportsLiveBot: 43 tests, 98% coverage
   - EsportsSeriesBot: 60 tests, 84% coverage
   - Total test count: 1237 (up from 1134)

### Session 59 (2026-03-07) — WeatherBot Self-Scout (NOT esports)
- Only esports-adjacent: Price enrichment cap raised 50→200 markets in `esports_market_service.py`.

### Session 60 (2026-03-07) — Kalshi Integration (NOT esports)
- No esports changes.

### Session 61 (2026-03-08) — WeatherBot Doom Loop + EnsembleBot Archive
- EnsembleBot archived (0.2% win rate, -$5.6K). **14 active bots now** (was 15).
- No direct esports changes, but system now has 14 bots.

---

## 11. UNCOMMITTED CHANGES

**WARNING**: There are uncommitted changes in 23 files (+805/-174 lines). The esports-relevant ones:

| File | Lines Changed | What |
|------|---------------|------|
| `bots/esports_bot.py` | +315 | Major changes — likely Session 62 work |
| `esports/data/esports_data_collector.py` | +58 | Data collector enhancements |
| `esports/data/pandascore_client.py` | +8 | Minor client fixes |
| `esports/markets/esports_market_scanner.py` | +6 | Scanner tweaks |
| `esports/markets/esports_market_service.py` | +4 | Service tweaks |
| `esports/models/esports_trainer.py` | +11 | Trainer enhancements |
| `esports/models/glicko2.py` | +8 | Glicko-2 fixes |
| `tests/unit/test_esports_bot.py` | +1 | Test addition |

**Non-esports uncommitted** (DO NOT TOUCH):
- `bots/weather_bot.py` (+328), `base_engine/execution/position_manager.py` (+50), `base_engine/exchanges/models.py` (+37), `sports/markets/cross_platform_arb.py` (+25), `base_engine/exchanges/kalshi_adapter.py` (+18), `base_engine/exchanges/arb_scanner.py` (+18), `bots/sports_arb_bot.py` (+15), `tests/unit/test_sports_arb_bot.py` (+13), `base_engine/risk/risk_manager.py` (+9), `base_engine/weather/forecast_client.py` (+9), `sports/markets/kalshi_client.py` (+36), `tests/integration/test_exchange_adapters.py` (+4), `config/settings.py` (+2), `base_engine/data/kalshi_client.py` (+2), `base_engine/data/ingestion_error_capture.txt` (+2)

---

## 12. KNOWN ISSUES & NEXT STEPS

### P0 — Critical
- **No known P0 esports issues**

### P1 — High Priority
1. **Pinnacle cross-market signals** (`ESPORTS_PINNACLE_ENABLED=false`): Phase 2 feature. Would add cross-exchange odds comparison as signal confluence input. Deferred indefinitely.
2. **RIOT_API_KEY not deployed**: Falls back to PandaScore for LoL live data. Could improve LoL live latency if deployed.

### P2 — Medium Priority
3. **Dota 2 / Valorant models**: Currently heuristic-only (Glicko-2). Could train dedicated XGBoost models when sufficient training data accumulates.
4. **Map veto data**: `series_model.py` has `map_veto_adjusted_prob()` but map veto data isn't consistently available from PandaScore. Would improve series predictions.
5. **HLTV scraper robustness**: Depends on HTML structure, could break with site redesign.

### P3 — Low Priority / Nice-to-Have
6. **Player-level features**: Current models are team-level only. Roster changes, player form, substitute detection could improve accuracy.
7. **Tournament context features**: Group stage vs playoffs, elimination games, regional strength differences.
8. **Live model retraining during tournaments**: Use early-tournament data to fine-tune.

### Previously Completed
- ✅ WS token confusion fix (Session 55)
- ✅ Label leakage fix (Session 55)
- ✅ CS2 model graduation (Session 55)
- ✅ Glicko-2 metadata blend for LoL (Session 58)
- ✅ paper_trading side=YES/NO fix (Session 58)
- ✅ EsportsLiveBot + EsportsSeriesBot test coverage (Session 58)
- ✅ Price enrichment cap 50→200 (Session 59)

---

## 13. TESTING

### Test Files & Coverage
| File | Tests | Coverage | Target |
|------|-------|----------|--------|
| `test_esports_bot.py` | ~49 | ~85% | `bots/esports_bot.py` |
| `test_esports_live_bot.py` | ~43 | ~98% | `bots/esports_live_bot.py` |
| `test_esports_series_bot.py` | ~60 | ~84% | `bots/esports_series_bot.py` |
| `test_esports_series_model.py` | ~40 | ~95% | `esports/models/series_model.py` |
| `test_esports_bankroll.py` | ~22 | ~90% | `esports/kelly/esports_bankroll_manager.py` |
| `test_patch_drift.py` | ~34 | ~90% | `esports/models/patch_drift.py` |

### Running Tests
```bash
# All tests (1242 should pass)
pytest

# Esports-only tests
pytest tests/unit/test_esports_bot.py tests/unit/test_esports_live_bot.py tests/unit/test_esports_series_bot.py tests/unit/test_esports_series_model.py tests/unit/test_esports_bankroll.py tests/unit/test_patch_drift.py -v

# Single bot
pytest tests/unit/test_esports_bot.py -v
```

### Test Patterns
- All tests use `unittest.mock` (AsyncMock, MagicMock, patch)
- `make_bot()` helper creates bot with mocked `base_engine` and `settings`
- PandaScore client is always mocked (no real API calls in tests)
- DB operations mocked via `MagicMock()` for `base_engine.db`

---

## 14. CRITICAL PATTERNS & TRAPS

### MUST FOLLOW
1. **BUY/SELL vs YES/NO**: `BaseBot.place_order()` expects `side="YES"` or `side="NO"`. NEVER pass "BUY"/"SELL". The paper_trading engine translates: entries → YES/NO, exits → SELL.

2. **PSEUDO_LABEL_ENABLED=false**: DO NOT enable. Only Location 1 (market resolution) labels are correct.

3. **ENSEMBLE_BLEND=1.0**: Bypasses learning_conf. Do not change.

4. **websockets.exceptions**: Must be imported explicitly (v15 lazy-loads).

5. **Polymarket category tagging unreliable**: Use keyword matching (esports_market_scanner.py), NOT category filter.

6. **CLOB markets have volume=0**: Don't use volume gates for filtering.

7. **BOT_REGISTRY has 14 bots** (MomentumBot DELETED, EnsembleBot ARCHIVED).

### ESPORTS-SPECIFIC TRAPS
8. **_market_token_map must be populated before WS works**: Built during `analyze_opportunity()`. If bot starts with no markets scanned yet, WS events will be silently dropped (no crash, just no trades).

9. **Glicko-2 cold start**: New teams start at mu=1500, phi=350 (very uncertain). First predictions will be near 50%. Need ~10 matches per team for ratings to stabilize.

10. **PandaScore rate limits**: 10 req/s. Client has retry/backoff but aggressive scanning can still hit limits.

11. **Game detection is keyword-based**: Adding a new game requires updating `_detect_game()` in esports_bot.py AND `esports_market_scanner.py`.

12. **Patch drift 48h observation**: After a game patch, the bot enters observation mode for 48 hours — no trades for that game. This is intentional.

13. **Series model BO1/BO2 skip**: EsportsSeriesBot ignores BO1 and BO2 matches (not enough maps for series math to add value).

14. **Maker fallback**: EsportsBot tries maker orders first, falls back to taker after `ESPORTS_MAKER_FALLBACK_TIMEOUT_S` (3s). This is for better fills.

### DATABASE SCHEMA
- **`esports_training_data`** table: `id, game, features (JSONB), label (int 0/1), match_id, team_a, team_b, game_state_json (JSONB), created_at`
- **`paper_trades`** table: `id, bot_name, market_id, token_id, side (YES/NO/SELL), size, price, confidence, realized_pnl, correlation_id, created_at`
- **`positions`** table: `id, bot_id, market_id, token_id, side, size, entry_price, current_price, created_at, updated_at`
- **`markets`** table: `id, question, yes_price, no_price, volume, active, condition_id, updated_at`

---

## 15. DEPLOY PROTOCOL

### VPS Details
- **Host**: Ubuntu-3 at `34.251.224.21` (16GB/4vCPU)
- **SSH key**: `C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem`
- **User**: `ubuntu`
- **Service**: `polymarket-ai` (systemd)
- **Install dir**: `/opt/polymarket-ai-v2/`
- **DB password**: `polymarket_s46`
- **Redis password**: `78psiRhepTgrmWSoy3cgNEIr`

### Deploy Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Deploy single file
scp -i "$KEY" -o StrictHostKeyChecking=no "bots/esports_bot.py" "$VPS:/tmp/"
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" 'sudo cp /tmp/esports_bot.py /opt/polymarket-ai-v2/bots/esports_bot.py && sudo systemctl restart polymarket-ai'

# Deploy multiple files (esports package)
scp -i "$KEY" -o StrictHostKeyChecking=no -r esports/ "$VPS:/tmp/esports/"
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" 'sudo cp -r /tmp/esports/ /opt/polymarket-ai-v2/ && sudo systemctl restart polymarket-ai'

# Verify post-deploy
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" 'journalctl -u polymarket-ai -f | grep -E "EsportsBot|EsportsLiveBot|EsportsSeriesBot"'
```

### Post-Deploy Verification Checklist
```bash
# All 3 esports bots should show "active (running)"
journalctl -u polymarket-ai --since "5 min ago" | grep -E "EsportsBot|EsportsLiveBot|EsportsSeriesBot"

# Check for errors
journalctl -u polymarket-ai --since "5 min ago" | grep -i "error" | grep -i "esports"

# Check scan output (should see market counts)
journalctl -u polymarket-ai -f | grep "esports.*scan"

# Check Glicko-2 init
journalctl -u polymarket-ai --since "5 min ago" | grep "glicko"
```

---

## 16. CLAUDE.md RULES SUMMARY

**These rules are NON-NEGOTIABLE. The new agent MUST follow them:**

1. **One fix per commit.** No "while I'm in here" refactors.
2. **Preserve every function signature** unless the signature IS the bug.
3. **Preserve every external interface** (API paths, DB columns, config keys, message formats).
4. **No silent behavior changes.** State: "This changes behavior from X to Y."
5. **Never delete code you don't understand.**
6. **No new dependencies without justification.**
7. **No structural refactors during bug fixes.**
8. **Read the entire file** before modifying it.
9. **Grep for dependents** before changing any module.
10. **Git snapshot** before any edit.
11. **Cross-bot verification** after touching shared modules (all 14 bots affected if touching base_bot.py).
12. **Mandatory change log** after every fix.

### Config Change Tiers
- **Tier 1** (thresholds): State what changed, why, expected impact.
- **Tier 2** (trade-universe gating): State what trades are now blocked/allowed. Rollback command.
- **Tier 3** (code changes): Full blast-radius protocol.

---

## APPENDIX A: IMPORT GRAPH

Files that import from the `esports/` package:
```
bots/esports_bot.py
bots/esports_live_bot.py
bots/esports_series_bot.py
esports/data/esports_data_collector.py
esports/data/esports_db.py
esports/kelly/esports_bankroll_manager.py
esports/live/esports_event_detector.py
esports/live/esports_game_monitor.py
esports/live/esports_live_trigger.py
esports/models/esports_trainer.py
esports/models/series_model.py
tests/unit/test_esports_bankroll.py
tests/unit/test_esports_live_bot.py
tests/unit/test_esports_series_model.py
tests/unit/test_patch_drift.py
```

## APPENDIX B: ENVIRONMENT VARIABLES (ESPORTS)

```env
# Required
PANDASCORE_API_KEY=<your-key>

# Optional
RIOT_API_KEY=<your-key>

# Bot enables (all default false)
BOT_ENABLED_ESPORTS=true
BOT_ENABLED_ESPORTS_LIVE=true
BOT_ENABLED_ESPORTS_SERIES=true

# All other ESPORTS_* settings have sane defaults (see Section 9)
```

## APPENDIX C: QUICK START FOR NEW AGENT

1. Read this document completely.
2. Read `CLAUDE.md` for system-wide rules.
3. Run `pytest tests/unit/test_esports_bot.py tests/unit/test_esports_live_bot.py tests/unit/test_esports_series_bot.py -v` to verify baseline.
4. Check `git diff --stat` for uncommitted changes that may need attention.
5. Check `git log --oneline -10` for recent commits.
6. Before ANY code change: state the bug, list files, grep dependents, git snapshot, read entire file.
7. After ANY code change: run `pytest`, list affected bots, verify each.

---

*Generated 2026-03-08. Session 63 handoff for EsportsBot ecosystem continuation.*
