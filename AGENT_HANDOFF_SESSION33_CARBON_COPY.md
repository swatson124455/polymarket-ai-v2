# CARBON-COPY AGENT HANDOFF — Session 33 → Session 34
**Created**: 2026-03-01 (Session 33 end)
**Purpose**: Full context for a new agent. Contains everything needed to continue seamlessly.
**Primary canonical doc**: `AGENT_HANDOFF_COMPLETE_2026_02_27.md` (full reference)
**Tests**: 750/750 passing locally. VPS: Sessions 1–32 deployed. **Session 33 deploy PENDING.**

---

## 1. WHAT THIS SYSTEM IS

A fully automated **paper-trading** Polymarket prediction bot system:
- Scans **Polymarket** binary prediction markets (https://polymarket.com)
- Uses an **11-model ML ensemble** to predict resolution probabilities
- Places **paper trades** ($100K virtual capital, SIMULATION_MODE=true)
- Tracks P&L, positions, model performance in **PostgreSQL on VPS** (local, NOT Supabase)
- Self-heals via FSM state machine, circuit breakers, kill switch, drawdown breaker
- Goal: Prove edge in paper → graduate to learning → graduated → real money phases

### Goal State Right Now
The system is in **Paper phase**. P&L: EnsembleBot -$1,606 (831 BUY, 763 SELL paper trades).
We need: 50 resolved predictions + Brier < 0.30 + win rate > 48% to advance to Learning phase.
Current blocker: most markets have NULL `end_date_iso` → resolution backfill can't find them.
Session 33 fixed the root cause (endDateIso field name). Bulk backfill running / pending deploy.

---

## 2. SYSTEM ARCHITECTURE

```
main.py (~400 lines)
├── BaseEngine (base_engine/base_engine.py ~3400 lines)
│   ├── PredictionEngine (prediction/prediction_engine.py ~2700 lines)
│   │   ├── 11 ML models: RF, XGB, GradBoost, ExtraTrees, HistGradBoost, LightGBM,
│   │   │                 CatBoost, LogReg, Ridge, KNN, MLP  (from data/model_cache.pkl)
│   │   ├── predict() — FV cache → ensemble → calibration → extremization (factor=1.4)
│   │   │   All 3 prediction_log writes gated on self._feature_cache_warmed
│   │   ├── _pred_ts + _fv_hash in feature_snapshot (S33: temporal integrity)
│   │   └── batch_precompute_all_features() — background DB fill for FV cache
│   ├── RiskManager (risk/risk_manager.py ~530 lines) — UNIVERSAL GATES (ALL 9 trading bots):
│   │   ├── Confidence gate (line 164)
│   │   ├── Directional edge: `if edge < min_edge` NO abs() (line 189)
│   │   ├── Price bounds 5%–95% (lines 198-202)
│   │   ├── Universal volume gate: _get_market_volume() 1h cache → rejects < $5K
│   │   ├── Position limits, loss limits, kill switch, CVaR tail risk
│   │   ├── Consecutive loss counter: _consecutive_losses[bot_name], gated on MAX_CONSECUTIVE_LOSSES
│   │   └── calculate_position_size() — Quarter-Kelly (KELLY_FRACTION/KELLY_ACTIVE_BOTS = 0.25/10)
│   ├── Database (data/database.py ~3400 lines, 23+ ORM tables)
│   │   ├── bulk_insert_markets() — S33: slug NULL normalization + batch slug dedup
│   │   ├── backfill_prediction_log_resolution() — S33: temporal assertion (resolved_at >= prediction_time)
│   │   └── backfill_prediction_log_from_closed_trades() — S32: pseudo-label fallback from SELL pnl
│   ├── OrderGateway (execution/order_gateway.py) — UNIVERSAL GATES:
│   │   ├── Kill switch, canary, CLOB spread/liquidity (warn-not-block in paper mode)
│   │   └── PaperTradingEngine: $100K cash, B5 epsilon guard
│   ├── AutomatedPositionManager (execution/position_manager.py)
│   │   ├── Stop-loss: 30%, take-profit: 60%, model reversal exits (warm guard)
│   │   └── set_risk_manager() → record_trade_outcome() on exit
│   ├── Data Pipeline
│   │   ├── data_ingestion.py — volume filter ($5K min), _infer_category()
│   │   │   S33: endDateIso fix (all 5 field variants: endDateISO|endDateIso|endDate|end_date|end_date_iso)
│   │   ├── resolution_backfill.py — Phases 1-7 (missing markets, resolution, prediction labels,
│   │   │   pseudo-labels, paper trades, positions, performance tracker, online learning)
│   │   │   S33: endDateIso added to Phase 2 opportunistic patch
│   │   └── ingestion_scheduler.py
│   │       ├── Mini backfill every 30min (S33: prediction_log + pseudo + paper trades)
│   │       └── Periodic health check every 60min (S33: HealthRunner)
│   └── Monitoring
│       ├── health_runner.py (NEW S33) — 10 parallel health checks
│       ├── pipeline_gate.py — S32 added end_date_iso conformance check
│       └── (7+ other monitoring modules)
└── 12 Bots (see table below)
```

---

## 3. BOT REGISTRY (main.py lines 91–105)

| Bot | File | Kelly | VPS State | Notes |
|-----|------|-------|-----------|-------|
| EnsembleBot | bots/ensemble_bot.py | central | **ENABLED** | ML ensemble, edge filter, CLOB spread, progressive cooldown, side-bias 75% |
| ArbitrageBot | bots/arbitrage_bot.py | central | **ENABLED** | NegRisk arb, all 7 paths Kelly-sized |
| MomentumBot | bots/momentum_bot.py | central | **DISABLED** | 0.4% win rate, -$7,164. KEEP DISABLED. |
| MirrorBot | bots/mirror_bot.py | central | enabled | Elite trader mirroring; 0 trades (Gamma API down) |
| CrossPlatformArbBot | bots/cross_platform_arb_bot.py | central | disabled | |
| OracleBot | bots/oracle_bot.py | central | disabled | |
| SportsBot | bots/sports_bot.py | central | **DISABLED** | Needs API Football key |
| LLMForecasterBot | bots/llm_forecaster_bot.py | N/A | disabled | Data collection only, no trades |
| WeatherBot | bots/weather_bot.py | central | disabled | SWOT done, central Kelly, NOAA edge |
| SportsInjuryBot | bots/sports_injury_bot.py | SportsBankrollManager | disabled | |
| SportsLiveBot | bots/sports_live_bot.py | SportsBankrollManager | disabled | |
| SportsArbBot | bots/sports_arb_bot.py | SportsBankrollManager | disabled | |

**CRITICAL ARCHITECTURE MANDATE (user-established)**: ALL base modules enforce universal gates.
`risk_manager.check_risk_limits()` + `order_gateway.place_order()` = universal enforcement.
Individual bots add ONLY their own specific logic on top. NEVER add universal checks bot-by-bot.

---

## 4. WHAT SESSION 33 FIXED (ALL LOCAL — PENDING VPS DEPLOY)

### 4A. DB Pool Exhaustion
- `.env` had DUPLICATE `DB_POOL_SIZE=10` then `DB_POOL_SIZE=12` — python-dotenv first-wins = 13 total
- VPS was hitting 17/17 pool exhaustion constantly
- **Fix**: Removed duplicates. `DB_POOL_SIZE=25, DB_MAX_OVERFLOW=5` → 30 total
- PostgreSQL `/etc/postgresql/16/main/conf.d/polymarket.conf`: `max_connections=40` (was 20)
- `sudo -u postgres psql -c "SELECT pg_reload_conf()"` — applied without restart

### 4B. Slug UniqueViolationError Spam (`database.py`)
```python
# (1) Empty slug → NULL:
"slug": market_data.get("slug") or None

# (2) Batch deduplication (before upsert):
_seen_slugs: set = set()
for _d in reversed(valid_dicts):
    _sl = _d.get("slug")
    if _sl is not None:
        if _sl in _seen_slugs:
            _d["slug"] = None    # Nullify duplicate within batch
        else:
            _seen_slugs.add(_sl)

# (3) ON CONFLICT update SET excludes slug field
#     (prevents stomping another market's slug on id conflict)
```

### 4C. Advisory Lock `idle in transaction` (`database_lock.py`)
- `pg_try_advisory_lock()` starts an implicit transaction that's never committed
- Session-level advisory locks SURVIVE COMMIT — safe to commit immediately
- **Fix**:
```python
r = await session.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id})
await session.commit()   # ← ADDED: close implicit transaction; lock remains held
```
- Also `await session.commit()` after release in the finally block

### 4D. `endDateIso` Field Name — Root Cause of 14k NULL `end_date_iso` Values
```python
# Gamma API returns:  endDateIso  (lowercase 'so')
# CLOB API returns:   endDateISO  (uppercase 'ISO')
# Our DB column:      end_date_iso

# BEFORE (broken — missing Gamma variant):
_end_raw = m.get("endDateISO") or m.get("endDate")

# AFTER (fixed — all 5 variants):
_end_raw = (m.get("endDateISO") or m.get("endDateIso")
            or m.get("endDate") or m.get("end_date")
            or m.get("end_date_iso"))
```
Applied in both `data_ingestion.py` (~line 958) and `resolution_backfill.py` (line 217).

Bulk backfill ran via `nohup python3 - << 'PYEOF' ...` → patched 12,193 markets.
Coverage: 683 → 4,799 → 16,992 markets (from 17,425 total).

### 4E. Stale sync_log Lock Blocking Ingestion
- 600s ingestion timeout left "running" entry in sync_log
- `SYNC_LOG_STALE_HOURS=2.0` meant ingestion was blocked for 2h after every timeout
- **Fix**: `UPDATE sync_log SET status='completed' WHERE status='running'` (cleared manually)
- `.env`: `SYNC_LOG_STALE_HOURS=0.25` (15 min — matches 10min ingestion timeout)

### 4F. New `health_runner.py` (`base_engine/monitoring/health_runner.py`)
```python
class HealthRunner:
    def __init__(self, db, settings): ...
    async def run(self, auto_fix=True) -> HealthReport:
        # 10 parallel checks via asyncio.gather:
        # _check_db_connectivity, _check_idle_in_transaction, _check_pool_exhaustion,
        # _check_markets, _check_prediction_log (label rate > 5%),
        # _check_paper_trades, _check_slug_collisions, _check_ingestion_sync,
        # _check_category_distribution, _check_bot_scan_times
        # + _check_resolution_backfill (sequential)
        # Returns HealthReport(issues=[{severity, message, source}])
```
Called from `ingestion_scheduler.py` every 60min. Critical issues surfaced as alerts.

### 4G. Mini Backfill in `ingestion_scheduler.py`
```python
# Every 30min (MINI_BACKFILL_INTERVAL_MINUTES), regardless of daily ingestion:
pred_updated = await db.backfill_prediction_log_resolution()
pseudo_updated = await db.backfill_prediction_log_from_closed_trades()
paper_updated = await db.backfill_paper_trades_resolution()
```
Ensures labels flow to the model as markets resolve. No longer waiting 24h for daily cycle.

### 4H. Feature Integrity in `prediction_engine.py`
```python
# At prediction time, embed temporal proof:
_pred_ts_iso = datetime.now(timezone.utc).isoformat()
_feat_snap["_pred_ts"] = _pred_ts_iso
_fv_str = _json.dumps({k: v for k, v in _feat_snap.items() if not k.startswith("_")}, sort_keys=True)
_feat_snap["_fv_hash"] = hashlib.sha256(_fv_str.encode()).hexdigest()[:16]
```
`backfill_prediction_log_resolution()` temporal assertion: `resolved_at >= prediction_time`.
Prevents data leakage where resolution timestamp predates the prediction.

### 4I. `redis_manager.py` Shim Completed
Added: `connect()`, `close()`, `hset()`, `hget()`, `hgetall()`, `zadd()`, `zrange()`.
Previously these methods didn't exist on the shim, causing 3 test errors.

### 4J. `pytest.ini` ResourceWarning Filters
```ini
ignore:.*unclosed.*socket.*:ResourceWarning
ignore:.*unclosed transport.*:ResourceWarning
ignore:.*unclosed connection.*asyncpg.*:ResourceWarning
ignore:.*unclosed.*_ProactorSocketTransport.*:ResourceWarning
```
These are asyncpg test teardown artifacts on Windows (per-test event loop closes before asyncpg GC).
Tests pass correctly; warnings were cosmetic noise.

---

## 5. VPS DEPLOY — SESSION 33 PENDING

**VPS IP**: 34.248.60.104 (AWS Lightsail eu-west-1)
**SSH key**: `C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem`
**App dir**: `/opt/polymarket-ai-v2/` (root-owned, sudo required for writes)
**Service**: `polymarket-ai.service`

### Deploy commands (PowerShell):
```powershell
$key = "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem"
$vps = "ubuntu@34.248.60.104"
$local = "C:\lockes-picks\polymarket-ai-v2"
$remote = "/opt/polymarket-ai-v2"

# Step 1: Copy files
scp -i $key "$local\base_engine\data\database.py" "${vps}:/tmp/database.py"
scp -i $key "$local\base_engine\data\database_lock.py" "${vps}:/tmp/database_lock.py"
scp -i $key "$local\base_engine\data\data_ingestion.py" "${vps}:/tmp/data_ingestion.py"
scp -i $key "$local\base_engine\data\resolution_backfill.py" "${vps}:/tmp/resolution_backfill.py"
scp -i $key "$local\base_engine\data\ingestion_scheduler.py" "${vps}:/tmp/ingestion_scheduler.py"
scp -i $key "$local\base_engine\prediction\prediction_engine.py" "${vps}:/tmp/prediction_engine.py"
scp -i $key "$local\base_engine\monitoring\health_runner.py" "${vps}:/tmp/health_runner.py"
scp -i $key "$local\base_engine\cache\redis_manager.py" "${vps}:/tmp/redis_manager.py"
scp -i $key "$local\config\settings.py" "${vps}:/tmp/settings.py"

# Step 2: Place files + restart
ssh -i $key $vps "sudo cp /tmp/database.py $remote/base_engine/data/database.py && sudo cp /tmp/database_lock.py $remote/base_engine/data/database_lock.py && sudo cp /tmp/data_ingestion.py $remote/base_engine/data/data_ingestion.py && sudo cp /tmp/resolution_backfill.py $remote/base_engine/data/resolution_backfill.py && sudo cp /tmp/ingestion_scheduler.py $remote/base_engine/data/ingestion_scheduler.py && sudo cp /tmp/prediction_engine.py $remote/base_engine/prediction/prediction_engine.py && sudo cp /tmp/health_runner.py $remote/base_engine/monitoring/health_runner.py && sudo cp /tmp/redis_manager.py $remote/base_engine/cache/redis_manager.py && sudo cp /tmp/settings.py $remote/config/settings.py"

# Step 3: Update .env (remove duplicate DB_POOL_SIZE, add new settings)
ssh -i $key $vps "grep DB_POOL_SIZE /opt/polymarket-ai-v2/.env"  # verify current state
# If there are duplicates, edit manually:
# ssh -i $key $vps "sudo nano /opt/polymarket-ai-v2/.env"
# Set: DB_POOL_SIZE=25, DB_MAX_OVERFLOW=5, SYNC_LOG_STALE_HOURS=0.25
# Add: HEALTH_CHECK_INTERVAL_MINUTES=60, MINI_BACKFILL_INTERVAL_MINUTES=30

# Step 4: Restart + verify
ssh -i $key $vps "sudo systemctl restart polymarket-ai && sleep 5 && sudo systemctl is-active polymarket-ai"
ssh -i $key $vps "sudo tail -30 /opt/polymarket-ai-v2/data/paper_trading.log"
```

---

## 6. CRITICAL MENTAL MODELS

### 6A. YES/NO/BUY/SELL
- YES and NO are both **BUY** — you buy that outcome's token
- SELL = close position only
- Market IDs: numeric `m.id` AND hex `condition_id` (0x339d…). Always JOIN both.

### 6B. VPS Database
- **LOCAL PostgreSQL** (NOT Supabase). DB: `polymarket`, User: `polymarket`
- **TIMESTAMP WITHOUT TIME ZONE** — ALWAYS `.replace(tzinfo=None)` before raw SQL
- `positions` UNIQUE: `(bot_id, market_id, side)`. SELL rows = audit trail. Filter `side != 'SELL'`.
- `paper_trades` schema: `bot_name` column (NOT `bot_id`)
- `positions` schema: `bot_id` column (NOT `bot_name`)
- Connect: `sudo -u polymarket psql -d polymarket`

### 6C. endDateIso Field Variants (S33 CRITICAL)
```python
# ALWAYS check all 5 variants:
_end_raw = (m.get("endDateISO") or m.get("endDateIso")
            or m.get("endDate") or m.get("end_date")
            or m.get("end_date_iso"))
# Gamma API → "endDateIso" (lowercase 'so')
# CLOB API  → "endDateISO" (uppercase 'ISO')
```

### 6D. Slug NULL Normalization (S33)
```python
"slug": market_data.get("slug") or None   # Never ""  — ix_markets_slug UniqueViolation
```

### 6E. Advisory Lock Commit (S33)
```python
# pg_try_advisory_lock starts an implicit transaction. Commit immediately.
# Session-level advisory locks SURVIVE commits — lock remains held.
await session.execute(text("SELECT pg_try_advisory_lock(:id)"), {"id": lock_id})
await session.commit()   # MUST DO THIS or connection goes idle-in-transaction
```

### 6F. Edge Filter (CRITICAL — S31)
```python
edge = confidence - price          # model_prob - market_price
# Category-specific min_edge from ENSEMBLE_CATEGORY_MIN_EDGES
# Price multiplier: >0.80 → 2×, >0.90 → 3×
net_edge = edge - (_spread / 2.0)  # deduct entry cost
if net_edge < _min_edge: return None   # REJECT
```

### 6G. Kelly Sizing (CRITICAL — S29)
```python
fraction = KELLY_FRACTION / KELLY_ACTIVE_BOTS    # 0.25 / 10 = 0.025 per bot
# Full Kelly reduced by: drawdown compression, Brier calibration, vol scaling
position_usd = kelly_frac * available_capital
```

### 6H. _feature_cache_warmed Gate (CRITICAL — S31)
```python
# ALL 3 prediction_log write locations gated:
# prediction_engine.py lines 2421, 2593, 2605
if PREDICTION_LOG_ENABLED and self.db and self._feature_cache_warmed:
    # Write to prediction_log
# self._feature_cache_warmed = False at init (line 370)
# set True in base_engine.py:1540 after precompute
```

### 6I. Extremization Factor (S31)
```python
# prediction_engine.py:2402 — log-odds scaling
_ext_d = 1.4  # EXTREMIZATION_FACTOR
_logit = math.log(_p / (1 - _p))
final_confidence = 1.0 / (1.0 + math.exp(-_ext_d * _logit))
# Effect: 60%→66%, 70%→78%, 80%→87%
```

### 6J. Python Scoping Trap
```python
# DANGER: ANY `from X import name` ANYWHERE in function makes name LOCAL for ENTIRE function
# Never: from datetime import datetime inside async def
# Always: import at module top level
```

### 6K. VPS .env Drift Prevention
- `.env` can have duplicate keys (python-dotenv first-wins)
- ALWAYS grep before editing: `grep DB_POOL_SIZE /opt/polymarket-ai-v2/.env`
- Session 33: found duplicate DB_POOL_SIZE=10 + DB_POOL_SIZE=12 (→ effective 10, not 12)

### 6L. Universal Guardrail Layer (S31 mandate)
```
risk_manager.check_risk_limits() = universal enforcement for ALL 9 trading bots
order_gateway.place_order()      = universal execution gates for ALL 9 trading bots
Individual bots add ONLY their specific logic on top
NEVER add universal checks bot-by-bot
```

---

## 7. KEY FILES (MOST CRITICAL)

```
main.py                                    ← BOT_REGISTRY (12 bots), startup
config/settings.py                         ← ALL env vars + defaults
base_engine/base_engine.py (~3400 lines)   ← engine wiring; _feature_cache_warmed set line 1540
base_engine/prediction/prediction_engine.py (~2700 lines)
    ← predict() extremization at 2402; prediction_log writes at 2421+2593+2605 (all warmed-gated)
    ← _pred_ts + _fv_hash in feature_snapshot (S33)
base_engine/data/database.py (~3400 lines)
    ← 23+ ORM tables; slug NULL normalization (S33); temporal assertion in backfill_prediction_log_resolution (S33)
    ← backfill_prediction_log_from_closed_trades() pseudo-labels (S32)
base_engine/data/database_lock.py          ← advisory lock; await session.commit() after lock (S33)
base_engine/data/data_ingestion.py         ← volume filter; _infer_category(); endDateIso fix (S33)
base_engine/data/resolution_backfill.py    ← Phases 1-7; endDateIso fix (S33); paper_trades in Phase 2
base_engine/data/ingestion_scheduler.py    ← mini backfill 30min + health check 60min (S33)
base_engine/monitoring/health_runner.py    ← NEW S33: HealthRunner(db, settings).run() — 10 checks
base_engine/risk/risk_manager.py (~530)    ← universal gates; Quarter-Kelly; volume gate; consecutive losses (S32)
base_engine/execution/order_gateway.py     ← universal CLOB spread/liquidity (warn-not-block paper mode)
base_engine/execution/position_manager.py  ← model reversal exits; set_risk_manager() (S32)
base_engine/cache/redis_manager.py         ← shim + all methods added (S33)
bots/ensemble_bot.py (~1465 lines)         ← edge filter, select-by-edge, side-bias, progressive cooldown
bots/arbitrage_bot.py (~1260 lines)        ← 7 execution paths, ALL Kelly-sized
pytest.ini                                 ← ResourceWarning filters added (S33)
```

---

## 8. DATABASE SCHEMA (Critical)

```sql
paper_trades (bot_name NOT bot_id, market_id, side='YES'/'NO'/'SELL', size, price, realized_pnl)
positions    (bot_id NOT bot_name, market_id, side, UNIQUE(bot_id, market_id, side))
markets      (id, condition_id, question, category, end_date_iso, resolution, resolved_at)
prediction_log (market_id, predicted_prob, prediction_time, was_correct, feature_snapshot)
    -- was_correct: set by backfill_prediction_log_resolution() when market resolves
    -- Only written when _feature_cache_warmed=True
    -- feature_snapshot: JSON with _pred_ts and _fv_hash (S33 integrity fields)
```

---

## 9. ENVIRONMENT

### VPS
```
IP: 34.248.60.104 (AWS Lightsail eu-west-1)
SSH: C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem  (NOT polymarket_vps)
App: /opt/polymarket-ai-v2/ (root-owned, sudo for writes)
Python: /opt/polymarket-ai-v2/venv/bin/python (Python 3.13)
Service: polymarket-ai.service
Log: /opt/polymarket-ai-v2/data/paper_trading.log
DB: PostgreSQL local, user=polymarket, db=polymarket
PostgreSQL config override: /etc/postgresql/16/main/conf.d/polymarket.conf (max_connections=40)
```

### VPS .env (Current State — Session 33 values pending deploy)
```env
DB_POOL_SIZE=25                      # S33 (was 12 from duplicates)
DB_MAX_OVERFLOW=5                    # S32
SYNC_LOG_STALE_HOURS=0.25           # S33 (was 2.0)
HEALTH_CHECK_INTERVAL_MINUTES=60     # S33 NEW
MINI_BACKFILL_INTERVAL_MINUTES=30    # S33 NEW
ENSEMBLE_MIN_EDGE=0.10               # S31
ENSEMBLE_CATEGORY_MIN_EDGES={"weather":0.08,"crypto":0.12,"sports":0.10,"politics":0.10,"science":0.10,"finance":0.10,"geopolitical":0.12,"entertainment":0.10}
EXTREMIZATION_FACTOR=1.4             # S31
MIN_MARKET_VOLUME=5000               # S31
ENSEMBLE_MIN_MARKET_VOLUME_USD=5000  # S31
KELLY_ACTIVE_BOTS=10                 # S29
ENSEMBLE_SIDE_BIAS_THRESHOLD=0.65    # S32
MAX_CONSECUTIVE_LOSSES=3             # S32
DAILY_LOSS_LIMIT_PCT=0.02            # S32
MIN_RESOLVED_FOR_RETRAIN=20          # S31
SIMULATION_MODE=true
TOTAL_CAPITAL=100000.0
BOT_ENABLED_ARBITRAGE=true
BOT_ENABLED_MOMENTUM=false
INGESTION_TIMEOUT_SECONDS=600
```

### Local Dev (Windows)
```
Dir: C:\lockes-picks\polymarket-ai-v2
Python: 3.13 system (no venv)
VPN: Surfshark ON required (US IPs get 403 from Polymarket API)
Tests: python -m pytest tests/unit/ tests/test_bots.py -q --no-cov
       Expected: 750 passed, 0 failed
```

---

## 10. PRIORITY NEXT STEPS

### IMMEDIATE (Do First)
1. **Deploy Session 33 to VPS** (see §5 for exact commands)
   - 9 files + .env update + service restart
   - Verify: no slug UniqueViolations, no idle-in-transaction, pool < 30 active conns

### After Deploy
2. Monitor `end_date_iso` coverage: `SELECT COUNT(*) FROM markets WHERE end_date_iso IS NOT NULL`
   Target: should reach ~16,000+ after mini backfill runs
3. Monitor prediction labels: `SELECT COUNT(*) FROM prediction_log WHERE was_correct IS NOT NULL`
   Need 50+ for graduation target
4. Check health runner output: `grep "health check" /opt/polymarket-ai-v2/data/paper_trading.log`
5. Confirm mini backfill running: `grep "Mini backfill" /opt/polymarket-ai-v2/data/paper_trading.log`

### Short-Term
6. Enable WeatherBot (SWOT done, Kelly wired, NOAA edge available)
7. Enable CrossPlatformArbBot (central Kelly, needs validation)
8. Track win rate toward 52% (Paper phase exit criteria)

### Deferred (Pre-Live-Money Only)
- Phase-based USD bet caps ($15/$20/$200)
- Category-specific Kelly fractions
- Platt scaling (needs 200+ resolved)
- Full graduation/demotion tracking
- Politics exit strategy (sell at 60-70% edge capture)
- Dynamic KELLY_ACTIVE_BOTS

---

## 11. GUARDRAIL PHASES (From guardrail_settings.csv)

**Current phase: Paper → transitioning to Learning**

| Metric | Paper | Learning (target) | Graduated | Production |
|--------|-------|-------------------|-----------|------------|
| Min resolved | 50 | 200 | 500 | — |
| Brier score | <0.30 | <0.22 | <0.20 | <0.18 |
| Win rate | >48% | >52% | >54% | >56% |
| Kelly fraction | 0.10x | 0.40x | 0.60x | 1.00x |
| Max bet USD | $15 | $20 | $200 | unlimited |
| Min edge (base) | 10% | 10% | 10% | 10% |
| Extremization | 1.4 | 1.4 | 1.4 | 1.4 |

**Current actual**: 2,322 labeled / 10,692 total (22% label rate), 120 correct (5.2% win rate so far).
EnsembleBot -$1,606. end_date_iso: 15,306/17,446 markets (88%) — bulk backfill completed.
Still need Session 33 deployed to fix 8× idle-in-transaction connections and get mini backfill running.

---

## 12. COMMON DEBUG COMMANDS

```bash
KEY="C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.248.60.104"

# Service status + recent errors:
ssh -i $KEY $VPS "sudo systemctl status polymarket-ai && sudo tail -50 /opt/polymarket-ai-v2/data/paper_trading.log | grep -E 'ERROR|CRITICAL|WARN'"

# DB pool usage:
ssh -i $KEY $VPS "sudo -u polymarket psql -d polymarket -c \"SELECT state, COUNT(*) FROM pg_stat_activity WHERE datname='polymarket' GROUP BY state;\""

# end_date_iso coverage:
ssh -i $KEY $VPS "sudo -u polymarket psql -d polymarket -c \"SELECT COUNT(*) total, COUNT(end_date_iso) has_date FROM markets;\""

# Labeled predictions:
ssh -i $KEY $VPS "sudo -u polymarket psql -d polymarket -c \"SELECT COUNT(*), SUM(CASE WHEN was_correct THEN 1 END) correct FROM prediction_log WHERE was_correct IS NOT NULL;\""

# P&L summary:
ssh -i $KEY $VPS "sudo -u polymarket psql -d polymarket -c \"SELECT bot_name, COUNT(*) trades, SUM(realized_pnl) total_pnl FROM paper_trades WHERE realized_pnl IS NOT NULL GROUP BY bot_name ORDER BY 3 DESC;\""

# Check for idle-in-transaction (should be 0 after S33):
ssh -i $KEY $VPS "sudo -u polymarket psql -d polymarket -c \"SELECT count(*) FROM pg_stat_activity WHERE state='idle in transaction' AND datname='polymarket';\""

# Verify .env settings:
ssh -i $KEY $VPS "grep -E 'DB_POOL_SIZE|SYNC_LOG|HEALTH_CHECK|MINI_BACKFILL' /opt/polymarket-ai-v2/.env"
```

---

## 13. KNOWN ACTIVE ISSUES

1. **Session 33 VPS deploy PENDING** — DB pool fix, slug dedup, endDateIso fix, health_runner, mini backfill not yet on VPS. VPS running Session 32 code.
2. **end_date_iso ~28% coverage** — bulk backfill ran but deploy pending. Will improve after Session 33 deploy + mini backfill runs.
3. **2,322 labeled predictions (22%)** — bulk backfill is flowing. 120 correct = 5.2% win rate. Session 33 mini backfill will improve this further.
4. **MirrorBot 0 trades** — Gamma API endpoints slow (31-50s). Non-blocking.
5. **websockets.legacy** — `websocket_manager.py` uses deprecated API in websockets 15.x. Filtered in pytest.ini. Migration deferred.
6. **Polygon RPC 401 warning** — `POLYGON_RPC_URL=https://rpc.ankr.com/polygon` is set. Non-critical (mempool, not trading).

---

*Carbon copy written: Session 33 end, 2026-03-01*
*Primary handoff: AGENT_HANDOFF_COMPLETE_2026_02_27.md (full reference)*
*Tests: 750/750 passing locally. VPS deploy required for Session 33 changes.*
