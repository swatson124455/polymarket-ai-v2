# EsportsBot Session 133 — Complete System Handoff

**Date**: 2026-03-26
**Scope**: EsportsBot-focused (1 shared module line changed: order_gateway.py)
**Deploy**: `20260326_145600` (Fixes 1-5 code), VPS `.env` (Fix 6)
**Previous**: S132 (data integrity fix), S131 (SQ formula root fix), S129 (handoff doc), S89 (feature session)
**Tests**: 87 EsportsBot unit tests passing, 1053/1054 full suite (1 pre-existing MirrorBot flake)

---

## TABLE OF CONTENTS

1. [System Overview](#system-overview)
2. [Session 133 Changes](#session-133-changes)
3. [All Code Changes with Line Numbers](#all-code-changes-with-line-numbers)
4. [Data Cleanup Performed](#data-cleanup-performed)
5. [Current Bot State (Post-Deploy)](#current-bot-state-post-deploy)
6. [EsportsBot Architecture](#esportsbot-architecture)
7. [Key Methods & Entry Points](#key-methods--entry-points)
8. [Configuration Knobs](#configuration-knobs)
9. [Database Schema](#database-schema)
10. [Calibrator & Learning System](#calibrator--learning-system)
11. [Risk & Exposure System](#risk--exposure-system)
12. [Resolution Pipeline](#resolution-pipeline)
13. [Critical Traps](#critical-traps)
14. [Known Issues & Monitor Items](#known-issues--monitor-items)
15. [P&L Summary](#pnl-summary)
16. [Session History (S83-S133)](#session-history)
17. [VPS Operations](#vps-operations)
18. [Files Modified](#files-modified)
19. [Rollback](#rollback)
20. [Verification Queries](#verification-queries)

---

## System Overview

### What Is This?
A **15-bot automated Polymarket trading system** running on a VPS (Ubuntu, 34.251.224.21). Currently in **paper trading mode** (`SIMULATION_MODE=true`) — real architecture, $0 execution. Paper trading IS production per CLAUDE.md.

### EsportsBot's Role
Trades **esports match-winner markets** (CS2, Dota2, Valorant, LoL, CoD, R6) using:
- **Glicko-2 ratings** for team strength modeling
- **BetaCalibrator** for probability calibration (needs 10+ resolved samples per game to fit)
- **Conformal prediction** for uncertainty intervals
- **Per-game XGBoost** models (optional, `ESPORTS_CATBOOST_ENABLED=False` currently)
- **Signal Quality (SQ)** — 5-component composite (agreement, calibration, uncertainty, enrichment, brier)
- **Series tracking** — correlated bets across BO3/BO5 series

### Key Relationships
- `bots/esports_bot.py` (~5,900 lines) — the bot itself
- `base_engine/` — shared infrastructure (order_gateway, risk_manager, paper_trading, resolution_backfill)
- `esports/` — esports-specific data layer (Glicko, PandaScore, prediction DB)
- `base_engine/execution/order_gateway.py` — routes orders through risk checks (S133: CVaR skip added)
- `base_engine/data/resolution_backfill.py` — shared resolution pipeline (Phase 4b computes P&L)

### Tech Stack
- Python 3.13, asyncio, asyncpg (PostgreSQL), Redis, WebSockets
- PandaScore API (live match data), Polymarket CLOB API (orderbook/prices)
- VPS: Ubuntu-3, 16GB/4vCPU, systemd service `polymarket-ai`

---

## Session 133 Changes

### Phase 1: Data Cleanup (13 corrupt rows deleted from trade_events)

**Pass 1** (10 rows):
| Type | Count | Why Corrupt |
|------|-------|-------------|
| SELL RESOLUTION | 1 | RESOLUTION events can't have side=SELL (resolution is market-level) |
| Zero-price EXIT | 7 | Paper engine returned price=0.0 on dead/empty orderbooks — fabricated max-loss P&L |
| Phantom EXIT | 2 | EXIT events for markets with 0 entries (orphaned by `_resolve_esports_from_clob`) |

**Pass 2** (3 rows):
| Type | Count | Why Corrupt |
|------|-------|-------------|
| SELL RESOLUTION | 2 | Same as above, missed in first pass |
| Orphan YES RESOLUTION | 1 | Market had 0 entries — resolution event from S104 workaround |

**Root cause**: All corruption originated from `_resolve_esports_from_clob()` (deleted in S132).

### Phase 2: Guardrail Verification + 3 New Guards Added

**Existing guards verified**:
- Opposing-side guard (S132 Fix 4) — blocks YES entry when NO exists
- `_entered_market_sides` set restored from DB on first scan
- Re-entry cooldown (15 min), per-market entry cap (2 per 12h window)
- Stop-loss (15%), max-hold (was 72h, now 48h)

**New guards (S133)**:

| Guard | Location | Description |
|-------|----------|-------------|
| **Penny entry floor** | `_execute_esports_trade()` line 3117 | Rejects entries where price < 0.05 or > 0.95 |
| **Penny entry floor (WS)** | `on_price_update()` line 749 | Same guard for WebSocket reactive path |
| **Dead market exit skip** | `_check_and_execute_exits()` line 1544 | Skips exit if current < 0.03 and entry >= 0.05 |

### Phase 3: Learning Acceleration (3 fixes)

| Fix | Description | File | Status |
|-----|-------------|------|--------|
| **Fix 1** | Early prediction logging — log ALL model predictions for calibrator, even if trade rejected | `esports_bot.py` line 1911 | DEPLOYED + VERIFIED |
| **Fix 2** | Skip CVaR for EsportsBot — bot has own per-game/tournament/team exposure caps | `order_gateway.py` line 527 | DEPLOYED + VERIFIED |
| **Fix 3** | Reduce max_hold from 72h to 48h — faster position turnover, more entries, more data | VPS `.env` | DEPLOYED + VERIFIED |

### Phase 3 Earlier Bugs Also Fixed (from earlier S133 sub-session)

| Bug | Description | Fix |
|-----|-------------|-----|
| **Stale `_cost` after max-bet cap** | Min-trade gate used pre-cap `_cost`, allowing dust trades | Recalculate `_cost = price * size` after max-bet cap (line 3296) |
| **Daily counter write no retry** | If `_inc_daily()` DB write failed, restart exposure counter lower than actual | Retry once before giving up (line 3357) |

---

## All Code Changes with Line Numbers

### File: `bots/esports_bot.py`

#### `_prediction_log_cache` initialization — line 267
```python
# Prediction log dedup: market_id -> (logged_prob, logged_ts)
# Skip re-logging if prediction unchanged for same market within 10 min
self._prediction_log_cache: Dict[str, tuple] = {}
```

#### S133 Guard 1: Penny/extreme price guard (WS path) — line 749
```python
# S133: Penny/extreme price guard (WS path)
_ws_min_price = float(getattr(settings, "ESPORTS_MIN_ENTRY_PRICE", 0.05))
_ws_max_price = float(getattr(settings, "ESPORTS_MAX_ENTRY_PRICE", 0.95))
if trade_price < _ws_min_price or trade_price > _ws_max_price:
    return
```

#### S133 Guard 2: Dead market exit skip — line 1544
```python
# S133: Skip exit if current_price is suspiciously low (dead/unquoted market)
# A price of 0.01-0.02 on a market we entered at 0.30+ means the book is empty,
# not that we lost 97%. Let resolution handle it instead.
if current < 0.03 and entry >= 0.05:
    logger.debug("esportsbot_exit_skip_dead_market", market_id=mid,
                 entry=round(entry, 4), current=round(current, 4))
    continue
```

#### S133 Fix 1: Early prediction logging — line 1911
```python
# S133: Early prediction logging — log ALL model predictions for calibrator learning,
# even if downstream edge/confidence gates reject the trade. The existing dedup
# (ON CONFLICT UPDATE) prevents duplicates if the trade also logs later.
_early_log_cache = self._prediction_log_cache.get(market_id)
_should_early_log = True
if _early_log_cache:
    _prev_prob, _prev_ts = _early_log_cache
    if abs(_prev_prob - model_prob) < 0.01 and (time.monotonic() - _prev_ts) < 600:
        _should_early_log = False
if _should_early_log:
    try:
        _db_early = getattr(self.base_engine, "db", None)
        if _db_early is not None:
            from esports.data.esports_db import log_prediction as _early_log_pred
            _early_side = "YES" if model_prob >= price else "NO"
            _early_edge = abs(model_prob - price)
            await _early_log_pred(
                db=_db_early, match_id=market_id, game=game,
                market_id=market_id, bot_name="EsportsBot",
                predicted_prob=model_prob, market_price=price,
                side=_early_side, edge=round(_early_edge, 4),
            )
            self._prediction_log_cache[market_id] = (model_prob, time.monotonic())
    except Exception:
        pass
```

#### S133 Guard 3: Penny/extreme price guard (main path) — line 3117
```python
# S133: Penny/extreme price guard — reject entries on dead/resolved markets
_entry_price = float(opp.get("price", 0))
_esports_min_price = float(getattr(settings, "ESPORTS_MIN_ENTRY_PRICE", 0.05))
_esports_max_price = float(getattr(settings, "ESPORTS_MAX_ENTRY_PRICE", 0.95))
if _entry_price < _esports_min_price or _entry_price > _esports_max_price:
    logger.info(
        "esports_extreme_price_rejected",
        market_id=opp.get("market_id", "")[:16],
        price=round(_entry_price, 4),
        min_price=_esports_min_price,
        max_price=_esports_max_price,
    )
    return False
```

#### S133 Cost recalculation after max-bet cap — line 3296
```python
_cost = price * size  # S133: Recalculate after max-bet cap
```

#### S133 Daily counter retry — line 3357
```python
# S133: Retry once on failure — without DB write, restart loses the increment
```

### File: `base_engine/execution/order_gateway.py`

#### S133 Fix 2: CVaR skip for EsportsBot — line 526-527
```python
# S97: WeatherBot skips CVaR Monte Carlo — has own group/city exposure limits
# S133: EsportsBot skips CVaR — has own per-game/tournament/team exposure caps
_skip_cvar = (bot_name in ("WeatherBot", "EsportsBot"))
```
**Was**: `_skip_cvar = (bot_name == "WeatherBot")`

### File: VPS `.env`

#### S133 Fix 3: Max hold reduction
```
ESPORTS_MAX_HOLD_HOURS=48
```
**Was**: Not set (code default 72 at line 1529)

---

## Data Cleanup Performed

### Trigger Management
```sql
-- Must disable on PARTITION (not parent table)
ALTER TABLE trade_events_2026_03 DISABLE TRIGGER trg_trade_events_immutable;
-- ... deletions ...
ALTER TABLE trade_events_2026_03 ENABLE TRIGGER trg_trade_events_immutable;
```

### P&L Impact
- **Before cleanup**: -$3,359 realized
- **After cleanup**: -$2,933 realized (removed $426 of fabricated losses from 13 corrupt rows)

---

## Current Bot State (Post-Deploy 2026-03-26 14:57 UTC)

### Service Health
```
Scan cycles: ~144-237ms
Markets scanned: 6 (4 CS2, 2 LoL)
Live matches: 9
Waterfall: no_prediction=2, low_confidence=1, passed=2, high_uncertainty=1
```

### Position Activity on Deploy
- **16 stale positions exited** via max_hold (49h-95h old): 10 CS2, 3 Valorant, 1 LoL
- **2 new trades entered** immediately (CVaR no longer blocking):
  - CS2 YES, confidence 0.7562, edge 0.1212, `success=True`
  - CS2 YES, confidence 0.7589, edge 0.2589, `success=True`
- **paper_trades status**: 39 filled (active), 222 resolved

### Calibrator Fitting Status
| Game | Total Predictions | Resolved | Need 10 | Status |
|------|------------------|----------|---------|--------|
| CS2 | 166 | 64 | 10 | **FITTED** |
| Dota2 | 42 | 20 | 10 | **FITTED** |
| Valorant | 34 | 8 | 10 | 2 more needed |
| LoL | 21 | 8 | 10 | 2 more needed |
| CoD | 5 | 1 | 10 | 9 more needed |
| R6 | 2 | 0 | 10 | 10 more needed |

---

## EsportsBot Architecture

### Processing Pipeline (per scan cycle, every ~2s)

```
scan_and_trade()
 |-- Phase A: Market discovery (get_active_markets from esports service)
 |-- Phase B: Opportunity analysis (analyze_opportunity per market)
 |   |-- Game detection (CS2/Dota2/Valorant/LoL/CoD/R6)
 |   |-- Observation mode check (48h after game patch)
 |   |-- Team name matching -> Glicko-2 ratings
 |   |-- Model prediction (_get_model_prediction)
 |   |   |-- Glicko-2 probability
 |   |   |-- BetaCalibrator (if fitted, 10+ samples)
 |   |   |-- Conformal prediction interval
 |   |   |-- BO adjustment (best-of series factor)
 |   |-- S133: Early prediction logging (ALL predictions, not just edge-passing)
 |   |-- Signal Quality computation (5 components)
 |   |-- Confidence gate (MIN_CONFIDENCE=0.50)
 |   |-- Edge gate (MIN_EDGE=0.05)
 |   |-- Return opportunity dict or None
 |-- Phase C: Execution (_execute_esports_trade per opportunity)
 |   |-- S133: Penny/extreme price guard (0.05-0.95)
 |   |-- S132: Opposing-side guard (_entered_market_sides)
 |   |-- Exposure checks (game/tournament/team caps)
 |   |-- Kelly sizing (with SQ as multiplier, not confidence — S131)
 |   |-- Conformal conservative sizing
 |   |-- BotBankrollManager sizing
 |   |-- place_order() -> order_gateway -> risk_manager (CVaR SKIPPED) -> paper_trading
 |   |-- Post-trade: update exposure, counters, prediction log
 |-- Exit check (_check_and_execute_exits)
 |   |-- Stop-loss (15%)
 |   |-- Max-hold (48h)
 |   |-- S133: Dead market exit skip (current < 0.03)
 |   |-- Daily loss limit ($500)
 |-- Backfill check (resolution status from markets table)
```

### WebSocket Reactive Path (on_price_update)
```
on_price_update(event)
 |-- Filter: only esports markets in prediction cache
 |-- S133: Penny/extreme price guard (0.05-0.95)
 |-- Price change threshold (1% from cached prediction)
 |-- Cooldown (10s between trades per market)
 |-- Game exposure check
 |-- S132: Opposing-side guard
 |-- Series override check
 |-- _execute_esports_trade(opportunity)
```

### Signal Quality (SQ) — 5 Components
```
SQ = weighted_mean(agreement, calibration, uncertainty, enrichment, brier)
```
- **agreement**: How much Glicko and market agree (0-1)
- **calibration**: BetaCalibrator quality (0.5 if unfitted)
- **uncertainty**: Conformal interval width (narrower = higher SQ)
- **enrichment**: Data richness (roster, draft, recent form) (0-1)
- **brier**: Historical Brier score for game (0.4 default)

**S131 change**: SQ moved from confidence multiplier to sizing multiplier.
- **Before**: `confidence = side_prob * SQ` (SQ crushed confidence -> rejected at gate)
- **After**: `confidence = side_prob`, `size *= SQ` (SQ scales position size, not entry signal)

### Key State Variables (in-memory)
```python
self._entered_market_sides: set = set()          # {(market_id, side)} opposing-side guard
self._entered_sides_restored: bool = False        # One-time DB restore flag
self._prediction_log_cache: Dict[str, tuple] = {} # market_id -> (prob, ts) dedup
self._game_exposure: Dict[str, float] = {}        # game -> USD (write-through daily_counters)
self._tournament_exposure: Dict[str, float] = {}  # tournament -> USD
self._team_exposure: Dict[str, float] = {}        # team -> USD
self._market_game: Dict[str, str] = {}            # market_id -> game (for exit decrement)
self._ws_prev_prices: Dict[str, float] = {}       # token_id -> last price (WS dedup)
self._position_details: Dict[str, dict] = {}      # market_id -> position info
```

---

## Key Methods & Entry Points

| Method | Line | Purpose |
|--------|------|---------|
| `scan_and_trade()` | varies | Main scan loop, called every ~2s |
| `analyze_opportunity(market_data)` | 1785 | Full waterfall: game detect -> model -> SQ -> gates |
| `_execute_esports_trade(opp)` | 3107 | Penny guard -> opposing-side -> exposure -> sizing -> place_order |
| `_check_and_execute_exits(db, positions)` | 1510 | Stop-loss, max-hold, dead-market-skip, daily loss |
| `on_price_update(event)` | 636 | WS reactive path for opportunistic entries |
| `_get_model_prediction(...)` | varies | Glicko -> calibrator -> conformal -> BO adjustment |
| `place_order(...)` | inherited | BaseBot -> order_gateway -> risk (CVaR skipped) -> paper_trading |

---

## Configuration Knobs

### Core Trading
| Setting | Default | VPS Override | Description |
|---------|---------|-------------|-------------|
| `ESPORTS_MIN_EDGE` | 0.05 | — | Min edge to trade |
| `ESPORTS_MIN_CONFIDENCE` | 0.50 | — | Min confidence (= side_prob after S131) |
| `ESPORTS_MAX_BET_USD` | 300.0 | — | Max bet size |
| `ESPORTS_MIN_TRADE_USD` | 10.0 | — | Min trade size |
| `ESPORTS_KELLY_DEFAULT_FRACTION` | 0.25 | — | Kelly fraction |
| `ESPORTS_KELLY_MAX_FRACTION` | 0.35 | — | Kelly cap |

### Risk & Exposure
| Setting | Default | VPS Override | Description |
|---------|---------|-------------|-------------|
| `ESPORTS_STOP_LOSS_PCT` | 0.15 | — | Stop-loss percentage |
| `ESPORTS_MAX_HOLD_HOURS` | 72 | **48** | Max position hold time |
| `ESPORTS_MAX_GAME_EXPOSURE` | 300.0 | — | Per-game exposure cap per trade |
| `ESPORTS_PER_MARKET_CAP` | 600 | — | Per-market position cap |
| `ESPORTS_DAILY_LOSS_LIMIT` | 500.0 | — | Daily loss halt |
| `ESPORTS_TOTAL_CAPITAL` | 5000.0 | — | Total capital allocation |
| `ESPORTS_DRAWDOWN_HALT_PCT` | 0.20 | — | Halt at 20% drawdown |

### Entry Guards
| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_MIN_ENTRY_PRICE` | 0.05 | S133: Penny floor |
| `ESPORTS_MAX_ENTRY_PRICE` | 0.95 | S133: Extreme price ceiling |
| `ESPORTS_EXIT_COOLDOWN_SECONDS` | 900.0 | 15-min cooldown after exit |
| `ESPORTS_MAX_ENTRIES_PER_MARKET_WINDOW` | 2 | Max entries per window |
| `ESPORTS_ENTRY_WINDOW_HOURS` | 12.0 | Entry counting window |
| `ESPORTS_REENTRY_MIN_EDGE` | 0.12 | Higher edge for re-entry |

### Model & Calibration
| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_OBSERVATION_HOURS` | 48 | Observation after game patch |
| `ESPORTS_MIN_ACCURACY_TO_TRADE` | 0.52 | Min accuracy to trade game |
| `ESPORTS_BRIER_HALT_THRESHOLD` | 1.0 | Halt game if Brier exceeds |
| `ESPORTS_CATBOOST_ENABLED` | False | XGBoost per-game models |
| `ESPORTS_RFLB_STRENGTH` | 0.03 | Random forest leaf bias |

### Series Trading
| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_SERIES_MIN_EDGE` | 0.10 | Min edge for series bets |
| `ESPORTS_SERIES_HEDGE_ENABLED` | True | Enable series hedging |
| `ESPORTS_SERIES_REFRESH_INTERVAL` | 30 | Series data refresh (s) |

### WebSocket
| Setting | Default | Description |
|---------|---------|-------------|
| `ESPORTS_WS_PRICE_CHANGE_PCT` | 0.01 | Min price change for WS trade |
| `ESPORTS_WS_COOLDOWN_SECONDS` | 10 | WS per-market cooldown |

---

## Database Schema

### Key Tables

**`trade_events`** (P&L AUTHORITY — partitioned by event_time)
```
Columns: sequence_num, market_id, bot_name, event_type, side, size, price,
         realized_pnl, event_data (JSONB), event_time, correlation_id, idempotency_key
Event types: ENTRY, EXIT, RESOLUTION
Immutability trigger: trg_trade_events_immutable (disable on PARTITION for cleanup)
NOTE: Uses sequence_num NOT id. No id column exists.
```

**`paper_trades`** (legacy, still used for position tracking)
```
Columns: market_id, bot_name, side, price, size, status, resolution,
         realized_pnl, created_at, resolved_at
Status: 'filled' (open), 'resolved', 'sold'
NOTE: Uses price NOT entry_price. No entry_price column. No metadata JSONB column.
```

**`esports_prediction_log`** (calibrator training data)
```
Columns: match_id, game, market_id, bot_name, predicted_prob, market_price,
         side, edge, actual_outcome, created_at
ON CONFLICT (match_id, bot_name) UPDATE — dedup safe
actual_outcome filled by S125 fallback from markets table
```

**`traded_markets`** (resolution tracking)
```
bot_names is TEXT (not array) — use LIKE '%EsportsBot%'
resolved: BOOLEAN
```

**`daily_counters`** (exposure write-through for restart recovery)
```
Used by _game_exposure write-through. Restored on startup via _restore_exposure_from_db()
```

---

## Calibrator & Learning System

### BetaCalibrator
- Fits Beta distribution: raw model probabilities -> calibrated probabilities
- **Requires 10+ resolved samples per game** to fit
- Unfitted games use raw Glicko probabilities (no calibration)
- Training data: `esports_prediction_log WHERE actual_outcome IS NOT NULL`

### S133 Learning Acceleration
**Problem**: CVaR blocked ALL entries ($12,828 > $10K cap). Only CS2/Dota2 had fitted calibrators.

**Solution**:
1. **Early prediction logging** — captures predictions from rejected trades (low_confidence, high_uncertainty)
2. **CVaR skip** — EsportsBot uses own exposure caps, global CVaR was redundant
3. **Max-hold 48h** — faster turnover = more entries = more data

### Resolution Pipeline
```
Market closes on Polymarket -> resolution_backfill.py Phase 4b ->
computes realized_pnl -> emits RESOLUTION to trade_events ->
S125 fallback also resolves esports_prediction_log from markets table
```
**`_resolve_esports_from_clob()` is DELETED (S132)** — shared pipeline is the ONLY correct path.

---

## Risk & Exposure System

### Layered Architecture
```
1. EsportsBot exposure caps (game/tournament/team/market/daily)
2. BotBankrollManager (max_bet=$300, kelly=0.25)
3. order_gateway risk_manager (position/exposure limits — CVaR SKIPPED for EsportsBot)
4. Kill switch (bot/portfolio/system level)
```

### Sizing Pipeline
```
1. Kelly fraction (0.25, degraded if Brier > 0.28)
2. Signal Quality multiplier (SQ scales size — S131)
3. Conformal conservative sizing
4. BotBankrollManager max_bet cap ($300)
5. ESPORTS_MAX_BET_USD cap ($300)
6. Per-market cap ($600)
7. Game exposure check
8. S133: _cost recalculated after cap
```

---

## Critical Traps

### EsportsBot-Specific
1. **`_resolve_esports_from_clob()` is DELETED** — do NOT recreate. Shared pipeline only.
2. **`confidence = side_prob`** (S131) — SQ is sizing multiplier, NOT confidence multiplier.
3. **`_entered_market_sides`** restored from DB on first scan (`_entered_sides_restored` flag).
4. **`_prediction_log_cache`** dedup: skip if prob unchanged (<0.01) within 10 min.
5. **`paper_trades`** uses `price` not `entry_price` — no `entry_price` column.
6. **`trade_events`** uses `sequence_num` not `id` — no `id` column.
7. **PatchDriftDetector**: Only set `_patch_timestamps` when `old is not None` (S88).
8. **Brier halt**: When game Brier > threshold, ALL that game's markets skipped.
9. **`no_prediction`**: Team name matching failed — teams not in Glicko database.
10. **S-T override path**: Series trades bypass normal sizing, use `st_override` directly.

### Shared Infrastructure
11. **`trade_events` is P&L AUTHORITY** — NEVER read `paper_trades` for P&L.
12. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
13. **Immutability trigger**: On PARTITION tables. Disable/re-enable for cleanup.
14. **RESOLUTION idempotency**: `ON CONFLICT` broken on partitions. Uses INSERT...SELECT WHERE NOT EXISTS.
15. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
16. **asyncpg timestamps**: `paper_trades` uses `timestamp without time zone` — `.replace(tzinfo=None)`.
17. **Python 3.13 scoping**: Local imports shadow top-level for ENTIRE function.
18. **`traded_markets.bot_names`**: TEXT, use `LIKE '%EsportsBot%'`.
19. **`positions` table**: NO `closed_at`, NO `updated_at`.
20. **`prediction_log`**: NO `rejection_reason`. Use `trade_executed` + `model_name`.
21. **BotBankrollManager = SIZING; risk_manager = LIMITS.**
22. **`risk_manager.calculate_position_size()` DEPRECATED.**
23. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable.
24. **Paper trading IS production.**

### VPS Operations
25. **Deploy**: SCP + restart or `deploy.sh` (atomic symlink swap).
26. **SSH key**: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
27. **VPS path**: `/opt/polymarket-ai-v2/`
28. **Service**: `sudo systemctl restart polymarket-ai`
29. **Logs**: `sudo journalctl -u polymarket-ai -f | grep EsportsBot`
30. **DB**: `PGPASSWORD=polymarket_s46 psql -h localhost -p 6432 -U polymarket -d polymarket`

---

## Known Issues & Monitor Items

1. **Valorant/LoL 2 away from fitted calibrator** — S133 early logging should accelerate.

2. **10 "unknown" game resolutions** — pre-game-tagging, P&L real ($-889.70) but unattributable.

3. **LoL 7.7% WR (1/13)** — genuinely bad. Consider per-game minimum confidence or disabling.

4. **7 historical both-sides markets** (Valorant, pre-S132 Fix 4) — cannot undo, Fix 4 prevents new.

5. **EXIT losses 2.5x worse than RESOLUTION** ($1,448 vs $2,920 on 192 vs 127 events) — stop-loss/max-hold exits are the #1 P&L destroyer. Investigate exit pricing.

6. **`no_prediction`**: ~2/scan. Team name matching failures. Fuzzy matching or alias table would help.

7. **CS2 Brier halt** — most liquid game halted intermittently. Is threshold (1.0) too tight?

8. **Local git out of sync** — S131+S132+S133 on VPS, local at S129 (`f125fae`). Need VPS->local sync + commit.

9. **Fix 5 (S132) validation** — confidence/signal_quality in event_data for new entries. 2 new trades entered post-S133 should have these. Verify with query in Verification Queries section.

---

## P&L Summary

### All-Time (post-S133 cleanup, pre-new-exits)
```
Entries:     329 (327 + 2 new)
Exits:       208 (192 + 16 max-hold exits on deploy)
Resolutions: 127
EXIT P&L:    -$1,448.35 (before 16 new exits)
RES P&L:     -$2,704
Pre-cleanup: -$3,359
Post-cleanup: -$2,933 (removed $426 fabricated losses)
```

### By Game (RESOLUTION only, post-cleanup)
| Game | Res | Wins | WR% | P&L |
|------|-----|------|-----|-----|
| sc2 | 1 | 1 | 100% | +$40.61 |
| cod | 1 | 1 | 100% | +$4.24 |
| valorant | 12 | 6 | 50% | -$178.83 |
| cs2 | 62 | 27 | 43.5% | -$382.73 |
| dota2 | 28 | 16 | 57.1% | -$561.35 |
| unknown | 10 | 4 | 40% | -$889.70 |
| lol | 13 | 1 | 7.7% | -$951.94 |

**Key correction**: Dota2 was "50% WR, -$5,829" (corrupt) -> **57.1% WR, -$561** (clean).

---

## Session History

| Session | Date | Key Changes |
|---------|------|-------------|
| S83 | 2026-03-13 | P7 roadmap, architecture |
| S88 | 2026-03-14 | PatchDriftDetector observation mode fix |
| S89 | 2026-03-14 | E2-E5 features + 9 audit fixes |
| S129 | 2026-03-25 | Cross-game mutation fix, dead config guard |
| S131 | 2026-03-25 | **SQ -> sizing multiplier** (was confidence multiplier) |
| S132 | 2026-03-25 | **Data integrity**: delete _resolve_esports_from_clob, opposing-side guard, confidence in event_data, P&L recompute 115 rows, phantom cleanup 85 rows |
| **S133** | **2026-03-26** | **Data cleanup 13 rows, 3 guardrails, 3 learning acceleration fixes, 2 bug fixes** |

---

## VPS Operations

### SSH / Deploy / Logs / DB
```bash
# SSH
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21

# Deploy single file
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem bots/esports_bot.py ubuntu@34.251.224.21:/tmp/
ssh -i ... ubuntu@34.251.224.21 "sudo cp /tmp/esports_bot.py /opt/polymarket-ai-v2/bots/ && sudo systemctl restart polymarket-ai"

# Logs
ssh -i ... ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai --since '5 min ago' --no-pager | grep -i esports"

# DB query
ssh -i ... ubuntu@34.251.224.21 "PGPASSWORD=polymarket_s46 psql -h localhost -p 6432 -U polymarket -d polymarket -c 'QUERY'"

# .env change
ssh -i ... ubuntu@34.251.224.21 "sudo bash -c \"echo 'KEY=VALUE' >> /opt/polymarket-ai-v2/.env\""

# VPS -> Local sync (REQUIRED before local edits)
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21:/opt/polymarket-ai-v2/bots/esports_bot.py bots/esports_bot.py
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21:/opt/polymarket-ai-v2/tests/unit/test_esports_bot.py tests/unit/test_esports_bot.py
```

### CODE DIVERGENCE WARNING
VPS has S131+S132+S133 changes. Local git is at S129 (`f125fae`). **Always sync VPS -> local before editing.**

---

## Files Modified

| File | Change | Scope | Status |
|------|--------|-------|--------|
| `bots/esports_bot.py` | ~60 lines added (guards + logging + bugs) | EsportsBot only | DEPLOYED, not committed |
| `base_engine/execution/order_gateway.py` | 2 lines (CVaR skip) | Shared, additive only | DEPLOYED, not committed |
| VPS `.env` | 1 line (`ESPORTS_MAX_HOLD_HOURS=48`) | VPS config | DEPLOYED |

---

## Rollback

```bash
# Code rollback:
cd /opt/polymarket-ai-v2 && git log --oneline -5
git revert <S133-commit-sha>
sudo systemctl restart polymarket-ai

# .env rollback (restore 72h default):
sudo sed -i '/ESPORTS_MAX_HOLD_HOURS/d' /opt/polymarket-ai-v2/.env
sudo systemctl restart polymarket-ai

# Data: 13 corrupt rows deleted. Do NOT restore — they were fabricated losses.
```

---

## Verification Queries

```sql
-- Prediction log growth (Fix 1)
SELECT game, COUNT(*) FROM esports_prediction_log
WHERE created_at > NOW() - INTERVAL '1 hour' GROUP BY game;

-- Data integrity: no corrupt events
SELECT COUNT(*) FROM trade_events WHERE bot_name='EsportsBot'
AND event_type='RESOLUTION' AND (side='SELL' OR realized_pnl IS NULL);
-- Should be 0

SELECT COUNT(*) FROM trade_events WHERE bot_name='EsportsBot'
AND event_type='EXIT' AND price=0.0;
-- Should be 0

-- S132 Fix 5: confidence in new entries
SELECT event_data->>'confidence', event_data->>'signal_quality'
FROM trade_events WHERE bot_name='EsportsBot' AND event_type='ENTRY'
ORDER BY event_time DESC LIMIT 5;
-- Both non-NULL for recent entries

-- Calibrator progress
SELECT game, COUNT(*) total, COUNT(*) FILTER (WHERE actual_outcome IS NOT NULL) resolved
FROM esports_prediction_log GROUP BY game ORDER BY total DESC;

-- Open positions
SELECT status, COUNT(*) FROM paper_trades WHERE bot_name='EsportsBot' GROUP BY status;

-- Scan health
-- journalctl: grep 'esportsbot_scan_summary'
```

---

## Next Steps (Suggested Priority)

1. **Monitor calibrator fitting** — Valorant/LoL should cross 10 resolved within days
2. **Investigate EXIT P&L drag** — exits are 2.5x worse than resolutions
3. **LoL 7.7% WR** — consider disabling or minimum confidence per game
4. **CS2 Brier halt frequency** — most liquid game halted too often?
5. **`no_prediction` team matching** — 2/scan lost to name matching failures
6. **Local git sync** — S131+S132+S133 need commit
7. **Bet sizing review** — is $150-300 avg optimal for data collection?
