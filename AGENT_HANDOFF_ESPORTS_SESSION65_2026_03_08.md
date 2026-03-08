# EsportsBot Ecosystem — Agent Handoff (Session 65)

> **Purpose**: Complete handoff for a new agent to continue EsportsBot ecosystem development.
> **Scope**: EsportsBot ecosystem only (3 bots: EsportsBot, EsportsLiveBot, EsportsSeriesBot). No bleed to other modules.
> **Date**: 2026-03-08
> **Branch**: `master` (main branch is `main`)
> **HEAD commit**: `418f422` (fix: asyncpg scheduled_at needs datetime not str + JSONB CAST)
> **Test count**: 1242 passed, 6 skipped (system-wide).
> **VPS state**: Service restarted. RiotApiClient initialized confirmed.
> **Plan file**: `C:\Users\samwa\.claude\plans\jiggly-chasing-meerkat.md`
> **Previous handoff**: `AGENT_HANDOFF_ESPORTS_SESSION63_2026_03_08.md` (852 lines, Sessions 63-64)

---

## TABLE OF CONTENTS

1. [Session 65 Summary](#1-session-65-summary)
2. [Commits Made This Session](#2-commits-made-this-session)
3. [Bugs Found and Fixed](#3-bugs-found-and-fixed)
4. [Bugs Found NOT Yet Fixed](#4-bugs-found-not-yet-fixed)
5. [Deployed to VPS](#5-deployed-to-vps)
6. [P1.1 Seed Data — Current Status](#6-p11-seed-data--current-status)
7. [Immediate Next Steps](#7-immediate-next-steps)
8. [Approved Plan (Steps 4-7)](#8-approved-plan-steps-4-7)
9. [System Architecture](#9-system-architecture)
10. [File Map with Line Counts](#10-file-map-with-line-counts)
11. [Configuration & Credentials](#11-configuration--credentials)
12. [PandaScore Slug Mapping](#12-pandascore-slug-mapping)
13. [Critical Patterns & Traps](#13-critical-patterns--traps)
14. [Deploy Protocol](#14-deploy-protocol)
15. [Verification Commands](#15-verification-commands)
16. [Prior Handoff Documents](#16-prior-handoff-documents)

---

## 1. SESSION 65 SUMMARY

Session 65 focused on making the 8-game expansion actually work end-to-end. Session 63 added the code for 8 games but only LoL and CS2 had training data, the Riot API import was silently broken, and PandaScore data collection had 3 independent bugs preventing any data from being stored.

**What was accomplished:**
- Fixed 5 bugs blocking the esports data pipeline (import, slug, date range, JSONB cast, datetime)
- Wired `train_cross_game()` into the EsportsBot training flow (was defined but never called)
- Created `seed_esports_data.py` for one-shot historical data collection
- Deployed all fixes to VPS and confirmed RiotApiClient initialization
- Attempted seed data collection, hitting and fixing bugs iteratively until PandaScore rate limit exhausted

**What is blocked:**
- Seed data collection blocked by PandaScore rate limit (1000 req/hr exhausted by failed runs)
- P2.1 (Dota2/Valorant models) depends on seed data
- Map veto has never executed (known bug, planned for P2.3)

---

## 2. COMMITS MADE THIS SESSION

All commits in chronological order:

| # | SHA | Message | Files Changed | LOC |
|---|-----|---------|---------------|-----|
| 1 | `8662001` | feat: wire train_cross_game() into EsportsBot training flow | bots/esports_bot.py | +9 |
| 2 | `7f01f56` | fix: PandaScore CoD slug cod-mw -> codmw (was returning 404) | esports/data/pandascore_client.py | +1/-1 |
| 3 | `332bf69` | fix: RiotApiClient import name (was RiotAPIClient, class is RiotApiClient) | bots/esports_bot.py | +2/-2 |
| 4 | `be1f137` | feat: seed_esports_data.py -- one-shot data collection for 6 new games | scripts/seed_esports_data.py (new) | +85 |
| 5 | `f0e1157` | fix: PandaScore range[scheduled_at] missing end date (400 Bad Request) | esports/data/pandascore_client.py | +4/-2 |
| 6 | `d2477a8` | fix: JSONB cast syntax for asyncpg | esports/data/esports_data_collector.py | +1/-1 |
| 7 | `418f422` | fix: asyncpg scheduled_at needs datetime not str + JSONB CAST | esports/data/esports_data_collector.py | +9/-1 |

**Total**: 7 commits, 5 files modified, 1 file created, ~110 LOC changed.

---

## 3. BUGS FOUND AND FIXED

### Bug 1: RiotApiClient Import Name Mismatch

**Location**: `bots/esports_bot.py:116`
**Root cause**: Code imported `RiotAPIClient` (uppercase "PI") but the class in `riot_api_client.py` is `RiotApiClient` (lowercase "pi"). The import was inside a try/except, so it silently failed. Riot API never initialized on VPS even with RIOT_API_KEY set.
**Fix**: Changed `RiotAPIClient` to `RiotApiClient` on lines 116-117.
**Commit**: `332bf69`

### Bug 2: PandaScore CoD Slug

**Location**: `esports/data/pandascore_client.py` GAME_SLUGS dict
**Root cause**: Slug was `"cod-mw"` which returned 404 from PandaScore API. Correct slug is `"codmw"`.
**Fix**: Changed `"cod-mw"` to `"codmw"`.
**Commit**: `7f01f56`
**Verification**: All 8 slugs tested against live API and confirmed 200 OK.

### Bug 3: PandaScore Date Range Missing End Bound

**Location**: `esports/data/pandascore_client.py`, `get_past_matches()`
**Root cause**: The `range[scheduled_at]` parameter was sent as `"{since},"` (trailing comma, no end date). PandaScore returned 400 Bad Request for ALL past match requests across ALL games. This meant zero historical data was ever fetched for any game.
**Fix**: Added current UTC time as upper bound: `"{since},{until}"`.
**Commit**: `f0e1157`

### Bug 4: asyncpg JSONB Cast Syntax

**Location**: `esports/data/esports_data_collector.py`, `_store_row()`
**Root cause**: SQL used `:game_state_json::jsonb` which asyncpg misparses (treats `::` as part of the bind parameter name). All INSERT statements for training data failed.
**Fix**: Changed to `CAST(:game_state_json AS jsonb)`.
**Commit**: `d2477a8`

### Bug 5: asyncpg Datetime Conversion

**Location**: `esports/data/esports_data_collector.py`, `_store_row()`
**Root cause**: The `scheduled_at` column is a PostgreSQL timestamp, but the code passed an ISO 8601 string. asyncpg requires native Python `datetime` objects for timestamp columns. All INSERTs failed with a type error.
**Fix**: Added `fromisoformat()` parsing to convert the string to a `datetime` object before binding.
**Commit**: `418f422`

### Impact Chain

Bugs 3, 4, and 5 interacted as a chain failure:
1. Bug 3 meant PandaScore never returned matches (400 error) for any game
2. Even if matches were returned, Bug 4 meant the INSERT SQL failed (asyncpg JSONB parse error)
3. Even if the SQL was valid, Bug 5 meant the datetime binding failed (asyncpg type error)

All three had to be fixed before ANY training data could be stored. This is why 3 separate data collection runs were needed, each discovering the next bug in the chain.

---

## 4. BUGS FOUND NOT YET FIXED

### Bug 6: series_prob_with_map_veto() Signature Mismatch

**Location**: `bots/esports_series_bot.py:269-275`
**Root cause**: The call passes `best_of=best_of` but the function `series_prob_with_map_veto()` in `esports/models/series_model.py:170` expects `veto_order: List[str]` as its 3rd positional argument. The call is missing the `veto_order` parameter entirely and passes `best_of` which is not a parameter of this function.

```python
# Current call (esports_series_bot.py:269-275):
model_prob = series_prob_with_map_veto(
    team_a_map_rates=map_rates_a,
    team_b_map_rates=map_rates_b,
    maps_won_a=maps_a,
    maps_won_b=maps_b,
    best_of=best_of,    # <-- WRONG: function expects veto_order as 3rd arg
)

# Expected signature (series_model.py:170-175):
def series_prob_with_map_veto(
    team_a_map_rates: Dict[str, float],
    team_b_map_rates: Dict[str, float],
    veto_order: List[str],             # <-- This is missing from the call
    maps_won_a: int = 0,
    maps_won_b: int = 0,
    ...
)
```

**Impact**: Always raises TypeError, caught by the except block on line 276. Falls through to `_simple_series_prob()`. Map veto has NEVER executed in production.
**Priority**: P2.3 (planned, not blocking — simple model works as fallback)
**Planned fix**: See Step 6 in Section 8.

---

## 5. DEPLOYED TO VPS

The following files were deployed to VPS (`34.251.224.21`) and the service was restarted:

| Local File | VPS Path |
|-----------|----------|
| bots/esports_bot.py | /opt/polymarket-ai-v2/bots/esports_bot.py |
| esports/data/pandascore_client.py | /opt/polymarket-ai-v2/esports/data/pandascore_client.py |
| esports/data/esports_data_collector.py | /opt/polymarket-ai-v2/esports/data/esports_data_collector.py |
| scripts/seed_esports_data.py | /opt/polymarket-ai-v2/scripts/seed_esports_data.py |

**Environment variables set on VPS:**
- `RIOT_API_KEY=RGAPI-5f245329-29d0-4e43-87d6-e5c3d8c19b05` (DEV key, expires every 24h)

**Post-deploy verification:**
- `RiotApiClient: initialised` confirmed in journalctl
- Service running, all bots scanning

---

## 6. P1.1 SEED DATA -- CURRENT STATUS

**Status: BLOCKED by PandaScore rate limit (1000 req/hr)**

### Timeline of attempts:

1. **Run 1**: Fetched 1115 Dota2 matches from PandaScore. All `_store_row()` calls failed due to Bug 4 (JSONB cast syntax). Zero rows stored.
2. **Run 2**: Fixed JSONB bug. Re-ran. All `_store_row()` calls failed due to Bug 5 (datetime as string). Zero rows stored.
3. **Run 3**: Fixed datetime bug. Re-ran. Rate limit exhausted from previous failed runs. PandaScore returned 429 Too Many Requests.

### What needs to happen:

1. Wait approximately 1 hour for PandaScore rate limit to reset
2. Run the seed script on VPS:
   ```bash
   cd /opt/polymarket-ai-v2 && sudo -E venv/bin/python -m scripts.seed_esports_data 2>&1
   ```
3. Verify data was stored:
   ```sql
   SELECT game, COUNT(*) FROM esports_training_data GROUP BY game;
   ```

### Known issue: 403 on team_stats endpoint

PandaScore free tier returns 403 Forbidden for team stats on non-LoL/CS2 games. The data collector calls this endpoint and it wastes rate limit budget even though it fails. This is non-blocking since Glicko-2 handles team strength without team stats, but the collector should ideally skip team_stats calls for games that return 403. This is a minor optimization, not a bug fix.

---

## 7. IMMEDIATE NEXT STEPS (Priority Order)

### Step 1: Re-run seed script after rate limit reset

**Priority**: P0 (everything else depends on this)
**Action**: Wait ~1 hour, then run on VPS:
```bash
cd /opt/polymarket-ai-v2 && sudo -E venv/bin/python -m scripts.seed_esports_data 2>&1
```
**Verify**:
```sql
SELECT game, COUNT(*) FROM esports_training_data GROUP BY game;
```
**Expected**: 100-1000+ rows per game for dota2, valorant, cod, r6, sc2, rl.

### Step 2: Optimize seed script (optional but recommended)

Skip team_stats API calls that return 403 (saves ~700 wasted requests per run). This is a minor optimization in `esports_data_collector.py` to catch 403 responses from the team_stats endpoint and skip them for games where it is known to fail.

### Step 3: Continue with P2 items

After seed data is confirmed, proceed with the plan in Section 8 (Steps 4-7).

---

## 8. APPROVED PLAN (STEPS 4-7)

These steps were approved by the user but not started. They are defined in detail in the plan file: `C:\Users\samwa\.claude\plans\jiggly-chasing-meerkat.md`

### Step 4: P2.2 -- P&L Monitoring

**Goal**: Periodic P&L summary logging (per-game PnL, win rate, avg edge) in the existing 10-minute monitoring cycle.

**Changes**:
- `esports/data/esports_db.py` -- new `compute_pnl_summary()` function (~50 LOC)
  - Queries `paper_trades` WHERE `bot_name LIKE 'Esports%'`, groups by game
  - Returns: `{per_game_pnl, total_pnl, win_rate, avg_edge, total_trades}`
- `bots/esports_bot.py` -- add call in `_check_monitoring_thresholds()` (~10 LOC)
  - Logs `esportsbot_pnl_summary` every 10 minutes

**Verify**: `journalctl -u polymarket-ai -f | grep "esportsbot_pnl_summary"`

### Step 5: P2.1 -- Dota2 + Valorant Dedicated Models

**Goal**: XGBoost classifiers using Glicko-2 features + `best_of`. Same pattern as LoLWinModel but simpler.

**Depends on**: P1.1 seed data (need 50+ samples per game)

**New files**:
- `esports/models/dota2_model.py` (~180 LOC) -- `Dota2Model` class
- `esports/models/valorant_model.py` (~180 LOC) -- `ValorantModel` class

**Modified files**:
- `esports/models/esports_trainer.py` -- replace early-return for dota2/valorant with `_train_dota2()` / `_train_valorant()` (~80 LOC)
- `bots/esports_bot.py` -- add `self._dota2_model`, `self._valorant_model` in `__init__` + load in `start()` + wire into prediction path (~30 LOC)

**New test files**:
- `tests/unit/test_dota2_model.py` (~80 LOC)
- `tests/unit/test_valorant_model.py` (~80 LOC)

**Model spec**:
- Features: `team_strength_diff, matchup_uncertainty, rd_asymmetry, team_a_volatility, team_b_volatility, best_of`
- XGBoost: `n_estimators=60, max_depth=2` (fewer features = simpler)
- Min samples: 50 each
- Saved to: `saved_models/dota2_xgb.json`, `saved_models/valorant_xgb.json`
- Both follow `lol_win_model.py` pattern: `predict()`, `train()`, `save()`, `load()`, heuristic fallback

### Step 6: P2.3 -- Map Veto Wiring for SeriesBot

**Goal**: Fix Bug #6 so map veto actually executes for CS2 series.

**Changes**:
- `esports/data/esports_data_collector.py` line 294 -- add `"map_name": map_name` to CS2 `game_state` dict (1 line)
- `esports/data/esports_db.py` -- new `get_team_map_rates(db, team_name, game, last_n)` (~60 LOC)
  - Queries `esports_training_data` for CS2 rows with `map_name` in `game_state_json`
  - Returns `{map_name: win_rate}` per team
- `bots/esports_series_bot.py` lines 251-282 -- rewrite veto block (~50 LOC):
  1. Call `get_team_map_rates()` from DB instead of HLTV stubs
  2. Build `veto_order` from PandaScore `games` array (maps played in order)
  3. Fallback: derive veto from team preferences
  4. Pass correct `veto_order` param to `series_prob_with_map_veto()`
- New helper: `_derive_veto_order(rates_a, rates_b, best_of, pool)` (~20 LOC)

**Verify**: `pytest tests/unit/test_esports_series_bot.py tests/unit/test_esports_series_model.py -v`

### Step 7: P2.4 -- HLTV Scraper Implementation

**Goal**: Replace 5 stub methods in `hltv_scraper.py` with real scraping.

**Changes**:
- `esports/data/hltv_scraper.py` -- implement 5 stub methods (~180 LOC replacing ~20):
  - `_scrape_hltv_team_rating(team_name)` -- parse `hltv.org/ranking/teams`
  - `_scrape_hltv_map_stats(team_name)` -- parse per-map win rates
  - `_scrape_hltv_results(team_name, n)` -- recent match results
  - `_scrape_cs2_patch()` -- latest CS2 update from counter-strike.net
  - `_scrape_liquipedia_results(team_name, game, n)` -- Liquipedia API (multi-game)
- New dependency: `beautifulsoup4>=4.12.0` in `requirements.txt`
- Rate limit: 10s between HLTV requests, 0.5s for Liquipedia API
- Graceful fallback: bot already works without HLTV data

**Risk**: HLTV blocks scrapers aggressively. HLTV is supplementary only.

### Commit Order

| # | Commit Message | Files | LOC |
|---|---------------|-------|-----|
| 1 | `feat: compute_pnl_summary() P&L monitoring` | esports_db.py, esports_bot.py | ~60 |
| 2 | `feat: Dota2Model + ValorantModel XGBoost` | 2 new model files, trainer, esports_bot.py, 2 test files | ~630 |
| 3 | `fix: map veto wiring + PandaScore map rates` | esports_data_collector.py, esports_db.py, esports_series_bot.py | ~130 |
| 4 | `feat: HLTV scraper implementation` | hltv_scraper.py, requirements.txt, test file | ~240 |

**Total remaining**: ~1060 new/modified LOC across 4 commits.

---

## 9. SYSTEM ARCHITECTURE

### The 14-Bot System

```
main.py -> BOT_REGISTRY -> BaseBot subclasses
                           |-- EsportsBot          (uses esports/ package)
                           |-- EsportsLiveBot      (uses esports/ package)
                           |-- EsportsSeriesBot    (uses esports/ package)
                           |-- WeatherBot          (uses weather/ package)
                           |-- SportsBot, SportsArbBot, SportsLiveBot, SportsInjuryBot
                           |-- ArbitrageBot, LogicalArbBot, CrossPlatformArbBot
                           |-- MirrorBot, OracleBot, LLMForecasterBot
                           \-- (EnsembleBot -- ARCHIVED Session 61)
                               (MomentumBot -- DELETED)
```

14 active bots. EnsembleBot archived (0.2% win rate, -$5.6K). MomentumBot deleted.

### The 3 Esports Bots

| Bot | Purpose | Scan Interval | Key File |
|-----|---------|---------------|----------|
| **EsportsBot** | Pre-match & live match winner prediction | 120s (10s during live) | `bots/esports_bot.py` |
| **EsportsLiveBot** | In-game event-driven trading (baron, economy breaks) | 60s idle / 10s active | `bots/esports_live_bot.py` |
| **EsportsSeriesBot** | BO3/BO5 series outcome trading | 300s idle / 30s active | `bots/esports_series_bot.py` |

### Supported Games (8 total)

| Game | Slug | Model Status | Data Status |
|------|------|-------------|-------------|
| League of Legends | `lol` | Full ML (XGBoost + Glicko-2 blend) | Has data |
| Counter-Strike 2 | `csgo` | XGBoost economy model (graduated, Brier 0.2473) | Has data |
| Dota 2 | `dota2` | Glicko-2 heuristic only | BLOCKED (needs seed) |
| Valorant | `valorant` | Glicko-2 heuristic only | BLOCKED (needs seed) |
| Call of Duty | `codmw` | Glicko-2 heuristic only | BLOCKED (needs seed) |
| Rainbow Six | `r6siege` | Glicko-2 heuristic only | BLOCKED (needs seed) |
| StarCraft 2 | `starcraft-2` | Glicko-2 heuristic only | BLOCKED (needs seed) |
| Rocket League | `rl` | Glicko-2 heuristic only | BLOCKED (needs seed) |

### Data Flow

```
PandaScore API ---> esports_data_collector.py ---> esports_training_data (DB)
                                               \-> glicko2_ratings (DB)
                                               \-> Glicko-2 in-memory ratings

Riot API ---------> riot_api_client.py ---------> patch_drift.py (LoL patch detection)

HLTV (stubs) -----> hltv_scraper.py ------------> (NOT WIRED -- all stubs)

esports_training_data --> esports_trainer.py ----> lol_win_model.py / cs2_economy_model.py
                                               \-> saved_models/*.json

Polymarket -------> esports_market_scanner.py ---> esports_market_service.py ---> EsportsBot
```

### Prediction Flow (EsportsBot)

```
scan_for_opportunities()
  |-> esports_market_scanner.find_markets()       # discover markets
  |-> for each market:
  |     |-> get Glicko-2 ratings for both teams
  |     |-> if LoL: lol_win_model.predict()        # XGBoost
  |     |-> if CS2: cs2_economy_model.predict()    # XGBoost
  |     |-> else: Glicko-2 heuristic only
  |     |-> _compute_confluence()                  # model 55% / freshness 30% / agreement 15%
  |     |-> Bayesian prior blending (phi-based)    # phi>=350 -> 80% toward 0.50
  |     |-> apply_signal_enhancements()            # from base_bot
  |     |-> edge = |model_prob - market_price|
  |     |-> if edge > threshold: place_order()
```

---

## 10. FILE MAP WITH LINE COUNTS

### Esports Package (7,198 total lines across 17 Python files)

| File | Lines | Purpose |
|------|-------|---------|
| `bots/esports_bot.py` | 1,278 | Main trading bot (pre-match + live) |
| `bots/esports_series_bot.py` | 518 | Series prediction (BO3/BO5) |
| `bots/esports_live_bot.py` | 256 | Live event-driven trading |
| `esports/data/esports_data_collector.py` | 613 | PandaScore -> DB training data + Glicko-2 |
| `esports/data/pandascore_client.py` | 423 | PandaScore REST API client |
| `esports/data/esports_db.py` | 529 | DB helper functions |
| `esports/data/hltv_scraper.py` | 224 | HLTV stub (NOT wired) |
| `esports/data/riot_api_client.py` | 196 | Riot Games API client (LoL patches) |
| `esports/models/esports_trainer.py` | 575 | Training orchestration |
| `esports/models/lol_win_model.py` | 456 | LoL XGBoost model |
| `esports/models/cs2_economy_model.py` | 526 | CS2 3-tier model |
| `esports/models/glicko2.py` | 279 | Glicko-2 rating system |
| `esports/models/series_model.py` | 276 | Map veto series functions |
| `esports/models/patch_drift.py` | 272 | Patch detection |
| `esports/markets/esports_market_scanner.py` | 268 | Market discovery |
| `esports/markets/esports_market_service.py` | 424 | DB-backed market service |
| `scripts/seed_esports_data.py` | 85 | One-shot data seeder |

### Config

Esports configuration is in `config/settings.py` (lines ~900-967).

---

## 11. CONFIGURATION & CREDENTIALS

### VPS

| Key | Value |
|-----|-------|
| IP | `34.251.224.21` |
| User | `ubuntu` |
| SSH Key | `C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem` |
| Service | `polymarket-ai` (systemd) |
| Code path | `/opt/polymarket-ai-v2/` |
| Venv | `/opt/polymarket-ai-v2/venv/` |

### Database

| Key | Value |
|-----|-------|
| Password | `polymarket_s46` |
| Pattern | `db.get_session()` -> `async with ... as session:` -> `session.execute(text(...))` |

### Redis

| Key | Value |
|-----|-------|
| Password | `78psiRhepTgrmWSoy3cgNEIr` |

### API Keys

| Key | Value | Notes |
|-----|-------|-------|
| PANDASCORE_API_KEY | `-JttfhX0NAapsJ8fRb14QT46Jl43e7z68-_27mrMoTnO0S5tR_Y` | Free tier, 1000 req/hr |
| RIOT_API_KEY | `RGAPI-5f245329-29d0-4e43-87d6-e5c3d8c19b05` | DEV key, **expires every 24h** |

### PandaScore Free Tier Limitations

- 1000 requests per hour
- Team stats endpoint returns 403 for non-LoL/CS2 games
- No websocket/live push (polling only)

---

## 12. PANDASCORE SLUG MAPPING

All 8 slugs tested against live API and confirmed working:

```python
GAME_SLUGS = {
    "lol": "lol",
    "cs2": "csgo",
    "dota2": "dota2",
    "valorant": "valorant",
    "cod": "codmw",           # Fixed from "cod-mw" in commit 7f01f56
    "r6": "r6siege",
    "sc2": "starcraft-2",
    "rl": "rl",
}
```

---

## 13. CRITICAL PATTERNS & TRAPS

### Code Patterns

1. **BUY/SELL vs YES/NO**: `BaseBot.place_order()` expects `side="YES"` or `side="NO"`. NEVER pass `"BUY"` or `"SELL"`.

2. **PSEUDO_LABEL_ENABLED=false**: DO NOT enable. Only Location 1 (market resolution) labels are correct.

3. **ENSEMBLE_BLEND=1.0**: Bypasses learning_conf.

4. **Database JSONB syntax**: Use `CAST(:param AS jsonb)` NOT `:param::jsonb`. asyncpg misparses the `::` notation.

5. **Database datetime binding**: asyncpg requires native Python `datetime` objects for timestamp columns. Use `fromisoformat()` to parse ISO strings before binding.

6. **PandaScore date ranges**: `range[scheduled_at]` needs both start AND end: `"{since},{until}"`. Missing end date returns 400.

7. **Import names matter**: `RiotApiClient` (lowercase "pi"), NOT `RiotAPIClient`. Silent failures inside try/except.

8. **CLOB markets have volume=0**: Do not use volume gates for market filtering.

9. **Polymarket category tagging is unreliable**: Use keyword matching, not category filter.

10. **websockets.exceptions** must be imported explicitly (v15 lazy-loads).

### Esports-Specific Traps

1. **Glicko-2 outcome**: `outcome == 1` means team_a wins. This was inverted (Bug A, Session 62) and was ROOT CAUSE of 14% win rate.

2. **`_clean_team_names()`**: Strips game prefixes + tournament suffixes. Fuzzy match is longest-first unidirectional (prevents "t1" matching "fnatic").

3. **Confluence weights** (Session 62 rewrite): model 55% / freshness 30% / agreement 15%. Whale/orderbook signals removed (always returned 0.5).

4. **Bayesian prior blending** (Session 63): phi >= 350 -> 80% prior toward 0.50, 200-350 -> 50%, 100-200 -> 20%, < 100 -> 0%. In `_get_glicko2_prediction()`.

5. **Cross-game XGBoost**: `train_cross_game()` pools all 8 games with game_id feature. Saved to `saved_models/cross_game_xgb.json`. Wired into training flow in Session 65 (`8662001`).

6. **E4 monitoring**: 10min Brier check, > 0.25 warn, > 0.30 halt.

7. **PandaScore team stats 403**: Free tier blocks team stats for non-LoL/CS2 games. Wastes rate limit budget. Non-blocking since Glicko-2 covers team strength.

8. **Riot API key expires every 24h**: Dev key limitation. Bot degrades gracefully without it (no patch drift detection for LoL).

9. **Map veto never executes**: Bug #6 (Section 4). Always falls through to simple model. Planned for P2.3.

### Position Management

- `current_price` auto-updated every 10s by `position_manager._update_current_prices()`
- `positions` schema has `bot_id` column; `paper_trades` schema has `bot_name` column

### Bankroll

- BotBankrollManager handles SIZING; risk_manager handles LIMITS. Both must pass.
- `risk_manager.calculate_position_size()` is DEPRECATED (BotBankrollManager used instead)
- Paper trading phase: PHASE_MAX_BET_USD=$1000, but BotBankrollManager max_bet_usd=$100 is the real cap

---

## 14. DEPLOY PROTOCOL

### Standard Deploy (Windows -> VPS)

```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Copy file to VPS
scp -i "$KEY" -o StrictHostKeyChecking=no "local/path/file.py" "$VPS:/tmp/"

# Install and restart
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" 'sudo cp /tmp/file.py /opt/polymarket-ai-v2/path/file.py && sudo systemctl restart polymarket-ai'
```

### Run seed script on VPS

```bash
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" 'cd /opt/polymarket-ai-v2 && sudo -E venv/bin/python -m scripts.seed_esports_data 2>&1'
```

### Check service status

```bash
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" 'sudo systemctl is-active polymarket-ai && journalctl -u polymarket-ai --since "5 min ago" --no-pager | tail -50'
```

---

## 15. VERIFICATION COMMANDS

### On VPS (via SSH)

```bash
# Service running
sudo systemctl is-active polymarket-ai

# All bots scanning (wait 2 min after restart)
journalctl -u polymarket-ai --since "2 min ago" | grep -c "Bot\|bot"

# Riot API initialized
journalctl -u polymarket-ai | grep -i "riot"

# Esports bot specifically
journalctl -u polymarket-ai -f | grep "EsportsBot"
journalctl -u polymarket-ai -f | grep "EsportsLiveBot"
journalctl -u polymarket-ai -f | grep "EsportsSeriesBot"

# P&L summary (after Step 4)
journalctl -u polymarket-ai -f | grep "esportsbot_pnl_summary"

# Map veto (after Step 6, when CS2 series occur)
journalctl -u polymarket-ai | grep "map_veto\|veto_order"

# Training data counts
psql -c "SELECT game, COUNT(*) FROM esports_training_data GROUP BY game;"

# Glicko-2 ratings
psql -c "SELECT game, COUNT(*) FROM glicko2_ratings GROUP BY game;"
```

### Local (Windows)

```bash
# Run all tests
pytest tests/ -x -q

# Esports tests specifically
pytest tests/unit/test_esports*.py tests/unit/test_lol*.py tests/unit/test_cs2*.py -v
```

---

## 16. PRIOR HANDOFF DOCUMENTS

| Document | Scope | Date |
|----------|-------|------|
| `AGENT_HANDOFF_ESPORTS_SESSION63_2026_03_08.md` | Full esports ecosystem (852 lines, Sessions 53-64) | 2026-03-08 |
| `AGENT_HANDOFF_WEATHERBOT_SESSION61_2026_03_08.md` | WeatherBot doom loop fix + EnsembleBot archive | 2026-03-08 |
| `AGENT_HANDOFF_SESSION47_2026_03_03.md` | Canonical system handoff (all 15 bots) | 2026-03-03 |

### Session 63-64 Summary (for context)

- **Session 62**: Glicko-2 outcome inversion fix (ROOT CAUSE of 14% win rate), 8-game expansion, confluence rewrite
- **Session 63**: Full handoff doc, E4 monitoring, E5 Bayesian prior, per-bot documentation
- **Session 64**: S-T multi-bucket Kelly, Baker-McHale weighting, dynamic Kelly graduation, CRPS scoring, cross-game XGBoost, edge decay analysis

### Key Backlog (from Session 63 plan)

**P1 (Foundation)**:
- P1.1: Seed data for 6 new games -- **IN PROGRESS** (blocked by rate limit)
- P1.2: Live pipeline -- DONE (20 games tracked)
- P1.3: Riot API key -- DONE (deployed Session 65)

**P2 (Enhancement)**:
- P2.1: Dota2/Valorant models -- PLANNED (depends on P1.1)
- P2.2: P&L monitoring -- PLANNED (Step 4)
- P2.3: Map veto wiring -- PLANNED (Step 6)
- P2.4: HLTV scraper -- PLANNED (Step 7)
- P2.5: CoD slug fix -- DONE (commit `7f01f56`)

**P3 (Advanced)**:
- P3.1: Player-level features
- P3.2: Tournament context
- P3.3: Live retraining pipeline
- P3.4: PandaScore paid tier evaluation

---

## UNCOMMITTED CHANGES

As of HEAD (`418f422`), there are uncommitted changes in:

| File | Status | Notes |
|------|--------|-------|
| `AGENT_HANDOFF_ESPORTS_SESSION63_2026_03_08.md` | Modified | Minor updates |
| `base_engine/data/ingestion_error_capture.txt` | Modified | Runtime error log (auto-generated) |
| `bots/weather_bot.py` | Modified | Unrelated to esports |

None of these affect the esports ecosystem.

---

## CHANGE LOG (Session 65)

```
## CHANGE: 2026-03-08 (Session 65)
**Issue:** train_cross_game() defined but never called
**Root cause:** No invocation in training flow
**Files modified:** bots/esports_bot.py
**Lines changed:** +9
**Blast radius:** EsportsBot training cycle
**Verification:** Tests pass, deployed
**Rollback:** git revert 8662001

## CHANGE: 2026-03-08 (Session 65)
**Issue:** PandaScore CoD slug returned 404
**Root cause:** Slug "cod-mw" incorrect, should be "codmw"
**Files modified:** esports/data/pandascore_client.py
**Lines changed:** +1/-1
**Blast radius:** PandaScore CoD match fetching only
**Verification:** All 8 slugs tested live, 200 OK
**Rollback:** git revert 7f01f56

## CHANGE: 2026-03-08 (Session 65)
**Issue:** Riot API never initialized on VPS despite key being set
**Root cause:** Import name mismatch (RiotAPIClient vs RiotApiClient)
**Files modified:** bots/esports_bot.py
**Lines changed:** +2/-2
**Blast radius:** EsportsBot Riot API initialization
**Verification:** journalctl shows "RiotApiClient: initialised"
**Rollback:** git revert 332bf69

## CHANGE: 2026-03-08 (Session 65)
**Issue:** No mechanism to seed training data for 6 new games
**Root cause:** New script needed
**Files modified:** scripts/seed_esports_data.py (NEW)
**Lines changed:** +85
**Blast radius:** None (standalone script)
**Verification:** Script created and deployed
**Rollback:** Delete file

## CHANGE: 2026-03-08 (Session 65)
**Issue:** PandaScore get_past_matches() returned 400 for all games
**Root cause:** range[scheduled_at] missing end date (trailing comma)
**Files modified:** esports/data/pandascore_client.py
**Lines changed:** +4/-2
**Blast radius:** All PandaScore historical match fetching
**Verification:** Dota2 matches fetched successfully (1115 matches)
**Rollback:** git revert f0e1157

## CHANGE: 2026-03-08 (Session 65)
**Issue:** _store_row() INSERT failed with asyncpg JSONB parse error
**Root cause:** :param::jsonb syntax incompatible with asyncpg
**Files modified:** esports/data/esports_data_collector.py
**Lines changed:** +1/-1
**Blast radius:** All esports training data storage
**Verification:** No JSONB errors after fix
**Rollback:** git revert d2477a8

## CHANGE: 2026-03-08 (Session 65)
**Issue:** _store_row() INSERT failed with asyncpg type error on scheduled_at
**Root cause:** asyncpg requires native datetime, not ISO string
**Files modified:** esports/data/esports_data_collector.py
**Lines changed:** +9/-1
**Blast radius:** All esports training data storage
**Verification:** Data collection pipeline end-to-end (blocked by rate limit before full verification)
**Rollback:** git revert 418f422
```
