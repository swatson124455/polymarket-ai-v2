# AGENT HANDOFF — WeatherBot Session 131 (2026-03-25)

## STATUS: Code-complete, NOT yet deployed. All 1718 tests passing.

**Bot**: WeatherBot (1 of 14 bots in BOT_REGISTRY)
**Scope**: WeatherBot-only session. No bleed to other bots unless explicitly demanded.
**Deploy tag**: Pending — run `deploy.sh` on VPS after commit
**All-time P&L**: -$9,733 reported, -$7,036 clean (after removing -$2,697 phantom)
**Session date**: 2026-03-25

---

## WHAT WAS DONE THIS SESSION (S131)

### Root Cause Investigation
WeatherBot lost **-$13,076 on Mar 25**. Root causes verified 3× blind:

| Bug | Impact | Root Cause |
|-----|--------|------------|
| **Tokyo VWAP blowup** | -$10,207 single trade | Shares sized at signal price $0.07 (11,381 shares = $797). Book walk filled at VWAP $0.897 → actual cost $10,207. No post-fill cost check. |
| **Kelly graduation while losing** | 40% bet size increase during drawdown | Graduated 0.25→0.35 based on MSE<9 + n≥100. Never checked P&L. |
| **Phantom RESOLUTION events** | -$2,697 fake losses | 15 RESOLUTION events used inflated paper_trades sizes (UPSERT overwrites). Madrid: res_size 5,315 vs entry 19 (278×). |
| **Re-entry guard race** | 9 entries per market | DB guard queries `positions` table, but paper engine writes to DB AFTER lock release. TOCTTOU window allows re-entries. |
| **Cross-type loss correlation** | No circuit breaker | Consecutive loss tracker is per-market-type only. Correlated losses across temperature+precipitation don't trigger drawdown compression. |

**Key finding**: Lead time is **NOT a bug** — <2h entries are +$1,608 all-time. The edge from METAR data near resolution is real. The problem is bet sizing on thin books.

### 6 Fixes Implemented (ALL CODE-COMPLETE)

#### PHASE 1: Post-VWAP Cost Cap (P0) ✅
**File**: `base_engine/execution/paper_trading.py` (lines ~558-579)
- After book walk + latency drift + partial fill finalize price, checks if `size * price > _max_bet_usd * 1.5`
- If exceeded, caps shares: `size = (max_bet * 1.5) / price`
- Opt-in: only fires when `_max_bet_usd` is in event_data. Currently only WeatherBot passes it.
- Log event: `paper_post_vwap_cap`

**File**: `bots/weather_bot.py` (line ~2822 in event_data dict)
- Added `"_max_bet_usd": self.bankroll.max_bet_usd if self.bankroll else 600.0`

#### PHASE 2: Kelly Graduation P&L Gate (P1) ✅
**File**: `bots/weather_bot.py` (lines ~4543-4610)
- Queries trailing 7d realized P&L from `trade_events_2026_03`
- **Graduate gate**: 7d P&L must be ≥ $0 to upgrade Kelly tier
- **Demotion gate**: 7d P&L < -$500 forces demote to default (0.25)
- Log events: `weatherbot_kelly_demotion`, `weatherbot_kelly_graduation_blocked_by_pnl`

#### PHASE 3: Re-Entry Guard Race Fix (P2) ✅
**File**: `bots/weather_bot.py` (lines ~2074-2082)
- Added paper engine in-memory positions dict as PRIMARY guard
- Key format: `("WeatherBot", str(market_id))` — matches paper engine exactly
- Uses `isinstance()` checks to safely handle mocked engines in tests
- Existing DB guard + `_open_position_markets` kept as secondary/tertiary guards

#### PHASE 4: Overall Consecutive Loss Counter (P3) ✅
**File**: `bots/weather_bot.py`
- Added `self._consecutive_losses_overall: int = 0` (line ~396)
- `_compute_weather_drawdown_factor()` returns `min(per_type_factor, overall_factor)`
- `_record_weather_outcome()` increments/resets overall counter alongside per-type
- Log events: `weatherbot_drawdown_reset_overall`, `weatherbot_losing_streak` (now includes `overall_losses`)

#### PHASE 5: Phantom Data Cleanup Script (P1) ✅
**File**: `scripts/cleanup_phantom_resolutions.py` (NEW)
- Finds RESOLUTION events where `res_size / entry_size > 5.0`
- Disables immutability trigger, deletes inflated events, re-enables trigger
- Has `--dry-run` mode
- Run on VPS: `python scripts/cleanup_phantom_resolutions.py --dry-run` then without flag

#### PHASE 6: Ensemble Count Warning (P4) ✅
**File**: `bots/weather_bot.py` (line ~1918)
- Logs warning when `len(forecast.ensemble_members) < 100` (expected ~133: 31 GEFS + ~102 ECMWF)
- Log event: `weatherbot_low_ensemble_count`

### Test Results
- **1718 passed, 0 failed, 8 skipped** (full suite, 5 min)
- One test (`test_scan_with_weather_market_and_edge`) needed `isinstance()` guard on paper engine positions access (MagicMock doesn't support `> float` comparison)

---

## PRIOR SESSION CONTEXT (S126, S125, S124, S123)

### S126 (2026-03-24): Spread Inflation Activated
- Two-component spread inflation: `BASE=0.15`, `FACTOR=0.05`
- 10%/day auto-decay, hard zero at day 23 (Apr 16)
- Lead-time WR analysis: <24h=-$204, 24-48h=-$601, 48-72h=+$558, 72-120h=+$1,453
- Shadow analysis: 96% rejection rate, 1,784 would-trade shadows/day
- Deploy `20260324_200349`, P&L +$2,968

### S125 (2026-03-24): Resolution Starvation Fix
- SHADOW_ENTRY DB constraint violation fixed (ALTER TABLE)
- Resolution queue expired-first ordering (was priority_bot)
- 771 manual RESOLUTION backfill
- Deploy `20260324_101757`

### S124 (2026-03-24): Negative-EV Gate
- Blocks trades where `confidence < price` OR `_raw_size <= 0`
- Population→sample std fix in probability engine
- Spread inflation foundation (OFF by default, activated in S126)

### S123 (2026-03-23): Platt+Isotonic Calibration
- Auto-refitting calibrator: T=2.042 (was T=2.271)
- Brier improvement +1.93%
- 200-sample minimum for fit

---

## SYSTEM ARCHITECTURE

### WeatherBot Position in System
```
main.py → BotScheduler → WeatherBot.scan_and_trade()
                        → 13 other bots (MirrorBot, EsportsBot, etc.)

WeatherBot uses:
  ├── base_engine.order_gateway  (PaperTradingEngine — fills at VWAP)
  ├── base_engine.db             (Database — trade_events, positions, paper_trades)
  ├── BotBankrollManager         (sizing: Kelly fraction, max_bet, daily cap)
  ├── WeatherForecastClient      (Open-Meteo API: GEFS+ECMWF ensembles)
  ├── WeatherProbabilityEngine   (forecast → bucket probability)
  ├── WeatherMarketMapper        (question text → city/date/bucket)
  ├── MetarClient / MetarMonitor (live airport weather observations)
  ├── ModelRunMonitor             (GFS/ECMWF/HRRR model run tracking)
  └── Redis                      (exit cooldowns, backoff state)
```

### 11-Layer Sizing Pipeline
```
1. Raw model probability (ensemble forecast → CDF)
2. EMOS station-level bias correction (a=0.79, b=0.98 global)
3. Platt+Isotonic confidence calibration (T=2.042, auto-refitting)
4. Spread inflation gate (BASE=0.15 + FACTOR=0.05×lead_factor, decaying to 0 by Apr 16)
5. Kelly full fraction calculation: (p*b - q) / b
6. Kelly dampening: _kelly_mult (0.25 default, upgradable to 0.35/0.50 with P&L gate)
7. Combined boost: expiry × regime × severe × jump × NBM × freshness (cap: 2.0)
8. Baker-McHale uncertainty scaling: 1/(1+σ²) with floor 0.50
9. Station reliability factor (MSE-based)
10. Bühlmann calibration ramp: n/(n+κ) where κ=30
11. Post-VWAP cost cap in paper engine (1.5× max_bet tolerance)
```

### Key Config Values (Live VPS)
```python
# WeatherBot via BotBankrollManager
capital = 20000          # $20K
kelly_fraction = 0.25    # quarter-Kelly (can graduate to 0.35/0.50 with P&L gate)
max_bet_usd = 600        # per trade cap
max_daily_usd = 20000    # daily cap

# Environment (settings)
WEATHER_MIN_EDGE = 0.08                    # 8% minimum edge
WEATHER_INTL_MIN_EDGE = 0.12              # 12% for international cities
WEATHER_MAX_BUCKETS_PER_GROUP = 3          # max positions per city+date
WEATHER_NO_MAX_ENTRY_PRICE = 0.65          # NO side price cap
WEATHER_HOLD_HOURS_BEFORE_RESOLUTION = 48  # hold window for expiry boost
WEATHER_COMBINED_BOOST_CAP = 2.0           # max combined boost
WEATHER_BM_FLOOR = 0.50                    # Baker-McHale minimum
WEATHER_MIN_TRADE_USD = 5.0                # minimum trade size
WEATHER_KELLY_FRACTION = 0.25              # default (overrideable)
WEATHER_SPREAD_INFLATION_BASE = 0.15       # S126
WEATHER_SPREAD_INFLATION_FACTOR = 0.05     # S126
WEATHER_SPREAD_INFLATION_LAUNCH_DATE = 2026-03-24  # S126
WEATHER_ALPHA_DECAY_HALF_LIFE_S = 1800     # edge decay
```

### Key Files and Their Roles
| File | Role | WeatherBot-specific? |
|------|------|---------------------|
| `bots/weather_bot.py` | Main bot logic (~4800 lines) | YES |
| `base_engine/execution/paper_trading.py` | VWAP fills, positions dict | Shared (S131 cap opt-in) |
| `base_engine/risk/bankroll_manager.py` | Kelly sizing, max_bet, daily cap | Shared |
| `base_engine/data/database.py` | trade_events, paper_trades, positions CRUD | Shared |
| `base_engine/weather/forecast_client.py` | Open-Meteo GEFS+ECMWF API | YES |
| `base_engine/weather/probability_engine.py` | Forecast → probability via CDF | YES |
| `base_engine/weather/market_mapper.py` | Question text parsing → city/date/bucket | YES |
| `base_engine/weather/station_registry.py` | 106 stations with ICAO codes | YES |
| `base_engine/weather/metar_client.py` | METAR observation fetcher | YES |
| `base_engine/weather/metar_monitor.py` | Live METAR + daily max tracking | YES |
| `base_engine/weather/model_run_monitor.py` | GFS/ECMWF/HRRR model run detection | YES |
| `base_engine/weather/precipitation_engine.py` | Precip probability math | YES |
| `config/settings.py` | All env var config | Shared |
| `scripts/bot_pnl.py` | Canonical P&L script | Shared |
| `scripts/cleanup_phantom_resolutions.py` | S131 one-time phantom cleanup | YES (NEW) |

---

## CRITICAL TRAPS (DO NOT BREAK)

### Data & P&L
1. **trade_events is P&L AUTHORITY** — never read paper_trades for P&L. SELL/EXIT trades only exist in trade_events.
2. **paper_trades UPSERT**: `ON CONFLICT (bot_name, market_id, side) DO UPDATE SET size = EXCLUDED.size` — REPLACES size, doesn't accumulate. This is why RESOLUTION events using paper_trades.size are inflated.
3. **trade_events immutability trigger**: `trg_trade_events_immutable` prevents DELETE/UPDATE. Must `DISABLE TRIGGER` then re-enable for data cleanup.
4. **RESOLUTION event idempotency**: `ON CONFLICT (idempotency_key, event_time)` is BROKEN on partitioned tables. `insert_trade_event()` uses atomic INSERT...SELECT with WHERE NOT EXISTS.
5. **trade_events JSONB column is `event_data`** — NOT `metadata_json`.
6. **Resolution backfill excludes SELL trades** — SELL P&L computed by paper engine at exit time.

### Bot Logic
7. **YES/NO mandate**: `place_order()` requires `side="YES"/"NO"`. NEVER BUY/SELL.
8. **BotBankrollManager handles SIZING; risk_manager handles LIMITS.** Both must pass.
9. **`risk_manager.calculate_position_size()` DEPRECATED** — BotBankrollManager is the real sizer.
10. **Paper engine positions key format**: `(bot_name, market_id)` — tuple of 2 strings.
11. **Position `current_price` auto-updated every 10s** by `position_manager._update_current_prices()`.
12. **Paper trading is PRODUCTION** — NOT a sandbox. Every feature matters identically in paper/live mode.

### Python & Async
13. **Python 3.13 scoping**: `from X import Y` inside function makes `Y` local for ENTIRE function. Use before import → `UnboundLocalError`.
14. **asyncpg JSONB**: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
15. **asyncpg DATE columns**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime string.
16. **`websockets.exceptions` must be imported explicitly** (v15 lazy-loads).

### WeatherBot-Specific
17. **Spread inflation has TWO components**: BASE and FACTOR. Both required. Without FACTOR, inflation is constant (no lead-time scaling).
18. **S126 auto-decay**: Spread inflation decays 10%/day from launch date. Hard zero at day 23. `days_since = (today - launch_date).days`, `decay = max(0, 1 - days_since/23)`.
19. **EMOS 20-sample minimum** is per (station, lead_bucket) — not a hard gate for trading.
20. **City onboarding is manual**: Edit `station_registry.py`, add `WeatherStation` entry, deploy. New cities CAN trade day 1 (global T applies, EMOS not required).
21. **35-city freeze**: City count stuck at 35 for days. `unmatched_cities=[]` — could be regex or upstream filtering.
22. **`_kelly_mult` graduation requires 7d P&L ≥ $0** (S131). Demotion at 7d P&L < -$500.
23. **Alpha decay requires `scan_start_mono` in event_data**: Only WeatherBot passes it.
24. **`_recently_exited` uses Redis TTL** for persistence across restarts.
25. **`_group_exposure` / `_city_exposure`** restored from DB on startup via `_restore_exposure_from_db()`.
26. **S124 negative-EV gate**: `confidence < price` OR `_raw_size <= 0` → logged as SHADOW_ENTRY, not traded.
27. **Confidence calibrator auto-refits**: NOT static. Reloads every 6h from trade_events.
28. **GEFS = 31 members, ECMWF = ~102 members** → expect ~133 total. Open-Meteo sometimes returns fewer.
29. **Post-VWAP cap only fires for BUY side** — SELL orders don't inflate via book walk in same way.
30. **`_consecutive_losses_overall` is NOT persisted to DB/Redis** — resets on restart. Per-type streaks also reset on restart (restored from prediction_log in `_backfill_weather_outcomes()`).

### Cross-Bot
31. **BOT_REGISTRY = 14 bots** — shared module change requires all 14 verified.
32. **`paper_trades` has NO `metadata` JSONB column** — never assume metadata is available.
33. **`positions` table columns**: NO `closed_at`, NO `updated_at`. Only `opened_at` + `status`.
34. **`traded_markets.bot_names`**: TEXT column (not array), use `LIKE '%BotName%'` not `= ANY()`.

---

## OPEN ITEMS / NEXT SESSION PRIORITIES

### P0: Deploy S131 Fixes
1. Commit all changes (2 files modified, 1 new script)
2. Deploy to VPS via `deploy.sh`
3. 30-min log verification:
```bash
journalctl -u polymarket-ai -f | grep paper_post_vwap_cap      # Phase 1
journalctl -u polymarket-ai -f | grep kelly_demotion            # Phase 2
journalctl -u polymarket-ai -f | grep kelly_graduation_blocked  # Phase 2
journalctl -u polymarket-ai -f | grep low_ensemble_count        # Phase 6
```
4. Verify no duplicate entries:
```sql
SELECT market_id, COUNT(*) FROM positions
WHERE bot_id='WeatherBot' AND status='open'
GROUP BY market_id HAVING COUNT(*)>1;
```
5. Run phantom cleanup:
```bash
python scripts/cleanup_phantom_resolutions.py --dry-run
python scripts/cleanup_phantom_resolutions.py
```
6. Verify corrected P&L: `python scripts/bot_pnl.py WeatherBot 720`

### P1: Bet Size Review (PINNED from plan)
The $600 max_bet was set when Kelly was 0.25. With graduation, effective sizing inflates. Post-VWAP cap addresses acute blowups, but longer term:
- Should max_bet scale DOWN with Kelly graduation?
- Or max_bet stays fixed and Kelly graduation only affects mid-range sizing?
- Current exposure: 155 open positions, $33K cost basis on $20K capital

### P2: `_consecutive_losses_overall` Persistence
Currently resets on restart. Should be persisted to Redis or restored from prediction_log (walk backwards across ALL market types, find cross-type streak). Low priority since per-type streaks also reset.

### P3: City Count Investigation
35 cities stable for days. Investigate:
- `is_weather_market()` regex filtering
- `group_markets()` upstream drops
- New Polymarket city formats not matching

### P3: NO vs YES Asymmetry
72% YES WR vs 39% NO WR. Confirmed pattern, monitoring before config change.

### P4: GEFS Subsampling Review
S131 removed GEFS subsampling at user request. Open-Meteo returns 50-80 GEFS members. Long term, evaluate whether full ensemble or subsampled provides better spread estimates. Pin for review in handoff.

---

## KEY LEARNINGS & DECISIONS

1. **Lead time is NOT a bug** — User explicitly confirmed. <2h entries are +$1,608 all-time. METAR edge near resolution is real. Problem was bet sizing, not timing.
2. **Position accumulation is mostly legacy** — 863 multi-entry markets were pre-S118, only 2 post-S118. The 91.7× ratio was from legitimate re-entries at different prices on different days.
3. **Phantom data = -$2,697** — 15 inflated RESOLUTION events. Clean P&L: -$7,036 (not -$9,733).
4. **Kelly graduation was blind to P&L** — Graduated during losing stretch. Now gated on 7d P&L ≥ $0.
5. **Paper engine positions dict is always in sync** — Updated inside the lock. DB guard has async lag. Use in-memory as primary.

---

## WEATHERBOT METHOD INDEX (weather_bot.py, ~4800 lines)

### Core Flow
| Method | Line | Purpose |
|--------|------|---------|
| `__init__` | 261 | Initialize all state, clients, monitors |
| `scan_and_trade` | 1008 | Main scan loop: discover → analyze → execute |
| `_analyze_group` | 1850 | Compute edges for temperature group |
| `_analyze_precipitation_group` | 1541 | Compute edges for precip group |
| `_analyze_snowfall_group` | 1660 | Compute edges for snow group |
| `_analyze_wind_group` | 1741 | Compute edges for wind group |
| `_execute_group_trades` | 2368 | S-T allocate + execute list |
| `_execute_weather_trade` | 2430 | Single trade: guards + sizing + order |
| `analyze_opportunity` | 1358 | Single market analysis → opportunity dict |

### Risk & Sizing
| Method | Line | Purpose |
|--------|------|---------|
| `_compute_weather_drawdown_factor` | 510 | min(per_type, overall) consecutive loss factor |
| `_record_weather_outcome` | 530 | Update loss streak counters |
| `_smoczynski_tomkins_allocate` | 2298 | Group-level Kelly allocation |
| `_calibration_confidence` | 643 | Bühlmann ramp: n/(n+κ) |
| `_get_station_reliability_factor` | 743 | MSE-based station weight |
| `_get_min_edge` | 609 | Category/intl-adjusted min edge |

### Calibration
| Method | Line | Purpose |
|--------|------|---------|
| `fit_from_trade_events` | 109 | Fit Platt+Isotonic calibrator |
| `calibrate` | 229 | Apply calibration transform |
| `_maybe_reload_calibration` | 4240 | 6h reload from weather_calibration table |
| `_fit_emos` | 3785 | Static EMOS regression fit |
| `_check_emos_drift` | 788 | DDM/EDDM drift detection |

### Monitoring & Controls
| Method | Line | Purpose |
|--------|------|---------|
| `_check_monitoring_thresholds` | 4499 | Brier check → Kelly graduation/halt |
| `_close_stale_positions` | 840 | Exit positions past resolution date |
| `_reevaluate_open_positions` | 2916 | Exit signals for open positions |
| `_handle_daily_boundary` | 3440 | UTC midnight P&L reset |

### Data & Discovery
| Method | Line | Purpose |
|--------|------|---------|
| `_fetch_weather_events_by_tag` | 3066 | Gamma API paginated fetch |
| `_fetch_weather_markets_direct` | 3001 | Direct DB+Gamma fallback |
| `_enrich_with_live_prices` | 3203 | Add current CLOB prices |
| `_check_weather_market_availability` | 3411 | Startup market probe |

### State Persistence
| Method | Line | Purpose |
|--------|------|---------|
| `_restore_exposure_from_db` | 3322 | group/city exposure on restart |
| `_restore_daily_pnl_from_db` | 3461 | Realized P&L from trade_events |
| `_restore_exits_from_redis` | 3296 | Exit cooldowns from Redis TTL |
| `_restore_backoff_from_redis` | 3271 | Scan backoff state |
| `_save_exit_to_redis` | 3284 | Persist exit cooldown |
| `_save_backoff_to_redis` | 3261 | Persist backoff state |
| `_backfill_weather_outcomes` | 472 | Restore loss streaks from prediction_log |

### Weather Services
| Method | Line | Purpose |
|--------|------|---------|
| `_get_model_age_hours` | 554 | Hours since latest model run |
| `_compute_regime_boost` | 2969 | ENSO regime → Kelly boost |
| `_get_severe_weather_boost` | 4038 | NWS alerts → confidence boost |
| `_should_halt_severe_weather` | 4048 | Halt-category alert check |
| `_get_afd_spread_factor` | 4072 | AFD text → spread multiplier |
| `_get_enso_regime` | 3869 | NOAA ENSO monthly regime |
| `_apply_metar_resolution_day_override` | 2194 | METAR-based resolution override |
| `_maybe_bootstrap_cold_station` | 661 | Cold-start: historical bias |
| `_compute_crps` | 3698 | CRPS scoring (static) |

---

## PAPER TRADING ENGINE KEY DETAILS

### Positions Dict
- **Key**: `(bot_name: str, market_id: str)` — tuple
- **Value**: `{"size": float, "avg_price": float, "token_id": str, "side": str, "entry_fee": float}`
- **Updated INSIDE the lock** — always in sync (no TOCTTOU)
- **DB write happens AFTER lock release** — can lag behind in-memory

### _place_order_locked() Flow (paper_trading.py)
```
Line 382: Method start
Line 420: Idempotency check (memory)
Line 428: Idempotency check (DB)
Line 450: Order state setup, extract event_data
Line 475: Book walk (depleted book re-walk)
Line 512: Book walk (fresh VWAP from order_gateway)
Line 526: Latency drift penalty
Line 545: Partial fill handling
Line 558: S131 Post-VWAP cost cap ← NEW
Line 581: Fee calculation
Line 598: BUY execution (cash check, position update)
Line 650+: SELL execution (realized P&L, position close)
```

---

## DATABASE SCHEMA NOTES

### trade_events (P&L authority)
- Partitioned by `event_time` (monthly: `trade_events_2026_03`)
- Event types: ENTRY, EXIT, RESOLUTION, SHADOW_ENTRY
- `ON CONFLICT` broken on partitioned tables → atomic INSERT...SELECT for RESOLUTION
- Immutability trigger: must DISABLE/ENABLE for cleanup
- JSONB column: `event_data` (NOT metadata_json)

### paper_trades (legacy, NOT for P&L)
- UPSERT replaces size on `(bot_name, market_id, side)` conflict
- NO `metadata` column, NO `resolved_pnl` column (it's `resolved_at`)
- Still used by paper engine for trade logging, NOT for P&L

### positions
- NO `closed_at`, NO `updated_at` columns
- Only `opened_at` + `status` (open/closed)
- For closed position timing, query trade_events EXIT events

---

## VPS & DEPLOYMENT

- **VPS**: Ubuntu-3 at 34.251.224.21 (16GB/4vCPU)
- **SSH key**: `~/.ssh/LightsailDefaultKey-eu-west-1.pem`
- **Deploy**: `deploy.sh` — atomic symlink swap
- **Service**: `systemctl restart polymarket-ai`
- **Logs**: `journalctl -u polymarket-ai -f | grep WeatherBot`
- **P&L script**: `python scripts/bot_pnl.py WeatherBot 720`

---

## SESSION HISTORY (WeatherBot)

| Session | Date | Key Changes |
|---------|------|-------------|
| **S131** | 03-25 | 6 bug fixes: post-VWAP cap, Kelly P&L gate, re-entry guard, overall loss counter, phantom cleanup script, ensemble warning. Code-complete, not deployed. |
| **S126** | 03-25 | Spread inflation activated (BASE=0.15, FACTOR=0.05, 10%/day decay). Shadow analysis. |
| **S125** | 03-24 | Resolution starvation fix, SHADOW_ENTRY DB constraint, 771 manual backfill |
| **S124** | 03-24 | Negative-EV gate, population→sample std fix, spread inflation foundation |
| **S123** | 03-23 | Platt+Isotonic calibration (T=2.271→2.042), Brier +1.93% |
| **S122** | 03-23 | Cap uncapping, shadow entries, confidence penalty removal |
| **S119** | 03-22 | 6 root causes: NO price trap, correlated blowups, position stacking |
| **S118** | 03-22 | Open-Meteo 429 rate limiting self-healed. S116 reverted. |
| **S108** | 03-19 | Fill pipeline: taker 0.85, bestAsk pre-filter, volume passthrough |
| **S104** | 03-18 | Fill quality logging, exposure leak fix, daily counter |
| **S100** | 03-17 | Alpha decay, canary persistence, SSH timeouts, P&L +$2,881 |

---

## INSTRUCTIONS FOR NEXT AGENT

1. **Scope**: WeatherBot only. Do not touch other bot files unless explicitly asked.
2. **Read CLAUDE.md first** — it has the Prime Directive and surgical fix rules.
3. **Read this handoff** — it IS the context. Don't re-derive what's already here.
4. **Priority**: Deploy S131 fixes → verify logs → run phantom cleanup → verify P&L.
5. **Bet size review pinned**: max_bet vs Kelly graduation scaling question needs analysis.
6. **All state attributes listed above** — use as reference, don't re-grep the whole file.
7. **P&L math**: `cost = entry_price * size` (ALL sides), `uPnL = (current - entry) * size` (ALL sides). NEVER invert for NO.
8. **Canonical P&L**: `python scripts/bot_pnl.py WeatherBot <hours>`
9. **Test before deploy**: `pytest tests/ -x --timeout=30` — must be 1718+ passed, 0 failed.
