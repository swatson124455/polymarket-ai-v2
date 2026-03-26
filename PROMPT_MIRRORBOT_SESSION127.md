# MirrorBot Session 127 — Continuation Prompt
## SCOPE: MirrorBot-only. No bleed to other bots unless shared infra demands it.

---

## WHO YOU ARE

You are continuing work on **MirrorBot**, one bot in a 15-bot Polymarket automated paper trading system. MirrorBot copies elite traders in real-time via RTDS WebSocket feed. The system runs on an Ubuntu VPS at `34.251.224.21` with `SIMULATION_MODE=true`. Paper trading IS production per CLAUDE.md.

**Read these files first (in order):**
1. `CLAUDE.md` — Prime directive, rules of engagement, critical traps
2. `AGENT_HANDOFF_MIRRORBOT_SESSION125_2026_03_24.md` — S124/S125 full context
3. `AGENT_HANDOFF_MIRRORBOT_SESSION126_2026_03_24.md` — S126 changes
4. `bots/mirror_bot.py` — Main bot (~1700 lines)
5. `bots/mirror_ml_selector.py` — ML trade selector (312 lines)
6. `base_engine/data/resolution_backfill.py` — S126 changes (Phase 4b-alt + end_date fix)
7. `config/settings.py` — All `MIRROR_*` and `PM_*` config keys

**Do NOT read/modify other bot files** unless explicitly asked.

---

## SYSTEM ARCHITECTURE (MirrorBot)

### Runtime Data Flow
```
RTDSWebSocket (base_engine/data/rtds_websocket.py)
  → receives ALL Polymarket trades globally
  → EliteWatchlist.on_rtds_trade() (bots/elite_watchlist.py)
    → O(1) watchlist lookup (top 500 traders)
    → dedup, wash detection, fast pre-filter
    → MirrorBot._execute_mirror_trade() (bots/mirror_bot.py)
      → 16 rejection gates
      → multi-factor confidence: F1(category WR) + F2(price edge) + F3(whale conviction)
      → ML selector shadow scoring (XGBoost + Q-learning + combo) — scores logged to event_data
      → position sizing: Kelly via BotBankrollManager
      → BaseBot.place_order() → OrderGateway → PaperTradingEngine
```

### Key Files
| File | Purpose |
|------|---------|
| `bots/mirror_bot.py` | Main: 16 gates, confidence, sizing, exits, ML integration |
| `bots/mirror_ml_selector.py` | XGBoost + Q-learning + combo scoring |
| `bots/elite_watchlist.py` | RTDS handler, watchlist, wash detection |
| `base_engine/learning/elite_reliability.py` | Bayesian Beta reliability scoring |
| `base_engine/data/resolution_backfill.py` | Resolution event emission (S126: Phase 4b-alt) |
| `base_engine/execution/position_manager.py` | Shared PM — S125 bot exclusion + S125b SL/TP guards |
| `scripts/train_mirror_ml_selector.py` | Offline training (XGBoost + Q-table) |
| `scripts/ml_selector_shadow_analysis.py` | Three-ledger shadow comparison |
| `config/settings.py` | All MIRROR_* / PM_* config |

---

## LIVE VPS CONFIG (as of S126 deploy)
```
SIMULATION_MODE=true
MIRROR_USE_CALIBRATION=false (disabled S117, re-enable ~Apr 5)
MIRROR_MIN_CONFIDENCE=0.45
MIRROR_MIN_RELIABILITY=0.52
MIRROR_MAX_CONCURRENT_POSITIONS=600
MIRROR_FAVORITE_DAMPENER=1.0 (no-op for data collection)
MIRROR_EXTREME_PRICE_DAMPENER=1.0 (no-op)
MIRROR_TOTAL_CAPITAL=20000
BOT_BANKROLL_CONFIG: MirrorBot capital=20000, kelly=0.25, max_bet=300, max_daily=999999 (UNCAPPED)
WATCHLIST_ENABLED=true, WATCHLIST_SIZE=500
PAPER_TAKER_FEE_BPS=150

ML SELECTOR:
MIRROR_USE_ML_SELECTOR=false (shadow mode — scores but never blocks)
MIRROR_ML_STRATEGY=xgb
MIRROR_ML_MIN_SCORE=0.45
MIRROR_ML_MODEL_PATH=models/mirror_ml_selector.pkl
MIRROR_ML_MAX_AGE_DAYS=14

PM_EXCLUDE_BOTS=EsportsBot,MirrorBot,WeatherBot
```

---

## WHAT S124-S126 DID (cumulative)

### S124: ML Trade Selector (3-way shadow race)
- Created `bots/mirror_ml_selector.py` — XGBoost + Q-learning + combo scoring
- Created `scripts/train_mirror_ml_selector.py` — offline training pipeline
- Created `scripts/ml_selector_shadow_analysis.py` — 3-ledger comparison
- Integrated into `mirror_bot.py` — scores logged to `event_data` on every ENTRY
- 24 unit tests in `tests/unit/test_mirror_ml_selector.py`
- Committed: `dad3ef7`

### S125: PM_EXCLUDE_BOTS + SL/TP failure handling
- MirrorBot/EsportsBot/WeatherBot excluded from PM exits (their own exit logic is complete)
- Stop-loss and take-profit in position_manager.py got cooldown + ghost cleanup (ported from _execute_exit)
- Committed: `1a85437`, `44a15c5`

### S126: ML model fix + Resolution backlog
- **ML models retrained on VPS** — `models/` dir was missing, recreated, models trained (1167 samples, AUC=0.590). Now loading `xgb=True ql=True` on every restart.
- **Phase 4b-alt** added to `resolution_backfill.py` — position-based resolution path. Queries `positions WHERE status='closed'` + `markets.resolution IN ('YES','NO')` + no existing RESOLUTION event. Subtracts EXIT P&L to avoid double-counting.
- **Bogus end_date_iso cleanup** — CLOB-imported markets with `end_date_iso = 2020-11-04` nulled out when CLOB confirms still open.
- **NOT committed** — `resolution_backfill.py` changes are deployed to VPS but not in git.

---

## CURRENT STATE (as of end of S126, 2026-03-25 ~00:30 UTC)

| Metric | Value |
|--------|-------|
| MirrorBot | ✅ scanning, ~597 open positions, at position cap |
| ML selector | ✅ Loaded (xgb=True, ql=True), shadow scoring active |
| ML shadow data | ❌ Only 1 ML-scored entry — blocked by position cap |
| Phase 4b-alt | ❌ Has NOT fired — service keeps getting externally restarted before ingestion cycle completes |
| Resolution backlog | 313 Mirror / 622 Weather / 68 Esports = 1,176 total |
| Tests | 1717 passed, 0 failed |

### Active Blockers
1. **Service external restarts every ~5-7 min** — kills ingestion cycle before resolution backfill runs. Pre-existing, cause unknown (no crontab). Needs investigation.
2. **Position cap 597/600** — no room for new entries, ML shadow race can't collect data.
3. **`_check_price` NameError** — VPS has old mirror_bot.py code referencing a removed function. Needs full deploy sync.

---

## PRIORITY TODO LIST (ordered)

### P0: Investigate Service Restart Loop
The service keeps receiving `systemd Stopping polymarket-ai.service` every ~5-7 minutes. This is NOT from our code — it's external. Check:
- `sudo journalctl -u polymarket-ai --since '1 hour ago' | grep -i 'stopping\|sigterm\|signal\|oom\|kill'`
- `dmesg | grep -i oom`
- `sudo systemctl list-timers` (any timer restarting the service?)
- Look for deploy scripts or health checks sending restart signals
This blocks ALL ingestion/resolution work.

### P1: Position Cap — Free Up Space for ML Shadow Data
597/600 open positions. Options:
- **Raise cap**: `MIRROR_MAX_CONCURRENT_POSITIONS=800` (or higher). Pro: immediate. Con: more capital at risk.
- **Accelerate exits**: Force-exit the oldest/worst positions. The 96h force exit is already running but positions accumulate faster than they drain.
- **Lower force-exit threshold**: Currently 96h. Could reduce to 72h or 48h.
ML shadow race needs new entries flowing to collect comparison data.

### P1: Deploy Sync
VPS code is out of sync with git HEAD. The `_check_price` NameError proves this. Run full `deploy.sh` or manually sync all modified files. This will also pick up the resolution_backfill.py changes properly.

### P2: Verify Phase 4b-alt Fires
Once service restarts stop, watch: `journalctl -u polymarket-ai | grep '4b-alt'`
Expected: "Resolution backfill Phase 4b-alt: emitted N RESOLUTION events from positions"
If 0 emitted: all 313 markets are still unresolved on Polymarket (resolution=NULL). They'll drain naturally.

### P3: ML Shadow Race Analysis
Once 48h of ML-scored entries exist (need position cap freed first):
```bash
PYTHONPATH=/opt/polymarket-ai-v2 python scripts/ml_selector_shadow_analysis.py --hours 48
```
Compare XGBoost vs Q-learning vs Combo vs status quo. Pick winner.
Activate: `export MIRROR_ML_STRATEGY=xgb|ql|combo && export MIRROR_USE_ML_SELECTOR=true && sudo systemctl restart polymarket-ai`

### P4: Confidence Threshold Decision
S120 data: 0.45-0.55 confidence bucket is deeply negative (-$6,745 in 48h).
Options:
- ML selector handles it (preferred — data-driven)
- Raise `MIRROR_MIN_CONFIDENCE` to 0.55 (blunt instrument)
Deferred until shadow analysis proves ML selector value.

### P5: Future Roadmap
- **R1b: Q-Learning Online Learning** — upgrade from offline to online PER+ADWIN at 10K+ resolved trades
- **Calibration re-enable** — `MIRROR_USE_CALIBRATION=true` ~Apr 5
- **CLOB credentials** — test script ready (`scripts/test_clob_order.py`), needs env vars + funded wallet
- **Model persistence in deploy.sh** — `models/` dir must survive deploys

---

## RESOLUTION BACKLOG — DEEP DIVE

### The Problem (found in S126)
313 closed MirrorBot positions have ZERO RESOLUTION events. Root cause:

- **Phase 4b** (existing) reads `paper_trades` to emit RESOLUTION events
- `paper_trades.market_id` uses a different identifier than `positions.market_id` for the same logical market
- Result: 2,642 RESOLUTION events emitted from paper_trades match ZERO positions
- The 313 closed positions use market_ids that exist in `positions` and `trade_events ENTRY` but NOT in the resolution events

### The Data
```
trade_events ENTRY: 4,362 unique market_ids
trade_events RESOLUTION: 2,522 unique market_ids (all match an ENTRY)
positions: 865 unique market_ids
paper_trades ∩ ENTRY: 4,362 (100%)
paper_trades ∩ positions: 865 (17%)
RESOLUTION ∩ positions: 0 (0%) ← THE BUG
```

### Sub-bugs Found
| Bug | Count | Status |
|-----|-------|--------|
| Fully exited (EXIT >= ENTRY, closed correctly) | 160 | OK — S120 skip logic handles |
| Partially exited (EXIT < ENTRY, will double-count) | 117 | HANDLED — 4b-alt subtracts EXIT P&L |
| Ghost positions (closed, 0 trade_events) | 10 | Created 2026-03-18 in bulk, no audit trail |
| Markets still live on Polymarket | ~313 | Will resolve naturally when markets close |
| Bogus end_date_iso = 2020-11-04 | 28 | FIXED — nulled when CLOB confirms open |

### Phase 4b-alt (the fix, deployed)
Location: `resolution_backfill.py` ~line 486, after existing Phase 4b.
```python
# Queries positions directly (not paper_trades)
# Joins markets for resolution outcome
# Subtracts EXIT P&L to avoid double-counting
# Only fires when market is actually resolved (m.resolution IN ('YES','NO'))
# LIMIT 500, wrapped in try/except
```

---

## ML SHADOW RACE — FULL SPECIFICATION

### Event Data Fields (persisted on every ENTRY)
```json
{
  "ml_score_xgb": 0.5823,        // XGBoost P(win)
  "ml_decision_xgb": true,       // score >= 0.45?
  "ml_score_ql": 0.3412,         // Q-advantage: Q(trade) - Q(skip)
  "ml_q_trade": 0.5200,          // Raw Q(TRADE)
  "ml_q_skip": 0.1788,           // Raw Q(SKIP)
  "ml_decision_ql": true,        // Q(trade) > Q(skip)?
  "ml_score_combo": 0.6318,      // Average of normalized XGBoost + QL
  "ml_decision_combo": true      // Both agree?
}
```

### Model Details
- **XGBoost**: Binary classifier P(profitable_trade). Features: price, conf_price_adj, hour_utc, rel_mult, conf_composite, category_encoded. CV AUC=0.590. Brier: raw 0.2527, calibrated 0.2384.
- **Q-table**: 216 states (3×3×2×3×4). States: confidence_bucket × price_bucket × side_bucket × hour_bucket × category_bucket. 69/216 states visited, 28 prefer TRADE, 188 prefer SKIP.
- **Combo**: Average of normalized XGBoost score and Q-advantage. Decision = both agree.
- **Training data**: 1,167 resolved Mirror trades. Run: `python scripts/train_mirror_ml_selector.py`
- **Cold start guard**: Returns 0.50 (neutral) if < 300 samples or model > 14 days old.

### Shadow → Live Workflow
1. Train models on VPS ✅
2. Deploy shadow mode (`MIRROR_USE_ML_SELECTOR=false`) ✅
3. Wait 48h — all trades scored, persisted ⏳ BLOCKED by position cap
4. Analyze: `python scripts/ml_selector_shadow_analysis.py --hours 48`
5. Pick winner based on P&L separation, win rate lift
6. Activate: `MIRROR_USE_ML_SELECTOR=true` + `MIRROR_ML_STRATEGY=xgb|ql|combo`

---

## CRITICAL TRAPS (DO NOT VIOLATE)

1. **`MIRROR_USE_CALIBRATION=false`** — Do NOT re-enable until ~Apr 5
2. **`MIRROR_USE_ML_SELECTOR=false`** — Do NOT enable until shadow analysis proves value
3. **`max_daily_usd=999999`** — Intentionally uncapped for data collection
4. **`_entered_market_sides`** — Populated from trade_events on startup AND on execution
5. **`trade_events` immutability trigger** — Disable on ALL partitions before DELETE/UPDATE
6. **`_state_restored` guard** — If False, RTDS trades dropped
7. **Dead zone dampener is GONE** — Do not re-add
8. **Dampeners at 1.0** — Both `FAVORITE` and `EXTREME_PRICE` are no-ops
9. **NO contrarian fix** — `NO and price < 0.45` is contrarian. Do NOT revert
10. **Phase 4b ENTRY guard** — Resolution events only emitted when matching ENTRY exists
11. **Paper trading IS production** — Per CLAUDE.md
12. **YES/NO mandate** — `place_order()` requires `side="YES"/"NO"`. Never BUY/SELL
13. **trade_events is P&L authority** — Never read paper_trades for P&L
14. **PAPER_TAKER_FEE_BPS=150** — NOT in VPS .env, must be in settings.py
15. **ML selector cold-start** — Returns 0.50 if < 300 training samples or model > 14 days old
16. **Q-table 216 states** — NOT 324 like rl_trade_timing.py
17. **ML scores merged into event_data** — `_ml_scores.update()` before `place_order()`
18. **asyncpg JSONB** — `CAST(:x AS jsonb)` NOT `:x::jsonb`
19. **asyncpg DATE** — Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
20. **PM_EXCLUDE_BOTS** — MirrorBot excluded from PM exits
21. **PM SL/TP have cooldown** — S125b: 300s cooldown + ghost cleanup
22. **Position 65655 pattern** — Ghost "Insufficient position" auto-cleaned
23. **`_market_meta_cache`** — 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
24. **`resolution_backfill.py` is shared infra** — Phase 4b-alt + end_date fix affect ALL 15 bots
25. **Models NOT in git** — `models/*.pkl` files are VPS-only. Must retrain after deploy.
26. **trade_events partitioned by event_time** — Use `event_time` not `recorded_at` or `created_at` for date queries
27. **positions table** — NO `bot_name` column. Use `source_bot`. NO `closed_at`/`updated_at`. Only `opened_at` + `status`.

---

## DB SCHEMA QUICK REFERENCE

### trade_events (partitioned by event_time)
```
sequence_num, event_type, execution_mode, event_time, knowledge_time, recorded_at,
bot_name, market_id, token_id, correlation_id, order_id, side, size, price, fees,
realized_pnl, confidence, predicted_probability, model_version, model_name,
idempotency_key, event_data (JSONB — ML scores live here)
```
Partitions: `trade_events_2026_01` through `trade_events_2026_12` + `trade_events_default`

### positions
```
id, bot_id, source_bot, market_id, token_id, side, size, entry_price,
current_price, unrealized_pnl, opened_at, status, is_paper, entry_cost,
breakeven_price, trader_addresses
```
NO `bot_name`, NO `closed_at`, NO `updated_at`.

### paper_trades
```
id, order_id, market_id, token_id, bot_name, side, size, price, confidence,
created_at, resolution, resolved_at, realized_pnl, correlation_id, latency_ms,
status, submitted_at, filled_at
```
NO `metadata` JSONB column. NO `condition_id`.

---

## VPS ACCESS
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
cd /opt/polymarket-ai-v2 && source venv/bin/activate

# Service
sudo systemctl restart polymarket-ai
sudo journalctl -u polymarket-ai -f | grep MirrorBot

# Deploy single file
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem <local> ubuntu@34.251.224.21:/tmp/
ssh ... "sudo cp /tmp/<file> /opt/polymarket-ai-v2/<path> && sudo chown polymarket:polymarket /opt/polymarket-ai-v2/<path>"

# DB query template
PYTHONPATH=/opt/polymarket-ai-v2 python3 -c "
import asyncio
from sqlalchemy import text
async def main():
    from base_engine.data.database import Database
    db = Database()
    await db.init()
    async with db.get_session() as s:
        r = await s.execute(text('SELECT ...'))
        print(r.fetchall())
asyncio.run(main())
"
```

---

## GIT STATE
### Committed (on master, deployed)
```
44a15c5 fix(pm): S125b — port cooldown + ghost handling to stop-loss and take-profit
1a85437 fix(pm): S125 — exclude EsportsBot/MirrorBot/WeatherBot from PM exits
dad3ef7 feat(mirror): S124 — ML trade selector three-way shadow race
```

### Uncommitted (deployed to VPS, not in git)
- `base_engine/data/resolution_backfill.py` — Phase 4b-alt + bogus end_date fix

### Unstaged modifications (from git status)
Many files modified across multiple sessions. MirrorBot-relevant:
- `bots/mirror_bot.py` (M)
- `bots/mirror_ml_selector.py` (M)
- `bots/mirror_calibration.py` (M)
- `config/settings.py` (M)
- `base_engine/data/resolution_backfill.py` (M)
- `base_engine/data/database.py` (M)
- `base_engine/learning/elite_reliability.py` (M)

---

## SAFETY AUDIT (from S124)
| Category | Verdict |
|----------|---------|
| Size resurrection | SAFE — no max(), dust check < $10, size<=0 caught |
| Exception swallowing | LOW RISK — reliability fail → neutral 1.0x |
| Default/fallback | SAFE — all return 0.0 |
| Confidence inflation | BOUNDED — capped 0.75, per-bet $300 |
| Race conditions | LOW RISK — asyncio cooperative, overshoot 1-3 trades |
| Type coercion | SAFE — errors crash trade (fail-safe) |
| NaN propagation | FIXED S124 — `_math_isfinite(size)` guard |
| State corruption | SAFE — exposure clamped max(0.0,...), RTDS blocked until restored |

---

## SESSION HISTORY (MirrorBot lineage)
- **S92** (Mar 15): Conformal dampening, Kelly 0.25
- **S94** (Mar 16): Latency 2967→11.9ms, RTDS fast-path
- **S100-103** (Mar 17-18): L2 book walk, whale trade, category cap, confidence gate
- **S109** (Mar 19): 5 P&L root causes, condition_id enrichment
- **S111** (Mar 20): Multi-factor confidence, archive migration
- **S116** (Mar 22): Bot dead — flood purge, 7-phase recovery
- **S117** (Mar 22): Bot revived, calibration disabled, dampener removed
- **S119** (Mar 23): 5 bug fixes, dead code purge, dampener neutralization
- **S120** (Mar 23): Fee 150bps, balance query, fill confirm, CLOB test, slug fix
- **S124** (Mar 23): ML trade selector 3-way shadow race (XGBoost + Q-learning + combo)
- **S125** (Mar 24): PM_EXCLUDE_BOTS, SL/TP failure handling, ML deploy
- **S126** (Mar 24): ML model retrain, Phase 4b-alt resolution path, end_date fix
