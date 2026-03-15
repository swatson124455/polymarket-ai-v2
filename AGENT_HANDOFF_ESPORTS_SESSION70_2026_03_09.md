# EsportsBot Ecosystem -- Agent Handoff (Session 70)

> **Purpose**: Exhaustive carbon-copy handoff for a new agent to continue EsportsBot ecosystem development with zero loss of context, vision, or progress. A new agent should be able to read ONLY this file and have 100% of the context needed.
> **Date**: 2026-03-09
> **HEAD commit (local)**: `5e66431` -- feat(esports): P5 -- training data quality, smart retraining, phase calibration
> **VPS deployed**: `5e66431` confirmed deployed, PID 1774460
> **Tests**: 212 esports-specific / 1333 system-wide passing (6 skipped)
> **Project root**: `C:\lockes-picks\polymarket-ai-v2`
> **Bot scope**: EsportsBot, EsportsLiveBot, EsportsSeriesBot (3 bots, zero bleed to other 11)

---

## TABLE OF CONTENTS

1. [System Overview](#1-system-overview)
2. [Development Rules (from CLAUDE.md)](#2-development-rules-from-claudemd)
3. [Complete Session History (Sessions 62-70)](#3-complete-session-history-sessions-62-70)
4. [Training Data Quality](#4-training-data-quality)
5. [Architecture Deep-Dive](#5-architecture-deep-dive)
6. [Key Patterns / Traps (CRITICAL)](#6-key-patterns--traps-critical)
7. [Settings Reference (esports-specific)](#7-settings-reference-esports-specific)
8. [Deploy Pattern](#8-deploy-pattern)
9. [Current State (Post-P5)](#9-current-state-post-p5)
10. [What's Left (Deferred / Future Work)](#10-whats-left-deferred--future-work)
11. [File Index (all esports-scoped files)](#11-file-index-all-esports-scoped-files)
12. [Complete Commits Log](#12-complete-commits-log)

---

## 1. SYSTEM OVERVIEW

### What This System Is

A 15-bot Polymarket automated trading system with real capital at risk. The system runs on Ubuntu-3 VPS at 34.251.224.21, deployed as a systemd service (`polymarket-ai`) at `/opt/polymarket-ai-v2/`. 14 bots are currently active (MomentumBot was DELETED, EnsembleBot was ARCHIVED in Session 61 after 0.2% win rate and -$5.6K).

### The 3 Esports Bots

| Bot | File | Purpose | Scan Interval |
|-----|------|---------|---------------|
| **EsportsBot** | `bots/esports_bot.py` (1850 lines) | Pre-game + live in-play trading across 8 game titles | 120s (10s during live) |
| **EsportsLiveBot** | `bots/esports_live_bot.py` (298 lines) | In-game event detection via EsportsGameMonitor queue drain | 60s idle / 10s active |
| **EsportsSeriesBot** | `bots/esports_series_bot.py` (695 lines) | BO3/BO5 series conditional probability + map veto | 300s idle / 30s active |

All 3 require `PANDASCORE_API_KEY` and fail fast if missing.

### 8 Supported Games

| Game | PandaScore Slug | Model Status | Data Rows (post-filter) |
|------|----------------|-------------|------------------------|
| League of Legends | `lol` | Dedicated XGBoost + Glicko-2 | 1953 (1370 clean) |
| Counter-Strike 2 | `csgo` | Dedicated XGBoost + Glicko-2 | 4145 (3350 clean) |
| Dota 2 | `dota2` | Dedicated XGBoost + Glicko-2 | 2454 (2062 clean) |
| Valorant | `valorant` | Dedicated XGBoost + Glicko-2 | 182 (67 clean) |
| Call of Duty | `codmw` | Glicko-2 + cross-game XGB | 367 (330 clean) |
| Rainbow Six | `r6siege` | Glicko-2 + cross-game XGB | 676 (435 clean) |
| StarCraft 2 | `starcraft-2` | Glicko-2 + cross-game XGB | 550 (439 clean) |
| Rocket League | `rl` | Glicko-2 + cross-game XGB | 722 (487 clean) |

### VPS Infrastructure

| Key | Value |
|-----|-------|
| IP | `34.251.224.21` |
| User | `ubuntu` |
| SSH Key | `C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem` |
| Service | `polymarket-ai` (systemd) |
| Code path | `/opt/polymarket-ai-v2/` |
| Venv | `/opt/polymarket-ai-v2/venv/` |
| Specs | 16GB RAM / 4 vCPU |
| Current PID | 1774460 (restarted 2026-03-09 20:12:44 UTC) |
| Active bots | 14 (MomentumBot DELETED, EnsembleBot ARCHIVED) |

**CRITICAL**: VPS is NOT git-based. Files are deployed via `scp` to `/tmp` then `sudo cp` to `/opt/polymarket-ai-v2/`. There is no git repo on the VPS. The working tree on the development machine may have changes not in git history.

---

## 2. DEVELOPMENT RULES (from CLAUDE.md)

### Prime Directive

Working code is sacred. Fix only what is broken. Fix it at the root. Prove it before and after. If you cannot explain exactly why a line needs to change and exactly what breaks if you don't change it, do not change it.

### Pre-Edit Checklist (MANDATORY)

Complete this checklist out loud before modifying ANY file:

1. **State the bug** in one sentence. If you can't, you don't understand the problem yet.
2. **List files you will touch.** If more than 3, stop and justify why.
3. **Grep for dependents:**
   - `grep -rn "from <module> import" --include="*.py"`
   - `grep -rn "import <module>" --include="*.py"`
   - List the top 5 importers. Read them. State which you skipped and why.
4. **Git snapshot:** Run `git stash` or `git commit -m "pre-fix: <description>"` before any edit.
5. **Read the entire file** you're modifying, not just the function you're changing.

### Rules of Engagement

1. **One fix per commit.** Each commit addresses exactly ONE issue. No "while I'm in here" refactoring.
2. **Preserve every function signature.** Do not change names, parameter names, parameter order, return types, or default values unless the signature IS the bug. If you change one, update every single caller.
3. **Preserve every external interface.** API endpoints, DB column names/types, env var names, config keys, message formats, WS channel names -- these are contracts.
4. **No silent behavior changes.** If a function returns None on failure, don't change it to raise. If it retries 3 times, don't change to 5. State: "This changes behavior from X to Y. All callers that depend on X: [list]. I verified each handles Y."
5. **Never delete code you don't understand.** It may handle an edge case at 3am during a Polygon outage.
6. **No new dependencies without justification.**
7. **No structural refactors during bug fixes.** No moving functions between files, no sync-to-async conversion.

### Cross-Bot Verification (CRITICAL for shared modules)

After modifying ANY shared module (`base_bot.py`, `bankroll_manager.py`, `risk_manager.py`, `position_manager.py`, `prediction_engine.py`, `database.py`, `main.py`):

1. Run `pytest` -- all 1333+ tests must pass
2. List every bot affected by name (all 14 if you touched `base_bot.py`)
3. For each affected bot, state what you verified
4. Provide post-deploy journalctl verification commands

### Zero Bleed Guarantee for Esports Changes

Esports changes should ONLY touch esports-scoped files. No shared modules. The 6 files in the esports blast radius: `esports/models/esports_trainer.py`, `esports/data/esports_data_collector.py`, `esports/data/esports_db.py`, `bots/esports_bot.py`, `bots/esports_series_bot.py`, `bots/esports_live_bot.py`, `config/settings.py` (esports section only).

### Forbidden Patterns

1. **"While I'm in here" refactor** -- fix the bug, only the bug
2. **Band-aid fix** -- `try/except` that hides the real error
3. **Shotgun fix** -- changed 4 things hoping one works
4. **Scope creep** -- "You asked me to fix X but I noticed Y"
5. **Silent migration** -- changing a DB column without updating every consumer
6. **Optimistic rewrite** -- old module handled 47 edge cases, your rewrite handles 12

### Config Tuning Protocol

**Tier 1** (threshold tuning): State what changed, why, expected impact.
**Tier 2** (trade-universe gating): State what trades are now blocked/allowed. Provide rollback.
**Tier 3** (code changes): Full blast-radius protocol.

---

## 3. COMPLETE SESSION HISTORY (Sessions 62-70)

### Session 62-63: Root Fixes + 8-Game Expansion

**Commit**: `92a87fd` (8 files, +566/-97)

**ROOT CAUSE FIX -- Glicko-2 Outcome Inversion (Bug A)**:
- `esports/models/glicko2.py` -- `outcome == 0` was treated as team_a win. Fixed to `outcome == 1`.
- This single bug was making the bot bet BACKWARDS on every prediction. Root cause of the 14% win rate.

**Bug B: _clean_team_names() too aggressive**:
- Now strips game prefixes + tournament suffixes.
- Fuzzy match changed to longest-first unidirectional (prevents "t1" matching "fnatic").

**Bug C: LoL/CS2 min-sample thresholds were swapped**:
- LoL threshold was 100, CS2 was 50 -- they were reversed.

**8-Game Expansion**:
- Added Dota 2, Valorant, CoD, R6, StarCraft 2, Rocket League alongside existing LoL + CS2.
- Generic processor `_process_generic_match()` added to `esports_data_collector.py`.

**Confluence Rewrite**:
- Removed whale/orderbook signals (always returned 0.5 -- no real data source).
- Reweighted: model 55% / freshness 30% / agreement 15%.

**Glicko-2 DB Persistence**:
- Fast-path load from `glicko2_ratings` DB table on startup.
- 914 teams loaded across 8 games.

**E4 Monitoring**:
- 10-minute Brier score check per game.
- Brier > 0.25 logs warning.
- Brier > 0.30 halts trading for that game.

**E5 Bayesian Prior**:
- phi-based blending toward 0.50 (market prior).
- phi >= 350 -> 80% prior, 200-350 -> 50%, 100-200 -> 20%, < 100 -> 0%.
- In `_get_glicko2_prediction()`.

**Session 63**: Full handoff doc (852 lines) at `AGENT_HANDOFF_ESPORTS_SESSION63_2026_03_08.md`.

---

### Session 64: Deep-Dive Tiers 2+3

**Tier 2 Items (6)**:
- **W3+W5**: S-T multi-bucket Kelly sizing for temperature laddering (WeatherBot)
- **E5**: Bayesian prior (phi-based blending: phi>=350 -> 80% prior, <100 -> 0%)
- **W7**: Baker-McHale `k*=1/(1+sigma^2)` where sigma=model_spread/3.0 (WeatherBot)
- **E7**: Cross-game XGBoost -- `train_cross_game()` in `esports_trainer.py` -- pools all 8 games with `game_id` categorical feature
- **pymc+nutpie** added to requirements

**Tier 3 Items (3)**:
- **W6**: Dynamic Kelly graduation (100+ resolved + MSE<9 -> 0.35, 200+ + MSE<4 -> 0.50) (WeatherBot)
- **W8**: CRPS scoring via `_compute_crps()` (Ferro 2014 fair CRPS) (WeatherBot)
- **Edge decay**: `analyze_edge_decay()` in `esports_db.py`

Tests: 1242 passed. Tier 4 deferred (W9/W10/E8 blocked or 500+ LOC).

---

### Session 65: P1+P2 Bug Fixes + Data Pipeline

**7 Commits**: `8662001` through `418f422`

**Commit 1** (`8662001`): Wire `train_cross_game()` into EsportsBot training flow
- Was defined in `esports_trainer.py` but never called from `esports_bot.py`.
- Added invocation in the background training cycle.

**Commit 2** (`7f01f56`): PandaScore CoD slug fix
- `cod-mw` -> `codmw` (was returning 404 for all CoD requests).
- All 8 slugs tested against live PandaScore API and confirmed 200 OK.

**Commit 3** (`332bf69`): RiotApiClient import name fix
- Code imported `RiotAPIClient` (uppercase PI) but class is `RiotApiClient` (lowercase pi).
- Import was inside try/except so it silently failed. Riot API never initialized on VPS even with key set.

**Commit 4** (`be1f137`): Seed script creation
- `scripts/seed_esports_data.py` (85 lines) -- one-shot historical data collection for 6 new games.

**Commit 5** (`f0e1157`): PandaScore date range fix (chain bug 1/3)
- `range[scheduled_at]` sent as `"{since},"` (trailing comma, no end date). PandaScore returned 400 Bad Request on ALL past match requests for ALL games. Fixed to `"{since},{until}"`.

**Commit 6** (`d2477a8`): asyncpg JSONB cast fix (chain bug 2/3)
- SQL used `:param::jsonb`. asyncpg misparses `::` as part of bind param name. All INSERT statements for training data failed. Fixed to `CAST(:param AS jsonb)`.

**Commit 7** (`418f422`): asyncpg datetime fix (chain bug 3/3)
- `scheduled_at` column passed as ISO 8601 string. asyncpg requires native Python `datetime` objects. All INSERTs failed with type error. Fixed with `fromisoformat()`.

**Chain Failure**: Bugs 5, 6, and 7 were a cascading chain. All three had to be fixed before ANY training data could be stored for the 6 new games. Each bug was discovered only after fixing the previous one.

**RIOT_API_KEY deployed**: `RGAPI-5f245329-29d0-4e43-87d6-e5c3d8c19b05` (PRODUCTION key, App ID 809100, 20 req/s, does NOT expire).

**Seed data**: P1.1 was blocked by PandaScore rate limit (1000 req/hr exhausted across 3 failed runs). Later completed after rate limit reset.

---

### Sessions 66-67: P0-P3 Bug Fixes + Feature Implementation

These sessions covered a full 16-item plan across P0 (critical bugs) through P3 (enrichment APIs).

**P0 Bug Fixes (5 critical bugs)**:

| Bug | SHA | Location | Root Cause | Fix |
|-----|-----|----------|-----------|-----|
| P0.1 | `0cc5761` | `esports_db.py` | `get_team_map_rates()` had COUNT(*) aggregates but no GROUP BY map_name | Added GROUP BY, moved LIMIT to subquery |
| P0.2 | `0cc5761` | `esports_db.py` | `compute_clv_stats()` used `INTERVAL ':days days'` -- param inside string literal, never substituted | Changed to `MAKE_INTERVAL(days => :days)` |
| P0.3 | `0cc5761` | `esports_db.py` | `analyze_edge_decay()` used f-string SQL injection: `INTERVAL '{days} days'` | Parameterized with MAKE_INTERVAL |
| P0.4 | `6e6f847` | `esports_live_bot.py` | `_on_bg_task_done()` logged failure but never restarted game monitor. Bot appeared running but generated zero signals after crash. | Added `_restart_monitor()` with exponential backoff (2s -> 60s cap) |
| P0.5 | `bdae419` | `esports_series_bot.py` | `_series_prediction_cache` grew unbounded -- never pruned | Added TTL eviction (>30min) at start of `scan_and_trade()` |

**P1 Performance (3 items)**:

| Item | SHA | Change |
|------|-----|--------|
| P1.1 Calibration cache | `abbc159` | 1-hour TTL cache in EsportsBankrollManager. Prevents hundreds of DB round-trips/hour. |
| P1.2 Background training | `ce70782` | Training runs in background async task (`_bg_train_tasks` dict). No longer blocks scan loop. |
| P1.3 Map rates cache | `3771099` | Module-level TTL cache (60s) for `get_team_map_rates()`. |

**P2 Features (4 items)**:

| Item | SHA | Change |
|------|-----|--------|
| P2.1 Kelly graduation | `abbc159` | 50+ resolved + Brier<0.25 -> kelly 0.35. 100+ resolved + Brier<0.20 -> kelly 0.50. Auto-downgrades. |
| P2.2 Exposure tracking | `ce70782` | `_game_exposure`, `_tournament_exposure`, `_team_exposure` dicts in EsportsBot. Settings: MAX_GAME=300, MAX_TOURNAMENT=200, MAX_TEAM=150. |
| P2.3 S-T series Kelly | `f7393aa` | `_smoczynski_tomkins_allocate()` in EsportsSeriesBot. When >=2 live series: pro-rata by Kelly edge, capped at group budget. |
| P2.4 Tournament phase boost | `ce70782` | `serie_type` -> confidence multiplier: group_stage=0.85x, bracket=1.0x, finals=1.15x. |

**P3 External API Clients (4 items)**:

| Item | SHA | File | Purpose | Key Required |
|------|-----|------|---------|-------------|
| P3.1 OpenDota | `fa3dc75` | `esports/data/opendota_client.py` | Dota2 hero win rates, form adjustment | None (free, no auth) |
| P3.2 Aligulac | `14a6238` | `esports/data/aligulac_client.py` | SC2 Elo ratings, 50/50 Glicko-2 blend | `ALIGULAC_API_KEY` (NOT set on VPS) |
| P3.3 OddsPapi | `667efa7` | `esports/data/oddspapi_client.py` | Pinnacle closing lines for CLV benchmarking | `ODDSPAPI_API_KEY` (NOT set on VPS) |
| P3.4 Ballchasing | `a9ed7df` | `esports/data/ballchasing_client.py` | RL replay stats (boost, accuracy, positioning) | `BALLCHASING_API_KEY` (NOT set on VPS) |

**Additional fixes in this period**:

| SHA | Fix |
|-----|-----|
| `1a2ece9` | Cross-game XGBoost save path fix (was saving relative, systemd read-only); wired OddsPapi CLV + Ballchasing RL into prediction path |
| `cb2e8df` | Backfill CS2 `map_name` via ON CONFLICT DO UPDATE (ready for when paid PandaScore provides map names) |
| `ebcdf84` | Restore HLTV fallback for CS2 map veto when DB query returns {} (PandaScore free tier never provides map names) |

**HLTV Scraper Implementation** (`08ae2db`):
- `esports/data/hltv_scraper.py` -- real web scraping for CS2 team data.
- `get_map_win_rates(team_name)` scrapes hltv.org map stats.
- Rate-limited (10s between requests).
- Falls back to `{m: 0.50 for m in CS2_MAP_POOL}` on any failure.

**Dota2 + Valorant Models** (`05fe84e`):
- `esports/models/dota2_model.py` (180 lines) -- XGBoost with 6 Glicko-2 features.
- `esports/models/valorant_model.py` (180 lines) -- Same feature set.
- Both saved to `saved_models/{game}_xgb.json`.

**P&L Monitoring + Map Veto Fix** (`049d88b`):
- `compute_pnl_summary()` in `esports_db.py` -- queries `paper_trades WHERE bot_name LIKE 'Esports%'`, groups by game.
- Logged as `esportsbot_pnl_summary` every 10 minutes.
- Map veto `series_prob_with_map_veto()` call fixed -- now passes correct `veto_order` parameter.

---

### Session 68 (P4): Performance + Config Extraction

**Commit**: `16eb8de` (8 files, +156/-51)

All 10 P4 items completed in a single commit.

**P4.1-A: Cross-game XGBoost wired into prediction path**
- Was loaded at startup but NOT used during prediction for games without dedicated models.
- Now: 40% XGB + 60% Glicko-2 blend for CoD, R6, SC2, RL (and any game with Glicko-2 data).

**P4.1-B: Collection retry**
- `_collection_attempted` changed from set to Dict with max 3 retries (was one-shot).

**P4.1-C: OddsPapi asyncio.Lock**
- Added lock on rate limiter to fix race condition.

**P4.1-D: Exception visibility**
- 5 `except Exception: pass` blocks upgraded to `logger.warning()`.
- Affected: OpenDota init, Aligulac init, Ballchasing init, accuracy gate, Glicko-2 fallback.

**P4.2: Config extraction**
- 22 new `ESPORTS_*` settings added to `settings.py`.
- All hardcoded values (confluence weights, freshness decay, live trigger caps, monitor backoff, training thresholds) now configurable via env vars.

**P4.3-A: Freshness decay separation**
- Pre-game: 600s decay (was zeroed at 5 minutes -- too aggressive).
- Live: 120s decay (matches need fast reactions).

**P4.3-B: Live bot queue drain**
- Max events per scan: 20 -> 50.
- Stale event filtering: events older than 60s are skipped.

**VPS verification post-deploy**:
- `cross_game_xgb loaded` confirmed.
- All 3 API clients initialized.
- 8 Glicko-2 games loaded (914 teams).
- First scan: 2.2s, background retrain for cross_game+lol+cs2 all complete.
- No new WARNING from upgraded exception handlers = no hidden failures.

---

### Session 69: Boot Blocker Fix + MirrorBot (non-esports)

**WeatherBot boot blocker** (`65abd0e`):
- `market_mapper.py` had 6 classes + grouping methods in working tree but NEVER committed.
- `precipitation_engine.py` was untracked (new file since Session 66, never committed).
- `ImportError: cannot import name 'PrecipitationMarketGroup'` on any clean import.
- Fix: committed both files.

**Critical finding**: VPS is NOT git-based. Files are deployed via scp. Unstaged working tree changes may be critical uncommitted work not in git history. Always read uncommitted changes before assuming git = source of truth.

Session 69 also included MirrorBot daily cap fix (unrelated to esports).

---

### Session 70 (P5): Training Data Quality + Smart Retraining + Phase Calibration

**Commit**: `5e66431`, deployed PID 1774460

**Plan file**: `C:\Users\samwa\.claude\plans\stateless-chasing-treasure.md`

**Commit 1 -- Filter polluted training rows**
- **File**: `esports/models/esports_trainer.py`
- **Bug**: `train_cross_game()` was training on rows where `team_strength_diff==0.0` AND `matchup_uncertainty==0.0`. These rows mean "Glicko-2 data unavailable" (never seen team), NOT "teams are evenly matched". Real even matches have non-zero `matchup_uncertainty` from phi values.
- **Fix**: Added filter BEFORE temporal split in `train_cross_game()`:
  ```python
  pooled = [r for r in pooled if not (
      float(r.get("team_strength_diff", 0.0)) == 0.0
      and float(r.get("matchup_uncertainty", 0.0)) == 0.0
  )]
  ```
- **Result**: 892 rows filtered (11049 -> 10157) on first retrain after deploy.
- **Critical**: Filter placed BEFORE temporal split. Placing after would be wrong because `train_set`/`val_set` are already computed.

**Commit 2 -- Skip PandaScore team_stats for non-LoL/CS2**
- **File**: `esports/data/esports_data_collector.py`
- **Bug**: `_get_team_strength()` called PandaScore team_stats API for all 8 games. Free tier returns 403 for non-LoL/CS2, wasting ~700 API calls per seed run (0.5s each = 350s wasted).
- **Fix**: Early return `if game not in ("lol", "cs2"): return 0.5` at top of `_get_team_strength()`.
- **Behavior**: Identical outcomes (403 catch path already defaulted to 0.5), just faster.

**Commit 3 -- Prune ended series from EsportsSeriesBot**
- **File**: `bots/esports_series_bot.py`
- **Bug**: `_active_series` dict grew unbounded. `_refresh_series()` added new series from PandaScore but never removed ones that ended.
- **Fix**: After fetching live matches, compute `stale = set(self._active_series) - set(new_series)`, log, and prune.

**Commit 4 -- Smart retraining triggers**
- **Files**: `esports/models/esports_trainer.py`, `bots/esports_bot.py`
- **Gap**: 24h fixed retraining interval regardless of accuracy degradation, patch changes, or data volume.
- **Trainer side changes**:
  - `__init__` extended with: `_last_train_brier` (dict), `_last_train_row_count` (dict), `_last_train_patch` (dict), `_min_retrain_interval=7200.0` (2h minimum cooldown)
  - `needs_retrain()` extended with keyword params: `current_brier`, `current_row_count`, `current_patch`, `recent_loss_streak`
  - 5 triggers (any fires -> retrain):
    1. **2h minimum cooldown** -- hard floor to prevent spam
    2. **24h interval** -- original behavior (always fires after 24h)
    3. **Brier degradation >0.05** -- model accuracy declining since last train
    4. **Data volume >=50 rows** -- enough new data to justify retrain
    5. **Patch change (LoL only)** -- game meta shifted
    6. **Loss streak >=4** -- something is wrong, retrain
  - State stored at training time: `_last_train_brier[game]`, `_last_train_row_count[game]`, `_last_train_patch[game]`
- **Bot side changes**:
  - Pre-computes `_smart_brier` (from `get_rolling_accuracy`), `_smart_row_count` (COUNT query), `_lol_patch` (from `patch_drift._known_patches`)
  - Passes to `needs_retrain()` via keyword args
  - All queries wrapped in try/except -- smart triggers degrade gracefully to 24h interval if any query fails

**Commit 5 -- Tournament phase calibration tracking**
- **Files**: `esports/data/esports_db.py`, `bots/esports_bot.py`, `config/settings.py`
- **DB changes**:
  - Added `tournament_phase` column to `esports_prediction_log` via `ALTER TABLE ADD COLUMN IF NOT EXISTS`.
  - One-shot flag `_phase_column_ensured` prevents repeated ALTER TABLE calls.
  - New function `_ensure_phase_column(db)` -- runs once, silently succeeds if column exists.
- **New DB function**: `get_phase_accuracy(db, game, phase)` -- computes Brier per tournament phase from resolved predictions. Returns `{total, accuracy, brier_score}`.
- **Phase detection**: `_detect_tournament_phase()` static method -- returns `"group"`, `"bracket"`, `"finals"`, or `"unknown"` from `serie_type` or question text.
- **Phase calibration**: `_get_tournament_phase_mult()` changed from `@staticmethod` to async method with auto-calibration:
  - Static multipliers: group=0.85, bracket=1.0, finals=1.15, unknown=1.0
  - When >=20 resolved trades exist for a phase: blend 70% static + 30% calibrated
  - Calibrated mult formula: `1.0 + (0.25 - brier) * 2.0`, capped [0.70, 1.30]
- **Settings**: `ESPORTS_TOURNAMENT_PHASE_MIN_SAMPLES=20` added.
- **Logging**: `tournament_phase` logged with every prediction via `log_prediction()`.

---

## 4. TRAINING DATA QUALITY

### DB Numbers (from VPS query)

| Game | Total Rows | Missing Glicko-2 | % Polluted |
|------|-----------|-------------------|-----------|
| valorant | 182 | 115 | **63%** |
| r6 | 676 | 241 | **36%** |
| rl | 722 | 235 | **33%** |
| lol | 1953 | 583 | **30%** |
| sc2 | 550 | 111 | **20%** |
| cs2 | 4145 | 795 | **19%** |
| dota2 | 2454 | 392 | **16%** |
| cod | 367 | 37 | **10%** |

**After P5 filter**: 11049 -> 10157 rows (892 dropped from cross-game training).

### What "Missing Glicko-2" Means

Rows where `team_strength_diff == 0.0` AND `matchup_uncertainty == 0.0`. This happens when the Glicko-2 tracker has never seen either team (no prior matches processed). The zero values encode "unknown", not "even match". Real even matches always have non-zero `matchup_uncertainty` from their phi (rating deviation) values.

### Impact on Models

Before P5: Cross-game XGB trained on all 11049 rows, treating 892 "unknown" rows as "teams are perfectly even" -- a false signal that biases the model toward 50/50 predictions.

After P5: These rows are filtered BEFORE the temporal train/val split. Models retrain with cleaner data on every scan cycle.

---

## 5. ARCHITECTURE DEEP-DIVE

### EsportsBot (`bots/esports_bot.py`, 1850 lines)

The main trading bot. Handles pre-game predictions and live in-play trading across all 8 game titles.

**`__init__` key state**:
- `_pandascore` / `_patch_drift` / `_market_scanner` / `_market_service` -- data + market clients
- `_lol_model` / `_cs2_model` / `_dota2_model` / `_valorant_model` -- dedicated ML models
- `_cross_game_model` -- XGBClassifier from `saved_models/cross_game_xgb.json`
- `_trainer` -- `EsportsModelTrainer` for background retraining
- `_opendota` / `_aligulac` / `_ballchasing` -- external data enrichment clients
- `_bg_train_tasks` -- Dict[str, asyncio.Task] for background training
- `_game_exposure` / `_tournament_exposure` / `_team_exposure` -- USD tracking
- `_live_matches` -- Dict of active PandaScore matches
- `_prediction_cache` / `_market_token_map` -- WS reactive path state
- `_collection_attempted` -- Dict[str, int] with max 3 retries
- `_monitoring_halted_games` -- set of games halted by E4 Brier check

**`scan_and_trade()` flow**:
1. `_check_monitoring_thresholds()` -- E4 Brier alerts (10min cycle)
2. Auto-retrain with smart triggers (Commit 4 of P5):
   - Pre-compute `_smart_brier`, `_smart_row_count`, `_lol_patch`
   - Call `trainer.needs_retrain(game, current_brier=..., current_row_count=..., current_patch=..., recent_loss_streak=...)`
   - If triggered, launch background training task
3. Collect historical data for missing games (one-shot, max 3 retries)
4. Rolling accuracy check -- auto-disable below threshold
5. Patch drift check (LoL only, via Riot API)
6. Refresh live matches from PandaScore
7. Get esports markets via `EsportsMarketService`
8. `analyze_opportunity()` for each market -> `_execute_esports_trade()`

**`analyze_opportunity()` flow**:
1. Validate price, detect game, check halted/exposure/observation period
2. `_get_model_prediction()`:
   - Tries dedicated ML model first (LoL/CS2/Dota2/Valorant)
   - Falls back to Glicko-2 heuristic with cross-game XGB blend (40% XGB + 60% Glicko-2)
3. Edge validation (YES/NO sides)
4. Tournament phase detection + confidence multiplier (static or auto-calibrated)
5. Log prediction with phase via `log_prediction()`
6. Confluence gate (3 factors: edge 55%, freshness 30%, agreement 15%)

**WS reactive path**: `on_price_update()` for real-time price reactions -- uses `_prediction_cache` + `_market_token_map` to process only esports markets (skips all 26K markets via early exit).

**Models loaded at startup**:
- `LoLWinModel` -- saved at `data/esports_lol_model.pkl`
- `CS2EconomyModel` -- saved at `data/esports_cs2_economy_model.pkl`
- `Dota2Model` -- saved at `saved_models/dota2_xgb.json`
- `ValorantModel` -- saved at `saved_models/valorant_xgb.json`
- Cross-game XGB -- saved at `saved_models/cross_game_xgb.json`

### EsportsLiveBot (`bots/esports_live_bot.py`, 298 lines)

Lightweight live match monitor. Receives `EsportsGameState` updates from `EsportsGameMonitor` and converts detected `EsportsLiveEvent`s into bet placements via `EsportsLiveTrigger`.

**Architecture**:
- `_game_update_queue` -- asyncio.Queue(maxsize=200)
- `_game_monitor` -- `EsportsGameMonitor` runs as background task
- `_event_detector` -- `EsportsEventDetector` classifies game state changes
- `_live_trigger` -- `EsportsLiveTrigger` enforces cooldowns + caps + places orders
- `_monitor_task` -- auto-restarts on failure with exponential backoff (2s -> 60s cap)

**`scan_and_trade()`**: Drains queue up to `ESPORTS_LIVE_MAX_EVENTS_PER_SCAN` (50), skips events older than `ESPORTS_LIVE_EVENT_MAX_AGE_SECONDS` (60s), processes via event detector + live trigger.

### EsportsSeriesBot (`bots/esports_series_bot.py`, 695 lines)

Series-level tracking for BO3/BO5 matches. Exploits momentum fallacy, map veto ignorance, and conditional probability errors.

**Key methods**:
- `_refresh_series()` -- fetches live BO3+ series from PandaScore, prunes stale ones (Commit 3 of P5)
- `_analyze_series()` -- computes conditional series probability:
  - DB-first: `get_team_map_rates()` queries CS2 training data for map win rates
  - HLTV fallback: `get_map_win_rates()` when DB returns {} (always on free tier)
  - `_derive_veto_order()` builds plausible veto from team map preferences
  - `series_prob_with_map_veto()` computes conditional probability with correct `veto_order` param
  - Falls back to `_simple_series_prob()` (uniform 0.50 game win rate) when no map data
- `_smoczynski_tomkins_allocate()` -- S-T Kelly for >=2 concurrent series bets
- `_execute_series_trade()` -- uses own `EsportsBankrollManager` with optional S-T size override

### EsportsModelTrainer (`esports/models/esports_trainer.py`, 744 lines)

Orchestrates training for all 8 games.

**Key methods**:
- `train_game(game, db)` -- per-game training pipeline:
  1. Load training data from DB
  2. Collect from PandaScore if insufficient
  3. Temporal split (oldest = train, newest = validation)
  4. Route to `_train_lol()`, `_train_cs2()`, `_train_dota2()`, `_train_valorant()`, or return early for cod/r6/sc2/rl (Glicko-2 only)
  5. Evaluate accuracy, Brier, ECE
  6. Save if graduated (always graduates now -- user controls go-live)
  7. Store smart retrain state: `_last_train_brier`, `_last_train_row_count`, `_last_train_patch`

- `train_cross_game(db)` -- E7 cross-game XGBoost:
  1. Pool all 8 games from DB
  2. **Filter polluted rows** (P5 Commit 1): drop where `team_strength_diff==0.0 AND matchup_uncertainty==0.0`
  3. Temporal split (oldest = train, newest = validation)
  4. Extract features: `team_strength_diff, matchup_uncertainty, rd_asymmetry, team_a_volatility, team_b_volatility, game_id, best_of`
  5. XGBoost: n_estimators=200, max_depth=4, learning_rate=0.05
  6. Save to `saved_models/cross_game_xgb.json`

- `needs_retrain(game, *, current_brier, current_row_count, current_patch, recent_loss_streak)` -- smart triggers (P5 Commit 4):
  1. Hard minimum: 2h cooldown (`_min_retrain_interval=7200.0`)
  2. 24h interval (original)
  3. Brier degradation >0.05
  4. Data volume >=50 rows
  5. Patch change (LoL only)
  6. Loss streak >=4

### EsportsDataCollector (`esports/data/esports_data_collector.py`, 623 lines)

PandaScore API client wrapper for historical data collection.

**Key methods**:
- `collect_historical(game, days_back, db)` -- fetches completed matches, processes into training rows, stores in DB
- `_process_lol_match()` -- 1 row per game in series, neutral in-game features (gold/towers/dragons = 0.5/0.0/0.0), Glicko-2 metadata features are real
- `_process_cs2_match()` -- 1 row per map, neutral economy defaults, `map_name` from PandaScore game data
- `_process_generic_match()` -- for dota2/valorant/cod/r6/sc2/rl, stores Glicko-2 metadata + outcome
- `_get_team_strength()` -- **early return for non-LoL/CS2** (P5 Commit 2), PandaScore team_stats for LoL/CS2 only
- `_store_row()` -- INSERT with `CAST(:game_state_json AS jsonb)`, `ON CONFLICT DO UPDATE` for map_name backfill
- `get_training_data(db, game)` -- loads from DB, neutralizes label-leaked features from old data

### EsportsDB (`esports/data/esports_db.py`, 798 lines)

Database helper functions.

**Key functions**:
- `upsert_esports_team()` / `upsert_esports_match()` -- standard upserts
- `log_prediction(db, ..., tournament_phase="")` -- logs to `esports_prediction_log` with phase (P5 Commit 5)
- `_ensure_phase_column(db)` -- one-shot ALTER TABLE IF NOT EXISTS for `tournament_phase` column
- `resolve_predictions(db, market_id, outcome)` -- backfill `actual_outcome` for resolved markets
- `get_rolling_accuracy(db, game, bot_name, last_n)` -- rolling accuracy + Brier from resolved predictions
- `get_phase_accuracy(db, game, phase)` -- Brier per tournament phase (P5 Commit 5)
- `compute_clv_stats(db, game, days)` -- Closing Line Value stats (requires `closing_price` in DB)
- `analyze_edge_decay(db, game, days, n_bins)` -- prediction edge vs resolution time analysis
- `compute_pnl_summary(db)` -- P&L grouped by game, joins paper_trades with esports_prediction_log
- `get_team_map_rates(db, team_name, game, last_n)` -- per-map win rates with 60s TTL cache
- `update_calibration()` / `get_calibration()` -- calibration data CRUD
- `backfill_pinnacle_closing_lines(db, oddspapi_client)` -- OddsPapi CLV backfill

### Data Flow

```
PandaScore API ----> esports_data_collector.py ----> esports_training_data (DB)
                                                \--> glicko2_ratings (DB)
                                                \--> Glicko-2 in-memory ratings

Riot API ----------> riot_api_client.py ---------> patch_drift.py (LoL patch detection)

HLTV (scraped) ----> hltv_scraper.py ------------> esports_series_bot.py (CS2 map veto)

OpenDota ----------> opendota_client.py ---------> EsportsBot (Dota2 form adjustment)
Aligulac ----------> aligulac_client.py ---------> EsportsBot (SC2 Elo blend)
OddsPapi ----------> oddspapi_client.py ---------> esports_db.py (CLV backfill)
Ballchasing -------> ballchasing_client.py ------> EsportsBot (RL replay stats)

esports_training_data --> esports_trainer.py ----> lol_win_model / cs2_economy_model
                                               \-> dota2_model / valorant_model
                                               \-> saved_models/cross_game_xgb.json

Polymarket --------> esports_market_scanner.py ---> esports_market_service.py ---> EsportsBot
```

### Prediction Flow (EsportsBot)

```
scan_and_trade()
  |-> _check_monitoring_thresholds()          # E4 Brier alerts (10min)
  |-> Smart retrain triggers (P5)             # Brier/rows/patch/loss_streak
  |-> Collect historical if missing           # PandaScore, max 3 retries
  |-> Rolling accuracy check                  # Auto-disable below threshold
  |-> Patch drift check                       # LoL only, via Riot API
  |-> Refresh live matches                    # PandaScore
  |-> Get esports markets                     # EsportsMarketService
  |-> for each market:
  |     |-> analyze_opportunity()
  |     |     |-> Validate price, detect game
  |     |     |-> _get_model_prediction()     # ML model or Glicko-2 + XGB blend
  |     |     |-> Edge validation (YES/NO)
  |     |     |-> _detect_tournament_phase()  # group/bracket/finals/unknown
  |     |     |-> _get_tournament_phase_mult() # static or auto-calibrated
  |     |     |-> log_prediction(phase=...)   # With tournament_phase
  |     |     |-> Confluence gate             # edge 55% + freshness 30% + agreement 15%
  |     |-> _execute_esports_trade()
```

---

## 6. KEY PATTERNS / TRAPS (CRITICAL -- memorize these)

### Universal Code Patterns

- **BUY/SELL vs YES/NO**: `BaseBot.place_order()` expects `side="YES"` or `side="NO"`. NEVER pass `"BUY"` or `"SELL"`.
- **PSEUDO_LABEL_ENABLED=false**: DO NOT enable. Only Location 1 (market resolution) labels are correct.
- **ENSEMBLE_BLEND=1.0**: Bypasses learning_conf.
- **Database pattern**: `db.get_session()` -> `async with ... as session:` -> `session.execute(text(...))`
- **asyncpg JSONB**: Use `CAST(:x AS jsonb)` NOT `:x::jsonb`. asyncpg misparses `::` as part of param name.
- **asyncpg datetime**: Timestamp columns need `datetime.fromisoformat()`, not ISO strings.
- **Polymarket category tagging unreliable**: Use keyword matching, not category filter.
- **CLOB markets have volume=0**: Don't use volume gates.
- **websockets.exceptions** must be imported explicitly (v15 lazy-loads).
- **paper_trades schema**: `bot_name` column. **positions schema**: `bot_id` column.
- **BOT_REGISTRY has 14 bots**. MomentumBot DELETED, EnsembleBot ARCHIVED.

### Esports-Specific Traps

- **Glicko-2 outcome**: `outcome == 1` means team_a wins. This was INVERTED before Session 62 -- ROOT CAUSE of 14% win rate.
- **Glicko-2 filter**: `team_strength_diff==0` AND `matchup_uncertainty==0` means MISSING DATA, not an even match. Real even matches have non-zero matchup_uncertainty from phi values. P5 Commit 1 filters these during training.
- **PandaScore date ranges**: `range[scheduled_at]` needs BOTH start AND end: `"{since},{until}"`. Missing end date returns 400.
- **PandaScore team_stats**: Returns 403 for non-LoL/CS2 on free tier. P5 Commit 2 skips these.
- **PandaScore CoD slug**: `codmw` (not `cod-mw`). All 8 slugs tested and confirmed.
- **RIOT_API_KEY**: PRODUCTION key (App ID 809100). 20 req/s, 100 req/2min. Does NOT expire. LoL-focused.
- **Import names**: `RiotApiClient` (lowercase "pi"), NOT `RiotAPIClient`. Silent failures inside try/except.
- **_clean_team_names()**: Strips game prefixes + tournament suffixes. Fuzzy match is longest-first unidirectional (prevents "t1" matching "fnatic").
- **Confluence weights**: model 55% / freshness 30% / agreement 15%. Whale/orderbook removed (always 0.5).
- **Bayesian prior**: phi >= 350 -> 80% prior toward 0.50, 200-350 -> 50%, 100-200 -> 20%, < 100 -> 0%. In `_get_glicko2_prediction()`.
- **Cross-game XGBoost**: `train_cross_game()` pools all 8 games with game_id feature. Saved to `saved_models/cross_game_xgb.json`. Loaded at startup + after retrain. Used in Glicko-2 fallback: 40% XGB + 60% Glicko-2 blend (CoD/R6/SC2/RL + any game with Glicko-2 data).
- **E4 monitoring**: 10min Brier check. >0.25 warn, >0.30 halt trading for that game.
- **Map veto never uses DB data**: PandaScore free tier never provides per-game map names. All 4,145 CS2 rows have `map_ct_rate=0.5`, `map_name=''`. HLTV scraper is the active fallback for map veto.
- **Tournament phase column**: Added via ALTER TABLE IF NOT EXISTS, one-shot flag `_phase_column_ensured`. Phase logged with every prediction.
- **Smart retrain cooldown**: 2h minimum (`_min_retrain_interval=7200.0`). Prevents spam.
- **Freshness decay**: Pre-game 600s vs live 120s. Configurable via `ESPORTS_FRESHNESS_DECAY_PREGAME_SECONDS` / `ESPORTS_FRESHNESS_DECAY_SECONDS`.
- **Background training**: Non-blocking via `_bg_train_tasks` dict. 200s timeout per game.
- **Series prediction cache**: 30min TTL, pruned at start of each `scan_and_trade()`.
- **Active series pruning**: Stale series (not in PandaScore live feed) are pruned in `_refresh_series()`.
- **Collection retries**: Max 3 per game (`_collection_attempted` dict), not one-shot.

### Database Credentials

| Key | Value |
|-----|-------|
| DB password | `polymarket_s46` |
| Redis password | `78psiRhepTgrmWSoy3cgNEIr` |

### API Keys

| Key | Value | Notes |
|-----|-------|-------|
| `PANDASCORE_API_KEY` | `-JttfhX0NAapsJ8fRb14QT46Jl43e7z68-_27mrMoTnO0S5tR_Y` | Free tier, 1000 req/hr |
| `RIOT_API_KEY` | `RGAPI-5f245329-29d0-4e43-87d6-e5c3d8c19b05` | PRODUCTION, App ID 809100, 20 req/s, non-expiring |
| `ALIGULAC_API_KEY` | NOT SET on VPS | Client skips gracefully |
| `ODDSPAPI_API_KEY` | NOT SET on VPS | Client skips gracefully |
| `BALLCHASING_API_KEY` | NOT SET on VPS | Client skips gracefully |

### PandaScore Slug Mapping

```python
GAME_SLUGS = {
    "lol": "lol",
    "cs2": "csgo",
    "dota2": "dota2",
    "valorant": "valorant",
    "cod": "codmw",           # Fixed from "cod-mw" in Session 65
    "r6": "r6siege",
    "sc2": "starcraft-2",
    "rl": "rl",
}
```

---

## 7. SETTINGS REFERENCE (esports-specific in `config/settings.py`)

### Bot Enable Flags

| Setting | Default | Description |
|---------|---------|-------------|
| `BOT_ENABLED_ESPORTS` | `false` | Master enable for EsportsBot |
| `BOT_ENABLED_ESPORTS_LIVE` | `false` | Master enable for EsportsLiveBot |
| `BOT_ENABLED_ESPORTS_SERIES` | `false` | Master enable for EsportsSeriesBot |

### Scan Intervals

| Setting | Default | Description |
|---------|---------|-------------|
| `SCAN_INTERVAL_ESPORTS` | `120` (seconds) | EsportsBot pre-game scan interval |
| `SCAN_INTERVAL_ESPORTS_LIVE` | `10` (seconds) | EsportsBot during live matches / EsportsLiveBot active |
| `SCAN_INTERVAL_ESPORTS_SERIES` | `30` (seconds) | EsportsSeriesBot during active series |

### Edge / Confidence Thresholds

| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_MIN_EDGE` | `0.08` | Minimum edge to trade |
| `ESPORTS_MIN_CONFIDENCE` | `0.55` | Minimum confidence to trade |
| `ESPORTS_SERIES_MIN_EDGE` | `0.10` | Minimum edge for series trades |
| `ESPORTS_SERIES_REVERSE_SWEEP_FLOOR` | `0.05` | Floor for reverse sweep pricing |

### Bankroll / Sizing

| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_TOTAL_CAPITAL` | `5000.0` | Total capital allocation for esports |
| `ESPORTS_MAX_BET_USD` | `100.0` | Max single bet |
| `ESPORTS_MAX_DAILY_USD` | `500.0` | Max daily deployment |
| `ESPORTS_KELLY_DEFAULT_FRACTION` | `0.25` | Default Kelly fraction |

### Execution

| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_MAKER_FALLBACK_TIMEOUT_S` | `3.0` | Maker order timeout before taker |
| `ESPORTS_OBSERVATION_HOURS` | `48` | Observation period before first trade |

### Model Training Pipeline

| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_MODEL_MIN_ACCURACY` | `0.55` | Graduation gate: minimum accuracy |
| `ESPORTS_MODEL_MAX_BRIER` | `0.24` | Graduation gate: maximum Brier |
| `ESPORTS_RETRAIN_INTERVAL_HOURS` | `24` | Default retrain interval |
| `ESPORTS_MIN_ACCURACY_TO_TRADE` | `0.52` | Rolling accuracy floor to keep trading |
| `ESPORTS_LOL_HEURISTIC_ENABLED` | `true` | Enable LoL heuristic fallback |
| `ESPORTS_VALIDATION_SPLIT` | `0.2` | Train/validation split ratio |
| `ESPORTS_MIN_LOL_SAMPLES` | `50` | Minimum LoL training samples |
| `ESPORTS_MIN_CS2_SAMPLES` | `100` | Minimum CS2 training samples |
| `ESPORTS_MIN_CS2_UNIQUE_MATCHES` | `15` | Minimum unique CS2 matches |
| `ESPORTS_EARLY_STOPPING_ROUNDS` | `20` | XGBoost early stopping rounds |
| `ESPORTS_TOURNAMENT_PHASE_MIN_SAMPLES` | `20` | Min samples for phase calibration (P5) |

### Exposure Limits

| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_MAX_GAME_EXPOSURE` | `300.0` | Max USD per game |
| `ESPORTS_MAX_TOURNAMENT_EXPOSURE` | `200.0` | Max USD per tournament |
| `ESPORTS_MAX_TEAM_EXPOSURE` | `150.0` | Max USD per team |

### Signal Confluence

| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_CONFLUENCE_MIN` | `0.60` | Minimum confluence score to trade |
| `ESPORTS_CONFLUENCE_WEIGHT_EDGE` | `0.55` | Edge weight in confluence |
| `ESPORTS_CONFLUENCE_WEIGHT_FRESHNESS` | `0.30` | Freshness weight in confluence |
| `ESPORTS_CONFLUENCE_WEIGHT_AGREEMENT` | `0.15` | Agreement weight in confluence |
| `ESPORTS_FRESHNESS_DECAY_SECONDS` | `120.0` | Live freshness decay (seconds) |
| `ESPORTS_FRESHNESS_DECAY_PREGAME_SECONDS` | `600.0` | Pre-game freshness decay (seconds) |
| `ESPORTS_WHALE_SMART_MONEY_THRESHOLD` | `0.60` | Smart money threshold (disabled -- no data source) |

### WebSocket Reactive

| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_WS_PRICE_CHANGE_PCT` | `0.01` | EsportsBot WS significance threshold |
| `ESPORTS_WS_COOLDOWN_SECONDS` | `10` | EsportsBot WS cooldown |
| `ESPORTS_LIVE_WS_PRICE_CHANGE_PCT` | `0.005` | EsportsLiveBot WS threshold (tighter) |
| `ESPORTS_LIVE_WS_COOLDOWN_SECONDS` | `5` | EsportsLiveBot WS cooldown |
| `ESPORTS_SERIES_WS_PRICE_CHANGE_PCT` | `0.01` | EsportsSeriesBot WS threshold |
| `ESPORTS_SERIES_WS_COOLDOWN_SECONDS` | `10` | EsportsSeriesBot WS cooldown |

### Live Trigger

| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_LIVE_COOLDOWN_SECONDS` | `60.0` | Live trigger cooldown per match |
| `ESPORTS_LIVE_MAX_PER_MATCH` | `5` | Max live bets per match |
| `ESPORTS_LIVE_MAX_PER_MAP` | `2` | Max live bets per map |
| `ESPORTS_LIVE_MAX_EVENTS_PER_SCAN` | `50` | Max events processed per scan |
| `ESPORTS_LIVE_EVENT_MAX_AGE_SECONDS` | `60.0` | Stale event cutoff |

### Game Monitor

| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_MONITOR_BASE_BACKOFF` | `30` | Monitor base backoff (seconds) |
| `ESPORTS_MONITOR_MAX_BACKOFF` | `300` | Monitor max backoff (seconds) |
| `ESPORTS_MONITOR_POLL_INTERVAL` | `15` | Monitor poll interval (seconds) |

### Latency / Refresh

| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_PANDASCORE_REFRESH_INTERVAL` | `15` | PandaScore live match refresh (seconds) |
| `ESPORTS_SERIES_REFRESH_INTERVAL` | `30` | Series refresh interval (seconds) |

### Per-Game Thresholds

| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_LOL_GOLD_DIFF_THRESHOLD` | `5000` | LoL gold diff significance threshold |
| `ESPORTS_LOL_TOWER_DIFF_THRESHOLD` | `3` | LoL tower diff significance threshold |
| `ESPORTS_CS2_ROUND_DIFF_THRESHOLD` | `5` | CS2 round diff significance threshold |
| `ESPORTS_CS2_ECONOMY_BREAK_THRESHOLD` | `10000` | CS2 economy break threshold |

### API Keys

| Setting | Default | Description |
|---------|---------|-------------|
| `PANDASCORE_API_KEY` | None (required) | PandaScore API key |
| `RIOT_API_KEY` | None (optional) | Riot Games API key |
| `ALIGULAC_API_KEY` | `""` | Aligulac SC2 API key |
| `ODDSPAPI_API_KEY` | `""` | OddsPapi (Pinnacle CLV) API key |
| `BALLCHASING_API_KEY` | `""` | Ballchasing RL replay API key |

### Deferred

| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_PINNACLE_ENABLED` | `false` | Pinnacle cross-market (Phase 2, deferred) |

---

## 8. DEPLOY PATTERN

### Standard Deploy (Windows -> VPS)

```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# 1. Run tests locally
pytest tests/ -x -q   # Must be 1333+ passing

# 2. Copy files to VPS staging (/tmp)
scp -i "$KEY" -o StrictHostKeyChecking=no "bots/esports_bot.py" "$VPS:/tmp/"
scp -i "$KEY" -o StrictHostKeyChecking=no "esports/models/esports_trainer.py" "$VPS:/tmp/"

# 3. Install to production path + set ownership
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" '
  sudo cp /tmp/esports_bot.py /opt/polymarket-ai-v2/bots/esports_bot.py
  sudo cp /tmp/esports_trainer.py /opt/polymarket-ai-v2/esports/models/esports_trainer.py
  sudo chown polymarket:polymarket /opt/polymarket-ai-v2/bots/esports_bot.py
  sudo chown polymarket:polymarket /opt/polymarket-ai-v2/esports/models/esports_trainer.py
  sudo systemctl restart polymarket-ai
'

# 4. Verify
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" '
  sudo journalctl -u polymarket-ai --since "1 min ago" --no-pager | tail -30
'
```

### Run Seed Script on VPS

```bash
ssh -i "$KEY" -o StrictHostKeyChecking=no "$VPS" '
  cd /opt/polymarket-ai-v2 && sudo -E venv/bin/python -m scripts.seed_esports_data 2>&1
'
```

### VPS Verification Commands

```bash
# Service running
sudo systemctl is-active polymarket-ai

# All bots scanning (wait 2 min after restart)
journalctl -u polymarket-ai --since "2 min ago" | grep -c "Bot\|bot"

# Esports bots specifically
journalctl -u polymarket-ai -f | grep "EsportsBot"
journalctl -u polymarket-ai -f | grep "EsportsLiveBot"
journalctl -u polymarket-ai -f | grep "EsportsSeriesBot"

# Cross-game XGB loaded
journalctl -u polymarket-ai | grep "cross_game_xgb"

# Background retrain
journalctl -u polymarket-ai -f | grep "bg retrain"

# Smart retrain triggers
journalctl -u polymarket-ai -f | grep "retrain trigger"

# Tournament phase tracking
journalctl -u polymarket-ai -f | grep "tournament_phase"

# P&L monitoring
journalctl -u polymarket-ai -f | grep "esportsbot_pnl_summary"

# Training data counts
psql -c "SELECT game, COUNT(*) FROM esports_training_data GROUP BY game;"

# Glicko-2 ratings
psql -c "SELECT game, COUNT(*) FROM glicko2_ratings GROUP BY game;"

# Prediction log
psql -c "SELECT game, COUNT(*), COUNT(actual_outcome) as resolved FROM esports_prediction_log GROUP BY game;"

# Map veto (when CS2 series occur)
journalctl -u polymarket-ai | grep "map_veto\|veto_order"
```

### Local Testing

```bash
# All tests
pytest tests/ -x -q

# Esports tests specifically
pytest tests/unit/test_esports*.py tests/unit/test_lol*.py tests/unit/test_cs2*.py -v
```

---

## 9. CURRENT STATE (Post-P5)

### Deployment

- **Commit**: `5e66431` on `master`
- **PID**: 1774460 (restarted 2026-03-09 20:12:44 UTC)
- **All 3 esports bots**: Running clean
- **Tests**: 1333 system-wide, 212 esports-specific, all passing

### Model State

| Model | Status | Last Train Stats |
|-------|--------|-----------------|
| Cross-game XGB | Trained with 10157 clean rows (892 polluted filtered) | Accuracy=0.589, Brier=0.238 |
| LoL XGBoost | Trained | From 1953 rows |
| CS2 Economy | Trained | From 4145 rows |
| Dota2 XGBoost | Trained | From 2454 rows |
| Valorant XGBoost | Trained | From 182 rows |

### Active Features

- Smart retraining: Active (Brier, row count, patch, loss streak triggers)
- Tournament phase logging: Active (column created, data accumulating)
- Glicko-2 data filter: Active (892 polluted rows excluded from cross-game training)
- Series pruning: Active (stale series cleaned up each refresh)
- PandaScore team_stats optimization: Active (non-LoL/CS2 skipped)

### Trading State

- 147+ paper trades
- ~2.7% win rate (data accumulation phase)
- No errors post-deploy
- Brier check: no games halted
- All exposure limits within bounds

### API Client State

| Client | Status | Notes |
|--------|--------|-------|
| PandaScore | Active | Free tier, 1000 req/hr |
| Riot API | Active | PRODUCTION key, non-expiring |
| OpenDota | Active | Free, no auth |
| Aligulac | Inactive | Key not set on VPS |
| OddsPapi | Inactive | Key not set on VPS |
| Ballchasing | Inactive | Key not set on VPS |
| HLTV Scraper | Active | Used for CS2 map veto fallback |

### Glicko-2 Ratings (on VPS)

```
lol      → 290 teams
cs2      → 197 teams
dota2    → 178 teams
rl       → 115 teams
valorant → 60 teams
sc2      → 42 teams
r6       → 22 teams
cod      → 12 teams
Total: 914 teams across 8 games
```

---

## 10. WHAT'S LEFT (Deferred / Future Work)

### Not Worth Doing Now

- **P3.1 Player features**: Blocked by PandaScore free tier (no per-player stats). 400-600 LOC. Uncertain ROI.
- **Whale/orderbook signals**: Infrastructure not ready. Correctly disabled (always returns 0.5).
- **CS2 economy model real features**: Blocked by paid data source. Free tier only provides match outcomes, not per-round economy.
- **HLTV scraper hardening**: Fragile but has good fallback design (returns `{m: 0.50 for m in pool}`). Not worth investing until HLTV becomes critical path.
- **CS2 `map_ct_rate=0.50`**: Root cause is PandaScore free tier missing map names. HLTV fallback possible but adds complexity for uncertain gain (model already uses shallow trees that de-weight this feature).
- **HLTV scraper for non-CS2 games**: Liquipedia API stubs exist in `hltv_scraper.py` but not wired. Low priority.

### Could Do Next (P6 Candidates)

1. **Investigate 2.7% win rate**: May need more data accumulation (only 147 trades), or edge thresholds need tuning. Check if the Brier filter (P5) improves things over the next few days.
2. **Set VPS API keys**: User action required for Aligulac, OddsPapi, Ballchasing. See instructions in Section 6.
3. **EsportsLiveBot actual trading**: Currently event detection only. The trigger + bankroll manager are wired but no trades are being placed (low confidence thresholds + few live events).
4. **Series-level hedging**: EsportsSeriesBot could hedge across match-winner + current-map-winner when both are liquid.
5. **Cross-game XGB feature engineering**: Add more features beyond Glicko-2 (hero/agent/operator meta, recent form, etc.).
6. **PandaScore paid tier evaluation**: Unlocks team_stats for all games, map names for CS2, per-round economy data. Would fix multiple data gaps.
7. **LoL old row feature migration**: Pre-Session 62 rows have old features (`herald_blue`, `inhib_down_diff`) but missing new ones (`matchup_uncertainty`, etc.). Will self-correct as new data accumulates. Could manually backfill if data volume is an issue.
8. **PandaScore live endpoint reliability**: `get_live_matches()` sometimes times out. Consider HTTP retry with jitter if persistent.

### User Action Required (API Keys)

These need the user to register and add to `/opt/polymarket-ai-v2/.env`:

```bash
# On VPS:
sudo nano /opt/polymarket-ai-v2/.env
# Add:
ALIGULAC_API_KEY=your_key_here       # http://aligulac.com/api/v1/ (free)
ODDSPAPI_API_KEY=your_key_here       # https://api.oddspapi.com/v4 (free tier)
BALLCHASING_API_KEY=your_key_here    # https://ballchasing.com/api (free with account)
# Then:
sudo systemctl restart polymarket-ai
```

---

## 11. FILE INDEX (all esports-scoped files)

### Bot Files

| File | Lines | Purpose |
|------|-------|---------|
| `bots/esports_bot.py` | 1,850 | Main trading bot. 8-game prediction. Background training. Smart retrain. Exposure tracking. Tournament phase. WS reactive. |
| `bots/esports_series_bot.py` | 695 | BO3/BO5 conditional probability. Map veto (DB -> HLTV fallback). S-T Kelly. Series pruning. WS reactive. |
| `bots/esports_live_bot.py` | 298 | In-game event trading. Queue drain. Auto-restart with backoff. Stale event filtering. |

### Data Files

| File | Lines | Purpose |
|------|-------|---------|
| `esports/data/esports_data_collector.py` | 623 | PandaScore -> DB. LoL/CS2/generic processors. Glicko-2 update. JSONB CAST fix. datetime fix. team_stats skip for non-LoL/CS2. |
| `esports/data/pandascore_client.py` | 423 | PandaScore REST API client. All 8 slugs confirmed. Date range fix. |
| `esports/data/esports_db.py` | 798 | DB helpers. log_prediction with phase. get_phase_accuracy. CLV stats. Edge decay. P&L summary. Map rates cache. |
| `esports/data/hltv_scraper.py` | 514 | Real HLTV scraping. get_map_win_rates() live. Rate-limited (10s). Fallback to 0.50. |
| `esports/data/riot_api_client.py` | 196 | Riot Games API. LoL patch detection. 20 req/s. |
| `esports/data/opendota_client.py` | 267 | OpenDota API. Hero win rates. Dota2 form adjustment. Free, no auth. |
| `esports/data/aligulac_client.py` | 202 | Aligulac SC2 Elo ratings. 50/50 Glicko-2 blend. Key needed. |
| `esports/data/ballchasing_client.py` | 251 | Ballchasing RL replays. Boost/accuracy/positioning stats. Key needed. |

### Model Files

| File | Lines | Purpose |
|------|-------|---------|
| `esports/models/esports_trainer.py` | 744 | Training orchestration. train_cross_game() with Glicko-2 filter. Smart retrain triggers. |
| `esports/models/glicko2.py` | 279 | Glicko-2 rating system. Outcome inversion fixed (Session 62). |
| `esports/models/lol_win_model.py` | 456 | LoL XGBoost. 9 features. Patch-aware. Calibration. |
| `esports/models/cs2_economy_model.py` | 526 | CS2 3-tier XGBoost. Graduated Kelly. |
| `esports/models/dota2_model.py` | 180 | Dota2 XGBoost. 6 Glicko-2 features. |
| `esports/models/valorant_model.py` | 180 | Valorant XGBoost. 6 Glicko-2 features. |
| `esports/models/series_model.py` | 276 | Map veto series functions. bo3/bo5/series_prob_with_map_veto. |
| `esports/models/patch_drift.py` | 272 | LoL patch drift detection via Riot API. |

### Market Files

| File | Lines | Purpose |
|------|-------|---------|
| `esports/markets/esports_market_scanner.py` | 268 | Market discovery by keyword matching. |
| `esports/markets/esports_market_service.py` | 424 | DB-backed market service. 5-min background refresh. |

### Live Infrastructure

| File | Lines | Purpose |
|------|-------|---------|
| `esports/live/esports_game_monitor.py` | 342 | PandaScore live match polling. Game state updates. Queue producer. |
| `esports/live/esports_event_detector.py` | 274 | Classifies game state changes into events (baron, economy break, etc.). |
| `esports/live/esports_live_trigger.py` | 207 | Cooldown enforcement. Per-match/per-map caps. Order placement. |

### Kelly / Risk

| File | Lines | Purpose |
|------|-------|---------|
| `esports/kelly/esports_bankroll_manager.py` | 270 | Per-bot bankroll. Dynamic Kelly graduation. Calibration cache (1h TTL). |

### Scripts

| File | Lines | Purpose |
|------|-------|---------|
| `scripts/seed_esports_data.py` | 85 | One-shot historical data seeder for 6 games. |

### Saved Models (on VPS)

| File | Size | Notes |
|------|------|-------|
| `saved_models/cross_game_xgb.json` | ~394KB | Accuracy=0.589, Brier=0.238, 10157 rows |
| `saved_models/dota2_xgb.json` | ~36KB | From 2454 training rows |
| `saved_models/valorant_xgb.json` | ~23KB | From 182 training rows |
| `data/esports_lol_model.pkl` | varies | LoL XGBoost |
| `data/esports_cs2_economy_model.pkl` | varies | CS2 economy model |

### OddsPapi Client (not in esports/data/ -- separate)

| File | Lines | Purpose |
|------|-------|---------|
| `esports/data/oddspapi_client.py` | ~140 | Pinnacle CLV benchmarking. Key needed. |

### TOTAL LINE COUNT

```
10,900 total lines across 26 Python files in the esports ecosystem
```

---

## 12. COMPLETE COMMITS LOG

All esports-relevant commits from Session 62 onwards, in chronological order:

| # | SHA | Session | Message | Key Change |
|---|-----|---------|---------|-----------|
| 1 | `92a87fd` | 62 | fix: EsportsBot Session 62 -- outcome inversion, 8-game expansion, confluence rewrite | ROOT CAUSE fix + 8 games |
| 2 | `7d7ff17` | 62 | fix: esports DB API mismatch + E7 label fix + E2 patch training | DB fixes |
| 3 | `4cf5819` | 63 | feat: forecast cache invalidation + Glicko-2 ratings migration | Glicko-2 DB persistence |
| 4 | `a191398` | 64 | feat: WeatherBot Tier 2+3 -- S-T sizing, Baker-McHale, Kelly graduation, CRPS | Esports: E5 Bayesian prior, E7 XGB |
| 5 | `8662001` | 65 | feat: wire train_cross_game() into EsportsBot training flow | Was never called |
| 6 | `7f01f56` | 65 | fix: PandaScore CoD slug cod-mw -> codmw (was returning 404) | 404 fix |
| 7 | `332bf69` | 65 | fix: RiotApiClient import name (was RiotAPIClient, class is RiotApiClient) | Silent import fail |
| 8 | `be1f137` | 65 | feat: seed_esports_data.py -- one-shot data collection for 6 new games | New script |
| 9 | `f0e1157` | 65 | fix: PandaScore range[scheduled_at] missing end date (400 Bad Request) | Chain bug 1/3 |
| 10 | `d2477a8` | 65 | fix: JSONB cast syntax for asyncpg | Chain bug 2/3 |
| 11 | `418f422` | 65 | fix: asyncpg scheduled_at needs datetime not str + JSONB CAST | Chain bug 3/3 |
| 12 | `713da5b` | 65 | docs: Session 65 esports handoff (729 lines) | Docs only |
| 13 | `08ae2db` | 66 | feat: HLTV scraper -- real web scraping for CS2 team data | HLTV live |
| 14 | `05fe84e` | 66 | feat: Dota2 + Valorant dedicated XGBoost models | 2 new models |
| 15 | `049d88b` | 66 | feat: P&L monitoring, map veto fix, Dota2/Valorant model wiring | Multi-feature |
| 16 | `0cc5761` | 67 | fix: 3 SQL bugs in esports_db (GROUP BY, INTERVAL, f-string) | P0.1-P0.3 |
| 17 | `6e6f847` | 67 | fix: EsportsLiveBot monitor task auto-restart with backoff | P0.4 |
| 18 | `bdae419` | 67 | fix: prune stale series prediction cache entries (>30min TTL) | P0.5 |
| 19 | `abbc159` | 67 | feat: calibration cache (1h TTL) + dynamic Kelly graduation | P1.1 + P2.1 |
| 20 | `3771099` | 67 | perf: cache get_team_map_rates() with 60s TTL | P1.3 |
| 21 | `ce70782` | 67 | feat: background training + tournament boost + exposure tracking | P1.2 + P2.2 + P2.4 |
| 22 | `fa3dc75` | 67 | feat: OpenDota API client + Dota2 form adjustment | P3.1 |
| 23 | `14a6238` | 67 | feat: Aligulac SC2 client + 50/50 Glicko-2 blend | P3.2 |
| 24 | `667efa7` | 67 | feat: OddsPapi client for Pinnacle CLV benchmarking | P3.3 |
| 25 | `a9ed7df` | 67 | feat: Ballchasing RL replay stats client | P3.4 |
| 26 | `f7393aa` | 67 | feat: S-T allocation for correlated series bets | P2.3 |
| 27 | `1a2ece9` | 67 | fix: cross-game XGBoost save path + wire OddsPapi CLV + Ballchasing RL | Fix + wiring |
| 28 | `cb2e8df` | 67 | fix: backfill CS2 map_name via ON CONFLICT DO UPDATE for map veto | DB upsert fix |
| 29 | `ebcdf84` | 68 | fix: restore HLTV fallback for CS2 map veto (PandaScore free tier gap) | HLTV restored |
| 30 | `4e00ed5` | 68 | fix: per-bot exposure caps + raise global cap (unblock esports trades) | Risk fix |
| 31 | `16eb8de` | 69 | feat(esports): P4 improvements -- XGB wiring, exception visibility, config extraction | P4 (10 items) |
| 32 | `5e66431` | 70 | feat(esports): P5 -- training data quality, smart retraining, phase calibration | P5 (5 items) |

**Total**: 32 esports-relevant commits across Sessions 62-70.

---

## PRIOR HANDOFF DOCUMENTS

| Document | Scope | Date |
|----------|-------|------|
| `AGENT_HANDOFF_ESPORTS_SESSION70_2026_03_09.md` | **THIS FILE** -- Sessions 62-70 complete | 2026-03-09 |
| `AGENT_HANDOFF_ESPORTS_SESSION68_2026_03_09.md` | Sessions 62-68 (pre-P5) | 2026-03-09 |
| `AGENT_HANDOFF_ESPORTS_SESSION65_2026_03_08.md` | Session 65 (data pipeline) | 2026-03-08 |
| `AGENT_HANDOFF_ESPORTS_SESSION63_2026_03_08.md` | Sessions 53-64 (852 lines) | 2026-03-08 |
| `AGENT_HANDOFF_SESSION47_2026_03_03.md` | Canonical system handoff (all 15 bots) | 2026-03-03 |

---

## DATABASE SCHEMA (esports tables)

### esports_training_data
```sql
match_id TEXT, game TEXT, team_a TEXT, team_b TEXT, patch TEXT,
game_state_json JSONB, outcome INTEGER, snapshot_type TEXT,
tournament TEXT, scheduled_at TIMESTAMP, created_at TIMESTAMP
-- UNIQUE: (match_id, snapshot_type, game) WHERE snapshot_type = 'match'
```

### esports_prediction_log
```sql
match_id TEXT, game TEXT, market_id TEXT, bot_name TEXT,
predicted_prob DOUBLE PRECISION, market_price DOUBLE PRECISION,
side TEXT, edge DOUBLE PRECISION, actual_outcome INTEGER,
resolved_at TIMESTAMP, closing_price DOUBLE PRECISION,
tournament_phase VARCHAR(50) DEFAULT '',  -- Added by P5 Commit 5
created_at TIMESTAMP
```

### esports_teams
```sql
external_id TEXT PRIMARY KEY, name TEXT, game TEXT, region TEXT,
logo_url TEXT, updated_at TIMESTAMP
```

### esports_matches
```sql
external_id TEXT PRIMARY KEY, game TEXT, tournament TEXT,
team_a TEXT, team_b TEXT, team_a_id TEXT, team_b_id TEXT,
best_of INTEGER, status TEXT, score_a INTEGER, score_b INTEGER,
scheduled_at TIMESTAMP, updated_at TIMESTAMP
```

### esports_calibration
```sql
game TEXT, market_type TEXT, bet_count INTEGER, correct_count INTEGER,
brier_score DOUBLE PRECISION, kelly_fraction DOUBLE PRECISION,
updated_at TIMESTAMP
-- UNIQUE: (game, market_type)
```

### glicko2_ratings
```sql
game TEXT, team_id TEXT, mu DOUBLE PRECISION, phi DOUBLE PRECISION,
sigma DOUBLE PRECISION, updated_at TIMESTAMP
-- Loaded into memory at startup for fast-path prediction
```

### esports_live_events
```sql
match_id TEXT, game TEXT, event_type TEXT, description TEXT,
confidence DOUBLE PRECISION, market_side TEXT, edge_estimate DOUBLE PRECISION,
created_at TIMESTAMP
```

---

*End of handoff. A new agent reading only this file should have 100% of the context needed to continue EsportsBot ecosystem development.*
