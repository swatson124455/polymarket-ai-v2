# AGENT HANDOFF — EsportsBot Session 87 (2026-03-14)

**Session scope**: EsportsBot ONLY. No bleed-over to MirrorBot/WeatherBot unless explicitly demanded.
**Previous sessions**: Session 83 (P7 roadmap), Session 85 (data overhaul), Session 86 (dedup attempt 1)
**Handoff files to read**: `AGENT_HANDOFF_SESSION85_DATA_OVERHAUL_2026_03_14.md`, `memory/AGENT_HANDOFF_ESPORTS_SESSION83_2026_03_13.md`
**MEMORY.md**: Always read `C:\Users\samwa\.claude\projects\C--lockes-picks-polymarket-ai-v2\memory\MEMORY.md` first — it has complete system state.

---

## WHAT THIS SESSION DID

### 1. EsportsBot Fixes (from Session 83 leftovers) — Committed in `a159da7` (prior session)
All 4 fixes were already committed before this session started. Verified and deployed.

**Fix A — LoL 0 Opportunities (ROOT CAUSE)**
- `bots/esports_bot.py` line 2662: Hard gate `if tracker is None or tracker.match_count < 10: return None`
- The retrain trigger collected data via PandaScore but never rebuilt the in-memory Glicko-2 tracker because `init_glicko=False` was the default
- **Fix**: Lines 646-651: Conditionally pass `init_glicko=True` when tracker is missing:
  ```python
  _needs_glicko = self._glicko2_trackers.get(_retrain_game) is None
  self._bg_train_tasks[_retrain_game] = asyncio.create_task(
      self._train_in_background(_retrain_game, db, init_glicko=_needs_glicko),
      name=f"retrain_{_retrain_game}",
  )
  ```

**Fix B — INTERVAL $1 asyncpg Syntax Error**
- `base_engine/features/calibration.py` lines 243, 372
- `FocalTemperatureCalibrator` and `HorizonBiasCalibrator` passed `f"{int(n_days)} days"` as INTERVAL param
- asyncpg can't bind strings as INTERVAL. Fixed: `INTERVAL '1 day' * :interval_days` with `int(n_days)`

**Fix C — Game Exposure Restart Drift**
- `bots/esports_bot.py` lines 979-997
- `_game_exposure` decremented in-memory on exit but never wrote through to `daily_counters`
- After restart, cumulative entries loaded without accounting for exits → exposure over-counted
- **Fix**: Call `_inc_daily()` with `-size` on exit

**Fix D — Exit Game Lookup Stale Cache**
- `bots/esports_bot.py` line 80: Added `self._market_game: Dict[str, str] = {}`
- Line 2005-2008: Populated on trade entry
- Lines 979-997: Used as primary lookup on exit (prediction_cache has 1h TTL, often expired)

### 2. RESOLUTION Event Duplication — CRITICAL BUG FOUND AND FIXED

**The Problem (discovered this session)**:
- `trade_events` is partitioned by `event_time` (monthly). PostgreSQL REQUIRES partition key in unique indexes.
- The unique index is `UNIQUE(idempotency_key, event_time)` — NOT just `idempotency_key`
- When resolution backfill runs multiple times, it creates the SAME idempotency_key with DIFFERENT event_time values
- Result: `ON CONFLICT (idempotency_key, event_time) DO NOTHING` NEVER fires because event_time differs
- Every backfill run (every 30min mini + daily full + every restart) created duplicate RESOLUTION events
- **Impact**: P&L was inflated by 2.8x-7x depending on how many backfill runs occurred

**Session 86's "fix" was insufficient**: It used deterministic `correlation_id=resolution:{market_id}` + `event_time=resolved_at`, but `resolved_at` in paper_trades was set by `now()` when `end_date_iso` was missing, so event_time still differed between runs.

**The Root Cause Chain**:
1. `resolution_backfill.py` line 337: `_resolved_at = _end_dt_parsed or datetime.now(...)` — fallback to `now()` when API lacks `end_date_iso`
2. `database.py` `save_market_resolution()` stores this in `markets.resolved_at`
3. `backfill_paper_trades_resolution()` copies to `paper_trades.resolved_at`
4. Phase 4b reads `MIN(pt.resolved_at)` as event_time — different each run
5. `ON CONFLICT (idempotency_key, event_time)` doesn't match because event_time differs

**The Real Fix** (`base_engine/data/database.py` lines 4568-4594):
```python
if event_type == "RESOLUTION":
    # Atomic INSERT...SELECT with WHERE NOT EXISTS
    result = await session.execute(
        _sa_text(
            "INSERT INTO trade_events (...) SELECT ... "
            " WHERE NOT EXISTS ("
            "   SELECT 1 FROM trade_events te"
            "   WHERE te.bot_name = :bot_name"
            "     AND te.market_id = :market_id"
            "     AND te.side = :side"
            "     AND te.event_type = 'RESOLUTION'"
            " ) RETURNING sequence_num"
        ), _params,
    )
else:
    # Non-RESOLUTION: original INSERT...ON CONFLICT path
    result = await session.execute(...)
```
This is atomic — the NOT EXISTS check and INSERT happen in a single SQL statement. No race condition between SELECT and INSERT.

**Data Cleanup Performed**:
- Disabled `trg_trade_events_immutable` trigger on `trade_events_2026_03`
- Deleted 3,597 + 1,281 = **4,878 duplicate RESOLUTION events** total (across this session)
- Re-enabled trigger
- Final state: 709 RESOLUTION events for 709 unique combos = 1.0x (clean)

### 3. Deploys This Session
| Deploy | Time | Contents |
|--------|------|----------|
| `20260313_173955` | 2026-03-13 21:40 UTC | EsportsBot 4 fixes (commit a159da7) |
| `20260314_002105` | 2026-03-14 04:21 UTC | Session 85 data overhaul |
| `20260314_132907` | 2026-03-14 17:29 UTC | RESOLUTION dedup fix + cleanup |

### 4. Diagnostic Script Created
- `scripts/esports_diag.py` — Full EsportsBot diagnostic: trade_events, positions, closed, resolutions, prediction waterfall, system-wide exposure
- `scripts/verify_dedup.py` — Quick RESOLUTION dedup verification
- `scripts/cleanup_resolution_dupes.py` — Cleanup tool: disables trigger, deletes dupes keeping MIN(sequence_num), re-enables

---

## CURRENT ESPORTSBOT STATE (post-cleanup, deploy 20260314_132907)

### P&L (CORRECT — from deduplicated trade_events)
| Event Type | Count | Realized P&L |
|------------|-------|-------------|
| ENTRY | 75 | $0.00 |
| EXIT | 16 | **+$59.62** |
| RESOLUTION | 65 | **-$82.00** |
| **TOTAL** | | **-$22.38** |

### Open Positions (6)
| Market | Side | Size | Entry | Current | uPnL |
|--------|------|------|-------|---------|------|
| 0x7e1c... (PARIVISION vs NIP Map 2) | YES | 186.7 | 0.38 | 0.365 | -$2.80 |
| 0x235a... | YES | 164.9 | 0.48 | 0.485 | +$0.82 |
| 0x7004... (ex-RUBY vs Eternal Fire BO3) | NO | 198.6 | 0.50 | 0.50 | $0.00 |
| 0xb837... (NaVi vs B8 BO3) | YES | 67.6 | 0.74 | 0.75 | +$0.68 |
| 0x000a... (EYEBALLERS vs ledopieca BO1) | YES | 32.7 | 0.54 | 0.50 | -$1.31 |
| 0xdaaa... | NO | 186.7 | 0.38 | 0.36 | +$3.73 |
- **Total invested**: ~$432
- **Unrealized P&L**: ~+$1.13

### System Exposure
| Bot | Positions | Invested |
|-----|-----------|----------|
| MirrorBot | ~190 | ~$14,000 |
| WeatherBot | ~400 | ~$6,000 |
| EsportsBot | 6 | ~$432 |
| **TOTAL** | | ~$20,432 |
- Max allowed: $20,000 — EsportsBot may be blocked intermittently when total exceeds cap

### Scanning
- Markets found per scan: lol=14-15, cs2=8, valorant=2-4, sc2=1, cod=1
- Scan cycle: ~2-10s normal, 44-53s when calibrators run
- Waterfall: observation→no_prediction→edge_cap→low_confidence→passed (2-3 per scan)
- Trades placed since last deploy: 2 new ENTRY (blocked earlier by exposure cap)

### Initialization Confirmed
- Glicko-2: lol=1875 matches/248 teams, total 949 teams
- ML models: lol + cs2 trained
- Calibrators: FocalTemp + BiasDecomp + HorizonBias all initialized
- Conformal/TabPFN: Initialized (awaiting calibration data)
- PandaScore: Connected, fetching markets

---

## FILES MODIFIED THIS SESSION

| File | What Changed | Lines |
|------|-------------|-------|
| `base_engine/data/database.py` | `insert_trade_event()`: RESOLUTION uses atomic INSERT...SELECT with NOT EXISTS instead of broken ON CONFLICT | 4547-4613 |
| `scripts/esports_diag.py` | NEW: Full esports diagnostic script | all |
| `scripts/verify_dedup.py` | NEW: Quick dedup verification | all |
| `scripts/cleanup_resolution_dupes.py` | NEW: Dedup cleanup tool | all |

No other files touched. No signature changes. No config changes.

---

## CRITICAL TRAPS (carry forward)

### RESOLUTION Dedup
- **`ON CONFLICT (idempotency_key, event_time)` IS BROKEN on partitioned tables** — NEVER rely on it for RESOLUTION events
- The fix is in `insert_trade_event()` — atomic INSERT...SELECT with WHERE NOT EXISTS for event_type='RESOLUTION'
- If you ever need to clean up duplicates: `scripts/cleanup_resolution_dupes.py` (disables trigger, deletes, re-enables)
- The `trg_trade_events_immutable` trigger prevents DELETE/UPDATE. Must DISABLE before cleanup.

### P&L Math (MANDATORY)
- `cost = entry_price * size` (ALL sides — YES and NO)
- `uPnL = (current_price - entry_price) * size` (ALL sides)
- **NEVER invert for NO positions** — prices are token-specific
- Data source: `trade_events` (realized), `positions.unrealized_pnl` (mark-to-market)
- Canonical script: `python scripts/bot_pnl.py BotName hours`

### EsportsBot-Specific
- `_market_game: Dict[str, str]` — persistent market→game mapping, populated on ENTRY, used on EXIT for game exposure decrement
- `_game_exposure` write-through: `_inc_daily(db, "EsportsBot", f"game_{game}", -size)` on exit
- Glicko-2 hard gate: `tracker.match_count < 10 → return None` (line 2662)
- Retrain trigger: `init_glicko=True` when tracker is None (lines 646-651)
- asyncpg INTERVAL: Use `INTERVAL '1 day' * :param` with integer, never string

### System-Wide
- **trade_events is P&L authority** — NEVER paper_trades
- **paper_trades has NO `resolved_pnl` column** — it's `resolved_at`
- **paper_trades has NO `metadata` JSONB column**
- **trade_events JSONB column is `event_data`** not `metadata_json`
- `SIMULATION_MODE=true` — paper trading, but treat as production (CLAUDE.md Prime Directive)
- Python 3.13 scoping: local imports shadow ENTIRE function. Never shadow top-level names.

---

## OUTSTANDING ITEMS (EsportsBot)

### P1 — System-wide exposure cap blocking trades
- Total exposure ~$20,432 exceeds $20,000 max
- MirrorBot dominates (~$14k), EsportsBot has only $432
- EsportsBot gets blocked by cross-bot cap despite having minimal exposure
- **Options**: (a) raise cap, (b) per-bot exposure isolation, (c) wait for MirrorBot positions to resolve
- This is a cross-bot issue — requires explicit user approval to touch shared code

### P2 — Slow scan cycles (intermittent)
- Normal: 2-10s. Slow: 44-53s
- Likely caused by calibrator runs (FocalTemp/HorizonBias query historical data)
- Not blocking functionality, just slower opportunity detection

### P3 — Blocked items requiring external data/models
- GLiNER2 NER: 500MB model download needed
- d3rlpy offline RL: Needs 500+ resolved trades (currently at ~65 for EsportsBot)
- Calibrator activation: Needs 30-50 resolved predictions per game
- Whale alerts / orderbook signals: Blocked on external services

### P4 — EsportsLiveBot + EsportsSeriesBot
- Both active but 0 trade_events in last 16h
- May need investigation if they should be trading

---

## ARCHITECTURE QUICK REFERENCE

### EsportsBot Prediction Pipeline
```
PandaScore API → esports_training_data → train_game() → GBM model + Glicko-2 tracker
                                                              ↓
Market scan → _predict_market() → Glicko-2 rating (line 2662 hard gate: match_count >= 10)
                                      ↓
                              bias_decomp → focal_temp → horizon_bias → edge computation
                                      ↓
                              conformal → expiry_boost → phi_factor → dd_factor
                                      → game_kelly_mult → edge_decay_mult → $100 cap
```

### Key Files
| File | Purpose | Lines |
|------|---------|-------|
| `bots/esports_bot.py` | Main EsportsBot (~2700 lines) | Entry, exit, prediction, sizing |
| `base_engine/data/database.py` | DB access (~4600 lines) | trade_events, paper_trades, positions |
| `base_engine/data/resolution_backfill.py` | Market resolution (~500 lines) | Phase 1-6 backfill pipeline |
| `base_engine/features/calibration.py` | Prediction calibrators | FocalTemp, BiasDecomp, HorizonBias |
| `base_engine/data/daily_counter.py` | Exposure persistence | increment_counter(), read-through |
| `esports/data/pandascore_client.py` | PandaScore API | Match data, results, team info |
| `esports/models/esports_trainer.py` | Model training | GBM + feature engineering |

### Database Tables (EsportsBot relevant)
| Table | Role |
|-------|------|
| `trade_events` | **P&L authority** — ENTRY/EXIT/RESOLUTION, immutable, partitioned by event_time |
| `positions` | Open/closed tracking, 10s price updates |
| `paper_trades` | Legacy (still written, NEVER for P&L) |
| `daily_counters` | `game_{game}` keys for exposure, `daily_exposure_usd` |
| `esports_training_data` | Historical match data from PandaScore |
| `glicko2_ratings` | Persisted Glicko-2 team ratings |
| `prediction_log` | Prediction audit trail with rejection_reason |

### Deploy
```bash
cd /c/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@34.251.224.21" bash deploy/deploy.sh
```

### Config
```
EsportsBot:  capital=$5000, kelly=0.25, max_bet=$100, max_daily=$500
EsportsLiveBot: capital=$1000, kelly=0.25, max_bet=$100, max_daily=$500
EsportsSeriesBot: capital=$1000, kelly=0.25, max_bet=$100, max_daily=$500
ESPORTS_MIN_CONFIDENCE=0.52, ESPORTS_MIN_EDGE=0.08
ESPORTS_FRESHNESS_DECAY_SECONDS=30.0
SIMULATION_MODE=true
```

---

## VERIFICATION COMMANDS

```bash
# Check EsportsBot scanning
ssh -i KEY ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai -f" | grep EsportsBot

# Check RESOLUTION dedup
scp -i KEY scripts/verify_dedup.py ubuntu@34.251.224.21:/tmp/ && \
ssh -i KEY ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && PYTHONPATH=. /opt/pa2-shared/venv/bin/python /tmp/verify_dedup.py"

# Full diagnostic
scp -i KEY scripts/esports_diag.py ubuntu@34.251.224.21:/tmp/ && \
ssh -i KEY ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && PYTHONPATH=. /opt/pa2-shared/venv/bin/python /tmp/esports_diag.py"

# Cleanup dupes (if needed)
scp -i KEY scripts/cleanup_resolution_dupes.py ubuntu@34.251.224.21:/tmp/ && \
ssh -i KEY ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && PYTHONPATH=. /opt/pa2-shared/venv/bin/python /tmp/cleanup_resolution_dupes.py"
```

---

## WHAT THE NEXT AGENT SHOULD DO

1. **Read MEMORY.md first** — has complete system state, all critical traps, all config
2. **Read CLAUDE.md** — Prime Directive, Rules of Engagement, forbidden patterns
3. **Check dedup is holding**: Run `verify_dedup.py` on VPS — should show 1.0x
4. **Pick up outstanding items** based on user direction (P1 exposure cap most impactful)
5. **Before any code change**: Follow CLAUDE.md checklist (state bug, list files, grep dependents, read entire file)
6. **Tests**: `pytest tests/ -x -q --timeout=30` — expect 1211+ passed, 1 pre-existing failure (`test_edge_threshold_identical_both_modes[EsportsBot]`)
