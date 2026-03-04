# MASTER HANDOFF — Polymarket AI V2 — COMPLETE SESSION RECORD
**Date**: 2026-02-24
**Supersedes**: All previous handoff documents
**Tests**: ✅ 321/321 passing, 0 warnings, 0 failures
**Status**: Paper trading ready. Feature-complete per elite_polymarket_v2.docx. 3 runtime bugs fixed. Bot not currently running (needs restart with VPN).

---

## 1. PROJECT IDENTITY & VISION

**What this is**: A production-grade autonomous paper-trading bot for Polymarket prediction markets. 8 specialized bots (4 currently active) running continuously under a shared engine, kill switch, and risk manager. Goal: live USDC trading once paper trading validates edge over 2–4 weeks.

**The core structural edge** (Becker 2026, Reichenbach & Walther 2025):
- YES longshot buyers overpay by **1.85pp on average** — up to **7.32pp in World Events, 6.2pp in Entertainment**
- The bias is **cognitive, not informational** — it's durable and won't arbitrage away
- Strategy: express NO-side bias on longshot prices in high-emotion categories
- FLB (Favourite-Longshot Bias) is now **category-scaled** (A1) — World Events at 4× base, Finance at 0.09×

**Architecture philosophy**: Single-process asyncio monolith. All 4 bots share one DB pool, one prediction engine (10-model ensemble), one order gateway, one kill switch. Simple > distributed. Next unlock: VPS migration (400ms → 1ms queries).

**Paper trading baseline**: System started ~2026-02-22. No real paper trades yet from live bot scanning real markets (0 trades — expected, thresholds conservative and system was crashing from DB exhaustion which is now fixed). The 12 "trades" in the log are from test runs on mock market `m1`.

---

## 2. ENVIRONMENT (IMMUTABLE — NEVER CHANGE)

```
OS:          Windows 10 Home 10.0.19045
Shell:       PowerShell (Windows). Unix bash syntax in scripts.
Python:      3.13.3 system-installed — NO venv, NO conda
Working dir: C:\lockes-picks\polymarket-ai-v2
Run:         python run_paper.py      ← background paper trading
             python main.py           ← foreground with stdout
Log:         data/paper_trading.log   ← TeeLogger writes directly (bypasses buffering)
VPN:         Surfshark ON before ANY run — Polymarket API returns 403 on US IPs
```

**Running tests** (must pass 321/321, 0 warnings after EVERY change):
```powershell
powershell.exe -Command "cd 'C:\lockes-picks\polymarket-ai-v2'; python -m pytest tests/unit/ -v --no-cov --tb=short"
```

**To restart the bot**:
```powershell
# 1. Turn on Surfshark VPN (US → EU server)
# 2. Delete stale model cache (if implementing new features):
#    del C:\lockes-picks\polymarket-ai-v2\data\model_cache.pkl
# 3. Start bot:
python run_paper.py
# 4. Monitor:
Get-Content data/paper_trading.log -Tail 50 -Wait
```

---

## 3. INFRASTRUCTURE — SUPABASE (CRITICAL LIMITS)

### Compute
- Plan: **Pro + MICRO** (1GB RAM, 2-core ARM, $0.01344/hr, dedicated CPU)
- `max_connections` = 97 (PG level, but pooler enforces 15)
- Session pooler **hard cap = 15 connections** — DO NOT exceed

### Connection Architecture
```
DATABASE_URL → pooler.supabase.com:5432   ← SESSION MODE — required for DDL + long transactions
               NEVER port 6543            ← transaction mode breaks DDL and session state
               NEVER direct db.*.supabase.co for app code (migrations only)
```

### Pool Settings (DO NOT CHANGE)
```env
DB_POOL_SIZE=12
DB_MAX_OVERFLOW=3
# Total = 15 = Supabase Pro session pooler hard cap
# DO NOT set DB_POOL_SIZE > 15 → MaxClientsInSessionMode crash
```

### `get_session()` vs `get_raw_session()`
```python
get_session()     # Acquires asyncio.Semaphore(15), then SQLAlchemy session
                  # 30s timeout → raises DatabaseError instead of hanging forever
                  # Use for: bot scans, feature queries, normal reads/writes

get_raw_session() # Bypasses semaphore entirely — never blocks
                  # Use for: kill switch, advisory locks, bulk inserts
```

### DB Semaphore Budget (after fixes, 2026-02-24)
```
EnsembleBot scans:      2 concurrent (ENSEMBLE_SCAN_CONCURRENCY=2) × 1 session each = 2
Feature precompute:     1 serial (was 3 concurrent — FIXED 2026-02-24)            = 1
Signal ingestion:       _signal_db_sem = Semaphore(2) limits concurrent writes     = 2
MomentumBot scan:       sequential                                                  = 1
MirrorBot scan:         sequential                                                  = 1
ArbitrageBot corr:      5 sequential (was 20 — FIXED 2026-02-24)                  = 1
Whale tracker:          occasional                                                  = 1
Ingestion scheduler:    advisory lock                                               = 1
────────────────────────────────────────────────────────────────────────────────────
Total peak estimate:    ~10/15 slots — 5 slots headroom (was 15+/15 → exhaustion)
```

---

## 4. CRITICAL CONCEPTS (MUST KNOW)

```
YES and NO are BOTH BUY — you buy the YES token OR the NO token.
SELL = closing an existing position only. Never sell to open.

P&L = (current_price - entry_price) / entry_price — identical formula for YES and NO sides.

Signals use YES/NO (which token to buy). Order gateway normalizes to BUY/SELL for routing.

Market IDs: numeric m.id (e.g. 628113) vs condition_id (0x339d...). ALWAYS JOIN both columns.
            Never assume one format — always resolve via id_resolver.

Logging: structlog.configure() MUST run BEFORE any application imports.
         cache_logger_on_first_use=True locks the factory on first call — order matters.
         See main.py:_configure_logging() for the exact initialization sequence.

Scan cycles: always log at INFO level — "Scan cycle starting" / "Scan cycle done" visible in log.
             If you don't see these, the scan loop is hung (check kill switch / DB semaphore).

CatBoost: NEVER wrap with CalibratedClassifierCV — it doesn't implement __sklearn_tags__.
          All 10 models support sample_weight natively. Apply recency weights directly.

trades.timestamp: TIMESTAMP WITHOUT TIME ZONE (naive UTC).
                  ALWAYS pass naive datetimes: datetime.now(timezone.utc).replace(tzinfo=None)
                  NEVER pass timezone-aware datetimes to any query comparing t.timestamp.
```

---

## 5. COMPLETE FILE MAP

### Entry Points
```
main.py (~400 lines)
  - Entry point. Configures logging FIRST, then imports.
  - _preflight_check(): kills stale backends holding advisory locks 100001-100008
  - Registers all 4 bots. Starts BaseEngine, OrderGateway, kill switch.
  - Pre-flight clears stale locks that prevent IngestionScheduler from acquiring advisory locks.

run_paper.py
  - Thin wrapper: calls main.py for background operation.
```

### Configuration
```
config/settings.py (~620 lines)
  Pydantic BaseSettings — all params come from .env or defaults.

  Key settings (complete list):
    DB_POOL_SIZE=12, DB_MAX_OVERFLOW=3
    SCAN_MARKET_LIMIT=10              # increase to 50+ after VPS migration
    ENSEMBLE_SCAN_CONCURRENCY=2       # increase to 5+ after VPS migration
    BOT_SCAN_TIMEOUT_SECONDS=120
    ENSEMBLE_MIN_CONFIDENCE=0.65      # adaptive via Brier-score feedback
    ALPHA_DECAY_LAMBDA=0.5
    TRAINING_RECENCY_LAMBDA=1.0       # B2: exp decay weight for training samples
    PAPER_TRADING=true                # set false for live trading
    WALLET_ADDRESS=0x...              # your Polymarket wallet
    LEARNING_PERSISTENCE=false        # set true to persist models to Supabase
    DAILY_INGESTION_MARKETS_COUNT=200
    DAILY_INGESTION_PRICES_MARKETS=200
    INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS=180

    # NEW 2026-02-24 additions:
    CATEGORY_BIAS_SCALE: dict         # A1 — per-category FLB scale factors (in code default)
    NEGRISK_MAX_TOTAL_RISK=300.0      # Tier 2 #12 — max $ risk across all NegRisk outcomes
    RL_TRADE_TIMING_ENABLED=false     # set true to activate RL timing agent (fully implemented)
    CONFORMAL_WIDE_INTERVAL_THRESHOLD=0.30   # B7 DtACI — wide PI threshold
    CONFORMAL_NARROW_INTERVAL_THRESHOLD=0.10 # B7 DtACI — narrow PI threshold
    CONFORMAL_WIDE_SIZE_MULTIPLIER=0.50      # B7 DtACI — position scaling at wide PI
    ARB_CORRELATION_MARKET_LIMIT=5    # was 20, reduced 2026-02-24 to prevent pool exhaustion
```

### Core Engine
```
base_engine/base_engine.py
  - Owns: DB, prediction engine, order gateway, all background services
  - get_all_tradeable_markets(): in-memory 60s TTL cache + asyncio.Lock serialization
  - update_market_index(): O(1) lookup by condition_id or numeric id
  - Background services started in start():
      1. streaming_persister
      2. signal_ingestion
      3. whale_tracker
      4. feature_precompute_loop (90s delayed — lets bots scan first)
      5. ks_regime_detection_loop [B12 — NEW 2026-02-24] (5min delayed, 30min interval)
      6. observability_sli_loop [NEW 2026-02-24] (2min delayed, 60s interval)
      7. ingestion_scheduler
      8. resolution_listener
  - _shared_feature_stats dict: B5 cross-bot feature sharing (rolling 2h window)
  - publish_feature_lift() / get_cross_bot_feature_boost(): B5 in/out
  - get_observability_slis(): SLI snapshot (data freshness, queue depth, semaphore free)
  - _ks_regime_detection_loop(): scipy.stats.ks_2samp on rolling prediction dist halves
```

### Database
```
base_engine/data/database.py (~3400 lines, 23 ORM tables)
  ALL database access through here. No direct asyncpg calls in app code.

  Key ORM tables:
    Market          — markets with neg_risk (BOOL), outcome_count (INT) from migration 017
    Trade           — user_address, market_id, size, timestamp (naive UTC), side
    User            — address, win_rate, is_elite, is_likely_market_maker
    Position        — open positions, entry_price, size
    PredictionLog   — predictions with feature_snapshot JSONB (migration 018)
    WalletCluster   — wallet clustering results (Tier 2 #19)
    SystemConfig    — key/value for kill switch and config
    PerformanceRecord — per-bot, per-category performance tracking
    SignalRecord    — ingested signals
    + 14 more tables

  _SemaphoreSession: asyncio.Semaphore(15) with 30s timeout (raises DatabaseError)
  NaiveUTCDateTime: custom type that stores/retrieves naive UTC datetimes

  Key methods:
    get_session()       — use for all normal queries (enforces semaphore)
    get_raw_session()   — bypass semaphore (kill switch, bulk inserts only)
    get_all_tradeable_markets() — in-memory 60s cache + Lock
    bulk_insert_prices_raw() / bulk_insert_trades() — use get_raw_session()
```

### Prediction Engine (ML Core)
```
base_engine/prediction/prediction_engine.py (~1600+ lines)

  10-model ensemble:
    random_forest, extra_trees, xgboost, hist_gradient_boosting,
    gradient_boosting, lightgbm, catboost, logistic_regression, ridge, knn

  Model weights: NOW DYNAMIC via OOF Ridge stacking (A3, 2026-02-24)
    - cross_val_predict(cv=3) → RidgeCV meta-learner replaces fixed weights
    - Requires ≥50 training samples AND ≥3 models (falls back to rank-weights if not)
    - Delete data/model_cache.pkl to force retrain with new stacking

  Training: background, 180s timeout, triggers on cold start / drift / 24h staleness
  Recency weighting (B2): w_i = exp(-TRAINING_RECENCY_LAMBDA * (T-i)/T) applied to sample_weight

  _DriftTracker (6-layer drift detection):
    Layer 1: PHT (Page-Hinkley Test) — DR=1.0, FPR=0.0, runs first [A2, NEW 2026-02-24]
    Layer 2: Distribution shift (z-score > 2.0 from training baseline)
    Layer 3: Confidence collapse (>60% predictions near 0.5)
    Layer 4: Calibration drift (recent accuracy < 45%)
    Layer 5: ADWIN on accuracy stream
    Layer 6: Mann-Whitney U test (B6) — covariate shift, p < 0.05 → drift flag
    _high_surprise deque(maxlen=200): records prediction errors > 0.3 [B1, NEW 2026-02-24]
    is_high_surprise_market(market_id, window=50): returns True if ≥2 surprises in window

  CRITICAL BUG FIXED 2026-02-24 (Issue #2):
    Elite query at ~line 2609: _as_of MUST be naive:
      CORRECT:   _as_of = datetime.now(timezone.utc).replace(tzinfo=None)
      WRONG:     _as_of = datetime.now(timezone.utc)   ← triggers UndefinedFunctionError
    trades.timestamp is TIMESTAMP WITHOUT TIME ZONE; timezone-aware param causes
    "operator does not exist: timestamp without time zone >= interval" — 166× per run!

  Feature vector cache: batch_precompute_all_features() background job
    FIXED 2026-02-24: Semaphore(3) → Semaphore(1) — was causing pool exhaustion
```

### Risk Management
```
base_engine/risk/dynamic_position_sizing.py
  - Kelly criterion (fractional, default 0.25× full Kelly)
  - A5 Meister boundary Kelly: scale = min(1.0, 4*p*(1-p))
    Full Kelly at p=0.5, ~36% at p=0.1/0.9 — prevents extreme-probability oversizing
  - conformal_multiplier slot in calculate_optimal_size():
    B7 DtACI: auto-activates when 100+ resolved paper trades accumulate
    CONFORMAL_WIDE_INTERVAL_THRESHOLD=0.30 → 0.50× size at wide prediction intervals
  - adjust_for_volatility(), adjust_for_confidence() — standard Kelly adjustments

base_engine/risk/risk_manager.py
  - CVaR limits, portfolio heat, position size limits
  - check_risk_limits() uses O(1) in-memory exposure (no DB query in hot path)
```

### Order Execution
```
base_engine/execution/order_gateway.py
  - Full order path: kill switch → risk check → liquidity → coordinator → execute
  - In-memory position tracker for O(1) has_open_position()
  - rl_agent slot: wired and ready (see rl_trade_timing.py)
  - Paper trading: logs all orders, tracks P&L, never touches real CLOB

base_engine/execution/rl_trade_timing.py  ← COMPLETE AND WIRED
  - Tabular Q-learning, 324 states × 3 actions (immediate/wait-5s/wait-30s)
  - Prioritized Experience Replay (PER buffer, maxlen=50000)
  - ADWIN drift detection: resets policy if distribution shifts
  - 5 state dimensions: time_bin(6) × price_vol_bin(3) × spread_bin(3) × hour_bin(6) × dow(5)/3 = ~324 buckets
  - Activation: set RL_TRADE_TIMING_ENABLED=true in .env — NO CODE CHANGES NEEDED
```

### Kill Switch
```
base_engine/coordination/kill_switch.py
  Line 38: uses get_raw_session() — bypasses semaphore (CRITICAL — was get_session() before 2026-02-22 fix)
  Uses SystemConfig table WHERE key = 'kill_switch'
  Also: multi_kill_switch.py for per-bot and per-category halt logic
```

### Signal Ingestion
```
base_engine/signals/signal_ingestion.py
  - 9 signal sources: price, trade, volume, sentiment, whale, orderbook, social, news, on-chain
  - _signal_db_sem = asyncio.Semaphore(2) — limits concurrent DB writes
  - B9 asyncio.Queue ring buffers [NEW 2026-02-24]:
    self._signal_queues: Dict[str, asyncio.Queue(maxsize=10000)] per source
    put_nowait() drops on full (non-blocking backpressure)
    enqueue_signal() / get_queue_stats() methods

base_engine/signals/kalshi_signal.py  [NEW 2026-02-24]
  - B10: Read-only Kalshi prices (no auth needed for public endpoint)
  - KALSHI_API_BASE = "https://trading-api.kalshi.com/trade-api/v2"
  - 60s TTL in-memory cache, 5s HTTP timeout
  - get_kalshi_yes_price(ticker) → Optional[float]
  - get_kalshi_signal(polymarket_price, kalshi_ticker, side) → ±0.02 if >3pp gap
```

### Streaming Persister
```
base_engine/data/streaming_persister.py
  - Batch queue flushes every 10s
  - bulk_insert_prices_raw(), bulk_insert_trades() — use get_raw_session() (bypasses semaphore)
  - Tracks _last_flush_ts for SLI freshness monitoring
```

### Learning / Analytics
```
base_engine/learning/wallet_clustering.py  [FULLY REWRITTEN 2026-02-24 — Tier 2 #19]
  3-heuristic similarity graph for wallet clustering:

  Heuristic 1 (Co-trading frequency):
    market_timeline per market_id → sliding window (300s) → co_trade_count pairs
    Edge added when pair co-trades ≥ 3 times (_MIN_CO_TRADES=3)

  Heuristic 2 (Log trade-size Pearson correlation):
    Only checks pairs that already have co-trading edges (prune search space)
    wallet_market_sizes: {addr: {market_id: mean_log_size}}
    Pearson r > 0.65 on pairs sharing ≥ 5 common markets → edge

  Heuristic 3 (Top-quartile co-activity):
    Top 25% by trade count → check median log-size similarity
    ratio = sz_a/sz_b (or sz_b/sz_a) > 0.6 (sizes within ~40%) → edge
    Only compares within consecutive 50 in sorted list (O(n×50) not O(n²))

  Graph traversal: iterative DFS (avoids Python recursion limit)
  DB query: SELECT user_address, market_id, size, created_at FROM trades
            WHERE user_address IS NOT NULL ORDER BY created_at DESC LIMIT 40000
            + SET LOCAL statement_timeout = '30s'

  Constants: _CO_TRADE_WINDOW_SECONDS=300, _SIZE_CORRELATION_THRESHOLD=0.65,
             _MIN_CO_TRADES=3, _MAX_WALLETS_TO_ANALYZE=2000, _WIN_RATE_PROXIMITY=0.05

  Methods:
    identify_clusters(min_cluster_size=2) → List[WalletCluster]
    get_cluster_for_wallet(wallet) → Optional[str]
    get_cluster_wallets(cluster_id) → Set[str]
    get_cluster_rank(wallet) → float  # 0–1, smarter money = higher

base_engine/learning/performance_tracker.py
  - Per-bot, per-category P&L tracking
  - Used by EnsembleBot._refresh_category_mults() every 15min
  - Feeds dynamic category weight scaling
```

### Bots
```
bots/base_bot.py (~430 lines)
  Abstract base. All 4 active bots inherit this.
  Scan loop: jitter = (hash(bot_name) % 20) + 5 seconds before first scan
  Kill switch check: asyncio.wait_for(..., timeout=10) — won't hang even if pool exhausted
  scan_and_trade(): wrapped in asyncio.wait_for(..., timeout=120)
  Logs "Scan cycle starting" and "Scan cycle done" at INFO level with scan_ms

bots/ensemble_bot.py (~550+ lines)  ← PRIMARY BOT
  Full confidence pipeline (complete post-2026-02-24):

  1. predict() → raw ML confidence from 10-model ensemble
  2. FLB delta (A1): category-scaled per CATEGORY_BIAS_SCALE
     World Events 4.0×, Media 3.9×, Entertainment 2.6×, Politics 1.5×,
     Sports 1.2×, Crypto 1.0×, Science 0.7×, Finance 0.09×
  3. A4 lifecycle YES penalty: markets <48h old, YES price <0.55 → up to -0.03×(1-age/48)
  4. Partition dependence penalty: YES near 50% on markets <24h old, vol <$1K → up to -0.04
  5. B1 high-surprise penalty: is_high_surprise_market(market_id) → 0.90× multiplier
  6. Sentiment signal: from ingested sentiment data
  7. B3 large-trade toxicity: large_trade_pct >0.4 AND VPIN <0.5 → 0.85× multiplier
     large_trade_pct = fraction of trades in last 60min with size > 2× median
  8. LLM clarity (Tier 2 #16): resolution clarity score → multiplier
  9. B8 OFI proxy: price_velocity + volume_acceleration → ±0.02/0.03 adjustment
     _get_price_momentum_signal() method
  10. B4 continuous IQR disagreement: mult = max(0.3, 1.0 - IQR/0.5) using model quartiles
  11. Alpha decay: ALPHA_DECAY_LAMBDA on time-to-resolution
  12. Min confidence gate: ENSEMBLE_MIN_CONFIDENCE (adaptive via Brier score, updated every 5min)

  Additional features:
  - B5 cross-bot sharing: publish_feature_lift() called after each scan
  - B11 shadow maker logging: logs hypothetical maker quotes ±1.5% spread before each trade
  - Kalshi signal (B10): wired to add ±0.02 if cross-venue gap >3pp (where kalshi_ticker known)
  - _adapt_min_confidence(): Brier-score adaptive threshold every 5min
  - _refresh_category_mults(): PerformanceTracker-based category weights every 15min
  - ENSEMBLE_TARGET_CATEGORIES: optional filter via .env (empty = all categories)

bots/arbitrage_bot.py
  Standard arbitrage (YES+NO sum ≠ 1.0) plus:

  Tier 2 #12 NegRisk arbitrage [NEW 2026-02-24] in _scan_negrisk_arbitrage():
    LEG A (buy all YES): SUM(yes_prices) < 1.0 - min_profit_threshold
      Proportional Kelly: complement_weight = (1-p) / sum(1-p for all outcomes)
      Cheaper outcomes get more allocation (more upside per dollar)
    LEG B (buy all NO): SUM(yes_prices) > (N-1) + min_profit_threshold
      Equivalent to SUM(no_prices) < 1; profit = total_yes - (N-1)
      Same proportional sizing on NO side
    Uses market.neg_risk ORM column (migration 017) as priority signal
    Max total risk: NEGRISK_MAX_TOTAL_RISK=$300

  FIXED 2026-02-24 (Issue #3): Sub-scan timeouts added:
    _scan_cross_market_arbitrage → asyncio.wait_for(..., timeout=30s)
    _scan_bond_opportunities     → asyncio.wait_for(..., timeout=20s)
    _scan_negrisk_arbitrage      → asyncio.wait_for(..., timeout=20s)
    Total sub-scan budget: 70s max < 120s hard limit

  ARB_CORRELATION_MARKET_LIMIT: default 20 → 5 [FIXED 2026-02-24]
    Each find_correlated_markets() uses 1 DB session; 20 at 30s semaphore timeout = 600s worst case

bots/momentum_bot.py
  5 momentum modes:
    Mode 1: Price momentum (directional)
    Mode 2: Volume surge
    Mode 3: Sentiment momentum
    Mode 4: Whale activity correlation
    Mode 5: Disposition effect [Tier 2 #17] — winner/loser divergence signal
  Slowest bot: ~50-55s warm scan (complex per-market queries)

bots/mirror_bot.py
  Copy-trading / wallet mirroring.
  Uses WalletClustering (Tier 2 #19) for cluster-based signal amplification.
  Fast: ~6-22s scan.
```

---

## 6. COMPLETE BUG FIX HISTORY

### 2026-02-22: DB Semaphore Exhaustion (CRITICAL — was blocking all bots)
**Symptom**: All 4 bots hung forever. Log: `Kill switch check bot_name=EnsembleBot` (last line, no progress).
**Root cause**: `get_session()` had no timeout on semaphore acquire. Background services consumed all 15 slots. Kill switch couldn't acquire a slot → permanent hang.

**All 10 fixes applied**:
1. `kill_switch.py:38` — `get_session()` → `get_raw_session()` (bypasses semaphore)
2. `database.py` — 30s `asyncio.wait_for` on `semaphore.acquire()` in `_SemaphoreSession`
3. `base_bot.py` — kill switch check wrapped in `asyncio.wait_for(..., timeout=10)`
4. `main.py:_preflight_check()` — terminates backends holding advisory locks 100001-100008
5. `signal_ingestion.py` — `_signal_db_sem = asyncio.Semaphore(2)` limits concurrent writes
6. `streaming_persister.py` — bulk inserts use `get_raw_session()`
7. `base_engine.py` — `get_all_tradeable_markets()` in-memory 60s TTL cache + asyncio.Lock
8. Feature precompute delay: 90s (was 30s)
9. `.env`: `INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS=180`
10. Semaphore = `max(total_connections, 3)` = 15 (was 12, missing overflow slots)

**DO NOT**: Set `DB_POOL_SIZE > 15`. Reverted from 40 → Supabase Pro session pooler hard-caps at 15.

---

### 2026-02-24: DB Semaphore Exhaustion Recurrence (Issue #1)
**Symptom**: `DB semaphore timeout — all slots occupied for 30s` in log, all bots getting errors simultaneously.
**Root cause**: `batch_precompute_all_features()` had `Semaphore(3)` — 3 concurrent `_extract_features` calls (each using 1 DB session) competed with EnsembleBot(2) + signal ingestion(2) + others → 9+ sessions at once from just these two sources.
**Fix**: `prediction_engine.py:407`: `Semaphore(3)` → `Semaphore(1)` — serializes precompute, frees 2 critical slots.

---

### 2026-02-24: SQL Timestamp Type Error (Issue #2) — 166 errors per run
**Symptom**: `UndefinedFunctionError: operator does not exist: timestamp without time zone >= interval` in every elite query, elite signals always returned 0.
**Root cause**: `_as_of = datetime.now(timezone.utc)` is timezone-aware. PG computed `:as_of - INTERVAL '1 hour'` → `timestamptz`, then can't compare with `TIMESTAMP WITHOUT TIME ZONE` column.
**Fix**: `prediction_engine.py:~2609`:
```python
_as_of = datetime.now(timezone.utc).replace(tzinfo=None)  # CORRECT: matches column type
```

---

### 2026-02-24: ArbitrageBot 120s Scan Timeout (Issue #3)
**Symptom**: `scan_and_trade() timed out after 120s — DB or external service may be hung [bot_name=ArbitrageBot]`
**Root cause**: Pool exhaustion → each correlation DB call waits 30s → 20 calls × 30s = 600s worst case. No sub-scan timeouts existed.
**Fix**:
1. `ARB_CORRELATION_MARKET_LIMIT` default: 20 → 5 in `arbitrage_bot.py:538`
2. Added `asyncio.wait_for(self._scan_cross_market_arbitrage(...), timeout=30.0)`
3. Added `asyncio.wait_for(self._scan_bond_opportunities(...), timeout=20.0)`
4. Added `asyncio.wait_for(self._scan_negrisk_arbitrage(...), timeout=20.0)`
Combined sub-scan budget: max 70s, with 50s headroom inside 120s hard limit.

---

## 7. COMPLETE IMPLEMENTATION LOG (elite_polymarket_v2.docx)

All items from the research document implemented 2026-02-23/24.

### Part A — Direct Research Integrations

**A1: Category-scaled FLB delta** (`config/settings.py` + `bots/ensemble_bot.py`)
```python
# settings.py
CATEGORY_BIAS_SCALE: dict = {
    "world events": 4.0, "media": 3.9, "entertainment": 2.6,
    "politics": 1.5, "sports": 1.2, "crypto": 1.0,
    "science": 0.7, "finance": 0.09,
}
# ensemble_bot.py — after FLB block:
_cat_for_flb = (market_data.get("category") or market_data.get("market_category") or "").lower()
_flb_delta *= getattr(settings, "CATEGORY_BIAS_SCALE", {}).get(_cat_for_flb, 1.0)
```
Source: Becker 2026 — YES longshot bias varies 43× by category; World Events/Entertainment most extreme.

**A2: Page-Hinkley Test fast drift layer** (`prediction_engine.py:_DriftTracker`)
```python
def _pht_test(self, delta: float = 0.005, lambda_: float = 50.0) -> bool:
    outcomes = list(self._recent_outcomes)
    if len(outcomes) < 20:
        return False
    cumsum = 0.0; min_cumsum = 0.0
    for o in outcomes[-100:]:
        cumsum += (1.0 - o) - delta
        min_cumsum = min(min_cumsum, cumsum)
        if cumsum - min_cumsum > lambda_:
            return True
    return False
```
Runs first in `check_drift()` before ADWIN. Detection Rate=1.0, FPR=0.0 in benchmarks.

**A3: OOF Ridge meta-learner** (`prediction_engine.py:_train_models()`)
```python
# At end of _train_models(), after rank-weight assignment:
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
```
Replaces fixed model weights (was 0.05–0.15 fixed) with data-driven OOF stacking.
⚠️ **ACTION**: Delete `data/model_cache.pkl` on next restart to force retrain with stacking.

**A4: Market lifecycle YES penalty** (`ensemble_bot.py`)
```python
# After FLB block, before min_confidence gate:
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
Source: Sonnemann PNAS 2013 — newly-listed markets anchor to 50/50 ignorance prior → YES overpriced.

**A5: Meister boundary Kelly** (`dynamic_position_sizing.py`)
```python
# After fractional_kelly = kelly_f * kelly_fraction:
_boundary_scale = min(1.0, 4.0 * odds * (1.0 - odds))
fractional_kelly = fractional_kelly * _boundary_scale
# Result: full Kelly at p=0.5, ~36% at p=0.1/0.9 (parabola 4p(1-p))
```
Source: arXiv:2412.14144 — prevents oversizing at probability extremes where Kelly amplifies estimation error.

### Part B — Alternative Implementations

**B1: High-surprise outcome relay** (`prediction_engine.py:_DriftTracker` + `ensemble_bot.py`)
```python
# In _DriftTracker.__init__:
self._high_surprise: deque = deque(maxlen=200)

# In record_outcome(predicted, actual, market_id=None):
if abs(predicted - actual) > 0.3 and market_id:
    self._high_surprise.append((market_id, abs(predicted - actual), time.time()))

# New method:
def is_high_surprise_market(self, market_id: str, window: int = 50) -> bool:
    recent = list(self._high_surprise)[-window:]
    return sum(1 for mid, _, _ in recent if mid == market_id) >= 2

# In ensemble_bot._analyze_one_token():
if self._pe._drift_tracker.is_high_surprise_market(market_id):
    consensus_confidence *= 0.90  # 10% penalty on markets with prior big errors
```

**B2: Recency-weighted training** (`prediction_engine.py:_prepare_training_data()`)
```python
_lambda = getattr(settings, "TRAINING_RECENCY_LAMBDA", 1.0)
n = len(sample_weights)
_recency_decay = np.exp(-_lambda * np.arange(n - 1, -1, -1) / max(n - 1, 1))
sample_weights = sample_weights * _recency_decay
```
All 10 sklearn models support `sample_weight`. Setting `TRAINING_RECENCY_LAMBDA=1.0` in .env.

**B3: Large-trade toxicity proxy** (`ensemble_bot.py:_get_vpin_toxicity()`)
Returns `large_trade_pct` = fraction of trades in last 60min with size > 2× median.
If `large_trade_pct > 0.4` AND `VPIN < 0.5`: apply 0.85× confidence multiplier.
Source: Ng et al. SSRN 2025 — large trades are best Polymarket proxy for informed flow.

**B4: Continuous IQR position scaling** (`ensemble_bot.py`)
Replaced binary `_disagreement_mult` (threshold-based) with:
```python
preds_arr = np.array(list(all_predictions.values()))
_iqr = float(np.percentile(preds_arr, 75) - np.percentile(preds_arr, 25))
_disagreement_mult = max(0.3, 1.0 - (_iqr / 0.5))
```
Wide model spread → smaller Kelly position. Continuous vs. hard threshold.

**B5: Cross-bot feature sharing** (`base_engine.py` + `ensemble_bot.py`)
`_shared_feature_stats` dict in BaseEngine: `{feature_name: [(timestamp, lift, bot_name), ...]}`
Rolling 2h window. `publish_feature_lift()` called after each scan. `get_cross_bot_feature_boost()` returns up to 1.10× multiplier for informative features.

**B6: Mann-Whitney U drift** (`prediction_engine.py:_DriftTracker.check_drift()`)
```python
from scipy import stats as _scipy_stats
half = len(preds) // 2
_, mw_pval = _scipy_stats.mannwhitneyu(preds[:half], preds[half:], alternative="two-sided")
if mw_pval < 0.05:
    # covariate drift flag
```
Added as Layer 6 of drift detection. scipy already installed (sklearn transitive dependency).

**B7: DtACI conformal sizing** (`dynamic_position_sizing.py`)
Infrastructure complete. `conformal_multiplier` slot in `calculate_optimal_size()`:
- `prediction_interval_width >= 0.30` → 0.50× size
- Linear interpolation 0.10–0.30 → 0.50×–1.0× size
- Auto-activates once `prediction_log` has 100+ resolved trades. NO code changes needed.
Currently: returns `conformal_multiplier = 1.0` (sparse bins).

**B8: Price velocity / OFI proxy** (`ensemble_bot.py:_get_price_momentum_signal()`)
```python
price_velocity = (p_t - p_t_minus_10) / 10      # 10-price change rate
vol_acceleration = vol_recent / vol_baseline - 1  # volume surge
# Returns ±0.02 (aligned) or ±0.03 (strong signal) or 0.0
```

**B9: asyncio.Queue ring buffers** (`signal_ingestion.py`)
`self._signal_queues: Dict[str, asyncio.Queue(maxsize=10000)]` per source.
`put_nowait()` drops silently on full (non-blocking backpressure). Logs every 100 drops.
`get_queue_stats()` → `{source: {depth, utilization, drops}}` used by SLI monitoring.

**B10: Kalshi read-only signal** (`base_engine/signals/kalshi_signal.py`)
New file. Fetches `https://trading-api.kalshi.com/trade-api/v2/markets/{ticker}`. No auth needed for prices.
Returns ±0.02 if `abs(kalshi_price - polymarket_price) > 0.03`.
60s TTL cache, 5s HTTP timeout, graceful degradation returns 0.0.

**B11: Shadow maker P&L tracking** (`ensemble_bot.py`)
Before each `place_order()`, logs:
```python
_shadow_maker_orders.append({
    "market_id": market_id, "side": side, "price": price,
    "model_mid": confidence, "bid_quote": confidence - 0.015, "ask_quote": confidence + 0.015,
    "timestamp": time.time()
})
```
Rolling deque(500). Used to evaluate hypothetical maker P&L when market crosses quoted price.
Builds evidence for maker strategy switch at go-live.

**B12: KS test regime detection** (`base_engine.py:_ks_regime_detection_loop()`)
```python
from scipy import stats as _scipy_stats
half = len(preds) // 2
_ks_stat, _ks_pval = _scipy_stats.ks_2samp(preds[:half], preds[half:])
if _ks_stat > 0.3:
    logger.warning("B12 KS regime shift detected...")
```
Background task every 30min. `ks_stat > 0.3` = regime shift candidate → warning logged.

### Structural Alpha Additions

**Partition Dependence Filter** (`ensemble_bot.py`)
Source: Sonnemann PNAS 2013 — binary YES/NO framing anchors to 50/50 ignorance prior.
```python
# _partition_dependence_penalty():
if abs(price - 0.5) < 0.10 and market_age_hours < 24 and volume < 1000:
    return -0.04 * (1 - abs(price - 0.5) / 0.10)
```
Applied after A4 lifecycle penalty.

**Observability SLIs** (`base_engine.py:_observability_sli_loop()`)
60s periodic check: data freshness >60s alert, signal queue >80% alert, DB semaphore ≤2 free alert.
Uses in-memory metrics only (no DB calls in SLI loop).

---

## 8. MIGRATIONS STATUS

```
Migration 017 (neg_risk, outcome_count): ✅ APPLIED 2026-02-22
  - neg_risk BOOLEAN, outcome_count INTEGER columns added to markets table
  - ORM enabled in database.py
  - Applied via session-mode pooler + SET statement_timeout='0' trick

Migration 018 (feature_snapshot JSONB): ✅ APPLIED 2026-02-24
  SQL applied:
    ALTER TABLE prediction_log ADD COLUMN IF NOT EXISTS feature_snapshot JSONB;
    CREATE INDEX IF NOT EXISTS idx_prediction_log_feature_snapshot
      ON prediction_log USING gin (feature_snapshot)
      WHERE feature_snapshot IS NOT NULL;
  Applied via asyncpg direct connection (no psql on PATH — used Python asyncpg).

Verify with:
  SELECT column_name FROM information_schema.columns
  WHERE table_name='prediction_log' AND column_name='feature_snapshot';

Future migrations rules (NEVER VIOLATE):
  - Session mode URL (port 5432) only — NEVER transaction mode for DDL
  - NEVER ALTER TABLE ADD COLUMN DEFAULT x NOT NULL on large tables (full table lock)
  - ALWAYS: add NULL first → backfill in batches → ADD CONSTRAINT NOT VALID → VALIDATE
  - ALWAYS: CREATE INDEX CONCURRENTLY (never CREATE INDEX on live table)
```

---

## 9. ML MODEL DETAILS

**10-model ensemble**: `random_forest`, `extra_trees`, `xgboost`, `hist_gradient_boosting`, `gradient_boosting`, `lightgbm`, `catboost`, `logistic_regression`, `ridge`, `knn`

**Training triggers**: cold start (no cache), drift detected, 24h staleness

**Model weights** (post A3):
- Previously fixed (0.05–0.15)
- Now: OOF `cross_val_predict(cv=3)` → `RidgeCV` meta-learner → non-negative normalized weights
- Requires ≥50 training samples AND ≥3 models; falls back to rank-weights if insufficient data

**Known issue**: Models predict ~0.92 (overconfident) — expected before sufficient paper trade feedback. Adaptive min-confidence threshold and Brier score calibration will correct over time.

**Cache**: `data/model_cache.pkl` — delete to force retrain (~2-3 min)
Must contain: `models`, `scaler`, `model_weights`, `ensemble_blend`, `feature_columns`, `best_feature_names`
CatBoost note: SKIP `CalibratedClassifierCV` — no `__sklearn_tags__` support.

**Features** (~50+ from FeatureEngineer): price, volume, time-to-resolution, sentiment, VPIN, wallet stats, on-chain, price velocity, large-trade toxicity, elite flow signals.

---

## 10. TIER 2 FEATURES — ALL COMPLETE

```
#12 NegRisk multi-outcome arbitrage: ✅ DONE 2026-02-24
    LEG-A (buy all YES when SUM<1) + LEG-B (buy all NO when SUM>N-1)
    Proportional Kelly sizing: complement_weight = (1-p) / sum(1-p)
    File: bots/arbitrage_bot.py:_scan_negrisk_arbitrage()

#16 LLM resolution clarity:         ✅ DONE (earlier session)
    _get_resolution_clarity() multiplier in EnsembleBot

#17 Disposition effect:             ✅ DONE (earlier session)
    MomentumBot Mode 5

#18 VPIN toxicity:                  ✅ DONE (earlier session)
    Wired into EnsembleBot + ArbitrageBot via _get_vpin_toxicity()

#19 Wallet clustering:              ✅ DONE 2026-02-24
    3-heuristic _build_similarity_graph() in wallet_clustering.py
    Used by MirrorBot for cluster-based signal amplification

#20 Order flow imbalance:           ✅ DONE via B8 (price velocity + volume acceleration proxy)
    _get_price_momentum_signal() in EnsembleBot
```

---

## 11. CURRENT SCAN PERFORMANCE (warm cache, Supabase ~400ms/query)

```
EnsembleBot:  ~12-15s (cold start: 120s+ first, ~50s second, ~12s third+)
ArbitrageBot: ~10-15s (was timing out at 120s — fixed 2026-02-24)
MomentumBot:  ~50-55s
MirrorBot:    ~6-22s
```

After VPS migration (direct PostgreSQL, ~1ms/query): expect ~10× speedup across all bots.
Then raise: `SCAN_MARKET_LIMIT=50+`, `ENSEMBLE_SCAN_CONCURRENCY=5+`, `DB_POOL_SIZE=40+`.

---

## 12. PENDING WORK — PRIORITY ORDER

### 🔴 Immediate Action (before restart)

1. **Delete `data/model_cache.pkl`** — forces retrain with Ridge stacking weights (A3) and recency weighting (B2). Current cache has fixed weights from before the implementation.
   ```powershell
   Remove-Item C:\lockes-picks\polymarket-ai-v2\data\model_cache.pkl
   ```

### 🟡 Data-dependent (activates automatically as trades accumulate)

2. **B7 DtACI conformal sizing** — zero code needed. `conformal_multiplier` slot is live in `calculate_optimal_size()`. Activates when `prediction_log` has 100+ resolved paper trades. Monitor: check `prediction_log` row count and `outcome` column fill rate.

3. **B1 high-surprise relay** — live now. Starts generating penalties once `record_outcome()` receives resolved trade data. Zero code needed.

4. **OOF Ridge stacking weights (A3)** — live but falls back to rank-weights until ≥50 training samples. Monitor: look for `"Stacking weights: {...}"` in log after training.

5. **B11 shadow maker analysis** — data accumulating. After paper trading, query `_shadow_maker_orders` from EnsembleBot to evaluate maker strategy viability.

### 🟡 Feature activation (single .env change)

6. **RL Trade Timing Agent** — set `RL_TRADE_TIMING_ENABLED=true` in `.env`.
   Fully implemented in `rl_trade_timing.py`. Wired in `order_gateway.py`. Tabular Q-learning, 324 states × 3 actions. No code changes needed.

### 🟢 Infrastructure (ops work — biggest performance unlock)

7. **VPS migration** — move from Supabase pooler (400ms/query) to direct PostgreSQL on EU VPS (~1ms/query).
   Target: Amsterdam (AMS) or Dublin (DUB) — CLOB servers are in AWS eu-west-2 London; pick lowest ping.
   Steps:
   a. Provision VPS (Hetzner CX21 or similar — €5/mo)
   b. Install PostgreSQL 15, configure pg_hba.conf for remote access
   c. `pg_dump` from Supabase → `pg_restore` to VPS
   d. Update `DATABASE_URL` in `.env` to direct connection string
   e. Remove `statement_cache_size=0` from asyncpg connect args (only needed for pooler)
   f. Raise limits: `DB_POOL_SIZE=40`, `SCAN_MARKET_LIMIT=50`, `ENSEMBLE_SCAN_CONCURRENCY=5`
   No application code changes required.

8. **Redis connection** — set `REDIS_URL` in `.env` when VPS is provisioned.
   Currently all Redis caches miss (in-memory fallbacks active — functional but no cross-process sharing).
   Redis enables: arb dedup cache, model weight sharing, cross-session position tracking.

9. **Live trading switch** — after 2–4 weeks of paper trading validates positive EV + acceptable drawdown:
   - `PAPER_TRADING=false`
   - Fund real USDC at `WALLET_ADDRESS`
   - Consider `ENSEMBLE_MIN_CONFIDENCE=0.60` (slightly lower threshold for more trade frequency)
   - Monitor first 48h of live trading closely for unexpected behavior

---

## 13. KNOWN ISSUES (non-blocking)

```
Redis not connected:        All Redis caches miss. In-memory fallbacks active.
                            ArbitrageBot dedup returns False (was_executed_recently always False —
                            safe for paper trading, may re-submit same arb in live trading).

Polygon RPC 401:            Mempool monitoring disabled. Non-critical (informational only).

EnsembleBot cold start:     First scan always times out (120s). Second scan ~12s. Expected.
                            MODEL CACHE (data/model_cache.pkl) prevents this on restart
                            (avoids full retrain on startup).

0 paper trades from bots:   System was crashing from DB exhaustion (now fixed).
                            After restart with VPN, bots should begin scanning and
                            potentially executing trades when confidence > ENSEMBLE_MIN_CONFIDENCE.
                            Conservative threshold (0.65) means first trades may take hours/days.

Models predict ~0.92:       Overconfident. Will self-correct as paper trade outcomes flow in
                            and calibrate Brier-score adaptive threshold.

Elite signals returning 0:  FIXED 2026-02-24 (timezone bug). Will populate on next run.
```

---

## 14. MOCK PATTERNS FOR TESTS

```python
# db.get_session() is SYNC returning async context manager:
from unittest.mock import MagicMock, AsyncMock

class MockSessionCtx:
    def __init__(self, mock_session=None):
        self._session = mock_session or MagicMock()
    async def __aenter__(self):
        return self._session
    async def __aexit__(self, *a):
        pass

db = MagicMock()
db.get_session = MagicMock(return_value=MockSessionCtx())
db._verify_database = AsyncMock()
db.session_factory = MagicMock()  # truthy = DB available

# For tests that need the semaphore:
db._semaphore = asyncio.Semaphore(15)

# For async DB methods:
db.get_all_tradeable_markets = AsyncMock(return_value=[...])
db.get_open_positions = AsyncMock(return_value=[])
```

---

## 15. CURRENT .ENV SETTINGS (complete reference)

```env
# Database
DATABASE_URL=postgresql+asyncpg://postgres.[ref]:[pass]@aws-0-us-east-1.pooler.supabase.com:5432/postgres
DB_POOL_SIZE=12
DB_MAX_OVERFLOW=3

# Scanning
SCAN_MARKET_LIMIT=10
ENSEMBLE_SCAN_CONCURRENCY=2
BOT_SCAN_TIMEOUT_SECONDS=120
DAILY_INGESTION_MARKETS_COUNT=200
DAILY_INGESTION_PRICES_MARKETS=200
INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS=180

# Trading
ENSEMBLE_MIN_CONFIDENCE=0.65
ENSEMBLE_TARGET_CATEGORIES=
ALPHA_DECAY_LAMBDA=0.5
TRAINING_RECENCY_LAMBDA=1.0
NEGRISK_MAX_TOTAL_RISK=300.0
ARB_CORRELATION_MARKET_LIMIT=5

# Feature flags
RL_TRADE_TIMING_ENABLED=false        # set true to activate RL agent
PAPER_TRADING=true                   # set false for live trading
LEARNING_PERSISTENCE=false           # set true to persist models to Supabase

# Wallet
WALLET_ADDRESS=0x...                 # your Polymarket wallet (USDC)

# Optional
REDIS_URL=                           # empty = in-memory fallbacks
```

After VPS migration, change to:
```env
DATABASE_URL=postgresql+asyncpg://user:pass@VPS_IP:5432/polymarket
DB_POOL_SIZE=40
SCAN_MARKET_LIMIT=50
ENSEMBLE_SCAN_CONCURRENCY=5
```

---

## 16. HOW TO RESUME A SESSION — STEP BY STEP

```
1. Read this file fully (you are here)

2. Check current state:
   - Get-Content C:\lockes-picks\polymarket-ai-v2\data\paper_trading.log | Select-Object -Last 100
   - Look for: "Scan cycle done" (bot running), semaphore timeouts (pool issues), trade executions

3. Run tests to confirm baseline:
   powershell.exe -Command "cd 'C:\lockes-picks\polymarket-ai-v2'; python -m pytest tests/unit/ -v --no-cov --tb=short"
   Must show: 321 passed, 0 failed, 0 warnings

4. Check MEMORY.md:
   C:\Users\samwa\.claude\projects\C--lockes-picks-polymarket-ai-v2\memory\MEMORY.md

5. VPN: Turn on Surfshark before any python run

6. If starting fresh run: delete data/model_cache.pkl first (Ridge stacking weights)

7. For implementation tasks: always run tests after EVERY change
   If tests fail: check test output carefully — mock patterns are specific (see Section 14)

8. Current recommended next actions (in order):
   a. Delete model_cache.pkl → restart bot → monitor for first real paper trades
   b. Once 100+ resolved trades accumulate → B7 and B1 activate automatically
   c. VPS migration when budget approved → unlock 10× scan performance
   d. Set RL_TRADE_TIMING_ENABLED=true when stable scanning established
   e. Switch to live trading after 2-4 weeks paper validation
```

---

## 17. ARCHITECTURE FLOW DIAGRAM

```
main.py
  └── pre_flight_check()  ← clears stale advisory locks
  └── BaseEngine.start()
        ├── StreamingPersister (bulk DB writes via get_raw_session)
        ├── SignalIngestion (9 sources, Semaphore(2) DB writes, B9 queues)
        ├── WhaleTracker
        ├── FeaturePrecompute (90s delay, 60s interval, Semaphore(1) ← FIXED)
        ├── KS Regime Detection (5min delay, 30min interval, no DB)
        ├── Observability SLI (2min delay, 60s interval, no DB)
        ├── IngestionScheduler (180s delay, advisory lock)
        └── ResolutionListener → event_bus → prediction_log backfill

  EnsembleBot scan loop
    ├── get_all_tradeable_markets() → 60s cache
    ├── asyncio.Semaphore(ENSEMBLE_SCAN_CONCURRENCY=2) per market batch
    └── _analyze_one_token(market_id, token_id, side, price)
          ├── predict() → _extract_features() → get_session() [1 session for elite+path+regime]
          ├── FLB delta (A1 category-scaled)
          ├── Lifecycle penalty (A4)
          ├── Partition dependence penalty
          ├── High-surprise penalty (B1)
          ├── Sentiment
          ├── VPIN + large-trade toxicity (B3)
          ├── LLM clarity (Tier 2 #16)
          ├── OFI proxy (B8)
          ├── IQR disagreement (B4)
          ├── Alpha decay
          └── min_confidence gate → place_order() or skip

  ArbitrageBot scan loop
    ├── analyze_opportunity() per market (YES+NO sum ≠ 1)
    ├── _scan_cross_market_arbitrage() [30s timeout ← FIXED]
    ├── _scan_bond_opportunities() [20s timeout ← FIXED]
    └── _scan_negrisk_arbitrage() [20s timeout ← FIXED]
          ├── LEG-A: SUM(YES) < 1 → buy all outcomes
          └── LEG-B: SUM(YES) > N-1 → buy all NO outcomes

  MomentumBot scan loop
    └── 5 modes including disposition effect (Mode 5, Tier 2 #17)

  MirrorBot scan loop
    └── WalletClustering → cluster-based signal amplification (Tier 2 #19)

  OrderGateway.execute()
    ├── kill_switch check (get_raw_session — bypasses semaphore)
    ├── risk_manager.check_risk_limits()
    ├── trade_coordinator (advisory lock dedup)
    ├── [optional] rl_agent.decide() timing adjustment
    └── paper_trading_gateway OR live_clob_gateway
```

---

## 18. KEY CONSTANTS AND THRESHOLDS (quick reference)

```python
# Prediction engine
TRAINING_RECENCY_LAMBDA = 1.0          # B2: recency decay (exp(-1.0 * (T-i)/T))
PHT_DELTA = 0.005                       # A2: PHT sensitivity
PHT_LAMBDA = 50.0                       # A2: PHT detection threshold
HIGH_SURPRISE_THRESHOLD = 0.3          # B1: prediction error threshold

# Position sizing
KELLY_FRACTION = 0.25                   # 25% of full Kelly
MAX_POSITION_PCT = 0.10                 # 10% of capital per position
MIN_POSITION_PCT = 0.01                 # 1% of capital min
MEISTER_SCALE = "4*p*(1-p)"            # A5: parabola 0→1→0

# Wallet clustering
CO_TRADE_WINDOW_SECONDS = 300          # 5-minute co-trade window
SIZE_CORRELATION_THRESHOLD = 0.65      # Pearson r threshold
MIN_CO_TRADES = 3                       # minimum co-trades for edge

# NegRisk arbitrage
NEGRISK_MAX_TOTAL_RISK = 300.0         # max $ across all outcomes

# DB pool
SEMAPHORE_LIMIT = 15                    # Supabase Pro hard cap
SEMAPHORE_TIMEOUT = 30.0               # seconds before DatabaseError raised
PRECOMPUTE_CONCURRENCY = 1             # FIXED (was 3)

# ArbitrageBot timeouts (FIXED 2026-02-24)
CROSS_MARKET_ARB_TIMEOUT = 30.0
BOND_SCAN_TIMEOUT = 20.0
NEGRISK_SCAN_TIMEOUT = 20.0
ARB_CORRELATION_LIMIT = 5              # FIXED (was 20)
```

---

## 19. TESTING NOTES

**24 test files, 321 tests** — must all pass before any commit or restart.

Notable test files:
```
test_2026_alpha_infrastructure.py   — A1-A5, B-series signal infrastructure
test_arbitrage_bot.py               — arb scanning, NegRisk LEG-A/B
test_dynamic_position_sizing.py     — Kelly + A5 boundary scale + B7 conformal
test_ensemble_bot.py                — full pipeline, FLB, IQR, toxicity
test_kalshi_signal.py               — B10 Kalshi cross-venue signal
test_prediction_engine.py           — drift detection, Ridge stacking, PHT
test_rl_trade_timing.py             — Q-learning agent, PER, ADWIN
test_wallet_clustering.py           — 3-heuristic graph, DFS components
test_signal_ingestion.py            — B9 queue ring buffers
```

If tests fail after a change:
- Check mock patterns in Section 14 — DB mock is specific
- `db.get_session` is SYNC returning async context manager (not AsyncMock)
- `db._verify_database` IS AsyncMock
- Many tests use `MagicMock(spec=Database)` — add new methods as needed
