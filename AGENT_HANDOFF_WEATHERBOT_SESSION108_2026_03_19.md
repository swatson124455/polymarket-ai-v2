# AGENT HANDOFF — WeatherBot Session 108 (2026-03-19)

## Session Summary
**Focus**: Fill pipeline bottleneck fix (92% trade failure → target 25-35% fill rate) + ghost position fix + Munich investigation + bet sizing
**Bot scope**: WeatherBot primary. Cross-bot changes in `order_gateway.py`, `paper_trading.py`, `config/settings.py` (shared modules).
**Tests**: 1642 passed, 0 failed, 8 skipped
**Key commits this session**: `ab3c018` (ghost position fix), `2c4fb3f` (fill pipeline: 4 fixes)
**Prior session commit**: `10c5f23` (S106 taker-side flat factor + probability engine fallback + stale positions)

---

## COMPLETE SYSTEM ARCHITECTURE

### What Is This System?
A 15-bot automated Polymarket trading system. Currently in **paper trading mode** (`SIMULATION_MODE=true`). Paper trading is PRODUCTION — the only difference from live is the final order submission. $20K capital allocated, $300 max bet per bot.

### Bot Roster
| Bot | Purpose | Active |
|-----|---------|--------|
| **WeatherBot** | Temperature/precip/snow/wind bucket markets via NOAA ensembles | YES — primary focus |
| **MirrorBot** | Copy-trades elite Polymarket wallets via RTDS feed | YES |
| **EsportsBot** | Pre-match esports (LoL/CS2/Valorant/Dota2) via PandaScore + Glicko | YES |
| **EsportsLiveBot** | In-play esports odds | YES |
| **EsportsSeriesBot** | Series-level esports (Bo3/Bo5) | YES |
| 10 others | Sports, ensemble, arbitrage, etc. | Mostly inactive |

### Infrastructure
- **VPS**: Ubuntu at 34.251.224.21 (16GB/4vCPU), SSH key `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **Deploy**: `deploy.sh` does atomic symlink swap at `/opt/polymarket-ai-v2`
- **Service**: `systemctl` unit `polymarket-ai`, logs via `journalctl -u polymarket-ai -f`
- **DB**: PostgreSQL `polymarket` (asyncpg), Redis for caching
- **Python 3.13** — CRITICAL: `from X import Y` inside function body shadows module-level; `websockets.exceptions` must be explicit import

---

## WEATHERBOT DEEP DIVE

### Strategy
1. Fetch GFS/HRRR/GEFS + ECMWF ensemble forecasts via Open-Meteo (free, no key)
2. Fit skew-normal distribution to ensemble spread
3. Integrate CDF across each temperature bucket's bounds → model probabilities
4. Compare model probs vs market-implied probs (YES prices)
5. Trade when edge ≥ 8% US / 12% international, sized by fractional Kelly (0.25)

### File: `bots/weather_bot.py` (4180 lines)
The main bot file. Key sections:

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

### Key State Dictionaries
```python
_group_exposure: Dict[str, float]    # "city:date" → USD deployed (lock-protected)
_city_exposure: Dict[str, float]     # city → total USD deployed
_recently_exited: Dict[str, float]   # market_id → monotonic time (4hr cooldown)
_fill_fail_tracker: Dict[str, Tuple] # market_id → (consec_fails, last_mono)
_market_group_cache: Dict[str, Tuple] # market_id → (group_key, city, cost_usd)
_daily_pnl: float                     # today's realized P&L
```

### Supporting Modules (under `base_engine/weather/`)
| Module | Purpose |
|--------|---------|
| `forecast_client.py` | Open-Meteo API wrapper, ensemble fetching, Redis cache |
| `probability_engine.py` | Skew-normal CDF integration, bucket probability computation |
| `precipitation_engine.py` | Precipitation probability model |
| `market_mapper.py` | Question parsing → WeatherMarketGroup/TemperatureBucket |
| `station_registry.py` | 102 stations (US + intl), ICAO codes, WMO IDs, health monitoring |
| `model_run_monitor.py` | GFS/ECMWF/HRRR model run tracking, priority queue |
| `metar_monitor.py` | Real-time METAR observation polling, boundary crossing detection |
| `metar_client.py` | METAR API client |

### Shared Modules (cross-bot, changes affect ALL 15 bots)
| Module | Purpose | WeatherBot-specific |
|--------|---------|-------------------|
| `base_engine/execution/paper_trading.py` | Fill probability, slippage, book walk, partial fills | Taker-side factor, alpha decay via `scan_start_mono` |
| `base_engine/execution/order_gateway.py` | Order routing, position tracking, market index | Volume fallback from event_data |
| `base_engine/coordination/trade_coordinator.py` | Position reservation/confirmation | WeatherBot skips coordinator for BUY (`WEATHER_SKIP_COORDINATOR_BUY=true`) |
| `base_engine/risk/bankroll_manager.py` | Kelly sizing, drawdown compression | `BotBankrollManager` handles SIZING |
| `base_engine/risk/risk_manager.py` | Position limits, exposure caps | Handles LIMITS (deprecated for sizing) |
| `config/settings.py` | All config with env var overrides | WeatherBot-specific settings below |

---

## ALL CHANGES MADE IN S107 (TWO COMMITS)

### Commit `ab3c018`: Ghost position bug fix
**Root cause**: `paper_trade_idempotent_memory` in `paper_trading.py:559-562` returned `{"success": True, "filled": 0}` for duplicate correlation IDs. `order_gateway.py` saw `success=True` → called `confirm_position(size=0)` → 137 ghost positions (status='open', size=0) created. These blocked re-entry on 137 markets.

**Fix 1 — paper_trading.py:559-562**: Idempotent memory now returns `success: False`:
```python
if correlation_id and correlation_id in self._pending_correlation_ids:
    return {"success": False, "idempotent": True, "order_id": "pending", "error": "duplicate: already pending"}
```

**Fix 2 — order_gateway.py:795**: Guard `_filled_size > 0` before calling `confirm_position`. Zero-fill successes release reservation instead.

**DB cleanup**: 137 ghost positions closed on VPS: `UPDATE positions SET status='closed' WHERE bot_id='WeatherBot' AND status='open' AND size=0`

### Commit `2c4fb3f`: Fill pipeline — 4 fixes for 92% trade failure rate

Post-deploy review showed only 2 of 26 paper trade attempts succeeded (8% fill rate). Diagnosed 3 compounding bottlenecks:

| Failure reason | Count | % |
|---|---|---|
| Fill probability too low (17-33%) | 12 | 46% |
| Slippage eats edge | 7 | 27% |
| Idempotent duplicate | 4 | 15% |
| Other | 3 | 12% |

**Fix 1 — config/settings.py**: `PAPER_TAKER_SIDE_FACTOR` raised from 0.55 to 0.85.
- 0.55 modeled resting limit orders (45% same-side taker chance → unfillable). But ALL bots are taker-style (crossing the spread). 0.85 reflects only queue/timing risk (~15%).
- **Affects ALL bots** — this is intentionally correct for all bots since paper trading simulates taker orders.
- Biggest single lever: raises fill probability from ~0.20 to ~0.31 immediately.

**Fix 2 — weather_bot.py:2190-2220**: bestAsk pre-filter.
- Looks up `bestAsk` from order gateway's `_market_index` / `_market_index_by_cid`
- Skips trade if `confidence <= bestAsk` (no edge after book depth)
- Prevents 27% of failures (slippage-eats-edge) from wasting paper engine calls
- Uses `isinstance(dict)` checks on market index to handle mock/uninitialized states
- Fail-open: if lookup fails, proceeds without filter (paper engine's own book walk still catches)
- Also stores `_clob_volume` in opp dict for Fix 3

**Fix 3 — weather_bot.py:2449 + order_gateway.py:727-728**: Pass CLOB volume to fill model.
- WeatherBot passes `volume_24h` in event_data dict when calling `place_order()`
- OrderGateway reads `event_data.get("volume_24h")` as fallback when `_market_index` lookup returns no volume
- Replaces $50K generic fallback with actual market volume → more accurate fill probabilities

**Fix 4 — weather_bot.py:2160-2171**: Same-side duplicate entry protection.
- 700 markets had duplicate ENTRY events (up to 15x on one market, avg 3x)
- Old code checked `_open_position_markets` (market_id only, no side awareness)
- New code checks `_position_details` (tracks side) → only blocks same market_id AND same side
- Mirrors MirrorBot's S109 fix pattern
- `WEATHER_SKIP_COORDINATOR_BUY=true` bypasses the other dedup layer, making this essential

### Earlier S107 changes (prior commits, already deployed)
5. **Re-entry cooldown 15min→4hr** — `WEATHER_EXIT_COOLDOWN_SECS=14400` (configurable). Per market_id, NOT per city.
6. **Min trade floor $1→$5** — `WEATHER_MIN_TRADE_USD=5.0`. Eliminates dust positions (67% of entries were <10 cents).
7. **Baker-McHale floor 0.50** — `WEATHER_BM_FLOOR=0.50`. Prevents extreme shrinkage from ensemble spread.
8. **Drawdown compression removed from combined_boost** — Was double-counted (also in BotBankrollManager).
9. **Ghost position bug fix** — See commit `ab3c018` above.

---

## FILL PROBABILITY MODEL (CRITICAL UNDERSTANDING)

Located in `base_engine/execution/paper_trading.py`. This is the heart of whether trades succeed or fail.

### 5 Multiplicative Factors (lines 128-168)
```
fill_probability = price_depth × size_impact × spread × time_of_day × sqrt_participation
```

1. **Price-depth** (`0.3 + 0.7 * 4*p*(1-p)`): U-shaped — best at 0.50, worst at extremes. Range: 0.30–1.00
2. **Size-impact** (`1 - 0.4*(size/max_size)`): Larger orders fill worse. Range: 0.60–1.00
3. **Spread factor** (`max(0.1, 1 - spread*10)`): Wide spread = low fill. Range: 0.10–1.00
4. **Time-of-day** (US market hours best, nights/weekends worst): Range: 0.50–1.00
5. **Sqrt participation** (`sqrt(volume * participation_rate / 10000)`): Volume proxy. Range: varies

### Additional Multipliers Applied After Base
- **Taker-side factor** (lines 763-773): `PAPER_TAKER_SIDE_FACTOR=0.85` when no taker_side data
- **Kyle's lambda** (lines 650-660): Adverse selection multiplier ~0.7x
- **Resolution proximity** (lines 186-202): <30min = 0.5x fill, 3.0x slippage

### Slippage Model
- **Heuristic slippage**: ~7 bps typically (minimal)
- **Book walk VWAP** (lines 641-669): L2 order book walk computing real fill cost. This is where most "slippage eats edge" failures come from — thin weather markets have displayed price much lower than actual fill cost
- **Resolution proximity penalty**: 2-3x multiplier for trades close to resolution

### Key Insight
The slippage model is working correctly. "Slippage eats edge" failures are NOT excessive slippage — they're the book walk accurately revealing that thin weather markets cost much more to fill than the displayed price suggests. The bestAsk pre-filter (Fix 2) catches these before wasting a paper engine call.

---

## CURRENT CONFIGURATION (VPS .env + settings.py defaults)

### WeatherBot-specific
```
WEATHER_MIN_EDGE=0.08                    # 8% minimum edge for US cities
WEATHER_INTL_MIN_EDGE=0.12               # 12% for international
WEATHER_MAX_PER_GROUP_USD=200.0           # Max per city+date group
WEATHER_DAILY_LOSS_LIMIT=500.0            # Stop trading if daily P&L < -$500
WEATHER_MAX_CORRELATED_EXPOSURE=500.0     # Max per city (all dates)
WEATHER_KELLY_FRACTION=0.25              # Kelly multiplier
WEATHER_DEFAULT_SIZE=100.0               # Default position size
WEATHER_MAX_LEAD_TIME_HOURS=168.0        # Max 7 days ahead
WEATHER_EXIT_COOLDOWN_SECS=14400         # 4hr re-entry cooldown (was 900s)
WEATHER_BM_FLOOR=0.50                    # Baker-McHale minimum factor
WEATHER_MIN_TRADE_USD=5.0                # Min position size (was $1)
WEATHER_MAX_POSITIONS=500                # Position cap
WEATHER_SKIP_COORDINATOR_BUY=true        # Bypass TradeCoordinator for buys
WEATHER_HOLD_HOURS_BEFORE_RESOLUTION=48  # Hold window for expiry boost
```

### Cross-bot (affects all 15 bots)
```
PAPER_TAKER_SIDE_FACTOR=0.85             # Taker-side fill discount (was 0.55)
SIMULATION_MODE=true                     # Paper trading
PHASE_MAX_BET_USD=1000                   # Phase-level cap
```

### Per-bot bankroll
```
ALL BOTS: capital=$20000, max_bet=$300, max_daily=$10000, kelly=0.25
```

---

## MUNICH INVESTIGATION (TABLED — MONITOR 2 WEEKS)

- **Station**: EDDM (Munich Airport) — setup correct, ICON-D2 model
- **All-time P&L**: 6 exits, 2W/4L, -$78.13
- **Root cause**: Re-entry loop on one market (4 consecutive losing re-entries). Fixed by 4hr cooldown.
- **Recommendation**: Monitor until 30+ exits. Sample too small to condemn.
- **European comparison**: Munich -$78, Ankara -$11, Paris +$3, London +$31

---

## SIZING PIPELINE (how trade size is computed)

Located in `_execute_weather_trade()` (lines 2240-2430):

1. **Short-term override** (lines 2255-2280): If `_st_size_override` exists, use it directly (capped by exposure limits)
2. **Kelly sizing** (lines 2300-2400): `BotBankrollManager.calculate_kelly_bet()` with boosts:
   - Expiry boost (1.0-2.0x based on lead time)
   - Regime boost (1.0-1.2x from cross-city consensus)
   - Jump boost (from model run / METAR boundary events)
   - NBM benchmark boost (1.15x when NBM agrees)
   - Baker-McHale factor (0.50-1.0x from ensemble spread, floored at `WEATHER_BM_FLOOR`)
   - Drawdown compression REMOVED from combined_boost (was double-counted)
3. **Min trade floor**: `WEATHER_MIN_TRADE_USD=5.0` — sizes below this are rejected
4. **Exposure locks**: Atomic reservation under `_exposure_lock` for group + city exposure
5. **Liquidity guardian**: If available, check slippage vs edge

---

## P&L DATA MODEL

### Authoritative source: `trade_events` table
- **NEVER** read `paper_trades` for P&L — it's legacy
- Event types: ENTRY, EXIT, RESOLUTION
- Partitioned by `event_time` (monthly)
- Immutability trigger: `trg_trade_events_immutable` prevents DELETE/UPDATE

### P&L formulas (ALL sides, NEVER invert for NO)
```
cost = entry_price × size
unrealized_pnl = (current_price - entry_price) × size
```

### Position tracking
- `positions` table: `opened_at`, `status` (open/closed), NO `closed_at`/`updated_at`
- For closed position data → query `trade_events WHERE event_type='EXIT'`
- Ghost positions: Fixed in S107. Were blocking re-entry on 137 markets.

---

## WEBSOCKET ARCHITECTURE

Three channels, all with reconnection logic:

| Channel | URL | Purpose | Health |
|---------|-----|---------|--------|
| **WebSocketManager** | `wss://ws-subscriptions-clob.polymarket.com/ws/market` | Market prices, order books | 30s ping, circuit breaker after 10 failures |
| **UserOrderWebSocket** | `wss://.../ws/user` | Own order/trade events | Exponential backoff 2→60s |
| **RTDSWebSocket** | `wss://ws-live-data.polymarket.com` | Global trade feed (MirrorBot) | 5s manual PING, stale detection |

### Key files
- `base_engine/data/websocket_manager.py` (377 lines)
- `base_engine/data/user_order_websocket.py` (168 lines)
- `base_engine/data/rtds_websocket.py` (209 lines)

---

## FILES MODIFIED IN S107 (ALL COMMITS)

| File | Lines | Changes |
|------|-------|---------|
| `config/settings.py` | ~80 | +3 settings (cooldown, BM floor, min trade), taker factor 0.55→0.85 |
| `bots/weather_bot.py` | 4180 | Cooldown (5 loc), min trade (3), BM floor (1), drawdown removal (1), bestAsk pre-filter (30 lines), volume passthrough (1), same-side dedup (12 lines) |
| `base_engine/execution/paper_trading.py` | ~800 | Taker-side flat factor, idempotent memory returns success=False |
| `base_engine/execution/order_gateway.py` | ~900 | Ghost position guard (_filled_size>0), volume fallback from event_data |
| `base_engine/weather/probability_engine.py` | ~200 | Degenerate distribution returns empty dict |

---

## OUTSTANDING ITEMS (PRIORITIZED)

### P2 — Active Monitoring
- **Fill rate post-deploy**: Target >25% fill rate (was 8%). Verify with:
  ```bash
  journalctl -u polymarket-ai --since '30 min ago' | grep 'Order latency.*Weather' | grep -c 'success=True'
  journalctl -u polymarket-ai --since '30 min ago' | grep -c 'Paper trade FAILED.*Weather'
  ```
- **Munich city P&L**: Monitor 2+ weeks. Revisit if still negative after 30+ exits.
- **Position sizes**: Should be $5+ (no dust). Verify with DB query.
- **Entries per hour**: Expect ~5-15, not ~40-60.

### P3 — Known Issues
- **NO vs YES asymmetry**: 72% WR on NO vs 39% on YES. Monitor, no config change yet.
- **`no_prediction: 12` per scan**: 12 markets where team names can't be matched to Glicko data (CS2/Valorant parsing failures — EsportsBot issue, not WeatherBot).

### P4 — Nice-to-have
- **Fill quality monitoring**: Verify partial fill distribution post-deploy.
- **Unit test for probability_engine degenerate fallback**: Currently untested.
- **Volume gate accuracy**: Verify the event_data volume_24h passthrough is producing better fill probability estimates vs the $50K fallback.

### P5 — Deferred
- **Kalshi cross-platform arbitrage**: 8-16h effort, deferred.
- **Remove diagnostic logging**: session_factory warning, RTDS raw samples.

---

## ROLLBACK GUIDE

### Full rollback (revert fill pipeline fixes)
```bash
git revert 2c4fb3f   # Reverts 4 fill pipeline fixes
sudo systemctl restart polymarket-ai
```

### Full rollback (revert ghost position fix)
```bash
git revert ab3c018   # Reverts ghost position fix
sudo systemctl restart polymarket-ai
```

### Partial rollback via env vars (no code change)
| Change | Revert command | Effect |
|--------|----------------|--------|
| Taker factor 0.85 → 0.55 | `export PAPER_TAKER_SIDE_FACTOR=0.55` | Lower fill probability |
| 4hr cooldown → 15min | `export WEATHER_EXIT_COOLDOWN_SECS=900` | Faster re-entry |
| $5 min → $1 min | `export WEATHER_MIN_TRADE_USD=1.0` | Allow dust positions |
| BM floor → uncapped | `export WEATHER_BM_FLOOR=0.0` | Aggressive BM shrinkage |

Apply: edit `/opt/polymarket-ai-v2/.env`, then `sudo systemctl restart polymarket-ai`

---

## POST-DEPLOY MONITORING COMMANDS

```bash
# Verify WeatherBot scanning and trading
journalctl -u polymarket-ai -f | grep -i "weather"

# Check fill rate (target >25%)
journalctl -u polymarket-ai --since '10 min ago' | grep 'Order latency.*Weather'

# Check bestAsk pre-filter is working
journalctl -u polymarket-ai --since '10 min ago' | grep "weatherbot_bestask_skip"

# Check same-side dedup is working
journalctl -u polymarket-ai --since '10 min ago' | grep "_position_details"

# Verify position sizes are $5+ (no dust)
sudo -u postgres psql -d polymarket -c "
SELECT round(entry_cost::numeric, 2) as cost, count(*)
FROM positions WHERE bot_id='WeatherBot' AND opened_at > NOW() - INTERVAL '2 hours'
GROUP BY 1 ORDER BY 1;"

# Verify entries per hour (expect ~5-15)
sudo -u postgres psql -d polymarket -c "
SELECT date_trunc('hour', opened_at) as hr, count(*), round(avg(entry_cost)::numeric, 2) as avg_cost
FROM positions WHERE bot_id='WeatherBot' AND opened_at > NOW() - INTERVAL '6 hours'
GROUP BY 1 ORDER BY 1;"

# Verify no ghost positions
sudo -u postgres psql -d polymarket -c "SELECT count(*) FROM positions WHERE bot_id='WeatherBot' AND status='open' AND size=0;"

# Check cooldown is working (should see 4hr blocks)
journalctl -u polymarket-ai -f | grep "recently_exited\|exit_cooldown"
```

---

## CRITICAL TRAPS (DO NOT BREAK)

1. **trade_events is P&L AUTHORITY** — never read paper_trades for P&L
2. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL
3. **VPS deploys via `deploy.sh`**: atomic symlink swap. Working tree ≠ VPS ≠ git HEAD
4. **BotBankrollManager handles SIZING; risk_manager handles LIMITS**. Both must pass
5. **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer
6. **PSEUDO_LABEL_ENABLED=false** — DO NOT enable
7. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
8. **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
9. **Python 3.13 scoping**: `from X import Y` inside function body → entire function shadows Y
10. **websockets v15**: `import websockets.exceptions` must be explicit
11. **Paper trading IS production** — never skip features because "we're only paper trading"
12. **positions table**: NO `closed_at`, NO `updated_at` columns
13. **prediction_log columns**: NO `rejection_reason` — use `trade_executed` flag
14. **`traded_markets.bot_names`**: TEXT column (not array), use `LIKE '%BotName%'`
15. **Alpha decay requires `scan_start_mono` in event_data**: Only WeatherBot passes it
16. **`paper_trades` has NO `metadata` JSONB column**
17. **Resolution backfill excludes SELL trades** — SELL P&L computed at exit time
18. **trade_events immutability trigger**: Must DISABLE/ENABLE for data cleanup
19. **RESOLUTION event idempotency**: ON CONFLICT broken on partitioned tables → uses atomic INSERT...SELECT with WHERE NOT EXISTS
20. **Ghost positions fixed**: Idempotent memory returns `success: False`, order gateway guards `_filled_size > 0`

---

## EXPECTED IMPACT OF S107 CHANGES

| Metric | Before S107 | After S107 (estimated) |
|--------|------------|----------------------|
| Fill rate | 8% (2/26) | 25-35% (7-9 per 26) |
| Avg position size | $0.22 | $10-30 |
| Dust positions (<$0.10) | 67% | 0% |
| Positions/day | ~450 | 30-80 |
| Capital deployed/day | ~$100 | $300-800 |
| Slippage-eats-edge rate | 27% | ~5-10% |
| Re-entry loops | Yes (4x on Munich) | No (4hr cooldown) |

---

## NEXT SESSION PRIORITIES

1. **Deploy S107 fill pipeline fixes** (commit `2c4fb3f` — NOT YET DEPLOYED)
2. **Monitor fill rate** — target >25%. If still low, investigate remaining bottlenecks
3. **Monitor position sizes** — should be $5-$30 range, no dust
4. **Munich monitoring** — check after 30+ exits
5. **NO vs YES asymmetry investigation** — 72% vs 39% WR if data accumulates
6. **Verify websocket health on VPS** — all 3 channels should be connected and receiving

---

## SESSION HISTORY REFERENCE

| Session | Date | Focus |
|---------|------|-------|
| S92 | 03-15 | P1 jump detection, P2 NBM benchmark |
| S95 | 03-16 | 4 paper trading elevations |
| S97 | 03-16 | 3 stations, P&L breakdown script |
| S100 | 03-17 | Alpha decay, canary persistence, SSH timeouts |
| S101 | 03-17 | Full session — graduated expiry boost, city digest |
| S102 | 03-17 | METAR Redis daily max, exposure lock |
| S104 | 03-18 | Fill quality logging, exposure leak fix, daily counter, alpha decay BUY-only |
| S106 | 03-18 | Taker-side flat factor, probability engine fallback, stale positions |
| S107 | 03-19 | Munich investigation, bet sizing (cooldown/min trade/BM floor/drawdown), ghost position fix |
| S108 | 03-19 | Fill pipeline: taker factor 0.85, bestAsk pre-filter, volume passthrough, same-side dedup |
