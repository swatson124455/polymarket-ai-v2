# AGENT HANDOFF — EsportsBot Session 88 (2026-03-14)

**Session scope**: EsportsBot ONLY. No bleed-over to MirrorBot/WeatherBot unless explicitly demanded.
**Previous sessions**: S83 (P7 roadmap), S85 (data overhaul), S86 (dedup attempt 1), S87 (dedup fix + cleanup), S88 (observation mode fix)

---

## READ THESE FIRST (in order)

1. **`C:\Users\samwa\.claude\projects\C--lockes-picks-polymarket-ai-v2\memory\MEMORY.md`** — Complete system state, all critical traps, config, bot status
2. **`CLAUDE.md`** — Prime Directive, Rules of Engagement, checklist before editing, forbidden patterns
3. **This file** — Session 88 specifics and outstanding items

---

## SYSTEM OVERVIEW (for new agents)

### What Is This?
A live 15-bot Polymarket automated trading system. Real capital at risk. Currently in **paper trading mode** (`SIMULATION_MODE=true`), but paper trading IS production — see CLAUDE.md Prime Directive. 5 bots active, 9 disabled, 1 deleted (MomentumBot), 1 archived (EnsembleBot).

### The 3 Active Esports Bots
| Bot | Capital | Max Bet | Max Daily | Role |
|-----|---------|---------|-----------|------|
| **EsportsBot** | $5,000 | $100 | $500 | Core esports prediction (LoL, CS2, Dota2, Valorant, SC2, etc.) |
| **EsportsLiveBot** | $1,000 | $100 | $500 | Live in-game odds (currently 0 trades — PandaScore timeout) |
| **EsportsSeriesBot** | $1,000 | $100 | $500 | Series/tournament-level (currently 0 trades) |

### Other Active Bots (DON'T TOUCH unless asked)
| Bot | Realized P&L | Notes |
|-----|-------------|-------|
| MirrorBot | **+$15,051** | RTDS live, ~0 open positions (resolved naturally) |
| WeatherBot | **+$910** | ~400 open positions, 156/643 resolved |

---

## WHAT SESSION 88 DID

### 1. Verified Session 87 RESOLUTION Dedup Fix
- Ran `verify_dedup.py` on VPS: 709 events for 709 unique combos = **1.0x** (clean, holding)
- All code already committed in git (`83310d1`, `4c56349`)
- The atomic INSERT...SELECT WHERE NOT EXISTS in `database.py` `insert_trade_event()` is working

### 2. CRITICAL BUG FOUND & FIXED — False Observation Mode on Every Restart

**Root Cause**: `PatchDriftDetector._check_patch_version()` in `esports/models/patch_drift.py` set `_patch_timestamps[game]` on the FIRST version check (when `old=None` — initialization), even though it correctly returned `None` (no patch change). But then `check_game()` line 91 called `is_observation_mode(game)` which saw the timestamp and returned `True`.

**Impact**: Every service restart falsely triggered 48h observation mode for ALL games with detectable patches. 14-15 LoL markets blocked from trading every scan cycle. 0 opportunities, 0 trades for LoL (the largest game category).

**The Fix** (commit `dd27694`):
```python
# BEFORE (broken): timestamp set unconditionally
self._known_patches[game] = version
self._patch_timestamps[game] = _dt.datetime.now(_dt.timezone.utc)  # ← ALWAYS SET
if old is not None:
    return version

# AFTER (fixed): timestamp only on genuine patch change
self._known_patches[game] = version
if old is not None:  # Don't trigger on first check
    self._patch_timestamps[game] = _dt.datetime.now(_dt.timezone.utc)
    return version
```

Applied to both LoL (Riot API) and CS2 (HLTV scraper) code paths.

**Verification**: Post-deploy scan summary went from `observation: 15` to `observation: 0`. First trade placed within 30 seconds. Scan cycle dropped from 21-26s to 3.3s.

### 3. Fixed `esports_diag.py` Script (3 column bugs)
- `positions.closed_at` → doesn't exist. Changed to use `trade_events WHERE event_type='EXIT'`
- `positions.updated_at` → doesn't exist either. Same fix.
- `prediction_log.rejection_reason` → doesn't exist. Changed to `trade_executed` + `model_name`
- P&L formula in script: corrected `(1-entry)*size` for NO → `entry*size` for ALL sides

### 4. Deployed
- **Commit**: `dd27694`
- **Deploy**: `20260314_134929`
- **Tests**: 36 patch_drift tests passed, 76 esports_bot tests passed

---

## CURRENT ESPORTSBOT STATE (as of deploy `20260314_134929`)

### P&L
| Event Type | Count | Realized P&L |
|------------|-------|-------------|
| ENTRY | 75+ | $0.00 |
| EXIT | 16+ | **+$59.62** |
| RESOLUTION | 65+ | **-$82.00** |
| **TOTAL** | | **~-$22 realized** |
| Unrealized | 7 positions | **-$14.27** |

### Open Positions (7)
| Market | Side | Size | Entry | Current | uPnL |
|--------|------|------|-------|---------|------|
| 0xdaaa... | NO | 186.7 | 0.38 | 0.365 | -$2.80 |
| 0x000a... | YES | 32.7 | 0.54 | 0.50 | -$1.31 |
| 0xb837... (NaVi vs B8) | YES | 67.6 | 0.74 | 0.75 | +$0.68 |
| 0x7004... | NO | 198.6 | 0.50 | 0.50 | $0.00 |
| 0x7e1c... | YES | 186.7 | 0.38 | 0.335 | -$8.40 |
| 0x3fe5... | NO | 164.9 | 0.48 | 0.465 | -$2.47 |
| 0x8bbb... | NO | 8.0 | 0.53 | 0.535 | +$0.04 |
| **Total** | | | **$392 invested** | | **-$14.27** |

### System Exposure
- **EsportsBot only**: $392 / $20,000 cap = **$19,608 headroom**
- MirrorBot positions have resolved naturally — no longer blocking
- ~~P1 exposure cap blocker~~ — **RESOLVED**

### Scanning (post-fix)
- **31 markets per scan**: lol=16, cs2=7, valorant=5, sc2=1, cod=1, other=1
- **Scan cycle**: ~3.3s (was 21-26s when observation mode was blocking)
- **Waterfall**: `no_prediction:12, edge_cap:10, low_confidence:5, low_edge:1, no_game:1, passed:1`
- **Trades**: Actively placing (1 trade on first scan after fix, $100 at price 0.335)

### Glicko-2 Data (from DB `glicko2_ratings`)
| Game | Teams | Avg Match Count |
|------|-------|----------------|
| cs2 | 395 | 3,754 |
| lol | 248 | 1,875 |
| dota2 | 154 | 1,758 |
| valorant | 87 | 249 |
| rl | 53 | 722 |
| sc2 | 42 | 550 |
| r6 | 24 | 28 |
| cod | 12 | 367 |
| **Total** | **1,015** | All ≥10 matches (prediction-ready) |

Training data: 11,049 rows in `esports_training_data`

### Initialization Status
- Glicko-2: All games loaded ✅
- ML models: LoL + CS2 trained ✅
- Calibrators: FocalTemp + BiasDecomp + HorizonBias all initialized ✅
- Conformal/TabPFN: Initialized (awaiting calibration data) ✅
- PandaScore: Connected, fetching markets ✅
- PatchDriftDetector: Fixed, not falsely triggering ✅

---

## FILES MODIFIED THIS SESSION (Session 88)

| File | What Changed |
|------|-------------|
| `esports/models/patch_drift.py` | `_check_patch_version()`: moved `_patch_timestamps` assignment inside `if old is not None:` for both LoL and CS2 |
| `scripts/esports_diag.py` | Fixed 3 column bugs + P&L formula. Uses trade_events for exits, trade_executed for waterfall |
| `scripts/verify_dedup.py` | NEW (Session 87): Quick RESOLUTION dedup verification |
| `scripts/cleanup_resolution_dupes.py` | NEW (Session 87): Cleanup tool with trigger disable/enable |

---

## ALL COMMITS THIS SESSION + RECENT HISTORY

| Commit | Date | Description |
|--------|------|-------------|
| `dd27694` | 2026-03-14 | **fix(esports): prevent false observation mode on every restart** |
| `83310d1` | 2026-03-14 | fix(data): remove duplicate RESOLUTION emitter from backfill |
| `4c56349` | 2026-03-14 | fix(resolution): prevent duplicate RESOLUTION events in trade_events |
| `4491c89` | 2026-03-14 | fix(ingestion): clear orphaned sync_log entries on scheduler startup |
| `d5a1c9f` | 2026-03-14 | fix(resolution): remove second shadowed datetime import in Phase 7 |
| `1280b33` | 2026-03-14 | fix(resolution): remove shadowed datetime import causing Python 3.13 scoping error |
| `d34abb0` | 2026-03-14 | fix(resolution+equity): add debug stats to backfill + fix equity |

---

## PREDICTION PIPELINE (complete architecture)

```
PandaScore API → esports_training_data table → train_game()
                                                    ↓
                                         GBM model + Glicko-2 tracker
                                                    ↓
Market scan → _predict_market()
    │
    ├─ Team name extraction: _clean_team_names() + _match_team_name()
    │   └─ 6 question patterns + _TEAM_ALIASES (50+ entries)
    │
    ├─ Glicko-2 prediction (HARD GATE: match_count >= 10)
    │   └─ Returns None if either team has < 10 matches
    │
    ├─ ONNX inference (per-game: LoL, CS2, Dota2, Valorant)
    │   └─ Fallback to native GBM if ONNX not available
    │
    ├─ Calibration pipeline (ORDER MATTERS):
    │   1. bias_decomp.recalibrate(prob, game)        [needs 30+ predictions/game]
    │   2. focal_temp.calibrate(prob)                  [needs 50+ prediction_log entries]
    │   3. horizon_bias.calibrate(prob, "esports", ttr_days)  [needs 15+ trades/bucket]
    │
    ├─ Edge computation: calibrated_prob - market_price
    │
    ├─ CoT validator (edge > 15% only, max 3/scan, Claude Haiku)
    │
    └─ Sizing pipeline:
        1. Conformal conservative_prob (p_low for YES, p_high for NO)
        2. Expiry boost (1.5x <6h, 1.2x <24h)
        3. phi_factor (uncertainty scaling)
        4. dd_factor (drawdown reduction)
        5. game_kelly_mult (per-game Brier-based, dynamic EGM d)
        6. edge_decay_mult (0.6/0.8/1.0 from CLV analysis)
        7. $100 cap (BotBankrollManager max_bet_usd)
```

### Waterfall Gates (in order, from scan logs)
1. `no_game` — unrecognized game category
2. `exposure_cap` — game/bot/system exposure limit hit
3. **`observation`** — ~~patch drift 48h window~~ **FIXED Session 88** (was falsely triggering on every restart)
4. `halted` — calibration broken (never triggered yet)
5. `no_prediction` — can't match team names to Glicko data
6. `low_edge` — edge below ESPORTS_MIN_EDGE (0.05 in scan, 0.08 for sizing)
7. `edge_cap` — edge suspiciously high (capped)
8. `low_confidence` — below ESPORTS_MIN_CONFIDENCE (0.52)
9. `low_confluence` — models disagree
10. `passed` → trade executed

---

## CRITICAL TRAPS (carry forward — ALL of these)

### RESOLUTION Events
- **`ON CONFLICT (idempotency_key, event_time)` IS BROKEN on partitioned tables** — NEVER rely on it for RESOLUTION events
- `insert_trade_event()` uses atomic INSERT...SELECT with WHERE NOT EXISTS for event_type='RESOLUTION'
- Cleanup tool: `scripts/cleanup_resolution_dupes.py` (disables `trg_trade_events_immutable` trigger, deletes, re-enables)

### P&L Math (MANDATORY)
- `cost = entry_price * size` (ALL sides — YES and NO)
- `uPnL = (current_price - entry_price) * size` (ALL sides)
- **NEVER invert for NO positions** — prices are token-specific
- Data source: `trade_events` (realized), `positions.unrealized_pnl` (mark-to-market)
- Canonical script: `python scripts/bot_pnl.py BotName hours`

### EsportsBot-Specific
- `_market_game: Dict[str, str]` — persistent market→game mapping, populated on ENTRY, used on EXIT
- `_game_exposure` write-through: `_inc_daily(db, "EsportsBot", f"game_{game}", -size)` on exit
- Glicko-2 hard gate: `tracker.match_count < 10 → return None` (line 2662)
- Retrain trigger: `init_glicko=True` when tracker is None (lines 646-651)
- `_init_glicko2_trackers()` two-path loading: fast from `glicko2_ratings` table, slow from `esports_training_data`
- asyncpg INTERVAL: Use `INTERVAL '1 day' * :param` with integer, never string

### PatchDriftDetector
- `_patch_timestamps` must ONLY be set on genuine patch changes (`old is not None`)
- Setting on first check falsely triggers 48h observation mode on every restart — **FIXED Session 88**
- Observation mode = 48h paper-only after real patch (controlled by `_OBSERVATION_HOURS = 48`)

### Database Schema Gotchas
- **trade_events is P&L authority** — NEVER paper_trades
- **paper_trades has NO `resolved_pnl` column** — it's `resolved_at`
- **paper_trades has NO `metadata` JSONB column**
- **trade_events JSONB column is `event_data`** not `metadata_json`
- **positions table**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`. For closed positions → query `trade_events WHERE event_type='EXIT'`
- **prediction_log**: NO `rejection_reason`. Use `trade_executed` (bool) + `model_name` for analysis
- **glicko2_ratings**: Column is `match_count` NOT `matches_played`
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **asyncpg timestamps**: `paper_trades` uses `timestamp without time zone` — pass `.replace(tzinfo=None)`. `created_at` has NO DEFAULT
- **asyncpg DATE**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime string
- **asyncpg tuple IN**: Can't bind tuples for `IN (:param)`. Use f-string `IN ('val1','val2')` pattern

### System-Wide
- `SIMULATION_MODE=true` — paper trading, but treat as production (CLAUDE.md Prime Directive)
- Python 3.13 scoping: local imports shadow ENTIRE function. Never shadow top-level names
- `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL"
- `BOT_REGISTRY=14 bots` — shared module change requires all 14 verified
- `BotBankrollManager` handles SIZING; `risk_manager` handles LIMITS. Both must pass
- `risk_manager.calculate_position_size()` is DEPRECATED — BotBankrollManager is the real sizer
- `PSEUDO_LABEL_ENABLED=false` — DO NOT enable

---

## OUTSTANDING ITEMS (EsportsBot)

### P2 — Team Name Matching Failures (12 markets/scan)
- `no_prediction: 12` per scan cycle — 12 markets where team names can't be matched to Glicko data
- Mostly CS2 tournament names not parsing correctly and Valorant regional teams
- Log examples:
  - `esportsbot_team_match_fail game=cs2 team_b_id=None` — tournament name "aorus league latam group a" not recognized
  - `esportsbot_team_match_fail game=valorant team_b_id=None` — "vct game changers latin america north group stage"
- **Fix options**: Expand `_clean_team_names()` tournament suffix list, add more `_TEAM_ALIASES` entries
- **Impact**: ~12 markets skipped per scan cycle that could potentially be traded

### P3 — PandaScore API Timeouts
- `esportsbot_live_refresh_stale` — consecutive_failures climbing (was at 6)
- 5s timeout on `get_live_matches()` — PandaScore API occasionally slow
- **Impact**: EsportsLiveBot gets 0 live data; EsportsBot not directly affected (uses cached matches)
- **Fix options**: Increase timeout, add retry logic, or investigate PandaScore latency

### P3 — EsportsLiveBot + EsportsSeriesBot Inactive
- Both active in BOT_REGISTRY but 0 trade events ever
- EsportsLiveBot blocked by PandaScore timeout
- EsportsSeriesBot may need investigation of its scan logic

### P3 — 604 Unresolved Markets
- In `traded_markets` table — genuinely still open, will resolve over time
- Backfill runs every 30min (mini) + daily (full) + on restart

### P5 — Blocked Items (need external data/models)
- GLiNER2 NER: 500MB model download needed
- d3rlpy offline RL: Needs 500+ resolved trades (EsportsBot at ~65)
- Calibrator activation: Needs 30-50 resolved predictions per game
- Whale alerts / orderbook signals: Blocked on external services

### P5 — Pre-existing Test Failure
- `test_edge_threshold_identical_both_modes[EsportsBot]` — not investigated
- Does not affect other tests

---

## KEY FILES REFERENCE

| File | Lines | Purpose |
|------|-------|---------|
| `bots/esports_bot.py` | ~2700 | Main EsportsBot: scan, predict, size, trade, exit |
| `base_engine/data/database.py` | ~4600 | DB access: trade_events, positions, paper_trades |
| `base_engine/data/resolution_backfill.py` | ~500 | Market resolution pipeline (Phases 1-6) |
| `esports/models/patch_drift.py` | ~272 | Patch drift detection + observation mode |
| `esports/models/conformal_wrapper.py` | ~145 | MAPIE conformal prediction intervals |
| `esports/models/tabpfn_ensemble.py` | ~120 | TabPFN v2 for sparse games |
| `esports/models/cot_validator.py` | ~135 | CoT LLM validation for high-edge trades |
| `esports/data/pandascore_client.py` | — | PandaScore API client |
| `esports/models/esports_trainer.py` | — | GBM model training + feature engineering |
| `base_engine/features/calibration.py` | — | FocalTemp, BiasDecomp, HorizonBias calibrators |
| `base_engine/data/daily_counter.py` | — | Exposure persistence: increment_counter() |
| `scripts/esports_diag.py` | ~143 | Full esports diagnostic (trade_events, positions, exposure) |
| `scripts/verify_dedup.py` | ~47 | Quick RESOLUTION dedup check |
| `scripts/cleanup_resolution_dupes.py` | ~75 | Dedup cleanup with trigger management |
| `scripts/bot_pnl.py` | — | Canonical P&L script |

---

## DATABASE TABLES (EsportsBot relevant)

| Table | Role | Key Columns |
|-------|------|-------------|
| `trade_events` | **P&L authority** | event_type, bot_name, market_id, side, size, price, realized_pnl, event_data (JSONB), event_time. Partitioned monthly. Immutable trigger. |
| `positions` | Position tracking | bot_id, market_id, side, size, entry_price, current_price, unrealized_pnl, opened_at, status. 10s price updates. |
| `paper_trades` | Legacy compat | Still written (28 callers), NEVER read for P&L. No metadata column. |
| `daily_counters` | Exposure tracking | `game_{game}` keys (additive), `daily_exposure_usd` (absolute-set) |
| `esports_training_data` | Historical matches | 11,049 rows from PandaScore |
| `glicko2_ratings` | Team ratings | game, team_key, mu, phi, sigma, match_count |
| `prediction_log` | Prediction audit | trade_executed (bool), model_name, edge, confidence. NO rejection_reason. |
| `traded_markets` | Resolution backfill | Market status, resolution tracking |

**Purged (migration 052)**: position_snapshots, trade_model_linkage, model_registry, model_performance_daily, feature_sets

---

## STATE PERSISTENCE (all gaps closed)

| State | Mechanism | Status |
|-------|-----------|--------|
| `_daily_exposure_usd` | `daily_counters` 60s flush + SIGTERM + startup restore | Done |
| `_game_exposure` (EsportsBot) | `daily_counters` write-through + exit decrement | Done |
| Open positions | `order_gateway.seed_positions_from_db()` | Done |
| `_market_game` dict | Populated on ENTRY, used on EXIT for game lookup | Done |
| Glicko-2 trackers | `glicko2_ratings` table (fast path) + `esports_training_data` (slow path) | Done |

### daily_counters Write Patterns (DO NOT MIX)
- **ADDITIVE**: EsportsBot `game_{game}` keys — `counter_value += amount` via `increment_counter()`
- **ABSOLUTE-SET**: OrderGateway `daily_exposure_usd` — `counter_value = total` via `_flush_daily_exposure()`

---

## CONFIG (live VPS values)

```env
# EsportsBot
ESPORTS_CAPITAL=5000
ESPORTS_KELLY_FRACTION=0.25
ESPORTS_MAX_BET=100
ESPORTS_MAX_DAILY=500
ESPORTS_MIN_CONFIDENCE=0.52
ESPORTS_MIN_EDGE=0.08
ESPORTS_FRESHNESS_DECAY_SECONDS=30.0
ESPORTS_CONFORMAL_ALPHA=0.10
ESPORTS_COT_EDGE_THRESHOLD=0.15
ESPORTS_COT_MAX_PER_SCAN=3

# EsportsLiveBot / EsportsSeriesBot
# capital=$1000, kelly=0.25, max_bet=$100, max_daily=$500

# System
SIMULATION_MODE=true
```

---

## DEPLOY & VERIFICATION

### Deploy
```bash
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
# Rollback:
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/rollback.sh
```

VPS: `/opt/polymarket-ai-v2` → symlink to latest in `/opt/pa2-releases/`. Shared: `/opt/pa2-shared/{data,saved_models,venv}`. Atomic swap via `mv -T`. Health check 90s.

### SSH into VPS
```bash
ssh -i "C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" ubuntu@34.251.224.21
```

### Verification Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"

# Live logs
ssh -i "$KEY" ubuntu@34.251.224.21 "journalctl -u polymarket-ai -f" | grep -i esports

# Scan summary (check waterfall, opportunities, trades)
ssh -i "$KEY" ubuntu@34.251.224.21 "journalctl -u polymarket-ai --no-pager -n 200" | grep esportsbot_scan_summary

# RESOLUTION dedup check
scp -i "$KEY" scripts/verify_dedup.py ubuntu@34.251.224.21:/tmp/ && \
ssh -i "$KEY" ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && source /opt/pa2-shared/venv/bin/activate && PYTHONPATH=. python3 /tmp/verify_dedup.py"

# Full esports diagnostic
scp -i "$KEY" scripts/esports_diag.py ubuntu@34.251.224.21:/tmp/ && \
ssh -i "$KEY" ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && source /opt/pa2-shared/venv/bin/activate && PYTHONPATH=. python3 /tmp/esports_diag.py"

# P&L check
ssh -i "$KEY" ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && source /opt/pa2-shared/venv/bin/activate && PYTHONPATH=. python3 scripts/bot_pnl.py EsportsBot 24"
```

### Run Tests
```bash
python -m pytest tests/unit/test_patch_drift.py tests/unit/test_esports_bot.py -x -q
# Full suite: pytest tests/ -x -q --timeout=30
```

---

## SESSION HISTORY (Sessions 77-88)

| Session | Date | Key Deliverable |
|---------|------|----------------|
| 77 | 2026-03-11 | MirrorBot P1-P8 + stale entry pricing fix + resolution SELL overwrite fix |
| 79 | 2026-03-12 | MirrorBot selectivity tightening (confidence 0.55, reliability 0.52) |
| 81 | 2026-03-12 | RTDS live + paper_trades DB persistence fix |
| 82 | 2026-03-13 | EsportsBot calibrators (BiasDecomp, FocalTemp), MetaculusBenchmark |
| 83 | 2026-03-13 | EsportsBot P7 roadmap (9 items): HorizonBias, ONNX, team names, conformal, TabPFN, CoT, EGM d, edge decay |
| 85 | 2026-03-14 | Resolution backfill fix (3 root causes) + P&L data overhaul (10 bugs) |
| 86 | 2026-03-14 | Ingestion sync fix + RESOLUTION dedup attempt 1 (insufficient) |
| 87 | 2026-03-14 | RESOLUTION dedup fix (atomic INSERT...SELECT) + 4,878 dupes cleaned |
| **88** | **2026-03-14** | **Observation mode fix (false 48h block on restart) + esports_diag.py fixes** |

---

## WHAT THE NEXT AGENT SHOULD DO

1. **Read MEMORY.md + CLAUDE.md first** (mandatory)
2. **Run `verify_dedup.py` on VPS** — confirm still 1.0x
3. **Check scan logs** — confirm `observation: 0` in waterfall, trades executing
4. **Pick up outstanding items** based on user direction:
   - P2: Team name matching (12 markets/scan skipped) — most impactful for trade volume
   - P3: PandaScore timeout (EsportsLiveBot dead)
   - P3: EsportsSeriesBot investigation
5. **Before any code change**: Follow CLAUDE.md checklist (state bug, list files, grep dependents, read entire file)
6. **One fix per commit**. No "while I'm in here" refactors. Fix only what is broken.
7. **Paper trading IS production** — implement everything as if real capital is at risk
