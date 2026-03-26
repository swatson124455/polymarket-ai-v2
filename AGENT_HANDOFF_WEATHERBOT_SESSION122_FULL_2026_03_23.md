# AGENT HANDOFF — WeatherBot Session 122 FULL (2026-03-23)

## STATUS: LIVE PAPER TRADING | 2 COMMITS DEPLOYED | DATA ANALYSIS COMPLETE | PENDING SHADOW ENTRY REVIEW

---

## READ THESE BEFORE DOING ANYTHING

1. `CLAUDE.md` — Prime directive, rules of engagement, architecture facts, critical traps
2. `C:\Users\samwa\.claude\projects\C--lockes-picks-polymarket-ai-v2\memory\MEMORY.md` — Session history, P&L data, outstanding items
3. This handoff doc — everything you need

**This is a WeatherBot-only session. No bleed to other bots unless explicitly demanded.**

---

## SYSTEM OVERVIEW

- **What**: 14-bot automated Polymarket trading system. WeatherBot trades temperature-bucket markets using NOAA ensemble forecasts (GFS/ECMWF/HRRR/AIFS, 133 members).
- **Phase**: Paper trading (SIMULATION_MODE=true). Real capital at risk when boolean flips. Paper trading IS production.
- **VPS**: Ubuntu-3 at 34.251.224.21 (16GB/4vCPU)
- **SSH**: `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21`
- **Deploy**: `bash deploy/deploy.sh` from local repo (atomic symlink swap)
- **Logs**: `sudo journalctl -u polymarket-ai -f`
- **Current deploy**: `20260323_130618` (S122)
- **DB**: PostgreSQL on VPS. Access: `sudo -u postgres psql polymarket`
- **P&L script**: `python scripts/bot_pnl.py WeatherBot <hours>`
- **48h charts**: `python scripts/weather_48h_charts.py` (needs `wb_48h_raw.csv` from VPS export)

---

## WHAT WAS DONE THIS SESSION (S121 + S122)

### S121 — Paper Trading Realism + Live Retry + Model Freshness (commit `99a7759`)

#### SELL-side VWAP book walk
- Added `_vwap_from_bids()` in `paper_trading.py` — mirrors existing ask-side walk for BUY
- Exits now simulate progressive bid slippage (previously: flat best-bid fill)
- SELL event_data enriched with `sell_vwap`, `sell_walk_depth`, `sell_slippage_bps`

#### Per-scan book depletion
- After each paper fill, subtract filled shares from in-memory book copy
- Consecutive fills in same scan see progressively worse depth
- Reset at scan start via `_scan_book_state`

#### PAPER_LATENCY_DRIFT_BPS_PER_SEC
- Was dead config (never wired). Now wired in paper_trading.py
- Default OFF (0). Set to 10 for ~0.1%/sec adverse price movement simulation

#### Live order retry (`_execute_with_retry()`)
- New method in `order_gateway.py` for live CLOB orders
- 3x retry with exponential backoff for transient failures (rate limits, nonce, timeouts)
- Immediate fail for permanent errors (market closed, delisted)
- Settings: `LIVE_ORDER_MAX_RETRIES=3`, `LIVE_ORDER_RETRY_BASE_S=1.0`

#### Model freshness sizing factor
- `ModelRunMonitor` age now feeds into sizing
- <2h old model: boost. >8h old: discount. Via `_get_model_age_hours()`
- Externalized: `WEATHER_ALPHA_DECAY_HALF_LIFE_S`, `WEATHER_NBM_BOOST`, `WEATHER_COMBINED_BOOST_CAP`

#### Other S121 fixes
- Fixed Python 3.13 local import shadow in `_get_model_age_hours()`
- Fixed precipitation engine indentation bug (`elif` → `if`, ROOT 2 from S120)
- `PAPER_TAKER_FEE_BPS` default changed 0 → 150 (1.5% realistic)

#### Files: `paper_trading.py`, `order_gateway.py`, `weather_bot.py`, `settings.py`, `precipitation_engine.py`
#### New files: `scripts/weather_brier_by_side.py`, `scripts/weather_edge_decay.py`, `tests/unit/test_book_walk.py`, `scripts/test_clob_order.py`

---

### S122 — Uncap Sizing + Shadow Entries + Data Analysis (commit `c338f8f`)

#### Cap Adjustments (let Kelly self-regulate)
| Cap | Setting | Old | New | File |
|-----|---------|-----|-----|------|
| Max per group | `WEATHER_MAX_PER_GROUP_USD` | $1,000 | **$10,000** | settings.py, weather_bot.py |
| Max per city | `WEATHER_MAX_CORRELATED_EXPOSURE` | $2,000 | **$5,000** | settings.py, weather_bot.py |
| Combined boost cap | `WEATHER_COMBINED_BOOST_CAP` | 2.0x | **1.5x** | settings.py |
| Max buckets/group | `WEATHER_MAX_BUCKETS_PER_GROUP` | 3 | **5** | settings.py |
| NO max entry price | `WEATHER_NO_MAX_ENTRY_PRICE` | 0.65 | **1.0 (removed)** | settings.py |
| BM max_bet_usd | BOT_CONFIGS["WeatherBot"] | $300 | **$600** | bankroll_manager.py |
| BM max_daily_usd | BOT_CONFIGS["WeatherBot"] | $10,000 | **$20,000** | bankroll_manager.py |
| Max positions | `WEATHER_MAX_POSITIONS` | 500 | **1,000** | settings.py |

#### Confidence Penalty Removals
- **NO confidence discount (0.80x at >70c)** — REMOVED in `weather_bot.py` L1919-1925.
  - WHY: NO 60-80c had 58.8% WR (positive!) but negative P&L (-$164) because discount shrank bets below break-even threshold. Kelly already handles payoff asymmetry via (p*b - q)/b.
- **Boundary risk 50% discount** — REMOVED in `weather_bot.py` L1900.
  - WHY: Kelly already accounts for edge. The 50% penalty was crushing profitable boundary trades. `boundary_risk` still tracked for logging, just no longer discounts confidence.

#### Shadow Entry Logging (NEW)
- Sub-$5 trades now logged as `SHADOW_ENTRY` events in `trade_events`
- Location: `weather_bot.py` L2495-2526 (inside exposure lock, before `return False`)
- Fields in event_data: `raw_size_usd`, `combined_boost`, `city`, `reason`
- `reason` values: `sub_min_trade` (Kelly < $5) or `exposure_cap` (group/city cap hit)
- Query:
```sql
SELECT event_data->>'reason' AS reason, COUNT(*) AS n,
    ROUND(AVG(CAST(event_data->>'raw_size_usd' AS FLOAT))::numeric, 2) AS avg_raw_size,
    ROUND(AVG(confidence)::numeric, 4) AS avg_conf,
    ROUND(AVG(confidence - price)::numeric, 4) AS avg_edge
FROM trade_events WHERE event_type = 'SHADOW_ENTRY' AND bot_name = 'WeatherBot'
GROUP BY reason;
```

#### Files: `weather_bot.py`, `settings.py`, `bankroll_manager.py`

---

## DATA ANALYSIS FINDINGS (3,884 entries, 1,185 resolutions as of S122)

### Weekly Edge Decay (stabilizing at ~10%)
| Week | Entries | Avg Edge | YES/NO |
|------|---------|----------|--------|
| 03-02 | 26 | 23.4% | 12/14 |
| 03-09 | 1,496 | 11.6% | 479/1017 |
| 03-16 | 2,183 | 10.3% | 755/1428 |
| 03-23 | 11 | 9.8% | 2/9 |

### Weekly Resolution P&L (WR improving, $/trade declining)
| Week | Resolved | WR | P&L | $/trade |
|------|----------|-----|------|---------|
| 03-09 | 252 | 53.6% | +$1,768 | $7.02 |
| 03-16 | 694 | 63.5% | +$599 | $0.86 |
| 03-23 | 239 | 61.9% | +$162 | $0.68 |

### P&L by Side x Price Bucket (ALL TIME)
| Side | Bucket | N | WR | Brier | P&L |
|------|--------|---|-----|-------|-----|
| NO | 0-20c | 27 | 40.7% | 0.32 | +$4 |
| NO | 20-40c | 38 | 44.7% | 0.32 | **+$544** |
| NO | 40-60c | 175 | 52.0% | 0.32 | **+$761** |
| NO | 60-80c | 839 | 58.8% | 0.33 | **-$164** |
| NO | 80-100c | 885 | 73.2% | 0.24 | +$348 |
| YES | 0-20c | 669 | 38.3% | 0.27 | -$175 |
| YES | 20-40c | 462 | 45.7% | 0.28 | **+$811** |
| YES | 40-60c | 51 | 52.9% | 0.28 | +$116 |
| YES | 60-80c | 16 | 25.0% | 0.62 | -$203 |
| YES | 80-100c | 6 | 50.0% | 0.45 | +$19 |

### Per-City P&L (ALL TIME)
**Losers:** Buenos Aires (-$355, 51.1% WR), London (-$76), Paris (-$50), Ankara (-$34)
**Winners:** Seattle (+$251), Wellington (+$233), Sao Paulo (+$195), Toronto (+$181), NYC (+$158)

### Last 48h (506 resolutions — BOTH SIDES NEGATIVE)
- NO: 1,061 trades, 62.8% WR, **-$402** (avg win $6.49, avg loss $11.96, avg bet $21.25)
- YES: 617 trades, 43.8% WR, **-$546** (avg win $8.91, avg loss $8.51, avg bet $3.45)
- 0-20c: -$724 (333 trades). 20-40c: +$214 (283 trades). 60-80c: -$428 (454 trades)
- London -$170, Toronto -$110, Miami -$100 worst cities in 48h

### CALIBRATION (CRITICAL — 48h data)
| Predicted | N | Actual WR | Gap |
|-----------|---|-----------|-----|
| 10-20% | 168 | 42.3% | +26pp UNDERCONFIDENT |
| 20-30% | 192 | 43.8% | +19pp UNDERCONFIDENT |
| 30-40% | 211 | 45.0% | +10pp |
| 40-50% | 96 | 46.9% | +3pp BEST |
| 50-60% | 32 | 53.1% | -0.5pp BEST |
| 60-70% | 25 | 52.0% | -13pp |
| 70-80% | 73 | 52.1% | -24pp OVERCONFIDENT |
| 80-90% | 139 | 52.5% | -33pp OVERCONFIDENT |
| 90-100% | 742 | 67.4% | -27pp OVERCONFIDENT |

**Model says 94.5% but wins only 67.4% of the time. Model says 16% but wins 42% of the time. Actual WR clusters around 45-55% regardless of predicted confidence.** The model has signal but confidence values are poorly scaled.

### Why So Many $1 Bets
Paper fill model (`fill_frac`) only filling 40-50% of orders on thin weather books. Kelly sizes correctly, then partial fills slash it. 75% of trades get <60% filled. This is the paper fill model simulating realistic liquidity — in live trading, maker orders would likely fill fully.

| Fill % | Trades | Avg Bet |
|--------|--------|---------|
| <20% | 21 | $17 |
| 20-40% | 258 | $26 |
| 40-60% | 434 | $41 |
| 60-80% | 98 | $46 |
| 80-100% | 43 | $94 |

---

## PENDING DECISIONS / NEXT SESSION PRIORITIES

### P0: Review Shadow Entries (24-48h after S122 deploy)
Run the shadow entry query above. Decision: should min trade go below $5, or are these correctly filtered dust?

### P1: Calibration Fix
The calibration data shows the model is badly miscalibrated. The confidence values don't track reality. This is the single biggest issue — Kelly sizes based on confidence, but confidence is wrong. Options:
1. Isotonic regression post-hoc recalibration
2. Platt scaling (logistic recalibration)
3. Histogram binning (empirical CDF correction)
4. Reduce to binary signal (edge > threshold = flat bet size)

### P2: 48h Negative P&L
Both sides negative in last 48h (-$948 total). Need to determine if this is:
- Statistical noise (506 resolutions is decent sample)
- Cap changes from S122 haven't had time to help (deployed mid-period)
- Model degradation (spring transition)

### P3: Bet Size Distribution
YES avg bet $3.45 vs NO avg bet $21.25. YES is undersized. With caps removed in S122, monitor if YES bets increase.

### P4: Buenos Aires Chronic Loser
-$355 all-time, 51.1% WR. Southern hemisphere seasonal bias? EMOS 90-day window (S120) should help as spring data accumulates. Consider station exclusion if doesn't improve.

---

## ARCHITECTURE REFERENCE (WeatherBot scan path)

```
scan_and_trade() [L779]
  ├─ _handle_daily_boundary() [L784] — resets exposure, restores P&L
  ├─ asyncio.gather(_maybe_reload_calibration, _load_category_params) [L787]
  │   └─ EMOS uses 90-day rolling window (WEATHER_EMOS_WINDOW_DAYS=90)
  ├─ _check_monitoring_thresholds() [L794] — Brier/drawdown halt + Kelly graduation
  ├─ PM exit detection [L799-855] — tracks position_manager exits, decrements exposure
  ├─ _prefetch_severe_weather_alerts() [L988] — NWS batch fetch
  ├─ asyncio.gather(*[_analyze_group(g) for g in groups]) [L1000]
  │   ├─ forecast_client.get_combined_forecast() — GFS+ECMWF+AIFS (133 members)
  │   ├─ prob_engine.fit_distribution() — skew-normal + EMOS correction
  │   ├─ prob_engine.bucket_probabilities() — CDF integration per bucket
  │   ├─ _apply_metar_resolution_day_override() — METAR running max (< 12h)
  │   ├─ prob_engine.compute_edges() — model_prob - market_price
  │   └─ tradeable filtering:
  │       ├─ edge >= min_edge (spread-confidence gated)
  │       ├─ exit cooldown (4h)
  │       ├─ penny-bet filter (4c-97c)
  │       ├─ NO price cap — REMOVED S122 (was 65c)
  │       ├─ in-memory position check (fast path)
  │       ├─ DB position guard (ground truth) [S119]
  │       ├─ boundary_risk — LOGGED ONLY, no discount (S122)
  │       ├─ max 5 buckets per group (S122, was 3)
  │       └─ NO confidence discount — REMOVED S122 (was 0.80x above 70c)
  ├─ _compute_regime_boost() — cross-city warm/cold detection
  ├─ _exec_group() for each group with edge:
  │   ├─ ≥2 buckets: _execute_group_trades() → S-T multi-bucket Kelly
  │   └─ 1 bucket: _execute_weather_trade() → independent Kelly
  │       ├─ same-side dedup (_position_details)
  │       ├─ exit cooldown check
  │       ├─ fill-failure cooldown
  │       ├─ daily loss limit
  │       ├─ group/city exposure caps (locked) — $10K/$5K (S122)
  │       ├─ expiry boost (1.0-2.0x by lead time)
  │       ├─ regime boost (1.2x)
  │       ├─ severe weather halt/boost
  │       ├─ jump boost (model run delta)
  │       ├─ NBM boost (1.3x high conviction)
  │       ├─ model freshness factor (S121)
  │       ├─ combined boost (additive, cap 1.5x S122 — was 2.0x)
  │       ├─ Baker-McHale uncertainty (model spread)
  │       ├─ station reliability (MSE-based)
  │       ├─ Buhlmann calibration confidence (cold-start ramp)
  │       ├─ slippage check (liquidity guardian)
  │       ├─ Kelly sizing via BotBankrollManager ($600 cap, $20K daily — S122)
  │       ├─ SHADOW_ENTRY logging for sub-$5 trades (S122)
  │       ├─ exposure lock reservation (atomic)
  │       └─ place_order() → order_gateway (VWAP gate BYPASSED for WeatherBot)
  │           └─ paper_trading.place_order() → VWAP fill from book walk
  │               ├─ BUY: ask-side VWAP walk
  │               ├─ SELL: bid-side VWAP walk (S121)
  │               └─ per-scan book depletion (S121)
  ├─ _reevaluate_open_positions() — feed position_manager fresh probs
  └─ every 10 scans: backfill_outcomes + check_emos_drift + close_stale_positions
```

---

## CURRENT CONFIG (live VPS values post-S122)

```
WeatherBot BotBankrollManager:
  capital=$20,000, kelly_fraction=0.25, max_bet_usd=$600, max_daily_usd=$20,000

WEATHER_MIN_EDGE=0.08, WEATHER_INTL_MIN_EDGE=0.12
WEATHER_MIN_CONFIDENCE=0.10
WEATHER_MAX_POSITIONS=1000
WEATHER_MAX_PER_GROUP_USD=10000
WEATHER_MAX_CORRELATED_EXPOSURE=5000
WEATHER_COMBINED_BOOST_CAP=1.5
WEATHER_MAX_BUCKETS_PER_GROUP=5
WEATHER_NO_MAX_ENTRY_PRICE=1.0 (effectively removed)
WEATHER_KELLY_FRACTION=0.25
WEATHER_DEFAULT_SIZE=25
WEATHER_EXIT_COOLDOWN_SECS=14400 (4h)
WEATHER_MIN_TRADE_USD=5.0
WEATHER_EMOS_WINDOW_DAYS=90
WEATHER_BM_FLOOR=0.50
WEATHER_BUHLMANN_KAPPA=30.0
WEATHER_DAILY_LOSS_LIMIT=10000
WEATHER_MAX_TOTAL_EXPOSURE_USD=50000
WEATHER_TOTAL_CAPITAL=20000
WEATHER_MAX_LEAD_TIME_HOURS=168

PAPER_TAKER_FEE_BPS=150
PAPER_REALISTIC_FILLS=true
PAPER_DEFAULT_SPREAD=0.04
PAPER_LATENCY_DRIFT_BPS_PER_SEC=0 (disabled, set to 10 to enable)
PAPER_TRADING_CAPITAL=10000000

LIVE_ORDER_MAX_RETRIES=3
LIVE_ORDER_RETRY_BASE_S=1.0
```

---

## KEY FILES (WeatherBot-specific)

| File | Lines | Purpose |
|------|-------|---------|
| `bots/weather_bot.py` | ~4,400 | Main bot: scan, analyze, trade, exit |
| `base_engine/weather/probability_engine.py` | 520 | Skew-normal distribution + EMOS calibration |
| `base_engine/weather/precipitation_engine.py` | 235 | Rain/snow probability (fixed S120) |
| `base_engine/weather/market_mapper.py` | 1,136 | Polymarket question → city/bucket mapping |
| `base_engine/weather/forecast_client.py` | 1,587 | GFS/ECMWF/HRRR/AIFS ensemble fetching |
| `base_engine/weather/metar_client.py` | 237 | Airport observations (resolution day) |
| `base_engine/weather/metar_monitor.py` | 283 | METAR running max tracking |
| `base_engine/weather/model_run_monitor.py` | 339 | Model freshness tracking (S121) |
| `base_engine/execution/paper_trading.py` | ~900 | Paper fills: VWAP walk, book depletion (S121) |
| `base_engine/execution/order_gateway.py` | ~1,200 | Order routing, VWAP gate, live retry (S121) |
| `base_engine/risk/bankroll_manager.py` | 263 | Kelly sizing, per-bot caps |
| `base_engine/learning/calibration_tracker.py` | 259 | EMOS + Brier tracking |
| `config/settings.py` | WEATHER_* block | All config |
| `tests/unit/test_weather_bot.py` | ~1,734 | Main test suite |
| `tests/unit/test_book_walk.py` | 342 | SELL VWAP + book depletion tests (S121) |
| `scripts/weather_48h_charts.py` | 206 | 6-panel analysis charts |
| `scripts/weather_brier_by_side.py` | 104 | Brier score by side x price |
| `scripts/weather_edge_decay.py` | 103 | Weekly edge/WR/$/trade trends |
| `scripts/bot_pnl.py` | canonical | P&L reporting |

---

## DATA SOURCES

| What | Where | Notes |
|------|-------|-------|
| P&L (realized) | `trade_events` table | SOLE AUTHORITY. `realized_pnl` column. |
| P&L (unrealized) | `positions.unrealized_pnl` | Mark-to-market, updated every 10s |
| Shadow entries | `trade_events WHERE event_type='SHADOW_ENTRY'` | Sub-$5 trades (S122) |
| Predictions | `prediction_log` | `was_correct` = calibration, NOT trade WR |
| Paper trades | `paper_trades` | LEGACY. Do not use for P&L. |
| Canonical P&L | `python scripts/bot_pnl.py WeatherBot <hours>` | |
| 48h charts | Export data then run `python scripts/weather_48h_charts.py` | |

### VPS Data Export (for charts)
```bash
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 \
  "sudo -u postgres psql polymarket -t -A -F'|' -c \"
SELECT r.event_time::date, e.side, ROUND(r.realized_pnl::numeric,4),
    ROUND((e.size*e.price)::numeric,4), ROUND(e.price::numeric,6),
    COALESCE(e.event_data->>'city','Unknown'),
    COALESCE(ROUND(CAST(e.event_data->>'lead_time_hours' AS FLOAT)::numeric,1),-1),
    ROUND(e.confidence::numeric,6), CASE WHEN r.realized_pnl>0 THEN 1 ELSE 0 END
FROM trade_events r JOIN (
    SELECT DISTINCT ON (market_id) market_id, side, size, price, confidence, event_data
    FROM trade_events WHERE bot_name='WeatherBot' AND event_type='ENTRY'
    ORDER BY market_id, event_time
) e ON e.market_id=r.market_id
WHERE r.bot_name='WeatherBot' AND r.event_type='RESOLUTION'
  AND r.event_time >= NOW()-INTERVAL '48 hours' ORDER BY r.event_time;
\"" > wb_48h_raw.csv
```

---

## CRITICAL TRAPS (cumulative — DO NOT VIOLATE)

1. **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
2. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL
3. **VWAP edge gate BYPASSED for WeatherBot** (order_gateway.py). Weather CLOBs have 99c structural asks. Do NOT re-enable.
4. **Paper capital is $10M intentionally.** BotBankrollManager is the real limit.
5. **EMOS has 90-day rolling window** (`WEATHER_EMOS_WINDOW_DAYS=90`). Cold stations fall back to global EMOS.
6. **`_in_model_window()` is DELETED.** Logic lives in `_get_scan_interval_seconds()`.
7. **Precipitation empirical fallback fixed S120.** `elif` → `if` for peer bucket-type branches.
8. **`boundary_risk` persisted in event_data** for audit but NO LONGER discounts confidence (S122).
9. **NO confidence discount REMOVED (S122).** Do not re-add. Kelly self-regulates.
10. **NO entry price cap REMOVED (S122).** `WEATHER_NO_MAX_ENTRY_PRICE=1.0`.
11. **`confidence` is a TOP-LEVEL column on trade_events**, NOT in event_data JSONB.
12. **Python 3.13 scoping**: `from X import Y` inside function makes Y local for ENTIRE function. Any use before import → UnboundLocalError.
13. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
14. **`paper_trades` has NO `metadata` JSONB column.**
15. **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time.
16. **`_market_meta_cache` in MirrorBot**: 3-tuple. NEVER expand. (MirrorBot-only but in shared memory)
17. **BOT_REGISTRY=14 bots** — shared module change requires all 14 verified.
18. **positions table**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`.
19. **SHADOW_ENTRY events** are best-effort (try/except pass). Don't block trades on logging failure.
20. **fill_frac** in event_data shows paper fill model partial fill fraction. 75% of trades get <60% filled.

---

## VERIFIED FALSE ALARMS (don't re-investigate)

| Claim | Status | Evidence |
|-------|--------|----------|
| Drawdown compression disabled | FALSE | `health_scheduler.py:214` updates every cycle |
| `bucket.temp_unit` not populated | FALSE | Set from regex at market_mapper.py |
| `_get_afd_spread_factor` dead | FALSE | Called at weather_bot.py L1743 |
| `_fit_samos` dead | FALSE | Called at weather_bot.py L4102 |
| `_get_enso_regime` dead | FALSE | Called at L3346, L3956, L4346 |
| `_save_backoff_to_redis` dead | FALSE | Called at L1126 |
| `compute_nbm_benchmark` dead | FALSE | Called at L1767 |
| All PSW scan methods dead | FALSE | Called at L1085-1087 |

---

## SESSION HISTORY (recent)

| Session | Date | Key Changes |
|---------|------|-------------|
| S122 | 03-23 | Cap uncapping, shadow entries, confidence penalty removal, data analysis |
| S121 | 03-23 | SELL VWAP walk, book depletion, live retry, model freshness, PAPER_TAKER_FEE 150bps |
| S120 | 03-23 | Full code audit, EMOS 90-day window, precip fix, VWAP gate bypass, 6 root fixes |
| S119 | 03-22 | NO price trap, correlated blowup, position stacking, high-conf NO losses, overnight losses, edge cap |
| S118 | 03-22 | S117 diagnosis wrong, bot self-healed from 429 rate limiting, S116 reverted |
| S115 | 03-21 | Shadow fills system, combined boost cap, station reliability, Buhlmann credibility |
| S108 | 03-19 | Fill pipeline: taker 0.85, bestAsk pre-filter, volume passthrough, same-side dedup |
| S104 | 03-18 | Fill quality logging, exposure leak fix, daily counter, alpha decay BUY-only |
| S100 | 03-17 | Alpha decay, canary persistence, SSH timeouts, backoff Redis, P&L +$2,881 |

---

## USER PREFERENCES (from memory)

- **Scope lock**: NEVER add unsolicited features. Only fix what the handoff or user explicitly requests.
- **Root fixes only**: No bandaids, no gates, no config tuning without data backing.
- **Show don't tell**: User wants charts opened in image viewer (`start "" "path.png"`), not ASCII or inline.
- **Charts format**: Use `chart_visual.py` 3x2 grid format (equity curve, daily bars, side P&L, price buckets, city P&L, calibration/lead time).
- **Terse responses**: User doesn't want trailing summaries or over-explanation.
- **"Paper trading IS production"**: Every feature must work identically in paper and live. No shortcuts.
- **Kelly self-regulation**: User believes Kelly + BotBankrollManager should be the sizing authority. Artificial caps on top reduce expected value. Only keep caps that serve as catastrophic backstops, not routine regulators.
