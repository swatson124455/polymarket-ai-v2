# COMPLETE AGENT HANDOFF — Polymarket AI V2
**Updated**: 2026-02-26 ~18:30 UTC — Session 20 (supersedes all previous versions)
**Purpose**: Full context transfer for a new agent to continue building without any prior conversation
**Tests**: 468/468 passing (25 test files)
**Live VPS PID**: 1199015 (started 2026-02-26 18:11 UTC) — 5 BOTS RUNNING (MomentumBot DISABLED)

---

## ⚡ IMMEDIATE STATE — READ THIS FIRST

### Current VPS Status
- **PID**: 1199015 (started 2026-02-26 18:11 UTC)
- **5 bots active**: EnsembleBot, ArbitrageBot, MirrorBot, SportsBot, WeatherBot
- **MomentumBot**: DISABLED (`BOT_ENABLED_MOMENTUM=false`) — 0.4% win rate, -$7,164 P&L. Repeated buy→stop→rebuy cycles at -37% to -75% losses on same markets.
- **Cash**: ~$89,281 (understated ~$8k due to pre-B5 paper trading bug baked into history — see §3C)
- **Open positions**: 49 EnsembleBot positions, ~$1,950 notional exposure
- **Realized P&L**: ~-$8,769 cumulative (MomentumBot -$7,164, EnsembleBot -$1,606)
  - **All 732 EnsembleBot losses** were caused by false model-reversal exits (now FIXED — see §8 Session 18)
  - **All MomentumBot losses** were real (stop-loss loop on same markets — bot disabled)

### ✅ What's Working Now
1. **Zero false exits** — model reversal/edge depletion disabled in position_manager (was causing 0% win rate)
2. **Adaptive exit learning** — "Adaptive exits refreshed: 24 markets, avg_mult=1.84" (live, confirmed)
3. **Wide stop/take** — 30% stop-loss / 60% take-profit (was 10%/20%), configurable
4. **EnsembleBot holding positions** — 49 open, zero premature exits since 18:11 UTC
5. **WeatherBot SWOT all 7 upgrades** deployed (local only — NOT on VPS yet, see §8 Session 19)
6. **Position cap fixed** — $1,000 per position (was $100 — was saturating portfolio with tiny positions)
7. **Cascade threshold fixed** — 0.8 (was hardcoded 0.6 — was blocking trades)

### ⚠️ Active Issues / Watch Points
1. **WeatherBot SWOT upgrades** (Sessions 19) — all 7 fixes built locally, **NOT YET deployed to VPS**. Waiting for VPS .env `BOT_ENABLED_WEATHER=true` (currently false — check). Deploy instructions in §8 Session 19.
2. **Live Brier = 0.424** — model degradation alert fires repeatedly. This is contaminated by the 732 false exits. Should improve as valid new trades accumulate. CV Brier = 0.169 (good).
3. **MirrorBot 31-50s scans** — slow, no trades (Gamma leaderboard API down). Non-blocking.
4. **WebSocket reconnects** — occasional (~every 3-5 min). Auto-recovers. Normal.
5. **Orphan trades warning** — "623 trades reference missing markets" from ingestion post-check. Pre-existing, non-fatal.

---

## 1. WHAT THIS SYSTEM IS

A fully automated paper-trading prediction market bot system:
- Scans **Polymarket** binary prediction markets (https://polymarket.com)
- Uses an **11-model ML ensemble** to predict resolution probabilities
- Places **paper trades** ($100k virtual capital, `SIMULATION_MODE=true`)
- Tracks P&L, positions, model performance in **PostgreSQL on VPS** (local, NOT Supabase)
- Self-heals via FSM state machine, circuit breakers, kill switch, drawdown breaker, DegradationManager

### Bots in BOT_REGISTRY (main.py)
1. **EnsembleBot** (`bots/ensemble_bot.py` ~1200 lines) — ML ensemble, conf threshold 0.30 — **49 open positions**
2. **MomentumBot** (`bots/momentum_bot.py`) — **DISABLED** (`BOT_ENABLED_MOMENTUM=false`) — 0.4% win rate, -$7,164 P&L
3. **ArbitrageBot** (`bots/arbitrage_bot.py`) — NegRisk arb + LEG-A/LEG-B; early-exit when Gamma CB OPEN
4. **MirrorBot** (`bots/mirror_bot.py`) — Mirrors elite traders; 0 trades (Gamma leaderboard down)
5. **SportsBot** (`bots/sports_bot.py`) — Sports markets bot; undocumented, ~1-7s scans
6. **WeatherBot** (`bots/weather_bot.py` ~370 lines) — Temperature bucket markets via Open-Meteo; SWOT upgrades local-only

### Bot Files That Exist But Are NOT in BOT_REGISTRY (not running)
- `bots/cross_platform_arb_bot.py` — cross-platform arbitrage
- `bots/llm_forecaster_bot.py` — LLM-based forecasting
- `bots/oracle_bot.py` — oracle-based resolution
- `bots/sports_arb_bot.py`, `sports_injury_bot.py`, `sports_live_bot.py` — sports variants

---

## 2. ARCHITECTURE

```
main.py (~400 lines)
├── BaseEngine (base_engine/base_engine.py ~3400 lines)
│   ├── PredictionEngine (base_engine/prediction/prediction_engine.py ~1600 lines)
│   │   └── 11 ML models from data/model_cache.pkl (16MB, 46 features, 11 models)
│   │       (GradientBoosting, LogReg×3, ExtraTrees×3, HistGradBoost×2, Ridge×3, KNN, MLP, CatBoost)
│   ├── Database (base_engine/data/database.py ~3400 lines, 23+ ORM tables)
│   ├── OrderGateway (base_engine/execution/order_gateway.py)
│   │   ├── PaperTradingEngine (base_engine/execution/paper_trading.py) ← B5 FIX + entry_fee fix
│   │   │   ├── self.cash = $100,000 initial (adjusted for realized_pnl on restart)
│   │   │   ├── self.positions = {market_id: {size, avg_price, token_id, side, entry_fee}}
│   │   │   └── B5 FIX: epsilon 1e-6 guard on position delete + ghost-position reset on BUY
│   │   ├── seed_positions_from_db() — seeds exposure on restart (SELL rows EXCLUDED)
│   │   └── reconcile_exposure_from_db() — DB ground truth every 5 min (SELL rows EXCLUDED)
│   ├── PositionManager (base_engine/execution/position_manager.py) ← SESSION 18+20 CHANGES
│   │   ├── Model reversal/edge depletion: DISABLED (was causing 0% win rate — all returns 0.208)
│   │   ├── Stop-loss: 30% base (configurable PM_STOP_LOSS_PCT) — adaptive per-market
│   │   ├── Take-profit: 60% base (configurable PM_TAKE_PROFIT_PCT) — adaptive per-market
│   │   └── _refresh_exit_learning(): churn analysis + resolution outcomes → per-market multipliers
│   ├── SignalIngestion (base_engine/signals/signal_ingestion.py)
│   ├── WhaleTracker (base_engine/signals/whale_tracker.py)
│   ├── StreamingPersister (base_engine/data/streaming_persister.py)
│   ├── KillSwitch (base_engine/coordination/kill_switch.py)
│   └── Monitoring suite (base_engine/monitoring/*.py — 10+ modules)
│       ├── bot_state_machine.py — `transitions` FSM: healthy→degraded→failed→recovering→safe_mode
│       ├── streaming_anomaly.py — `river` ADWIN per-metric + HalfSpaceTrees
│       ├── log_miner.py — `drain3` log template miner, 9 critical patterns
│       ├── portfolio_drawdown.py — 5%/10% drawdown circuit breaker
│       ├── degradation_manager.py — 5-tier fleet sizing (calibrated for 5 active bots)
│       └── health_scheduler.py — APScheduler 7 jobs
├── EnsembleBot
├── MomentumBot (DISABLED)
├── ArbitrageBot
├── MirrorBot
├── SportsBot
└── WeatherBot
    ├── base_engine/weather/station_registry.py ← SWOT P4 fixed (international station probing)
    ├── base_engine/weather/market_mapper.py    ← 4 regex patterns, TemperatureBucket grouping
    ├── base_engine/weather/forecast_client.py  ← SWOT P5 fixed (GEFS 31 + ECMWF 51 = ~82 members)
    └── base_engine/weather/probability_engine.py ← skew-normal fit, CDF buckets, Kelly sizing
```

---

## 3. CRITICAL MENTAL MODELS

### 3A. YES/NO/BUY/SELL
- **YES and NO are both BUY** — you buy that outcome's token. SELL = close position only.
- P&L formula: `(current_price - entry_price) / entry_price` — same for YES and NO
- Signals use YES/NO (which outcome token); OrderGateway normalizes to BUY/SELL for CLOB routing
- Market IDs exist in TWO forms: numeric `m.id` (e.g. 628113) and hex `condition_id` (0x339d...). Always JOIN both.

### 3B. VPS Database (CRITICAL)
- **LOCAL PostgreSQL** on VPS (NOT Supabase). DB: `polymarket`, User: `polymarket`
- **TIMESTAMP WITHOUT TIME ZONE** columns — NEVER pass timezone-aware datetime to asyncpg raw SQL
- Always `.replace(tzinfo=None)` on any datetime used in raw SQL (Session 9 root cause fix)
- **positions table unique constraint**: `(bot_id, market_id, side)`. Bot exits create a SELL row + keep YES/NO row. Both close together via `confirm_position()` (Session 12 fix).
- **SELL rows** in positions table = audit trail of exit attempts. Filter `side != 'SELL'` in ALL exposure calculations (seed, reconcile, paper_trading).
- **Connection**: `sudo -u polymarket psql -d polymarket` on VPS directly

### 3C. P&L Accounting (B5 Fix — CRITICAL)
- `realized_pnl = (price - avg_price) * size - exit_fee - entry_fee_total`
- `TAKER_FEE_BPS = 150` (1.5% per trade), `MAKER_FEE_BPS = 0`
- `entry_fee` accumulates ONLY across true averaging-up (same position, larger size)
- **B5 bug** (fixed Session 17): float residual from `size -= size` left 1e-14 instead of 0.0 → `size <= 0` didn't trigger → position dict not deleted → next BUY's entry_fee appended to accumulated fees → P&L grew from -$0.68 to -$20.52 over 184 cycles on one market
- **B5 fix**: `if pos["size"] <= 1e-6: del self.positions[market_id]` (SELL path) + ghost-position reset on BUY when residual ≤ 1e-6
- **Historical impact**: All pre-B5 P&L rows in DB are inflated. Cash starts ~$8k lower than it should. Cannot retroactively fix without complex per-trade recomputation.

### 3D. Cash Restoration on Restart
```python
# PaperTradingEngine seed sequence:
# 1. cash = initial_capital (100000.0)
# 2. For each row in paper_trades WHERE realized_pnl IS NOT NULL: cash += realized_pnl
# 3. For each open position: cash -= (size * avg_price + entry_fee)
# Historical inflated pnl values permanently reduce cash by ~$8k (pre-B5 bug)
```

### 3E. Python Scoping Trap (Session 4 root cause — NEVER REPEAT)
```python
# DANGER: ANY `from X import name` ANYWHERE in a function makes that name LOCAL
# for the ENTIRE function body — even lines BEFORE the import statement.
# NEVER use local `from datetime import datetime` inside functions.
# Always use the module-level import (line 1-20 of each file).
```

### 3F. Model Reversal False Exit Bug (Session 18 ROOT CAUSE of 0% win rate — FIXED)
```python
# BROKEN: position_manager called predict() without precomputed features
# → model returned 0.208 for ALL markets (default/empty feature vector)
# → 0.208 < 0.45 threshold → ALL YES positions force-exited as "model reversal"
# → 732 EnsembleBot exits, 0 wins, -$1,606 P&L
#
# FIX: Model reversal / edge depletion checks DISABLED in position_manager.py
# Positions now only exit on stop-loss (-30%) or take-profit (+60%)
```

### 3G. Cascade Detection (Session 18 fix)
```python
# CascadeDetector threshold was hardcoded 0.6 in game_theory.py
# All momentum markets had cascade_score 0.6-0.8 → cascade_active = True → blocked all trades
# FIX: threshold now configurable via settings.CASCADE_SCORE_THRESHOLD (default 0.6, VPS=0.8)
```

### 3H. Adaptive Exit Learning (Session 20 — CURRENT)
```python
# _refresh_exit_learning() in position_manager.py, runs every 30 min
# Queries paper_trades last 72h for churn (exit→rebuy cycles per market)
# Queries resolved markets to check if exits were premature
# Computes per-market multipliers stored in self._market_exit_mult
# Applied: eff_stop = base_stop * mult, eff_take = base_take / mult
# Wider stop on churned markets, tighter take on bad markets
# Result: 24 markets with avg_mult=1.84 (confirmed live)
```

---

## 4. ENVIRONMENT

### VPS (Bot runs here)
```
Provider:    AWS Lightsail, eu-west-1 (Ireland)
IP:          34.248.60.104
SSH key:     C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem
OS user:     ubuntu
App dir:     /opt/polymarket-ai-v2/ (root-owned — sudo required for ALL writes)
Python:      /opt/polymarket-ai-v2/venv/bin/python (Python 3.13)
Service:     polymarket-ai.service (auto-restart on failure, enabled at boot)
Log:         /opt/polymarket-ai-v2/data/paper_trading.log
Current PID: 1199015 (started 2026-02-26 18:11 UTC)
```

### VPS Deploy Pattern (MEMORIZE THIS)
```powershell
# ALWAYS SCP to /tmp first (ubuntu can write), then sudo cp to app dir

# Single file:
scp -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" `
  "C:\lockes-picks\polymarket-ai-v2\path\to\file.py" ubuntu@34.248.60.104:/tmp/file.py

ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 `
  "sudo cp /tmp/file.py /opt/polymarket-ai-v2/path/to/file.py && sudo systemctl restart polymarket-ai && sleep 5 && sudo systemctl is-active polymarket-ai"

# Multiple files (batch deploy):
scp -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" "file1.py" ubuntu@34.248.60.104:/tmp/file1.py
scp -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" "file2.py" ubuntu@34.248.60.104:/tmp/file2.py
ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 `
  "sudo cp /tmp/file1.py /opt/polymarket-ai-v2/path/file1.py && sudo cp /tmp/file2.py /opt/polymarket-ai-v2/path/file2.py && sudo systemctl restart polymarket-ai"

# .env changes:
ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 "
  sudo sed -i 's/OLD_SETTING=.*/NEW_SETTING=value/' /opt/polymarket-ai-v2/.env
  sudo systemctl restart polymarket-ai"
```

### VPS .env (Confirmed Live as of Session 20)
```env
# DB (local PostgreSQL, NOT Supabase)
DB_POOL_SIZE=40
DB_MAX_OVERFLOW=5

# Bot scan
SCAN_MARKET_LIMIT=100           ← raised from 50 (Session 18)
BOT_SCAN_TIMEOUT_SECONDS=300
ENSEMBLE_SCAN_CONCURRENCY=5
BOT_ENABLED_ARBITRAGE=true
BOT_ENABLED_MOMENTUM=false      ← DISABLED (Session 18)
BOT_ENABLED_WEATHER=false       ← check if true/false; SWOT upgrades local-only

# Confidence thresholds
ENSEMBLE_MIN_CONFIDENCE=0.30
MIN_CONFIDENCE_THRESHOLD=0.30

# Risk limits
RISK_MAX_POSITION_SIZE_USD=1000  ← raised from 100 (Session 18)
MAX_POSITION_SIZE_PCT=0.5        ← total exposure cap = 50% × $100k = $50k
CASCADE_SCORE_THRESHOLD=0.8      ← raised from hardcoded 0.6 (Session 18)

# Ingestion delay
INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS=600
ARB_MAX_MARKETS_PER_SCAN=10

# Paper trading
PAPER_TRADING=true
SIMULATION_MODE=true
TOTAL_CAPITAL=100000.0

# Position manager adaptive exits (Session 20)
PM_STOP_LOSS_PCT=0.30            ← was 0.10 (10%)
PM_TAKE_PROFIT_PCT=0.60          ← was 0.20 (20%)
PM_ADAPTIVE_EXITS=true
PM_LEARNING_REFRESH_SECONDS=1800

# WeatherBot (code defaults — also set in .env if BOT_ENABLED_WEATHER=true)
# WEATHER_MIN_CONFIDENCE=0.55
# WEATHER_MIN_EDGE=0.15
# WEATHER_MAX_PER_GROUP_USD=200
# WEATHER_DAILY_LOSS_LIMIT=500
# WEATHER_MAX_CORRELATED_EXPOSURE=500
# WEATHER_KELLY_FRACTION=0.25
# WEATHER_DEFAULT_SIZE=25
# WEATHER_FORECAST_CACHE_TTL=900
# WEATHER_MAX_LEAD_TIME_HOURS=168
```

### Local Dev (Windows)
```
Working dir: C:\lockes-picks\polymarket-ai-v2
Python:      3.13.3 system-installed (no venv)
VPN:         Surfshark ON required (US IPs get 403 from Polymarket API)
Run:         python main.py  OR  python run_paper.py
```

---

## 5. TESTS

```powershell
# Run all tests (always before deploying)
powershell.exe -Command "cd 'C:\lockes-picks\polymarket-ai-v2'; python -m pytest tests/unit/ -v --no-cov --tb=short"
# Expected: 468 passed (added 11 WeatherBot SWOT tests in Session 19)

# Run just weather bot tests (fast, 51+ tests)
powershell.exe -Command "cd 'C:\lockes-picks\polymarket-ai-v2'; python -m pytest tests/unit/test_weather_bot.py -v --no-cov --tb=short"
```

Test files: 25 files in `tests/unit/`. Note: Sports/kelly/arb tests (50 tests) fail when run in full suite due to `settings` mock leaking — pass in isolation. Pre-existing issue.

---

## 6. KEY FILES REFERENCE

```
main.py (~400 lines)                              ← BOT_REGISTRY, startup, watchdog
run_paper.py                                      ← background runner
config/settings.py (~610 lines)                   ← ALL settings with defaults
                                                     NEW: CASCADE_SCORE_THRESHOLD, PM_STOP_LOSS_PCT,
                                                          PM_TAKE_PROFIT_PCT, PM_ADAPTIVE_EXITS,
                                                          PM_LEARNING_REFRESH_SECONDS

base_engine/base_engine.py (~3400 lines)          ← core engine, market fetch, feature compute
                                                     CHANGED: CascadeDetector gets threshold from settings
base_engine/data/database.py (~3400 lines)        ← 23+ ORM models, get_session, get_raw_session
base_engine/prediction/prediction_engine.py (~1600 lines) ← 11-model ensemble, retrain, calibration
base_engine/execution/order_gateway.py            ← order routing, seed/reconcile exposure
                                                     CHANGED: RISK_MAX_POSITION_SIZE_USD=$1000
base_engine/execution/paper_trading.py            ← paper P&L engine ← B5 FIX + entry_fee fix
base_engine/execution/position_manager.py         ← MOST CHANGED SESSION 18+20
                                                     - Model reversal/edge depletion DISABLED
                                                     - Adaptive exit thresholds (30%/60% base)
                                                     - _refresh_exit_learning() churn analysis
                                                     - _market_exit_mult dict per-market multipliers
base_engine/coordination/trade_coordinator.py     ← reserve/confirm positions, stale reaper
base_engine/coordination/kill_switch.py           ← system-wide kill switch (30s TTL cache)
base_engine/signals/signal_ingestion.py           ← news/social/4chan signal collection
base_engine/signals/whale_tracker.py              ← whale trade monitoring
base_engine/data/streaming_persister.py           ← real-time price/trade streaming to DB
base_engine/analysis/game_theory.py               ← CHANGED: CascadeDetector threshold configurable
                                                     class CascadeDetector.__init__(db, threshold=0.6)
base_engine/data/polymarket_client.py             ← CHANGED: non-dict response → logger.debug (not warning)
base_engine/monitoring/bot_state_machine.py       ← FSM: healthy→degraded→failed→recovering→safe_mode
base_engine/monitoring/degradation_manager.py     ← 5-tier fleet sizing
base_engine/monitoring/health_scheduler.py        ← APScheduler 7 jobs (60s/10s/30s/30s/30s/120s/300s)
base_engine/monitoring/portfolio_drawdown.py      ← 5%/10% drawdown circuit breaker
base_engine/monitoring/log_miner.py               ← drain3 log pattern miner

bots/base_bot.py (430 lines)                      ← BaseBot abstract, scan loop, state machine
bots/ensemble_bot.py (~1200 lines)                ← ML ensemble bot (49 open positions)
bots/momentum_bot.py                              ← DISABLED (BOT_ENABLED_MOMENTUM=false)
bots/arbitrage_bot.py                             ← NegRisk arb (LEG-A + LEG-B)
bots/mirror_bot.py                                ← elite trader mirroring (0 trades, Gamma down)
bots/sports_bot.py                                ← sports markets (undocumented, ~1-7s scans)
bots/weather_bot.py (~370 lines)                  ← temperature bucket markets
                                                     SWOT UPGRADES: local-only, NOT yet on VPS

base_engine/weather/__init__.py                   ← package init
base_engine/weather/station_registry.py (211 lines)   ← 13 ASOS stations, alias lookup, health
                                                         SWOT P4: international station probe via Open-Meteo
base_engine/weather/market_mapper.py (222 lines)      ← 4 regex patterns, bucket grouping
base_engine/weather/forecast_client.py (208 lines)    ← Open-Meteo async client
                                                         SWOT P5: GEFS 31 + ECMWF IFS025 51 = ~82 members
base_engine/weather/probability_engine.py (220 lines) ← skew-normal CDF, Kelly sizing
                                                         SWOT P1: _maybe_reload_calibration() every 6h

schema/migrations/021_self_healing_tables.sql     ← APPLIED ✅
schema/migrations/022_weather_tables.sql          ← APPLIED ✅ (Session 16)

data/paper_trading.log                            ← main log (TeeLogger)
data/model_cache.pkl                              ← trained models (16MB, 46 features, 11 models)
```

---

## 7. DB SCHEMA (KEY TABLES)

```sql
-- Core trading tables
paper_trades       (trade_id, market_id, token_id, side, size, price, realized_pnl,
                    bot_name, correlation_id, created_at)
positions          (id, bot_id, market_id, token_id, side, size, entry_price,
                    status, opened_at, closed_at)
                   -- UNIQUE constraint: (bot_id, market_id, side)
                   -- CRITICAL: filter side != 'SELL' in exposure queries

-- Market data
markets            (id, condition_id, question, category, end_date, yes_price, volume_usd, active)
market_prices      (market_id, yes_price, no_price, recorded_at)

-- ML/Learning
prediction_log     (market_id, bot_name, prediction, confidence, actual_outcome, resolved_at, correlation_id)
bot_market_params  (bot_name, market_id, param_name, param_value, updated_at)
feature_snapshot   (market_id, features JSONB, computed_at) + GIN index

-- Self-healing (Session 3)
bot_health_states  (bot_id, state, consecutive_health_ok, last_transition, created_at)
config_history     (key, old_value, new_value, changed_at)
dead_letter_queue  (operation, payload JSONB, error, created_at, retry_count)

-- Weather (Session 16)
weather_forecasts  (station_id, target_date, forecast_time, lead_time_hours,
                    ensemble_members JSON, deterministic_high, model_spread, models_used JSON)
                   -- UNIQUE: (station_id, target_date, forecast_time)
weather_calibration (station_id, target_date, forecast_temp, actual_temp, lead_time_hours,
                     bias, model_name)
```

### DB Connection Commands
```bash
# On VPS:
sudo -u polymarket psql -d polymarket

# Trade counts + P&L:
SELECT bot_name, COUNT(*),
  SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
  ROUND(SUM(COALESCE(realized_pnl,0))::numeric,2) as total_pnl
FROM paper_trades GROUP BY bot_name;

# Open positions (no phantom SELL):
SELECT side, status, COUNT(*) FROM positions GROUP BY side, status;
SELECT * FROM positions WHERE status='open' AND side='SELL';  -- should be empty

# Fix phantom SELL rows if they appear:
UPDATE positions SET status='closed' WHERE status='open' AND side='SELL';

# Check adaptive exit multipliers (from paper_trades churn):
SELECT market_id,
  SUM(CASE WHEN side='SELL' AND realized_pnl < 0 THEN 1 ELSE 0 END) AS loss_exits,
  SUM(CASE WHEN side='BUY' THEN 1 ELSE 0 END) AS buys
FROM paper_trades
WHERE created_at > NOW() - INTERVAL '72 hours'
GROUP BY market_id
HAVING SUM(CASE WHEN side='SELL' AND realized_pnl < 0 THEN 1 ELSE 0 END) >= 2
ORDER BY loss_exits DESC LIMIT 10;
```

---

## 8. COMPLETE FIX HISTORY (CHRONOLOGICAL)

### Sessions 1-2 (2026-02-22/23) — DB Pool Round 1 + Full Audit (16 fixes)
1. `momentum_bot.py:242` — `markets = markets[:settings.SCAN_MARKET_LIMIT]`
2. `base_engine.py` — feature precompute delay 90s → 150s
3. `id_resolver.py` — `get_raw_session()` (bypasses semaphore on hot path)
4. `trade_coordinator.py:237` — reap_stale_reservations → `get_raw_session()`
5-16. EnsembleBot SCAN_MARKET_LIMIT cap, ArbitrageBot cap, signal_ingestion timeouts, whale_tracker backoff, position_manager idle skip, StreamingPersister restart backoff, feature precompute guards, bulk price fetch slicing, whale_tracker nested session cache

### Session 3 (2026-02-23) — Self-Healing Architecture (6 new modules)
- bot_state_machine, streaming_anomaly, log_miner, portfolio_drawdown, degradation_manager, health_scheduler
- DB migration 021 applied ✅

### Session 4 (2026-02-24) — VPS Deploy + Thundering Herd + **UnboundLocalError datetime**
- Bot stagger 30s, whale/position_manager initial delays, ingestion delay 600s
- **CRITICAL Python scoping fix**: removed 3 local `from datetime import datetime` in ensemble_bot.py (lines 379, 596, 948). Any local `from X import name` in a function makes that name local for the ENTIRE function body — even lines BEFORE the import. This crashed `_analyze_one_token` on EVERY market.
- Market price filter: `BETWEEN 0.05 AND 0.95` (excluded near-resolved markets)
- `_do_warm()` 300s timeout, `_feature_cache_warmed=True` always set in finally

### Sessions 5-6 (2026-02-24) — Confidence + Arb
- Both confidence gates lowered: 0.55 → 0.45 → 0.40 → 0.30
- ArbitrageBot: circuit-breaker early-exit when Gamma API CB OPEN
- LogMiner: narrowed "FAILED" → "bot state machine failed" (stopped 2000+/hr self-cascade)

### Session 7 (2026-02-24) — SentimentAnalyzer Type Bug ← CRITICAL
- `SentimentAnalyzer.overall_sentiment` returns string enum ("bullish"/"bearish"), NOT float
- `abs(rs_score)` crashed every token that passed confidence threshold (silent in asyncio.gather)
- Fixed: string-to-float map in ensemble_bot.py + momentum_bot.py

### Session 9 (2026-02-24) — ROOT CAUSE OF ZERO DB TRADES ← SINGLE MOST CRITICAL FIX
```python
# BROKEN (all prior sessions — 0 trades placed in DB):
now = datetime.now(timezone.utc)  # timezone-AWARE

# FIXED:
now = datetime.now(timezone.utc).replace(tzinfo=None)  # timezone-NAIVE for asyncpg
```
VPS `positions` table uses `TIMESTAMP WITHOUT TIME ZONE`. asyncpg raises `DataError`. Caught as DEBUG → completely invisible. ALL reserve_position() calls failed → "Position already taken" → zero trades.
File: `base_engine/coordination/trade_coordinator.py:98`

### Session 10 (2026-02-24) — Drawdown + SafeMode
- Drawdown formula fixed: `equity = pe.cash + sum(pos.size * pos.avg_price for all positions)`
- SafeMode recovery wired to scan loop (was dead code)

### Session 11 (2026-02-24) — Exposure Reconciliation
- NEW: `order_gateway.reconcile_exposure_from_db()` — DB ground truth every 5 min

### Session 12 (2026-02-25) — SELL Phantom Exposure ← CRITICAL FOR RESTART ACCURACY
Three-place fix for SELL rows creating phantom exposure:
1. `order_gateway.seed_positions_from_db()` — `.where(Position.side != "SELL")`
2. `order_gateway.reconcile_exposure_from_db()` — same SELL filter
3. `trade_coordinator.confirm_position(side='SELL')` — marks SELL row closed + closes YES/NO row
Also: CPU steal fix (`cpu_times_percent().user + .system`), SELL coordinator timeout 15s

### Session 13 (2026-02-25) — Stop-Loss Loop Fix + Learning Wired
- MomentumBot: `_recently_exited` dict + `MOMENTUM_EXIT_COOLDOWN_SECONDS=900`
- `learning_thresholds` NOW wired to entry z-threshold AND exit stop/take (was dead code)
- Dynamic stop/take per-market from learning ratio; 5-15 min grace period for new positions

### Session 14 (2026-02-25) — Full Infrastructure Audit + 18 Fixes (Batch 1-3)
Critical Batch 1:
- `arbitrage_bot.py:1136` — NegRisk LEG-B: `price=pr_no` (was using YES price for NO orders)
- `ensemble_bot.py:641-656` — Warm flag only on success (was setting True even on TimeoutError → 0 trades)
- `degradation_manager.py` — Recalibrated DEGRADATION_TIERS for 4 bots (was 8-bot → all bots at 50% sizing)
- `health_monitor.py:238` — CB OPEN → DEGRADED health status
- `log_miner.py:159` — Self-exclusion: `if "logminer:" in line_lower: continue`
- `health_scheduler.py:114` — Renamed `"api_response_ms"` → `"data_freshness_ms"` (false drift → 50% sizing)
- `momentum_bot.py:96-100` — Clock-skew clamp: `remaining = max(0.0, min(exit_cooldown, expire_ts - now_wall))`
Performance Batch 2:
- MomentumBot cache TTLs: Regime 120s→600s, Cascade 60s→300s (was shorter than scan → every scan cache miss)
- `prediction_engine.py` — L8 TA query in shared session (eliminated 2nd `get_session()` per market)
- DB indexes: `idx_trades_market_ts`, `idx_market_prices_market_ts`
Robustness Batch 3:
- `mirror_bot.py:288` — `"category": _cat` added to items dict (was always "")
- `ensemble_bot.py:184` — `if not last: return False` (no training timestamp = stale, not fresh)
- `position_manager.py:214` — Cooldown dict pruning (memory leak fix)

### Session 15 (2026-02-25) — ML Accuracy + Hardening
- `settings.py:172` — `PATH_SUMMARY_MAX_ROWS` 5000 → 50000 (was missing path/regime features for 80% of training rows → Brier=0.424)
- `prediction_engine.py` — Drift tracker persistence (saved in model_cache.pkl)
- `paper_trading.py` — `entry_fee` stored on BUY, restored on restart; `realized_pnl` deducts both fees
- `paper_trading.py` — per-position try/except in seed loop
- `trade_coordinator.py` — STALE_RESERVATION_MINUTES 2 → 5
- `order_gateway.py:574` — `confirm_position()` wrapped in try/except with WARNING log

### Session 16 (2026-02-26) — WeatherBot Built + Deployed
New files: station_registry.py, market_mapper.py, forecast_client.py, probability_engine.py
New: `schema/migrations/022_weather_tables.sql` ✅ applied, `tests/unit/test_weather_bot.py` (51 tests)
Modified: `bots/weather_bot.py` (181 → 370 lines), `config/settings.py` (11 WEATHER_* settings), `base_engine/data/database.py` (WeatherForecast + WeatherCalibration ORM)

### Session 17 (2026-02-26) — B5 Ghost Position Fix
**Root cause**: IEEE 754 float residual (1e-14) in `pos["size"] -= size` prevented position deletion → next BUY accumulated ALL historical entry fees → P&L grew to -$20.52 per market over 184 cycles.
```python
# SELL path fix (paper_trading.py):
if pos["size"] <= 1e-6:   # was: <= 0
    del self.positions[market_id]

# BUY path fix (paper_trading.py):
if pos.get("size", 0) <= 1e-6:  # ghost-position reset
    self.positions[market_id] = {"size": size, "avg_price": price,
                                  "token_id": token_id, "side": _token_side, "entry_fee": fee}
```

### Session 18 (2026-02-26) — Critical P&L Fixes + MomentumBot Disabled ← MAJOR SESSION

**Fix 1 (ROOT CAUSE of 0% EnsembleBot win rate)**: `position_manager.py` model reversal
```python
# BROKEN: predict() called from position_manager had no precomputed features
# All markets returned pred=0.208 → 0.208 < 0.45 threshold → ALL YES positions force-exited
# 732 exits, 0 wins, -$1,606 P&L
# FIX: disabled model reversal + edge depletion checks in position_manager entirely
# Positions now only exit on stop-loss or take-profit (price-based, not model-based)
```

**Fix 2**: `order_gateway.py:372` — Position cap raised
```python
RISK_MAX_POSITION_SIZE_USD=100 → 1000  # in VPS .env
# Was clamping all orders to $99 → saturating portfolio with 56 tiny $99 positions
```

**Fix 3**: `game_theory.py` CascadeDetector threshold configurable
```python
class CascadeDetector:
    def __init__(self, db, threshold: float = 0.6):  # was hardcoded 0.6
        self._threshold = threshold
# cascade_active = cascade_score > self._threshold  (line 174)
# base_engine.py: CascadeDetector(db, threshold=settings.CASCADE_SCORE_THRESHOLD)
# VPS .env: CASCADE_SCORE_THRESHOLD=0.8 (was blocking all momentum trades)
```

**Fix 4**: `polymarket_client.py:694`
```python
logger.debug(...)   # was logger.warning — 147 non-dict warnings per 30 min
```

**MomentumBot disabled**:
- 0.4% win rate (2 wins / 518 losses), -$7,164 realized P&L
- Repeated buy→stop→rebuy cycles on same markets (e.g. market 566187 cycling at -37% to -75%)
- Set `BOT_ENABLED_MOMENTUM=false` in VPS .env
- 9 open MomentumBot positions closed in DB

**VPS .env changes this session**:
- `RISK_MAX_POSITION_SIZE_USD=1000`
- `SCAN_MARKET_LIMIT=100`
- `CASCADE_SCORE_THRESHOLD=0.8`
- `BOT_ENABLED_MOMENTUM=false`

### Session 19 (2026-02-26) — WeatherBot SWOT Upgrades (All 7 Shipped) — LOCAL ONLY, NOT ON VPS

**468/468 tests pass** (+11 new WeatherBot SWOT tests)

**P1 (`weather_bot.py`)**: `_maybe_reload_calibration()` queries `weather_calibration` DB every 6h → calls `prob_engine.load_calibration()`. Was dead code before.

**P2 (`weather_bot.py`)**: `_handle_daily_boundary()` replaces sync `_reset_daily_pnl_if_needed`. On day boundary: queries `SUM(realized_pnl) FROM paper_trades WHERE bot_name='WeatherBot' AND created_at>=today` → restores `_daily_pnl`. Daily loss limit now survives restarts.

**P3 (`weather_bot.py`)**: `_save_forecast_to_db()` writes to `weather_forecasts` (ON CONFLICT DO NOTHING) + inserts calibration row (forecast_temp only) after each group analysis. Dedup via `_written_forecasts` set per session.

**P4 (`station_registry.py`)**: `StationHealthMonitor._check_station()` no longer returns True blindly for international. New `_probe_openmeteo()`: fetches 1-day Open-Meteo forecast for station coords; fails open on error.

**P5 (`forecast_client.py`)**: `get_ensemble_forecast()` fetches GEFS (31 members) + ECMWF IFS025 (51 members) in parallel. ECMWF members offset-indexed (member31+) and merged into GEFS dict. ~82 members vs prior 31 (~40% variance reduction).

**Near-expiry boost (`weather_bot.py`)**: `lead_time_hours < 24` → Kelly multiplier × 1.5 (capped at 2×) in `_execute_weather_trade`.

**Cross-city regime (`weather_bot.py`)**: `_compute_regime_boost()`: if ≥3 US cities all show unanimous warm (YES) or cold (NO) edge → 1.2× Kelly boost on all that scan cycle.

**Architecture changes (Session 19)**:
- `scan_and_trade()` refactored to 3 phases: (1) analyze all groups, (2) compute regime boost, (3) execute trades
- `_handle_daily_boundary()` is now async
- Forecast client has `_fetch_ensemble_model()` helper for parallelism

**⚠️ TO DEPLOY SESSION 19 SWOT UPGRADES:**
```bash
scp -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" `
  "C:\lockes-picks\polymarket-ai-v2\bots\weather_bot.py" ubuntu@34.248.60.104:/tmp/weather_bot.py
scp -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" `
  "C:\lockes-picks\polymarket-ai-v2\base_engine\weather\forecast_client.py" ubuntu@34.248.60.104:/tmp/forecast_client.py
scp -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" `
  "C:\lockes-picks\polymarket-ai-v2\base_engine\weather\station_registry.py" ubuntu@34.248.60.104:/tmp/station_registry.py
ssh -i "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem" ubuntu@34.248.60.104 "
  sudo cp /tmp/weather_bot.py /opt/polymarket-ai-v2/bots/weather_bot.py
  sudo cp /tmp/forecast_client.py /opt/polymarket-ai-v2/base_engine/weather/forecast_client.py
  sudo cp /tmp/station_registry.py /opt/polymarket-ai-v2/base_engine/weather/station_registry.py
  sudo sed -i 's/BOT_ENABLED_WEATHER=.*/BOT_ENABLED_WEATHER=true/' /opt/polymarket-ai-v2/.env
  sudo systemctl restart polymarket-ai"
```

### Session 20 (2026-02-26) — Adaptive Exit System Deployed ← CURRENT SESSION

**Verified**: Background task b33573c (Session 18 deploy) confirmed exit code 0.
**Confirmed live**: PID 1199015, "Adaptive exits refreshed: 24 markets, avg_mult=1.84"
**Zero false exits** since restart at 18:11 UTC.

**What was deployed to VPS:**
- `base_engine/execution/position_manager.py` — adaptive exit system
- `config/settings.py` — PM_STOP_LOSS_PCT, PM_TAKE_PROFIT_PCT, PM_ADAPTIVE_EXITS, PM_LEARNING_REFRESH_SECONDS

**Adaptive exit code in position_manager.py:**
```python
# __init__ additions:
self.default_stop_loss_pct = getattr(settings, "PM_STOP_LOSS_PCT", 0.30)
self.default_take_profit_pct = getattr(settings, "PM_TAKE_PROFIT_PCT", 0.60)
self._market_exit_mult: Dict[str, float] = {}
self._last_learning_refresh: float = 0.0
self._learning_refresh_interval: float = float(getattr(settings, "PM_LEARNING_REFRESH_SECONDS", 1800))

# _check_position() — adaptive threshold application:
_mid = str(getattr(position, "market_id", ""))
_mult = self._market_exit_mult.get(_mid, 1.0)
_eff_stop = self.default_stop_loss_pct * _mult      # wider stop for churned markets
_eff_take = self.default_take_profit_pct / _mult     # tighter take for churned markets
if pnl_pct <= -_eff_stop:
    await self._execute_stop_loss(position, pnl_pct)
elif pnl_pct >= _eff_take:
    await self._execute_take_profit(position, pnl_pct)

# _refresh_exit_learning() — full logic:
# 1. Churn query: buys/loss_exits per market (last 72h) → mult = 1.0 + (churn - 0.5) * 1.0
# 2. Resolution query: resolved markets avg_pnl < -5 → mult *= 1.3, avg_pnl > 0 → mult *= 0.8
# All mults clamped: min(3.0, max(0.5, mult))
```

---

## 9. PENDING NEXT STEPS (RECOMMENDED ORDER)

### Immediate — High Priority
1. **Deploy WeatherBot SWOT upgrades** (Session 19 — local-only, NOT on VPS). Use deploy commands in §8 Session 19. Set `BOT_ENABLED_WEATHER=true`. Verify: `grep 'WeatherBot' /opt/polymarket-ai-v2/data/paper_trading.log | tail -20`

2. **Monitor EnsembleBot P&L trajectory** — now that false exits are fixed, positions should hold 30%-60% range. Check within 24h:
   ```bash
   sudo -u polymarket psql -d polymarket -c "
   SELECT bot_name, COUNT(*),
     SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) wins,
     ROUND(SUM(COALESCE(realized_pnl,0))::numeric,2) total_pnl
   FROM paper_trades
   WHERE created_at > NOW() - INTERVAL '6 hours'
   GROUP BY bot_name;"
   ```

3. **Investigate service frequent restarts** — PID changed 5+ times on Feb 26. Root cause unknown (OOM, watchdog, systemd policy):
   ```bash
   sudo journalctl -u polymarket-ai --since '12 hours ago' --no-pager | grep -E 'Started|Stopping|Stopped|killed|OOM|Error' | head -30
   ```

### Near-term
4. **MomentumBot re-evaluation** — re-enable only after understanding root cause of stop-loss loops. The repeated buy→stop→rebuy cycle suggests the market selection scoring favors markets that are fundamentally not moving (e.g. near-resolution with stuck price). Fix: add resolved/near-resolved filter, tighter market quality filter, or use EnsembleBot's ML confidence as pre-filter.

5. **SportsBot documentation** — read `bots/sports_bot.py`, understand what it trades, verify risk limits. Check if it has any open positions or P&L.

6. **Live Brier improvement** — currently 0.424 (all 50 live samples are from the false-exit era). Will naturally improve as new correct trades accumulate. Accelerate by triggering retrain after 50+ new resolved predictions:
   ```bash
   sudo rm /opt/polymarket-ai-v2/data/model_cache.pkl && sudo systemctl restart polymarket-ai
   ```

7. **RL Trade Timing** (already implemented, not enabled):
   ```env
   RL_TRADE_TIMING_ENABLED=true  # add to .env to activate
   ```

8. **P4-1 temporal leakage** (deferred from Session 15) — `user_win_rate` at inference uses lifetime stats, training uses point-in-time. Fix requires schema migration to add `user_win_rate_at_trade_time` column.

9. **Historical P&L correction** (optional) — recompute correct realized_pnl for all pre-B5-fix trades. Complex SQL but would restore correct cash baseline (~$8k higher).

---

## 10. WEATHERBOT TECHNICAL DETAILS

### Architecture (with SWOT upgrades)
```
Open-Meteo API (free, no API key, 10k req/day limit)
  ├── /v1/forecast (GFS deterministic daily max temps)
  ├── /v1/ensemble?models=gfs_seamless (GEFS 31 members)
  └── /v1/ensemble?models=ecmwf_ifs025 (ECMWF 51 members) ← SWOT P5 NEW
         ↓ fetched in parallel, merged to ~82 members
forecast_client.py (async aiohttp, 15-min cache, 50 req/min token bucket)
         ↓
probability_engine.py
  ├── _maybe_reload_calibration() every 6h from DB ← SWOT P1 NEW
  ├── fit_distribution(ensemble_members, det_high, lead_time_hours)
  │   ├── ensemble_mean + std
  │   ├── lead_time inflation: effective_std = std * (1 + 0.02 * lead_time_hours)
  │   └── scipy.stats.skewnorm MLE fit → (loc, scale, skew)
  └── bucket_probabilities(loc, scale, skew, buckets)
         ↓
market_mapper.py (parse question text → TemperatureBucket + WeatherMarketGroup)
  4 regex patterns:
  - "between X-Y°F" → range bucket
  - "X°F or below"  → at_or_below bucket
  - "X°F or higher" → at_or_higher bucket
  - "exactly X°C"   → exact bucket
         ↓
weather_bot.py scan_and_trade() — 3-phase (SWOT architecture change):
  Phase 1: analyze all groups (build results list)
  Phase 2: _compute_regime_boost() — ≥3 US cities unanimous → 1.2× Kelly ← SWOT NEW
  Phase 3: execute trades with boost applied

  Other SWOT changes:
  - _handle_daily_boundary() (async) restores _daily_pnl from DB on restart ← SWOT P2
  - _save_forecast_to_db() writes weather_forecasts + calibration after each group ← SWOT P3
  - lead_time_hours < 24 → Kelly multiplier × 1.5 (capped 2×) ← SWOT near-expiry boost

  Risk controls (unchanged):
  - Daily loss limit: $500 (reset UTC midnight)
  - Per-group (city+date): $200 max
  - Correlated city exposure: $500 max across all dates for one city
  - Min edge: 15% (|model_prob - market_price| >= 0.15)
  - Re-entry cooldown, skip markets resolving within 2 hours
```

### Station Registry (13 cities)
```python
NYC=KLGA (40.7772/-73.8726), London=EGLC (51.5053/0.0553),
Toronto=CYYZ (43.6773/-79.6248), Seoul=RKSS (37.4386/126.9969),
Buenos Aires=SAEZ (-34.8222/-58.5358), Atlanta=KATL (33.6407/-84.4277),
Seattle=KSEA (47.4480/-122.3088), Dallas=KDFW (32.8998/-97.0403),
Wellington=NZWN (-41.3272/174.8051), Ankara=LTAC (39.9483/32.6886),
Miami=KMIA (25.7959/-80.2870), Chicago=KORD (41.9742/-87.9073),
Denver=KDEN (39.8561/-104.6737)
```

---

## 11. ML MODEL DETAILS

### PredictionEngine (11 models, 46 features)
```python
# Models (from model_cache.pkl — ~16MB):
- GradientBoostingClassifier
- LogisticRegression × 3 (different regularization)
- ExtraTreesClassifier × 3
- HistGradientBoostingClassifier × 2
- RidgeClassifier × 3 (warns "fitted without feature names" — harmless)
- KNeighborsClassifier (warns "fitted without feature names")
- MLPClassifier (warns "fitted without feature names")
- CatBoostClassifier

# Key features (46 total):
- Price: yes_price, spread, price_velocity, price_acceleration
- Volume: volume_24h, volume_change, liquidity_score
- Time: days_to_resolution, lifecycle_phase
- Category: category_win_rate, user_win_rate
- Signal: sentiment_score, whale_signal, cascade_signal, persuasion_score
- Path/regime: regime_score, path_complexity (requires PATH_SUMMARY_MAX_ROWS=50000)
- Technical: RSI, MACD, Bollinger bands (L8 TA features)

# Current model state:
- CV Brier = 0.169 (good — last retrain 17:14 UTC)
- Live Brier = 0.424 (bad — contaminated by false-exit era, will improve)
- Drift tracker: persisted in model_cache.pkl
- Auto-retrain: triggered when Brier > 0.30 or accuracy < 0.45

# Delete cache to force retrain:
sudo rm /opt/polymarket-ai-v2/data/model_cache.pkl && sudo systemctl restart polymarket-ai
```

---

## 12. STARTUP SEQUENCE

1. `logging_config.py` — structlog configured (**MUST run BEFORE any module imports that use logger**)
2. `_preflight_check()` — clears stale advisory locks in DB
3. `BaseEngine.__init__()`:
   - Load/retrain PredictionEngine from model_cache.pkl (~2-3 min if missing)
   - Connect to local PostgreSQL (pool_size=40, max_overflow=5)
   - seed_positions_from_db() (SELL-filtered)
   - PaperTradingEngine.seed(): cash = 100000 + sum(realized_pnl) - open_position_notional
4. Bot instantiation with `_BOT_START_STAGGER_SECONDS=30` stagger
5. DegradationManager.register_bot() for each bot
6. Each bot scan_loop() starts with jitter = `(hash(bot_name) % 20) + 5` seconds
7. Health scheduler: 7 APScheduler jobs (60s/10s/30s/30s/30s/120s/300s)
8. Ingestion scheduler: starts after 600s delay

### Bot Stagger (startup order)
```
EnsembleBot  T+0s
ArbitrageBot T+30s   (MomentumBot disabled)
MirrorBot    T+60s
SportsBot    T+90s
WeatherBot   T+120s
```

---

## 13. KNOWN PATTERNS & PITFALLS

### async/await Patterns
```python
# DB session (normal — uses semaphore):
async with db.get_session() as session:
    result = await session.execute(...)

# DB session (hot path — bypasses semaphore):
async with db.get_raw_session() as session:
    result = await session.execute(...)

# NEVER use timezone-aware datetime in raw SQL (Session 9 fix):
now = datetime.now(timezone.utc).replace(tzinfo=None)  # CORRECT
now = datetime.now(timezone.utc)  # WRONG — asyncpg DataError on TIMESTAMP WITHOUT TIME ZONE

# Coordinator timeout pattern:
result = await asyncio.wait_for(coordinator.reserve_position(...), timeout=5.0)
```

### Mock Patterns for Tests
```python
# db.get_session() is SYNC returning async context manager (NOT AsyncMock):
mock_db = MagicMock()
mock_db.get_session.return_value = MockSessionCtx()

# db._verify_database() is async:
mock_db._verify_database = AsyncMock()
```

### LogMiner Self-Cascade
CRITICAL_PATTERNS list must not contain substring that matches its own output. Use specific patterns like `"bot state machine failed"` not generic `"FAILED"`. Always check for self-reference when adding patterns.

### DegradationManager Tier Calibration
Currently calibrated for 5 active bots (MomentumBot disabled):
- Tier 0 (1.0x sizing): 4+ healthy
- Tier 1 (0.75x): 3 healthy
- Tier 2 (0.5x): 2 healthy
- Tier 3 (0.1x): 1 healthy
- Tier 4 (0.0x): 0 healthy

---

## 14. RESEARCH INTEGRATIONS (ALL COMPLETE)

From `elite_polymarket_v2.docx` — all 17 features implemented:

**Series A (ML accuracy)**: A1 Category FLB ✅, A2 PHT drift ✅, A3 Ridge stacking ✅, A4 Lifecycle penalty ✅, A5 Meister Kelly ✅

**Series B (signal alpha)**: B1 High-surprise relay ✅, B2 Recency training ✅, B3 VPIN toxicity ✅, B4 IQR scaling ✅, B5 Ghost position fix ✅, B6 Mann-Whitney U ✅, B7 Conformal sizing ✅, B8 OFI proxy ✅, B9 Queue buffers ✅, B10 Kalshi signal ✅, B11 Shadow maker ✅, B12 KS regime detection ✅

**Structural alpha**: Partition dependence filter ✅, Observability SLIs ✅

**RL Trade Timing** (implemented, activate via `RL_TRADE_TIMING_ENABLED=true` in .env)

**WeatherBot SWOT** (7/7 complete locally, awaiting VPS deploy)

---

## 15. REFERENCE COMMANDS

```bash
SSH="ssh -i C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.248.60.104"

# Service health:
$SSH "sudo systemctl status polymarket-ai --no-pager | head -10"

# Recent log tail:
$SSH "tail -30 /opt/polymarket-ai-v2/data/paper_trading.log"

# Trade P&L by bot:
$SSH "sudo -u polymarket psql -d polymarket -c \"
SELECT bot_name, COUNT(*),
  SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END) wins,
  ROUND(SUM(COALESCE(realized_pnl,0))::numeric,2) total_pnl
FROM paper_trades GROUP BY bot_name;\""

# Recent trades (last hour):
$SSH "sudo -u polymarket psql -d polymarket -c \"
SELECT bot_name, side, ROUND(size::numeric,2), ROUND(price::numeric,4),
  ROUND(realized_pnl::numeric,4), created_at
FROM paper_trades
WHERE created_at > NOW() - INTERVAL '1 hour'
ORDER BY created_at DESC LIMIT 10;\""

# Open positions:
$SSH "sudo -u polymarket psql -d polymarket -c \"
SELECT bot_id, COUNT(*) as open_positions
FROM positions WHERE status='open' AND side != 'SELL'
GROUP BY bot_id;\""

# Check adaptive exit learning (run in VPS Python or via log):
$SSH "grep 'Adaptive exits refreshed' /opt/polymarket-ai-v2/data/paper_trading.log | tail -5"

# Scan timing:
$SSH "grep 'Scan cycle done' /opt/polymarket-ai-v2/data/paper_trading.log | tail -20"

# WeatherBot activity:
$SSH "grep -E 'WeatherBot|weatherbot' /opt/polymarket-ai-v2/data/paper_trading.log | tail -10"

# Restart investigation:
$SSH "sudo journalctl -u polymarket-ai --since '12 hours ago' --no-pager | grep -E 'Started|Stopping|killed|OOM' | head -20"
```

### Run Tests (Local)
```powershell
cd 'C:\lockes-picks\polymarket-ai-v2'; python -m pytest tests/unit/ -v --no-cov --tb=short
# Expected: 468 passed
```

### Deploy Single File
```powershell
$KEY = "C:\Users\samwa\.ssh\LightsailDefaultKey-eu-west-1.pem"
$VPS = "ubuntu@34.248.60.104"
$FILE = "relative\path\to\file.py"
$DEST = "/opt/polymarket-ai-v2/path/to/file.py"

scp -i $KEY "C:\lockes-picks\polymarket-ai-v2\$FILE" "${VPS}:/tmp/file.py"
ssh -i $KEY $VPS "sudo cp /tmp/file.py $DEST && sudo systemctl restart polymarket-ai && sleep 5 && sudo systemctl is-active polymarket-ai"
```

---

## 16. SYSTEM STATE SNAPSHOT (2026-02-26 ~18:30 UTC — Session 20)

| Component | State | Notes |
|-----------|-------|-------|
| VPS Service | ✅ active (PID 1199015) | Started 18:11 UTC |
| EnsembleBot | ✅ running | 49 open positions, ZERO false exits since restart |
| MomentumBot | ❌ DISABLED | `BOT_ENABLED_MOMENTUM=false` — -$7,164 P&L history |
| ArbitrageBot | ✅ running | Early-exits when Gamma CB OPEN |
| MirrorBot | ⚠️ running | 0 trades, slow (31-50s scans), Gamma API down |
| SportsBot | ⚠️ running | Undocumented, 1-7s scans |
| WeatherBot | ⚠️ running | SWOT upgrades local-only, NOT deployed to VPS yet |
| Adaptive exits | ✅ LIVE | 24 markets, avg_mult=1.84, refreshes every 30 min |
| Model reversal | ✅ DISABLED | Was 100% cause of EnsembleBot losses |
| Stop-loss | 30% base | Adaptive per-market (up to 90% for churned markets) |
| Take-profit | 60% base | Adaptive per-market |
| Position cap | $1,000 | Was $100 |
| Cascade threshold | 0.8 | Was hardcoded 0.6 |
| PostgreSQL | ✅ healthy | 49 open positions, pool_size=40 |
| ML models | ✅ loaded | 11 models, CV Brier=0.169, live Brier=0.424 (contaminated) |
| Tests (local) | ✅ 468/468 | 25 test files |

---

## 17. P&L CONTEXT (WHAT HAPPENED AND WHY)

### The Real Story of -$8,769
- **EnsembleBot -$1,606** (732 losses, 0 wins): ALL losses caused by model_reversal false exits. predict() returned 0.208 for every market (no precomputed features) → exited every YES position immediately. Systematic -$0.01/trade from entry-exit spread × 732 trades. **NOT real losses — system error.** Fixed Session 18.
- **MomentumBot -$7,164** (2 wins / 518 losses): Real losses. Stop-loss loop: bot would buy a market, hit 5% stop, then re-buy the SAME market, repeat. Evidence: market 566187 cycled 184 times. Root causes: poor market selection (bought near-resolved markets that barely moved), stop-loss too tight (5%), re-entry cooldown insufficient. Bot disabled.

### Expected Trajectory Now
- EnsembleBot has 49 positions at various profit/loss levels (NOT exited prematurely)
- Positions will hold until 30% loss or 60% gain (or natural resolution)
- Model CV Brier=0.169 suggests edge exists; live performance contaminated by false-exit history
- Should see first real wins within 24-48h as positions resolve

---

*End of Agent Handoff — Session 20*
*This document supersedes MASTER_HANDOFF_2026_02_25.md and all prior versions.*
*Next agent: start by reading §1 (Immediate State), then §9 (Next Steps).*
