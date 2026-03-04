# MASTER HANDOFF — Polymarket AI V2
**Session 16 | 2026-02-25 UTC | Carbon-Copy Continuity Document**
**Read this FIRST before any code. Supersedes all prior handoffs.**

---

## 0. CRITICAL ORIENTATION

**What this project is**: A 4-bot autonomous paper-trading system on Polymarket prediction markets. Runs 24/7 on AWS Lightsail VPS. Makes real-time predictions using a 10-model ML ensemble and places paper trades (no real money yet). Goal: prove positive alpha before switching to live trading.

**Current status**: LIVE and trading. VPS PID ~1080991 (post-Session 15 deploy). 321/321 tests pass. Net P&L: -$5,567 since launch (expected — bots were misconfigured through Sessions 1-14, fixed progressively).

**Session 16 deliverable**: 11-phase Master Elevation Plan fully designed (not yet implemented). This handoff covers everything needed to implement it.

---

## 1. VPS & ENVIRONMENT

```
VPS: AWS Lightsail, 34.248.60.104, eu-west-1
SSH: ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104
App dir: /opt/polymarket-ai-v2/
Python: /opt/polymarket-ai-v2/venv/bin/python  (Python 3.x)
Service: polymarket-ai.service (systemd, auto-restart)
DB: Local PostgreSQL on VPS (NOT Supabase), pool_size=40, max_overflow=5
Log: /opt/polymarket-ai-v2/data/paper_trading.log
```

**Local dev**:
```
Working dir: C:\lockes-picks\polymarket-ai-v2
Python 3.13.3, Windows 10, PowerShell
VPN REQUIRED: Surfshark ON (US IP) before running — Polymarket API 403s without it
Run: python main.py OR python run_paper.py
```

**Deploy pattern (single file)**:
```bash
scp -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" "local_file" "ubuntu@34.248.60.104:/tmp/file"
ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 "sudo cp /tmp/file /opt/polymarket-ai-v2/path/file && sudo systemctl restart polymarket-ai.service"
```

---

## 2. FOUR BOTS

| Bot | Strategy | Status | Key Settings |
|-----|---------|--------|-------------|
| EnsembleBot | 10-model ML ensemble | TRADING | ENSEMBLE_MIN_CONFIDENCE=0.40 |
| MomentumBot | Z-score momentum + learning | TRADING | SCAN_MARKET_LIMIT=50 |
| MirrorBot | Copy elite Polymarket traders | NEAR-ZERO ALPHA | MIRROR_MIN_CONSENSUS=2 |
| ArbitrageBot | Binary/NegRisk price gaps | TRADING | ARB_MAX_MARKETS_PER_SCAN=10 |

---

## 3. ARCHITECTURE MAP (ALL KEY FILES)

```
C:\lockes-picks\polymarket-ai-v2\
├── main.py                            # Entry point, watchdog, bot stagger, preflight
├── run_paper.py                       # Thin wrapper for paper trading mode
├── config/
│   └── settings.py (~610 lines)      # ALL configurable settings, env-readable
├── base_engine/
│   ├── base_engine.py                # Core: market fetch, feature precompute, coordination
│   ├── base_bot.py (430 lines)       # BaseBot: scan loop, capital allocator, kill switch
│   ├── prediction/
│   │   └── prediction_engine.py (~3000 lines) # 10-model ensemble, training, inference
│   ├── risk/
│   │   └── risk_manager.py           # Kelly sizing, calibration penalty, position limits
│   ├── execution/
│   │   ├── order_gateway.py          # 14-gate trade validator, paper/live routing
│   │   ├── paper_trading.py          # Paper trade execution, position tracking
│   │   └── position_manager.py       # Exit monitoring, P&L calculation, grace periods
│   ├── coordination/
│   │   ├── trade_coordinator.py      # Atomic reservation (advisory locks), STALE_RESERVATION=5min
│   │   └── kill_switch.py            # System-wide halt (30s TTL cache, raw session)
│   ├── data/
│   │   ├── database.py (~3400 lines, 23 ORM tables) # SQLAlchemy models
│   │   ├── streaming_persister.py    # WebSocket trade/price persistence
│   │   └── recovery_hierarchy.py    # 6-tier recovery escalation
│   ├── signals/
│   │   ├── signal_ingestion.py       # 9 collection loops (news, social, events, etc.)
│   │   ├── whale_tracker.py          # Whale detection, Redis publish to "whale_alerts"
│   │   └── ...
│   ├── learning/
│   │   └── scheduler.py              # Retrain scheduler, drift detection, A/B testing
│   └── monitoring/
│       ├── bot_state_machine.py      # FSM: healthy→degraded→failed→recovering→safe_mode
│       ├── health_scheduler.py       # APScheduler: 7 jobs (drawdown, exposure reconcile, etc.)
│       ├── degradation_manager.py    # 5-tier fleet sizing (1.0/0.75/0.5/0.1/0.0)
│       ├── portfolio_drawdown.py     # High-water-mark circuit breaker (5% daily / 10% peak)
│       ├── streaming_anomaly.py      # ADWIN + HalfSpaceTrees per-metric
│       └── log_miner.py              # drain3 log template miner, 9 critical patterns
├── bots/
│   ├── ensemble_bot.py (529 lines)   # EnsembleBot: 10-model, IQR scaling, warm cache
│   ├── momentum_bot.py               # MomentumBot: Z-score, dynamic stops, cooldown
│   ├── mirror_bot.py                 # MirrorBot: elite trader copying, consensus gating
│   └── arbitrage_bot.py              # ArbitrageBot: binary + NegRisk arb, WS reactive
├── tests/unit/ (321 tests, 24 files) # All must pass: pytest tests/unit/ -v --no-cov --tb=short
├── data/
│   ├── paper_trading.log             # TeeLogger writes here
│   └── model_cache.pkl               # Cached scaler/models — DELETE to force retrain
└── schema/migrations/                # 021 applied (self-healing tables)
```

---

## 4. KEY CONCEPTS & RULES (NEVER VIOLATE THESE)

1. **YES and NO are both BUY** — buying the YES or NO outcome token. SELL = close position only. P&L = `(current - entry) / entry` regardless of direction.
2. **Brier Score** — primary model quality metric. Current: 0.424 (poor). Coin flip = 0.25. Target: <0.32.
3. **321 tests must always pass** after EVERY change: `python -m pytest tests/unit/ -v --no-cov --tb=short`
4. **Delete `model_cache.pkl` after feature dimension changes** — otherwise scaler/models mismatch new feature count.
5. **Kelly is permanently disabled** — `KELLY_MAX_BRIER=0.20` vs current Brier=0.424. Phase 2 of elevation plan fixes this.
6. **Market IDs**: numeric `m.id` (628113) vs condition_id `0x339d...`. Always JOIN both.
7. **SELL side in positions table is NOT an open position** — it's an exit attempt record. Filter `WHERE side != 'SELL'` when counting exposure.
8. **DB semaphore timeout = 30s** — raises DatabaseError instead of hanging. `get_raw_session()` bypasses semaphore (for kill switch, bulk inserts).
9. **Signal features always return (0.5, 0.5) at inference** — dead code confirmed. Phase 7 fixes this.
10. **VPS DB pool**: pool_size=40, max_overflow=5, semaphore=45.
11. **LogMiner self-exclusion**: `if "logminer:" in line_lower: continue` (must stay — prevents 2000+/hr cascade).
12. **BiasDetector 0.95 correlation with price**: EXPECTED AND CORRECT. Price = consensus probability. Not a bug.

---

## 5. CURRENT STATE (POST-SESSION 15)

**Deployed (all working)**:
- Batch 1-4 from Sessions 12-15 (see deployment history below)
- 51 positions seeded, cash ~$92,345, exposure ~$2,087
- EnsembleBot warm scans: 1.3-3.9s (was 750s+)
- MomentumBot warm scans: 13-37s (was 160s)
- Drift tracker persists across restarts (Batch 4)
- Trade #135+ confirmed placed

**Not yet deployed**: The entire 11-Phase Master Elevation Plan (designed in Session 16).

---

## 6. SESSION DEPLOYMENT HISTORY (CONDENSED)

**Session 9** — THE critical fix: `datetime.now(timezone.utc).replace(tzinfo=None)` in trade_coordinator. VPS postgres is TIMESTAMP WITHOUT TIME ZONE — timezone-aware datetimes caused DataError on EVERY reservation. All prior sessions had 0 trades. This one fix enabled trading.

**Sessions 10-11** — Fixed drawdown circuit breaker formula, SafeMode recovery path, exposure reconciliation loop (5-minute scheduled reconcile from DB ground truth).

**Session 12** — Fixed SELL phantom exposure (SELL-side rows counted as open positions), CPU steal time fix (Lightsail 66% hypervisor steal → was 100% CPU), SELL coordinator timeout 5s → 15s.

**Session 13** — Stop-loss re-entry loop fix (MomentumBot on market 566187 was cycling buy→stop→rebuy). Added `_recently_exited` cooldown dict (15min). Wired learning thresholds to Z-entry. Dynamic stop/take per learning ratio.

**Session 14 (Batch 1-3)** — 16 fixes deployed including:
- NegRisk LEG-B price bug (was using YES price for NO orders)
- DegradationManager recalibrated for 4 bots (was 8-bot config → all bots at 50% sizing permanently)
- Cache TTLs fixed (MomentumBot: 60s→600s, was shorter than scan duration)
- L8 TA query consolidated (eliminated duplicate get_session() per market)
- DB indexes added (idx_trades_market_ts, idx_market_prices_market_ts)
- Signal ingestion semaphore timeout fixed
- Mirror bot category fix (`"category": _cat` was always "")

**Session 15 (Batch 4)** — ML accuracy + reporting fixes:
- PATH_SUMMARY_MAX_ROWS: 5000→50000 (was missing 80% of training rows)
- Drift tracker persistence (to_dict/from_dict in model_cache.pkl)
- unrealized_pnl now includes taker fee
- Zero-size exit guard
- entry_fee stored in position dict (P&L accuracy)
- STALE_RESERVATION_MINUTES: 2→5min
- Reaper DELETE wrapped in asyncio.wait_for(timeout=10s)

---

## 7. KNOWN ISSUES (REMAINING)

1. **Redis not connected** — all Redis caches miss, in-memory fallbacks used. Non-blocking. Whale alerts published to Redis but nothing subscribes (Phase 3.2 in elevation plan fixes the subscribe side).
2. **Brier=0.424** — Feature engineering defects. Phase 1 of elevation plan fixes root causes.
3. **Kelly permanently disabled** — KELLY_MAX_BRIER=0.20 too low. Phase 2 raises to 0.45.
4. **Whale detection 40s latency** — Phases 3.1+3.2 reduce to <15s end-to-end.
5. **MirrorBot near-zero alpha** — structural problems, Phases 4.1-4.4 address all.
6. **Signal features always (0.5, 0.5) at inference** — Phase 7 fixes dead code.
7. **Polygon RPC 401** — mempool monitoring disabled. Non-critical.
8. **`prediction_log.correlation_id` missing** — non-fatal warning on every prediction log write. Fix: `ALTER TABLE prediction_log ADD COLUMN IF NOT EXISTS correlation_id VARCHAR(64);` on VPS postgres.

---

## 8. DB / INFRA DETAILS

**VPS PostgreSQL** (local, NOT Supabase):
- pool_size=40, max_overflow=5, total semaphore=45
- `get_session()`: normal sessions with semaphore
- `get_raw_session()`: bypasses semaphore (kill switch, bulk inserts, advisory locks)
- **Supabase**: Legacy connection at port 5432 (NOT 6543), max 15 connections. DO NOT use for VPS deploy.

**Key ORM tables** (database.py, ~3400 lines, 23 tables):
```
markets                — m.id (numeric), m.condition_id (0x...), m.resolution_source, m.yes_price, m.end_date_iso
trades                 — user_address, market_id, price, size, side, trade_ts
positions              — bot_id, market_id, side (YES/NO/SELL), status, avg_price, size, opened_at
paper_trades           — correlation_id, bot_name, market_id, side, price, size, fee
market_prices          — market_id, condition_id, price, timestamp
users                  — address, win_rate, profit, elite_score
trade_signals          — market_id, signal_confidence, signal_direction, signal_source, created_at
signals                — market_id, source_type, direction, confidence, is_breaking, priority_score
whale_alerts           — (Redis only — not a DB table)
bot_health_states      — bot_name, status, consecutive_errors (migration 021)
config_history         — key, old_val, new_val, changed_at (migration 021)
dead_letter_queue      — payload, error_msg, retry_count (migration 021)
```

**Applied migrations**: 017 (neg_risk, outcome_count), 018 (feature_snapshot JSONB+GIN), 021 (self-healing tables).

---

## 9. PREDICTION ENGINE INTERNALS (prediction_engine.py, ~3000 lines)

**43 features currently** (confirmed, dimension changes in Phase 1 → 46):
```python
# L1-L6: Market fundamentals
["price", "liquidity", "volume", "user_win_rate", "user_profit", "category_encoded"]

# Conditional L7a: Elite direction (if USE_ELITE_NET_DIRECTION=True)
["elite_net_direction", "elite_direction_1h", "elite_direction_6h", "elite_direction_24h"]

# L7b: Path/regime features (enabled since PATH_SUMMARY_MAX_ROWS=50000)
# [~12 path features]

# L7c: Time features (BROKEN — Phase 1 fixes)
["time_day_of_week",    # linear float(weekday())/6.0 — BROKEN (line 1914 train, 2892 inf)
 "time_hour_of_day",    # linear float(hour)/23.0 — BROKEN (line 1915 train, 2893 inf)
 "time_market_age",     # OK — monotonic [0,1]
 "time_to_expiry"]      # OK — monotonic [0,1]

# L8: Technical analysis
["ta_rsi", "ta_bollinger_pos", "ta_atr_normalized"]

# Conditional: Signal features (if USE_SIGNAL_FEATURES_IN_ML=True)
["signal_confidence",         # ALWAYS 0.5 at inference (dead code — Phase 7 fixes)
 "signal_direction_encoded"]  # ALWAYS 0.5 at inference (dead code — Phase 7 fixes)
```

**Caches in prediction_engine.py** (ALL are `_BoundedCache` instances):
```python
self._prediction_cache        # TTL=300s (Phase 1.3 reduces to 60s), line ~337
self._feature_vector_cache    # TTL=120s, max_size=2000, key="fv:{market_id}:{token_id}", line 352
self._l8_cache                # dict (NOT _BoundedCache), key="l8:{market_id}"
self._signal_cache            # MUST be _BoundedCache(max_size=500, default_ttl=30.0) — Phase 7 adds init
self._market_cache            # _BoundedCache
```

**_BoundedCache class** (lines 265-304):
- Methods: `get(key)`, `set(key, value, ttl=None)`, `clear()`, `__len__()`
- Internal storage: `self._data` dict (NOT `self._cache` — important for Phase 6)
- NO `.pop()` method — Phase 6 adds `delete(key)` method: `self._data.pop(key, None)`
- Eviction: purge expired → if >90% capacity, remove oldest 25%

**Training**: SQL query ~line 1558 computes elite_net_direction as equal-weighted (not dollar-weighted — Phase 5 fixes).
**Inference signal lookup** (lines 2837-2853): dead code — `_sig_svc = None`, always returns (0.5, 0.5).
**Feature vector cache fast-path** (lines 2169-2177): cache hit updates only `price` slot inline — all other features (RSI, elite direction) remain up to 120s stale (Phase 6 adds invalidation on >3pp price move).

**10 models in ensemble**:
CatBoost, XGBoost, LightGBM, RandomForest, ExtraTreesClassifier, GradientBoosting, Ridge stacking (A3), Calibrated variants, etc.

---

## 10. RISK MANAGER (risk_manager.py)

**`calculate_position_size()` flow** (~line 396):
1. Base size = TOTAL_CAPITAL × RISK_BASE_PCT (10% = $10K)
2. × confidence scalar (0.35→0.55 confidence → 0.3→0.8× multiplier)
3. × calibration penalty (Brier>0.30 → 0.75× floor)
4. Cap at RISK_MAX_POSITION_SIZE_USD=$1,000 (kills confidence scaling — Phase 2 replaces with edge-proportional cap)
5. Convert to shares via price

**`_kelly_position_size()`** (lines 453-489): Fully implemented, PERMANENTLY DISABLED. Gate: `if brier > KELLY_MAX_BRIER: return None` where KELLY_MAX_BRIER=0.20 and Brier=0.424. Kelly formula: `kelly_full = (confidence×b - q)/b` where b=`(1-price)/price`, q=`1-confidence`. Guard at line 489: `if kelly_full <= 0: return None` (self-limits on bad predictions). KELLY_FRACTION=0.25 (quarter Kelly).

---

## 11. ORDER GATEWAY — 14 GATE CHECKS (order_gateway.py)

Sequential gates before any trade:
1. Kill switch active?
2. Paper trading mode check
3. Circuit breaker allow?
4. Bot state machine (healthy?)
5. Degradation tier (sizing reduction)
6. Capital allocated (USE_CAPITAL_ALLOCATOR)
7. Kelly/linear sizing
8. Min confidence threshold
9. Min position size ($50 floor)
10. Max single position ($1K cap — Phase 2 replaces)
11. Max total exposure ($10K cap)
12. Duplicate position check
13. TradeCoordinator.reserve_position() (advisory lock, 5min stale cleanup)
14. Final cash check

---

## 12. STREAMING / WHALE PIPELINE

**Current latency**: WebSocket trade → `StreamingPersister.on_trade()` <1ms → DB flush 1-10s → WhaleTracker polls every 30s → **total: 11-40s**

**WhaleTracker** (whale_tracker.py):
- `min_whale_size_usd = 10000.0` default
- Publishes to Redis channel `"whale_alerts"` (lines 187-198) — JSON with user_address, market_id, side, size, value_usd, smart_money_rank, timestamp
- Smart money rank formula: `score = win_rate×0.40 + roi×0.30 + volume×0.20 + elite×0.10`
- **CONFIRMED**: Zero bots subscribe to `"whale_alerts"` channel (orphaned pipeline)
- `_monitoring_loop()` polls DB every 30s (backup for REST-arrived trades)

**StreamingPersister** (streaming_persister.py):
- `on_trade()` fires in <1ms on every WS tick
- Queue flush: 10s interval OR 200-trade HWM
- Exponential backoff on flush crash: 1,2,4,8,16,30s cap

---

## 13. MIRROR BOT SPECIFICS (mirror_bot.py)

**Elite trader source**: `get_elite_traders(limit=25)` — users with high win_rate from DB
**Consensus threshold**: MIRROR_MIN_CONSENSUS=2 (noise level — p≈0.5% coincidence)
**Staleness filter**: MIRROR_MAX_DELAY_MINUTES=30 (trades fully repriced by then)
**`_parse_and_validate_trade()`** (lines 344-394): 5 filters: type check, dedup, required fields, price validation, freshness check
**`_check_and_execute_exits()`** (line 398): PURELY REACTIVE — follows elite traders' exits only. NO autonomous stop-loss. NO take-profit.
**`EliteReliabilityTracker`**: Instantiated at line 63, used for confidence weighting but NOT for entry filtering.

---

## 14. SIGNAL INGESTION (signal_ingestion.py)

**`start()` creates 9 core tasks** (lines 146-156):
- `_news_collection_loop()`, `_social_collection_loop()`, `_event_calendar_loop()`
- `_wikipedia_collection_loop()`, `_gdelt_collection_loop()`, `_hackernews_collection_loop()`
- `_spike_detection_loop()`, `_velocity_collection_loop()`, `_fourchan_collection_loop()`
- + 3 conditional: Reddit (USE_REDDIT_STREAMING), Telegram (TELEGRAM_API_ID), Discord (DISCORD_BOT_TOKEN)

**`_publish_signal()`**: Writes to `Signal` ORM table + Redis zadd to `signals:market:{mid}` and global signal queue.

---

## 15. MASTER ELEVATION PLAN — ALL 11 PHASES

### Critical Path Summary

| Phase | Problem | Root Cause | P&L Impact | Latency Fix |
|-------|---------|-----------|-----------|------------|
| 1 | Brier=0.424 | Hour AND DoW encoding broken, resolution_source unused, 5min stale predictions | Brier→~0.33 | 300s→60s cache |
| 2 | Kelly disabled, flat $1K cap | KELLY_MAX_BRIER=0.20 too low, flat cap kills confidence scaling | +10-20% position efficiency | None |
| 3 | Whale 40s latency + orphaned Redis | StreamingPersister unhooked; no bot subscribes | None | 40s→<15s end-to-end |
| 4 | MirrorBot noise + no stop-loss | Consensus=2 noise, 30min stale, no autonomous exit | +20-35% MirrorBot P&L | 30min→5min |
| 5 | Elite direction unweighted | $50K whale = 50×$1K retail | Brier→~0.30 | None |
| 6 | Stale features on news | Feature cache doesn't invalidate on big moves | Coherent news predictions | None |
| 7 | Signal features always 0.5 | Dead code at inference, _signal_cache not initialized | Removes calibration error | None |
| 8 | No cross-platform signals | No Kalshi/PredictIt polling | +$1,500-$3,000/yr predictive alpha | 10-60s lead |
| 9 | Flat vol sizing, no ATR stops | regime_vol + atr_normalized computed but unused | +$1,000-$2,000/yr | None |
| 10 | Kelly unaware of drawdown; 6h retrain | No drawdown compression; slow learning | -$500-$1,000 loss avoidance | None |
| 11 | ArbitrageBot WS arb misses mispricings | Paired-token check only in 45s digest scan | +$500-$1,500/yr | 45s→<500ms |

**Delete model_cache.pkl**: After Phase 1 (43→46 features) and Phase 5 (elite direction values change).

---

### PHASE 1 — Feature Engineering (DEPLOY FIRST)

**Fix 1.1 — Hour-of-day AND day-of-week cyclical encoding**
File: `base_engine/prediction/prediction_engine.py`
- Training lines 1914-1915: Replace `float(weekday())/6.0` and `float(hour)/23.0` with:
```python
import math as _math
_l7_dow_sin = _math.sin(2 * _math.pi * _row_ts_l7.weekday() / 7.0)
_l7_dow_cos = _math.cos(2 * _math.pi * _row_ts_l7.weekday() / 7.0)
_l7_hod_sin = _math.sin(2 * _math.pi * _row_ts_l7.hour / 24.0)
_l7_hod_cos = _math.cos(2 * _math.pi * _row_ts_l7.hour / 24.0)
```
- Inference lines 2892-2893: Same replacements. `_now_utc.weekday()` and `_now_utc.hour`.
- Default (missing timestamp): all four → 0.0
- Feature columns line 1987: Replace `"time_day_of_week", "time_hour_of_day"` with `"time_dow_sin", "time_dow_cos", "time_hour_sin", "time_hour_cos"` → **43→45 features**
- Tests: assert all 4 new names in feature_columns; assert old names absent; verify `cos(2π×0/24) == 1.0`

**Fix 1.2 — Resolution source as feature**
File: `base_engine/prediction/prediction_engine.py`
- Training SQL (~line 1620): Add `COALESCE(m.resolution_source, '')` to SELECT (markets table already JOINed)
- Training feature assembly (~line 1763): After category_encoded:
```python
_res_src_map = {"gamma_api": 1, "blockchain": 2, "manual": 3, "oracle": 4, "uma": 5, "disputed": 6}
resolution_source_encoded = float(_res_src_map.get(str(row.get("resolution_source") or "").lower().strip(), 0))
base_cols.append("resolution_source_encoded")
```
- Inference (`_extract_features()` ~line 2826):
```python
_res_src = (getattr(market, "resolution_source", None) or "").lower().strip()
resolution_source_encoded = float(_res_src_map.get(_res_src, 0))
```
- Feature count: 45→46 total
- Delete model_cache.pkl: YES (combined with 1.1)

**Fix 1.3 — Prediction cache TTL 300s → 60s**
File: `config/settings.py` line 131 OR VPS `.env`
- Change: `CACHE_TTL_PREDICTIONS: int = int(os.getenv("CACHE_TTL_PREDICTIONS", "60"))`
- VPS .env: `CACHE_TTL_PREDICTIONS=60`
- Delete model_cache.pkl: NO

**Phase 1 deploy**: scp prediction_engine.py + settings.py → delete model_cache.pkl on VPS → restart
**Verify**: startup log shows "Training complete" with 46 features

---

### PHASE 2 — Kelly Sizing + Edge-Proportional Caps

**Fix 2.1 — Lower KELLY_MAX_BRIER to 0.45**
File: `config/settings.py` line 306 OR `.env`
- `KELLY_MAX_BRIER: float = float(os.getenv("KELLY_MAX_BRIER", "0.45"))`
- VPS .env: `KELLY_MAX_BRIER=0.45`
- Brier=0.424 < 0.45 → Kelly activates. If Brier degrades past 0.45, Kelly auto-disables again.

**Fix 2.2 — Edge-proportional position size cap**
File: `base_engine/risk/risk_manager.py` lines 447-448
```python
# Replace flat RISK_MAX_POSITION_SIZE_USD cap:
_edge = max(0.0, abs(confidence - 0.5))        # 0.0 at coin flip, 0.5 at certainty
_edge_scale = min(1.0, _edge / 0.15)            # 0% edge → 0×; 15%+ edge → 1×
max_pos_usd = getattr(settings, "RISK_MAX_POSITION_SIZE_USD", 1000.0) * max(0.2, _edge_scale)
adjusted_size = min(adjusted_size, max_pos_usd)
# max(0.2, ...) ensures $200 floor for any approved trade
```

**Fix 2.3 — Enable capital allocator**
File: `config/settings.py` line 311 OR `.env`
- `USE_CAPITAL_ALLOCATOR: bool = os.getenv("USE_CAPITAL_ALLOCATOR", "true").lower() in ("true", "1", "yes")`
- Infrastructure already exists in `base_bot.py:474-506` (`_get_allocated_capital()`): `multiplier = 1.0 + max(-0.5, min(0.5, pnl_pct × 5))`. Zero new code needed.

**Phase 2 deploy**: scp risk_manager.py + .env update → restart
**Verify**: log shows "Kelly sizing: fraction=0.25"

---

### PHASE 3 — Whale Detection Latency + Bot Pipeline

**Fix 3.1 — StreamingPersister → WhaleTracker direct hook**
File: `base_engine/data/streaming_persister.py` `__init__()`:
```python
self._whale_callback: Optional[Any] = None
self._whale_threshold_usd: float = 10000.0
```
Add to `on_trade()` after queue append (~line 126):
```python
_size = float(data.get("size") or 0)
_price = float(data.get("price") or data.get("outcomePrice") or 0.5)
if _size * _price >= self._whale_threshold_usd and self._whale_callback is not None:
    try:
        if asyncio.get_event_loop().is_running():
            asyncio.create_task(self._whale_callback(dict(data)))
    except RuntimeError:
        pass
```

File: `base_engine/signals/whale_tracker.py` — add method:
```python
async def handle_streaming_trade(self, record: dict) -> None:
    """Fast-path: called <1ms after WS tick."""
    try:
        market_id = record.get("market_id") or record.get("market") or ""
        size = float(record.get("size") or 0)
        price = float(record.get("price") or record.get("outcomePrice") or 0.5)
        if size * price < self.min_whale_size_usd:
            return
        _smr = await self._get_smart_money_rank_cached(record.get("user_address") or "")
        logger.info("Whale fast-path detected: market=%s size_usd=%.0f smr=%.2f", market_id, size*price, _smr)
        await self._publish_whale_alert(record, smart_money_rank=_smr)
    except Exception as _e:
        logger.debug("Whale fast-path error: %s", _e)
```

File: `base_engine/base_engine.py` — after both initialized:
```python
if hasattr(self, "streaming_persister") and hasattr(self, "whale_tracker"):
    self.streaming_persister._whale_callback = self.whale_tracker.handle_streaming_trade
    self.streaming_persister._whale_threshold_usd = self.whale_tracker.min_whale_size_usd
```

**Fix 3.2 — Bot subscription to whale_alerts Redis channel**
File: `bots/base_bot.py` `__init__()`:
```python
self._whale_priority_queue: asyncio.Queue = asyncio.Queue(maxsize=50)
```
Add method:
```python
async def _whale_alert_listener(self) -> None:
    if not hasattr(self, "_cache") or not getattr(self._cache, "redis", None):
        return  # No Redis: non-fatal
    try:
        pubsub = self._cache.redis.pubsub()
        await pubsub.subscribe("whale_alerts")
        async for message in pubsub.listen():
            if message.get("type") == "message":
                try:
                    data = json.loads(message["data"])
                    mid = str(data.get("market_id") or "")
                    if mid and not self._whale_priority_queue.full():
                        await self._whale_priority_queue.put(mid)
                except Exception:
                    pass
    except Exception as _e:
        logger.debug("Whale alert listener stopped: %s", _e)
```
Wire into scan loop before standard market fetch:
```python
_whale_priority: list[str] = []
while not self._whale_priority_queue.empty():
    try: _whale_priority.append(self._whale_priority_queue.get_nowait())
    except asyncio.QueueEmpty: break
if _whale_priority:
    logger.info("Processing %d whale-priority markets first", len(_whale_priority))
    for _wmid in _whale_priority:
        asyncio.create_task(self._analyze_one_market(_wmid))
```
Start listener: `asyncio.create_task(self._whale_alert_listener())` in scan loop setup.

**Phase 3 deploy**: scp streaming_persister.py + whale_tracker.py + base_engine.py + base_bot.py → restart (4 files)
**Verify**: within <1s of next large trade, log shows "Whale fast-path detected"

---

### PHASE 4 — MirrorBot Structural Fixes

**Fix 4.1 — Raise MIRROR_MIN_CONSENSUS to 3**
File: `config/settings.py` line 254 OR `.env`
- `MIRROR_MIN_CONSENSUS: int = int(os.getenv("MIRROR_MIN_CONSENSUS", "3"))`
- Consensus=3: p≈0.006% vs consensus=2: p≈0.5% (80× improvement)
- Adaptive threshold (`_update_consensus_threshold()` at line 108) can still lower to 2 for proven categories

**Fix 4.2 — Hot-trade timing filter (30min → 5min for mid-market)**
File: `bots/mirror_bot.py` `_parse_and_validate_trade()` ~line 344, after existing stale filter:
```python
_trade_age_s = (datetime.now(timezone.utc) - trade_ts).total_seconds()
_is_mid_market = 0.20 <= float(parsed.get("price", 0.5)) <= 0.80
_hot_max_s = getattr(settings, "MIRROR_HOT_TRADE_MAX_SECONDS", 300)
if _is_mid_market and _trade_age_s > _hot_max_s:
    logger.debug("MirrorBot: rejecting stale mid-market trade (%.0fs > %ds)", _trade_age_s, _hot_max_s)
    return None
```
Add to settings: `MIRROR_HOT_TRADE_MAX_SECONDS: int = int(os.getenv("MIRROR_HOT_TRADE_MAX_SECONDS", "300"))`
Near-resolution trades (price<0.20 or >0.80) keep 30min window.

**Fix 4.3 — EliteReliabilityTracker minimum reliability gate**
File: `bots/mirror_bot.py` `_collect_and_aggregate_elite_trades()` ~line 280
```python
_reliability = self.elite_reliability_tracker.get_reliability(trader_address)
if _reliability is not None and _reliability < getattr(settings, "MIRROR_MIN_RELIABILITY", 0.45):
    continue  # Skip regressing traders
```
Add to settings: `MIRROR_MIN_RELIABILITY: float = float(os.getenv("MIRROR_MIN_RELIABILITY", "0.45"))`

**Fix 4.4 — MirrorBot position-level stop-loss and max hold time**
File: `bots/mirror_bot.py` `_check_and_execute_exits()` — add BEFORE elite-following logic:
```python
_stop_loss_pct = getattr(settings, "MIRROR_STOP_LOSS_PCT", 0.15)
_max_hold_h = getattr(settings, "MIRROR_MAX_HOLD_HOURS", 72)
_now = datetime.now(timezone.utc)
for position in open_positions:
    _entry = float(position.get("avg_price", 0.5) or 0.5)
    _current = float(position.get("current_price", _entry) or _entry)
    _side = position.get("side", "YES")
    _pnl_pct = (_current - _entry) / _entry if _side == "YES" else (_entry - _current) / _entry
    if _pnl_pct <= -_stop_loss_pct:
        logger.warning("MirrorBot stop-loss: market=%s pnl=%.1f%%", position.get("market_id"), _pnl_pct*100)
        await self._execute_exit(position, reason="stop_loss")
        continue
    _opened_at = position.get("opened_at")
    if _opened_at and isinstance(_opened_at, datetime):
        _hold_h = (_now - _opened_at).total_seconds() / 3600
        if _hold_h >= _max_hold_h:
            await self._execute_exit(position, reason="max_hold_time")
```
Add to settings: `MIRROR_STOP_LOSS_PCT: float`, `MIRROR_MAX_HOLD_HOURS: int = 72`

**Phase 4 deploy**: scp mirror_bot.py + settings.py → restart
**Verify**: fewer opportunities logged; positions >15% loss trigger stop-loss log

---

### PHASE 5 — Dollar-Weighted Elite Direction SQL

**Fix 5.1**
File: `base_engine/prediction/prediction_engine.py` training SQL LATERAL join ~line 1560
Replace equal-weight with log-dollar weight (applies to all 4 time windows: current/1h/6h/24h):
```sql
SUM(
    CASE WHEN t2.side IN ('YES','BUY') THEN 1.0 ELSE -1.0 END
    * COALESCE(u2.win_rate, 0.5)
    * LOG(1 + ABS(COALESCE(t2.size, 0)) * COALESCE(t2.price, 0.5))
) / NULLIF(SUM(
    COALESCE(u2.win_rate, 0.5) * LOG(1 + ABS(COALESCE(t2.size, 0)) * COALESCE(t2.price, 0.5))
), 0) as elite_net_direction
```
CRITICAL: Apply identical formula to inference path (~line 2662-2680). Train-serve parity required.
Gotcha: `ABS()` for legacy negative size records; `COALESCE(t2.price, 0.5)` for NULL prices.
Delete model_cache.pkl: YES (elite_net_direction values change for all training rows).

**Phase 5 deploy**: scp prediction_engine.py → delete model_cache.pkl → restart
**Verify**: retrain occurs, Brier check in 6h

---

### PHASE 6 — Feature Cache Price-Move Invalidation

**Fix 6.1**
FIRST: Add `delete(key)` method to `_BoundedCache` class (lines 265-304):
```python
def delete(self, key: str) -> None:
    """Explicitly invalidate a cache entry."""
    self._data.pop(key, None)
```
(IMPORTANT: `_BoundedCache` stores data in `self._data`, NOT `self._cache`. Original plan had wrong attribute name.)

File: `base_engine/prediction/prediction_engine.py` feature vector cache fast-path ~line 2169:
```python
if _cached_fv is not None:
    features = list(_cached_fv)
    _price_idx = None
    try: _price_idx = (self.feature_columns or []).index("price")
    except ValueError: pass
    if _price_idx is not None:
        _cached_price = features[_price_idx]
        _move = abs(price - _cached_price)
        _thresh = getattr(settings, "FV_CACHE_INVALIDATE_PRICE_MOVE", 0.03)
        if _move >= _thresh:
            _fv_key = f"fv:{market_id}:{token_id}"
            self._feature_vector_cache.delete(_fv_key)
            logger.debug("FV cache invalidated (price move %.3f): market=%s", _move, market_id)
            features = await self._extract_features(market_id, price, user_address)
        else:
            features[_price_idx] = price
```
Add to settings: `FV_CACHE_INVALIDATE_PRICE_MOVE: float = float(os.getenv("FV_CACHE_INVALIDATE_PRICE_MOVE", "0.03"))`

---

### PHASE 7 — Signal Feature Train-Serve Mismatch

**Fix 7.1**
FIRST (before code deploy): Run on VPS postgres:
```sql
CREATE INDEX IF NOT EXISTS idx_trade_signals_market_created
ON trade_signals(market_id, created_at DESC);
```

Add to `prediction_engine.py:__init__()` (~line 350):
```python
self._signal_cache: _BoundedCache = _BoundedCache(max_size=500, default_ttl=30.0)
# Remove any existing plain dict assignment for _signal_cache
```

Replace dead code block (lines 2837-2853) with:
```python
if getattr(settings, "USE_SIGNAL_FEATURES_IN_ML", True) and "signal_confidence" in (self.feature_columns or []):
    sig_conf, sig_dir_enc = 0.5, 0.5
    _sig_ck = f"sig:{market_id}"
    _sig_cached = self._signal_cache.get(_sig_ck)
    if _sig_cached is not None:
        sig_conf, sig_dir_enc = _sig_cached
    elif self.db and getattr(self.db, "session_factory", None):
        try:
            async with self.db.get_session() as _sig_sess:
                _sig_res = await _sig_sess.execute(text("""
                    SELECT signal_confidence, signal_direction FROM trade_signals
                    WHERE market_id = :mid AND created_at >= NOW() - INTERVAL '30 minutes'
                    ORDER BY created_at DESC LIMIT 1
                """), {"mid": str(market_id)})
                _sig_row = _sig_res.fetchone()
            if _sig_row:
                sig_conf = float(_sig_row[0] or 0.5)
                _raw_dir = str(_sig_row[1] or "")
                sig_dir_enc = 1.0 if _raw_dir == "YES" else (0.0 if _raw_dir == "NO" else 0.5)
        except Exception as _sig_err:
            logger.debug("Signal feature lookup failed: %s", _sig_err)
    self._signal_cache.set(_sig_ck, (sig_conf, sig_dir_enc), ttl=30.0)
    features.extend([sig_conf, sig_dir_enc])
    feature_names.extend(["signal_confidence", "signal_direction_encoded"])
```

---

### PHASE 8 — Kalshi/PredictIt Cross-Platform Lead Signals

File: `base_engine/signals/signal_ingestion.py`
Add new collection loop following existing pattern:
```python
async def _fetch_kalshi_signals(self) -> None:
    """Poll Kalshi public API every 10s for cross-platform lead signals."""
    KALSHI_API = "https://trading-api.kalshi.com/trade-api/v2/markets"
    PREDICTIT_API = "https://www.predictit.org/api/marketdata/all/"
    _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))
    while True:
        try:
            async with _session.get(KALSHI_API, params={"limit": 50, "status": "open"}) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    for market in data.get("markets", []):
                        await self._process_kalshi_market(market)
        except Exception as _e:
            logger.debug("Kalshi signal fetch failed: %s", _e)
        await asyncio.sleep(10)
```
`_process_kalshi_market()`: Compute YES price delta since last poll. If delta > CROSS_PLATFORM_SIGNAL_THRESHOLD (0.03), publish signal with `signal_source="kalshi"`, `signal_confidence=min(1.0, abs(delta)/0.10)`. Map to Polymarket market via `ILIKE` on question column; cache match in Redis 24h.
Gotcha: HTTP 429 → exponential backoff. PredictIt 2-3s response → separate session.

Add to `start()`: `asyncio.create_task(self._fetch_kalshi_signals())`
Add settings: `CROSS_PLATFORM_SIGNAL_THRESHOLD=0.03`, `KALSHI_ENABLED=true`, `PREDICTIT_ENABLED=true`
NOTE: Phase 7 must be deployed first (signals flow through Phase 7's DB lookup automatically).

**Verify**: `SELECT signal_source, COUNT(*) FROM trade_signals WHERE created_at > NOW() - INTERVAL '1 hour' GROUP BY signal_source;` → shows kalshi/predictit rows.

---

### PHASE 9 — Volatility-Scaled Sizing + ATR Stops

**Fix 9.1 — Vol-scaled position sizing**
File: `base_engine/risk/risk_manager.py` after calibration penalty (~line 442), before edge cap:
```python
_market_vol = float(kwargs.get("market_vol", 0.0) or 0.0)  # regime_vol from prediction
if _market_vol > 0.0:
    _vol_divisor = max(1.0, 1.0 + _market_vol * getattr(settings, "VOL_SCALE_FACTOR", 2.0))
    adjusted_size /= _vol_divisor
    logger.debug("Vol-scaled: vol=%.3f divisor=%.2f size=%.2f", _market_vol, _vol_divisor, adjusted_size)
```
Pass `market_vol=regime_vol` from prediction result to `place_order()` in ensemble_bot.py and momentum_bot.py.
Add settings: `VOL_SCALE_FACTOR: float = 2.0` (at vol=0.5: halved; at vol=0.1: 83% of normal)

**Fix 9.2 — MomentumBot ATR-scaled stops**
File: `bots/momentum_bot.py` `_check_exits()` ~line 193:
```python
_atr = float(position.get("atr_at_entry", 0.0) or 0.0)
_atr_scale = getattr(settings, "MOMENTUM_ATR_SCALE", 2.0)
if _atr > 0.0:
    _atr_stop_add = min(0.10, _atr * _atr_scale)
    effective_stop = default_stop * lr + _atr_stop_add
    effective_take = default_take * lr + _atr_stop_add
```
Store `atr_at_entry` in position dict when opening: `position["atr_at_entry"] = ta_atr_normalized` from prediction feature vector.
Add settings: `MOMENTUM_ATR_SCALE: float = 2.0`

---

### PHASE 10 — Drawdown-Dependent Kelly + 2h Retrain

**Fix 10.1 — Drawdown Kelly compression**
File: `base_engine/risk/risk_manager.py` `_kelly_position_size()` after computing `kelly_frac`:
```python
try:
    _dd_ctrl = getattr(self, "_drawdown_controller", None)
    if _dd_ctrl:
        _status = await _dd_ctrl.check_drawdown_status(_portfolio_snapshot)
        _dd_pct = abs(_status.get("current_drawdown_pct", 0.0))
        if _dd_pct > 0.02:
            _compress = max(0.30, 1.0 - _dd_pct * 4.0)
            kelly_frac *= _compress
            logger.debug("Drawdown Kelly compression: dd=%.1f%% compress=%.2f×", _dd_pct*100, _compress)
except Exception:
    pass
```
Wire `_drawdown_controller` to RiskManager at instantiation in `base_engine.py` (PortfolioDrawdownController already initialized there).

**Fix 10.2 — 2h retrain**
File: `config/settings.py` line 167 OR `.env`: `RETRAIN_INTERVAL_HOURS=2`
Deploy immediately via .env — no code deploy needed.

---

### PHASE 11 — ArbitrageBot WebSocket Paired-Token Arb

File: `bots/arbitrage_bot.py` `on_price_update()` ~line 214 (after price-change threshold check)
Add `_ws_token_timestamps: dict = {}` in `__init__()` (parallel to `_ws_token_prices`).
Update `_ws_token_prices[token_id] = price` to also set `_ws_token_timestamps[token_id] = time.time()`.

```python
_market_data = self.base_engine.get_market_from_index(market_id)
if _market_data:
    _yes_tid = (_market_data.get("yes_token_id") or "").strip()
    _no_tid = (_market_data.get("no_token_id") or "").strip()
    if (_yes_tid and _no_tid
            and _yes_tid in self._ws_token_prices and _no_tid in self._ws_token_prices):
        # Staleness check: both prices must be recent
        _now_t = time.time()
        _max_age = getattr(settings, "ARB_MAX_PRICE_AGE_SECONDS", 5)
        if (_now_t - self._ws_token_timestamps.get(_yes_tid, 0) <= _max_age and
                _now_t - self._ws_token_timestamps.get(_no_tid, 0) <= _max_age):
            _yes_p = self._ws_token_prices[_yes_tid]
            _no_p = self._ws_token_prices[_no_tid]
            _arb_margin = 1.0 - (_yes_p + _no_p)
            _min_profit = getattr(settings, "ARB_WS_MIN_PROFIT_MARGIN", 0.02)
            if _arb_margin > _min_profit:
                logger.info("ArbitrageBot: paired-token arb detected market=%s YES=%.4f NO=%.4f margin=%.4f",
                            market_id, _yes_p, _no_p, _arb_margin)
                _patched = dict(_market_data)
                _patched["yes_price"] = _yes_p
                _patched["no_price"] = _no_p
                asyncio.create_task(self._execute_ws_arb(_patched, _yes_p, _no_p, _arb_margin))
```
Add `_execute_ws_arb()`: wraps existing `analyze_opportunity()` + `ArbitrageTransactionCoordinator.execute_long_arbitrage()`.
Add settings: `ARB_WS_MIN_PROFIT_MARGIN=0.02`, `ARB_MAX_PRICE_AGE_SECONDS=5`

---

## 16. DEPLOYMENT ORDER (ALL 11 PHASES)

```
Phase 1 → Phase 2 → Phase 3 → Phase 4 → Phase 5 → Phase 6 → Phase 7
                                                               ↓
                                         Phase 8 (can parallel with 6-7)
                                                               ↓
                                         Phase 9 → Phase 10 → Phase 11
```

**Run after EACH phase**: `python -m pytest tests/unit/ -v --no-cov --tb=short` (must be 321/321)

---

## 17. TESTS

```bash
# Run all tests (must pass before any VPS deploy)
cd C:\lockes-picks\polymarket-ai-v2
python -m pytest tests/unit/ -v --no-cov --tb=short

# Key test files to update for Phase 1:
tests/unit/test_model_diversity.py       # Assert cyclical feature names in feature_columns
tests/unit/test_risk_manager_import.py  # Assert Kelly fires at Brier=0.42 with threshold=0.45
```

**Mock patterns**:
- `db.get_session()` is SYNC returning async ctx mgr: `MagicMock(return_value=MockSessionCtx())`
- `db._verify_database()` is async: `AsyncMock()`

---

## 18. KEY SETTINGS (config/settings.py ~610 lines)

```python
# Currently deployed on VPS
SCAN_MARKET_LIMIT=50
ENSEMBLE_MIN_CONFIDENCE=0.40
MIN_CONFIDENCE_THRESHOLD=0.40
BOT_SCAN_TIMEOUT_SECONDS=300
DB_POOL_SIZE=40
DB_MAX_OVERFLOW=5
PATH_SUMMARY_MAX_ROWS=50000       # Batch 4 fix — was 5000
INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS=600
STALE_RESERVATION_MINUTES=5       # Batch 4 fix — was 2

# To change via .env (no code deploy):
KELLY_MAX_BRIER=0.45              # Phase 2 (was 0.20)
USE_CAPITAL_ALLOCATOR=true        # Phase 2 (was false)
MIRROR_MIN_CONSENSUS=3            # Phase 4 (was 2)
CACHE_TTL_PREDICTIONS=60          # Phase 1 (was 300)
RETRAIN_INTERVAL_HOURS=2          # Phase 10 (was 6)
```

---

## 19. ESTIMATED P&L IMPACT (12-Month Horizon, $100K)

| Phase | Estimated Annual Impact |
|-------|------------------------|
| 1 | Brier 0.424→~0.33 → unlock Kelly, +6-10% EnsembleBot win rate |
| 2 | Kelly + edge caps → +$2,000-$4,000 |
| 3 | Whale pipeline → +$800-$2,000 |
| 4 | MirrorBot structural → +$1,500-$3,000 |
| 5 | Dollar-weighted elite → Brier→~0.30, +2-3% win rate |
| 6 | Feature cache coherence → -$500-$1,000 loss avoidance |
| 7 | Signal parity → removes calibration error |
| 8 | Kalshi/PredictIt → +$1,500-$3,000 |
| 9 | Vol sizing + ATR stops → +$1,000-$2,000 |
| 10 | Drawdown Kelly + 2h retrain → -$500-$1,000 loss avoidance |
| 11 | ArbitrageBot WS arb → +$500-$1,500 |
| **Total** | **+$9,300-$18,500 net annually** |

---

## 20. VISION

Build a self-improving prediction market trading system that:
1. Starts with ML-driven probability predictions (current: 10-model ensemble)
2. Incorporates social signals, on-chain whale detection, cross-platform lead signals
3. Applies rigorous Kelly-based position sizing proportional to edge
4. Self-heals from degradation, auto-retrains on drift, adjusts sizing during drawdowns
5. Achieves Brier < 0.30 and consistent positive alpha across all 4 bot strategies
6. Scales to live trading once paper trading demonstrates >6 months positive P&L

**Current Brier trajectory**: 0.424 (broken features) → 0.33 (Phase 1) → 0.30 (Phase 5) → sub-0.28 (Phases 7-8 signals)

---

*This document is the complete context for Session 17. Begin by implementing Phase 1.*
