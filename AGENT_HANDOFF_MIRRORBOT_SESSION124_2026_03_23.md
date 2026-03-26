# AGENT HANDOFF — MirrorBot Session 124 (2026-03-23)
## SCOPE: MirrorBot ONLY — no other bot changes
## SESSION TYPE: ML Trade Selector (three-way shadow race: XGBoost / Q-learning / combo)

---

## QUICK CONTEXT FOR NEW AGENT

You are continuing work on **MirrorBot**, one bot in a 15-bot Polymarket automated trading system. MirrorBot copies elite traders in real-time via RTDS WebSocket feed. The system is paper trading (`SIMULATION_MODE=true`) on an Ubuntu VPS at `34.251.224.21`. Real capital is NOT at risk yet but paper trading IS treated as production per CLAUDE.md.

**Read these files first:**
- `CLAUDE.md` — Prime directive, rules of engagement, critical traps
- `AGENT_HANDOFF_MIRRORBOT_SESSION120_2026_03_23.md` — Prior session (production readiness, fee, fill confirmation, 48h P&L analysis)
- `.claude/plans/starry-waddling-fairy.md` — Approved implementation plan (three-way shadow race design)
- This file — Everything done in S124

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
      → S124: ML selector shadow scoring (XGBoost + Q-learning + combo) — logs all 3 scores to event_data
      → S124: optional live gate (MIRROR_USE_ML_SELECTOR=true blocks trades below threshold)
      → position sizing: Kelly via BotBankrollManager → reliability_mult → dampeners → per-market/daily caps
      → BaseBot.place_order() → OrderGateway → PaperTradingEngine
```

### Periodic Housekeeping (scan_and_trade, every ~45s)
```
scan_and_trade()
  ├── _restore_state_on_startup() [scan 1]
  ├── calibration fit [daily, DISABLED]
  ├── S124: ML selector model load [scan 1: loads XGBoost + Q-table from models/]
  ├── adaptive safety refresh [DISABLED]
  ├── leader reconciliation [scan 3]
  ├── dedup flush, elite refresh, RTDS connect, daily reset, exits, etc.
```

### Key Files (MirrorBot scope)
| File | Lines | Purpose |
|------|-------|---------|
| `bots/mirror_bot.py` | ~1700 | Main bot: 16 gates, confidence, sizing, exits, S124 ML selector integration |
| `bots/mirror_ml_selector.py` | 312 | **NEW S124**: MirrorMLSelector — XGBoost + Q-learning + combo scoring |
| `bots/elite_watchlist.py` | 561 | RTDS handler, watchlist refresh, wash detection |
| `base_engine/learning/elite_reliability.py` | ~200 | Bayesian Beta reliability scoring |
| `bots/mirror_calibration.py` | 110 | FTS + horizon calibration (DISABLED) |
| `bots/mirror_adaptive_safety.py` | 148 | Dynamic position limits (DISABLED) |
| `scripts/train_mirror_ml_selector.py` | 265 | **NEW S124**: Offline training pipeline (XGBoost + Q-table bootstrap) |
| `scripts/ml_selector_shadow_analysis.py` | 130 | **NEW S124**: Three-ledger shadow comparison analysis |
| `tests/unit/test_mirror_ml_selector.py` | 290 | **NEW S124**: 24 unit tests for ML selector |
| `config/settings.py` | all MIRROR_* | Configuration values, S124: +5 ML selector keys |

---

## LIVE VPS CONFIG (as of this session)
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

NEW S124 CONFIG KEYS:
MIRROR_USE_ML_SELECTOR=false (default — shadow mode, scores but never blocks)
MIRROR_ML_STRATEGY=xgb (which strategy to use when live: xgb | ql | combo)
MIRROR_ML_MIN_SCORE=0.45 (XGBoost rejection threshold when live)
MIRROR_ML_MODEL_PATH=models/mirror_ml_selector.pkl
MIRROR_ML_MAX_AGE_DAYS=14 (stale model guard)
```

---

## WHAT WAS DONE THIS SESSION (S124)

### R1: ML Trade Selector — Three-Way Shadow Race

Built a complete ML trade selector with three independent strategies that score every trade in shadow mode. After 48h of shadow data, the analysis script compares all three to pick the best strategy before activating it as a live gate.

#### New File: `bots/mirror_ml_selector.py` (312 lines)
- **MirrorMLSelector class** with three scoring strategies:
  - **A: XGBoost** — Binary classifier, P(trade wins), with isotonic calibration. Cold-start guard (refuses if < 300 training samples). Stale model guard (refuses if > 14 days old).
  - **B: Tabular Q-learning** — 216-state Q-table (conf×price×side×reliability×hour × 2 actions). Adapted from `base_engine/execution/rl_trade_timing.py` pattern.
  - **C: Combo** — Both A and B must agree to accept.
- `score_trade(features)` → returns dict with all 3 scores + decisions
- `should_block(scores)` → checks active strategy's decision (only used when `MIRROR_USE_ML_SELECTOR=true`)
- `load_xgb()` / `load_qtable()` / `load_all()` — pickle load with guards
- `encode_category(cat)` — target-encoded category lookup
- Q-learning state discretization: 3×3×2×3×4 = 216 states

#### New File: `scripts/train_mirror_ml_selector.py` (265 lines)
- Extracts resolved MirrorBot trades from `trade_events` + `paper_trades`
- 12 features (all already computed in `_execute_mirror_trade`):
  - `conf_base`, `conf_price_adj`, `conf_conv_adj`, `rel_mult`, `price`, `whale_trade_usd`, `category_encoded`, `consensus`, `hour_utc`, `side_is_no`, `price_extremity`, `conf_composite`
- XGBoost: `TimeSeriesSplit` 5-fold CV, `scale_pos_weight=auto`, `max_depth=3`, isotonic calibration on OOF predictions
- Q-table: Offline bootstrap with 5 replay epochs, off-policy updates for both TRADE and SKIP actions
- Saves: `models/mirror_ml_selector.pkl`, `models/mirror_ml_qtable.pkl`, `models/mirror_ml_selector_meta.json`
- CLI: `python scripts/train_mirror_ml_selector.py [--days 90] [--min-samples 300]`

#### New File: `scripts/ml_selector_shadow_analysis.py` (130 lines)
- Queries `trade_events` where `ml_score_xgb` is present in `event_data`
- Joins with resolution events for realized P&L
- Prints three-ledger comparison table:
  ```
  Strategy            Bucket     N   Win%    Avg P&L    Total P&L      Worst
  A: XGBoost          ACCEPT   ...   ...%   $...       $...          $...
                      REJECT   ...   ...%   $...       $...          $...
  B: Q-learning       ACCEPT   ...   ...%   $...       $...          $...
                      REJECT   ...   ...%   $...       $...          $...
  C: Combo (A+B)      ACCEPT   ...   ...%   $...       $...          $...
                      REJECT   ...   ...%   $...       $...          $...
  STATUS QUO          ALL      ...   ...%   $...       $...          $...
  ```
- Recommends best strategy based on P&L lift vs status quo
- CLI: `python scripts/ml_selector_shadow_analysis.py [--hours 48]`

#### Modified: `config/settings.py` (+5 keys)
- `MIRROR_USE_ML_SELECTOR` (bool, default false) — shadow vs live gate
- `MIRROR_ML_STRATEGY` (str, default "xgb") — which strategy to use when live
- `MIRROR_ML_MIN_SCORE` (float, default 0.45) — XGBoost rejection threshold
- `MIRROR_ML_MODEL_PATH` (str, default "models/mirror_ml_selector.pkl")
- `MIRROR_ML_MAX_AGE_DAYS` (int, default 14) — stale model guard

#### Modified: `bots/mirror_bot.py` (+3 integration points)
1. **`__init__`** (~line 121): Instantiates `MirrorMLSelector` (lazy import, try/except)
2. **`scan_and_trade`** (~line 504): Loads models on first scan via `load_all()`
3. **`_execute_mirror_trade`** (~line 1479): After multifactor confidence, before calibration:
   - Builds feature dict from all 12 already-computed values
   - Calls `score_trade()` → gets all 3 strategy scores
   - If `MIRROR_USE_ML_SELECTOR=true` and active strategy rejects → `return False`
   - Merges all scores into `_event_data` for persistence in `trade_events`

#### New File: `tests/unit/test_mirror_ml_selector.py` (24 tests)
- Default state, cold-start, should_block with no models
- XGBoost: load, cold-start guard, predict loaded/unloaded
- Q-learning: load, predict, trade-preferred Q-values
- State discretization: bounds, different inputs → different states
- Three-way scoring: both loaded, XGBoost rejects low score
- should_block: xgb/ql/combo/unknown strategy routing
- Category encoding: known/unknown/empty

### R2: Sample-Size Ramp (Already Implemented)
- **Already in working tree** from prior session:
  - `elite_reliability.py:195-201` — `total_trade_count()` method
  - `mirror_bot.py:1385-1389` — `min(1.0, _eq_n / 50)` ramp on `reliability_mult`
- Behavior: 0 trades = 0x sizing, 25 trades = 0.5x, 50+ = full size
- No new changes needed — just documenting it's done.

### Backlog Cleanup
- **R3 (Conformal Prediction Intervals)** — DROPPED from backlog
- **R4 (Price Direction Pre-Filter)** — Already implemented in `mirror_bot.py:1353-1362`, removed from to-do
- **R5 (Controlled Averaging-Up)** — DROPPED from backlog

---

## THREE-WAY SHADOW RACE — HOW IT WORKS

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
1. **Train**: SSH to VPS, run `python scripts/train_mirror_ml_selector.py`
2. **Deploy shadow**: Restart service (default `MIRROR_USE_ML_SELECTOR=false`)
3. **Wait 48h**: All trades scored, scores persisted in event_data
4. **Analyze**: `python scripts/ml_selector_shadow_analysis.py --hours 48`
5. **Decide**: Pick winner based on P&L separation, win rate lift, trade count
6. **Activate**: `export MIRROR_ML_STRATEGY=xgb|ql|combo && export MIRROR_USE_ML_SELECTOR=true && sudo systemctl restart polymarket-ai`

### Future Upgrade: R1b (Q-Learning Online Learning)
Once data volume reaches 10K+ resolved trades, can upgrade Q-learning from offline bootstrap to online learning with PER (Prioritized Experience Replay) and ADWIN drift detection. Infrastructure already exists in `base_engine/execution/rl_trade_timing.py`. No new dependencies needed.

---

## CURRENT STATE (post S124)

| Metric | Value |
|--------|-------|
| Service | active |
| Open positions | 600 (at cap) |
| Daily exposure | ~$30k (uncapped) |
| Entry rate | ~420/day |
| ML selector | NOT YET DEPLOYED — code in working tree, needs training + deploy |
| Tests | 1595 passed, 0 failed |

---

## ITEMS NOT YET DONE / PENDING USER DECISION

### P0: Train + Deploy ML Selector
- Code complete and tested (1595 tests pass)
- **Next steps**:
  1. Commit changes
  2. Deploy to VPS
  3. Run `python scripts/train_mirror_ml_selector.py` on VPS (needs DB access)
  4. Restart service → shadow mode active
  5. Wait 48h → run `python scripts/ml_selector_shadow_analysis.py`
  6. User decides which strategy to activate

### P1: Raise MIRROR_MIN_CONFIDENCE
- Data strongly supports raising from 0.45 → 0.55 (0.50-0.55 bucket is -$6,745 in 48h)
- ML selector may handle this better than a hard threshold
- Decision deferred until shadow analysis shows whether ML selector is better

### P2: Deploy slug fix
- `database.py` slug collision pre-check (in working tree, not committed)

### P3: CLOB Credentials Setup
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
19. **Q-table state space** — 216 states (3×3×2×3×4), NOT 324 like rl_trade_timing.py. Different discretization bins tuned for MirrorBot features.
20. **ML scores merged into event_data** — `_ml_scores` dict is `.update()`'d into `_event_data` before `place_order()`. All 8 keys persist.
21. **asyncpg JSONB** — `CAST(:x AS jsonb)` NOT `:x::jsonb`
22. **P&L analysis scripts** — Use `::numeric` NOT `::float` in SQL. Use `Decimal` in Python.

---

## SAFETY AUDIT (S124 — ALL 8 CATEGORIES)

Full audit of every possible way MirrorBot could fire a trade it shouldn't:

| Category | Verdict | Detail |
|----------|---------|--------|
| Size resurrection (WeatherBot bug) | **SAFE** | No `max()` on size. Dust check is reject-gate (`< $10 → return False`). `size<=0` caught at L1617. |
| Exception swallowing | **LOW RISK** | Reliability tracker fail → neutral 1.0x (not blocked, but min_confidence 0.45 still applies). |
| Default/fallback values | **SAFE** | All fallbacks return 0.0. T10 fix removed old $100*conf fallback. |
| Confidence inflation | **BOUNDED** | Hard-capped at 0.75 by multifactor L1467. Signal enhancements disabled. Per-bet cap $300. |
| Race conditions | **LOW RISK** | Asyncio cooperative scheduling. Can overshoot position cap by 1-3 trades. No lock. |
| Type coercion | **SAFE** | Type errors crash trade (fail-safe). Inputs validated at watchlist level. |
| NaN propagation | **FIXED S124** | Added `_math_isfinite(size)` guard at L1572. Was theoretical risk only. |
| State corruption | **SAFE** | Exposure clamped to `max(0.0, ...)`. RTDS blocked until `_state_restored=True`. |

### NaN/Inf Guard Added (S124)
```python
# mirror_bot.py L1-2: import math as _math; _math_isfinite = _math.isfinite
# mirror_bot.py L1572:
if not _math_isfinite(size) or size < 0:
    size = 0.0
```
Defense-in-depth: If Kelly somehow returns NaN/inf (near-impossible given input validation in BankrollManager), the guard prevents it from bypassing `size <= 0` and `trade_usd < $10` checks (Python NaN comparisons return False for all `<`, `<=`, `>`, `>=`).

### Exception Swallowing Detail (the one LOW RISK path)
At mirror_bot.py L1390-1409, if `_reliability_tracker.likelihood_ratio()` throws:
- `reliability_mult` stays at 1.0 (neutral, set at L1391)
- An unreliable trader (LR < 1.0) whose lookup fails gets through with default sizing
- BUT: min_confidence gate at L1555 still applies (0.45 threshold)
- AND: the multi-factor confidence base (`_base`) falls back to 0.50 (L1427)
- Net effect: trade fires at 0.50 confidence with 1.0x reliability — small Kelly size, bounded by $300 per-bet cap
- Probability of this path: near-zero (DB connectivity issue during single trade)

---

## FILES MODIFIED THIS SESSION

| File | Changes |
|------|---------|
| `bots/mirror_ml_selector.py` | **NEW** — MirrorMLSelector class (XGBoost + Q-learning + combo) |
| `scripts/train_mirror_ml_selector.py` | **NEW** — Offline training pipeline |
| `scripts/ml_selector_shadow_analysis.py` | **NEW** — Three-ledger shadow comparison |
| `tests/unit/test_mirror_ml_selector.py` | **NEW** — 24 unit tests |
| `config/settings.py` | +5 ML selector config keys |
| `bots/mirror_bot.py` | +ML selector init, load, shadow scoring, +`import math`, +NaN guard at L1572 |

## TESTS
- **1595 passed, 0 failed** (full suite including 24 new ML selector tests)

## NOT YET COMMITTED OR DEPLOYED
- All S124 changes are in working tree only
- Training requires VPS DB access (`python scripts/train_mirror_ml_selector.py`)
- Models directory (`models/`) will be created on VPS during training

---

## UPCOMING MILESTONES

| Date | Milestone | Action |
|------|-----------|--------|
| ASAP | Train ML models | SSH to VPS, run training script, restart service |
| +48h | Shadow analysis | Run `ml_selector_shadow_analysis.py`, pick winner |
| +48h | Activate ML gate | Set `MIRROR_ML_STRATEGY` + `MIRROR_USE_ML_SELECTOR=true` |
| ~Apr 5 | Calibration re-enable | `MIRROR_USE_CALIBRATION=true` |
| 10K+ trades | R1b: Online Q-learning | Upgrade from offline bootstrap to online PER+ADWIN |
| TBD | CLOB credentials | Set up on VPS, canary stage 1 |
