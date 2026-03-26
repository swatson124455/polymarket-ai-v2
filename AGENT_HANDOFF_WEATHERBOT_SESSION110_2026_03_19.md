# AGENT HANDOFF — WeatherBot Session 110 (2026-03-19)

## READ THIS FIRST — AGENT INSTRUCTIONS

You are continuing work on the **WeatherBot** module of a 15-bot Polymarket automated trading system. This document is your complete context. You are **scope-locked to WeatherBot** — no cross-bot changes unless explicitly demanded by the user.

**Before you write ANY code:**
1. State the bug in one sentence
2. List files you will touch (max 3 unless justified)
3. Grep for dependents of any module you modify
4. Read the ENTIRE file you're modifying, not just the target function
5. Never add unsolicited features. Fix only what is broken or explicitly requested.

**CLAUDE.md is law.** Read `CLAUDE.md` in the repo root before any code change. It contains the Prime Directive, Rules of Engagement, and Forbidden Patterns.

---

## SYSTEM OVERVIEW

### What Is This?
A 15-bot Polymarket prediction market trading system. Currently **paper trading** (`SIMULATION_MODE=true`). Paper trading IS production — the only difference from live is the final order submission. $20K capital, $300 max bet per bot.

### Bot Roster (15 bots)
| Bot | Purpose | Status |
|-----|---------|--------|
| **WeatherBot** | Temperature/precip/snow/wind bucket markets via NOAA/ECMWF ensembles | **ACTIVE — YOUR FOCUS** |
| MirrorBot | Copy-trades elite Polymarket wallets via RTDS feed | Active |
| EsportsBot | Pre-match esports (LoL/CS2/Valorant/Dota2) via PandaScore + Glicko | Active |
| EsportsLiveBot | In-play esports odds | Active |
| EsportsSeriesBot | Series-level esports (Bo3/Bo5) | Active |
| 10 others | Sports, ensemble, arbitrage, etc. | Mostly inactive |

### Infrastructure
- **VPS**: Ubuntu at `34.251.224.21` (16GB/4vCPU)
- **SSH**: `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21`
- **Deploy**: `bash deploy/deploy.sh` — atomic symlink swap at `/opt/polymarket-ai-v2`
- **Current deploy**: `20260319_172220` (S108 fill pipeline + S109 sizing fix)
- **Service**: `sudo systemctl restart polymarket-ai`, logs: `sudo journalctl -u polymarket-ai -f`
- **DB**: PostgreSQL `polymarket` (asyncpg), Redis for caching
- **Python 3.13**

---

## WEATHERBOT STRATEGY

1. Fetch GFS/HRRR/GEFS + ECMWF ensemble forecasts via Open-Meteo (free, no API key)
2. Fit skew-normal distribution to ensemble spread
3. Integrate CDF across each temperature bucket's bounds → model probabilities
4. Compare model probs vs market-implied probs (YES token prices)
5. Trade when edge >= 8% US / 12% international, sized by fractional Kelly (0.25)
6. Weather markets resolve when actual temperature is recorded on the target date. Most markets have 6-12 temperature buckets (e.g., "70-74°F"). Only ONE bucket resolves YES; all others resolve NO.

---

## WEATHERBOT FILE MAP

### Main File: `bots/weather_bot.py` (~4180 lines)
| Lines | Section |
|-------|---------|
| 1-58 | Imports, docstring |
| 59-220 | `__init__` — all config, caches, state dicts |
| 222-300 | Prediction logging + outcome backfill |
| 300-600 | `start()`, `stop()`, cache warming, state restore |
| 600-900 | `scan_and_trade()` — main loop entry, market discovery |
| 900-1200 | Market grouping, price enrichment, tag discovery |
| 1200-1600 | `_analyze_group()` — ensemble fetch, probability computation, edge detection |
| 1600-1700 | Opportunity construction (YES/NO sides, edge, confidence) |
| 1700-1900 | Precip/snow/wind analysis pipelines |
| 1900-2050 | Short-term override sizing, Baker-McHale factor, NBM benchmark |
| 2050-2150 | `_analyze_group_opps()` — group budget, S-T size overrides |
| 2150-2500 | `_execute_weather_trade()` — risk checks, sizing, order placement |
| 2500-2700 | Exit logic, position management |
| 2700-3100 | State persistence (Redis, DB), daily boundary handling |
| 3100-3500 | Station health, calibration, monitoring |
| 3500-3800 | Helper functions, ENSO regime, AFD parsing |
| 3800-4180 | Canary stages, observability, discovery helpers |

### Supporting Modules (`base_engine/weather/`)
| Module | Purpose |
|--------|---------|
| `forecast_client.py` | Open-Meteo API wrapper, ensemble fetching, Redis cache |
| `probability_engine.py` | Skew-normal CDF integration, bucket probability computation |
| `precipitation_engine.py` | Precipitation probability model |
| `market_mapper.py` | Question parsing → WeatherMarketGroup/TemperatureBucket |
| `station_registry.py` | 102 stations (US + intl), ICAO codes, WMO IDs |
| `model_run_monitor.py` | GFS/ECMWF/HRRR model run tracking, priority queue |
| `metar_monitor.py` | Real-time METAR observation polling, boundary crossing |
| `metar_client.py` | METAR API client |

### Shared Modules (cross-bot — changes affect ALL 15 bots)
| Module | Purpose | WeatherBot-specific |
|--------|---------|-------------------|
| `base_engine/execution/paper_trading.py` | Fill probability model, slippage, book walk, partial fills | Taker-side factor, alpha decay via `scan_start_mono` |
| `base_engine/execution/order_gateway.py` | Order routing, position tracking, market index | Volume fallback from event_data |
| `base_engine/coordination/trade_coordinator.py` | Position reservation/confirmation | WeatherBot skips coordinator for BUY (`WEATHER_SKIP_COORDINATOR_BUY=true`) |
| `base_engine/risk/bankroll_manager.py` | Kelly sizing, drawdown compression | `BotBankrollManager` handles SIZING |
| `base_engine/risk/risk_manager.py` | Position limits, exposure caps | Handles LIMITS (deprecated for sizing) |
| `config/settings.py` | All config with env var overrides | WeatherBot-specific settings below |
| `bots/base_bot.py` | Base class — `place_order()`, `calculate_bot_position_size()` | `calculate_bot_position_size()` returns SHARES |

---

## KEY STATE DICTIONARIES (in-memory, `weather_bot.py`)

```python
_group_exposure: Dict[str, float]    # "city:date" → USD deployed (lock-protected, DB-restored)
_city_exposure: Dict[str, float]     # city → total USD deployed (lock-protected, DB-restored)
_recently_exited: Dict[str, float]   # market_id → monotonic time (4hr cooldown, Redis-persisted)
_fill_fail_tracker: Dict[str, Tuple] # market_id → (consec_fails, last_mono)
_market_group_cache: Dict[str, Tuple] # market_id → (group_key, city, cost_usd)
_daily_pnl: float                     # today's realized P&L
_open_position_markets: set           # market_ids with open positions
_position_details: Dict[str, dict]    # market_id → {side, size, entry_price}
```

---

## SIZING PIPELINE (how trade size is computed)

Located in `_execute_weather_trade()` (lines ~2240-2500):

1. **Short-term override** (lines 2255-2280): If `_st_size_override` exists, use it directly
2. **Kelly sizing** (lines 2300-2400):
   - `BotBankrollManager.calculate_kelly_bet()` → returns a suggested amount
   - `calculate_bot_position_size(confidence, price)` → returns **SHARES** (`size_usd / price`)
   - WeatherBot converts: `size = max(_min_trade, kelly_shares * opp["price"] * combined_boost)` → this is in **USD**
   - Boosts: expiry (1.0-2.0x), regime (1.0-1.2x), jump, NBM (1.15x), Baker-McHale (0.50-1.0x)
3. **Min trade floor**: `WEATHER_MIN_TRADE_USD=5.0` — sizes below $5 rejected
4. **Exposure locks**: Atomic reservation under `_exposure_lock` for group + city exposure
5. **USD → shares conversion** (S109 fix): `_size_shares = size / opp["price"]` before `place_order()`
6. **`place_order(size=_size_shares)`** — paper engine expects shares, not USD

---

## FILL PROBABILITY MODEL

Located in `base_engine/execution/paper_trading.py`. Determines whether paper trades succeed.

### 5 Multiplicative Factors
```
fill_probability = price_depth × size_impact × spread × time_of_day × sqrt_participation
```
1. **Price-depth** (`0.3 + 0.7 * 4*p*(1-p)`): Best at 0.50, worst at extremes. Range: 0.30–1.00
2. **Size-impact** (`1 - 0.4*(size/max_size)`): Larger orders fill worse. Range: 0.60–1.00
3. **Spread** (`max(0.1, 1 - spread*10)`): Wide spread = low fill. Range: 0.10–1.00
4. **Time-of-day** (US market hours best): Range: 0.50–1.00
5. **Sqrt participation** (`sqrt(volume * participation_rate / 10000)`): Volume proxy

### Additional Multipliers
- **Taker-side factor**: `PAPER_TAKER_SIDE_FACTOR=0.85` (S108: was 0.55)
- **Kyle's lambda**: Adverse selection ~0.7x
- **Resolution proximity**: <30min = 0.5x fill, 3.0x slippage

### Current Fill Rate: ~14.7%
Weather markets are genuinely thin. The model is accurate — this is the realistic baseline, not a bug.

---

## ALL CHANGES MADE IN SESSIONS 107-109

### Commit `454e616` (S109): Sizing units bug fix — **DEPLOYED**
**Root cause**: `calculate_bot_position_size()` returns SHARES. WeatherBot converted shares→USD (`kelly_shares * price`) then passed USD to `place_order()` which expects SHARES. Result: positions undersized by 1/price factor (3-10x too small at low prices).

**Fix** (3 lines in `weather_bot.py` ~line 2459):
```python
# S109: Convert USD to shares for place_order (paper engine expects shares).
_size_shares = size / opp["price"]
result = await self.place_order(..., size=_size_shares, ...)
```
Plus log update: `size_usd=round(size, 2), size_shares=round(_size_shares, 2)`

**Verification**: Pre-fix avg cost $28.47 (median $13.22) → post-fix avg cost $35.87 (median $22.93). Positions now consistently in $5-$200 range. Confirmed with trade: `size_usd=93.71, size_shares=120.91`, partial fill to 62.39 shares = $49.35 actual cost.

### Commit `2c4fb3f` (S108): Fill pipeline — 4 fixes
1. **Taker factor 0.55→0.85** (`config/settings.py`): All bots are taker-style. Affects ALL bots.
2. **bestAsk pre-filter** (`weather_bot.py:2190-2220`): Skips trade if `confidence <= bestAsk` (no edge after depth)
3. **Volume passthrough** (`weather_bot.py:2449` + `order_gateway.py:727-728`): Passes real CLOB volume instead of $50K fallback
4. **Same-side dedup** (`weather_bot.py:2160-2171`): Checks `_position_details` for side-aware duplicate blocking

### Commit `ab3c018` (S107): Ghost position fix
- `paper_trading.py:559-562`: Idempotent memory returns `success: False` (was True)
- `order_gateway.py:795`: Guard `_filled_size > 0` before `confirm_position()`
- Closed 137 ghost positions (size=0) on VPS

### Commit `1f60153` (S107): Sizing pipeline tightening
- Re-entry cooldown 15min→4hr (`WEATHER_EXIT_COOLDOWN_SECS=14400`)
- Min trade floor $1→$5 (`WEATHER_MIN_TRADE_USD=5.0`)
- Baker-McHale floor 0.50 (`WEATHER_BM_FLOOR=0.50`)
- Drawdown compression removed from `combined_boost` (was double-counted with BotBankrollManager)

---

## CURRENT SYSTEM STATE (as of 2026-03-19 23:30 UTC)

### Open Positions
| Metric | Value |
|--------|-------|
| Total open | 193 |
| Target dates | Mar 19-23, Mar 31 + 20 without date |
| Cities | 24 |
| Total deployed | $6,301 |
| Avg cost per position | $32.65 |
| YES / NO split | 66 YES / 127 NO |
| Total unrealized P&L | -$2.95 |

### Positions by Target Date
| Date | Count | Deployed | uP&L |
|------|-------|----------|------|
| Mar 19 | 9 | $179 | **+$27.25** (resolving today) |
| Mar 20 | 37 | $1,993 | -$30.21 (lines moving) |
| Mar 21 | 35 | $1,179 | $0.00 (flat) |
| Mar 22 | 30 | $1,293 | $0.00 (flat) |
| Mar 23 | 60 | $1,037 | $0.00 (flat) |
| Mar 31 | 2 | $6 | $0.00 |
| No date | 20 | $614 | $0.00 |

### Positions with Price Movement (9 of 193)
Only Mar 19 (resolving today) and Mar 20 (tomorrow) positions have moved:
| City | Date | Side | Cost | Entry→Now | uP&L |
|------|------|------|------|-----------|------|
| Seattle | Mar 19 | NO | $37.63 | 0.50→0.78 | **+$21.07** |
| London | Mar 20 | NO | $190.19 | 0.69→0.78 | +$24.81 |
| NYC | Mar 19 | NO | $17.99 | 0.61→0.84 | +$6.78 |
| Atlanta | Mar 19 | NO | $53.34 | 0.79→0.82 | +$1.69 |
| London | Mar 20 | NO | $116.62 | 0.86→0.71 | -$21.02 |
| Toronto | Mar 20 | NO | $84.83 | 0.89→0.75 | -$13.82 |
| Buenos Aires | Mar 20 | NO | $107.10 | 0.86→0.82 | -$5.60 |
| Buenos Aires | Mar 20 | NO | $109.86 | 0.76→0.74 | -$3.61 |
| Miami | Mar 19 | NO | $37.46 | 0.90→0.85 | -$2.29 |

### All-Time P&L
| Event Type | Count | Realized P&L |
|------------|-------|-------------|
| ENTRY | 3,350 | $0.00 |
| EXIT | 281 | **+$676.85** |
| RESOLUTION | 678 | **+$2,283.00** |
| **TOTAL** | | **+$2,959.85** |

### YES/NO Asymmetry (Confirmed — Both Profitable)
| Side | Resolved | Win Rate | Total P&L | Avg P&L |
|------|----------|----------|-----------|---------|
| NO | 407 | **76.7%** | +$1,238 | +$3.04 |
| YES | 153 | **24.8%** | +$752 | +$4.92 |

### City P&L (top/bottom)
| City | Resolved | Win Rate | P&L |
|------|----------|----------|-----|
| London | 10 | 70.0% | **+$63** |
| Seoul | 10 | 80.0% | +$19 |
| Chicago | 10 | 60.0% | +$17 |
| NYC | 17 | 41.2% | +$14 |
| Dallas | 8 | 50.0% | **-$83** |
| Wellington | 10 | 30.0% | -$64 |

### Latest Trades (Post-Sizing Fix — Correct Sizes)
| Time | City | Side | Shares | Price | Cost |
|------|------|------|--------|-------|------|
| 23:21 | Madrid | NO | 20.94 | 0.79 | $16.54 |
| 23:11 | Milan | YES | 48.06 | 0.19 | $9.13 |
| 23:11 | São Paulo | YES | 331.09 | 0.23 | $76.15 |
| 23:06 | Warsaw | NO | 39.77 | 0.91 | $36.19 |
| 23:06 | Lucknow | NO | 147.88 | 0.85 | $125.70 |
| 23:06 | Seattle | NO | 84.11 | 0.82 | $68.97 |

Sizing fix confirmed working: $5-$200 cost range, median ~$23.

---

## CURRENT CONFIGURATION (VPS .env)

### WeatherBot-Specific
```
WEATHER_MIN_EDGE=0.08                    # 8% min edge (US cities)
WEATHER_INTL_MIN_EDGE=0.12               # 12% min edge (international)
WEATHER_MAX_PER_GROUP_USD=200.0           # Max per city+date group
WEATHER_DAILY_LOSS_LIMIT=500.0            # Stop if daily P&L < -$500
WEATHER_MAX_CORRELATED_EXPOSURE=500.0     # Max per city (all dates)
WEATHER_KELLY_FRACTION=0.25              # Kelly multiplier
WEATHER_DEFAULT_SIZE=100.0               # Default position size
WEATHER_MAX_LEAD_TIME_HOURS=168.0        # Max 7 days ahead
WEATHER_EXIT_COOLDOWN_SECS=14400         # 4hr re-entry cooldown
WEATHER_BM_FLOOR=0.50                    # Baker-McHale minimum factor
WEATHER_MIN_TRADE_USD=5.0                # Min position size ($5 floor)
WEATHER_MAX_POSITIONS=500                # Position cap
WEATHER_SKIP_COORDINATOR_BUY=true        # Bypass TradeCoordinator for buys
WEATHER_HOLD_HOURS_BEFORE_RESOLUTION=48  # Hold window for expiry boost
CANARY_STAGE=4                           # 100% capital
```

### Cross-Bot
```
PAPER_TAKER_SIDE_FACTOR=0.85             # Taker fill discount (S108: was 0.55)
SIMULATION_MODE=true                     # Paper trading
PHASE_MAX_BET_USD=1000                   # Phase cap
ALL BOTS: capital=$20000, max_bet=$300, max_daily=$10000, kelly=0.25
```

---

## P&L DATA MODEL

### Authority: `trade_events` table
- **NEVER** read `paper_trades` for P&L — it's legacy
- Event types: ENTRY, EXIT, RESOLUTION
- P&L is in the `realized_pnl` column (NOT `event_data->>'resolution'` which is often empty)
- Partitioned by `event_time` (monthly)
- Immutability trigger: `trg_trade_events_immutable` prevents DELETE/UPDATE

### P&L Formulas (ALL sides — NEVER invert for NO)
```
cost = entry_price × size
unrealized_pnl = (current_price - entry_price) × size
```

### Position Tracking
- `positions` table: `opened_at`, `status` (open/closed), NO `closed_at`/`updated_at`
- For closed position data → query `trade_events WHERE event_type='EXIT'`
- `entry_cost` in positions = friction cost (slippage+fees), NOT total capital. Actual capital = `size * entry_price`

---

## WEBSOCKET ARCHITECTURE

| Channel | Purpose | Health |
|---------|---------|--------|
| WebSocketManager | Market prices, order books | 30s ping, circuit breaker |
| UserOrderWebSocket | Own order/trade events | Exponential backoff 2→60s |
| RTDSWebSocket | Global trade feed (MirrorBot only) | 5s ping, stale detection |

Files: `base_engine/data/websocket_manager.py`, `user_order_websocket.py`, `rtds_websocket.py`

---

## OUTSTANDING ITEMS (PRIORITIZED)

### P2 — Active Monitoring
- **Fill rate**: Stable at ~14.7%. Accept as realistic for thin weather markets unless user wants to push higher.
- **Position sizes**: Post-fix median $23, range $5-$200. Verified working correctly.
- **Mar 19 positions**: 9 positions resolving today. Net uP&L +$27.25. Seattle NO (+$21.07) is the big winner.
- **Mar 20 positions**: 37 positions resolving tomorrow. Net uP&L -$30.21. London NO (-$21) and Toronto NO (-$14) are dragging.

### P2 — City P&L Monitoring
- **Dallas** (-$83, 8 resolutions) and **Wellington** (-$64, 10 resolutions) are worst
- Sample too small for config changes. Revisit after 30+ resolutions per city.

### P3 — Munich Monitoring
- Only 1 resolution (+$1.13). Need 30+ for evaluation.

### P3 — NO vs YES Asymmetry
- 76.7% NO WR vs 24.8% YES WR — both sides profitable, no action needed
- YES compensates with higher avg P&L (+$4.92 vs +$3.04)

### P4 — Open-Meteo 429s on Cold Start
- 129 rate limit errors on first scan after restart (cold cache, 421 API calls)
- Resolves naturally as Redis cache warms. Transient, not a concern.

### P5 — Deferred
- Kalshi cross-platform arbitrage (8-16h effort)
- Remove diagnostic logging (session_factory warning, RTDS raw samples)

---

## CRITICAL TRAPS (DO NOT BREAK)

1. **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
2. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL
3. **`place_order(size=...)` expects SHARES not USD** — WeatherBot converts via `_size_shares = size / price`
4. **`entry_cost` in positions** = friction cost, NOT total capital. Actual capital = `size * entry_price`
5. **`calculate_bot_position_size()` returns SHARES** (`size_usd / price` at line 576 of base_bot.py)
6. **BotBankrollManager handles SIZING; risk_manager handles LIMITS**. Both must pass.
7. **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer
8. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
9. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
10. **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
11. **Python 3.13 scoping**: `from X import Y` inside function body → local for entire function. Any use before that import → `UnboundLocalError`.
12. **websockets v15**: `import websockets.exceptions` must be explicit (lazy-loads)
13. **Paper trading IS production** — never skip features because "we're only paper trading"
14. **positions table**: NO `closed_at`, NO `updated_at` columns
15. **prediction_log columns**: NO `rejection_reason` — use `trade_executed` flag
16. **`traded_markets.bot_names`**: TEXT column (not array), use `LIKE '%BotName%'`
17. **Alpha decay requires `scan_start_mono` in event_data**: Only WeatherBot passes it
18. **`paper_trades` has NO `metadata` JSONB column**
19. **Resolution backfill excludes SELL trades** — SELL P&L computed at exit time
20. **trade_events immutability trigger**: Must `DISABLE TRIGGER` then re-enable for data cleanup
21. **RESOLUTION event idempotency**: ON CONFLICT broken on partitioned tables → uses atomic INSERT...SELECT with WHERE NOT EXISTS
22. **Ghost positions fixed (S107)**: Idempotent memory returns `success: False`, order gateway guards `_filled_size > 0`
23. **`WEATHER_SKIP_COORDINATOR_BUY=true`** — WeatherBot buys bypass TradeCoordinator → direct INSERT into positions
24. **`_market_meta_cache` in MirrorBot**: 3-tuple `(cat, ttr, expiry_monotonic)`. NEVER expand.
25. **VPS deploys via `deploy.sh`**: atomic symlink swap. Working tree ≠ VPS ≠ git HEAD.
26. **Open-Meteo 429s on cold start** are transient — Redis cache warms after first scan
27. **`system_kv` table**: Generic key-value store. Used for canary stage persistence. Key='canary_stage'.
28. **BOT_REGISTRY = 14 bots** — shared module change requires all 14 verified

---

## DEPLOY & ROLLBACK

### Deploy
```bash
# From local working tree
bash deploy/deploy.sh
# Atomic: tar → upload → extract → migrations → symlink swap → restart → health check
```

### Current Deploy
```
/opt/pa2-releases/20260319_172220  (S108 fill pipeline + S109 sizing fix)
```

### Rollback
```bash
# Full rollback to previous release
ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@34.251.224.21
sudo ln -sfn /opt/pa2-releases/20260319_161025 /opt/polymarket-ai-v2
sudo systemctl restart polymarket-ai
```

### Partial Rollback (env var only, no code)
```bash
# Edit /opt/polymarket-ai-v2/.env on VPS, then:
sudo systemctl restart polymarket-ai
```
| Change | Revert |
|--------|--------|
| Taker factor 0.85→0.55 | `PAPER_TAKER_SIDE_FACTOR=0.55` |
| 4hr cooldown→15min | `WEATHER_EXIT_COOLDOWN_SECS=900` |
| $5 min→$1 min | `WEATHER_MIN_TRADE_USD=1.0` |
| BM floor→uncapped | `WEATHER_BM_FLOOR=0.0` |

---

## POST-DEPLOY MONITORING COMMANDS

```bash
# WeatherBot scan health
sudo journalctl -u polymarket-ai -f | grep -i "weather"

# Fill rate (target ~15%)
sudo journalctl -u polymarket-ai --since '30 min ago' | grep 'Order latency.*Weather' | grep -oP 'success=(True|False)' | sort | uniq -c

# Position sizes (actual cost = size * entry_price)
sudo -u postgres psql -d polymarket -c "
SELECT round((size * entry_price)::numeric, 2) as actual_cost, side, count(*)
FROM positions WHERE source_bot='WeatherBot' AND opened_at > NOW() - INTERVAL '2 hours'
GROUP BY 1, 2 ORDER BY 1;"

# Ghost positions (should be 0)
sudo -u postgres psql -d polymarket -c "SELECT count(*) FROM positions WHERE source_bot='WeatherBot' AND status='open' AND size=0;"

# City P&L
sudo -u postgres psql -d polymarket -c "
WITH e AS (SELECT DISTINCT ON (market_id) market_id, event_data->>'city' as city FROM trade_events WHERE bot_name='WeatherBot' AND event_type='ENTRY' AND event_data ? 'city' ORDER BY market_id, event_time),
r AS (SELECT DISTINCT ON (market_id) market_id, realized_pnl FROM trade_events WHERE bot_name='WeatherBot' AND event_type='RESOLUTION' AND realized_pnl IS NOT NULL ORDER BY market_id, event_time)
SELECT e.city, count(*), sum(CASE WHEN r.realized_pnl>0 THEN 1 ELSE 0 END) wins, round(sum(r.realized_pnl)::numeric,2) pnl FROM e JOIN r ON e.market_id=r.market_id GROUP BY e.city ORDER BY pnl;"

# Open position summary
sudo -u postgres psql -d polymarket -c "
SELECT count(*) as open, round(sum(size*entry_price)::numeric,2) as deployed, round(sum(unrealized_pnl)::numeric,2) as upnl
FROM positions WHERE source_bot='WeatherBot' AND status='open';"

# All-time P&L
sudo -u postgres psql -d polymarket -c "
SELECT event_type, count(*), round(sum(COALESCE(realized_pnl,0))::numeric,2) as pnl
FROM trade_events WHERE bot_name='WeatherBot' GROUP BY event_type ORDER BY event_type;"
```

---

## GIT HISTORY (WeatherBot-relevant commits, newest first)

```
454e616 fix(weather): S109 — sizing units bug: pass shares not USD to place_order
2c4fb3f fix(weather): S107 — fill pipeline: taker factor 0.85, bestAsk pre-filter, volume passthrough, same-side dedup
ab3c018 fix(weather): S107 — ghost position bug: idempotent memory created size=0 positions
1f60153 fix(weather): S107 — 4hr re-entry cooldown + sizing pipeline fix (min $5, BM floor, drawdown dedup)
10c5f23 fix(weather): S106 — taker-side flat factor + probability engine fallback + stale positions fix
a955a15 fix(weather): S104b — blind review fixes: alpha decay BUY-only, cache cleanup
7758bec feat(weather): S104 — fill quality logging + exposure leak fix + daily counter
ac1a5cc fix(weather): S103 — positions never created when coordinator BUY skipped
c5b8e72 fix(weather): S102b — 5 silent bugs found via deep audit
163f6e6 feat(weather): S102 — HRRR detection, METAR Redis persistence, GEFS lead-time weighting
653e6ff fix(weather): S101b — raise SCAN_MARKET_LIMIT 800→1500 + add Milan station
```

---

## SESSION HISTORY

| Session | Date | Focus |
|---------|------|-------|
| S92 | 03-15 | P1 jump detection, P2 NBM benchmark |
| S95 | 03-16 | 4 paper trading elevations |
| S97 | 03-16 | 3 stations, P&L breakdown script |
| S100 | 03-17 | Alpha decay, canary persistence, SSH timeouts |
| S101 | 03-17 | Graduated expiry boost, city digest |
| S102 | 03-17 | METAR Redis daily max, exposure lock |
| S104 | 03-18 | Fill quality logging, exposure leak fix, daily counter, alpha decay BUY-only |
| S106 | 03-18 | Taker-side flat factor, probability engine fallback, stale positions |
| S107 | 03-19 | Munich investigation, bet sizing (cooldown/min trade/BM floor/drawdown), ghost position fix |
| S108 | 03-19 | Fill pipeline: taker factor 0.85, bestAsk pre-filter, volume passthrough, same-side dedup |
| S109 | 03-19 | Deploy S108, sizing units bug fix, P&L analysis, position audit |

---

## USER WORKING STYLE

- Demands root-cause analysis. No assumptions, no guesses — show real data.
- Hates fluff. Keep responses short and direct.
- Gets frustrated when data looks wrong — always validate before presenting ("there is literally no way 343 are deployed and open").
- Wants to see actual positions with dates, costs, prices — not summaries.
- Scope-locked sessions: WeatherBot only unless explicitly told otherwise.
- Expects you to just do the work, not ask permission for obvious next steps.
- Prefers tables with real numbers over explanations.
- Will curse when frustrated. Don't take it personally, just fix the problem.

---

## WHAT TO DO NEXT

No explicit task was assigned. The user was reviewing open position data by target date. Likely next steps:

1. **Monitor Mar 19 resolutions** — 9 positions resolving today, +$27.25 uP&L
2. **Monitor Mar 20 positions** — 37 positions, -$30.21 uP&L, lines are moving
3. **Dallas/Wellington city P&L** — revisit after 30+ resolutions
4. **Munich** — 1 resolution, need 30+
5. **Fill rate** — stable at ~15%, accept as baseline
6. **Position size distribution** — post-fix sizes look correct ($5-$200 range)

Wait for user instruction before making any code changes.
