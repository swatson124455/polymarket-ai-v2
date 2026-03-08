# EnsembleBot — Bot Reference

## Status (as of 2026-03-06)
| Field | Value |
|-------|-------|
| Enabled | YES (BOT_ENABLED_ENSEMBLE=true) — off in isolation mode |
| Capital | $8,000 (BotBankrollManager) |
| Max bet | $100 (max_bet_usd) |
| Max daily | $2,000 (max_daily_usd) |
| Kelly fraction | 0.25 base |
| VPS State | DISABLED in current isolation mode (only WeatherBot + EsportsBot active) |
| Last trade | Session 48 — first trades placed |
| Blocker | None when isolation lifted; model accuracy 53% (graduation needs Brier ≤ 0.22) |

## Purpose & Strategy
Primary directional trading bot. Trades all market categories using ML ensemble predictions.

**ML ensemble (11 models):** RF, XGBoost, GradientBoost, ExtraTrees, HistGradBoost, LightGBM, CatBoost, LogisticRegression, Ridge, KNN, MLP. Blended via `ENSEMBLE_BLEND` (default 1.0 = ignore learning_conf).

**Edge discovery flow:**
1. Scan 200 markets (configurable SCAN_MARKET_LIMIT) with semaphore concurrency=10
2. For each market: compute ML confidence, apply calibration + extremization
3. Edge = confidence - market_price (must exceed ENSEMBLE_MIN_EDGE=0.02 net of spread)
4. Relative spread check: spread/price ≤ 0.20 (ENSEMBLE_MAX_RELATIVE_SPREAD)
5. Category-specific edge minimums (JSON ENSEMBLE_CATEGORY_MIN_EDGES)
6. Price bounds: 0.15 ≤ price ≤ 0.90 (RISK_MIN_PRICE + RISK_MAX_PRICE)
7. Volume gate: 24h volume ≥ $5K (ENSEMBLE_MIN_MARKET_VOLUME_USD)

**Signal enhancements:**
- Price momentum: order flow inference via price velocity + volume acceleration
- Sentiment: 24h trade sentiment score (cached 600s)
- Event calendar: 1.05x confidence boost when scheduled event within 6h
- Partition dependence penalty: anchor bias reduction for <24h old, <$5K volume markets near 0.5
- Model disagreement penalty: -0.15 confidence if models diverge >0.20

**Anti-churn / progressive cooldown (Session 28, updated S47):**
- 1st exit: 5min cooldown (was 30min before S47)
- 2nd exit: 10min, 3rd: 20min... cap 1h (was 24h before S47)
- Tracks per-market `_exit_count[market_id]`

**Politics profit-taking (Session 38):**
- Exit when unrealized P&L ≥ 65% of max profit (POLITICS_EXIT_PCT)
- Minimum profit threshold: $2.00 (POLITICS_EXIT_MIN_PROFIT_USD)
- Can disable via POLITICS_EXIT_ENABLED=false

**WebSocket reactive path:**
- Reacts to WS price updates when price moves ≥ 0.5% (ENSEMBLE_WS_PRICE_CHANGE_PCT)
- O(1) market index lookup (in-memory, no DB query)
- Cooldown 10s per market (ENSEMBLE_WS_COOLDOWN_SECONDS)
- Kill switch checked before each reactive trade

## Key Files
| Purpose | Path |
|---------|------|
| Main bot | bots/ensemble_bot.py (~1897 lines) |
| Base bot (shared logic) | bots/base_bot.py |
| Bankroll manager | base_engine/risk/bankroll_manager.py |
| Prediction engine | base_engine/prediction/prediction_engine.py |
| Risk manager | base_engine/risk/risk_manager.py |
| Multiplier aggregator | base_engine/learning/multiplier_aggregator.py |

## Critical Code Paths
| Stage | Method | Approx Line |
|-------|--------|-------------|
| Main scan | scan_and_trade() | ~300 |
| Per-market analysis | analyze_opportunity() / _analyze_one_token() | ~1000 |
| Momentum signal | _get_price_momentum_signal() | ~700 |
| Sentiment score | _get_sentiment_score() | ~750 |
| Partition dependence | _check_partition_dependence() | ~800 |
| Politics profit-take | _check_politics_profit_taking() | ~850 |
| WS reactive trade | on_price_update() | ~1200 |
| Trade execution | _execute_ensemble_trade() | ~1100 |

## External Dependencies
| Dependency | Required | Notes |
|------------|----------|-------|
| Polymarket API | YES | Market data, price history |
| WebSocket | YES | Real-time price updates |
| ML model cache | YES | data/model_cache.pkl (16MB, 11 models) |
| PostgreSQL | YES | prediction_log, paper_trades, positions |
| Redis | NO | WS dedup, whale_alerts, FV cache |

## Configuration (env vars)
| Variable | Default | VPS Current | Purpose |
|----------|---------|-------------|---------|
| BOT_ENABLED_ENSEMBLE | true | false (isolation) | Enable gate |
| ENSEMBLE_MIN_CONFIDENCE | 0.55 | 0.45 | Min model confidence |
| ENSEMBLE_MIN_EDGE | 0.10 | 0.02 | Min net edge (after spread) |
| ENSEMBLE_MAX_RELATIVE_SPREAD | — | 0.20 | Max spread/price ratio (S49 fix) |
| ENSEMBLE_MAX_SPREAD_PCT | 0.10 | 0.10 | Max absolute spread |
| ENSEMBLE_SCAN_CONCURRENCY | 10 | 10 | Parallel market workers |
| ENSEMBLE_MIN_MARKET_VOLUME_USD | 5000 | 5000 | 24h volume gate |
| ENSEMBLE_WS_PRICE_CHANGE_PCT | 0.005 | 0.005 | WS reaction threshold |
| ENSEMBLE_WS_COOLDOWN_SECONDS | 5 | 10 | WS cooldown per market (s) |
| ENSEMBLE_EXIT_COOLDOWN_SECONDS | 1800 | 300 | Base exit cooldown (s) — S47: 30min→5min |
| ENSEMBLE_CATEGORY_MIN_EDGES | (JSON) | {"weather":0.03,"crypto":0.05,...} | Per-category min edges |
| ENSEMBLE_DISAGREEMENT_THRESHOLD | 0.20 | 0.20 | Model divergence threshold |
| ENSEMBLE_DISAGREEMENT_PENALTY | 0.15 | 0.15 | Confidence reduction on divergence |
| ENSEMBLE_BLEND | 1.0 | 1.0 | learning_conf blend weight (1.0=bypass) |
| POLITICS_EXIT_ENABLED | true | true | Enable politics profit-taking |
| POLITICS_EXIT_PCT | 0.65 | 0.65 | Exit at 65% of max profit |
| POLITICS_EXIT_MIN_PROFIT_USD | 2.0 | 2.0 | Min profit before exit |
| RISK_MIN_PRICE | 0.15 | 0.015 | Price floor (S49: raised to 15c) |
| RISK_MAX_PRICE | 0.90 | — | Price ceiling (S49: lowered to 90c) |
| MODEL_REVERSAL_THRESHOLD | 0.45 | 0.30 | Model reversal exit threshold (S49: 0.45→0.30) |
| PLATT_SCALING_ENABLED | false | false | Platt calibration (S50: disabled — interference) |
| EXTREMIZATION_FACTOR | 1.4 | 0.0 | Log-odds extremization (S50: set to 0.0) |

## Known Issues & Debug History
- **[Session 49 — FIXED]** 0% sell win rate: 3 root causes:
  1. Penny token churn (RISK_MIN_PRICE was 0.05 → raised to 0.15)
  2. Model reversal churn (MODEL_REVERSAL_THRESHOLD was 0.45 → lowered to 0.30)
  3. Absolute-only spread check → added ENSEMBLE_MAX_RELATIVE_SPREAD=0.20
- **[Session 48 — FIXED]** EnsembleBot first trades placed after multiple fixes.
- **[Session 47 — FIXED]** Kelly divisor shared across 10 bots → BotBankrollManager per-bot $8k capital.
  Cooldown 30min→5min. Edge 0.04→0.02.
- **[Session 50 — FIXED]** Model accuracy 49.6%: circular training disabled, EXTREMIZATION_FACTOR=0.0,
  ENSEMBLE_BLEND=1.0. Accuracy now 53%.
- **[OPEN]** Graduation: win_rate=53% (need ≥52% ✓), Brier=0.2418 (need ≤0.22 ✗), resolved=1619 (need ≥100 ✓).
  Blocked on Brier score only. Auto-retrain every 2h with clean data.
- **[OPEN]** PLATT_SCALING_ENABLED=false: Disabled in S50 (was interfering with accuracy fix).
  Re-enable when Brier < 0.22 and accuracy stable.
- **[OPEN]** EXTREMIZATION_FACTOR=0.0: Disabled in S50. Re-enable when accuracy > 55%.

## Debugging Commands
```bash
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
VPS="ubuntu@34.251.224.21"

# Live logs
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai -f | grep EnsembleBot"

# Model accuracy
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT COUNT(*) as resolved,
         ROUND(AVG(CASE WHEN was_correct THEN 1.0 ELSE 0.0 END)::numeric, 3) as win_rate,
         ROUND(AVG(POWER(predicted_prob - CASE WHEN was_correct THEN 1.0 ELSE 0.0 END, 2))::numeric, 4) as brier
  FROM prediction_log
  WHERE bot_name='EnsembleBot' AND was_correct IS NOT NULL;\""

# Recent trades P&L
ssh -i "$KEY" "$VPS" "sudo -u postgres psql -d polymarket -c \"
  SELECT DATE(created_at) as day, COUNT(*) as trades,
         SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins,
         ROUND(SUM(realized_pnl)::numeric, 2) as pnl
  FROM paper_trades
  WHERE bot_name='EnsembleBot' AND realized_pnl IS NOT NULL
  GROUP BY day ORDER BY day DESC LIMIT 10;\""

# Phase tracker status
ssh -i "$KEY" "$VPS" "sudo journalctl -u polymarket-ai --since '1 hour ago' | grep -i 'phase\|graduation\|brier\|win_rate'"

# Run EnsembleBot tests
pytest tests/ -k "ensemble" -v
```

## Next Steps / Blockers
- [ ] Re-enable when isolation mode lifted (set BOT_ENABLED_ENSEMBLE=true in VPS .env)
- [ ] Monitor auto-retrain: fires every 2h with clean data (RETRAIN_INTERVAL_HOURS=2)
- [ ] Graduation: need Brier ≤ 0.22 (currently 0.2418 — dropping slowly)
- [ ] Re-enable PLATT_SCALING_ENABLED=true once Brier < 0.22
- [ ] Re-enable EXTREMIZATION_FACTOR=1.4 once accuracy > 55%
- [ ] Consider USE_PER_BOT_MODELS=true when EnsembleBot has 200+ resolved prediction_log entries
