# AGENT HANDOFF — EsportsBot Session 119 (2026-03-23)

## Session Type: EsportsBot-scoped (full code audit + 64 fixes across 14 files)

## CRITICAL CONTEXT FOR NEXT AGENT

This is a **single-bot session for EsportsBot only**. Do not touch MirrorBot, WeatherBot, or any other bot's code/config unless explicitly requested. Read `CLAUDE.md` in the repo root.

### System Architecture (EsportsBot-specific)
- **15 bots** total in BOT_REGISTRY, this session is EsportsBot-scoped
- **EsportsBot** = pre-game match winner predictions using Glicko-2 + GBM models
- **EsportsLiveBot** = live in-game trading using WS price feeds (shares `esports_bot.py`)
- **EsportsSeriesBot** = series-level trading via `_series_scan()` (currently silent — no series markets)
- **Paper trading mode**: `SIMULATION_MODE=true`. Paper trading IS production (see CLAUDE.md)
- **VPS**: Ubuntu at 34.251.224.21, SSH key `~/.ssh/LightsailDefaultKey-eu-west-1.pem`

---

## What Was Done This Session (S119)

### Full Code Audit
Line-by-line forensic audit of entire EsportsBot subsystem: `esports_bot.py` (5743 lines) + 37 support files (~11,500 lines). 11 dedicated Opus agents, each reading every line.

### Phase 1: 17 Bug Fixes

| Fix | File | What |
|-----|------|------|
| 1A | esports_bot.py:1500 | Stale `entry` var in exit cost — re-extract per position in second for-loop |
| 1C | esports_db.py:449 | `db.fetch_all()` (nonexistent) → `db.get_session()` in `compute_calibration_curve` |
| 1D | esports_db.py:859 | `updated_at` → `last_updated` column name in `update_calibration` |
| 1E | esports_trainer.py:465 | Cross-game temporal split inverted — removed `reversed()` |
| 1F | esports_trainer.py:757 | LoL ONNX `n_features=8` → `9` |
| 1G | esports_bot.py:5387 | Series reverse sweep guard — added `return []` |
| 1H | esports_bot.py:5678-5743 | Series WS path: added anti-churn, fixed NO token, added entry tracking |
| 1I | esports_bot.py:3230 | Tournament exposure shares → USD (`+= _entry_cost`) |
| 1J | esports_bot.py:1525 | Hardcoded `'EsportsBot'` → `self.bot_name` in orphan cleanup |
| 1K | esports_bot.py:4512 | `_check_roster_stability` — needs full fix (deferred, see pending) |
| 1L | esports_bot.py:1910 | Double calibration — Platt now applied to raw prob, not beta-calibrated |
| 1M | esports_bot.py:3545 | Draw handling in Glicko-2 slow-path rebuild |
| 1N | esports_bot.py:1297 | Fire-and-forget → `await` for RESOLUTION writes |
| 1O | esports_bot.py:5171,5181 | Series trade count inside `if _ok:` only |
| 1P | esports_data_collector.py:583 | Added `team_a, team_b` to training SQL SELECT |
| 1R | dota2_model.py, valorant_model.py | `FEATURE_NAMES` as class attribute (fixes ONNX path) |
| 1S | opendota_client.py:160 | `radiant_win=None` guard |
| 1W | patch_drift.py:30 | Brier threshold 0.05 → 0.25 |
| P4 | esports_bot.py:2135+ | `scan_start_mono` in 4 event_data dicts (earlier in session) |

### Phase 2: 24 Dead Code Removals

| Item | File | What Removed |
|------|------|-------------|
| 2A | esports_bot.py | `_focal_calibrator`, `_bias_decomp`, `_horizon_calibrator` (deprecated S100) |
| 2B | esports_bot.py | `edge_no` variable (computed, never used) |
| 2C | esports_bot.py | Agreement score computation (multiplied by 0.0) |
| 2D | esports_bot.py | **Confluence gate entirely** (always passed — see PENDING REVIEW below) |
| 2E | esports_bot.py | `_team_exposure` dict (never written/read — see PENDING REVIEW) |
| 2F | esports_bot.py | Cross-game conformal predictor (gated False — see PENDING REVIEW) |
| 2G | esports_bot.py | `r.won is None` dead guard |
| 2H | esports_bot.py | 3 tautological conditions |
| 2I | esports_bot.py | `_models_graduated` (always = `_kelly_graduated`) |
| 2J | esports_bot.py | Series cache lookup by wrong key (match_id vs market_id) |
| 2K | series_model.py | `detect_momentum_fallacy()` (zero callers — see PENDING REVIEW) |
| 2L | patch_drift.py | `check_champion_drift()` / `set_champion_baseline()` (see PENDING REVIEW) |
| 2M | esports_event_detector.py | `prev_a`/`prev_b` (computed, unused) |
| 2N | esports_market_service.py | `_DEFAULT_MIN_VOLUME` / `vol_min` (see PENDING REVIEW) |
| 2O-2Q | esports_db.py | 3 broken functions (wrong schema, zero callers) |
| 2R | esports_data_collector.py | `_LOL_FEATURES`/`_CS2_FEATURES` stale constants |
| 2S | esports_data_collector.py | `_extract_lol_features()` (never called) |
| 2T | lol_win_model.py | `build_game_state_from_timeline()` (no callers) |
| 2U | cs2_economy_model.py | `total_remaining` (computed, unused) |
| 2V | cs2_economy_model.py | `classify_buy()`, `projected_loss_bonus()` (see PENDING REVIEW) |
| 2W | esports_trainer.py | `_evaluate_binary()`, `_evaluate_binary_cs2()` (superseded) |
| 2X | config/settings.py | `ESPORTS_MAX_EDGE`, `ESPORTS_CS2_ECONOMY_BREAK_THRESHOLD`, all `ESPORTS_CONFLUENCE_*` |

### Phase 3: 23 Cleanup Items

- 8 silent `except Exception: pass` blocks → added `logger.warning` or `logger.debug`
- 5 `hasattr` lazy-init dicts → moved to `__init__`
- 7 redundant `import re` / `import math` inside methods → removed
- Noisy `esportsbot_backfill_skip` log → removed (fired 9/10 scans)
- Stale alert string `(>0.30)` → uses actual `_halt_thresh` variable
- Fuzzy match docstring 0.85 → 0.78 to match code
- `_series_glicko2_cache` eviction added to `_cleanup_caches`
- EsportsSeriesBot added to daily P&L + backfill queries
- Stale docstrings fixed (lol_win_model, cot_validator)
- `asyncio.get_event_loop()` → `get_running_loop()` in esports_live_bot

### VPS .env Cleanup (earlier in session)
- Deleted stale `ESPORTS_MAX_EDGE=0.35`, `ESPORTS_MODEL_MAX_BRIER=0.248`
- Updated `ESPORTS_MAX_DAILY_USD` 10000 → 20000 in both `/opt/pa2-shared/.env` and `/opt/polymarket-ai-v2/.env`
- Updated `BOT_BANKROLL_CONFIG` EsportsBot `max_daily_usd` 10000 → 20000

---

## PENDING REVIEW — Removed Features That Could Elevate

**These items were removed because they were broken/dead, but the CONCEPTS have potential value. User must approve or deny rebuilding each one before any action is taken.**

### PR-1: Confluence Gate (was 2D) — CONCEPT: Multi-signal trade filter
**What it was**: Scored trades 0-1 using edge strength (65%) + prediction freshness (35%) + model agreement (0%). Gate at 0.55.
**Why removed**: Edge normalization saturated to 1.0 for any valid trade. Gate never rejected anything.
**Potential if rebuilt**: With proper normalization (`edge / (3 * min_edge)` to create gradient), could reject borderline trades where edge is barely above minimum AND prediction is stale. Freshness decay would penalize pre-game positions with 2h-old Glicko-2 ratings.
**Effort**: ~2h. New normalization + re-tuned weights + threshold.
**Status**: PENDING USER APPROVAL

### PR-2: Momentum Fallacy Detector (was 2K) — CONCEPT: Contrarian series signals
**What it was**: Detected when series markets overpriced momentum after map 1 (e.g., team wins map 1, market swings too far). Computed contrarian edge estimate.
**Why removed**: Zero callers. Never wired into series scan.
**Potential if rebuilt**: Real alpha in series markets — academic literature documents the momentum fallacy. Would generate contrarian signals when map 1 winner is overpriced.
**Blocked on**: No series markets currently on Polymarket.
**Status**: PENDING USER APPROVAL

### PR-3: Champion Drift Detection (was 2L) — CONCEPT: LoL patch-aware model invalidation
**What it was**: Monitored champion win rates for >3% shift from baseline. Flagged when patches changed champion power (e.g., nerf drops win rate 52% → 48%).
**Why removed**: Never called from esports_bot.py — wiring was missing.
**Potential if rebuilt**: **LoL is the worst game (Brier 0.308)**. Patches regularly change champion power. This detector could trigger targeted retraining when champion meta shifts, instead of waiting for Brier degradation to accumulate.
**Effort**: ~1h. Wire `set_champion_baseline()` after model training, `check_champion_drift()` into scan loop.
**Status**: PENDING USER APPROVAL

### PR-4: Team Exposure Tracking (was 2E) — CONCEPT: Correlated risk management
**What it was**: Per-team USD exposure dict + cap. Would limit capital on any single team across multiple markets.
**Why removed**: Dict declared but never written to or read from. Config `ESPORTS_MAX_TEAM_EXPOSURE=2000` exists but was unenforced.
**Potential if rebuilt**: Prevents concentration risk. If Navi has 3 matches today and bot takes all 3, team exposure could hit $900 on one org. A team-level cap limits correlated loss.
**Effort**: ~1h. Write on entry, decrement on exit, check before trade.
**Status**: PENDING USER APPROVAL

### PR-5: Volume Filter for Market Quality (was 2N) — CONCEPT: Liquidity gating
**What it was**: Minimum volume threshold for tradeable markets. Would reject illiquid markets.
**Why removed**: Defined but never applied in SQL query. Comment said "No volume filter" — intentionally disabled.
**Potential if rebuilt**: Shadow fills showed 24% avg slippage for EsportsBot. Volume filter would proactively reject thin markets before trying to trade. The S116 dead-market spread guard (>80%) catches the worst cases, but a volume floor would catch more.
**Effort**: ~30min. Add WHERE clause to market service query.
**Status**: PENDING USER APPROVAL

### PR-6: Cross-Game Conformal Predictor (was 2F) — CONCEPT: System-wide sizing safety net
**What it was**: Conformal prediction intervals from all resolved trades across games. Shrunk position sizes when system-wide calibration degrades.
**Why removed**: Gated behind `ESPORTS_USE_CONFORMAL=False`. Never ran. Per-game conformal predictors (still active) do the same at finer grain.
**Potential if rebuilt**: System-wide safety net that reduces ALL sizing when overall accuracy drops. Per-game conformal only adjusts within each game.
**Blocked on**: Needs 50+ resolved predictions (currently ~1).
**Status**: PENDING USER APPROVAL

### PR-7: CS2 Economy Helpers (was 2V) — CONCEPT: Economy-aware round prediction
**What it was**: `classify_buy()` (eco/force/full classification) and `projected_loss_bonus()` (income from loss streaks) for CS2.
**Why removed**: Zero callers. CS2 model runs on neutral economy defaults (no real-time data).
**Potential if rebuilt**: CS2 economy is the #1 predictor of round outcomes. If real-time economy data becomes available (HLTV scraping, paid PandaScore, FACEIT API), these would power economy-aware predictions.
**Blocked on**: Real-time CS2 economy data source.
**Status**: PENDING USER APPROVAL

---

## Live State at Session End

| Metric | Value |
|--------|-------|
| Open positions | ~18 |
| Markets scanned | 9 (4 LoL, 4 CS2, 1 other) |
| Live matches | 3 |
| WS trading | Active |
| Opportunities | 1 |
| Errors | 0 |
| Timing | ~159ms |
| Daily cap | $20,000 |

## Calibrator Status
- **0/8 games fitted** — S118 cleanup deleted corrupted data, fresh predictions accumulating
- CS2 has 1 resolved sample, needs 15 minimum
- ETA for first game fitting: ~48-72h from now as matches resolve

## P&L (unchanged from S118)
| Day | Net | Notes |
|-----|-----|-------|
| Mar 18 | +$175 | |
| Mar 19 | -$1,709 | |
| Mar 20 | +$1,357 | |
| Mar 21 | +$5,117 | 2 big Valorant wins |
| Mar 22 | -$79 | |
| **All-time** | **+$4,844** | |

## Live Config
```env
ESPORTS_TOTAL_CAPITAL=20000
ESPORTS_MAX_BET_USD=300
ESPORTS_MAX_DAILY_USD=20000
ESPORTS_MAX_TOTAL_EXPOSURE_USD=15000
ESPORTS_MAX_GAME_EXPOSURE=5000
ESPORTS_MAX_TOURNAMENT_EXPOSURE=8000
ESPORTS_MAX_TEAM_EXPOSURE=2000
ESPORTS_MIN_CONFIDENCE=0.48
ESPORTS_MIN_EDGE=0.05
ESPORTS_STOP_LOSS_PCT=0.15
ESPORTS_EXIT_COOLDOWN_SECONDS=300
ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW=5
ESPORTS_PER_MARKET_CAP=600
ESPORTS_BRIER_HALT_THRESHOLD=999.0
SIMULATION_MODE=true
```

## Files Modified This Session (14)
- `bots/esports_bot.py` — Phases 1-3 (bugs, dead code, cleanup)
- `esports/data/esports_db.py` — 1C, 1D, dead function removal
- `esports/data/esports_data_collector.py` — 1P, dead code removal
- `esports/data/opendota_client.py` — 1S
- `esports/models/esports_trainer.py` — 1E, 1F, dead code removal
- `esports/models/lol_win_model.py` — dead code, docstring fix
- `esports/models/cs2_economy_model.py` — dead code removal
- `esports/models/dota2_model.py` — 1R (FEATURE_NAMES class attr)
- `esports/models/valorant_model.py` — 1R (FEATURE_NAMES class attr)
- `esports/models/series_model.py` — dead code removal
- `esports/models/patch_drift.py` — 1W, dead code removal
- `esports/models/cot_validator.py` — docstring fix
- `bots/esports_live_bot.py` — 3U (deprecated asyncio)
- `config/settings.py` — dead settings removal

## Next Session Priorities
1. **User review of PR-1 through PR-7** — approve/deny each rebuild
2. **Monitor BetaCalibrator** — should start fitting within 48-72h
3. **1K: Fix `_check_roster_stability()`** — needs PandaScore API investigation for string team ID lookup
4. **Phase 4 design observations** — LoL model missing, Dota2/Valorant model dedup, etc.

## Tests
1415 passed, 2 skipped, 0 failures (excluding pre-existing weather_bot test failure).
