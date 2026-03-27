# WeatherBot Session 135 — Full Context Prompt

## YOU ARE

A WeatherBot-only agent for a live 15-bot Polymarket automated trading system. Real capital is at risk. You touch ONLY WeatherBot files unless explicitly told otherwise. Read `CLAUDE.md` first — it is law.

## SYSTEM OVERVIEW

This is `polymarket-ai-v2`, a Python 3.13 async trading system running on Ubuntu VPS (34.251.224.21). 14 bots trade Polymarket prediction markets via paper trading engine. WeatherBot trades temperature/precipitation/wind/snowfall weather markets across 35 cities using ensemble weather forecasts (GEFS+ECMWF, ~133 members) converted to bucket probabilities via CDF integration, calibrated with Platt+Isotonic, sized via Kelly criterion.

**VPS**: Ubuntu at 34.251.224.21, SSH key `~/.ssh/LightsailDefaultKey-eu-west-1.pem`, DB name `polymarket` (NOT polymarket_ai_v2), service `polymarket-ai`, deploy via manual SCP (deploy.sh blocked by 2 flaky tests).

## WHAT HAPPENED IN SESSION 134 (completed)

### Bugs Found & Fixed (3 commits: `081905e`, `74ff360`, `f35695f`)

1. **S132 dampeners NOT actually removed** — 6 of 8 documented removals were still in code. All 6 actually removed now: spread inflation, tail discount, Baker-McHale, spread gate, Buhlmann ramp, model freshness, combined boost cap.

2. **Kelly P&L gate `sa_text` NameError** — line 4600 used `sa_text` but import was `text`. Silent `except Exception: pass` meant gate never blocked. Fixed → Kelly graduated to 0.35.

3. **Phase 4b resolution_backfill.py** — was sourcing ENTRY size from `paper_trades` (mutable UPSERT). Fixed to use `trade_events` (immutable).

4. **Daily P&L restore reading corrupted RESOLUTION events** — -$40K phantom loss triggered 20% drawdown halt. Fixed to use EXIT-only query.

5. **Dead code cleanup** — removed `_get_model_age_hours()`, `_calibration_confidence()`, `_get_afd_spread_factor()`, `_get_station_wfo()`, `_parse_afd_uncertainty()`, `_afd_cache`, `_wfo_cache`, `_buhlmann_kappa`.

6. **Phantom cleanup executed on VPS** — 1,061 corrupted RESOLUTION events deleted ($37K phantom P&L) across all bots.

7. **Config**: `WEATHER_NO_MAX_ENTRY_PRICE=0.75` set on VPS.

8. **New scripts**: `weather_pnl_dashboard.py`, `weather_monitor_48h.py`, `cleanup_phantom_resolutions.py`.

### VPS Status (post S134 deploy)
- WeatherBot: RUNNING, 35 cities, 1300 markets, Kelly=0.35
- Calibration: T=2.061, Brier improvement +0.0195
- EMOS: 24 stations fitted, global fallback active
- 162 weather tests passing

## THE CORE PROBLEM: CONFIDENCE IS BROKEN

### Data (all-time, 3,360 resolved positions)

| Conf Tier | NO Trades | NO WR | NO P&L | YES Trades | YES WR | YES P&L |
|-----------|-----------|-------|--------|------------|--------|---------|
| 0.95+ | 1,163 | 87.6% | +$1,902 | 398 | 18.8% | -$1,268 |
| 0.90-0.95 | 100 | 77.0% | -$299 | 60 | 25.0% | -$10,376 |
| 0.85-0.90 | 51 | 68.6% | -$339 | 41 | 41.5% | +$745 |
| 0.80-0.85 | 34 | 85.3% | +$388 | 40 | 25.0% | -$1,653 |
| 0.01-0.50 | 10 | 70.0% | +$51 | 141 | 6.4% | -$3,159 |

**Total**: -$14,963. Tokyo alone = -$9,697 (one -$10,207 position = 31.4% of all losses).

### Root Causes (proven with data)

1. **Single calibrator on pooled YES+NO** — One Platt T cannot correct both populations. NO at 0.95+ is well-calibrated (87.6% WR). YES at 0.95+ is catastrophically wrong (18.8% WR).

2. **Calibrator maps raw P(YES)=0.02 → confidence 0.93** — For NO trades, raw_conf = 1-0.02 = 0.98, Platt compresses to 0.93, which is reasonable for NO. But prediction_log stores this as 0.93 for the MARKET, not per-side. When the bot trades YES on the same market (different bucket), it sees high confidence.

3. **Kelly oversizes YES at low prices** — At price=$0.05, b=19, even small confidence produces large share counts. Combined boost (up to 3.0x) amplifies further.

4. **No YES confidence floor** — Trades at 6.4% WR still executed.

### Additional P&L Breakdowns

**By entry price × side (all-time):**
- YES 0.00-0.10: 255 trades, 4.7% WR, -$2,100
- YES 0.10-0.20: 403 trades, 12.9% WR, +$138
- YES 0.20-0.30: 316 trades, 21.8% WR, +$335
- YES 0.30-0.50: 162 trades, 35.2% WR, +$825
- NO 0.80-0.90: 834 trades, 89.0% WR, +$466
- NO 0.90-1.00: 355 trades, 93.8% WR, +$234

**By lead time × side (all-time):**
- 0-24h: -$14,103 (catastrophic)
- 24-48h: -$927
- 48-72h: +$74
- 72-120h: +$374 (only consistently profitable)

**By city (top losers):**
- Tokyo: -$9,697 (one massive YES position)
- Atlanta: -$970
- Tel Aviv: -$1,121

## APPROVED PLAN: 3 Changes (R1 + R3 + R4)

Full implementation plan at: `C:\Users\samwa\.claude\plans\giggly-yawning-feather.md`

### R1: Split calibration by side (ROOT CAUSE FIX)

Create separate YES and NO `WeatherConfidenceCalibrator` instances with independent Platt T values. Data available: 1,426 NO + 661 YES resolved trades (both above 200 min_samples threshold).

**Code locations:**
- Calibrator class: `bots/weather_bot.py:66-258`
- `__init__` (add `side` param): line 76
- `fit_from_trade_events` SQL (add side filter): lines 123-138
- Calibrator instantiation: lines 362-364
- Calibration application: lines 2098-2101
- Refit call: search for `confidence_cal_fitted`

**Key change:** Add `side` param to constructor, filter SQL by side, create YES/NO/ALL trio, apply matching calibrator per trade side. ALL calibrator is fallback when per-side has insufficient data.

**Toggle:** `WEATHER_CONFIDENCE_CAL_SPLIT_BY_SIDE=true` (default on)

### R3: Wire YES confidence floor at 0.35 (SAFETY GATE)

The env var `WEATHER_YES_MIN_CONFIDENCE` already exists at `config/settings.py:728` (default 0.0, never wired). Wire it into `_analyze_group()` after line 2101 (after effective_confidence assigned, before tradeable.append).

**Toggle:** `WEATHER_YES_MIN_CONFIDENCE=0.35` (change default from 0.0)

### R4: Disable combined_boost for YES side (AMPLIFICATION GUARD)

Two-line change after combined_boost formula (line ~2554): if YES side and boost disabled, set combined_boost=1.0.

**Toggle:** `WEATHER_YES_BOOST_ENABLED=false` (default off, meaning YES gets no boost)

### Deferred (do NOT implement)

- R2 (YES max position cap $300) — symptom patch
- R5 (YES entry price cap $0.30) — arbitrary
- R6 (edge-based sizing) — Kelly is correct with right inputs

## WEATHERBOT SIZING PIPELINE (current, post-S134)

```
1. Raw ensemble members (133 GEFS+ECMWF) → mean, std (floor 0.5°)
2. EMOS correction: μ = a + b·X̄, σ = emos_sigma (per-station or global)
3. Skew-normal fit (≥10 members) or normal fallback → (loc, scale, shape)
4. CDF integration per bucket → model_prob[bucket] (clamped 0.001-0.999, normalized)
5. Edge = model_prob - market_price → side = YES if edge > 0, NO if edge < 0
6. Raw confidence: YES = model_prob, NO = 1 - model_prob → cap at 0.95
7. Calibration: sigmoid(logit(p) / T) → isotonic → effective_confidence
   [CHANGE R1: separate T per side]
8. [NEW R3: YES floor at 0.35 — skip if below]
9. Min edge gate (0.08 domestic, 0.12 intl)
10. Kelly: b = (1-price)/price, f = (conf*b - q)/b, size = f × fraction × capital
11. S-T multi-bucket allocation (if multiple opps in group)
12. Combined boost: 1.0 + (expiry-1) + (regime-1)*0.5 + (severe-1)*0.5 + (jump-1)*0.5 + (nbm-1)*0.5
    [CHANGE R4: = 1.0 for YES side]
13. Station reliability factor (MSE-based, 0.8-1.2x)
14. Slippage-adjusted edge check
15. Negative EV gate (confidence < price → shadow entry)
16. Exposure caps (per-group, per-city), min $5 floor
17. place_order → paper engine VWAP fill
```

## CRITICAL TRAPS (MUST READ)

### Data & P&L
- **trade_events is P&L authority** — never read paper_trades for P&L
- **paper_trades UPSERT overwrites size** — root cause of RESOLUTION corruption
- **trade_events immutability trigger**: must DISABLE then re-enable for cleanup
- **RESOLUTION event idempotency broken on partitioned tables** — uses WHERE NOT EXISTS
- **trade_events JSONB column is `event_data`** — NOT metadata_json
- **traded_markets.resolution is UPPERCASE** (YES/NO/PURGED)
- **markets table PK is `id`**, NOT `market_id`. Use `traded_markets` for market_id joins
- **VPS database name is `polymarket`**
- **S134: Daily P&L restore uses EXIT-only** — RESOLUTION events corrupted
- **S134: Phase 4b now sources from trade_events ENTRY** — never revert

### Bot Logic
- **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL
- **BotBankrollManager handles SIZING; risk_manager handles LIMITS**
- **Paper trading is PRODUCTION** — not a sandbox
- **WEATHER_NO_MAX_ENTRY_PRICE=0.75** on VPS
- **`_buhlmann_kappa` removed** (S134). `_station_n_resolved` still exists (cold-start bootstrap)
- **All 8 S132 dampeners are NOW actually removed** — do NOT re-add

### Python & Async
- **Python 3.13 scoping**: local imports shadow module-level for entire function
- **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **asyncpg DATE**: Use `CURRENT_DATE` as SQL literal

### Deploy
- **deploy.sh blocked** by 2 flaky tests — use manual SCP
- **VPS path**: `/opt/polymarket-ai-v2/` (no `current/` symlink)
- **Service**: `sudo systemctl restart polymarket-ai`
- Files owned by `polymarket:polymarket`

## KEY FILES

| File | Role | Lines |
|------|------|-------|
| `bots/weather_bot.py` | Main bot logic | ~4700 |
| `base_engine/weather/probability_engine.py` | Forecast → probability via CDF | ~400 |
| `base_engine/risk/bankroll_manager.py` | Kelly sizing, max_bet, daily cap | ~250 |
| `base_engine/execution/paper_trading.py` | VWAP fills, positions | shared |
| `base_engine/data/resolution_backfill.py` | Phase 4b RESOLUTION emission | shared |
| `config/settings.py` | All env var config | shared |
| `tests/unit/test_weather_bot.py` | Weather unit tests | ~2000 |
| `tests/unit/test_weather_cold_start.py` | Cold-start / calibration tests | ~200 |

## KEY CONFIG VALUES (VPS LIVE)

```
WEATHER_NO_MAX_ENTRY_PRICE = 0.75
WEATHER_MIN_EDGE = 0.08
WEATHER_INTL_MIN_EDGE = 0.12
WEATHER_MAX_BUCKETS_PER_GROUP = 3
WEATHER_HOLD_HOURS_BEFORE_RESOLUTION = 48
WEATHER_MIN_TRADE_USD = 5.0
WEATHER_KELLY_FRACTION = 0.25  (auto-graduated to 0.35)
WEATHER_YES_MIN_CONFIDENCE = 0.0  (CHANGE to 0.35)
```

## PRIORITIES

| Pri | Item | Status |
|-----|------|--------|
| **P0** | Implement R1+R3+R4 (confidence recalibration) | NOT DONE — plan ready |
| **P1** | Monitor 48h dampener removal | IN PROGRESS (Mar 26-28) |
| **P2** | Fix 2 flaky tests blocking deploy.sh | Not WB scope |

## USER WORKING STYLE

- Hands-on operator, wants numbers and proof before action
- Trust is earned — questioned data integrity AND agent reliability
- Prefers: direct answers, no fluff, show the query/numbers
- Rejected: deleting historical data without certainty guarantee
- Approved: forward-looking fixes, dampener removal, ground truth queries
- Bot-scoped sessions: separate sessions for WeatherBot, MirrorBot, EsportsBot. Don't bleed.
- "Review each change: does it elevate long-term, fix issues, will it break anything?"

## VERIFICATION COMMANDS

```bash
# Run weather tests
python -m pytest tests/unit/test_weather_bot.py tests/unit/test_weather_cold_start.py -x -q

# Deploy
scp -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem bots/weather_bot.py ubuntu@34.251.224.21:/tmp/
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo cp /tmp/weather_bot.py /opt/polymarket-ai-v2/bots/ && sudo chown polymarket:polymarket /opt/polymarket-ai-v2/bots/weather_bot.py && sudo systemctl restart polymarket-ai"

# Check logs
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo journalctl -u polymarket-ai --since '2 min ago' --no-pager | grep -i weather"

# P&L dashboard
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && sudo -u polymarket /opt/pa2-shared/venv/bin/python scripts/weather_pnl_dashboard.py 24"

# Monitor
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "cd /opt/polymarket-ai-v2 && sudo -u polymarket /opt/pa2-shared/venv/bin/python scripts/weather_monitor_48h.py 48"

# DB query
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21 "sudo -u postgres psql -d polymarket -c \"QUERY\""
```

## INSTRUCTIONS

1. Read `CLAUDE.md` first
2. Read this prompt — it IS your context
3. Read the plan file: `C:\Users\samwa\.claude\plans\giggly-yawning-feather.md`
4. Implement R1 → R3 → R4 in order
5. Run tests after each phase
6. Commit WeatherBot-only files
7. Deploy via manual SCP
8. Verify with log grep + monitoring script
9. Do NOT touch other bot files
10. Do NOT add features beyond what's planned
