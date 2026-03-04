# Polymarket AI V2 — Project Status
**Last updated: 2026-02-21**

Single authoritative source of truth for current system state, what is complete, what is next, and active blockers. Do not trust IMPLEMENTATION_PLAN_STATUS.md (stale, dated 2025-02-06). Do not trust HANDOFF_INDEX.md line 20 (previously said "ALL TIERS COMPLETE" — incorrect, now corrected).

---

## 1. System State

| Property | Value |
|----------|-------|
| Mode | **Paper trading** (SIMULATION_MODE=true, LIVE_TRADING=false) |
| Capital | $100,000 simulated |
| Active bots | 4 (Ensemble, Arbitrage, Momentum, Mirror) |
| ML models | 10 (RF, XGB, GB, LR, ET, HGB, LightGBM, CatBoost, Ridge, KNN) |
| Unit tests | **321 passing, 0 warnings** |
| WS tokens | ~344 subscribed |
| DB | Supabase Pro, 579 MB/8 GB, session pooler port 5432 |
| First trades | Executed 2026-02-18 |

**Pipeline:** WebSocket/API → Bot scan → ML predict (10-model ensemble) → Confidence filter → Risk check → DrawdownController → KillSwitch → OrderGateway → PaperTradingEngine → DB

---

## 2. What Is Complete

### Step 0 Prerequisites (5/5)
1. ✅ vaderSentiment added to requirements.txt + installed
2. ✅ `prediction_timestamp` added to `predict()` return dict
3. ✅ AlertingSystem wired into bot scan loop (3+ consecutive failure alerts)
4. ✅ Maker/taker fee simulation in PaperTradingEngine (TAKER_FEE_BPS=1.5%, MAKER=0%)
5. ✅ Migration 018: `feature_snapshot JSONB` column on prediction_log (ORM + SQL)

### Tier 1 Implementation (9/9)
| # | Item | File(s) |
|---|------|---------|
| 1 | Longshot bias filter | `ensemble_bot.py:529-541` |
| 3 | Alpha decay on signal freshness | `ensemble_bot.py:468-479` |
| 4 | DDM + EDDM drift detection | `calibration_tracker.py:15-106` |
| 5 | ADWIN formalization | `prediction_engine.py:76-93` |
| 7 | Maker-taker fee preference | `paper_trading.py:165-174`, `order_gateway.py:395-405` |
| 8 | Adverse selection gate | `order_gateway.py:358-372` |
| 9 | Square-root market impact | `market_impact.py:80-139` |
| 10 | Prompt caching (Anthropic) | `llm_probability.py:138-155` |
| 11 | Auto-alerts (Brier/Sharpe/drift) | `alerting.py:387-432` |

### Audit Phase Fixes (all sessions 2026-02-17 through 2026-02-20)
- **Side semantics** (critical): YES/NO = BUY (buying that token), SELL = close. Was systemically wrong.
- **CLOB API**: `token` → `token_id` param fix
- **Millisecond scan architecture**: Dual-key market index, feature vector cache (120s TTL), 800-market scan (was 300), concurrency=10 (was 3), cold-start guard, position guards in ArbitrageBot, 60s true cycle timing
- **WS reactive path**: Was silently failing (condition_id vs numeric id mismatch) — now works
- **PaperTrading**: Idempotent seed, side preservation, P&L formula fixed
- **StreamingPersister**: Prices now normalized to numeric market_id
- **DrawdownController + MultiLayerKillSwitch**: Wired into order pipeline
- **CorrelationRiskManager CVaR**: $200 cap gate active
- **MirrorBot**: Elite fetches parallelized (7s→1-2s)
- **Model cache**: `feature_columns` + `best_feature_names` now saved (was causing zero predictions after restart)
- **Test suite**: 321 tests, 0 warnings (was 17 warnings); structlog config, MLP convergence, LightGBM suppression
- **Bug A fix (2026-02-21)**: Closed-market guard added to EnsembleBot — skips markets with `active=False`, `closed=True`, or `end_date_iso` in the past. Prevents trades on resolved markets (was buying Super Bowl market after it closed).
- **Bug B fix (2026-02-21)**: Token-level order dedup in `_execute_ensemble_trade` — `_pending_orders` set prevents concurrent parallel scan from submitting duplicate orders for same `(market_id, token_id)`.
- **Bug C fix (2026-02-21)**: SELL size=0 guard in `order_gateway.py` — returns early with error if SELL size ≤ 0 instead of creating phantom trade records.
- **Learning mode (2026-02-21)**: Lowered `ENSEMBLE_MIN_CONFIDENCE` 0.65→0.45, `RISK_MAX_POSITION_SIZE_USD` 1000→100, `RISK_MAX_POSITIONS_COUNT` 50→100, `SCAN_MARKET_LIMIT` 800→1500. More distinct market exposures = faster resolved-outcome feedback loop.

### Tier 2 Implementation (5/12 complete, 2026-02-20)
| # | Item | File(s) |
|---|------|---------|
| 13 | Correlation IDs across scan→predict→order | `database.py` (ORM cols), `base_bot.py`, `prediction_engine.py`, `order_gateway.py`, `paper_trading.py`, `base_engine.py`, `ensemble_bot.py` |
| 14 | VADER sentiment scoring | `sentiment_analyzer.py:178-315`, `signal_ingestion.py:334,426,876,905,934` (already complete, was pre-existing) |
| 15 | Wikipedia Pageviews signal | `wikipedia_pageviews.py:22-112`, `signal_ingestion.py:87,543-589` (already complete, was pre-existing) |
| 21 | PSI feature drift detection | `prediction_engine.py` (baselines in cache + periodic check), `config/settings.py` (PSI_CHECK_INTERVAL, PSI_DRIFT_THRESHOLD) |
| 22 | Capital canary auto-transition | `scheduler.py` (_canary_auto_transition), `config/settings.py` (CANARY_AUTO_ADVANCE, CANARY_MIN_TRADES_PER_STAGE) |
| 23 | Latency path instrumentation | `base_bot.py` (_LatencyTracker class + scan loop), `ensemble_bot.py` (stage marks) |

---

## 3. What Is Next — Tier 2 (7 remaining items)

| # | Item | Effort | Blocker |
|---|------|--------|---------|
| 12 | `can_exit()` NegRisk pre-check | ~40 lines | **Migration 017** (neg_risk column) |
| 16 | LLM resolution clarity scoring | ~50 lines | None |
| 17 | ~~Disposition effect exploitation (MomentumBot — DELETED)~~ | — | — |
| 18 | VPIN toxicity detection | ~100 lines | None |
| 19 | Bot classification via wallet clustering | ~80 lines | None |
| 20 | Order flow fingerprinting | ~80 lines | None |

**Recommended next**: Items 16→17→18 (no blockers, moderate effort).

---

## 4. Active Blockers

### Migration 017 + 018 (Supabase Pooler)
- **017**: Adds `neg_risk`, `outcome_count`, `outcomes` columns to `markets` table
- **018**: Adds `feature_snapshot JSONB` to `prediction_log` (ORM done, SQL migration not applied)
- **Cause**: Supabase session pooler (port 5432) drops DDL statements mid-execution on long migrations
- **Workaround**: Apply via `psql` direct connection (not through pooler) OR wait for VPS migration
- **Impact**: Item #12 (can_exit NegRisk) blocked. Everything else proceeds.

### Lost 47-Item Master Plan File
- `C:\Users\samwa\.claude\plans\shimmying-greeting-kay.md` was **overwritten** with the warning-fix session plan
- The original 47-item content is **fully reconstructible** from HANDOFF_2026_02_20.md (contains Tier 2 table) + this file
- Tiers 3-5 were never started — no items lost that weren't already tracked

---

## 5. Key Goals (Vision)

```
Paper trading → validate strategy → apply migrations → Tier 2 features
→ latency <15ms → VPS deploy (Dublin, ~2ms to London CLOB)
→ live trading with real capital
```

### Latency Target
Current: WS event → trade decision ~100ms-25s (DB-bound)
Target: <15ms (in-memory market index ✅ done, position tracker in-memory ✅ done, fire-and-forget log writes pending)

### VPS Target
AWS Lightsail Dublin → ~2-5ms to Polymarket CLOB (London, eu-west-2)
UK is RESTRICTED — Dublin (eu-west-1) is closest allowed location

---

## 6. Pipeline Flow (Canonical)

```
Polymarket CLOB/WS
  └─ WebSocketManager (streaming_persister → market_prices DB)
       └─ EventBus → on_price_update()
            ├─ EnsembleBot: get_market_from_index() [O(1)] → predict() [feature cache] → analyze_opportunity()
            ├─ ArbitrageBot: WS price cache → binary arb screen → analyze_opportunity()
            └─ (MomentumBot removed — Session 44)

Bot scan_and_trade() [every 10-60s]:
  └─ get_all_tradeable_markets() [800 markets, LIMIT 800]
       └─ filter open positions [O(1) in-memory set]
            └─ prefetch_markets() [batch DB load]
                 └─ parallel analyze_opportunity() [concurrency=10, ~400ms for 800 markets]
                      └─ predict() [feature cache hit → scaler → 10 models → ensemble → <5ms]
                           └─ confidence filter [ENSEMBLE_MIN_CONFIDENCE=0.45 (learning mode)]
                                └─ RiskManager [position limits, CVaR gate, edge filter]
                                     └─ DrawdownController [caution/restricted/halted]
                                          └─ MultiLayerKillSwitch [emergency halt]
                                               └─ OrderGateway [adverse selection gate]
                                                    └─ PaperTradingEngine [fee sim, fill sim]
                                                         └─ DB persist [paper_trades, positions]
```

---

## 7. Critical Concepts for Next Agent

### Polymarket Side Semantics (NEVER GET THIS WRONG)
- **YES and NO are both BUY** — you are buying that outcome token
- **SELL only closes** an existing position
- P&L = `(current_price - entry_price) / entry_price` regardless of YES or NO
- Signals return YES/NO (which token). Gateway normalizes to BUY/SELL for order routing.

### Market ID Formats
- **Numeric ID** (`628113`): DB `markets.id`, used in `market_prices`, in model cache keys `fv:{id}:{token_id}`
- **Condition ID** (`0x339d...`): Polymarket WebSocket `"market"` field, used in `trades` (via API)
- Always JOIN with: `(mp.market_id = m.id OR mp.market_id = m.condition_id)`

### Model Cache
- File: `data/model_cache.pkl`
- Must contain: `feature_columns`, `best_feature_names` (checked on load — rejects if missing)
- Delete to force retrain: `del data\model_cache.pkl` → next `python main.py` retrains (~2-3 min)

### DB Connection
- Session pooler **port 5432** (NOT 6543 — transaction pooler breaks SSL)
- Auto-detected: `"pooler.supabase.com" in url` → `statement_cache_size=0`
- Pool: `pool_size=10, max_overflow=0, pool_timeout=30`
- `DATABASE_URL` in `.env` only — never in code or memory files

---

## 8. Documentation Map

| File | Trust Level | Purpose |
|------|------------|---------|
| **PROJECT_STATUS.md** (this file) | ✅ Current | Single source of truth for status |
| **NEW_AGENT_SUMMARY.md** | ✅ Current | Architecture, critical concepts, commands, gotchas |
| **HANDOFF_2026_02_20.md** | ✅ Current | Latest session detail, Tier 2 full list, Step 0 + Tier 1 code locations |
| **HANDOFF_2026_02_19.md** | ✅ Historical | Migration 017 issue, training feedback loop |
| **MEMORY.md** (`~/.claude/projects/.../memory/`) | ✅ Current | Agent persistent memory — mock patterns, DB facts, bug history |
| **HANDOFF_INDEX.md** | ✅ Current | Navigation index |
| `archive/` | 📦 Archived | Legacy docs, one-time scripts, superseded diagnostics |
| `IMPLEMENTATION_PLAN_STATUS.md` | ❌ Stale | Dated 2025-02-06, DO NOT USE |

---

## 9. Test Quick-Reference

```powershell
# Full suite (must always be 321 passed, 0 warnings)
powershell.exe -Command "cd 'C:\lockes-picks\polymarket-ai-v2'; python -m pytest tests/unit/ -v --no-cov --tb=short"

# Verification tests (run after any data/prediction code changes)
python -m pytest tests/unit/test_poly_data_fixes.py tests/unit/test_prediction_price_fallback.py tests/unit/test_ingestion_historical_price_flow.py -v --no-cov

# Single file
python -m pytest tests/unit/test_model_diversity.py -v --no-cov
```
