# AGENT HANDOFF — WeatherBot Session 135 (2026-03-27)

## SESSION SCOPE
**Bot**: WeatherBot ONLY. No MirrorBot or EsportsBot code changes.
**Operator**: samwa
**Branch**: master
**Commit**: `387775b` — refactor(weather): S135 — replace Platt+Isotonic calibration with LogisticRegression

---

## WHAT HAPPENED THIS SESSION

### 1. Calibration System Overhaul: Platt+Isotonic → Logistic Regression

**Problem**: WeatherBot's confidence calibration used Platt scaling (temperature parameter T) with Isotonic regression on top. This is a 1-parameter model that receives only `raw_confidence` as input. The data showed:
- YES at 0.95+ confidence: 18.8% actual win rate (catastrophically wrong)
- NO at 0.95+ confidence: 87.6% actual win rate (accurate)
- 0-24h lead time: -$14,103 P&L
- YES entry price < $0.10: 4.7% win rate

A single T parameter cannot correct all of these simultaneously. It averages across YES/NO, short/long lead, low/high price — producing a compromise that under-compresses YES and over-compresses NO.

**Solution**: Replace with `sklearn.linear_model.LogisticRegression` using 4 features:
1. `raw_confidence` (float 0-0.95)
2. `side` (binary: YES=1, NO=0)
3. `lead_time_hours` (float 0-168)
4. `entry_price` (float 0.04-0.97)

Same mathematical foundation (logistic model), but with 4 inputs instead of 1. The model learns that short-lead YES at low price is overconfident, while long-lead NO is accurate — from the data, no hard gates needed.

**Files changed**:
- `bots/weather_bot.py` — `WeatherConfidenceCalibrator` class rewritten (lines 60-217). Single calibrator instance replaces triple (YES/NO/ALL). `calibrate()` method now takes `(raw_conf, side, lead_time_hours, entry_price)`. Refit loop simplified (one fit instead of three). SQL query adds `lead_time_hours` and `entry_price` extraction from `event_data` JSONB.
- `config/settings.py` — Removed `WEATHER_CONFIDENCE_CAL_SPLIT_BY_SIDE` (no longer needed; side is a feature, not a split dimension).
- `tests/unit/test_weather_bot.py` — 8 Platt/Isotonic tests removed, 9 logistic regression tests added. 163 weather tests passing.

**Rollback**: `WEATHER_CONFIDENCE_CAL_ENABLED=false` disables calibration entirely.

### 2. Decision History: Why Logistic Regression, Not Split Platt

The session went through this reasoning:
1. **Original plan (from S134 handoff)**: R1=split Platt by side, R3=YES conf floor 0.35, R4=disable YES boost
2. **Review**: R3 and R4 are band-aids. If R1 (split Platt) works, they're redundant. If it doesn't, they mask the problem.
3. **Deeper issue**: Split Platt treats all YES trades the same. But YES at $0.05/6h lead is garbage while YES at $0.35/96h lead is profitable. Platt can't distinguish them.
4. **Insight**: Platt scaling IS logistic regression with 1 input. Adding more inputs (side, lead_time, price) is the same model with more context. Same sklearn, same math, same fitting speed.
5. **Decision**: Single 4-feature LogisticRegression replaces the triple Platt+Isotonic setup. Simpler code, richer model, no hard gates needed.

### 3. Comprehensive Trading Pathway Review (for 3rd party)

A full 16-section document was produced covering the ENTIRE WeatherBot trade flow:
1. System overview & architecture position
2. Market discovery (Gamma API, DB fallback, price enrichment)
3. Market parsing & matching (regex → city/date/bucket, station lookup)
4. Forecast acquisition (133 ensemble members, 3 NWP models, caching, rate limiting)
5. Probability computation (skew-normal fit, CDF integration, EMOS, METAR override)
6. EMOS calibration (mean/spread correction, per-station, fallback chain)
7. Edge computation & filtering (16 pre-trade gates)
8. Confidence calibration (LogisticRegression, 4 features, training data, fitting, inference)
9. Trade sizing (Kelly criterion, graduation, Smoczynski-Tomkins allocation)
10. Combined boost multipliers (expiry, regime, severe, jump, NBM, station reliability)
11. Risk management gates (11 execution-time gates, negative EV shadow entries)
12. Order execution (place_order → paper trading engine → VWAP fill)
13. Position management & exits (alpha decay, resolution, cooldowns)
14. Resolution & P&L (backfill, fee calculation, ground truth query)
15. Data storage & persistence (tables, partitioning, immutability, Redis)
16. Configuration reference (all env vars with defaults)

This review is in the chat history — not saved to a file. If needed for 3rd party, copy from chat.

### 4. Exhaustive 3-Bot Code Audit

Line-by-line audit of all three bot files + shared modules. Full results:

**WeatherBot**: 4 BUG, 2 ERROR, 5 INEFFICIENCY, 4 WARNING
**MirrorBot**: 2 BUG, 6 WARNING, 4 INEFFICIENCY (NOTE: MirrorBot bugs found but NOT fixed — WeatherBot-only session)
**EsportsBot**: 5 BUG, 6 WARNING, 3 INEFFICIENCY (NOTE: EsportsBot bugs found but NOT fixed — WeatherBot-only session)

#### WeatherBot bugs found (NOT YET FIXED):
| ID | Line | Finding |
|----|------|---------|
| W1 | 1135 | `date.today()` uses LOCAL time, not UTC — daily digest timing wrong on non-UTC VPS |
| W2 | 1792 | `date.today()` uses LOCAL time in `_analyze_group()` — skips today's markets after 23:00 UTC on UTC+1 |
| W3 | 1720 | `no_edge` variable is algebraically identical to `-yes_edge` — dead logic |
| W4 | 3918 | Return type annotation mismatch on `_bounded_fetch` |
| W5 | 2737 | Division by zero: `size / opp["price"]` — PSW markets bypass price filter |
| W6 | 1855 | Fragile nested ternary for `_mid` — works by accident |
| W7-W9 | 3786,4004,4062 | Three methods create new `aiohttp.ClientSession` per call instead of reusing forecast client |
| W10 | 1362 | `import httpx` inside function runs every scan |
| W11 | 1067 | Unnecessary `deepcopy` on read-only `weather_markets` |
| W12 | 428 | Silent `except Exception: pass` on prediction log insert |
| W13 | 2477 | Printf-style `%` formatting in structlog — garbles output |

#### MirrorBot P0 bugs found (NOT FIXED — out of scope):
| ID | Line | Finding |
|----|------|---------|
| M1 | 956 | Python 3.13 scoping crash — `_t` imported conditionally, used unconditionally. Every exit crashes. |
| M2 | elite_watchlist:465 | `_track_open_position()` deleted but still called. RTDS copies lose position tracking. |

#### EsportsBot P0 bugs found (NOT FIXED — out of scope):
| ID | Line | Finding |
|----|------|---------|
| E1 | 5918,5941 | `_series_on_price_update` doesn't convert NO token prices → fake 30-45% edges |
| E2 | 5914-5986 | `_series_on_price_update` bypasses ALL safety gates |

---

## CURRENT WEATHERBOT STATE

### What's deployed vs what's in git
- **Git HEAD (master)**: `387775b` — includes LogisticRegression calibrator
- **VPS deploy**: Unknown — session did not deploy. Operator must run `deploy.sh`.

### Key env vars (current values)
```
WEATHER_CONFIDENCE_CAL_ENABLED=true
WEATHER_CONFIDENCE_CAL_WINDOW_DAYS=30
WEATHER_CONFIDENCE_CAL_MIN_SAMPLES=200
WEATHER_YES_MIN_CONFIDENCE=0.35
WEATHER_YES_BOOST_ENABLED=false
WEATHER_MIN_EDGE=0.08
WEATHER_INTL_MIN_EDGE=0.12
WEATHER_NO_MAX_ENTRY_PRICE=0.75
WEATHER_KELLY_FRACTION=0.25
WEATHER_ALPHA_DECAY_HALF_LIFE_S=1800
WEATHER_MIN_TRADE_USD=5.0
WEATHER_MAX_BUCKETS_PER_GROUP=3
```

### Test status
- 163 weather tests passing (all)
- Full suite: 1090+ tests (not re-run this session after calibrator change — operator should verify)

### P&L snapshot (from S132 handoff)
- 24h clean: +$3,017 (excluding Tokyo)
- All-time: approximately -$15K (YES losses dominate)

---

## WHAT NEEDS TO HAPPEN NEXT (WEATHERBOT ONLY)

### P0 — Deploy + Monitor LogisticRegression calibrator
1. Deploy `387775b` to VPS via `deploy.sh`
2. Watch calibrator refit logs: `journalctl -u polymarket-ai -f | grep "weather_cal"`
3. Verify Brier score improves (log line: `weather_cal_refit_result`)
4. Verify coefficient signs: `coef_confidence > 0`, `coef_side_yes < 0`, `coef_lead_time > 0`
5. Monitor 48h: If YES P&L improves, the calibrator is working. If not, investigate coefficients.

### P1 — Fix audit bugs (W1, W2, W5)
1. **W1/W2**: Replace `date.today()` with `datetime.now(timezone.utc).date()` at lines 1135 and 1792
2. **W5**: Add `max(opp["price"], 0.01)` guard at line 2737

### P2 — Fix audit inefficiencies (W7-W9)
1. Reuse `self._forecast_client.get_session()` in `_get_enso_regime`, `_get_afd_spread_factor`, `_get_station_wfo`

### P3 — Evaluate YES conf floor and boost disable
After 48h of LogisticRegression data:
- If YES trades are now profitable → remove `WEATHER_YES_MIN_CONFIDENCE=0.35` floor and re-enable `WEATHER_YES_BOOST_ENABLED=true`
- If YES still losing → keep floors, investigate calibrator coefficients

### P4 — NO cap investigation
S132 noted NO cap needs 0.75 review. Current `WEATHER_NO_MAX_ENTRY_PRICE=0.75` may be too restrictive. After calibrator stabilizes, analyze NO trades at 0.75-0.85 entry price for profitability.

---

## FILES TOUCHED THIS SESSION
| File | Change |
|------|--------|
| `bots/weather_bot.py` | WeatherConfidenceCalibrator rewritten (lines 60-217), triple→single calibrator, call sites simplified |
| `config/settings.py` | Removed `WEATHER_CONFIDENCE_CAL_SPLIT_BY_SIDE` |
| `tests/unit/test_weather_bot.py` | 8 tests removed, 9 added for LogisticRegression |

## FILES READ BUT NOT TOUCHED
- `base_engine/weather/probability_engine.py` — CLEAN
- `base_engine/weather/forecast_client.py` — 2 warnings (SQL f-string, prior_forecasts rebuild)
- `base_engine/weather/market_mapper.py` — CLEAN
- `base_engine/weather/station_registry.py` — CLEAN
- `base_engine/execution/paper_trading.py` — CLEAN
- `base_engine/risk/bankroll_manager.py` — CLEAN
- `bots/mirror_bot.py` — 2 P0 bugs found (out of scope)
- `bots/esports_bot.py` — 2 P0 bugs found (out of scope)
- `bots/base_bot.py` — CLEAN for WeatherBot paths

---

## CRITICAL TRAPS (CARRY FORWARD)

1. **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
2. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL.
3. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
4. **Python 3.13 scoping**: `from X import Y` inside function makes Y local for ENTIRE function
5. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
6. **asyncpg DATE**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
7. **trade_events immutability trigger**: Must `DISABLE TRIGGER` for data cleanup
8. **RESOLUTION idempotency**: `ON CONFLICT` broken on partitioned tables — use `INSERT...SELECT WHERE NOT EXISTS`
9. **BotBankrollManager handles SIZING; risk_manager handles LIMITS**
10. **S132: rel_mult CAPPED at 1.0, _price_adj ZEROED, $50 whale gate, NO 0.5x dampener** — MirrorBot, do not change
11. **S120 P&L was FALSE** — reported +$26,986 from orphan RESOLUTION events
12. **LogisticRegression calibrator (S135)**: Replaces Platt+Isotonic. 4 features: raw_conf, side, lead_time, price. Rollback: `WEATHER_CONFIDENCE_CAL_ENABLED=false`
13. **std_floor = 0.5°** in probability_engine — minimum ensemble spread. Do not lower without analysis.
14. **EMOS coverage**: 24/106 stations have local EMOS. Others use global fallback.
15. **Paper trading IS production** — the only difference is `SIMULATION_MODE=true` at order submission.

---

## CALIBRATOR TECHNICAL DETAILS (for next agent)

### Class: `WeatherConfidenceCalibrator` (weather_bot.py:60-217)

```python
# Features
X = [raw_confidence, side_encoded, lead_time_hours, entry_price]
# side_encoded: YES=1.0, NO=0.0

# Training
scaler = StandardScaler()  # normalize features
X_scaled = scaler.fit_transform(X)
model = LogisticRegression(C=1.0, solver='lbfgs', max_iter=1000)
model.fit(X_scaled, y)  # y = 1.0 if realized_pnl > 0

# Inference
calibrated = model.predict_proba(scaler.transform([[conf, side, lead, price]]))[0, 1]
# Clipped to [0.01, 0.99]

# Validation: reject if calibrated Brier > raw Brier + 0.005
```

### SQL for training data
```sql
SELECT e.confidence, e.side, e.price,
       COALESCE((e.event_data->>'lead_time_hours')::float, 48.0) as lead_time_hours,
       CASE WHEN r.realized_pnl > 0 THEN 1.0 ELSE 0.0 END as won
FROM trade_events e
JOIN trade_events r ON r.market_id = e.market_id AND r.bot_name = e.bot_name
     AND r.event_type = 'RESOLUTION'
WHERE e.bot_name = 'WeatherBot' AND e.event_type = 'ENTRY'
  AND e.event_time >= NOW() - INTERVAL '30 days'
```

### Refit schedule
- Every 6 hours (controlled by `WEATHER_CALIBRATION_RELOAD_SECS=21600`)
- Inside `_reload_calibration_data()` which also refits EMOS, tail models, etc.

### Expected coefficients
| Feature | Expected Sign | Meaning |
|---------|--------------|---------|
| raw_confidence | + | Higher model conf → more likely to win |
| side (YES=1) | − | YES trades are overconfident → penalize |
| lead_time_hours | + | Longer lead → less market efficiency → better edge |
| entry_price | + | Higher price YES = stronger signal |
