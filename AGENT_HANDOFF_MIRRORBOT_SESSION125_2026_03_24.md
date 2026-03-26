# AGENT HANDOFF — MirrorBot Session 125 (2026-03-24)
## SCOPE: MirrorBot-primary, with shared infrastructure fixes (PM exits)
## SESSION TYPE: S124 deployment + Position Manager churn/ghost fix

---

## QUICK CONTEXT FOR NEW AGENT

You are continuing work on **MirrorBot**, one bot in a 15-bot Polymarket automated trading system. MirrorBot copies elite traders in real-time via RTDS WebSocket feed. The system is paper trading (`SIMULATION_MODE=true`) on an Ubuntu VPS at `34.251.224.21`. Real capital is NOT at risk yet but paper trading IS treated as production per CLAUDE.md.

**Read these files first (in order):**
1. `CLAUDE.md` — Prime directive, rules of engagement, critical traps
2. This file — Everything done in S125, current state, what to do next
3. `bots/mirror_bot.py` — Main bot code (~1700 lines)
4. `bots/mirror_ml_selector.py` — ML trade selector (312 lines)
5. `config/settings.py` — All `MIRROR_*` and `PM_*` config keys

**Do NOT read/modify other bot files** (weather_bot.py, esports_bot.py, etc.) unless explicitly asked. This is a MirrorBot-scoped session.

---

## SYSTEM ARCHITECTURE (MirrorBot only)

### Runtime Data Flow
```
RTDSWebSocket (base_engine/data/rtds_websocket.py)
  → receives ALL Polymarket trades globally
  → EliteWatchlist.on_rtds_trade() (bots/elite_watchlist.py)
    → O(1) watchlist lookup (top 500 traders from monthly leaderboard)
    → dedup by composite key
    → wash trader detection (M6)
    → fast pre-filter: _can_open_position(price) WITHOUT category
    → confidence from efficiency score
    → MirrorBot._execute_mirror_trade() (bots/mirror_bot.py)
      → 16 rejection gates (blocklist, cooldown, category, position cap, opposing-side, same-side dedup, market active, near resolution, slippage, reliability LR, confidence, sizing, dust)
      → multi-factor confidence: F1(category win rate) + F2(price edge) + F3(whale conviction)
      → ML selector shadow scoring (XGBoost + Q-learning + combo) — logs all 3 scores to event_data
      → optional live gate (MIRROR_USE_ML_SELECTOR=true blocks trades below threshold)
      → position sizing: Kelly via BotBankrollManager → reliability_mult → dampeners → per-market/daily caps
      → BaseBot.place_order() → OrderGateway → PaperTradingEngine
```

### Periodic Housekeeping (scan_and_trade, every ~45s)
```
scan_and_trade()
  ├── _restore_state_on_startup() [scan 1]
  ├── calibration fit [daily, DISABLED]
  ├── ML selector model load [scan 1: loads XGBoost + Q-table from models/]
  ├── adaptive safety refresh [DISABLED]
  ├── leader reconciliation [scan 3]
  ├── dedup flush, elite refresh, RTDS connect, daily reset, exits, etc.
```

### Key Files (MirrorBot scope)
| File | Lines | Purpose |
|------|-------|---------|
| `bots/mirror_bot.py` | ~1700 | Main bot: 16 gates, confidence, sizing, exits, ML selector integration |
| `bots/mirror_ml_selector.py` | 312 | MirrorMLSelector — XGBoost + Q-learning + combo scoring |
| `bots/elite_watchlist.py` | 561 | RTDS handler, watchlist refresh, wash detection |
| `base_engine/learning/elite_reliability.py` | ~200 | Bayesian Beta reliability scoring |
| `bots/mirror_calibration.py` | 110 | FTS + horizon calibration (DISABLED) |
| `bots/mirror_adaptive_safety.py` | 148 | Dynamic position limits (DISABLED) |
| `base_engine/execution/position_manager.py` | ~870 | Shared PM — S125 bot exclusion + S125b SL/TP failure handling |
| `scripts/train_mirror_ml_selector.py` | 265 | Offline training pipeline (XGBoost + Q-table bootstrap) |
| `scripts/ml_selector_shadow_analysis.py` | 130 | Three-ledger shadow comparison analysis |
| `tests/unit/test_mirror_ml_selector.py` | 290 | 24 unit tests for ML selector |
| `config/settings.py` | all MIRROR_* / PM_* | Configuration values |

---

## LIVE VPS CONFIG (as of S125 deploy)
```
SIMULATION_MODE=true (paper trading)
MIRROR_USE_CALIBRATION=false (disabled S117, re-enable ~Apr 5)
MIRROR_MIN_CONFIDENCE=0.45
MIRROR_MIN_RELIABILITY=0.52
MIRROR_MAX_CONCURRENT_POSITIONS=600
MIRROR_FAVORITE_DAMPENER=1.0 (S119: no-op for data collection)
MIRROR_EXTREME_PRICE_DAMPENER=1.0 (S119: no-op)
MIRROR_TOTAL_CAPITAL=20000
BOT_BANKROLL_CONFIG: MirrorBot capital=20000, kelly=0.25, max_bet=300, max_daily=999999 (UNCAPPED)
WATCHLIST_ENABLED=true, WATCHLIST_SIZE=500
PAPER_TAKER_FEE_BPS=150

ML SELECTOR CONFIG (S124):
MIRROR_USE_ML_SELECTOR=false (shadow mode — scores but never blocks)
MIRROR_ML_STRATEGY=xgb (which strategy when live: xgb | ql | combo)
MIRROR_ML_MIN_SCORE=0.45 (XGBoost rejection threshold when live)
MIRROR_ML_MODEL_PATH=models/mirror_ml_selector.pkl
MIRROR_ML_MAX_AGE_DAYS=14 (stale model guard)

POSITION MANAGER CONFIG (S125):
PM_EXCLUDE_BOTS=EsportsBot,MirrorBot,WeatherBot (these bots have own exit logic)
```

---

## WHAT WAS DONE THIS SESSION (S125)

### 1. S124 Commit + Deploy — ML Trade Selector
- **Committed** all S124 work (6 files, 1543 insertions):
  - `bots/mirror_ml_selector.py` (NEW)
  - `scripts/train_mirror_ml_selector.py` (NEW)
  - `scripts/ml_selector_shadow_analysis.py` (NEW)
  - `tests/unit/test_mirror_ml_selector.py` (NEW)
  - `bots/mirror_bot.py` (ML integration + NaN guard)
  - `config/settings.py` (+5 ML config keys)
- **Deployed to VPS** — release `20260323_215951`
- **Trained ML models on VPS** — `python scripts/train_mirror_ml_selector.py`
- **Verified**: `mirror_ml_selector_loaded xgb=True ql=True` in startup logs
- **Shadow mode active**: All trades scored with XGBoost + Q-learning + combo, scores persisted in `event_data`

### 2. S125: PM_EXCLUDE_BOTS — Stop Churn Loop Bleeding
**Bug**: Position Manager (PM) exits had no failure handling. When PM tried to exit an EsportsBot position and got "Insufficient position", it retried every 10s forever (pos 65655 proved this live). PM exits also don't set bot-level anti-churn (`_recently_exited`), so bots re-enter 2 seconds after PM exits. 93 churn loops across 15 markets, -$613 proven on one market.

**Fix**: Config-driven bot exclusion in PM. Bots with their own complete exit logic opt out.

**Bot Exit Logic Audit (informed the exclusion list):**
| Bot | Own Exits? | Types | Anti-Churn? | Safe to Exclude? |
|-----|-----------|-------|-------------|------------------|
| **MirrorBot** | YES | SL (graduated 15→5%), TP (25%), force (96h) | YES | YES |
| **EsportsBot** | YES | SL (15%), max-hold (72h) | YES (Redis) | YES |
| **WeatherBot** | PARTIAL | Stale cleanup (20h/date), no SL/TP | YES (Redis) | YES (stale cleanup sufficient) |
| All other 12 bots | NO | None | NO | NO — need PM |

**Changes:**
- `config/settings.py` line 404: `PM_EXCLUDE_BOTS = ["EsportsBot", "MirrorBot", "WeatherBot"]`
- `base_engine/execution/position_manager.py` line 510-513: Early return in `_check_position()` for excluded bots

**Commit**: `1a85437 fix(pm): S125 — exclude EsportsBot/MirrorBot/WeatherBot from PM exits`

**Rollback**: `export PM_EXCLUDE_BOTS="" && sudo systemctl restart polymarket-ai`

### 3. S125b: PM Stop-Loss/Take-Profit Failure Handling
**Bug**: `_execute_stop_loss()` and `_execute_take_profit()` in position_manager.py had zero failure handling — no cooldown on failure (infinite retry), no ghost position detection ("Insufficient position" loops forever). Meanwhile `_execute_exit()` (model reversal) had all three protections. Stop-loss and take-profit were simply never given the same treatment.

**Fix**: Ported the proven pattern from `_execute_exit()` (lines 630-711) into both methods:
1. **Cooldown check at entry** — skip if `position.id` in `_exit_cooldowns` (300s backoff)
2. **Zero-size guard** — skip if `position.size <= 0`
3. **Ghost position cleanup** — on "Insufficient position" error, mark closed in DB with pnl=0
4. **Failure cooldown** — on other errors, set 300s cooldown
5. **Exception cooldown** — on unhandled exception, set 300s cooldown
6. **Success cleanup** — clear cooldown on success

**Commit**: `44a15c5 fix(pm): S125b — port cooldown + ghost handling to stop-loss and take-profit`

**Blast radius**: All 15 bots, but change is purely additive (adds guards, doesn't alter success path). The 3 excluded bots won't even reach this code via PM_EXCLUDE_BOTS.

---

## THREE-WAY ML SHADOW RACE — STATUS & WORKFLOW

### Current Status
- **Models trained**: XGBoost + Q-table both loaded on VPS (`xgb=True, ql=True`)
- **Shadow scoring active**: Every trade gets all 3 strategy scores logged to `event_data`
- **Shadow start time**: ~2026-03-24 02:01 UTC (VPS restart after S124 deploy)
- **48h analysis window**: Ready ~2026-03-26 02:01 UTC

### Event Data Fields (persisted to trade_events on every ENTRY)
```json
{
  "ml_score_xgb": 0.5823,        // XGBoost P(win)
  "ml_decision_xgb": true,       // XGBoost: score >= 0.45?
  "ml_score_ql": 0.3412,         // Q-advantage: Q(trade) - Q(skip)
  "ml_q_trade": 0.5200,          // Raw Q-value for TRADE action
  "ml_q_skip": 0.1788,           // Raw Q-value for SKIP action
  "ml_decision_ql": true,        // Q-learning: Q(trade) > Q(skip)?
  "ml_score_combo": 0.6318,      // Average of normalized A and B scores
  "ml_decision_combo": true      // Both A and B agree?
}
```

### Shadow → Live Workflow
1. **Train**: `ssh ubuntu@34.251.224.21` → `cd /opt/polymarket-ai-v2 && source venv/bin/activate && python scripts/train_mirror_ml_selector.py` ✅ DONE
2. **Deploy shadow**: Restart service (default `MIRROR_USE_ML_SELECTOR=false`) ✅ DONE
3. **Wait 48h**: All trades scored, scores persisted ⏳ IN PROGRESS (~24h in)
4. **Analyze**: `PYTHONPATH=/opt/polymarket-ai-v2 python scripts/ml_selector_shadow_analysis.py --hours 48`
5. **Decide**: Pick winner based on P&L separation, win rate lift, trade count
6. **Activate**: `export MIRROR_ML_STRATEGY=xgb|ql|combo && export MIRROR_USE_ML_SELECTOR=true && sudo systemctl restart polymarket-ai`

---

## CURRENT SYSTEM STATE (post S125b deploy, 2026-03-24 ~17:05 UTC)

| Metric | Value |
|--------|-------|
| Service PID | 3269018, active since 17:05 UTC |
| MirrorBot | ✅ scanning, 583 open positions, RTDS dispatching 234K events |
| EsportsBot | ✅ scanning, 35 open positions, 15 live matches |
| WeatherBot | ✅ scanning, 76 open positions, 1500 weather markets |
| ML selector | ✅ Shadow mode, XGBoost + Q-table loaded, scoring all trades |
| PM exits for excluded bots | ✅ Zero since deploy |
| PM SL/TP failure handling | ✅ Deployed, cooldown + ghost handling active |
| Position 65655 retry loop | ✅ Dead |
| Tests | 1717 passed, 0 failed |
| Resolution backlog | 984 (602 Weather, 318 Mirror, 61 Esports) — draining via backfill |

---

## RESOLUTION BACKLOG (shared infrastructure, NOT mirror-scoped)

981→984 positions stuck as "closed" with no RESOLUTION event. This corrupts P&L tracking.

**Root causes (3 bugs):**
- **Timeout starvation**: Resolution backfill gets 300s (AUX_TIMEOUT). Needs 500s for 500 markets at 1s each.
- **NULL end_date_iso ordering bug**: S125 (esports session) fixed `priority_bot` but introduced tier ordering that puts NULL end_date markets at tier 1 (high priority) instead of tier 2 (lowest). These are unresolvable without enrichment but eat batch slots.
- **98% of stuck markets have NULL end_date_iso**: Gamma API omits `endDate` in list endpoints.

**Status**: Esports session is addressing this. 934 resolution events emitted in the first 35 min after restart. Backfill IS running but slowly. The ordering fix + timeout increase are separate PRs.

**NOT our fix** — this is shared infrastructure managed by the esports/system session. Our bots are draining naturally.

---

## ITEMS NOT YET DONE / NEXT PRIORITIES

### P0: ML Shadow Analysis (ready ~Mar 26)
- Wait for 48h window to complete
- Run: `PYTHONPATH=/opt/polymarket-ai-v2 python scripts/ml_selector_shadow_analysis.py --hours 48`
- Compare XGBoost vs Q-learning vs Combo vs status quo
- Pick winner, set `MIRROR_ML_STRATEGY` + `MIRROR_USE_ML_SELECTOR=true`

### P1: Raise MIRROR_MIN_CONFIDENCE
- S120 data showed 0.45-0.55 bucket is deeply negative (-$6,745 in 48h)
- ML selector may handle this better than a hard threshold
- Decision deferred until shadow analysis proves whether ML selector is better
- If ML selector doesn't help, raise to 0.55

### P2: R1b — Q-Learning Online Learning
- Once data volume reaches 10K+ resolved trades
- Upgrade from offline bootstrap to online learning with PER (Prioritized Experience Replay)
- ADWIN drift detection for concept drift
- Infrastructure already exists in `base_engine/execution/rl_trade_timing.py`

### P3: Calibration Re-enable (~Apr 5)
- `MIRROR_USE_CALIBRATION=true`
- FTS + horizon calibration in `bots/mirror_calibration.py`
- Need sufficient resolved trade data for fit

### P4: CLOB Credentials Setup
- Test script ready: `scripts/test_clob_order.py`
- Balance query + fill confirmation code deployed
- Needs env vars + funded wallet

---

## CRITICAL TRAPS (DO NOT VIOLATE)

1. **`MIRROR_USE_CALIBRATION=false`** — Do NOT re-enable until ~Apr 5
2. **`MIRROR_USE_ML_SELECTOR=false`** — Do NOT enable until shadow analysis proves value
3. **`max_daily_usd=999999`** — Intentionally uncapped for data collection
4. **`_entered_market_sides`** — Must be populated from trade_events on startup AND updated on execution
5. **`calibration_exclude` filter** — `WHERE COALESCE(event_data->>'calibration_exclude', '') = ''`
6. **`trade_events` immutability trigger** — Disable on ALL partitions before DELETE/UPDATE, re-enable after
7. **`_state_restored` guard** — If False, RTDS trades dropped
8. **Dead zone dampener is GONE** — Do not re-add
9. **Dampeners at 1.0** — `MIRROR_FAVORITE_DAMPENER=1.0`, `MIRROR_EXTREME_PRICE_DAMPENER=1.0`
10. **NO contrarian fix** — `NO and price < 0.45` is contrarian. Do NOT revert to `> 0.55`
11. **Category exposure restored on startup** — Own try/except to not break position restore
12. **Phase 4b ENTRY guard** — Resolution events only emitted when matching ENTRY exists
13. **Paper trading IS production** — Per CLAUDE.md
14. **YES/NO mandate** — `place_order()` requires `side="YES"/"NO"`. Never BUY/SELL
15. **trade_events is P&L authority** — Never read paper_trades for P&L
16. **PAPER_TAKER_FEE_BPS=150** — S120 default. NOT in VPS .env.
17. **ML selector scores are shadow-only by default** — `MIRROR_USE_ML_SELECTOR=false` means all trades pass through, scores just logged
18. **ML selector cold-start** — Returns 0.50 (no opinion) if < 300 training samples or model older than 14 days
19. **Q-table state space** — 216 states (3×3×2×3×4), NOT 324 like rl_trade_timing.py
20. **ML scores merged into event_data** — `_ml_scores` dict is `.update()`'d into `_event_data` before `place_order()`
21. **asyncpg JSONB** — `CAST(:x AS jsonb)` NOT `:x::jsonb`
22. **P&L analysis scripts** — Use `::numeric` NOT `::float` in SQL. Use `Decimal` in Python.
23. **PM_EXCLUDE_BOTS** — MirrorBot excluded from PM exits. MirrorBot has its own graduated stop-loss (15→10→5% by hold duration) + 96h force exit + TP at 25%. PM exits are redundant and were causing churn.
24. **PM SL/TP now have cooldown** — S125b added 300s cooldown + ghost cleanup to `_execute_stop_loss` and `_execute_take_profit`. If you modify PM, preserve these guards.
25. **Position 65655 pattern** — Ghost positions that return "Insufficient position" are now auto-cleaned (marked closed, pnl=0). Do not remove this ghost handling.

---

## VPS ACCESS
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
cd /opt/polymarket-ai-v2 && source venv/bin/activate

# Service
sudo systemctl restart polymarket-ai
sudo journalctl -u polymarket-ai -f | grep MirrorBot

# Deploy pattern (SCP single file)
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem <local_file> ubuntu@34.251.224.21:/tmp/
ssh ... "sudo -u polymarket cp /tmp/<file> /opt/polymarket-ai-v2/<path> && sudo systemctl restart polymarket-ai"

# DB access from VPS
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

## SAFETY AUDIT (carried from S124 — ALL 8 CATEGORIES)

| Category | Verdict | Detail |
|----------|---------|--------|
| Size resurrection | **SAFE** | No `max()` on size. Dust check rejects < $10. `size<=0` caught. |
| Exception swallowing | **LOW RISK** | Reliability tracker fail → neutral 1.0x. min_confidence 0.45 still applies. |
| Default/fallback values | **SAFE** | All fallbacks return 0.0. |
| Confidence inflation | **BOUNDED** | Hard-capped at 0.75. Per-bet cap $300. |
| Race conditions | **LOW RISK** | Asyncio cooperative. Can overshoot position cap by 1-3 trades. |
| Type coercion | **SAFE** | Type errors crash trade (fail-safe). |
| NaN propagation | **FIXED S124** | `_math_isfinite(size)` guard at L1572. |
| State corruption | **SAFE** | Exposure clamped to `max(0.0, ...)`. RTDS blocked until `_state_restored=True`. |

---

## GIT LOG (this session's commits)
```
44a15c5 fix(pm): S125b — port cooldown + ghost handling to stop-loss and take-profit
1a85437 fix(pm): S125 — exclude EsportsBot/MirrorBot/WeatherBot from PM exits
dad3ef7 feat(mirror): S124 — ML trade selector three-way shadow race (XGBoost + Q-learning + combo)
```

## TESTS
- **1717 passed, 0 failed** (full suite)

---

## UPCOMING MILESTONES

| Date | Milestone | Action |
|------|-----------|--------|
| ~Mar 26 02:00 UTC | 48h shadow window complete | Run `ml_selector_shadow_analysis.py`, pick ML winner |
| ~Mar 26 | Activate ML gate | `MIRROR_USE_ML_SELECTOR=true` + chosen strategy |
| ~Apr 5 | Calibration re-enable | `MIRROR_USE_CALIBRATION=true` |
| 10K+ trades | R1b: Online Q-learning | Upgrade offline → online PER+ADWIN |
| TBD | CLOB credentials | Set up on VPS, canary stage 1 |
| TBD | Resolution backlog fix | Shared infra — esports session handling |
