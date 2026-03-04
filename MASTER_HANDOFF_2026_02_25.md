# MASTER HANDOFF — Polymarket AI V2
**Last updated**: 2026-02-26 UTC — Session 16
**Supersedes**: ALL prior handoff documents — this is the ONLY one to read
**Tests**: 372/372 passing, 2 warnings (scipy precision on synthetic data — harmless) (~45s)
**Bot status**: LIVE on VPS (PID 1150070) — ALL 5 BOTS RUNNING (WeatherBot added Session 16)
**Canonical path**: `C:\lockes-picks\polymarket-ai-v2\MASTER_HANDOFF_2026_02_25.md`

---

## ⚠️ FIRST THING TO DO IN NEXT SESSION

### 1. Verify all 5 bots still running (WeatherBot added Session 16)
```bash
ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 \
  "sudo journalctl -u polymarket-ai --since '5 minutes ago' --no-pager | grep 'Scan cycle starting' | tail -10"
```
Expected: all 5 bot names cycling (EnsembleBot, MomentumBot, ArbitrageBot, MirrorBot, WeatherBot). PID should be 1150070 or higher (auto-restart bumps PID).

### 1b. Check WeatherBot scan output specifically
```bash
ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 \
  "sudo grep -E 'WeatherBot|weatherbot_scan' /opt/polymarket-ai-v2/data/paper_trading.log | tail -10"
```
Expected: `weatherbot_scan_done` with `weather_markets=N groups=M groups_with_edge=K trades=T`. If `weather_markets=0`: Polymarket has no active temperature bucket markets at this time (normal between market cycles). If `groups_with_edge=0`: spread too narrow (normal in low-volatility periods).

### 2. Check trade count + open positions + cash
```bash
ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 \
  "sudo -u polymarket psql -d polymarket -c \"SELECT bot_name, COUNT(*) as trades FROM paper_trades GROUP BY bot_name ORDER BY trades DESC;\" \
   && sudo -u polymarket psql -d polymarket -c \"SELECT bot_id, COUNT(*) as open, SUM(size*entry_price) as notional FROM positions WHERE status='open' AND side!='SELL' GROUP BY bot_id;\""
```

### 3. Verify no phantom SELL exposure (critical — fixed Session 12)
```bash
ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 \
  "sudo -u polymarket psql -d polymarket -c \"SELECT side, status, COUNT(*) FROM positions GROUP BY side, status ORDER BY status, side;\""
```
Expected: NO rows with `side='SELL' AND status='open'`. If any exist: `UPDATE positions SET status='closed' WHERE status='open' AND side='SELL';`

### 4. Check P&L and cash state
```bash
ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 \
  "sudo -u polymarket psql -d polymarket -c \"SELECT bot_name, COUNT(*) as total, SUM(CASE WHEN realized_pnl IS NOT NULL THEN 1 ELSE 0 END) as with_pnl, ROUND(SUM(COALESCE(realized_pnl,0))::numeric,2) as total_realized FROM paper_trades GROUP BY bot_name;\""
```

### 5. Check for stop-loss re-entry loop (new — watch for MomentumBot cycling same market_id)
```bash
ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 \
  "sudo journalctl -u polymarket-ai --since '30 minutes ago' --no-pager | grep -E 'stop-loss|take-profit|re-entry blocked|exit cooldown|grace period' | tail -20"
```
Expected: Stops show `threshold=X.XX%, learned_ratio=Y.YYY`. Re-entry blocked logs = cooldown working.

### 6. Verify learning thresholds loaded
```bash
ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 \
  "sudo journalctl -u polymarket-ai --since '10 minutes ago' --no-pager | grep 'R5: Loaded'"
```
Expected: `R5: Loaded 25 z-threshold overrides from DB for MomentumBot`

---

## 1. SYSTEM OVERVIEW

### What This Is
A fully automated paper-trading prediction market bot:
- Scans **Polymarket** binary prediction markets (https://polymarket.com)
- Uses an **11-model ML ensemble** (GradientBoosting, LogisticRegression×3, ExtraTrees×3, HistGradientBoosting×2, RidgeClassifier×3, KNeighbors, MLP, CatBoost) to predict resolution probabilities
- Places **paper trades** ($100k virtual capital, `SIMULATION_MODE=true`)
- Tracks P&L, positions, model performance in **PostgreSQL** (local VPS PostgreSQL, NOT Supabase)
- Self-heals via FSM state machine, circuit breakers, kill switch, drawdown breaker, DegradationManager

### Architecture
```
main.py (~400 lines)
├── BaseEngine (base_engine/base_engine.py ~3400 lines)
│   ├── PredictionEngine (base_engine/prediction/prediction_engine.py ~1600 lines)
│   │   └── 11 ML models from data/model_cache.pkl (16MB, 43 features)
│   ├── Database (base_engine/data/database.py ~3400 lines, 23 ORM tables)
│   ├── OrderGateway (base_engine/execution/order_gateway.py)
│   │   ├── PaperTradingEngine (base_engine/execution/paper_trading.py)
│   │   │   ├── self.cash = $100,000 initial (corrected for realized_pnl on restart)
│   │   │   └── self.positions = {market_id: {size, avg_price, ...}}
│   │   ├── seed_positions_from_db() — seeds exposure on restart (SELL rows EXCLUDED)
│   │   └── reconcile_exposure_from_db() — DB ground truth every 5 min (SELL rows EXCLUDED)
│   ├── PositionManager (base_engine/execution/position_manager.py)
│   │   └── Monitors ALL bots, 10min grace period, model reversal + edge depletion exit
│   ├── SignalIngestion (base_engine/signals/signal_ingestion.py)
│   ├── WhaleTracker (base_engine/signals/whale_tracker.py)
│   ├── StreamingPersister (base_engine/data/streaming_persister.py)
│   ├── KillSwitch (base_engine/coordination/kill_switch.py)
│   └── Monitoring suite (base_engine/monitoring/*.py — 10+ modules)
├── EnsembleBot (bots/ensemble_bot.py ~1200 lines)  ← ML ensemble, conf threshold 0.30
├── MomentumBot (bots/momentum_bot.py)              ← 1300+ trades, 5 modes + learning exit
├── ArbitrageBot (bots/arbitrage_bot.py)            ← Running, early-exit when Gamma CB OPEN
├── MirrorBot (bots/mirror_bot.py)                  ← Running, no elites to mirror (Gamma down)
└── WeatherBot (bots/weather_bot.py ~370 lines)     ← NEW Session 16: temperature bucket markets
    ├── base_engine/weather/station_registry.py     ← 13 ASOS stations, alias lookup, health check
    ├── base_engine/weather/market_mapper.py        ← 4 regex patterns, TemperatureBucket grouping
    ├── base_engine/weather/forecast_client.py      ← Open-Meteo async client, 15-min cache
    └── base_engine/weather/probability_engine.py   ← skew-normal fit, CDF buckets, Kelly sizing
```

### Critical Mental Model — READ THIS FIRST
- **YES and NO are both BUY** — you buy that outcome's token. SELL = close position only.
- **P&L always** = `(current_price - entry_price) / entry_price` — same formula for YES and NO
- **Signals use YES/NO** (which outcome token); OrderGateway normalizes to BUY/SELL for CLOB routing
- **Market IDs in two forms**: numeric `m.id` (e.g. `628113`) and hex condition_id (`0x339d...`). Always JOIN both.
- **VPS DB = LOCAL PostgreSQL** (NOT Supabase). DB: `polymarket`, User: `polymarket`. `TIMESTAMP WITHOUT TIME ZONE` columns — NEVER pass timezone-aware datetime to asyncpg raw SQL. Always `.replace(tzinfo=None)`.
- **positions table unique constraint**: `(bot_id, market_id, side)`. Bot exits create a SELL row + keep YES/NO row. Both fixed to close together via `confirm_position()` (Session 12).
- **SELL rows** in positions table = audit trail of exit attempts. Always filter `side != 'SELL'` in exposure calculations. Fixed in seed, reconcile, and paper_trading (Session 12).
- **learning_thresholds** in MomentumBot: per-market accumulated trade outcome ratios. NOW wired to entry z-threshold AND exit stop/take thresholds (Session 13 — was dead code before).

---

## 2. ENVIRONMENT

### VPS (Primary — bot is running here)
- **Provider**: AWS Lightsail, eu-west-1 (Ireland)
- **IP**: `34.248.60.104`
- **SSH key**: `C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem`
- **OS user**: `ubuntu`
- **App directory**: `/opt/polymarket-ai-v2/` (root-owned — sudo required for writes)
- **Python venv**: `/opt/polymarket-ai-v2/venv/bin/python` (Python 3.13)
- **Systemd service**: `polymarket-ai.service` (auto-restart on failure, enabled at boot)
- **Log**: `sudo journalctl -u polymarket-ai --since '5 minutes ago' --no-pager`
- **Current PID**: 1150070 (started 2026-02-26 04:13 UTC, Session 16 deploy — WeatherBot)

### VPS Deploy Pattern (CRITICAL — /opt is root-owned, direct SCP fails)
```powershell
# Step 1: SCP to /tmp (ubuntu can write here)
scp -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" `
  "C:\lockes-picks\polymarket-ai-v2\path\file.py" ubuntu@34.248.60.104:/tmp/file.py

# Step 2: sudo cp from /tmp to app dir + restart
ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 `
  "sudo cp /tmp/file.py /opt/polymarket-ai-v2/path/file.py && sudo systemctl restart polymarket-ai && sleep 3 && sudo systemctl is-active polymarket-ai"

# For .env changes:
ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 "
  sudo cp /opt/polymarket-ai-v2/.env /tmp/polymarket.env
  sudo sed -i 's/OLD_KEY=.*/NEW_KEY=value/' /tmp/polymarket.env
  sudo cp /tmp/polymarket.env /opt/polymarket-ai-v2/.env
  sudo systemctl restart polymarket-ai"
```

### VPS .env Settings — CONFIRMED LIVE Session 16
```env
# DB
DB_POOL_SIZE=40
DB_MAX_OVERFLOW=5

# Bot scan
SCAN_MARKET_LIMIT=50
BOT_SCAN_TIMEOUT_SECONDS=300
ENSEMBLE_SCAN_CONCURRENCY=5
BOT_ENABLED_ARBITRAGE=true

# Confidence thresholds
ENSEMBLE_MIN_CONFIDENCE=0.30
MIN_CONFIDENCE_THRESHOLD=0.30

# Risk limits
RISK_MAX_POSITION_SIZE_USD=100    ← $100 per individual position
MAX_POSITION_SIZE_PCT=0.5         ← total exposure cap = 50% × $100k = $50k

# Ingestion
INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS=600
ARB_MAX_MARKETS_PER_SCAN=10

# Paper trading
PAPER_TRADING=true
SIMULATION_MODE=true
TOTAL_CAPITAL=100000.0

# MomentumBot exit thresholds (NEW — Session 13)
MOMENTUM_STOP_LOSS_PCT=0.05        ← base (5%) — scaled per-market by learning ratio
MOMENTUM_TAKE_PROFIT_PCT=0.10      ← base (10%) — scaled per-market by learning ratio
MOMENTUM_EXIT_COOLDOWN_SECONDS=900 ← 15 min re-entry block after stop or take-profit

# WeatherBot (NEW — Session 16; auto-applied via code defaults, no .env required)
BOT_ENABLED_WEATHER=true
WEATHER_MIN_CONFIDENCE=0.55        ← was already set in VPS .env before Session 16
# SCAN_INTERVAL_WEATHER=300        ← 5 min default (matches code default)
# WEATHER_MIN_EDGE=0.15            ← 15% minimum edge (code default)
# WEATHER_MAX_PER_GROUP_USD=200    ← $200 per city+date group (code default)
# WEATHER_DAILY_LOSS_LIMIT=500     ← $500/day hard cap (code default)
# WEATHER_MAX_CORRELATED_EXPOSURE=500 ← $500 per city across all dates (code default)
# WEATHER_KELLY_FRACTION=0.25      ← fractional Kelly multiplier (code default)
# WEATHER_DEFAULT_SIZE=25          ← base position size $25 (code default)
# WEATHER_FORECAST_CACHE_TTL=900   ← 15-min Open-Meteo cache (code default)
# WEATHER_MAX_LEAD_TIME_HOURS=168  ← max 7-day forecast horizon (code default)

# RL (optional — not yet enabled)
# RL_TRADE_TIMING_ENABLED=true
```

### Local Dev (Windows)
- **Working dir**: `C:\lockes-picks\polymarket-ai-v2`
- **Python**: 3.13.3, system-installed (no venv on local)
- **VPN required**: Surfshark ON before running locally (US IPs get 403 from Polymarket API)
- **Run**: `python main.py` or `python run_paper.py`

---

## 3. TESTS — ALWAYS RUN BEFORE DEPLOYING

```powershell
cd 'C:\lockes-picks\polymarket-ai-v2'; python -m pytest tests/unit/ -v --no-cov --tb=short
# Expected: 372 passed in ~40-50s, 2 warnings (scipy on near-identical test data — harmless)
```
Tests are in `tests/unit/` (25 files — added `test_weather_bot.py` Session 16 with 51 tests). Never deploy to VPS without green tests.

---

## 4. COMPLETE FIX HISTORY — ALL SESSIONS

### Session 1 (2026-02-22) — DB Pool Exhaustion Round 1
1. **MomentumBot SCAN_MARKET_LIMIT** — `bots/momentum_bot.py:242` — `markets = markets[:settings.SCAN_MARKET_LIMIT]`. Was scanning ALL 481 markets/cycle.
2. **Feature precompute delay** — `base_engine/base_engine.py` — 90s → 150s.
3. **id_resolver.py** — `resolve_market_id` + `resolve_market_ids_batch` → `get_raw_session()`.
4. **reap_stale_reservations** — `trade_coordinator.py:237` → `get_raw_session()`.

### Session 2 (2026-02-23) — Full DB Pool Audit
5. **EnsembleBot SCAN_MARKET_LIMIT** — `ensemble_bot.py:~542` — `markets[:settings.SCAN_MARKET_LIMIT]`.
6. **ArbitrageBot cap** — `arbitrage_bot.py` — `limit=min(self.max_markets_per_scan, settings.SCAN_MARKET_LIMIT)`.
7. **signal_ingestion semaphore timeout** — `asyncio.wait_for(acquire, timeout=10.0)`.
8. **signal_ingestion API timeout** — `_get_active_markets()` — `asyncio.wait_for(timeout=5.0)`.
9. **whale_tracker exponential backoff** — 30*(2^n) capped 300s, suspends at 10 failures.
10. **position_manager skip when empty** — `_last_known_count` + `_force_check_cycle % 3` guard.
11. **signal_ingestion collection loop timeouts** — `asyncio.wait_for(timeout=10.0)` on all 9 loops.
12. **ingestion_scheduler timeout** — `asyncio.wait_for(ingest_everything(), timeout=600.0)`.
13. **StreamingPersister restart backoff** — exponential 1, 2, 4, 8, 16, 30s cap.
14. **Feature precompute guard** — `not pe.models or not getattr(pe, "initialized", False)`.
15. **get_markets_with_price_history over-fetch** — slices `markets[:limit]` BEFORE price bulk fetch.
16. **whale_tracker nested session** — `_get_category_accuracy()` uses Redis cache / 0.5 fallback.

### Session 3 (2026-02-23) — Self-Healing Architecture (ALL NEW MODULES)
All 6 monitoring modules built + DB migration 021 applied:
- `base_engine/monitoring/bot_state_machine.py` — `transitions` FSM: healthy→degraded→failed→recovering→safe_mode
- `base_engine/monitoring/streaming_anomaly.py` — `river` ADWIN per-metric + HalfSpaceTrees multivariate
- `base_engine/monitoring/log_miner.py` — `drain3` log template miner, 9 critical patterns
- `base_engine/monitoring/portfolio_drawdown.py` — 5%/10% drawdown circuit breaker
- `base_engine/monitoring/degradation_manager.py` — 5-tier fleet sizing
- `base_engine/monitoring/health_scheduler.py` — APScheduler 7 jobs (60s/10s/30s/30s/30s/120s/300s)
- `database.py` — 3 new ORM models: BotHealthState, ConfigHistory, DeadLetterQueue
- `schema/migrations/021_self_healing_tables.sql` — APPLIED ✅

### Session 4 (2026-02-24) — VPS Deploy + Thundering Herd Fixes
17. **Bot stagger** — `main.py _BOT_START_STAGGER_SECONDS=30`.
18. **Whale tracker 60s initial delay** — `whale_tracker.py:_monitoring_loop()`.
19. **Position manager 60s initial delay** — `position_manager.py:_monitor_positions()`.
20. **Ingestion scheduler delay** — `.env INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS=600`.
21. **IQR penalty order bug** — `ensemble_bot.py` — B4 IQR multiplier moved AFTER threshold check.
22. **LogMiner EOF spam** — `log_miner.py` — `if e.args and 'EOF' in str(e.args[0]): return {}`.
23. **MIN_CONFIDENCE_THRESHOLD hardcoded** — `ensemble_bot.py` — replaced 2 hardcoded 0.55 values with `self.min_consensus_confidence`.
24. **UnboundLocalError datetime** — `ensemble_bot.py` — removed 3 local `from datetime import datetime, timezone` at lines 379, 596, 948. **CRITICAL Python scoping rule**: ANY `from X import name` ANYWHERE in a function makes that name LOCAL for the ENTIRE function body. Line 948 inside `if _created_raw:` caused `UnboundLocalError` at line 847 for EVERY market. Root cause of EnsembleBot 0 trades.
25. **Market selection SQL price filter** — `base_engine/base_engine.py:_fetch_tradeable_markets()` — `AND COALESCE(m.yes_price, 0.5) BETWEEN 0.05 AND 0.95`. Was returning near-resolved markets.
26. **`_do_warm()` 120s → 300s timeout** — `ensemble_bot.py` — `asyncio.wait_for(timeout=300.0)` + finally sets `_feature_cache_warmed=True`.

### Session 5 (2026-02-24) — Confidence Diagnosis
27. Confirmed NO-side flip is correct: `ensemble_bot.py:889-896`: `weighted_prediction = 1.0 - weighted_prediction` for NO side. Not a bug.

### Session 6 (2026-02-24) — Threshold Unlock + ArbitrageBot Fix
28. **Both confidence gates lowered to 0.40** → now 0.30.
29. **ArbitrageBot circuit-breaker early-exit** — `bots/arbitrage_bot.py:scan_and_trade()` — checks `_client.circuit_breaker.allow_request()` at top, returns early if OPEN.
30. **LogMiner FAILED pattern narrowed** — replaced `"FAILED"` (self-referential cascade 2000+/hr) with `"bot state machine failed"`.

### Session 7 (2026-02-24) — EnsembleBot Now Trading
31. **`SentimentAnalyzer.overall_sentiment` type bug** — `ensemble_bot.py` + `momentum_bot.py` — returns string enum `"bullish"/"bearish"` NOT float. Fixed: string-to-float map `{"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}`.

### Session 8 (2026-02-24) — Scan Timeout Tuning
32. `BOT_SCAN_TIMEOUT_SECONDS=300`, `SCAN_MARKET_LIMIT=50`.

### Session 9 (2026-02-24) — ROOT CAUSE OF ZERO DB TRADES + BOT NOW TRADING ✅
**Root cause**: `reserve_position()` in `trade_coordinator.py:98` used `datetime.now(timezone.utc)` (timezone-aware) for `opened_at`. VPS table uses `TIMESTAMP WITHOUT TIME ZONE`. asyncpg raises `DataError` — caught at DEBUG — completely invisible. ALL retries fail → returns False → "Position already taken" for EVERY trade across all prior sessions.

33. **`trade_coordinator.py:98`** — `now = datetime.now(timezone.utc).replace(tzinfo=None)` — THE FIX.
34. **`ensemble_bot.py` batched execution** — analyze batch of CONCURRENCY markets → execute immediately → next batch.
35. **VPS DB migrations**: `ALTER TABLE paper_trades ADD COLUMN IF NOT EXISTS correlation_id VARCHAR(64);`

### Session 10 (2026-02-24) — Drawdown Breaker + SafeMode + All 4 Bots ✅
36. **Drawdown equity formula** — `health_scheduler.py:_run_drawdown_check()`:
    ```python
    # OLD (BROKEN): equity = max(0.0, 1000.0 - exposure * 0.05) → false trips
    # NEW (CORRECT):
    equity = pe.cash + sum(p.get("size",0)*p.get("avg_price",0) for p in pe.positions.values())
    ```
37. **BotStateMachine safe_mode recovery** — `bot_state_machine.py:record_health_ok()`: `elif current == "safe_mode" and self._consecutive_health_ok >= 5: self.start_recover()`. Was permanent trap.
38. **State machine wired to scan loop** — `base_bot.py:_scan_loop()`: calls `state_machine.record_health_ok()` on success, `state_machine.record_error()` on exception. Was dead code.
39. **Exposure cap raised** — `MAX_POSITION_SIZE_PCT=0.5` ($50k, was 0.1=$10k — was blocking MomentumBot).
40. **Confidence lowered to 0.30** — post-retrain model confidence 27-34%, old threshold was 35%.
41. **ArbitrageBot re-enabled** — `BOT_ENABLED_ARBITRAGE=true`.

### Session 11 (2026-02-24) — Exposure Reconciliation Fix
**Bug**: `_total_exposure_usd` drifted from actual DB open positions due to failed SELL writes, manual DB edits, or SELL-row pollution. Pre-fix VPS showed $10,026 exposure (blocking trades); DB showed ~$4,454 actual — $5,572 phantom drift.

42. **`order_gateway.py:reconcile_exposure_from_db()`** — NEW METHOD. Replaces `_open_position_markets`, `_position_exposure`, `_position_details`, `_total_exposure_usd` from DB ground truth. Queries all `status='open'` positions and rebuilds in-memory tracking.
43. **`health_scheduler.py:_run_exposure_reconcile()`** — NEW JOB at 300s interval. Calls `gw.reconcile_exposure_from_db(db)`. Now 7 total scheduler jobs.

### Session 12 (2026-02-25) — SELL Phantom Exposure + CPU Steal + Accounting ✅
Three root causes of $1,738–$5,572 phantom exposure and $26k+ cash accounting errors:

**Bug 1: CPU steal time** → `system: unhealthy` false alarm
```python
# health_monitor.py:_check_system_resources()
# OLD: cpu_percent = psutil.cpu_percent(interval=0.1)  → includes 66% hypervisor steal
# NEW:
try:
    cpu_times = psutil.cpu_times_percent(interval=0.1)
    cpu_percent = cpu_times.user + cpu_times.system  # excludes steal time
except Exception:
    cpu_percent = psutil.cpu_percent(interval=0.1)
```

**Bug 2: SELL coordinator timeout too short** → exits silently failed
```python
# order_gateway.py
# OLD: _coord_timeout = 5.0  (for all orders)
# NEW:
_is_sell = str(side).upper() == "SELL"
_coord_timeout = 15.0 if _is_sell else 5.0  # exits get more time (closing > opening)
```

**Bug 3: SELL rows sitting as status='open'** → phantom exposure on seed + reconcile
When bot-initiated exits call `confirm_position(side='SELL')`, a SELL row is created as `status='open'` but the original YES/NO row ALSO stays `status='open'`. Both get seeded as capital.

Fixes in 3 places:
```python
# trade_coordinator.py:confirm_position() — close SELL row AND find+close YES/NO row:
if _is_sell:
    pos.status = "closed"  # close SELL audit row
    orig = (query YES/NO row for same bot+market).scalar_one_or_none()
    if orig: orig.status = "closed"  # close the actual position
else:
    pos.status = "open"

# order_gateway.py:seed_positions_from_db() — exclude SELL rows:
.where(Position.status == "open")
.where(Position.side != "SELL")  # SELL rows = exit attempts, not open capital

# order_gateway.py:reconcile_exposure_from_db() — exclude SELL + accumulate (not overwrite):
.where(Position.status == "open", Position.side != "SELL")
prev = new_exposure.setdefault(bot, {}).get(mid, 0.0)
new_exposure[bot][mid] = prev + value  # accumulate YES+NO if both open on same market
```

**Bug 4: Cash accounting broken across restarts** → P&L history lost
`seed_positions_from_db()` reset cash to `$100k - open_positions` on EVERY restart, discarding ALL realized P&L history. With 1,300+ trades and ~$2,700 losses, cash showed $98k instead of $95k.

Fixes:
```python
# paper_trading.py:seed_positions_from_db()
# After seeding open positions, restore realized P&L from DB:
pnl_row = await session.execute(
    select(func.coalesce(func.sum(PaperTradeRecord.realized_pnl), 0.0))
    .where(PaperTradeRecord.realized_pnl.isnot(None))
)
cumulative_realized_pnl = float(pnl_row.scalar() or 0.0)
if cumulative_realized_pnl != 0.0:
    self.cash += cumulative_realized_pnl

# database.py:insert_paper_trade() — now accepts realized_pnl parameter:
async def insert_paper_trade(..., realized_pnl: Optional[float] = None) -> None:
    rec = PaperTradeRecord(..., realized_pnl=realized_pnl)

# paper_trading.py — SELL trades now compute and pass realized_pnl:
realized_pnl = (exit_price - entry_price) * size  # for SELL trades
```

**DB cleanup done during Session 12**:
```sql
-- Closed 24 stale SELL-side open positions:
UPDATE positions SET status='closed' WHERE status='open' AND side='SELL';
-- Closed duplicate position id=16 (market 566227):
UPDATE positions SET status='closed' WHERE id=16;
```

**Post-fix VPS state** (Session 12): PID 758037 → seeded $2,976 (was phantom $4,614). Cash $98,256. Losses accurately tracked.

### Session 13 (2026-02-25) — Stop-Loss Loop + Learning System Wiring ✅
**Bug: MomentumBot stop-loss re-entry loop**
MomentumBot has its OWN `_check_exits()` scan-level stop-loss (separate from position_manager's). After a stop-loss exit, NO cooldown prevented re-entering the same market on the very next scan (seconds later). Market 566187 was bought at 0.625, stopped at 0.375 (-40%), immediately re-entered, stopped again — cycling ~$2,500 in losses overnight.

**Bug: `learning_thresholds` was write-only dead code**
Per-market thresholds updated after every trade (`self.threshold * 0.9` on win, `* 1.1` on loss) but NEVER read back for entry or exit decisions. Wasted accumulated intelligence from 1,300+ trades.

**Fix 1 — Re-entry cooldown**:
```python
# bots/momentum_bot.py
# __init__:
self.exit_cooldown_seconds = getattr(settings, "MOMENTUM_EXIT_COOLDOWN_SECONDS", 900)
self._recently_exited: Dict[str, float] = {}  # market_id → monotonic exit timestamp

# _check_exits() — after each successful exit:
self._recently_exited[str(market_id)] = time.monotonic()

# scan_and_trade() — before _execute_momentum_trade():
_exit_ts = self._recently_exited.get(str(mid))
if _exit_ts is not None:
    _elapsed = time.monotonic() - _exit_ts
    if _elapsed < self.exit_cooldown_seconds:
        logger.debug("MomentumBot: re-entry blocked for %s (%.0fs / %.0fs elapsed)", ...)
        continue
    else:
        del self._recently_exited[str(mid)]  # expired, clean up
```

**Fix 2 — Wire learning into ENTRY z-threshold (Mode 1)**:
```python
# bots/momentum_bot.py:analyze_opportunity() — after regime adjustment, before Mode 1 check:
_learned = self.learning_thresholds.get(str(market_id))
if _learned is not None and _learned > 0 and self.threshold > 0:
    _learn_ratio = _learned / self.threshold  # 0.9 (good market) or 1.1 (bad market)
    effective_z_threshold *= min(1.3, max(0.8, _learn_ratio))
    # Good market: lower z bar (more trade signals)
    # Bad market: raise z bar (need more extreme move to enter)
```

**Fix 3 — Dynamic stop/take + grace period in `_check_exits()`**:
```python
# For each position:
_learned = self.learning_thresholds.get(str(market_id))
if _learned and self.threshold > 0:
    _lr = min(2.5, max(0.6, _learned / self.threshold))
    dynamic_stop_loss = self.stop_loss_pct * _lr       # bad market → wider stop
    dynamic_take_profit = self.take_profit_pct / _lr   # bad market → tighter take
else:
    dynamic_stop_loss, dynamic_take_profit = self.stop_loss_pct, self.take_profit_pct

# Grace period — new positions get 5-15 min before stop-loss can fire:
_base_grace = 300  # 5 min
_grace_seconds = min(900, _base_grace + (_lr_for_grace - 1.0) * 600)
opened_at = pos.get("opened_at")  # now returned by get_open_positions_for_bot()
if opened_at:
    _age_s = (datetime.now(timezone.utc).replace(tzinfo=None) - opened_at_naive).total_seconds()
    if _age_s < _grace_seconds: continue  # skip exit check during grace period

# Logs now show dynamic threshold used:
# "stop-loss exit 566187 @ 0.375 (pnl=-40.00%, threshold=5.50%, learned_ratio=1.100)"
```

**Fix 4 — `get_open_positions_for_bot()` now returns `opened_at`**:
```python
# database.py — added to return dict:
"opened_at": r.opened_at,  # NaiveUTC datetime for grace period support
```

**Verified**: R5 loaded 25 z-threshold overrides on first scan. No re-entry loops since deploy. Grace period and dynamic thresholds active at DEBUG level.

### Session 14 (2026-02-25) — Full Infrastructure Audit + 18 Fixes ✅
**4-agent parallel audit** of ~15,000 lines. All Batch 1-3 fixes deployed (3 restarts).

**Batch 1 — P0+P1+P3 (Critical Bugs):**
1. **`bots/arbitrage_bot.py:1136`** — NegRisk LEG-B: `price=pr_yes` → `price=pr_no`. YES price used for NO orders.
2. **`bots/ensemble_bot.py:641-656`** — Warm flag: `_warm_ok=False` → only set True after successful warm. Was True even on TimeoutError → bot ran with empty cache, 0 trades.
3. **`base_engine/monitoring/degradation_manager.py`** — Recalibrated DEGRADATION_TIERS for 4 bots (was 8-bot calibration). Tier 0=4 healthy → all 4 bots at 50% sizing permanently (was Tier 0=7).
4. **`base_engine/monitoring/health_monitor.py:238`** — CB check: if `client.circuit_breaker.allow_request()==False` → DEGRADED. Was reporting healthy when CLOB CB OPEN.
5. **`base_engine/monitoring/log_miner.py:159`** — Self-exclusion: `if "logminer:" in line_lower: continue`. Was "circuit breaker" pattern → self-cascade 2000+/hr.
6. **`base_engine/monitoring/health_scheduler.py:114`** — Renamed metric: `"api_response_ms"` → `"data_freshness_ms"`. Was feeding data_freshness to ADWIN as api_response → false drift alarms → system=degraded → 50% sizing.
7. **`bots/momentum_bot.py:96-100`** — Clock-skew clamp: `remaining = max(0.0, min(self.exit_cooldown_seconds, expire_ts - _now_wall))`.

**Batch 2 — Performance:**
8. **`bots/momentum_bot.py` cache TTLs**: Regime 120s→600s, Cascade 60s→300s, Persuasion 120s→600s. TTLs shorter than scan duration (150s) → every scan was cache miss. Now: 13-37s warm scans.
9. **`base_engine/prediction/prediction_engine.py:2601-2918`** — L8 TA query consolidated. Eliminated 2nd `get_session()` per market. EnsembleBot: 33s first warm → **2s subsequent**.
10. **VPS DB indexes**: `CREATE INDEX idx_trades_market_ts`, `idx_market_prices_market_ts` (one-time).
11. **`base_engine/learning/scheduler.py:466`** — Added `_feature_vector_cache`, `_market_cache`, `_l8_cache` to retrain clear loop (was using old-scaler features with new models).

**Batch 3 — Robustness + Alpha:**
12. **`bots/mirror_bot.py:288`** — Added `"category": _cat` to items dict. Was always `""` → per-category thresholds had zero effect.
13. **`bots/ensemble_bot.py:184`** — `if not last: return False` → `return True`. No training timestamp = stale, not fresh.
14. **`bots/momentum_bot.py:193-206`** — Kill switch in reactive path: `asyncio.wait_for(..., timeout=3.0)`. Was blocking 30s+ on DB hang.
15. **`bots/mirror_bot.py:160`** — Elite refresh: `asyncio.wait_for(..., timeout=10.0)`. Was blocking scan 30s+ under pool pressure.
16. **`base_engine/execution/position_manager.py:214`** — Cooldown dict pruning (entries older than 2× cooldown). Fixed memory leak.

**Post-deploy verified (Session 14)**: EnsembleBot conf 0.23-0.28 (predictions running). Warm scan 2s. MomentumBot 13-37s warm scans (was 75-160s). No LogMiner cascade. No spurious DegradationManager tier changes.

### Session 15 (2026-02-25) — Audit Review + Batch 4 + Hardening ✅
**Audit triage** of 24 findings from 2 agents: 7 already-fixed (Batch 1), 8 false positives, 6 theoretical edge cases → hardening, 2 minor reporting inaccuracies → fixed, 1 deferred (P4-1 temporal leakage, schema migration required).

**Batch 4 — ML Accuracy + Reporting + Hardening:**
1. **`config/settings.py:172`** — `PATH_SUMMARY_MAX_ROWS` 5000 → 50000. Path/regime features missing for 80% of training rows when >5000 trades in DB. Root cause of Brier=0.424.
2. **`base_engine/prediction/prediction_engine.py`** — Drift tracker persistence: `_DriftTracker.to_dict()` / `from_dict()`. Saved to model_cache.pkl so `_recent_predictions`, `_recent_outcomes`, `_high_surprise`, `_baseline_mean/std` survive restarts.
3. **`base_engine/execution/position_manager.py:250`** — `unrealized_pnl` now includes taker fee: `(exit_price - entry_price) * size - fee`.
4. **`base_engine/execution/position_manager.py:224`** — Zero-size exit guard: skip SELL if `position.size <= 0`.
5. **`base_engine/execution/paper_trading.py:263-278`** — `entry_fee` stored on BUY, accumulated on averaging-up. `realized_pnl = (price - avg_price) * size - exit_fee - entry_fee_total`. Cash accurate on restart.
6. **`base_engine/execution/paper_trading.py:120-140`** — Per-position try/except in seed loop.
7. **`base_engine/coordination/trade_coordinator.py:20`** — STALE_RESERVATION_MINUTES 2 → 5. 2min too aggressive vs 37s max scan time.
8. **`base_engine/coordination/trade_coordinator.py:161`** — WARNING log when YES/NO row not found on confirm_position SELL.
9. **`base_engine/coordination/trade_coordinator.py:253`** — Reaper DELETE: `asyncio.wait_for(timeout=10.0)`.
10. **`base_engine/execution/order_gateway.py:574`** — `confirm_position()` wrapped in try/except with WARNING log.

**Post-deploy verified (Session 15)**: 51 positions seeded, cash $92,345.96, exposure $2,087. EnsembleBot warm scans 1.3-3.9s, trade #135 confirmed.

### Session 16 (2026-02-26) — WeatherBot Full Implementation ✅
**NEW 5TH BOT**: WeatherBot added, 372/372 tests (51 new), deployed to VPS PID 1150070.

**Architecture**: Open-Meteo API (GFS/HRRR/GEFS 31-member ensemble) → `forecast_client.py` → `probability_engine.py` (skew-normal fit + CDF integration) → `market_mapper.py` (4 regex patterns) → `weather_bot.py` → `place_order()`.

**New Files Created**:
- `base_engine/weather/__init__.py` — package init
- `base_engine/weather/station_registry.py` — 13 ASOS stations (NYC/KLGA, London/EGLC, Toronto/CYYZ, Seoul/RKSS, Buenos Aires/SAEZ, Atlanta/KATL, Seattle/KSEA, Dallas/KDFW, Wellington/NZWN, Ankara/LTAD, Miami/KMIA, Chicago/KORD, Denver/KDEN), `lookup_station()` alias matching, `StationHealthMonitor`
- `base_engine/weather/market_mapper.py` — 4 regex patterns for `"between X-Y°F"` / `"X°F or below"` / `"X°F or higher"` / `"X°C"`, `TemperatureBucket` + `WeatherMarketGroup` dataclasses, `WeatherMarketMapper.group_markets()`
- `base_engine/weather/forecast_client.py` — async Open-Meteo client, `CombinedForecast` dataclass (ensemble_members, deterministic_high, model_spread, lead_time_hours), 15-min in-memory cache, synthetic ensemble fallback (spread=2.0°F) when ensemble unavailable
- `base_engine/weather/probability_engine.py` — `scipy.stats.skewnorm` fit + CDF integration per bucket (0.5° boundary offsets), `compute_edges()`, `kelly_fraction()` (fractional Kelly), `load_calibration()` for historical bias
- `schema/migrations/022_weather_tables.sql` — `weather_forecasts` + `weather_calibration` tables (applied to VPS)
- `tests/unit/test_weather_bot.py` — 51 tests (8 station, 11 mapper, 4 date parsing, 11 probability, 7 weather_bot, 2 health monitor, 1 each forecast+combined)

**Files Modified**:
- `bots/weather_bot.py` — complete rewrite 181 → 370 lines. Full ensemble pipeline, group analysis, daily loss limit ($500), per-group cap ($200), correlated city exposure ($500/city), 15-min re-entry cooldown, Kelly sizing
- `bots/base_bot.py:46` — added `"WeatherBot": "WEATHER"` to `_SCAN_INTERVAL_KEYS`
- `config/settings.py` — added 11 WEATHER_* settings (SCAN_INTERVAL_WEATHER=300, WEATHER_MIN_EDGE=0.15, etc.)
- `base_engine/data/database.py` — added `WeatherForecast` + `WeatherCalibration` ORM models

**WeatherBot Risk Controls**:
- `WEATHER_MIN_EDGE=0.15` (15% minimum edge vs market price)
- `WEATHER_MAX_PER_GROUP_USD=200` (per city+date group cap)
- `WEATHER_DAILY_LOSS_LIMIT=500` (hard daily stop)
- `WEATHER_MAX_CORRELATED_EXPOSURE=500` (max across all dates for one city)
- `WEATHER_KELLY_FRACTION=0.25` (fractional Kelly)
- 15-min re-entry cooldown per market (same mechanism as MomentumBot)

**Market Support**: NYC (KLGA), London (EGLC), Toronto (CYYZ), Seoul (RKSS), Buenos Aires (SAEZ), Atlanta (KATL), Seattle (KSEA), Dallas (KDFW), Wellington (NZWN), Ankara (LTAD), Miami (KMIA), Chicago (KORD), Denver (KDEN). Handles °F and °C markets. Multi-outcome aware (probabilities normalized per city+date group).

**VPS Deployment**:
- Migration 022 applied: `weather_forecasts` + `weather_calibration` tables live
- `BOT_ENABLED_WEATHER=true` already in VPS .env (pre-existing)
- `DegradationManager` registered 6 bots (was 4 — now includes WeatherBot + previous 5th bot)
- PID 1150070, first scan 04:16:54 UTC, scan_ms=476ms

**Pending for WeatherBot**:
- First weather trade will fire when Polymarket has active temperature bucket markets with ≥15% edge
- Calibration table (`weather_calibration`) will auto-populate as forecasts vs actuals accumulate
- Open-Meteo rate limit: ~2,880 req/day vs 10,000 free tier limit — comfortable

---

## 5. CURRENT BOT STATUS (2026-02-26 ~04:17 UTC, after Session 16)

### Trade Counts & P&L (approximate at Session 16 deploy)
| Bot | Trades | Open Positions | Realized P&L |
|-----|--------|---------------|--------------|
| MomentumBot | ~1,500+ | ~50 | ~-$3,000 est. |
| EnsembleBot | ~135+ | ~10 | ~-$500 est. |
| ArbitrageBot | Minimal | Minimal | ~$0 |
| MirrorBot | 0 | 0 | $0 |
| WeatherBot | 0 | 0 | $0 (just deployed) |

**Current cash**: ~$92,345.96 (verified post-Session 15 deploy)
**Current exposure**: ~$2,087 (51 open positions, verified post-Session 15 deploy)
**Model status**: Batch 4 retrain fixes applied (PATH_SUMMARY_MAX_ROWS=50000). Drift tracker persists across restarts (model_cache.pkl includes tracker state).

### MomentumBot ✅ ACTIVELY TRADING
- 5 trade modes: mean_reversion, cascade_fade, persuasion_fade, convergence_fade, disposition_exploit
- **Exit system** (Session 13): dynamic stop/take from learning ratio + 5-15 min grace period + 15-min re-entry cooldown
- **Performance** (Session 14): warm scan 13-37s (was 75-160s). Cache TTLs fixed: Regime 600s, Cascade 300s, Persuasion 600s.
- Learning thresholds: 25+ markets in DB.

### EnsembleBot ✅ ACTIVELY TRADING
- Warm scan 1.3-3.9s (Session 14 L8 TA consolidation). Trade #135+ confirmed post-Batch 4.
- Cold-start guard: first scan skips, warms in background (300s timeout).

### ArbitrageBot ✅ RUNNING
- Returns early when Gamma API circuit breaker OPEN (prevents 120s DB hold)
- NegRisk LEG-B price bug fixed (Session 14 — was using YES price for NO orders).

### MirrorBot ✅ RUNNING
- No elite traders to mirror (Gamma API leaderboard blocked). 0 trades expected.
- Category field now set correctly (Session 14 — was always `""`, per-category thresholds had zero effect).

### WeatherBot ✅ RUNNING (NEW — Session 16)
- First deployed 2026-02-26 04:16:31 UTC. Scan time: ~476ms.
- Scans every 5 minutes (SCAN_INTERVAL_WEATHER=300).
- Waiting for active temperature bucket markets with ≥15% edge.
- Open-Meteo free tier: 10k req/day limit, ~2,880 expected — comfortable margin.
- Will log `weatherbot_scan_done groups_with_edge=N trades=T` when opportunities found.

---

## 6. KEY BUGS TO WATCH / KNOWN ISSUES

### ⚠️ MomentumBot Slow Scan Accumulation
Old PID developed 152s→247s scans over ~2 hours before Session 13 restart. Root cause: `analyze_opportunity()` does 4 DB queries per market × 50 markets = 200 queries/scan (regime detector, cascade detector, persuasion detector, trade flow analyzer). If `scan_ms` climbs past 30s in new PID: investigate caching per-market DB calls or reducing cascade/persuasion DB query frequency.

### ⚠️ learning_thresholds Only ±10% Differentiation Currently
25 markets with 0.045 or 0.055 (one trade each). As more trades accumulate and ratios diverge further from 1.0, per-market differentiation will grow. At current scale, effect is modest (~10% modulation of z-threshold and stop/take). Check `bot_market_params` table after 50+ more trades to see wider spread.

### ⚠️ position_manager.py Also Has Stop-Loss (10% default)
`position_manager.py` runs a SEPARATE stop-loss system (default 10%) with 10-min grace period and model reversal checks. MomentumBot's `_check_exits()` (now dynamic 3-12.5%) fires first. Two systems are intentional — MomentumBot's is more aggressive/adaptive, position_manager's is the safety net. Not a bug.

### ⚠️ Gamma API Blocked on VPS
Circuit breaker OPEN. ArbitrageBot/MirrorBot/feature_warm affected. Fix: HTTP proxy on VPS (infrastructure change — advise user, don't code around it).

### ⚠️ Redis Not Connected
In-memory fallbacks. Non-blocking. Fix: `REDIS_URL=redis://...` in VPS .env.

### ⚠️ Model Auto-Retrain Cycles
Triggers at Brier > 0.30 or acc < 0.45 on 50+ samples. During retrain: `pe.initialized=False` → EnsembleBot returns in 0.5ms. Takes 2-3 min. Confidence may shift after retrain — monitor and adjust `ENSEMBLE_MIN_CONFIDENCE` if needed.

### ⚠️ LogMiner Self-Reference (reduced but present)
"circuit breaker" pattern in CRITICAL_PATTERNS matches Gamma CB warnings → cascade. Non-critical log noise.

### ⚠️ Orphan Trades Alert (non-blocking)
`ALERT [ERROR]: Ingestion post-check failed — orphan trades: 138096 trades reference missing markets` — this is a data quality alert from the ingestion pipeline. The `paper_trades` and `positions` tables are unaffected. It means historical `price_history` or `trades` data references markets not in the `markets` table. Acceptable for paper trading.

---

## 7. CODE ARCHITECTURE — KEY PATHS

### MomentumBot Trade + Exit Pipeline
```
scan_and_trade()
│
├── _load_thresholds_from_db()  [R5 — first scan only]
│   └── Loads 25 z-threshold overrides from bot_market_params into learning_thresholds dict
│
├── _check_exits(market_id_to_price)  ← RUNS FIRST every scan
│   ├── db.get_open_positions_for_bot(bot_name)  → list of {market_id, entry_price, side, opened_at, ...}
│   ├── For each position:
│   │   ├── Compute dynamic thresholds from learning_thresholds ratio:
│   │   │   _lr = learning_thresholds[market_id] / self.threshold  (0.9 good, 1.1 bad)
│   │   │   dynamic_stop = 5% * clamp(_lr, 0.6, 2.5)
│   │   │   dynamic_take = 10% / clamp(_lr, 0.6, 2.5)
│   │   ├── Grace period: skip if age < 300-900s (based on _lr)
│   │   ├── Compute pnl_pct = (current - entry) / entry  [same for YES and NO]
│   │   ├── if pnl_pct <= -dynamic_stop: close + _recently_exited[market_id] = now
│   │   └── elif pnl_pct >= dynamic_take: close + _recently_exited[market_id] = now
│
├── get_markets_with_price_history(limit=50)
│
├── For each market:
│   ├── Check _recently_exited cooldown (15 min) → skip if in cooldown
│   ├── analyze_opportunity(market, price_history)
│   │   ├── Mode 1: mean_reversion — z-score >= effective_z_threshold (regime + learning adjusted)
│   │   ├── Mode 2: cascade_fade — CascadeDetector score >= 0.7
│   │   ├── Mode 3: persuasion_fade — PersuasionDetector
│   │   ├── Mode 4: convergence_fade — near-resolution (< 24h) price extremes
│   │   └── Mode 5: disposition_exploit — high vol + tiny price move + buy_sell_ratio extreme
│   └── _execute_momentum_trade(opportunity) → place_order()
│       └── On success: learning_thresholds[market_id] = self.threshold * 0.9 (if price_change>0) or *1.1
```

### EnsembleBot Full Trade Pipeline
```
scan_and_trade()
│
├── _is_prediction_engine_ready() guard  → returns False during retrain (pe.initialized=False)
├── get_all_tradeable_markets() → 50 markets (price 5-95%, liquidity >= 100)
├── Build candidates: filter closed/expired/already-positioned (O(1) in-memory check)
├── pe.prefetch_markets(candidate_ids)
├── COLD-START GUARD: if not _feature_cache_warmed → asyncio.create_task(_do_warm()) → return
│
├── BATCHED ANALYSIS (5 concurrent):
│   for batch in chunks(candidates, CONCURRENCY=5):
│     results = await asyncio.gather(*[_analyze_one(m) for m in batch])
│     for result in results: await _execute_ensemble_trade(result)  ← IMMEDIATE (not defer)
│
└── _analyze_one_token(market_id, token_id, price, side)
    ├── pe.predict(market_id, token_id, price)  → {prediction: p_yes, confidence, ...}
    ├── weighted_prediction = weighted avg of 11 model predictions
    ├── IF side=="NO": weighted_prediction = 1.0 - weighted_prediction  ← CRITICAL FLIP
    ├── consensus_confidence = weighted_prediction (after FLB, LLM clarity, alpha decay, B1)
    ├── GATE: if consensus_confidence < self.min_consensus_confidence (0.30): return None
    ├── B4 IQR penalty (position sizing only — AFTER gate, not a go/no-go decision)
    └── Return opportunity dict
```

### OrderGateway In-Memory Exposure Tracking
```python
# gw._total_exposure_usd — sum of open position cost bases (BUY adds, SELL subtracts)
# Populated from seed_positions_from_db() on startup (SELL rows EXCLUDED)
# Corrected every 5 min by reconcile_exposure_from_db() (SELL rows EXCLUDED)
# Used by risk manager: if total_exposure + new_position_value > TOTAL_CAPITAL * 0.5: block

# Paper trading equity (for drawdown check):
pe = gw.paper_trading_engine
equity = pe.cash + sum(p["size"] * p["avg_price"] for p in pe.positions.values())
# = initial_capital + cumulative_realized_pnl (never false-trips on deployed capital)
```

### TradeCoordinator — confirm_position() SELL behavior
```python
# When side='SELL': SELL row marked closed + YES/NO original row found and marked closed
# When side='BUY'/'YES'/'NO': row marked 'open' (normal)
# CRITICAL: This prevents stale SELL rows accumulating as phantom exposure
```

### DB Session Types (CRITICAL for new code and tests)
```python
db.get_session()      # Acquires semaphore (max 45). 30s timeout → raises DatabaseError.
                      # Tests: MagicMock(return_value=MockSessionCtx())

db.get_raw_session()  # Bypasses semaphore. Use for: advisory locks, kill switch,
                      # streaming_persister bulk inserts, id_resolver, reaper
                      # Tests: AsyncMock() or contextlib.asynccontextmanager

db._verify_database() # Async. Tests: AsyncMock()
```

### BotStateMachine State Transitions
```
healthy → degrade() → degraded → recover_from_degraded() → healthy   (3 OKs)
healthy/degraded → fail() → failed → start_recover() → recovering → recover() → healthy (3 OKs)
any → enter_safe_mode() → safe_mode → start_recover() [auto after 5 consecutive OK scans] → recovering → healthy
                          ↑ Triggered by drawdown breaker or kill switch
record_health_ok() / record_error() called from base_bot.py:_scan_loop() (wired Session 10)
```

### _fetch_tradeable_markets() SQL
```sql
SELECT m.id FROM markets m
WHERE m.active = true AND m.resolved = FALSE
AND ((m.yes_token_id IS NOT NULL AND m.yes_token_id != '')
  OR (m.no_token_id IS NOT NULL AND m.no_token_id != ''))
AND COALESCE(m.liquidity, 0) >= :min_liq
AND COALESCE(m.yes_price, 0.5) BETWEEN 0.05 AND 0.95
ORDER BY COALESCE(m.liquidity, 0) DESC
LIMIT :scan_limit
-- Cache: Redis 300s TTL, falls back to in-memory 60s
-- Location: base_engine/base_engine.py ~line 1940
```

### HealthScheduler Jobs (7 total)
| Job | Interval | Purpose |
|-----|----------|---------|
| health_check | 60s | DB/Redis/API/system resource health |
| streaming_anomaly | 10s | ADWIN + HalfSpaceTrees anomaly detect |
| log_miner | 30s | drain3 log template critical pattern detect |
| degradation_check | 30s | DegradationManager fleet tier update |
| drawdown_check | 30s | Portfolio drawdown circuit breaker |
| sli_report | 120s | SLI metrics snapshot |
| exposure_reconcile | 300s | Reconcile `_total_exposure_usd` from DB ground truth |

---

## 8. VPS MONITORING COMMANDS

```bash
# All 4 bots scanning
sudo journalctl -u polymarket-ai --since '5 minutes ago' --no-pager | grep 'Scan cycle starting' | tail -10

# MomentumBot exits + learning system
sudo journalctl -u polymarket-ai --since '30 minutes ago' --no-pager \
  | grep -E 'stop-loss|take-profit|re-entry blocked|grace period|R5: Loaded|learning_adj'

# Clean live log (suppress noise)
sudo journalctl -u polymarket-ai --since '5 minutes ago' --no-pager \
  | grep -v 'LogMiner\|ADWIN\|HalfSpace\|anomaly\|UserWarning\|sklearn\|validation.py\|circuit breaker\|streaming_anomaly'

# Drawdown status (should be zero warnings)
sudo journalctl -u polymarket-ai --since '30 minutes ago' --no-pager \
  | grep -E 'drawdown|safe_mode|SAFE_MODE|Fleet health'

# Check no phantom SELL exposure
sudo -u polymarket psql -d polymarket -c \
  "SELECT side, status, COUNT(*) FROM positions GROUP BY side, status ORDER BY status, side;"

# Open positions + notional
sudo -u polymarket psql -d polymarket -c \
  "SELECT bot_id, side, COUNT(*) as pos, ROUND(SUM(size*entry_price)::numeric,2) as notional
   FROM positions WHERE status='open' GROUP BY bot_id, side ORDER BY bot_id, side;"

# Paper trades count + realized P&L
sudo -u polymarket psql -d polymarket -c \
  "SELECT bot_name, COUNT(*) as trades,
          ROUND(SUM(COALESCE(realized_pnl,0))::numeric,2) as realized_pnl
   FROM paper_trades GROUP BY bot_name ORDER BY trades DESC;"

# Learning thresholds per market
sudo -u polymarket psql -d polymarket -c \
  "SELECT COUNT(*), MIN(param_value::float), MAX(param_value::float), AVG(param_value::float)
   FROM bot_market_params WHERE bot_name='MomentumBot' AND param_name='z_threshold';"

# Service control
sudo systemctl status polymarket-ai --no-pager
sudo systemctl restart polymarket-ai

# Check current .env settings
sudo grep -E 'MAX_POSITION_SIZE_PCT|BOT_ENABLED|ENSEMBLE_MIN|MIN_CONFIDENCE|SCAN_MARKET|MOMENTUM_' \
  /opt/polymarket-ai-v2/.env
```

---

## 9. KEY FILES INDEX

```
main.py                                      # ~400 lines — startup, bot orchestration, watchdog loop
run_paper.py                                 # Thin wrapper: SIMULATION_MODE=true, calls main
config/settings.py                           # ~610+ lines — ALL configurable settings (Pydantic)
                                             # NEW (Session 13): MOMENTUM_STOP_LOSS_PCT,
                                             #   MOMENTUM_TAKE_PROFIT_PCT, MOMENTUM_EXIT_COOLDOWN_SECONDS

base_engine/base_engine.py                   # ~3400 lines — BaseEngine, market data, feature precompute loop
base_engine/data/database.py                 # ~3400+ lines — SQLAlchemy async ORM, 23 tables
                                             # NEW (Session 12): insert_paper_trade() accepts realized_pnl
                                             # NEW (Session 13): get_open_positions_for_bot() returns opened_at
base_engine/data/unified_market_service.py   # Market data cache L1/L2/L3
base_engine/data/streaming_persister.py      # WebSocket → DB price persistence (get_raw_session)
base_engine/data/ingestion_scheduler.py      # Periodic full market ingestion (advisory lock, 600s delay)
base_engine/data/polymarket_client.py        # Polymarket REST/Gamma API client with circuit breaker
base_engine/prediction/prediction_engine.py  # ~1600 lines — 11-model ensemble, _extract_features, caches
base_engine/learning/learning_engine.py      # Model training, calibration, feature engineering
base_engine/learning/scheduler.py            # Periodic retraining every 6h; clears prediction caches
base_engine/execution/order_gateway.py       # Order placement — simulation=write to DB, live=CLOB API
                                             # FIXED (Session 11): reconcile_exposure_from_db() NEW METHOD
                                             # FIXED (Session 12): SELL coord timeout 5s→15s
                                             # FIXED (Session 12): seed + reconcile exclude side='SELL'
base_engine/execution/paper_trading.py       # PaperTradingEngine: cash, positions, place_order()
                                             # FIXED (Session 12): seed restores realized_pnl from DB
                                             # FIXED (Session 12): SELL trades pass realized_pnl to insert
base_engine/execution/position_manager.py    # Position monitoring: stop-loss (10%), model reversal,
                                             # edge depletion, 10-min grace period (ALL bots)
base_engine/signals/signal_ingestion.py      # 9 signal collection loops (news, social, whale, etc.)
base_engine/signals/whale_tracker.py         # Whale trade monitoring (60s initial delay, exp backoff)
base_engine/coordination/kill_switch.py      # System kill switch (get_raw_session, 30s TTL cache)
base_engine/coordination/trade_coordinator.py # Reservation system: prevents two bots on same market
                                             # CRITICAL FIX (Session 9): opened_at .replace(tzinfo=None)
                                             # FIXED (Session 12): confirm_position() closes YES/NO on SELL

bots/base_bot.py                             # ~430 lines — base class, scan loop, jitter, watchdog
                                             # Session 10: state_machine.record_health_ok/error() wired
bots/ensemble_bot.py                         # ~1200 lines — ML ensemble trades
                                             # Session 9: batched execute (analyze+trade per batch)
bots/momentum_bot.py                         # 5 trade modes + dynamic learning-based exit system
                                             # FIXED (Session 13): re-entry cooldown, learning→z-thresh,
                                             #   dynamic stop/take, grace period from opened_at
bots/arbitrage_bot.py                        # Price history arb (CB early-exit Session 6)
bots/mirror_bot.py                           # Copies elite trader positions

base_engine/monitoring/bot_state_machine.py  # transitions FSM. Session 10: safe_mode recovery
base_engine/monitoring/streaming_anomaly.py  # river ADWIN + HalfSpaceTrees
base_engine/monitoring/log_miner.py          # drain3 log miner, 9 critical patterns
base_engine/monitoring/portfolio_drawdown.py # 5%/10% drawdown circuit breaker
base_engine/monitoring/degradation_manager.py# Fleet sizing tiers (NOT wired to trading path)
base_engine/monitoring/health_scheduler.py   # APScheduler 7 jobs. Session 11: exposure_reconcile added
base_engine/monitoring/health_monitor.py     # FIXED (Session 12): CPU steal time excluded

schema/migrations/                           # SQL migrations (017, 018, 021 applied)
data/model_cache.pkl                         # Trained model cache — delete to force retrain (~2-3 min)
.env                                         # ALL secrets/config — never commit
tests/unit/                                  # 321 unit tests across 24 files
momentum_bot_learning_thresholds.json        # Per-market z-threshold overrides (JSON backup, DB is primary)
```

---

## 10. DATABASE SCHEMA

### Key Tables
| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `markets` | Binary prediction markets | id, condition_id, yes_token_id, no_token_id, yes_price, no_price, liquidity, active, resolved |
| `positions` | Open/closed paper positions | market_id, bot_id, source_bot, side (YES/NO/SELL), entry_price, size, status (open/closed/reserving), is_paper, opened_at (TIMESTAMP WITHOUT TZ!) |
| `paper_trades` | Trade history | bot_name, market_id, side, size, entry_price, resolved, realized_pnl (populated for SELL trades), correlation_id |
| `prediction_log` | ML prediction audit | market_id, token_id, prediction, confidence, model_predictions JSON, correlation_id |
| `signals` | External signals | source, direction, confidence, market_id |
| `price_history` | OHLCV for training | market_id, token_id, price, volume, timestamp |
| `model_versions` | Training history | version, brier_score, accuracy, feature_count |
| `bot_health_states` | Per-bot FSM state | bot_name, state, failure_count, sizing_multiplier |
| `bot_market_params` | Per-market learning params | bot_name, market_id, param_name ('z_threshold'), param_value |
| `feature_snapshot` | Per-market feature vectors | market_id, features JSONB (GIN indexed) |
| `trade_signals` | Signal metadata per trade | trade_id, signal_direction, signal_source, multipliers |

### Migrations Applied
- 017 (neg_risk columns, outcome_count): ✅ APPLIED
- 018 (feature_snapshot + GIN index): ✅ APPLIED
- 021 (self_healing_tables: bot_health_states, config_history, dead_letter_queue): ✅ APPLIED

### VPS DB Connection
- **DB name**: `polymarket` (NOT `polymarket_ai`)
- **User**: `polymarket`
- **Access**: `sudo -u polymarket psql -d polymarket`
- **Local PostgreSQL** on VPS (NOT Supabase). pool_size=40, max_overflow=5.
- **CRITICAL**: `TIMESTAMP WITHOUT TIME ZONE` columns. NEVER pass `datetime.now(timezone.utc)` (aware) to raw asyncpg text() queries. Always `.replace(tzinfo=None)` first.

### positions Table Constraint
- **Unique**: `(bot_id, market_id, side)` — allows YES + NO + SELL rows per (bot, market) triplet
- **SELL rows**: Created when `confirm_position(side='SELL')` is called. Session 12 fix closes SELL row + YES/NO row together in same transaction.
- **Never count SELL rows** as open capital. Always filter `WHERE side != 'SELL'` in exposure queries.

---

## 11. RESEARCH INTEGRATIONS — ALL COMPLETE

**Series A — Prediction Quality** (all ✅):
- A1: Category FLB, A2: PHT regime drift, A3: Ridge stacking (auto-activates ≥50 samples), A4: Lifecycle penalty, A5: Meister Kelly

**Series B — Signal Enhancement** (all ✅):
- B1: High-surprise relay, B2: Recency training, B3: VPIN toxicity proxy, B4: IQR scaling (AFTER gate), B5: Cross-bot sharing, B6: Mann-Whitney U, B7: DtACI conformal sizing, B8: OFI proxy, B9: Queue buffers, B10: Kalshi signal, B11: Shadow maker, B12: KS regime detection

**Tier 2 Features** (all ✅):
- #12: NegRisk arbitrage (LEG-A/LEG-B + proportional Kelly)
- #16: LLM clarity multiplier in EnsembleBot
- #17: Disposition effect (MomentumBot Mode 5)
- #18: VPIN wired into EnsembleBot + ArbitrageBot
- #19: Wallet clustering (3-heuristic similarity graph)
- #20: Order flow via B8 proxy

**RL Trade Timing**: Fully implemented. Activate with `RL_TRADE_TIMING_ENABLED=true` in .env.

---

## 12. MODEL CACHE

- **File**: `data/model_cache.pkl` (VPS: `/opt/polymarket-ai-v2/data/model_cache.pkl`)
- **Contains**: 11 trained models + `feature_columns` list (43 features) + `best_feature_names`
- **11 models**: GradientBoostingClassifier, LogisticRegression×3, ExtraTreesClassifier×3, HistGradientBoostingClassifier×2, RidgeClassifier×3, KNeighborsClassifier, MLPClassifier, CatBoostClassifier
- **Delete to retrain**: `sudo rm /opt/polymarket-ai-v2/data/model_cache.pkl && sudo systemctl restart polymarket-ai` → 2-3 min retrain
- **A3 Ridge stacking**: auto-activates once ≥50 training samples in prediction_log
- **Auto-retrain**: Brier > 0.30 OR accuracy < 0.45 on 50+ samples → background retrain. During retrain: `pe.initialized=False` → EnsembleBot 0.5ms scans.
- **CatBoost**: Skip `CalibratedClassifierCV` wrapper (no `__sklearn_tags__` in Python 3.13)
- **sklearn warnings on startup**: "X has feature names, but [Model] was fitted without feature names" — harmless, no functional impact.

---

## 13. DIAGNOSTIC GUIDE

### ⚠️ DEBUGGING DISCIPLINE
1. **Check exception swallowing FIRST.** `asyncio.gather(return_exceptions=True)` + `logger.debug` = silent failures. Upgrade DEBUG → WARNING before adding diagnostic layers.
2. **Grep the return type before assuming.** `abs(rs_score)` crashed because `overall_sentiment` is a string enum. One grep would have found it.
3. **Don't add diagnostic layers — read the source.** Read the callee first.
4. **Token analysis exceptions in `_analyze_one_token()` are caught at DEBUG by default.** Upgrade to WARNING when debugging 0-trade issues.
5. **If a fix requires altering dependencies (Python packages, VPS config, proxy), ADVISE USER FIRST.**

### Bot isn't trading — checklist
1. `grep 'scan complete' logs` → check `trades=0 best_confidence=None`
2. `grep 'token analysis exception' logs` — silent crash swallowing opportunities
3. `grep 'Tradeable markets from DB'` → should show `total=50`, prices in 5-95% range
4. Check `ENSEMBLE_MIN_CONFIDENCE` vs actual opportunity confidence
5. Check cold-start guard stuck: `scan_ms=0.4-0.5` consistently = `_feature_cache_warmed=False` or retraining
6. Check exposure cap: `grep 'Total exposure.*exceeds max'` — if present, raise `MAX_POSITION_SIZE_PCT`
7. Check `_recently_exited` cooldown (MomentumBot): if just stopped-out, re-entry blocked 15 min

### Phantom exposure — checklist (all fixed Sessions 11-12)
```
Symptoms: OrderGateway seed shows $X, DB open positions show much less
Cause 1: SELL rows sitting as status='open' → filter WHERE side!='SELL'
Cause 2: Duplicate YES+NO rows for same market both counted → accumulate, don't overwrite in reconcile
Cause 3: reconcile not running → check exposure_reconcile job in HealthScheduler logs
```

### Drawdown breaker tripped — checklist (fixed Session 10)
```
Symptoms: "Fleet health: tier=4" every 30s, all bots in SAFE_MODE
Cause: equity formula regression in health_scheduler.py
Fix: equity = pe.cash + sum(p["size"]*p["avg_price"] for p in pe.positions.values())
NOT: 1000 - exposure * 0.05  (that formula is wrong)
```

### Cold-start sequence (normal expected behavior)
```
t=0s    : Service restart (PID changes)
t=5-35s : Bots start (30s stagger between bots)
t=5s    : EnsembleBot first scan → _do_warm() starts in background → returns (0.5ms)
t=5-305s: EnsembleBot shows scan_ms=0.4-0.5 (warming or retraining)
t=300s  : _do_warm() succeeds or times out → _feature_cache_warmed=True
t=300s+ : Real EnsembleBot scans: ~200ms warm, ~120s cold
t=60s   : position_manager starts (60s initial delay)
t=60s   : whale_tracker starts (60s initial delay)
t=600s  : ingestion_scheduler starts
t=~60s  : R5 z-thresholds loaded from DB into MomentumBot.learning_thresholds on first scan
```

### Mock patterns for unit tests
```python
# db.get_session() is SYNC returning async context manager (NOT an AsyncMock):
mock_session_ctx = MagicMock()
mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
mock_session_ctx.__aexit__ = AsyncMock(return_value=False)
db.get_session = MagicMock(return_value=mock_session_ctx)

# db._verify_database() is async:
db._verify_database = AsyncMock()

# pe.predict() correct signature:
pe.predict(market_id: str, token_id: str, price: float, user_address=None, correlation_id=None)
# NOT: current_price=price (wrong kwarg)

# BaseEngine constructor:
BaseEngine()  # NO arguments — NOT BaseEngine(db) or BaseEngine(config)
```

---

## 14. KNOWN WORKING / NOT WORKING

| Service | Status | Notes |
|---------|--------|-------|
| MomentumBot paper trading | ✅ Active | 1,500+ trades, dynamic learning exit, 15-min re-entry cooldown, 13-37s warm scan |
| EnsembleBot paper trading | ✅ Active | 135+ trades, conf 0.30, 1.3-3.9s warm scan, PATH_SUMMARY_MAX_ROWS=50k |
| ArbitrageBot | ✅ Running | CB early-exit active, NegRisk LEG-B price bug fixed |
| MirrorBot | ✅ Running | No elite traders (Gamma down), 0 trades, category field fixed |
| WeatherBot | ✅ Running | NEW Session 16, 5-min scans, awaiting temperature bucket markets with ≥15% edge |
| Exposure reconciliation | ✅ Fixed | 5-min reconcile job, SELL rows excluded |
| Cash accounting across restarts | ✅ Fixed | realized_pnl restored from DB on seed |
| SELL phantom exposure | ✅ Fixed | confirm_position closes YES/NO; seed+reconcile filter SELL |
| Drawdown breaker | ✅ Fixed | equity = cash + pos_cost |
| Safe mode recovery | ✅ Fixed | 5 consecutive clean scans → start_recover() |
| MomentumBot re-entry loop | ✅ Fixed | 15-min cooldown after any stop/take exit |
| MomentumBot learning system | ✅ Fixed | Now drives entry z-threshold + dynamic stop/take/grace |
| CPU health check | ✅ Fixed | Uses user+system time only (excludes hypervisor steal) |
| State machine health reporting | ✅ Working | Wired to base_bot scan loop |
| PostgreSQL (VPS local) | ✅ Healthy | pool=45, queries <5ms |
| WebSocket price streams | ✅ Connected | 1000 token streams |
| Redis | ⚠️ Not connected | In-memory fallbacks |
| Polymarket REST API | ✅ Connected | |
| Polymarket Gamma API | ❌ CB OPEN | Affects ArbitrageBot, MirrorBot, feature warm |
| Google Trends | ⚠️ Rate limited | 429 → 3600s backoff |
| Polygon RPC | ❌ 401 | Mempool disabled |

---

## 15. LONGER-TERM ROADMAP

### Immediate (next session)
1. **WeatherBot first trade** — check `grep 'weatherbot_trade_filled\|weatherbot_scan_done' /opt/polymarket-ai-v2/data/paper_trading.log | tail -20`. First trade fires when edge ≥ 15% on any temperature bucket market.
2. **WeatherBot calibration** — as forecasts resolve, populate `weather_calibration` table with actual vs forecast temps. `probability_engine.load_calibration()` will use bias offsets to improve accuracy over time.
3. **Monitor P&L on resolved trades** — check `realized_pnl` in paper_trades after markets resolve
4. **Watch EnsembleBot confidence post-retrain** — PATH_SUMMARY_MAX_ROWS fix (5k→50k) should materially improve Brier score. If conf distribution shifts to 0.30-0.50 range, model is working better.
5. **Watch MomentumBot slow scan recurrence** — if `scan_ms` climbs past 30s over hours, cache the per-market DB calls in `analyze_opportunity()` (regime, cascade, persuasion detectors)
6. **Learning threshold growth** — after 50+ more MomentumBot trades, check if ratios have diverged past ±20% for meaningful per-market differentiation

### Medium Term
5. **After 50+ resolved trades**: Delete model_cache.pkl → restart → fresh retrain activates A3 Ridge stacking
6. **Enable RL trade timing**: `RL_TRADE_TIMING_ENABLED=true` in VPS .env
7. **Configure HTTP proxy on VPS** — fixes Gamma API, ArbitrageBot, MirrorBot, feature warm. Advise user — infrastructure change.
8. **Connect Redis** — `REDIS_URL=redis://...` in VPS .env

### After Sustained Profitable Paper Trading
9. **Consider `SIMULATION_MODE=false`** — requires real Polymarket account, private key, risk tolerance discussion with user

---

## 16. SESSION TIMELINE

| Session | Date | Key Achievement |
|---------|------|-----------------|
| 1 | 2026-02-22 | DB pool fixes round 1 — SCAN_MARKET_LIMIT, get_raw_session |
| 2 | 2026-02-23 | Full DB pool audit — 12 fixes |
| 3 | 2026-02-23 | Self-healing architecture — 6 new monitoring modules |
| 4 | 2026-02-24 | VPS deploy, bot stagger, IQR bug, UnboundLocalError, market price filter |
| 5 | 2026-02-24 | NO-side flip confirmed correct |
| 6 | 2026-02-24 | Confidence gates to 0.40, ArbitrageBot CB early-exit, LogMiner cascade fix |
| 7 | 2026-02-24 | EnsembleBot trading — fixed SentimentAnalyzer string vs float |
| 8 | 2026-02-24 | BOT_SCAN_TIMEOUT 300s, SCAN_MARKET_LIMIT 50 |
| 9 | 2026-02-24 | **ROOT CAUSE**: timezone datetime in reserve_position → zero DB trades. BOT TRADING. |
| 10 | 2026-02-24 | Drawdown false-trip fixed, safe_mode recovery, state machine wired, all 4 bots, MAX_POSITION_SIZE_PCT=0.5 |
| 11 | 2026-02-24 | Exposure reconciliation — new reconcile_exposure_from_db() method + 5-min scheduler job |
| 12 | 2026-02-25 | CPU steal fix, SELL timeout 15s, SELL phantom exposure (3-place fix), cash accounting across restarts |
| 13 | 2026-02-25 | Stop-loss re-entry loop fixed (15-min cooldown), learning_thresholds wired to entry z-thresh + dynamic stop/take/grace |
| 14 | 2026-02-25 | Full infra audit — 18 fixes: NegRisk price bug, warm flag, DegradationTiers, CB health check, LogMiner cascade, EnsembleBot 2s scans, MomentumBot 13-37s scans |
| 15 | 2026-02-25 | Batch 4 audit: PATH_SUMMARY_MAX_ROWS 5k→50k, drift tracker persistence, P&L fee accounting, zero-size guard, hardening (6 items) |
| 16 | 2026-02-26 | **WeatherBot 5th bot**: 5 new modules, 51 tests (372 total), skew-normal ensemble, 13 ASOS stations, deployed PID 1150070 |

---

*To resume: Read this file top-to-bottom (~10 min). Run "FIRST THING TO DO" commands to verify 5 bots running (including WeatherBot) + no phantom SELL rows + R5 learning loaded. Current PID: 1150070. All 5 bots active. WeatherBot scanning every 5 min, will trade when temperature bucket markets appear with ≥15% edge. Next priority: watch for WeatherBot's first trade, monitor EnsembleBot conf (should be improving with PATH_SUMMARY_MAX_ROWS=50k retrain), let learning ratios accumulate.*
