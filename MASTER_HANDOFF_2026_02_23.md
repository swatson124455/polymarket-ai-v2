# MASTER HANDOFF — Polymarket AI V2
**Date**: 2026-02-23
**Previous handoff**: `MASTER_HANDOFF_2026_02_22.md` (superseded by this document)
**Status**: Paper trading live, 4 bots scanning, 0 trades (expected — thresholds conservative)

---

## 1. PROJECT IDENTITY & VISION

**What this is**: A production-grade autonomous paper-trading bot for Polymarket prediction markets.
8 specialized bots (currently 4 active) running continuously, each using different strategies,
all governed by a shared engine, kill switch, and risk manager. The goal is live USDC trading
once paper trading validates edge.

**The core structural edge** (Becker 2026, Reichenbach & Walther 2025): YES longshot buyers
systematically overpay — 1.85pp transfer to NO sellers on average, up to 7.32pp in World Events
and Entertainment. Our models should express this via NO-side bias on longshot prices in
high-emotion categories. The edge is cognitive, not informational — durable.

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
- The compute upgrade improves query latency and CPU-bound operations (ML training on DB data)
- Worth monitoring if 400ms/query drops — re-tune SCAN_MARKET_LIMIT upward if it does

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

config/settings.py (~610 lines)
  All tunable parameters via .env. Pydantic BaseSettings.
  Key settings documented in Section 7 below.

base_engine/base_engine.py
  Core engine. Owns: DB, prediction engine, order gateway, all background services.
  get_all_tradeable_markets(): in-memory 60s TTL cache + asyncio.Lock serialization.
  Background services: streaming persister, signal ingestion, whale tracker, feature precompute.
  Feature precompute loop: delayed 90s at startup (lets bots scan first).

base_engine/data/database.py (~3400 lines, 23 ORM tables)
  ALL database access goes through here.
  _SemaphoreSession: 30s timeout on acquire (added 2026-02-22).
  bulk_insert_prices_raw(), bulk_insert_trades(): use get_raw_session().
  get_all_tradeable_markets(): has in-memory cache + Lock.
  23 ORM tables include: Market, Position, Trade, PredictionLog, SignalRecord, SystemConfig,
    WalletCluster, FeatureSnapshot, PerformanceRecord, + more.

base_engine/prediction/prediction_engine.py (~1600 lines)
  10-model ML ensemble: RF, XGB, GB, ET, HGB, LGBM, CatBoost, LR, Ridge, KNN.
  _DriftTracker: ADWIN + distribution shift + calibration checks.
  _train_models(): background training, 180s timeout.
  model_cache.pkl: local file cache (avoids slow BYTEA fetch from Supabase).
  MUST contain: feature_columns, best_feature_names.
  CatBoost: skip CalibratedClassifierCV (no __sklearn_tags__).
  Elevation modules: VPIN, LLM clarity, disposition effect wired in here.

base_engine/execution/order_gateway.py
  Unified order path: kill switch → risk check → liquidity → coordinator → execute.
  In-memory position tracker: O(1) has_open_position() for reactive path.
  CVaR snapshot via get_all_open_positions_snapshot().
  rl_agent slot exists (unused, wired for future distributional RL).

base_engine/risk/dynamic_position_sizing.py
  Kelly criterion (fractional, default 0.25).
  calculate_kelly_size(win_probability, odds, bankroll).
  Standard Kelly: f = (p*b - q) / b with 0.25 fraction applied.

base_engine/risk/risk_manager.py
  Position limits, portfolio heat, CVaR limits.
  check_risk_limits() uses O(1) in-memory exposure (no DB query).

base_engine/coordination/kill_switch.py
  Line 38: uses get_raw_session() (fixed 2026-02-22 — was get_session(), caused hangs).
  Simple SELECT on SystemConfig WHERE key = 'kill_switch'.

base_engine/signals/signal_ingestion.py
  9 signal collection loops. _signal_db_sem = asyncio.Semaphore(2) limits concurrent writes.
  Sources: price, trade, volume, sentiment, whale, orderbook, social, news, on-chain.

base_engine/data/streaming_persister.py
  Bulk inserts: bulk_insert_prices_raw(), bulk_insert_trades() — use get_raw_session().
  Batch queue flushes every 10s.

bots/base_bot.py (~430 lines)
  Abstract base. Scan loop with jitter: (hash(bot_name) % 20) + 5 seconds before first scan.
  Kill switch check wrapped in asyncio.wait_for(..., timeout=10).
  scan_and_trade() wrapped in asyncio.wait_for(..., timeout=120).
  INFO logging: "Scan cycle starting", "Scan cycle done" with scan_ms.

bots/ensemble_bot.py (~529 lines)
  PRIMARY bot. 10-model ML predictions.
  Key pipeline: predict → FLB delta → category mult (L2) → sentiment → VPIN → clarity →
    disagreement penalty → alpha decay → signal enhancements → min_confidence gate → execute.
  _adapt_min_confidence(): Brier-score adaptive threshold every 5min.
  _refresh_category_mults(): PerformanceTracker-based category weights every 15min.
  _get_vpin_toxicity(): Tier 2 #18.
  _get_resolution_clarity(): Tier 2 #16.
  ENSEMBLE_TARGET_CATEGORIES: optional category filter via .env.

bots/arbitrage_bot.py
  Cross-market arbitrage. VPIN wired in.

bots/momentum_bot.py
  5 momentum modes including Mode 5 (Tier 2 #17: disposition effect).
  Slowest bot: ~50-55s warm scan.

bots/mirror_bot.py
  Copy-trading / wallet mirroring. Fast: ~6-22s.

base_engine/data/database_lock.py
  Advisory locks for ingestion scheduler (IDs 100001-100008).

data/paper_trading.log
  Live log file. TeeLogger writes directly. Use `tail -f` to monitor.

data/model_cache.pkl
  Local model cache. Delete to force retrain (~2-3 min).
  Must contain: models, scaler, model_weights, ensemble_blend, feature_columns, best_feature_names.

tests/unit/ (24 files, 321 tests)
  Must pass 0 failures after every change.
```

---

## 6. ALL BUGS FIXED — WHAT, WHY, HOW

### 2026-02-22: DB Semaphore Exhaustion (CRITICAL — was blocking all bots)

**Symptom**: All 4 bots hung forever. Log's last line: `Kill switch check bot_name=EnsembleBot`.
No scan ever started.

**Root cause**: `get_session()` called `await self.semaphore.acquire()` with NO timeout.
Background services (9 signal loops + streaming + whale + features + ingestion) consumed all
15 semaphore slots. Kill switch couldn't acquire a slot → permanent hang.
The 120s scan timeout didn't help — hang was BEFORE scan_and_trade() was called.

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
10. Semaphore limit: `max(total_connections, 3)` = 15 (was 12, missing 3 overflow slots)

### DB_POOL_SIZE=40 attempt (REVERTED — caused MaxClientsInSessionMode)
User confirmed: "you had me up this to 40?" — Supabase Pro session pooler hard-caps at 15.
DO NOT SET DB_POOL_SIZE > 15. Reverted to 12/3.

### EnsembleBot first scan timeout
Cold cache causes 120s+ first scan. Expected. Second scan ~12-15s (warm cache).
Not a bug — by design. MODEL CACHE (data/model_cache.pkl) avoids this on restart.

### Stale advisory lock blocking IngestionScheduler
Previous crash left advisory lock held. Pre-flight now terminates those backends.
Confirmed: "Pre-flight: terminated 1 sessions holding stale advisory locks".

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
TRAINING_RECENCY_LAMBDA=1.0                      # NEW (not yet implemented — see plan)

WALLET_ADDRESS=0x...                             # your Polymarket wallet
LEARNING_PERSISTENCE=false                       # set true to persist models to DB
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
random_forest: 0.12      extra_trees: 0.10
xgboost: 0.15            hist_gradient_boosting: 0.12
gradient_boosting: 0.10  lightgbm: 0.12
catboost: 0.12           logistic_regression: 0.07
ridge: 0.05              knn: 0.05
```
Weights are currently FIXED. Research integration plan (Section 11) replaces with Ridge stacking.

**Training**: Background, 180s timeout. Triggers on: cold start, drift detection, 24h staleness.
**Cache**: `data/model_cache.pkl` — delete to force retrain.
**Features**: ~50+ features from FeatureEngineer. Includes price, volume, time-to-resolution,
  sentiment, VPIN, wallet stats, on-chain indicators.
**Known issue**: Models predict ~0.92 (too high). Feedback loop via paper trade outcomes
  will correct over time.

**Drift detection** (in `_DriftTracker`):
- Distribution shift (z-score > 2.0 from training baseline)
- Confidence collapse (>60% predictions near 0.5)
- Calibration drift (recent accuracy < 45%)
- ADWIN on accuracy stream

---

## 10. TIER 2 FEATURES STATUS

```
#16 LLM resolution clarity:  DONE — wired into EnsembleBot via _get_resolution_clarity() multiplier
#17 Disposition effect:       DONE — MomentumBot Mode 5
#18 VPIN toxicity:            DONE — wired into EnsembleBot + ArbitrageBot

#12 (neg_risk arbitrage):     UNBLOCKED by migration 017 — pending implementation
#19 Wallet clustering:        Pending (ORM table exists)
#20 Order flow imbalance:     Pending (see research integration plan — OFI proxy via price velocity)
+1 other:                     TBD
```

---

## 11. RESEARCH INTEGRATION PLAN (next implementation task)

Source: Elite Polymarket V2 research report (72.1M Kalshi trades, 124M Polymarket trades analysis).

### Part A — 5 Direct Integrations (no new dependencies, exact file/lines)

**A1. Category-scaled FLB delta** — `bots/ensemble_bot.py:773`, `config/settings.py`

The existing `_flb_delta` (lines 764-774) is flat ±0.03 across all categories. Becker 2026 shows
the YES longshot bias varies 43× by category. Scale delta by category:

```python
# After existing FLB block (~line 773):
_cat = (market_data.get("category") or market_data.get("market_category") or "").lower()
_flb_delta *= getattr(settings, "CATEGORY_BIAS_SCALE", {}).get(_cat, 1.0)
```

Settings dict to add:
```python
CATEGORY_BIAS_SCALE: dict = {
    "world events": 4.0, "entertainment": 2.6, "politics": 1.5,
    "sports": 1.2, "crypto": 1.0, "science": 0.7, "finance": 0.09,
}
```

**A2. PHT fast drift layer** — `base_engine/prediction/prediction_engine.py:_DriftTracker`

Page-Hinkley Test: fastest abrupt-drift detector (DR=1.0 at δ=0.001). Add before ADWIN:
```python
def _pht_test(self, delta: float = 0.005, lambda_: float = 50.0) -> bool:
    outcomes = list(self._recent_outcomes)
    if len(outcomes) < 20:
        return False
    cumsum = min_cumsum = 0.0
    for o in outcomes[-100:]:
        cumsum += (1.0 - o) - delta
        min_cumsum = min(min_cumsum, cumsum)
        if cumsum - min_cumsum > lambda_:
            return True
    return False
```

**A3. Stacked Ridge meta-learner** — `base_engine/prediction/prediction_engine.py:_train_models()`

Replace fixed model weights with OOF-trained Ridge. At end of `_train_models()`:
```python
from sklearn.linear_model import RidgeCV
from sklearn.model_selection import cross_val_predict
if len(X_train) >= 50 and len(self.models) >= 3:
    oof = np.column_stack([
        cross_val_predict(m, X_train, y_train, cv=3, method="predict_proba")[:, 1]
        for m in self.models.values()
    ])
    meta = RidgeCV(alphas=[0.01, 0.1, 1.0, 10.0])
    meta.fit(oof, y_train)
    raw = meta.coef_; pos = np.maximum(raw, 0.001)
    self.model_weights = dict(zip(self.models.keys(), (pos / pos.sum()).tolist()))
    logger.info("Stacking weights: %s", {k: round(v,3) for k,v in self.model_weights.items()})
```
Delete data/model_cache.pkl after implementing to force refit.

**A4. Market lifecycle YES penalty** — `bots/ensemble_bot.py:~774`

After FLB block, before min_confidence gate:
```python
_created = market_data.get("created_at") or market_data.get("createdAt")
if _created and side == "YES" and price < 0.55:
    try:
        _created_dt = datetime.fromisoformat(str(_created).replace("Z", "+00:00"))
        if _created_dt.tzinfo is None:
            _created_dt = _created_dt.replace(tzinfo=timezone.utc)
        _age_hours = (datetime.now(timezone.utc) - _created_dt).total_seconds() / 3600.0
        if _age_hours < 48:
            consensus_confidence -= 0.03 * (1.0 - _age_hours / 48.0)
    except (ValueError, TypeError):
        pass
```

**A5. Meister-adapted Kelly** — `base_engine/risk/dynamic_position_sizing.py:69`

After `kelly_fraction` assignment in `calculate_kelly_size()`:
```python
_boundary_scale = min(1.0, 4.0 * odds * (1.0 - odds))
kelly_fraction = kelly_fraction * _boundary_scale
```
Full Kelly at p=0.5, 36% at p=0.1/0.9. Prevents oversizing near boundaries.

### Part B — Alternatives for Blocked Techniques

**B1. SUPER → High-surprise outcome sharing**
Can't do RL replay. Alternative: When prediction error > 0.3 on resolved market, add to
`_high_surprise` deque(maxlen=200) in `_DriftTracker`. Bots check for similar feature
profiles before trading. File: `prediction_engine.py:record_outcome()`.

**B2. DoubleAdapt → Sample reweighting with recency bias**
Can't do MAML. Alternative: sklearn's `sample_weight`. Exponential decay:
`w_i = exp(-TRAINING_RECENCY_LAMBDA * (T - t_i) / T)`. All 10 models support it.
Setting: `TRAINING_RECENCY_LAMBDA=1.0`. File: `prediction_engine.py:_train_models()`.

**B3. PULSE counterparty toxicity → Large-trade concentration score**
No wallet-per-fill data. Alternative: `large_trade_pct` = fraction of trades in last 60min
with size > 2× median. Ng et al. (SSRN 2025) confirm large trades are best Polymarket
proxy for informed flow. Add to `_get_vpin_toxicity()` return dict. If >0.4 and VPIN<0.5,
apply 0.85× confidence mult. File: `ensemble_bot.py:_get_vpin_toxicity()`.

**B4. Distributional RL → Continuous IQR-based position scaling**
No distributional neural nets. Alternative: Replace binary `_disagreement_mult` (threshold-based)
with continuous IQR scaling. `kelly_size *= (1.0 - (p75 - p25) / 0.5)`. Wide model spread =
smaller position. File: `ensemble_bot.py:_analyze_one_token():~756`.

**B5. Federated learning → Cross-bot feature performance sharing**
All bots in-process. Alternative: `_shared_feature_stats` dict in `BaseEngine`. Each bot
writes `{feature_name: recent_lift}` after scan. EnsembleBot boosts features other bots found
informative. File: `base_engine.py` + `ensemble_bot.py`.

**B6. HDDM_W/KSWIN/ADDM → Mann-Whitney U (scipy)**
frouros not installed, has documented bugs. Alternative: `scipy.stats.mannwhitneyu()` on
rolling prediction windows. `p_val < 0.05` → covariate drift flag. scipy already installed
(transitive sklearn dep). File: `prediction_engine.py:_DriftTracker.check_drift()`.

**B7. DtACI conformal sizing → Rolling bin calibration**
MAPIE needs 100+ resolved trades (have < 20). Alternative: Confidence-bin calibration from
`prediction_log`. Group into 0.05-wide bins, track empirical accuracy, size ×= (actual/predicted).
Graceful when sparse (returns 1.0 mult). File: `dynamic_position_sizing.py` + `database.py`.

**B8. OFI order book → Price velocity + volume acceleration proxy**
No per-level bid/ask depth. Alternative: `price_velocity = (p_t - p_{t-10})/10` and
`volume_acceleration = vol_recent/vol_baseline - 1`. Pointing same direction as trade: +0.02.
Opposing: -0.02. Uses streaming data already captured. New: `_get_price_momentum_signal()`.
File: `ensemble_bot.py`.

**B9. Redpanda → asyncio.Queue ring buffer per-source**
Full Kafka too complex. Alternative: Bounded `asyncio.Queue(maxsize=10000)` per ingestion source.
`put_nowait()` with LOW-priority drop on full. Consumer batches. Extends existing
streaming_persister pattern. File: `signal_ingestion.py`.

**B10. pmxt Kalshi → Kalshi read-only price signal**
No Kalshi trading yet. Alternative: Read-only fetch of Kalshi public prices for equivalent
markets. If Kalshi differs from Polymarket by >3pp, use as signal boost. No auth needed for
prices. New file: `base_engine/signals/kalshi_signal.py`.

**B11. pm-AMM / limit orders → Shadow maker P&L tracking**
System is paper taker. Alternative: In `_execute_ensemble_trade()`, log what price we WOULD
have quoted as maker (model_mid ± spread). When market crosses that price, hypothetical fill.
Builds data to justify maker switch at go-live. File: `ensemble_bot.py`.

**B12. BOCPD → 2-sample KS test (scipy)**
ruptures not installed. Alternative: `scipy.stats.ks_2samp()` on feature distributions.
KS stat > 0.3 = regime shift candidate. Background check every 30min. File: `base_engine.py`.

---

## 12. MIGRATIONS STATUS

```
Migration 017 (neg_risk, outcome_count): APPLIED 2026-02-22
  - Trick: session-mode pooler + SET statement_timeout='0' works
  - ORM columns neg_risk, outcome_count now enabled in code
  - Unblocks Tier 2 #12

Migration 018 (feature_snapshot):
  - ORM done (FeatureSnapshot table defined)
  - SQL NOT YET APPLIED to Supabase
  - Apply via session-mode URL (direct or pooler:5432)
  - Never transaction-mode (port 6543) for DDL

Future migrations: use session-mode URL only. pgroll for zero-downtime production.
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

1. **Implement Research Integration Plan A1-A5** (Section 11) — no new deps, high value
2. **Implement B2 (recency-weighted training)** — single sklearn change, high ROI
3. **Implement B4 (continuous IQR position scaling)** — replaces binary disagreement mult
4. **Implement B3 (large-trade toxicity proxy)** — extends existing VPIN path
5. **Tier 2 #12** — neg_risk arbitrage (unblocked by migration 017)
6. **Apply migration 018** (feature_snapshot SQL)
7. **Tier 2 #19, #20** — wallet clustering, order flow
8. **VPS migration** — single biggest performance unlock (1ms vs 400ms queries)
   After: raise SCAN_MARKET_LIMIT=50+, ENSEMBLE_SCAN_CONCURRENCY=3+
9. **Kalshi read-only signal** (B10) — cross-venue price discovery
10. **DtACI conformal sizing** (B7) — needs 100+ resolved trades first
11. **Live trading** — after paper trading validates edge

---

## 16. HOW TO RESUME A SESSION

1. Read this file first (you are here)
2. Check `data/paper_trading.log` for current state: `tail -100 data/paper_trading.log`
3. Run tests to confirm baseline: `python -m pytest tests/unit/ -v --no-cov --tb=short`
4. Check MEMORY.md at `C:\Users\samwa\.claude\projects\C--lockes-picks-polymarket-ai-v2\memory\MEMORY.md`
5. VPN ON (Surfshark US) before any run
6. For pending work: implement in order from Section 15, tests after every change

---

## 17. SUPABASE UPGRADE NOTES (2026-02-23)

Upgraded from NANO → MICRO (1GB RAM, 2-core ARM, dedicated CPU).

**What this changes**:
- Dedicated CPU: ML training on DB data will be faster
- Better PG query performance on complex joins
- pg_stat_activity queries more reliable (pre-flight checks)

**What this does NOT change**:
- Session pooler max 15 connections (Pro plan limit, not compute-dependent)
- DATABASE_URL stays the same
- DB_POOL_SIZE stays at 12 (DO NOT increase above 15)

**What to investigate after running a day**:
- Check if query latency dropped below 400ms → if yes, raise SCAN_MARKET_LIMIT to 20-30
- Monitor `data/paper_trading.log` for any `DB semaphore timeout` entries (should be zero)
- Check if EnsembleBot cold start is faster (dedicated ARM CPU helps background training)
