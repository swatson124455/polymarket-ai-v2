# MASTER HANDOFF — Polymarket AI V2
**Date**: 2026-02-24
**Previous handoff**: `MASTER_HANDOFF_2026_02_23.md` (superseded by this document)
**Status**: Paper trading live, 4 bots scanning, 0 trades (expected — thresholds conservative)
**Tests**: ✅ 321/321 passing, 0 warnings, 0 failures

---

## 1. PROJECT IDENTITY & VISION

**What this is**: A production-grade autonomous paper-trading bot for Polymarket prediction markets.
8 specialized bots (currently 4 active) running continuously, each using different strategies,
all governed by a shared engine, kill switch, and risk manager. The goal is live USDC trading
once paper trading validates edge.

**The core structural edge** (Becker 2026, Reichenbach & Walther 2025): YES longshot buyers
systematically overpay — 1.85pp transfer to NO sellers on average, up to 7.32pp in World Events
and Entertainment. Our models express this via NO-side bias on longshot prices in high-emotion
categories. The edge is cognitive, not informational — durable.

**Architecture philosophy**: Single-process asyncio monolith. All bots share one DB pool,
one prediction engine, one order gateway, one kill switch. Simple > distributed. Migration to
VPS + direct PG (< 1ms queries vs current 400ms Supabase) is the next major unlock.

---

## 2. ENVIRONMENT

```
OS:          Windows 10 Home 10.0.19045
Shell:       bash (Unix syntax always — /dev/null not NUL, forward slashes)
Python:      3.13.3 system-installed (NO venv — system Python)
Working dir: C:\lockes-picks\polymarket-ai-v2
Run:         python run_paper.py  (background paper trading)
             python main.py       (foreground with stdout)
Log:         data/paper_trading.log  (TeeLogger writes directly — bypasses stdout buffering)
VPN:         Surfshark ON before running — Polymarket API 403 on US IPs
```

**Running tests** (must pass 321/321, 0 warnings):
```
powershell.exe -Command "cd 'C:\lockes-picks\polymarket-ai-v2'; python -m pytest tests/unit/ -v --no-cov --tb=short"
```

---

## 3. INFRASTRUCTURE — SUPABASE

### Compute (as of 2026-02-23)
Upgraded from NANO to **MICRO**: 1GB RAM, 2-core ARM CPU, $0.01344/hour.
- This gives dedicated CPU (was shared on NANO)
- PG `max_connections` on Micro = 97 (same as NANO — Supabase caps this per plan tier)
- Session pooler pool_size is still 15 server-side on Pro plan
- **Do NOT raise DB_POOL_SIZE above 15 — Supabase session pooler enforces this hard**

### Connection Architecture (CRITICAL)
```
DATABASE_URL → pooler.supabase.com:5432 (session mode)
               Never port 6543 (transaction mode — breaks DDL)
               Never direct db.*.supabase.co for app code (use for migrations only)
```

### `get_session()` vs `get_raw_session()`
```
get_session()     → acquires asyncio.Semaphore(15), then SQLAlchemy session
                    Use for: bot scans, feature queries, normal reads/writes
                    Has 30s timeout — raises DatabaseError instead of hanging

get_raw_session() → bypasses semaphore entirely
                    Use for: kill switch, advisory locks, bulk inserts (streaming persister)
                    Never blocks on semaphore exhaustion
```

### Current .env Pool Config
```
DB_POOL_SIZE=12
DB_MAX_OVERFLOW=3
# Total semaphore limit = 15 (DB_POOL_SIZE + DB_MAX_OVERFLOW = pool_size of pooler)
# DO NOT exceed 15 total — MaxClientsInSessionMode error
```

---

## 4. CRITICAL CONCEPTS

```
YES and NO are both BUY — buying that outcome token. SELL = close position only.
P&L always = (current - entry) / entry regardless of YES/NO side.
Signals use YES/NO (which token), gateway normalizes to BUY/SELL for routing.

Market IDs: numeric m.id (e.g. 628113) vs condition_id (0x339d...). ALWAYS JOIN both.

Logging: structlog.configure() MUST run BEFORE application imports.
         cache_logger_on_first_use=True locks factory at first use — order matters.

Scan cycles log at INFO: "Scan cycle starting" / "Scan cycle done" always visible.
```

---

## 5. KEY FILES — COMPLETE MAP

```
main.py (~400 lines)
  Entry point. Pre-flight check (advisory lock cleanup, stale session termination).
  Registers all 4 bots. Starts BaseEngine, OrderGateway, kill switch.
  _preflight_check(): terminates backends holding advisory locks 100001-100008.

run_paper.py
  Thin wrapper around main.py for background operation.

config/settings.py (~620 lines)
  All tunable parameters via .env. Pydantic BaseSettings.
  NEW 2026-02-24: CATEGORY_BIAS_SCALE, TRAINING_RECENCY_LAMBDA, NEGRISK_MAX_TOTAL_RISK
    CONFORMAL_WIDE_INTERVAL_THRESHOLD, CONFORMAL_NARROW_INTERVAL_THRESHOLD,
    CONFORMAL_WIDE_SIZE_MULTIPLIER (DtACI conformal sizing infrastructure).

base_engine/base_engine.py
  Core engine. Owns: DB, prediction engine, order gateway, all background services.
  NEW: _shared_feature_stats dict (B5 cross-bot feature sharing).
  NEW: publish_feature_lift() / get_cross_bot_feature_boost() — rolling 2h window.
  NEW: get_observability_slis() / _observability_sli_loop() — 60s SLI monitoring.
  NEW: _ks_regime_detection_loop() — B12 KS test every 30min on prediction distributions.
  get_all_tradeable_markets(): in-memory 60s TTL cache + asyncio.Lock serialization.

base_engine/data/database.py (~3400 lines, 23 ORM tables)
  ALL database access goes through here.
  _SemaphoreSession: 30s timeout on acquire.
  bulk_insert_prices_raw(), bulk_insert_trades(): use get_raw_session().

base_engine/prediction/prediction_engine.py (~1600+ lines)
  10-model ML ensemble: RF, XGB, GB, ET, HGB, LGBM, CatBoost, LR, Ridge, KNN.
  NEW _DriftTracker features (2026-02-24):
    - PHT (Page-Hinkley Test): fastest abrupt-drift detector, runs first in check_drift()
    - Mann-Whitney U drift: covariate shift on prediction distributions (scipy)
    - _high_surprise deque (maxlen=200): records prediction errors > 0.3
    - is_high_surprise_market(market_id, window=50): checks if market has triggered high errors
  NEW training:
    - OOF Ridge stacking (A3): cross_val_predict + RidgeCV replaces fixed model weights
    - Recency-weighted training (B2): exponential decay sample_weight = exp(-λ*(T-i)/T)

base_engine/execution/order_gateway.py
  Unified order path: kill switch → risk check → liquidity → coordinator → execute.
  In-memory position tracker: O(1) has_open_position() for reactive path.
  rl_agent slot: wired and ready — activate via RL_TRADE_TIMING_ENABLED=true in .env.

base_engine/risk/dynamic_position_sizing.py
  Kelly criterion (fractional, default 0.25).
  NEW A5 Meister boundary Kelly: scale = min(1.0, 4*p*(1-p)) — full Kelly at 0.5, ~36% at extremes.
  NEW conformal_multiplier slot in calculate_optimal_size() — activates automatically when
    prediction_log accumulates 100+ resolved trades (DtACI conformal sizing, B7).

base_engine/risk/risk_manager.py
  Position limits, portfolio heat, CVaR limits.

base_engine/coordination/kill_switch.py
  Uses get_raw_session() (bypasses semaphore — no deadlock risk).

base_engine/signals/signal_ingestion.py
  9 signal collection loops. _signal_db_sem = asyncio.Semaphore(2) limits concurrent writes.
  NEW B9 asyncio.Queue ring buffers: maxsize=10000 per source, put_nowait() drops on full.
    enqueue_signal() / get_queue_stats() — backpressure without blocking.

base_engine/signals/kalshi_signal.py  [NEW 2026-02-24]
  B10: Read-only Kalshi price signal. Fetches public prices (no auth required).
  60s TTL cache. 5s timeout. Returns ±0.02 if cross-venue gap > 3pp.

base_engine/learning/wallet_clustering.py  [FULLY IMPLEMENTED 2026-02-24]
  Tier 2 #19: 3-heuristic wallet clustering.
  Heuristic 1: Co-trading frequency (same market, 5-min window, ≥3 co-trades → edge)
  Heuristic 2: Pearson r on log-trade-sizes (r > 0.65 → edge, requires ≥5 common markets)
  Heuristic 3: Top-quartile co-activity + similar median log-size (ratio > 0.6 → edge)
  _pearson_r() helper. Iterative DFS (avoids Python recursion limit). DB query with 30s timeout.

base_engine/execution/rl_trade_timing.py  [COMPLETE — already fully wired]
  Tabular Q-learning, 324 states × 3 actions.
  Prioritized Experience Replay (PER). ADWIN drift detection for policy reset.
  Activate via RL_TRADE_TIMING_ENABLED=true in .env — NO code changes needed.

bots/ensemble_bot.py (~550+ lines)
  PRIMARY bot. 10-model ML predictions.
  Full confidence pipeline (post 2026-02-24):
    predict → FLB delta (A1: category-scaled) → A4 lifecycle YES penalty →
    partition dependence penalty → B1 high-surprise penalty (0.90×) →
    sentiment → VPIN (B3: large-trade toxicity 0.85×) → clarity → B8 OFI proxy (±0.02/0.03) →
    B4 continuous IQR disagreement mult → alpha decay → min_confidence gate → execute.
  NEW shadow maker logging (B11): logs hypothetical maker quotes ± spread per trade.
  NEW B5 cross-bot feature sharing: publish_feature_lift() called after each scan.

bots/arbitrage_bot.py
  Cross-market arbitrage. VPIN wired in.
  NEW Tier 2 #12 NegRisk: LEG-A (buy all YES when SUM<1) + LEG-B (buy all NO when SUM(YES)>N-1).
  Proportional Kelly sizing per outcome. Respects NEGRISK_MAX_TOTAL_RISK setting.

bots/momentum_bot.py
  5 momentum modes including Mode 5 (Tier 2 #17: disposition effect).

bots/mirror_bot.py
  Copy-trading / wallet mirroring. Uses WalletClustering for cluster-based signals.

data/paper_trading.log
  Live log file. TeeLogger writes directly. Use `tail -f` to monitor.

data/model_cache.pkl
  Local model cache. Delete to force retrain (~2-3 min).
  Must contain: models, scaler, model_weights, ensemble_blend, feature_columns, best_feature_names.
  NOTE: After Ridge stacking (A3), model_weights are now dynamically computed OOF.
        Delete cache to force refit with new stacking weights on next startup.

tests/unit/ (24 files, 321 tests)
  Must pass 0 failures after every change.
```

---

## 6. ALL BUGS FIXED — WHAT, WHY, HOW

### 2026-02-22: DB Semaphore Exhaustion (CRITICAL — was blocking all bots)

**Symptom**: All 4 bots hung forever. Log's last line: `Kill switch check bot_name=EnsembleBot`.

**Root cause**: `get_session()` called `await self.semaphore.acquire()` with NO timeout.
Background services consumed all 15 semaphore slots. Kill switch couldn't acquire → permanent hang.

**Fixes applied** (all in place):
1. `kill_switch.py:38` — `get_session()` → `get_raw_session()` (bypasses semaphore entirely)
2. `database.py:_SemaphoreSession.__aenter__` — 30s `asyncio.wait_for` on semaphore.acquire()
3. `base_bot.py:kill_switch_check` — 10s `asyncio.wait_for` wrapper (defense in depth)
4. `main.py:_preflight_check()` — terminates backends holding advisory locks 100001-100008
5. `signal_ingestion.py` — `_signal_db_sem = asyncio.Semaphore(2)` limiting concurrent writes
6. `streaming_persister.py` — bulk inserts use `get_raw_session()`
7. `base_engine.py:get_all_tradeable_markets()` — 60s in-memory TTL cache + asyncio.Lock
8. Feature precompute delay: 90s (was 30s)
9. `INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS=180` (.env)
10. Semaphore limit: `max(total_connections, 3)` = 15

### DB_POOL_SIZE=40 attempt (REVERTED)
DO NOT SET DB_POOL_SIZE > 15. Supabase Pro session pooler hard-caps at 15.

---

## 7. CURRENT .ENV SETTINGS (key parameters)

```
DATABASE_URL=postgresql+asyncpg://postgres.[ref]:[pass]@aws-0-us-east-1.pooler.supabase.com:5432/postgres
DB_POOL_SIZE=12
DB_MAX_OVERFLOW=3

SCAN_MARKET_LIMIT=10                             # increase to 50+ after VPS migration
ENSEMBLE_SCAN_CONCURRENCY=2                      # increase to 3+ after VPS migration
BOT_SCAN_TIMEOUT_SECONDS=120
DAILY_INGESTION_MARKETS_COUNT=200
DAILY_INGESTION_PRICES_MARKETS=200
INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS=180

ENSEMBLE_MIN_CONFIDENCE=0.65                     # adaptive; lower to 0.45 in learning mode
ENSEMBLE_TARGET_CATEGORIES=                      # empty = all categories
ALPHA_DECAY_LAMBDA=0.5
TRAINING_RECENCY_LAMBDA=1.0                      # B2 recency weighting — active

NEGRISK_MAX_TOTAL_RISK=300.0                     # Tier 2 #12 max $ risk across all outcomes
RL_TRADE_TIMING_ENABLED=false                    # set true to activate RL timing agent

WALLET_ADDRESS=0x...                             # your Polymarket wallet
LEARNING_PERSISTENCE=false                       # set true to persist models to DB
PAPER_TRADING=true                               # set false when live trading validated
```

---

## 8. SCAN PERFORMANCE (warm cache, 10 markets, Supabase latency ~400ms/query)

```
EnsembleBot:  ~12-15s (cold start: 120s+ first scan, ~50s second, ~12s third+)
ArbitrageBot: ~10-15s
MomentumBot:  ~50-55s
MirrorBot:    ~6-22s
```

After VPS migration (direct PG, ~1ms/query): expect 10× speedup across all bots.
At that point: raise SCAN_MARKET_LIMIT to 50+, ENSEMBLE_SCAN_CONCURRENCY to 3+.

---

## 9. ML MODEL DETAILS

**Ensemble (10 models)**:
```
random_forest, extra_trees, xgboost, hist_gradient_boosting,
gradient_boosting, lightgbm, catboost, logistic_regression, ridge, knn
```

**Weights**: Previously fixed (0.05–0.15). Now replaced by **OOF Ridge stacking** (A3, 2026-02-24):
- At end of `_train_models()`, `cross_val_predict(cv=3)` on all models → `RidgeCV` meta-learner
- Non-negative weights, normalized to sum to 1
- Requires ≥50 training samples and ≥3 models; falls back to rank-weights if not
- **Action**: Delete `data/model_cache.pkl` to force retrain with stacking on next startup

**Training**: Background, 180s timeout. Triggers on: cold start, drift detection, 24h staleness.
**Recency weighting** (B2): `w_i = exp(-TRAINING_RECENCY_LAMBDA * (T-i)/T)` applied to sample_weight.
**Cache**: `data/model_cache.pkl` — delete to force retrain.
**Features**: ~50+ features from FeatureEngineer.

**Drift detection** (in `_DriftTracker`) — now 5 layers:
1. PHT (Page-Hinkley Test) — fastest, runs first (DR=1.0, FPR=0.0)
2. Distribution shift (z-score > 2.0 from training baseline)
3. Confidence collapse (>60% predictions near 0.5)
4. Calibration drift (recent accuracy < 45%)
5. ADWIN on accuracy stream
6. Mann-Whitney U (B6) — covariate shift on prediction distributions (p < 0.05)

---

## 10. TIER 2 FEATURES STATUS

```
#16 LLM resolution clarity:   DONE — wired into EnsembleBot via _get_resolution_clarity() multiplier
#17 Disposition effect:        DONE — MomentumBot Mode 5
#18 VPIN toxicity:             DONE — wired into EnsembleBot + ArbitrageBot
#19 Wallet clustering:         DONE 2026-02-24 — 3-heuristic _build_similarity_graph() fully implemented
#12 NegRisk arbitrage:         DONE 2026-02-24 — LEG-A + LEG-B + proportional Kelly sizing
#20 Order flow imbalance:      DONE via B8 (price velocity + volume acceleration proxy in EnsembleBot)

All 6 implemented Tier 2 features active. None pending.
```

---

## 11. RESEARCH INTEGRATION PLAN — COMPLETE STATUS

Source: `elite_polymarket_v2.docx` — 72.1M Kalshi trades, 124M Polymarket trades analysis.
**ALL ITEMS FULLY IMPLEMENTED AS OF 2026-02-24.**

### Part A — Direct Integrations (all complete)

| Item | Description | File | Status |
|------|-------------|------|--------|
| A1 | Category-scaled FLB delta | `ensemble_bot.py`, `settings.py` | ✅ DONE |
| A2 | PHT fast drift layer | `prediction_engine.py:_DriftTracker` | ✅ DONE |
| A3 | OOF Ridge meta-learner | `prediction_engine.py:_train_models()` | ✅ DONE |
| A4 | Market lifecycle YES penalty | `ensemble_bot.py:_analyze_one_token()` | ✅ DONE |
| A5 | Meister boundary Kelly | `dynamic_position_sizing.py` | ✅ DONE |

### Part B — Alternatives for Blocked Techniques (all complete)

| Item | Description | File | Status |
|------|-------------|------|--------|
| B1 | High-surprise outcome relay | `prediction_engine.py`, `ensemble_bot.py` | ✅ DONE |
| B2 | Recency-weighted training | `prediction_engine.py:_train_models()` | ✅ DONE |
| B3 | Large-trade toxicity proxy | `ensemble_bot.py:_get_vpin_toxicity()` | ✅ DONE |
| B4 | Continuous IQR position scaling | `ensemble_bot.py` | ✅ DONE |
| B5 | Cross-bot feature sharing | `base_engine.py`, `ensemble_bot.py` | ✅ DONE |
| B6 | Mann-Whitney U drift | `prediction_engine.py:_DriftTracker` | ✅ DONE |
| B7 | DtACI conformal sizing | `dynamic_position_sizing.py` | ✅ INFRASTRUCTURE DONE (auto-activates with 100+ resolved trades) |
| B8 | Price velocity / OFI proxy | `ensemble_bot.py:_get_price_momentum_signal()` | ✅ DONE |
| B9 | asyncio.Queue ring buffers | `signal_ingestion.py` | ✅ DONE |
| B10 | Kalshi read-only signal | `base_engine/signals/kalshi_signal.py` | ✅ DONE |
| B11 | Shadow maker P&L tracking | `ensemble_bot.py` | ✅ DONE |
| B12 | KS test regime detection | `base_engine.py` | ✅ DONE |

### Structural Alpha & Infrastructure (all complete)
| Item | Description | Status |
|------|-------------|--------|
| Partition dependence filter | YES near-50% on young markets → penalty | ✅ DONE |
| Observability SLIs | Data freshness, queue depth, semaphore depletion alerts | ✅ DONE |
| RL Trade Timing Agent | Tabular Q-learning, wired in `order_gateway.py` | ✅ DONE (activate via env flag) |

---

## 12. MIGRATIONS STATUS

```
Migration 017 (neg_risk, outcome_count): APPLIED 2026-02-22
  - ORM columns neg_risk, outcome_count enabled in code
  - Unblocked Tier 2 #12

Migration 018 (feature_snapshot): APPLIED 2026-02-24
  - ALTER TABLE prediction_log ADD COLUMN IF NOT EXISTS feature_snapshot JSONB
  - GIN partial index: idx_prediction_log_feature_snapshot (WHERE feature_snapshot IS NOT NULL)
  - Applied via asyncpg direct connection (no psql required)
  - Verify: SELECT column_name FROM information_schema.columns
            WHERE table_name='prediction_log' AND column_name='feature_snapshot';

Future migrations: use session-mode URL only (port 5432). pgroll for zero-downtime production.
Critical PG rules: NEVER ALTER TABLE ADD COLUMN DEFAULT x NOT NULL on large tables.
Always: add NULL first → backfill in batches → ADD CONSTRAINT NOT VALID → VALIDATE.
Always: CREATE INDEX CONCURRENTLY.
```

---

## 13. KNOWN ISSUES (non-blocking)

```
Redis not connected:    All Redis caches miss. In-memory fallbacks active. Low priority.
Polygon RPC 401:        Mempool monitoring disabled. Non-critical (informational only).
EnsembleBot cold start: First scan always times out. Second scan ~12s. Expected.
0 trades so far:        Conservative thresholds + cold feedback loop. Expected early behavior.
Models predict ~0.92:   Overconfident. Paper trade outcomes will calibrate over time.
                        Ridge stacking (A3) will correct as more data accumulates.
```

---

## 14. MOCK PATTERNS FOR TESTS

```python
# db.get_session() is SYNC returning async context manager:
db.get_session = MagicMock(return_value=MockSessionCtx())

# db._verify_database() is ASYNC:
db._verify_database = AsyncMock()

# MockSessionCtx pattern:
class MockSessionCtx:
    async def __aenter__(self): return mock_session
    async def __aexit__(self, *a): pass
```

---

## 15. PENDING WORK — PRIORITY ORDER

All code-level research integrations and Tier 2 features are complete. Remaining work is
operational and data-dependent:

### 🔴 Operational (do now)

1. **Delete `data/model_cache.pkl`** and restart — forces retrain with new Ridge stacking weights (A3)
   and recency weighting (B2). Old cache has fixed weights from before 2026-02-24 changes.

### 🟡 Data-dependent (activates automatically)

2. **B7 DtACI conformal sizing** — infrastructure in place (`conformal_multiplier` slot in
   `calculate_optimal_size()`). Activates naturally once `prediction_log` accumulates 100+
   resolved paper trades. No code needed.

3. **B1 high-surprise relay** — `_high_surprise` deque and `is_high_surprise_market()` are live.
   Starts generating penalties once resolved trades flow into `record_outcome()`.

4. **Ridge stacking model weights (A3)** — requires ≥50 training samples. Returns to rank-weights
   if not enough data. Will switch automatically once training data is sufficient.

### 🟡 Feature activation (env flag only)

5. **RL Trade Timing Agent** — fully implemented and wired. Set `RL_TRADE_TIMING_ENABLED=true`
   in `.env` to activate. Agent uses tabular Q-learning to adjust entry timing.
   Recommend activating after baseline paper trading shows stable scan behavior.

### 🟢 Infrastructure (ops work)

6. **VPS migration** — single biggest performance unlock (400ms → 1ms queries).
   Target: Amsterdam (AMS) or Ireland (DUB) VPS (CLOB servers in AWS eu-west-2 London).
   No code changes required — just `DATABASE_URL` + pool settings in `.env`:
   ```
   DB_POOL_SIZE=40   # or higher — no Supabase cap
   SCAN_MARKET_LIMIT=50
   ENSEMBLE_SCAN_CONCURRENCY=5
   ```

7. **Redis connection** — set `REDIS_URL` in `.env` when VPS is provisioned.
   Currently all Redis caches miss with in-memory fallbacks (functional but no cross-process sharing).

8. **Live trading switch** — after 2–4 weeks paper trading validates positive EV + acceptable drawdown:
   - `PAPER_TRADING=false`
   - Fund real USDC wallet at `WALLET_ADDRESS`
   - Consider lowering `ENSEMBLE_MIN_CONFIDENCE` slightly (0.60) to increase trade frequency

---

## 16. KEY IMPLEMENTATION DETAILS (2026-02-24 additions)

### A1 Category-scaled FLB (settings.py + ensemble_bot.py)
```python
CATEGORY_BIAS_SCALE: dict = {
    "world events": 4.0, "media": 3.9, "entertainment": 2.6,
    "politics": 1.5, "sports": 1.2, "crypto": 1.0,
    "science": 0.7, "finance": 0.09,
}
# Applied in ensemble_bot.py after FLB block:
_flb_delta *= getattr(settings, "CATEGORY_BIAS_SCALE", {}).get(_cat_for_flb, 1.0)
```

### A5 Meister boundary Kelly (dynamic_position_sizing.py)
```python
# After fractional_kelly = kelly_f * kelly_fraction:
_boundary_scale = min(1.0, 4.0 * odds * (1.0 - odds))
fractional_kelly = fractional_kelly * _boundary_scale
# Result: full Kelly at p=0.5, ~36% at p=0.1 or 0.9
```

### B2 Recency-weighted training (prediction_engine.py)
```python
# In _prepare_training_data(), before final clip:
n = len(sample_weights)
_lambda = getattr(settings, "TRAINING_RECENCY_LAMBDA", 1.0)
_recency_decay = np.exp(-_lambda * np.arange(n - 1, -1, -1) / max(n - 1, 1))
sample_weights = sample_weights * _recency_decay
```

### Tier 2 #12 NegRisk LEG-B (arbitrage_bot.py)
```python
# When sum(YES prices) > N-1:  buying all NO outcomes guarantees $1 payout
# profit = total_yes - (n - 1)
complement_sum = sum((1.0 - p) for p in yes_prices)
for market, price in zip(neg_risk_markets, yes_prices):
    complement_weight = (1.0 - price) / complement_sum
    position_size = min(total_budget * complement_weight, max_risk_per_outcome)
```

### Wallet Clustering heuristics (wallet_clustering.py)
```python
_CO_TRADE_WINDOW_SECONDS = 300   # 5-minute co-trade window
_SIZE_CORRELATION_THRESHOLD = 0.65  # Pearson r threshold
_MIN_CO_TRADES = 3               # edges formed at ≥3 co-trades
# Heuristic 3: top-quartile activity + ratio > 0.6 (sizes within ~40% of each other)
```

---

## 17. HOW TO RESUME A SESSION

1. Read this file first (you are here)
2. Check `data/paper_trading.log`: `tail -100 data/paper_trading.log`
3. Run tests: `powershell.exe -Command "cd 'C:\lockes-picks\polymarket-ai-v2'; python -m pytest tests/unit/ -v --no-cov --tb=short"`
4. Check MEMORY.md at `C:\Users\samwa\.claude\projects\C--lockes-picks-polymarket-ai-v2\memory\MEMORY.md`
5. VPN ON (Surfshark) before any run
6. **If starting fresh**: delete `data/model_cache.pkl` to force retrain with Ridge stacking weights

**Current state**: All research integrations (A1–A5, B1–B12), all Tier 2 features (#12, #16, #17,
#18, #19, #20), and migration 018 are fully implemented and passing 321 tests. The system is
feature-complete for the `elite_polymarket_v2.docx` specification. Next human action is the
VPS migration for the performance unlock.

---

## 18. SUPABASE UPGRADE NOTES (2026-02-23)

Upgraded from NANO → MICRO (1GB RAM, 2-core ARM, dedicated CPU).

**What this changes**: Dedicated CPU (ML training faster), better PG query performance.
**What this does NOT change**: Session pooler max 15 connections (Pro plan limit).
**DB_POOL_SIZE stays at 12** (DO NOT increase above 15).
