# Elite Model Deep Dive: From Data to Alpha

> Architectural reference + improvement roadmap for the Polymarket AI V2 prediction system.
> Generated 2026-03-01 from codebase analysis + third-party political prediction market research.

---

## Table of Contents

- **Part I: Current Architecture** (Sections 1-12)
- **Part II: Elevation Items** (Sections 13-21)
- **Part III: Design Principles & Roadmap** (Sections 22-23)

---

# PART I: CURRENT ARCHITECTURE

## 1. Pipeline Overview

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│  POLYMARKET  │    │   50+ SIGNAL │    │   DATABASE   │    │    REDIS     │
│  CLOB/Gamma  │    │   SOURCES    │    │  PostgreSQL  │    │    CACHE     │
│  WebSocket   │    │  (news,social│    │              │    │              │
│              │    │  trends,on-  │    │              │    │              │
│              │    │  chain,etc.) │    │              │    │              │
└──────┬───────┘    └──────┬───────┘    └──────┬───────┘    └──────┬───────┘
       │                   │                   │                   │
       ▼                   ▼                   ▼                   ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        BASE ENGINE (2092 lines)                         │
│  11-level initialization · 40+ subsystems · Event bus · Health runner   │
├─────────────────────────────────────────────────────────────────────────┤
│                                                                         │
│  ┌─────────────────┐  ┌──────────────────┐  ┌────────────────────────┐ │
│  │ DATA INGESTION  │  │ SIGNAL INGESTION │  │   LEARNING ENGINE      │ │
│  │ Markets, prices │  │ News, sentiment  │  │   Elite detection      │ │
│  │ Category infer  │  │ Whale, orderflow │  │   Calibration          │ │
│  │ Volume filter   │  │ Google trends    │  │   Feedback loop        │ │
│  └────────┬────────┘  └────────┬─────────┘  └───────────┬────────────┘ │
│           │                    │                         │              │
│           ▼                    ▼                         ▼              │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              PREDICTION ENGINE (3213 lines)                     │   │
│  │                                                                 │   │
│  │  FEATURES (46-50)          MODELS (11 ensemble)                 │   │
│  │  ├─ Base (6)               ├─ RandomForest                      │   │
│  │  ├─ Elite direction (4)    ├─ XGBoost                           │   │
│  │  ├─ Signal (2)             ├─ GradientBoosting                  │   │
│  │  ├─ Clarity (1)            ├─ LogisticRegression                │   │
│  │  ├─ Path summary (8)       ├─ ExtraTrees                        │   │
│  │  ├─ Regime (2)             ├─ HistGradientBoosting              │   │
│  │  ├─ FeatureEngineer (14)   ├─ LightGBM                          │   │
│  │  ├─ Time cyclical (6)      ├─ CatBoost                          │   │
│  │  └─ TA indicators (3)      ├─ Ridge + KNN + MLP                  │   │
│  │                             └─ TabPFN (optional)                 │   │
│  │                                                                  │   │
│  │  INFERENCE: 3-tier cache → Scale → predict_proba → Extremize    │   │
│  │  TRAINING: Walk-forward 80/20 · Class-balanced · Drift detect   │   │
│  └──────────────────────────────┬──────────────────────────────────┘   │
│                                 │                                      │
│                                 ▼                                      │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              ENSEMBLE BOT (10-step confidence pipeline)         │   │
│  │                                                                 │   │
│  │  ML prediction → LLM nudge → Alpha decay → FLB correction →    │   │
│  │  Lifecycle penalty → Drift penalty → Sentiment/VPIN/Clarity     │   │
│  │  multipliers → Edge filter → Category min edge                  │   │
│  └──────────────────────────────┬──────────────────────────────────┘   │
│                                 │                                      │
│                                 ▼                                      │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              RISK MANAGER (universal gates for ALL 12 bots)     │   │
│  │                                                                 │   │
│  │  Volume gate · Edge filter · Kelly sizing · Position limits ·   │   │
│  │  Exposure caps · Consecutive loss guard · CVaR tail risk ·      │   │
│  │  Oracle manipulation · Kill switches ($50/day, $150/week)       │   │
│  └──────────────────────────────┬──────────────────────────────────┘   │
│                                 │                                      │
│                                 ▼                                      │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              ORDER GATEWAY (15-layer decision pipeline)          │   │
│  │                                                                 │   │
│  │  Kill switch → Canary scaling → Drawdown ctrl → Adverse select  │   │
│  │  → RL timing → NegRisk defense → Risk check → Cascade detect   │   │
│  │  → Liquidity → Paper balance → Trade coordinator reserve →      │   │
│  │  Orderbook analysis → Execute (paper or live CLOB)             │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 11-Level Initialization Hierarchy (base_engine.py)

| Level | Components | Purpose |
|-------|-----------|---------|
| 1 | PolymarketClient, Database, Redis | Core infrastructure |
| 2 | DataIngestion, UnifiedMarketService, SmartDataFetcher | Data services |
| 3 | PredictionEngine, RiskManager, ExecutionEngine, BacktestEngine | Core engines |
| 4 | HealthMonitor, AlertingSystem, DataQualityMonitor, AutoHealer | Monitoring |
| 5 | MarketRegimeDetector, MultiTimeframeAnalyzer, CorrelationStrategy | Analysis |
| 6 | PortfolioRebalancer, DynamicPositionSizing | Portfolio/risk |
| 7 | AdvancedOrderManager, AutomatedPositionManager | Execution |
| 8 | SignalIngestion, WhaleTracker, WebSocketManager, EventBus | Signals/streaming |
| 9 | DrawdownController, OrderBookTracker, TradeFlowAnalyzer | Risk/analysis |
| 10 | ResolutionRiskAnalyzer, SmartOrderRouter, LiquidityGuardian, CopyTrading | Roadmap items |
| 11 | HistoricalDataWarehouse, PaperTradingEngine, ABTestingFramework | Lower priority |

### Key Wiring Points
- `prediction_engine._redis_cache = self.cache` (line 436)
- `prediction_engine._resolution_risk_analyzer = self.resolution_risk_analyzer` (line 641)
- WebSocket → `market_index_resolver` callback for condition_id translation (line 579)
- Streaming persister → whale tracker fast-path (<1ms callback) (line 589)

---

## 2. Data Foundation

**File**: `base_engine/data/data_ingestion.py` (3093 lines)

### Ingestion Flow
1. **Fetch markets** via PolymarketClient (filter: min liquidity, active, volume > 0)
2. **Volume filter**: Removes 0+0 zombie markets (Session 34 fix)
3. **Category inference**: `_infer_category()` (line 57) — keyword matching against 8 categories
4. **Parse fields**: Normalize 5 endDateIso variants (`endDateISO`, `endDateIso`, `endDate`, `end_date`, `end_date_iso`)
5. **Slug normalization**: Empty string → NULL (avoids UniqueViolation on ix_markets_slug)
6. **Price history**: Try `interval=max` first, fallback to 30-day chunks, 3 retries with backoff
7. **Write to DB**: Markets, MarketPrice (bulk insert), Users (aggregated stats)

### Database Schema (Core Tables)

| Table | Key Columns | Purpose |
|-------|------------|---------|
| `markets` | id, question, category, yes_price, no_price, liquidity, volume, resolution, resolved_at | Market state |
| `market_prices` | market_id, token_id, price, timestamp | Price time series |
| `trades` | market_id, user_address, side, price, size, timestamp | Historical trades |
| `users` | address, win_rate, profit, is_elite, trade_count | Trader profiles |
| `prediction_log` | market_id, predicted_prob, market_price, model_name, feature_snapshot | Prediction audit trail |
| `paper_trades` | bot_name, market_id, side, price, size, resolution | Simulated trades |
| `positions` | bot_id, market_id, side, size, entry_price, status | Open/closed positions |
| `trade_signals` | market_id, signal_source, direction, confidence | Signal metadata for ML |

---

## 3. Signal Sources (50+ Integrated)

### News & Information
| Source | Module | Update Freq | Cost |
|--------|--------|-------------|------|
| GDELT 2.0 | `signals/gdelt_client.py` | 15 min | Free |
| NewsAPI | `signals/news_sources.py` | On-demand | Free/Paid |
| 17 RSS feeds (BBC, NYT, AP, NPR, Politico, Guardian, etc.) | `signals/news_sources.py` | ~15 min | Free |

### Social Media
| Source | Module | Update Freq | Cost |
|--------|--------|-------------|------|
| Twitter/X API v2 | `signals/social_sources.py` | Streaming | Bearer token |
| Reddit (PRAW + JSON fallback) | `signals/social_sources.py` + `reddit_monitor.py` | Streaming | Free |
| Discord | `signals/discord_telegram_monitor.py` | Streaming | Bot token |
| Telegram | `signals/discord_telegram_monitor.py` | Streaming | API key |
| 4chan | `signals/fourchan_poller.py` | Polling | Free |
| BlueSky | `signals/social_sources.py` | Polling | Free |

### Trends & Sentiment
| Source | Module | Update Freq | Cost |
|--------|--------|-------------|------|
| Google Trends (anti-ban hardened) | `signals/google_trends.py` | On-demand | Free |
| Wikipedia pageviews | `signals/wikipedia_pageviews.py` | Daily | Free |
| VADER sentiment | `sentiment/sentiment_analyzer.py` | Real-time | Free |
| Sentiment velocity | `signals/sentiment_velocity.py` | Real-time | Free |

### On-Chain & Whale
| Source | Module | Update Freq | Cost |
|--------|--------|-------------|------|
| Dune Analytics | `signals/dune_analytics.py` | On-demand | Free (2500 credits/mo) |
| Whale tracker (>$10K) | `signals/whale_tracker.py` | Real-time via WS | Free |
| Polygon RPC (QuickNode/Alchemy) | blockchain client | Real-time | Tier-based |

### Regulatory & Cross-Platform
| Source | Module | Update Freq | Cost |
|--------|--------|-------------|------|
| CFTC/SEC RSS | `monitoring/regulatory_monitor.py` | Hourly | Free |
| Kalshi API | `cross_platform_arb.py` | Real-time | Free |
| Manifold Markets | integrations | On-demand | Free |
| NOAA weather | `signals/noaa_data.py` | 6h | Free |

### Anomaly Detection
| Component | Module | Method |
|-----------|--------|--------|
| Spike detector | `signals/spike_detector.py` | Multi-source Z-score |
| Velocity engine | `signals/velocity_engine.py` | Message rate-of-change |
| River streaming | drift detection | ADWIN + HalfSpaceTrees |

---

## 4. Feature Engineering (46-50 Features)

### Base Features (6) — Always computed
```
price              — Current YES token price (0.0-1.0)
liquidity          — Pool liquidity in USD
volume             — 24h trading volume
user_win_rate      — 30-day rolling win rate (fallback: lifetime if <5 resolved)
user_profit        — Cumulative P&L
category_encoded   — Top-7 categories label-encoded 1-7, else 0
```

### Elite Direction Features (4) — LATERAL JOIN for temporal safety
```
elite_net_direction  — Weighted elite trader bias (all time windows)
elite_direction_1h   — Elite direction in last 1 hour
elite_direction_6h   — Elite direction in last 6 hours
elite_direction_24h  — Elite direction in last 24 hours
```
**Critical**: LATERAL JOIN ensures old trades only see elite activity BEFORE their timestamp (prevents temporal leakage).

### Signal Features (2)
```
signal_confidence       — From trade_signals table (default 0.5)
signal_direction_encoded — YES/BUY=1.0, NO/SELL=0.0, NULL=0.5
```

### Resolution Clarity Score (1) — Tier 2 #16
```
clarity_score — LLM-rated 0.0 (ambiguous) to 1.0 (crystal clear)
               Blended: 60% Anthropic LLM + 40% regex-based score
               Cached: 2000 entries, 24h TTL
               Default: 0.7 for historical rows
```

### Path Summary Features (8)
```
path_min         — Minimum price in lookback window
path_max         — Maximum price in lookback window
path_final       — Last price before trade timestamp
path_volatility  — Standard deviation of returns
path_drawdown    — Maximum peak-to-trough decline
time_above_entry — Fraction of time price was above entry
max_run_up       — Largest consecutive upward move
max_run_down     — Largest consecutive downward move
```

### Regime Features (2) — 30-day rolling window
```
regime_trend      — Price trend direction/strength
regime_volatility — Realized volatility level
```

### FeatureEngineer Advanced (14)
```
MA-5, MA-10, MA-20          — Moving averages at 3 horizons
RSI                          — Relative Strength Index
bollinger_position           — Price position within Bollinger bands
volatility                   — N-day realized vol
price_percentile             — Rank within recent price range
price_change_1d/3d/7d       — Returns at 3 horizons
volume_change                — Volume momentum
spread                       — Bid-ask spread
momentum                     — Price momentum composite
mean_reversion_signal        — Distance from moving average
```

### Time Features (6) — Cyclical sin/cos encoding
```
dow_sin, dow_cos       — Day of week (no edge discontinuity Mon→Sun)
hour_sin, hour_cos     — Hour of day
market_age             — min(1.0, (now - created) / 365)
time_to_expiry         — min(1.0, (end - now) / 365)
```

### Technical Analysis (3)
```
RSI_14              — 14-period RSI
bollinger_position  — 0-1 within bands
ATR_normalized      — Average True Range / price
```

---

## 5. Training Pipeline

### Three Data Sources

| Source | Table | Weight | Purpose |
|--------|-------|--------|---------|
| Resolved trades | `trades` JOIN `markets` | 1.0× | Primary ground truth |
| Paper trades | `paper_trades` JOIN `markets` | 0.5× | Simulation feedback |
| Prediction log | `prediction_log` JOIN `markets` | 0.3× | Self-learning |

### Label Generation (Session 34 F1 — Critical Fix)

**Correct** (current): `m.resolution` — actual market outcome
```sql
outcome = CASE WHEN m.resolution = 'YES' THEN 1
               WHEN m.resolution = 'NO' THEN 0
               ELSE NULL END
```
**Wrong** (pre-Session 34): `pl.was_correct` — self-reinforcing (model trains on its own predictions)

### 4-Layer Temporal Leakage Prevention

1. **Resolution cutoff**: Only trades BEFORE `m.resolved_at`
2. **Convergence zone exclusion**: Drop trades within 6h of resolution (prices converge to outcome)
3. **Walk-forward split**: Train on 80% oldest, validate on 20% newest
4. **Point-in-time LATERAL JOINs**: Elite/user stats computed relative to EACH trade's timestamp

### Sample Weighting
```
Base weight = log1p(volume)               — clipped [0.1, 10.0]
× elite_multiplier (1.35 standard, 1.55 high-vol+high-return)
× source_discount (paper=0.5, prediction_log=0.3)
× P&L boost (correct+high_pnl=1.5×, wrong+big_loss=1.3× penalty)
× "Why wrong" boost (high confidence+high edge wrong=1.3×)
```

### Feature Removed (Session 34 F2)
`resolution_source_encoded` — removed from BOTH training and inference (was lookahead feature).

---

## 6. Model Ensemble (11 Models)

| Model | Library | Key Config | Purpose |
|-------|---------|-----------|---------|
| RandomForest | sklearn | n_est=100, depth=10, balanced | Tree baseline |
| XGBoost | xgboost | n_est=100, depth=6, scale_pos_weight | Fast gradient boosting |
| GradientBoosting | sklearn | n_est=100, depth=5 | Sequential trees |
| LogisticRegression | sklearn | C=1.0, balanced | Linear boundary |
| ExtraTrees | sklearn | n_est=100, depth=8 | Random splits |
| HistGradientBoosting | sklearn | max_iter=100, depth=5 | LightGBM-equivalent |
| LightGBM | lightgbm | n_est=100 | Leaf-wise growth |
| CatBoost | catboost | iterations=100 | Ordered boosting |
| Ridge | sklearn | alpha=1.0, calibrated | L2 regularization |
| KNN | sklearn | k=min(15, n/20) | Instance-based |
| MLP | sklearn | hidden=(64,32), adam | Neural network |
| TabPFN | tabpfn | n_est=4 (optional) | Transformer foundation |

### 3-Stage Ensemble Weighting
1. **Rank-based initial**: `(rank+1)^1.5` — nonlinear, less outlier-dominated
2. **Anti-domination cap**: No model > 20% of ensemble weight
3. **A3 Ridge stacking**: OOF-trained Ridge on holdout predictions (if ≥3 models, ≥50 samples)

### Validation Gates
- **Majority test (T9)**: ≥50% of models must beat DummyClassifier (majority-class predictor)
- **Brier rollback**: New ensemble Brier vs old — tolerance 0.02, reject if worse
- **Bias checks**: Price parroting detection, base rate verification

### Post-Training Transforms
- **Extremization**: Log-odds scaling by factor 1.4 → 60%→66%, 70%→78%, 80%→87%
- **Final blend**: 60% ensemble + 40% learning_confidence (configurable)
- **Model cache**: Saved to `data/model_cache.pkl` (backup `.bak` before each retrain)

---

## 7. Inference & Caching

### 3-Tier Cache Architecture

| Tier | Store | TTL | Max Size | Latency |
|------|-------|-----|----------|---------|
| L1 | Redis | 30s | — | ~1ms |
| L2 | In-memory `_prediction_cache` | 300s | 2000 | <1ms |
| FV | In-memory `_feature_vector_cache` | 300s | 2000 | <1ms |

### Prediction Latency Paths

| Path | Condition | Latency | DB Queries |
|------|-----------|---------|------------|
| Fast | L1/L2 cache hit | <5ms | 0 |
| Medium | FV cache hit, price within 3% | 10-20ms | 0 |
| Slow | FV cache miss | 100-500ms | 1 consolidated session |

### Feature Cache Warming
- **Delay**: 150s after boot (allows EnsembleBot cold scan to complete)
- **Frequency**: Every 60s via `_feature_precompute_loop()`
- **Concurrency**: `Semaphore(2)` to limit DB pool pressure
- **Gate**: `_feature_cache_warmed` flag must be True before prediction_log writes

### All ML Inference
- `predict_proba()` and `model.fit()` run via `asyncio.to_thread()` (Session 34 F6/F7)
- NaN/Inf sanitization before every predict call
- Feature snapshot + SHA-256 hash stored in prediction_log for integrity

---

## 8. Confidence Pipeline (EnsembleBot — 10 Steps)

```
                     ML ENSEMBLE
                         │
            ┌────────────┴────────────┐
            │  Weighted model average  │
            │  (10+ models × weights)  │
            └────────────┬────────────┘
                         │
            ┌────────────┴────────────┐
    Step 1  │  Side adjustment        │  Invert for NO token
            └────────────┬────────────┘
                         │
            ┌────────────┴────────────┐
    Step 2  │  LLM nudge              │  90% ML + 10% LLM estimate
            └────────────┬────────────┘
                         │
            ┌────────────┴────────────┐
    Step 3  │  Alpha decay            │  exp(-0.5 × hours_stale)
            └────────────┬────────────┘
                         │
            ┌────────────┴────────────┐
    Step 4  │  Model disagreement     │  IQR → size scaling [0.3, 1.0]
            └────────────┬────────────┘
                         │
            ┌────────────┴────────────┐
    Step 5  │  Favorite-longshot bias │  Category-scaled Becker 2026
            │  (+0.02-0.03 additive)  │  World Events 7.32pp, Finance 0.17pp
            └────────────┬────────────┘
                         │
            ┌────────────┴────────────┐
    Step 6  │  Lifecycle penalty      │  Young (<48h) low-prob YES: -0.03
            └────────────┬────────────┘
                         │
            ┌────────────┴────────────┐
    Step 7  │  Partition dependence   │  New (<24h) + thin (<$5K): -0.04
            └────────────┬────────────┘
                         │
            ┌────────────┴────────────┐
    Step 8  │  SUPER relay (drift)    │  High-surprise markets: 0.9×
            └────────────┬────────────┘
                         │
            ┌────────────┴────────────┐
    Step 9  │  Post-multipliers       │  Sentiment, event calendar,
            │  (asyncio.gather)       │  clarity (0.85-1.0×),
            │                          │  VPIN toxicity (0.75× if toxic),
            │                          │  B8 momentum OFI proxy
            └────────────┬────────────┘
                         │
            ┌────────────┴────────────┐
   Step 10  │  Signal enhancements    │  MultiplierAggregator [0.3, 2.0]
            │  (BaseBot layer)        │  Signal ingestion ±1.2×/0.6×
            │                          │  Order flow ±1.1×/0.85×
            │                          │  Google Trends +1.05×
            └────────────┬────────────┘
                         │
                         ▼
                 FINAL CONFIDENCE
```

### Adaptive Confidence Threshold
- Base: `ENSEMBLE_MIN_CONFIDENCE = 0.55`
- Hysteresis bands: loosen at accuracy > 0.68, tighten at < 0.42
- Exponential smoothing: 80% old + 20% new
- Bounds: [base×0.85, 0.90]
- Updates every 5 minutes

---

## 9. Edge & Trade Decision

### Edge Calculation
```
gross_edge = confidence - market_price
```

### Category-Specific Minimum Edges
```python
ENSEMBLE_CATEGORY_MIN_EDGES = {
    "politics": 0.15,
    "crypto":   0.12,
    "sports":   0.08,
    "science":  0.10,
    "weather":  0.08,
    # ...
}
```

### Price-Dependent Edge Scaling
```
if price > 0.90: min_edge *= 3.0    # Extreme prices = terrible risk/reward
if price > 0.80: min_edge *= 2.0
```

### CLOB Spread Deduction
```
spread = best_ask - best_bid
if spread > MAX_SPREAD_PCT (0.10): REJECT
net_edge = gross_edge - (spread / 2.0)   # Half-spread = entry cost
if net_edge < min_edge: REJECT
```

---

## 10. Risk Management (40+ Checks)

### Universal Gates (risk_manager.py — ALL 12 bots)

| Gate | Default | Effect |
|------|---------|--------|
| Min confidence | 0.55 | Block low-confidence |
| Min edge | RISK_MIN_EDGE_PCT | Block insufficient alpha |
| Volume gate | $5,000 | Block thin markets (1h TTL cache) |
| Max position | $100 | Cap per-position risk |
| Max total exposure | $500 | Portfolio-level cap |
| Max daily exposure | 10% of capital | Daily limit |
| Max positions per bot | configurable | Concentration limit |
| Consecutive losses | 3 | Pause on loss streaks |
| Daily loss limit | $50 | Triggers kill switch |
| Weekly loss limit | $150 | Triggers kill switch |
| CVaR tail risk | $200 portfolio max | Rejects high-tail-risk trades |
| Oracle manipulation | risk_score > 0.8 | Blocks UMA-vulnerable markets |
| PipelineGate | data freshness | Blocks stale data (warn in sim) |

### Kelly Criterion Position Sizing
```
b = (1.0 - price) / price                    # Decimal odds - 1
kelly_full = (confidence * b - (1-confidence)) / b
fraction = KELLY_FRACTION / KELLY_ACTIVE_BOTS  # 0.25 / 4 = 0.0625

kelly_frac = kelly_full × fraction
           × calibration_quality              # Brier > 0.30 → halved
           × drawdown_compression             # 5% DD → 0.8×, 10% → 0.6×
           × (1.0 / (1.0 + market_vol × 2.0)) # Volatility scaling
           × edge_scale                        # min(1.0, edge/0.15)

position_usd = kelly_frac × available_capital
             = min(position_usd, MAX_POSITION_SIZE_PCT × capital)
             = min(position_usd, RISK_MAX_POSITION_SIZE_USD × edge_scale)
```

### 15-Layer OrderGateway Pipeline

1. Multi-layer kill switch (bot, portfolio, system)
2. Canary deployment scaling (5%→25%→50%→100%)
3. SELL order detection (bypass most pre-trade filters)
4. Drawdown controller (graduated reduction)
5. Adverse selection sizing (0.5-1.0× multiplier)
6. RL trade timing agent (TRADE_NOW / WAIT / SKIP)
7. NegRisk defense (block BUY on multi-outcome)
8. Risk manager check (full `check_risk_limits()`)
9. Cascade + liquidity checks (parallel)
10. Paper trading balance check
11. NegRisk exit check (warn on sell)
12. Trade coordinator reserve (atomic)
13. Adverse selection gate (min profitable spread)
14. OrderBook analysis (improve limit price)
15. Execute (paper DB record OR live CLOB)

---

## 11. Drift Detection & Self-Healing

### 6 Detection Methods

| Method | What It Detects | Trigger |
|--------|----------------|---------|
| PHT (Page-Hinkley) | Abrupt accuracy shift | Cumulative error > λ=50 |
| Distribution shift | Prediction mean drift | Z-score > 2.0 |
| Confidence collapse | Model uncertainty spike | >60% predictions near 0.5 |
| Calibration drift | Sustained poor accuracy | Recent acc < 0.45 (≥30 outcomes) |
| ADWIN | Adaptive accuracy drift | Statistical change point (δ=0.01) |
| Mann-Whitney U | Covariate distribution shift | P-value < 0.05 |

### Auto-Retrain Triggers
```
IF AUTO_RETRAIN_ON_DEGRADATION:
  degradation = (brier > 0.30) OR (accuracy < 0.45) OR (any drift detected)
  IF degradation: force_retrain_now()
  cooldown: 1 hour between retrains
  post-retrain: reset drift only if new accuracy ≥ 0.45
```

---

## 12. Feedback Loop (Closed-Loop Learning)

```
┌─────────────┐     ┌──────────────┐     ┌─────────────────┐
│ Predictions │────▶│ prediction   │────▶│ Resolution      │
│ (real-time) │     │ _log table   │     │ backfill        │
└─────────────┘     └──────────────┘     │ (30min mini,    │
                                          │  daily full)    │
┌─────────────┐     ┌──────────────┐     └────────┬────────┘
│ Paper       │────▶│ paper_trades │              │
│ trades      │     │ table        │              │
└─────────────┘     └──────────────┘              │
                                                   ▼
                    ┌──────────────────────────────────────┐
                    │ TRAINING DATA (3-way merge)          │
                    │ resolved trades + paper + pred_log   │
                    │ with sample weights                  │
                    └──────────────────┬───────────────────┘
                                       │
                    ┌──────────────────┴───────────────────┐
                    │ RETRAIN (every 6h or on drift)       │
                    │ Gate: ≥20 resolved predictions       │
                    │ PipelineGate: data fresh + sufficient │
                    │ M7 Guard: no concurrent retrains     │
                    └──────────────────┬───────────────────┘
                                       │
                    ┌──────────────────┴───────────────────┐
                    │ VALIDATE → DEPLOY                    │
                    │ Majority beat DummyClassifier         │
                    │ Brier rollback check (tolerance 0.02)│
                    │ Save to model_cache.pkl               │
                    └──────────────────────────────────────┘
```

### Scheduling
- **Learning scheduler**: Every 6h retrain cycle + drift-triggered retrains
- **Ingestion scheduler**: Every 5min market ingestion, daily full, weekly full refresh
- **Mini backfill**: Every 30min — backfill prediction_log + paper_trades outcomes
- **Feature precompute**: Every 60s — refresh feature vector cache for all active markets
- **Health runner**: Every 60min — 10 parallel health checks

---

# PART II: ELEVATION ITEMS

> Actionable improvements derived from third-party analysis of profitable Polymarket strategies,
> academic research, and gap analysis against the existing system.

---

## 13. New Data Sources to Integrate

### 13a. Polling Data Pipeline (P0 — Highest Priority)

**Why this is the #1 missing edge**: Academic research consistently shows polling aggregation models beat raw market prices in less-liquid races. One trader made $85M on the 2024 election by commissioning proprietary polls identifying systematic bias in public data. Only 0.51% of Polymarket wallets ever exceed $1,000 profit — information edge is the differentiator.

**Sources to integrate**:

| Source | API | Cost | Update Freq | Data |
|--------|-----|------|-------------|------|
| VoteHub API | REST | Free | Daily | Hundreds of pollsters, sample sizes, partisan flags |
| FiveThirtyEight GitHub | CSV/JSON | Free (CC-BY-4.0) | Per election | Historical polls + model outputs |
| Cook Political Report PVI | Subscription | Paid | Per cycle | 435 congressional district ratings |

**Implementation**: New `base_engine/signals/polling_client.py`
- Daily fetch → `polls` table (pollster_id, sample_size, population_type, partisan_lean, margin_of_error, date)
- Wire into prediction_engine as new features (see Section 21a)

### 13b. Legislative Intelligence (P2)

**Sources**:

| Source | API | Cost | Update Freq | Data |
|--------|-----|------|-------------|------|
| Congress.gov API | REST | Free | 6×/daily | Bills, votes, committees, House Roll Call (May 2025+) |
| ProPublica Congress | REST | Free (5K/day) | 30min votes, 6×/day bills | Votes, bills, member data |
| LegiScan | REST | Enterprise | 15min | All 50 states + Congress |

**Implementation**: New `base_engine/signals/legislative_tracker.py`
- Monitor bills/votes affecting active prediction markets
- Extract trading signals from voting patterns and sponsor counts

### 13c. Court & Executive Action Monitoring (P3)

**Sources**:

| Source | API | Cost | Data |
|--------|-----|------|------|
| CourtListener (Free Law Project) | REST | Free | 9M+ court decisions, SCOTUS data |
| Federal Register API | REST | Free | Executive orders, regulations, presidential documents |

**Key timing**: SCOTUS opinions at 10am ET on non-argument days, bulk by mid-June.

**Implementation**: New `base_engine/signals/court_monitor.py`

### 13d. International Election Data (P3)

| Source | API | Cost | Data |
|--------|-----|------|------|
| IFES ElectionGuide | By request | Free | 240 countries, 93 datapoints/election |
| International IDEA | Public | Free | Comparative election databases |

**Note**: OpenSecrets API was discontinued April 15, 2025. FEC API provides raw federal campaign finance data as alternative.

---

## 14. Bayesian Polling Model (P0 — The Durable Edge)

### The Gelman/Goodrich/Han Approach (Used by The Economist)

This is the highest-alpha improvement available. Properly designed aggregation models beat prediction markets on 85% of questions tested (IARPA ACE Tournament).

### Architecture

```
┌─────────────────────────────────────────────────┐
│              FUNDAMENTALS PRIOR                  │
│                                                  │
│  Abramowitz "Time for Change" model:             │
│  • Q2 GDP growth                                 │
│  • Incumbent approval at mid-year                │
│  • Term penalty (party wins 7/9 first-term,     │
│    only 2/10 after two+ terms)                  │
│  • R² = 0.82 for popular vote                   │
│                                                  │
│  Midterm base rate:                              │
│  • President's party loses seats 90% of time    │
│  • Average: 26-28 seats lost overall            │
│  • Approval <50%: 37-43 seats lost              │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│              POLL UPDATING                        │
│                                                  │
│  Each new poll updates posterior via correlated  │
│  random walk across states/districts             │
│                                                  │
│  Poll observations = binomial draws adjusted for │
│  • Population type (LV > RV > adults)           │
│  • Polling mode (phone/online/automated)        │
│  • Pollster house effects                       │
│  • Sponsor partisan lean                        │
│  • Sample size (√n weighting, cap 1500)         │
│                                                  │
│  State-level errors: multivariate normal        │
│  (captures correlated polling biases)           │
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│              POLL WEIGHTING                       │
│                                                  │
│  Recency: EWMA early → local polynomial late    │
│  Quality: Predictive Plus-Minus per pollster    │
│  Herding: Flag clustering near average          │
│  House effects: Partial correction (keep signal)│
└──────────────────────┬──────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────┐
│              OUTPUT                               │
│                                                  │
│  polling_model_prob per market                   │
│  poll_market_divergence = |prob - market_price|  │
│  → TRADE when divergence > threshold             │
└─────────────────────────────────────────────────┘
```

### Key Insight for Trading
Poll impact on prices **declines over time** as information accumulates. Early-cycle polls have disproportionate value → largest model-vs-market divergences occur when fundamentals-weighted models identify mispricings that polls haven't yet confirmed. This is when edge is largest.

### Implementation
- **Library**: PyMC for Bayesian MCMC (or Stan)
- **Where to wire**: New feature `polling_model_prob` in prediction_engine, blend weight via settings
- **Calibration target**: FiveThirtyEight best Brier ~0.0375; Polymarket overall 0.0581 but >$1M markets 0.0256

---

## 15. Cross-Market Logical Arbitrage (P1)

### Three Relationship Types

| Type | Constraint | Example |
|------|-----------|---------|
| **Subset** | P(A) ≤ P(B) if A implies B | "Trump wins 2028" ≤ "Republican wins 2028" |
| **Mutual exclusivity** | Σ YES prices ≤ $1.00 | Multi-candidate nomination market |
| **Conditional probability** | P(A∩B) ≤ min(P(A), P(B)) | "Dems win popular vote AND presidency" |

### Detection Pipeline

```
1. Activate ChromaDB (currently commented out in requirements.txt)
   + e5-large-v2 sentence embeddings

2. Vector similarity search → group related markets
   (1500+ markets → semantic clusters)

3. LLM relationship extraction → identify logical implications
   (Claude classifies: subset / exclusive / conditional / independent)

4. Constraint check → flag violations as arbitrage opportunities

5. Profitability filter → spread must exceed ~2.5-3%
   (2% winner fee + gas ~$0.007 + slippage)

6. Multi-leg execution → 2-3 leg atomic trades
```

### Evidence
- $40M documented arbitrage profits from Polymarket (April 2024 - April 2025)
- Top 3 wallets captured $4.2M from 10,200+ bets
- Political markets were most profitable category for combinatorial arbitrage
- Simple YES/NO rebalancing closes in ~200ms (dominated by bots)
- Combinatorial spreads: 1-5% returns per trade

### Cross-Platform Caution
- Polymarket vs Kalshi spreads: 3-5% during 2024 election
- **Resolution risk**: During 2024 government shutdown, Polymarket resolved YES while Kalshi resolved NO on same event
- Only 5 of 13 identified cross-platform arb candidates delivered returns

**Implementation**: New `base_engine/analysis/logical_arbitrage.py` + `bots/logical_arb_bot.py`

---

## 16. Multi-LLM Consensus Validation (P1)

### Current State
Sequential fallback: Claude → GPT-4 → Gemini (in `llm_probability.py`)

### Target State
Parallel consensus with disagreement flagging:

```
                    ┌───────────┐
          ┌────────▶│  Claude   │────────┐
          │         └───────────┘        │
          │                              │
Market ───┼────────▶┌───────────┐        ├──▶ Median / Vote
question  │         │  GPT-4   │────────┤
          │         └───────────┘        │
          │                              │
          └────────▶┌───────────┐        │
                    │  Gemini   │────────┘
                    └───────────┘

          High disagreement → flag for review / reduce position
```

### Why It Matters
- Adversarial headline manipulation reduces single-LLM returns by up to 17.7 percentage points
- Multi-source corroboration essential before acting on unverified breaking news
- Consensus reduces hallucination risk

### Implementation
- Modify `llm_probability.py`: `LLM_CONSENSUS_MODE = "fallback" | "parallel_vote" | "median"`
- asyncio.gather for parallel calls, 5s timeout per model
- Disagreement metric: max spread across models → if > 0.15, reduce position size

---

## 17. Correlation-Aware Portfolio Enhancements (P2)

### Current State
- CVaR exists in `correlation_risk.py`
- Basic correlation matrix (30-day lookback)
- Bounded cache (5000 entries, 7-day TTL)

### Missing: PCA Factor Decomposition

UCLA/NBER research (Chernov, Elenev, Song 2024) found voter preferences have a **two-factor structure**. Failing to account for state correlations biases win probability by 10+ percentage points.

### Improvements

1. **PCA factor extraction** from market price covariance:
   - Factor 1: "Republican sweep" basket
   - Factor 2: "Democrat sweep" basket
   - Additional factors: geographic, policy theme, etc.

2. **Cluster exposure limits**: 15-20% total bankroll per correlated cluster
   - If holding "R wins presidency" + "R wins Senate" + "R wins House" → treat as single exposure

3. **Dynamic re-hedging**: Recalculate correlations after significant events (polls, debates, endorsements)

**Implementation**: Enhance `correlation_strategies.py` with PCA + cluster limits in `risk_manager.py`

---

## 18. Time-Horizon Capital Bucketing (P2)

### Current State
Single capital pool, Kelly sizing per trade.

### Target: Tiered Allocation

| Bucket | Resolution Horizon | Allocation | Rationale |
|--------|-------------------|------------|-----------|
| Short-term | <30 days | 40% | Higher turnover, faster feedback loop |
| Medium-term | 30-180 days | 35% | Political season trades |
| Long-term | >180 days | 5% | Structural positions only |
| Liquid reserve | — | 20% | Breaking news opportunities |

### Why It Matters
Political bets lock up capital for months. Without bucketing, the bot can become fully invested in long-dated positions with no dry powder for high-conviction breaking news opportunities.

**Implementation**: New `_capital_buckets` dict in `risk_manager.py`, classify markets by `time_to_expiry` feature.

---

## 19. Scheduled Event Pre-Positioning (P2)

### Current State
Event calendar extracts dates from market descriptions via regex. 1.05× confidence boost within 6h of event.

### Missing: Pre-Built Analysis Templates

| Event Type | Timing | Source |
|-----------|--------|--------|
| SCOTUS opinions | 10am ET non-argument days, bulk mid-June | supremecourt.gov |
| FOMC statements | 2:00pm ET exactly | federalreserve.gov |
| FOMC press conference | 2:30pm ET | federalreserve.gov |
| BLS/BEA economic data | Published annual schedule | bls.gov/bea.gov |
| Congressional floor votes | Real-time | live.house.gov |
| State of the Union | Annual, announced | whitehouse.gov |

### Implementation
Enhance `event_calendar.py` with:
- Hardcoded recurring schedules for known event types
- Pre-built LLM prompt templates per event type
- Market→event mappings maintained in DB
- T-minus countdown alerts (1h, 15min, 1min)
- Auto-execute prepared analysis when event fires (0-latency reaction)

---

## 20. 30-Second News Speed Optimization (P3)

### The Opportunity
When breaking political news hits, Polymarket prices take 30-60 seconds to adjust on liquid markets — longer on thinner ones. 10-20% of news breaks first on Twitter/X.

### Current Pipeline Latency
```
GDELT: 15 minutes          ← too slow for speed trading
RSS feeds: ~15 minutes     ← too slow
Twitter streaming: real-time ← good but unfiltered
```

### Target Pipeline
```
Twitter/X filtered stream (200 political journalists)
         │ (<1s)
         ▼
Claude Haiku fast screening (1-2s)
         │
         ▼
MinHash dedup across sources
         │
         ▼
Claude Opus complex assessment (3-5s)
         │
         ▼
Pre-mapped market→topic routing
         │
         ▼
Order execution (sub-second via CLOB)
         │
Total: <10 seconds end-to-end
```

### Cost Consideration
- Twitter/X Pro API: $5,000/month minimum for useful political monitoring
- Alternative: TwitterAPI.io ($0.15/1K tweets) — cheaper but legally risky
- Claude Haiku: ~$0.25/M input tokens (fast screening is cheap)

**Implementation**: Enhance `signal_ingestion.py` fast path with journalist watchlist, dedup, sub-10s LLM classification.

---

## 21. Additional Feature Engineering

### 21a. Polling-Derived Features (if Section 14 implemented)

| Feature | Source | Description |
|---------|--------|-------------|
| `polling_model_prob` | Bayesian model | Posterior probability from polling aggregate |
| `poll_market_divergence` | Computed | \|polling_prob - market_price\| — THE core edge signal |
| `poll_count_30d` | polls table | Information density (more polls = less uncertainty) |
| `pollster_agreement` | polls table | Variance across pollsters (high = uncertainty) |
| `fundamentals_prior` | Economic data | Abramowitz model output |

### 21b. Legislative Features (if Section 13b implemented)

| Feature | Source | Description |
|---------|--------|-------------|
| `bill_passage_prob` | Congress.gov | Committee vote + sponsor count + party control |
| `regulatory_activity` | Federal Register | Count of relevant entries in lookback window |

### 21c. Cross-Market Features (if Section 15 implemented)

| Feature | Source | Description |
|---------|--------|-------------|
| `related_market_consensus` | ChromaDB clusters | Average price of semantically similar markets |
| `logical_constraint_tension` | Constraint engine | Distance from constraint boundary (near = opportunity) |
| `cross_market_momentum` | Price correlation | Whether related markets are moving in same direction |

---

# PART III: DESIGN PRINCIPLES & ROADMAP

## 22. What Makes It Elite — 10 Core Principles

### 1. Temporal Integrity Everywhere
No future data leaks into training at any layer. Four safeguards: resolution cutoff, convergence zone exclusion, walk-forward split, point-in-time LATERAL JOINs.

### 2. Universal Guardrails
`risk_manager.check_risk_limits()` + `order_gateway.place_order()` are the enforcement points for ALL 12 bots. Universal gates here, NOT per-bot. This is non-negotiable.

### 3. Multi-Source Ensemble
ML models + LLM estimates + signal ingestion + sentiment + polling (future) + order flow + whale tracking. No single source dominates; diversity reduces model risk.

### 4. Adaptive Thresholds
Hysteresis-based confidence tuning with exponential smoothing. Not static cutoffs — the system tightens when losing and loosens when winning, preventing whipsaw.

### 5. Capital Preservation First
Quarter-Kelly sizing, drawdown compression, volatility scaling, loss kill switches. The bot should survive 100 trades of bad luck without going bust.

### 6. Self-Correcting
6 drift detection methods → auto-retrain on degradation → validation gates prevent deploying worse models. The system heals itself.

### 7. Domain Specialization
Deep category expertise beats broad shallow coverage. Top Polymarket wallets all concentrated in one domain. Category-specific min edges encode this.

### 8. Information Edge > Speed Edge
Proprietary polling models (Section 14) beat faster execution. The $85M whale won with better polls, not faster bots. Speed helps but information is the durable edge.

### 9. Quarter-Kelly Discipline
`KELLY_FRACTION = 0.25` acknowledges the market incorporates information you lack. Mathematically: your effective estimate is 25% your model + 75% market price. Half Kelly cuts volatility drastically while reducing returns only ~25%.

### 10. Closed-Loop Learning
Every prediction feeds back into training via prediction_log → resolution backfill → weighted training data. The system gets smarter with every resolved market.

---

## 23. Prioritized Improvement Roadmap

| Priority | Item | Expected Alpha | Effort | Section | New Files |
|----------|------|---------------|--------|---------|-----------|
| **P0** | Polling data + Bayesian model | Highest — beats markets in thin races | 2-3 weeks | 13a, 14 | `signals/polling_client.py`, `analysis/bayesian_model.py` |
| **P1** | Cross-market logical arbitrage | High — systematic $40M documented | 2 weeks | 15 | `analysis/logical_arbitrage.py`, `bots/logical_arb_bot.py` |
| **P1** | Multi-LLM consensus | Medium — reduces hallucination 17.7pp | 2-3 days | 16 | Modify `llm_probability.py` |
| **P2** | Legislative tracking | Medium — early policy signals | 1 week | 13b | `signals/legislative_tracker.py` |
| **P2** | PCA correlation clusters | Medium — prevents correlated blowups | 1 week | 17 | Enhance `correlation_strategies.py` |
| **P2** | Time-horizon bucketing | Medium — capital efficiency | 2-3 days | 18 | Enhance `risk_manager.py` |
| **P2** | Event pre-positioning | Medium — scheduled event alpha | 1 week | 19 | Enhance `event_calendar.py` |
| **P3** | Court/exec monitoring | Low-medium — niche valuable | 3-5 days | 13c | `signals/court_monitor.py` |
| **P3** | News speed optimization | Low-medium — 30s window compressing | 1 week | 20 | Enhance `signal_ingestion.py` |
| **P3** | International election data | Low — only when markets exist | 2-3 days | 13d | `signals/intl_elections.py` |

### 2026 Midterms Opportunity
The next major catalyst. The 90% base rate of president's party losing House seats, combined with hundreds of individual district markets expected on Polymarket, creates a target-rich environment. District-level markets will have thin liquidity and wide mispricings — exactly where polling models beat markets.

**Critical path**: P0 (polling model) must be operational before midterm markets open at scale.

---

## Key Statistics & Benchmarks

| Metric | Value | Source |
|--------|-------|--------|
| Polymarket 2025 volume | $33.4 billion | Platform data |
| Wallets >$1K profit | 0.51% | IMDEA study |
| 2024 election single-contract volume | $3.7 billion | Platform data |
| Documented arbitrage profits (Apr 2024 - Apr 2025) | $40 million | IMDEA study |
| Best Brier score (FiveThirtyEight) | ~0.0375 | Academic |
| Polymarket Brier (>$1M markets) | 0.0256 | Platform analysis |
| Polymarket Brier (overall 12h horizon) | 0.0581 | Platform analysis |
| IARPA: aggregation models beat markets | 85% of 113 questions | ACE Tournament |
| Abramowitz "Time for Change" R² | 0.82 | Academic |
| News speed arbitrage window | 30-60 seconds | Empirical |
| Adversarial headline LLM return reduction | 17.7 percentage points | Academic |
| Cross-platform arb spreads (2024) | 3-5% | IMDEA study |
| Arb spread compression (2023→2025) | 4.5% → 1.2% | Market data |
| Polymarket political market maker fees | 0% | Platform policy |
| Polymarket winning position fee | 2% | Platform policy |
| Gas cost per transaction (Polygon) | ~$0.007 | Network data |

---

*This document should be updated as new features are implemented and new research emerges.*
