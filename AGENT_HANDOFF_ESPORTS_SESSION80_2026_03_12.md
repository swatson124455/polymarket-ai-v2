# AGENT HANDOFF — EsportsBot Session 80 (2026-03-12)
# CARBON COPY: Complete system state for seamless agent continuation

---

## WHAT THIS IS

A **live automated Polymarket trading system** with 15 bots (5 active, 9 disabled, 1 deleted). This handoff is **EsportsBot-scoped** — covering the 3 esports bots (EsportsBot, EsportsLiveBot, EsportsSeriesBot) that trade esports prediction markets using Glicko-2 ratings, XGBoost, and 4 game-specific ML models. Runs 24/7 on a VPS in Dublin. Currently **paper trading** (`SIMULATION_MODE=true`).

**This document is a COMPLETE carbon copy** — everything needed to continue development with zero information loss.

---

## SESSION SCOPE LOCK (IMMUTABLE)

**ALL esports bot sessions are hardcoded to esports-only scope.**
- Only touch: `bots/esports_bot.py`, `bots/esports_live_bot.py`, `bots/esports_series_bot.py`, `esports/**`, tests involving esports
- Shared modules (`base_engine`, `database`, `config`) ONLY if required for an esports bug fix
- NEVER commit changes to `mirror_bot.py`, `weather_bot.py`, `elite_detector.py`, or other non-esports files
- If prior sessions left uncommitted non-esports changes, leave them alone

---

## SYSTEM ARCHITECTURE

### Infrastructure
- **Repo**: `C:\lockes-picks\polymarket-ai-v2` (Windows dev) → deploys to VPS
- **VPS**: Ubuntu-3, `34.251.224.21`, 16GB/4vCPU, eu-west-1 (Dublin)
- **DB**: PostgreSQL on VPS localhost, user=`polymarket`, db=`polymarket`
- **Redis**: localhost on VPS (caching/cooldowns)
- **Service**: `sudo systemctl restart polymarket-ai`
- **Branch**: `master` (PR target: `main`)
- **Tests**: `pytest` — 1423+ tests, must all pass before deploy

### Deploy
```bash
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
# Rollback:
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```
- Atomic symlink swap: `/opt/polymarket-ai-v2` → latest in `/opt/pa2-releases/`
- Shared state persists: `/opt/pa2-shared/{data,saved_models,venv,.env}`
- **Systemd reads**: `/opt/pa2-shared/.env` (persists across deploys)
- **Deploy copies**: `deploy/env.vps` → release dir `.env` (overwritten each deploy)
- **To change env vars**: Update BOTH `deploy/env.vps` AND `/opt/pa2-shared/.env`

### 5 Active Bots
| Bot | Capital | Kelly | Max Bet | Max Daily | Status |
|-----|---------|-------|---------|-----------|--------|
| WeatherBot | $5,000 | 0.25 | $500 | $2,000 | +$461 P&L (140 resolved) |
| MirrorBot | $3,000 | 0.30 | $250 | $10,000 | +$230 P&L (14 resolved). Blocked by $20k exposure cap |
| EsportsBot | $5,000 | 0.25 | $100 | $500 | Active, trading |
| EsportsLiveBot | $1,000 | 0.25 | $100 | $500 | Active |
| EsportsSeriesBot | $1,000 | 0.25 | $100 | $500 | Active |

9 bots disabled. MomentumBot DELETED. EnsembleBot ARCHIVED (-$5.6k).

---

## THE 3 ESPORTS BOTS — ARCHITECTURE

### EsportsBot (`bots/esports_bot.py`, ~2170 lines)
- **Strategy**: Pre-match + live analysis. Glicko-2 ratings + XGBoost cross-game model + 4 game-specific ML models
- **Scan interval**: 120s pre-game, 10s during live matches
- **Games**: LoL, CS2, Dota2, Valorant, CoD, R6, SC2, Rocket League (8 games)
- **Capital**: $5k, Kelly 0.25, max bet $100, max daily $500

### EsportsLiveBot (`bots/esports_live_bot.py`, ~298 lines)
- **Strategy**: Real-time in-game event detection via EsportsGameMonitor queue
- **Scan interval**: 10s
- **Capital**: $1k, Kelly 0.25, max bet $100, max daily $500

### EsportsSeriesBot (`bots/esports_series_bot.py`, ~792 lines)
- **Strategy**: BO3/BO5 conditional probability. Exploits momentum fallacy + map veto ignorance
- **Scan interval**: 30s
- **Capital**: $1k, Kelly 0.25, max bet $100, max daily $500

**Architecture recommendation**: Keep 3 separate bots. Different strategies, scan intervals, and failure isolation. Merging would create a 3000+ line monolith with coupled failure modes.

---

## ESPORTSBOT SIGNAL GENERATION PIPELINE

### Full Pipeline (with waterfall filter stages)
```
Market → _detect_game() → [no_game if unknown]
       → price check 0.03-0.97 → [no_price]
       → token extraction → [no_token]
       → game halted check → [halted]
       → per-game exposure cap → [exposure_cap]
       → 48h observation window → [observation]
       → _classify_market_type → [no_prediction if props/first_blood/tournament_winner]
       → _get_model_prediction → [no_prediction if Glicko-2 can't match teams]
       → edge check (>= min_edge) → [low_edge]
       → edge cap check (<= max_edge) → [edge_cap]
       → confidence check (>= min_confidence) → [low_confidence]
       → confluence check (>= min_confluence) → [low_confluence]
       → [passed] → trade execution
```

### 1. Market Discovery (`esports/markets/esports_market_service.py`, 445 lines)
- Polymarket Gamma API returns ZERO esports markets — must use CLOB API + direct DB query
- `get_tradeable_esports_markets()`: Query DB for active unresolved markets with `yes_price BETWEEN 0.03 AND 0.97`
- Keyword-gates via `_is_real_esports()`: substring matching (long keywords) + word-boundary regex (short acronyms)
- Background CLOB price refresh every 5 min via `start_background_refresh()`
- Cache TTL: 120s
- Game keywords dict (`_ESPORTS_GAME_KEYWORDS`): lol, cs2, dota2, valorant, cod, r6, sc2, rl
- Boundary keywords dict (`_BOUNDARY_KEYWORDS`): short acronyms like `\blec\b`, `\bpgl\b` requiring word boundaries
- Double-gate: rejects soccer/football false positives

### 2. Game Detection (`_detect_game()`)
- In BOTH `esports_bot.py` (class-level `_WB_LOL`, `_WB_CS2`, etc.) and `esports_market_service.py` (`_BOUNDARY_KEYWORDS`)
- Long keywords: safe substring match (e.g., "league of legends", "counter-strike")
- Short acronyms: compiled `\b...\b` regex to prevent "lec" matching "election"
- **Session 78 fix**: Changed from bare substring to word-boundary regex. Dropped 171→34 markets scanned.

### 3. Market Classification (`_classify_market_type()`)
- `match_winner` (default) — requires two teams, Glicko-2 prediction
- `map_winner` — "game 1/2/3", "map" keywords
- `tournament_winner` — "tournament", "championship" (single-team, NO Glicko-2)
- `total_maps` — over/under
- `first_blood` — first kill
- `props` — "mvp", "kills", "assists", "be said", "signs for"
- **Skipped types** (early return, no prediction): props, first_blood, tournament_winner

### 4. Glicko-2 Prediction (`_get_glicko2_prediction()`, ~line 1934)
- Extracts team names from "A vs B" or "Will A beat B?" question patterns
- Looks up in `_team_name_to_id` dict (populated from `glicko2_ratings` table)
- **On-demand backfill** (Session 78): If team not found, calls `_backfill_unknown_team()` → PandaScore search + match history → Glicko-2 update → persist
- Bayesian prior blending (E5) based on rating deviation (phi):
  - phi >= 350 (unrated): 80% prior (0.50), 20% Glicko-2
  - phi 200-350 (sparse): 50/50
  - phi < 200 (established): 20/80
  - phi < 100 (mature): 0/100

### 5. Cross-Game XGB Model (`_predict_cross_game()`, ~line 1940)
- 9 features: team ratings, RDs, volatilities, recent form
- Loaded from `/opt/pa2-shared/saved_models/cross_game_xgb.json`
- Retrained every 24h via `LearningScheduler.esports_trainer`

### 6. Game-Specific Models
- **LoL**: `LoLModel` (gold diff, tower diff, dragon) — `esports/models/lol_win_model.py`
- **CS2**: `CS2Model` (round diff, economy) — `esports/models/cs2_economy_model.py`
- **Dota2**: `Dota2Model` + OpenDota form adjustment (±3%) — `esports/models/dota2_model.py`
- **Valorant**: Basic Glicko-2 only currently — `esports/models/valorant_model.py`

### 7. Confluence Scoring (`_compute_confluence_score()`)
- Weighted: edge 55%, freshness 30%, agreement 15%
- Freshness decay: 30s for live, 120s default
- Min confluence: 0.60

---

## KEY CODE LOCATIONS

| What | File | Location |
|------|------|----------|
| Bot class | `bots/esports_bot.py` | `class EsportsBot(BaseBot)` line 28 |
| Instance vars init | `bots/esports_bot.py` | Lines 100-116 |
| `start()` | `bots/esports_bot.py` | Lines 124-268 |
| `scan_and_trade()` | `bots/esports_bot.py` | Main scan loop |
| `analyze_opportunity()` | `bots/esports_bot.py` | Full pipeline with waterfall counters |
| `_detect_game()` | `bots/esports_bot.py` | ~line 1390 |
| `_classify_market_type()` | `bots/esports_bot.py` | ~line 1435 |
| `_get_model_prediction()` | `bots/esports_bot.py` | Lines 940-1035, dispatches to models |
| `_get_glicko2_prediction()` | `bots/esports_bot.py` | ~line 1934 (NOW ASYNC) |
| `_backfill_unknown_team()` | `bots/esports_bot.py` | Lines 1852-1932 |
| `_predict_cross_game()` | `bots/esports_bot.py` | ~line 1940 |
| `_build_glicko2_game_state()` | `bots/esports_bot.py` | ~line 1966 |
| `_compute_confluence_score()` | `bots/esports_bot.py` | ~line 1450 |
| `_execute_esports_trade()` | `bots/esports_bot.py` | Position sizing + place_order() |
| `_init_glicko2_trackers()` | `bots/esports_bot.py` | Lines 1566-1662 |
| `_save_glicko2_ratings()` | `bots/esports_bot.py` | Lines 1678-1714 |
| `_match_team_name()` | `bots/esports_bot.py` | Lines 2150-2170 (fuzzy matching) |
| `_check_monitoring_thresholds()` | `bots/esports_bot.py` | Every 10 min, Brier/accuracy checks |
| `_backfill_esports_outcomes()` | `bots/esports_bot.py` | Every 10 scans, resolves predictions |
| Market service | `esports/markets/esports_market_service.py` | 445 lines |
| `_is_real_esports()` | `esports/markets/esports_market_service.py` | Double-gate filter |
| `_BOUNDARY_KEYWORDS` | `esports/markets/esports_market_service.py` | Word-boundary regex |
| PandaScore client | `esports/data/pandascore_client.py` | 531 lines |
| `search_team_by_name()` | `esports/data/pandascore_client.py` | Line 336 (NEW Session 78) |
| `get_team_matches()` | `esports/data/pandascore_client.py` | Line 353 (NEW Session 78) |
| Shared rate counter | `esports/data/pandascore_client.py` | Lines 108-114 (class-level) |
| Esports DB | `esports/data/esports_db.py` | 804 lines |
| `compute_pnl_summary()` | `esports/data/esports_db.py` | Line 573 (side-aware CTE join) |
| `log_prediction()` | `esports/data/esports_db.py` | Line 188 |
| Glicko-2 engine | `esports/models/glicko2.py` | 279 lines |
| `expected_score()` | `esports/models/glicko2.py` | Line 60 |
| `update_rating()` | `esports/models/glicko2.py` | Line 72 |
| Resolution backfill | `base_engine/data/database.py` | Lines 3062-3098 |
| P&L audit SQL | `deploy/esports_pnl_audit.sql` | 166 lines, 8 diagnostic sections |

---

## CRITICAL INSTANCE VARIABLES (esports_bot.py)

| Variable | Type | Purpose | Persistence |
|----------|------|---------|-------------|
| `_glicko2_trackers` | `Dict[game, Glicko2Tracker]` | Per-game rating systems | DB `glicko2_ratings` table |
| `_team_name_to_id` | `Dict[lowercase_name, key]` | Team name lookup | From DB on init, updated on backfill |
| `_backfill_attempted` | `Set[str]` | "game:name" keys already queried | Session-only (reset on restart) |
| `_live_matches` | `Dict[match_id, data]` | Currently live matches | Updated each scan |
| `_prediction_cache` | `Dict[market_id, {prob, ts, game, ml_raw, glicko2_est}]` | Cache for WS price updates | Session-only |
| `_market_token_map` | `Dict[market_id, {yes: token, no: token}]` | Token→YES/NO mapping | Populated during scan |
| `_ws_pending_trades` | `set` | Race-condition guard for WS trades | Bounded lifetime (finally block) |
| `_game_exposure` | `Dict[game, usd]` | Per-game exposure tracking | `daily_counters` write-through |
| `_prediction_log_cache` | `Dict[market_id, (prob, ts)]` | Dedup prediction logs (10 min) | Session-only |
| `_monitoring_halted_games` | `set` | Games halted by Brier alerts | Session-only |
| `_collection_attempted` | `Dict[game, count]` | Data collection retries (max 3) | Session-only |
| `_latency_samples` | `List[float]` | WS latency tracking | Bounded to 100 |
| `_wf` | `Dict[str, int]` | Waterfall filter counters (per scan) | Reset each scan |
| `_min_edge` | `float` | 0.05 (easy mode) | Config |
| `_min_confidence` | `float` | 0.52 | Config |
| `_max_edge` | `float` | 0.20 (overridden to 0.25 via env) | Config |
| `_models_graduated` | `bool` | False (toggles at 55% accuracy + brier<=0.24) | Runtime |

---

## PANDASCORE CLIENT (`esports/data/pandascore_client.py`, 531 lines)

### Rate Limiting (CRITICAL)
- 1000 req/hr free tier
- **Class-level shared counter** across all 3 esports bots (lines 108-114)
- Hard circuit breaker at 950/hr
- Milestones logged at 500, 750, 900, 950, 990

### Game Slugs
```python
"lol": "lol", "cs2": "csgo", "dota2": "dota2", "valorant": "valorant",
"cod": "codmw", "r6": "r6siege", "sc2": "starcraft-2", "rl": "rl"
```

### Key Methods
| Method | Line | Cost | Purpose |
|--------|------|------|---------|
| `get_live_matches(game)` | 155 | 1 req | Currently live matches, cached 30s |
| `get_upcoming_matches(game, hours)` | 178 | 1 req | Upcoming within N hours |
| `get_match_details(match_id)` | 203 | 1 req | Full match with games/maps |
| `get_team_stats(team_id, game)` | 215 | 1 req | Team statistics |
| `get_match_games(match_id)` | 230 | 1 req | Individual maps in match |
| `get_past_matches(game, days, per_page)` | 242 | N req | Historical, auto-paginated |
| `get_match_games_detail(match_id)` | 307 | 1 req | Detailed with timelines |
| `get_tournaments(game)` | 321 | 1 req | Running tournaments |
| `search_team_by_name(name)` | 336 | 1 req | **NEW**: Search team by name |
| `get_team_matches(team_id, game)` | 353 | 1 req | **NEW**: Team's recent matches |

### EsportsMatch Dataclass
```python
match_id, game, tournament, team_a, team_b, team_a_id, team_b_id,
score_a, score_b, best_of, status, scheduled_at, stream_url, league, raw
```

### Internal
- `_get()`: HTTP GET with 3-attempt retry, exponential backoff (1s→2s→4s)
- `_parse_match()`: Parse PandaScore JSON → EsportsMatch
- `_BoundedCache`: TTL cache with max-size eviction (500 entries, 30s TTL)

---

## ESPORTS DB (`esports/data/esports_db.py`, 804 lines)

### Key Functions
| Function | Line | Purpose |
|----------|------|---------|
| `upsert_esports_team(db, team_data)` | 49 | INSERT/UPDATE esports_teams |
| `upsert_esports_match(db, match_data)` | 78 | INSERT/UPDATE esports_matches |
| `log_prediction(db, ...)` | 188 | Log to esports_prediction_log |
| `get_calibration(db, game)` | 155 | Fetch bet_count, correct_count, brier, kelly |
| `resolve_predictions(db, market_id, outcome)` | 231 | Backfill actual_outcome |
| `get_rolling_accuracy(db, game, bot, n)` | 261 | Rolling accuracy last N predictions |
| `get_phase_accuracy(db, game, phase)` | 315 | Brier by tournament phase |
| `update_prediction_closing_price(db, ...)` | 366 | Record closing price (CLV tracking) |
| `compute_clv_stats(db, game, days)` | 403 | Closing Line Value analysis |
| `analyze_edge_decay(db, game, days)` | 468 | Edge decay with time-to-resolution |
| `compute_pnl_summary(db)` | 573 | **FIXED Session 78**: Side-aware CTE join |
| `get_team_map_rates(db, team, game)` | 644 | Per-map win rates (cached 60s) |
| `update_calibration(db, game, ...)` | 698 | Upsert esports_calibration |
| `backfill_pinnacle_closing_lines(db, client)` | 737 | Fetch Pinnacle lines |

### P&L Summary SQL (FIXED — side-aware join)
```sql
WITH game_map AS (
    SELECT DISTINCT ON (market_id, side) market_id, side, game, edge
    FROM esports_prediction_log
    ORDER BY market_id, side, created_at DESC
)
SELECT COALESCE(gm.game, 'unknown') as game, ...
FROM paper_trades pt
LEFT JOIN game_map gm ON pt.market_id = gm.market_id
    AND UPPER(gm.side) = UPPER(pt.side)
WHERE pt.bot_name LIKE 'Esports%'
  AND pt.realized_pnl IS NOT NULL AND pt.side IN ('YES', 'NO')
GROUP BY COALESCE(gm.game, 'unknown')
```

---

## GLICKO-2 SYSTEM (`esports/models/glicko2.py`, 279 lines)

### Constants
```python
_MU_DEFAULT = 1500.0     # Default rating
_PHI_DEFAULT = 350.0     # Default RD (high uncertainty)
_SIGMA_DEFAULT = 0.06    # Default volatility
_TAU = 0.5               # System constant
_SCALE = 173.7178        # Scaling factor
```

### Key Functions
- `expected_score(rating_a, rating_b)` → P(a beats b) accounting for uncertainty
- `update_rating(player, opponents, outcomes)` → new Glicko2Rating
- 63.1% accuracy on CS:GO (EsportsBench 2024) — outperforms Elo, TrueSkill

### On-Demand Team Backfill (Session 78, commit `6d42d8c`)
When `_match_team_name()` returns None:
1. Check `_backfill_attempted` set → skip if already tried
2. `search_team_by_name(name)` → PandaScore team object (1 API req)
3. `get_team_matches(team_id, game, per_page=20)` → recent matches (1 API req)
4. Process each match through `tracker.process_match()`
5. Add ALL teams (target + opponents) to `_team_name_to_id`
6. `_save_glicko2_ratings(db)` → persist to DB
7. Log `esportsbot_team_backfilled`

---

## ESPORTS MARKET SERVICE (`esports/markets/esports_market_service.py`, 445 lines)

### Why It Exists
Polymarket Gamma API returns ZERO esports markets. All ~1600 esports markets only accessible via CLOB API + direct DB query.

### Key Methods
- `get_tradeable_esports_markets(db, game, min_volume)` (line 124): Query + keyword filter
- `refresh_market_prices(market_ids)` (line 237): CLOB API price refresh (10 req/s max)
- `start_background_refresh()` (line 400): Every 5 min
- `_detect_game(question)` (line 96): Extract game from question text
- `_is_real_esports(question)` (line 82): Double-gate soccer/football filter

---

## CONFIGURATION (VPS live values)

### Esports Config
```
ESPORTS_MIN_EDGE=0.05              # Was 0.08, lowered Session 78
ESPORTS_MIN_CONFIDENCE=0.52        # Was 0.55, lowered Session 76
ESPORTS_MAX_EDGE=0.25              # Was 0.20, raised Session 78 (env override only)
ESPORTS_TOTAL_CAPITAL=5000.0
ESPORTS_MAX_BET_USD=100.0
ESPORTS_MAX_DAILY_USD=500.0
ESPORTS_KELLY_DEFAULT_FRACTION=0.25
ESPORTS_OBSERVATION_HOURS=48
ESPORTS_CONFLUENCE_MIN=0.60
ESPORTS_FRESHNESS_DECAY_SECONDS=30.0
ESPORTS_FRESHNESS_DECAY_PREGAME_SECONDS=600.0
ESPORTS_MAX_GAME_EXPOSURE=300.0
ESPORTS_MAX_TOURNAMENT_EXPOSURE=200.0
ESPORTS_MAX_TEAM_EXPOSURE=150.0
ESPORTS_SERIES_MIN_EDGE=0.10
ESPORTS_SERIES_REVERSE_SWEEP_FLOOR=0.05
ESPORTS_SERIES_HEDGE_ENABLED=true
ESPORTS_PINNACLE_ENABLED=false
ESPORTS_LOL_HEURISTIC_ENABLED=true
```

### Model Training Config
```
ESPORTS_MODEL_MIN_ACCURACY=0.55    # Graduation threshold
ESPORTS_MODEL_MAX_BRIER=0.24       # Graduation threshold
ESPORTS_RETRAIN_INTERVAL_HOURS=24
ESPORTS_MIN_ACCURACY_TO_TRADE=0.52
ESPORTS_VALIDATION_SPLIT=0.2
ESPORTS_MIN_LOL_SAMPLES=50
ESPORTS_MIN_CS2_SAMPLES=100
ESPORTS_MIN_CS2_UNIQUE_MATCHES=15
ESPORTS_EARLY_STOPPING_ROUNDS=20
ESPORTS_TOURNAMENT_PHASE_MIN_SAMPLES=20
```

### WebSocket Config
```
ESPORTS_WS_PRICE_CHANGE_PCT=0.01
ESPORTS_WS_COOLDOWN_SECONDS=10
ESPORTS_LIVE_WS_PRICE_CHANGE_PCT=0.005
ESPORTS_LIVE_WS_COOLDOWN_SECONDS=5
```

### Live Trading Config
```
ESPORTS_LIVE_COOLDOWN_SECONDS=60.0
ESPORTS_LIVE_MAX_PER_MATCH=5
ESPORTS_LIVE_MAX_PER_MAP=2
ESPORTS_LIVE_MAX_EVENTS_PER_SCAN=50
ESPORTS_LIVE_EVENT_MAX_AGE_SECONDS=60.0
```

### System-Wide
```
SIMULATION_MODE=true
PAPER_TRADING=true
LIVE_TRADING=false
BOT_ENABLED_ESPORTS=false  (disabled in env.vps, but active on VPS)
BOT_ENABLED_ESPORTS_LIVE=false
BOT_ENABLED_ESPORTS_SERIES=false
```

### IMPORTANT Config Trap
- `ESPORTS_MIN_EDGE` default in `settings.py` is 0.08 — VPS env overrides to 0.05
- `ESPORTS_MAX_EDGE` is NOT in `settings.py` — read via `getattr(settings, "ESPORTS_MAX_EDGE", 0.20)`, must be env var

---

## CURRENT WATERFALL STATE (2026-03-12)

```
markets=34, markets_by_game={'lol':10, 'cs2':11, 'valorant':10, 'cod':3}
waterfall={'observation':10, 'no_prediction':13, 'low_edge':4, 'edge_cap':1, 'low_confidence':5}
```
- `observation=10`: Markets in 48h observation window (normal)
- `no_prediction=13`: 11 props/tournament skips + 2 genuine Glicko-2 misses (Contra not in DB, BIG "qualify" not a match)
- `low_edge=4`: Edge below 0.05
- `edge_cap=1`: Edge above 0.25 max
- `low_confidence=5`: Confidence below 0.52 (genuinely uncertain)

---

## P&L STATUS (2026-03-12)

| Bot | Resolved Trades | P&L | Win Rate |
|-----|----------------|-----|----------|
| WeatherBot | 140 | +$461.74 | 44% (62W/78L, avg win $11.38, avg loss $3.13) |
| MirrorBot | 14 | +$230.59 | 50% (7W/7L) |
| EsportsBot | 31 resolved, ~47 in-flight | -$146.38 (old SELL trades) | Pending |

### Esports P&L Detail
- 31 resolved trades: All old SELL trades from pre-YES/NO era (-$146.38)
- ~47 YES/NO trades in-flight (placed 2026-03-10 to 2026-03-12, pending resolution)
- 4 open positions: Dota2 x2, Valorant x1, CS2 x1

---

## RESOLUTION & BACKFILL PIPELINE

### `backfill_paper_trades_resolution()` (`base_engine/data/database.py:3062-3098`)
- Updates `resolution`, `resolved_at`, `realized_pnl` from resolved markets
- P&L formula includes 1.5% taker fee (TAKER_FEE_BPS/10000)
- **CRITICAL**: `AND LOWER(pt.side) != 'sell'` — excludes SELL trades (paper engine computes at exit)
- Joins: `pt.market_id = CAST(m.id AS TEXT) OR pt.market_id = m.condition_id`

### `_backfill_esports_outcomes()` (esports_bot.py)
- Runs every 10 scans
- Resolves esports_prediction_log from paper_trades
- **Session 78 fix**: NULL guard prevents `int(None)` TypeError:
  ```python
  for r in resolved:
      if r.won is None:
          continue
      outcome = int(r.won) if r.side == "YES" else (1 - int(r.won))
  ```

### VPS Diagnostic Script (`deploy/esports_pnl_audit.sql`)
8 diagnostic sections + commented cleanup operations. Run with:
```bash
psql -U polymarket -d polymarket -f esports_pnl_audit.sql
```

---

## TESTS

### Test Files (5 total)
| File | Tests | Lines |
|------|-------|-------|
| `tests/unit/test_esports_bot.py` | 54 | 557 |
| `tests/unit/test_esports_live_bot.py` | — | — |
| `tests/unit/test_esports_series_bot.py` | — | — |
| `tests/unit/test_esports_series_model.py` | — | — |
| `tests/unit/test_esports_bankroll.py` | — | — |

### test_esports_bot.py Key Test Groups
- Initialization (4 tests): API key validation, settings storage
- Scan Interval (2 tests): Default vs live scan intervals
- Game Detection (15 tests): All 8 games + case sensitivity + boundary matching
- Market Type (9 tests): match_winner, map_winner, tournament, props, etc.
- analyze_opportunity (17 tests): Full pipeline coverage
- scan_and_trade (5 tests): Market iteration, trade execution, error handling
- _ws_pending_trades (3 tests): Bounded lifetime verification

### Important Mock Change (Session 78)
`_get_glicko2_prediction()` changed from **sync to async**. All test mocks using `MagicMock(return_value=X)` must use `AsyncMock(return_value=X)`. **7 test mocks** in test_esports_bot.py (lines 258, 268, 307, 331, 351, 380, 399).

---

## STATE PERSISTENCE (All Gaps Closed)

| State | Mechanism | Bot |
|-------|-----------|-----|
| `_daily_exposure_usd` | `daily_counters` 60s flush + SIGTERM + startup restore | All |
| `_game_exposure` | `daily_counters` write-through + `_restore_exposure_from_db()` | EsportsBot |
| Open positions | `order_gateway.seed_positions_from_db()` | All |
| XGB model | `/opt/pa2-shared/saved_models/cross_game_xgb.json` | EsportsBot |
| Glicko-2 ratings | `glicko2_ratings` table | EsportsBot |

### daily_counters Write Patterns (DO NOT MIX)
- **ADDITIVE**: EsportsBot `game_{game}` keys — `counter_value += amount` via `increment_counter()`
- **ABSOLUTE-SET**: OrderGateway `daily_exposure_usd` — `counter_value = total` via `_flush_daily_exposure()`

---

## GIT STATUS (as of session start)

### Current Branch: `master` (PR target: `main`)

### Uncommitted Changes (deployed to VPS but NOT committed)
```
M  bots/esports_bot.py                          (+94 lines: word-boundary, waterfall, backfill)
M  esports/markets/esports_market_service.py     (+45 lines: boundary keywords, game_matches)
M  deploy/env.vps                                (edge thresholds)
M  base_engine/data/ingestion_error_capture.txt  (test artifact, ignorable)
```

### Recent Commits (this session + prior)
```
88e34ee fix(db): UPSERT paper_trades on re-entry instead of failing on UNIQUE
dc79450 chore: update ingestion error capture with latest traceback
e631599 config(esports): lower edge floor 0.08→0.05, confidence 0.55→0.52, add max_edge 0.25
0da8eb0 fix(esports): boundary-match short game acronyms to prevent false positives
0337aeb feat(mirror+elite): tighten elite quality gates (NON-ESPORTS, pre-scope-lock)
fc54a85 fix(paper): UNIQUE constraint + duplicate guard on paper_trades
fac5223 fix(pnl): esports P&L audit fixes + diagnostic SQL script
6d42d8c feat(esports): on-demand PandaScore team backfill for Glicko-2 misses
```

---

## CROSS-POLLINATION ROADMAP (MirrorBot & WeatherBot → EsportsBot)

### Phase B4 (Waterfall Diagnostics) — DONE
Waterfall counters in `analyze_opportunity()`, logged in `esportsbot_scan_summary`.

### Phase 1: Critical Risk Guardrails — NOT IMPLEMENTED
| Item | Description | Priority |
|------|-------------|----------|
| **A1+A8** | Daily loss limit + drawdown halt. No daily P&L tracking, no circuit breaker. | **HIGH** |
| **B1** | Stop-loss exits. No stop-loss at all. MirrorBot pattern: 15% via `place_order(side="SELL")`. | **HIGH** |

### Phase 2: Position Intelligence — NOT IMPLEMENTED
| Item | Description | Priority |
|------|-------------|----------|
| **A2** | Position re-evaluation. Never updates open position predictions. | **HIGH** |
| **A10** | Pre-update exposure before order. Race condition fix. | **MED** |
| **B3** | Exit exposure decrement. `_game_exposure` only increments. | **MED** |

### Phase 3: Sizing Upgrades — NOT IMPLEMENTED
| Item | Description | Priority |
|------|-------------|----------|
| **A5** | Near-expiry confidence boost (<6h: 1.5x, <24h: 1.2x). | **MED** |
| **A6** | Uncertainty-scaled sizing (Baker-McHale). Use phi as proxy. | **MED** |
| **A3** | Dynamic Kelly graduation. 50+ resolved + Brier<0.24 → Kelly 0.30. | **MED** |

### Phase 4: Diagnostics — PARTIALLY DONE
| Item | Status |
|------|--------|
| **B4** Waterfall logging | **DONE** |
| **A4** Tournament-aware scan interval | NOT DONE |

### Deferred
- **B5** (Per-model reliability): Needs 50+ resolved per game
- **A7** (Slippage-adjusted edge): CLOB API unreliable
- **A9** (Lead-time-graduated edge cap): Low priority
- **B2** (Max hold time exit): Esports resolve within 24-48h

---

## CRITICAL TRAPS (DO NOT BREAK)

1. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
2. **`paper_trades` has NO `metadata` JSONB column** — never assume it exists.
3. **Resolution backfill MUST exclude SELL trades** (`AND LOWER(pt.side) != 'sell'`).
4. **`ESPORTS_MIN_EDGE` default in settings.py is 0.08** — VPS env overrides to 0.05.
5. **`ESPORTS_MAX_EDGE` NOT in settings.py** — only via `getattr(..., 0.20)` env override.
6. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
7. **asyncpg DATE**: `CURRENT_DATE` as SQL literal, NOT Python strftime.
8. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
9. **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is real sizer.
10. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
11. **Position `current_price` auto-updated every 10s** by position_manager.
12. **`websockets.exceptions`** must be imported explicitly (v15 lazy-loads).
13. **VPS shared .env** (`/opt/pa2-shared/.env`) is the REAL config. systemd reads it.
14. **BOT_REGISTRY has 14 bots** — shared module changes require all 14 verified.
15. **PandaScore rate limit**: All 3 bots share 1000 req/hr (class-level counter, breaker at 950).
16. **`_backfill_attempted` set**: Session-long. Prevents repeated PandaScore lookups.
17. **`_get_glicko2_prediction()` is NOW ASYNC** (changed Session 78). Test mocks need AsyncMock.
18. **`esports_teams` table is EMPTY** — `_team_name_to_id` comes from `glicko2_ratings` only.
19. **`_game_exposure` only increments** (B3 not yet fixed). Never decrements on exit.
20. **Temporary INFO logs should revert to DEBUG** after bottleneck analysis complete.

---

## DEVELOPMENT RULES (from CLAUDE.md)

1. **One fix per commit.** No "while I'm in here" refactors.
2. **Preserve function signatures.** Change callers if you change a signature.
3. **Read the entire file** before modifying.
4. **No new dependencies** without justification.
5. **Test before deploy**: `pytest` all pass → deploy → verify VPS logs.
6. **Cross-bot verification**: If touching shared modules, verify ALL 14 bots.
7. **State the bug in one sentence** before writing any code.
8. **Forbidden**: Band-aid try/except, shotgun fixes, scope creep, silent migrations, optimistic rewrites.
9. **Esports scope lock**: NEVER commit non-esports file changes in esports sessions.

---

## WHAT TO DO NEXT (Recommended Priority)

### Immediate
1. **Commit uncommitted Session 78 changes** (esports_bot.py, esports_market_service.py, env.vps)
2. **Deploy & run `esports_pnl_audit.sql`** on VPS to check live data integrity

### Short-term (next 1-2 sessions)
3. **Daily loss limit + drawdown halt** (A1+A8) — Most critical missing risk guardrail
4. **Stop-loss exits** (B1) — No way to exit losing positions before resolution
5. **Revert INFO debug logs to DEBUG** — Reduce log noise

### Medium-term
6. **Position re-evaluation** (A2) — Update predictions with fresh Glicko-2 data
7. **Exit exposure decrement** (B3) — `_game_exposure` fix
8. **Sizing upgrades** (A5, A6, A3)

### Long-term
9. **CANARY_STAGE 0→1** — Meet gate criteria in `LIVE_READINESS.md`
10. **Per-model reliability** (B5) — Needs 50+ resolved per game

---

## DB TABLES (Esports-Relevant)

| Table | Purpose |
|-------|---------|
| `paper_trades` | All paper trade records (BUY/SELL/YES/NO) |
| `positions` | Open/closed position tracking |
| `markets` | Market metadata + resolution |
| `glicko2_ratings` | Team Glicko-2 ratings (mu, phi, sigma, game) |
| `esports_prediction_log` | Prediction accuracy tracking |
| `esports_calibration` | Per-game calibration stats |
| `esports_teams` | Team metadata (CURRENTLY EMPTY) |
| `esports_matches` | Match metadata from PandaScore |
| `esports_training_data` | Historical match data for model training |
| `daily_counters` | Exposure/counter persistence |

---

## FILE MANIFEST (All Esports Files)

### Bot Files
- `bots/esports_bot.py` (~2170 lines) — Main bot
- `bots/esports_live_bot.py` (~298 lines) — Live event bot
- `bots/esports_series_bot.py` (~792 lines) — Series conditional probability bot

### Data Layer
- `esports/data/pandascore_client.py` (531 lines) — API client + rate limiting
- `esports/data/esports_db.py` (804 lines) — DB queries
- `esports/data/opendota_client.py` — Dota2 enrichment

### Market Layer
- `esports/markets/esports_market_service.py` (445 lines) — Market discovery + CLOB refresh
- `esports/markets/esports_market_scanner.py` (~300 lines) — Market discovery wrapper

### Models
- `esports/models/glicko2.py` (279 lines) — Rating system
- `esports/models/lol_win_model.py` (~200 lines) — LoL ML model
- `esports/models/cs2_economy_model.py` (~400 lines) — CS2 models
- `esports/models/dota2_model.py` — Dota2 model
- `esports/models/valorant_model.py` — Valorant model

### Live Event System
- `esports/live/esports_game_monitor.py` — Live match event monitoring
- `esports/live/esports_event_detector.py` — Event classification

### Training
- `esports/training/esports_trainer.py` (~600 lines) — Model retraining + calibration

### Tests
- `tests/unit/test_esports_bot.py` (557 lines, 54 tests)
- `tests/unit/test_esports_live_bot.py`
- `tests/unit/test_esports_series_bot.py`
- `tests/unit/test_esports_series_model.py`
- `tests/unit/test_esports_bankroll.py`

### Deploy
- `deploy/esports_pnl_audit.sql` (166 lines) — VPS diagnostic script
- `deploy/env.vps` — Environment variables

### Schema
- `schema/migrations/031_glicko2_ratings.sql` — Glicko-2 ratings table

---

*Generated 2026-03-12. This is Session 80's complete carbon copy for seamless agent continuation.*
