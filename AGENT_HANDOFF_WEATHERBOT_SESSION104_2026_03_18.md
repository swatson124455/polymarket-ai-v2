# AGENT HANDOFF — WeatherBot Session 104
**Date**: 2026-03-18
**Scope**: WeatherBot only — no MirrorBot, no EsportsBot, no shared infra changes beyond paper_trading.py
**Commits**: `7758bec` (S104), `a955a15` (S104b)
**Deploys**: `20260318_144347` (S104), `20260318_145657` (S104b)
**Previous WeatherBot handoff**: `AGENT_HANDOFF_WEATHERBOT_SESSION100_2026_03_17.md`

---

## 1. SESSION IDENTITY

- **Session**: S104 (WeatherBot only)
- **Date**: 2026-03-18
- **Commits**: `7758bec` (S104), `a955a15` (S104b) — both deployed to VPS
- **Deploy timestamps**: 20260318_144347 (S104), 20260318_145657 (S104b)

---

## 2. WHAT WAS DONE

### S103 fix (deployed earlier in session, separate commit)

- Position creation was broken since Mar 17 when S97's `WEATHER_SKIP_COORDINATOR_BUY=True` was deployed
- `confirm_position()` expected a `reserving` row but reserve was skipped, so positions were NEVER created in DB, resulting in 0 exits for 2 days
- Fix: `confirm_position()` now INSERTs directly when no reserving row exists
- Data proof: Mar 16 = 423 positions/day, Mar 17 = 2, Mar 18 = 0 (before fix)

### S104 — Fill quality logging + exposure leak fix + daily counter

**File: `base_engine/execution/paper_trading.py`**

- Initialized 6 default fill metric variables at top of `_place_order_locked()` (line ~540): `_fill_prob=1.0, _fill_frac=1.0, _decay_slip_bps=0, _lambda_slip_bps=0, _cum_add=0, _res_slip_mult=1.0`
- Mutate event_data dict in-place before BUY DB write (line ~945) with: slippage_bps, fill_prob, fill_frac, book_walk, alpha_decay_bps, kyle_lambda_bps, cross_scan_bps, res_prox_mult
- Expanded result dict for BUY orders with: fill_probability, fill_fraction, book_walk_used, alpha_decay_bps, kyle_lambda_bps
- All existing callers unaffected (new keys only, no existing keys changed)

**File: `bots/weather_bot.py`**

- Added import: `from base_engine.data.daily_counter import increment_counter as _inc_daily, restore_counters as _restore_daily`
- Added `_market_group_cache: Dict[str, Tuple[str, str, float]]` — maps market_id to (group_key, city, cost_usd)
- Populated on entry in `_execute_weather_trade()` after successful trade
- Used in PM exit detection block (line ~670) to decrement `_group_exposure`/`_city_exposure` — THIS WAS THE BIGGEST BUG: exposure only went UP and reset at midnight, never decremented on exit
- Daily counter write-through on: entry success, entry failure revert, exit
- Replaced `_restore_exposure_from_db()` body: old heavy paper_trades JOIN (~200ms) replaced with daily_counter pattern (<10ms)
- Added `_rebuild_market_group_cache()`: queries open positions + markets.question on startup, parses city/date, populates cache (96 positions on latest deploy)

### S104b — Blind review fixes

**File: `base_engine/execution/paper_trading.py`**

- Alpha decay gated to BUY-only (was applying signal staleness penalty to SELL exits, degrading exit prices by 5-15 bps)
- Changed line ~599: `if _realistic and latency_ms...` to `if _realistic and side == "BUY" and latency_ms...`

**File: `bots/weather_bot.py`**

- `_market_group_cache.clear()` added to `_handle_daily_boundary()` after exposure dicts are cleared
- `self._market_group_cache.pop(mid, None)` added to `_close_stale_positions()` loop after positions are closed in DB

---

## 3. REAL DATA COLLECTED

### Position hold durations (260 exits, Mar 13-17)

```
0-2h:   26 exits, 42% WR,  +$17   <- early exits lose
2-6h:    9 exits, 44% WR,   -$1
12-24h: 45 exits, 69% WR,   +$4
24-48h: 122 exits, 73% WR, +$432  <- sweet spot
48h+:   51 exits, 73% WR,  +$42
```

### Exit triggers (260 exits)

```
take_profit (>+20%)  | 188 exits | +$624
stop_loss (<-20%)    |  41 exits |   -$8
mid_range (5-20%)    |  25 exits |  -$10
model_reversal (~0%) |   6 exits |   -$1
```

### Resolution P&L by hold duration

```
12-24h: 18 resolutions, +$21, 50% WR
24-48h: 97 resolutions, +$1,054, 59% WR <- best
48-72h: 227 resolutions, +$500, 62% WR
72h+:   333 resolutions, +$707, 60% WR
```

### Side breakdown (all resolutions)

```
NO:  455 resolutions, 81.3% WR, +$1,696
YES: 220 resolutions, 15.5% WR, +$586
```

### All-time P&L (as of S104)

```
ENTRY: 2698 events
EXIT: 260 events, +$606 realized
RESOLUTION: 675 events, +$2,282 realized
Total realized: +$2,888
```

### Fill quality (first data from S104 deploy)

```
Tokyo: 10.2 bps slippage, 57.7% fill frac, book walk used, 2.9 bps alpha decay
Wellington: 6.1 bps slippage, 100% fill frac, book walk used
Singapore: 6.7 bps slippage, 32.1% fill frac, book walk used
```

---

## 4. 3RD PARTY SHADOW TRADING GUIDE — DECISIONS MADE

### ALREADY HAVE (no action needed)

- VWAP book walk (`_vwap_from_book()` at paper_trading.py:205-277)
- Alpha decay (exponential via `_alpha_decay_factor()`)
- Size-dependent slippage (4 tiers: 35-120 bps + boundary multiplier)
- Square-root market impact (`_sqrt_market_impact_bps()`)
- Kyle's lambda adverse selection (cached 1h TTL)
- Cross-scan cumulative impact (2nd+ BUY within 60s penalized)
- Resolution proximity penalties (3x slippage at <30min)
- Partial fill probability model (quadratic, peaks at 0.50)

### DEFERRED (need data first)

- **Taker-side filter** — the guide's number one recommendation. Reduces fill rate ~45% by checking taker direction. DEFERRED because we had ZERO fill quality data before S104. Now collecting. Evaluate after 3-5 days with this query:

```sql
SELECT avg((event_data->>'fill_prob')::numeric) as avg_fill_prob,
       avg((event_data->>'slippage_bps')::numeric) as avg_slip,
       count(*) FILTER (WHERE (event_data->>'book_walk')::boolean) as book_walk_ct
FROM trade_events WHERE bot_name='WeatherBot' AND event_type='ENTRY'
  AND event_time > NOW() - INTERVAL '3 days';
```

### SKIPPED (not applicable)

- Through-fill / queue models (we are taker orders, not limit/maker)
- Parallel fill models (overengineered for our scale)
- Complementary token depth / MINT mechanism (marginal impact)
- On-chain OrderFilled verification (overkill for paper trading)

### DATA SAYS NO

- **Autonomous exit logic** — position_manager TP/SL already works (+$606 exit P&L when positions exist)
- **Force exit timer** — Data CONTRADICTS. 24-48h positions are the BEST bucket. Weather signals converge over time, not decay. A 22h force-exit would destroy the most profitable trades.

---

## 5. BLIND REVIEW FINDINGS

### FIXED in S104b

1. **Alpha decay on SELL** — exit prices degraded 5-15 bps. FIXED: BUY-only gate added.
2. **_market_group_cache not cleared on day boundary** — FIXED: `.clear()` added in `_handle_daily_boundary()`.
3. **_close_stale_positions doesn't pop cache entries** — FIXED: `.pop()` added in loop.

### NOT A BUG (reviewer was wrong)

- `_rebuild_market_group_cache` JOIN key — the code uses `m.condition_id` which IS correct. Reviewer claimed `m.id`.

### DEFERRED (pre-existing, not from S104)

- `_scan_impact` dict pruning at 100 entries — fine for practical volumes
- `_pending_correlation_ids` survives crash — re-initialized empty on restart
- Partial fill fraction logic — defensible design, not a contradiction
- SELL EXIT event no retry — low priority, EXIT events non-critical for immediate operation
- `_close_stale_positions` queries paper_trades instead of trade_events — pre-existing bug, separate fix
- Day boundary race with in-flight trades — theoretical, 2-min scan interval makes near-impossible
- Exposure clamped to 0 hides divergence — `max(0.0,...)` is correct guard, should add logging

---

## 6. OUTSTANDING ITEMS / NEXT STEPS

### P1 — Taker-side filter evaluation (3-5 days post S104)

- Fill quality data now collecting in event_data
- After 3-5 days, run the evaluation query in Section 4
- If avg fill_prob < 0.5, the taker-side filter (0.55 multiplier) is warranted
- Implementation: single line change in `_fill_probability()`, configurable via `PAPER_TAKER_SIDE_FACTOR`

### P2 — Exposure counter negative values

- Daily counters show negative city values (e.g., city_Ankara = -$1704)
- Root cause: counters start at 0 on deploy day, but exits from yesterday's positions push them negative
- Not harmful (in-memory exposure uses `max(0.0,...)`) but cosmetically wrong
- Fix: on startup, if restoring from daily_counter and value < 0, clamp to 0

### P3 — _close_stale_positions uses paper_trades table

- Pre-existing bug. Query at line 524-527 checks `paper_trades.realized_pnl IS NOT NULL`
- Should use `trade_events WHERE event_type='EXIT'` instead (paper_trades doesn't have SELL records)
- Low impact: age-based fallback (20h) catches most cases anyway

### P3 — Fill quality data analytics

- Build a script (`scripts/fill_quality.py`) to analyze collected fill data
- Answer: what % of fills use book walk vs heuristic? Average slippage by city? Fill prob distribution?

### P5 — Partial fill logic review

- The blind review flagged that a 5% fill_prob order getting 50% partial fill is contradictory
- Current: `_fill_frac = min(1.0, _fill_prob + random() * (1-_fill_prob) * 0.5)`
- Consider: if an order fills at all (passed rejection), it should fill at higher fractions
- Need data to decide — the fill_frac field now in event_data will tell us

---

## 7. KEY ARCHITECTURE FACTS (WeatherBot-specific)

- **Scan interval**: 2 minutes (120s)
- **Trade concurrency**: `WEATHER_TRADE_CONCURRENCY=8` — up to 8 parallel place_order calls per scan
- **Exposure tracking**: `_group_exposure` (city:date to USD) + `_city_exposure` (city to USD), protected by `_exposure_lock` (asyncio.Lock)
- **Exit mechanism**: Position_manager handles TP/SL/model-reversal exits. WeatherBot detects exits via `_known_open_markets` diff (line ~666) and decrements exposure
- **Position creation**: `WEATHER_SKIP_COORDINATOR_BUY=True` means skip reserve_position(); confirm_position() does direct INSERT
- **Exposure restore**: daily_counter pattern (S104) — `_restore_daily(db, "WeatherBot")` returns dict of group_/city_ counters
- **Market-group cache**: `_market_group_cache` maps market_id to (group_key, city, cost_usd). Populated on entry, used for exit decrements, rebuilt on startup from open positions
- **Stale cleanup**: `_close_stale_positions()` runs every 10 scans. Closes positions where target date passed OR age >20h. Direct DB UPDATE, no SELL order.
- **Exit cooldown**: `_recently_exited` dict (market_id to monotonic time, 900s TTL). Persisted to Redis.
- **Day boundary**: `_handle_daily_boundary()` clears exposure dicts + market_group_cache at UTC midnight

---

## 8. PAPER TRADING FILL MODEL (complete reference)

BUY order path in `paper_trading.py._place_order_locked()`:

1. **Alpha decay** (BUY-only, S104b): exponential signal decay based on latency_ms. Half-life configurable (WeatherBot: 1800s)
2. **L2 Book walk** (if enabled + orderbook available): `_vwap_from_book()` — subtracts whale consumption, walks remaining asks
3. **Size-dependent slippage**: 4 tiers (35/50/75/120 bps) x boundary multiplier
4. **Square-root market impact**: `2 * 0.05 * sqrt(Q/V) * 10000` bps
5. **Kyle's lambda**: `lambda * 15` bps adverse selection
6. **Cross-scan cumulative**: +bps if 2nd+ BUY on same market within 60s (cap 200)
7. **Resolution proximity**: 1.0/1.5/2.0/3.0x multiplier at >6h/>2h/>0.5h/<0.5h
8. **Apply slippage**: jitter 0.5-1.5x, adjust price
9. **Fill probability**: 5 multiplicative factors (price-depth, size-impact, spread, TOD, participation)
10. **Rejection roll**: random() > fill_prob means reject
11. **Partial fill**: book walk deterministic OR heuristic draw from [fill_prob, 1.0]
12. **Cash deduction** + position creation

SELL orders: ALWAYS succeed (no fill probability, no slippage, no alpha decay). Returns realized_pnl.

Result dict (BUY): success, order_id, trade_id, filled, price, requested_price, slippage_bps, cash_remaining, fill_probability, fill_fraction, book_walk_used, alpha_decay_bps, kyle_lambda_bps

---

## 9. CRITICAL TRAPS (WeatherBot-specific)

- `_market_group_cache` stores 3-tuple `(group_key, city, cost_usd)` — NEVER expand without updating all consumers
- `_market_meta_cache` in MirrorBot is a DIFFERENT cache — do not confuse
- Daily counters use `CURRENT_DATE` (UTC) as key — auto-reset at midnight, no manual reset needed
- `_restore_exposure_from_db()` now uses daily_counter, NOT paper_trades JOIN. Do not revert to old query.
- Alpha decay is BUY-only (S104b fix). DO NOT remove the `side == "BUY"` gate.
- `_close_stale_positions()` does direct DB UPDATE — no trade_events EXIT record created. This is by design (not a bug).
- Exposure is reserved BEFORE `place_order()` under `_exposure_lock`, reverted on failure. The lock MUST be held for both reserve and revert.
- `event_data` dict is mutated in-place by paper_trading.py before DB write. The same dict object flows through the entire call chain. DO NOT copy it before passing to place_order.
- `WEATHER_SKIP_COORDINATOR_BUY=True` — confirm_position() does direct INSERT. Do not re-enable coordinator reserve without reverting the S103 fix.
- trade_events is P&L AUTHORITY — never read paper_trades for P&L
- Alpha decay requires `scan_start_mono` in event_data — do NOT remove from event_data dict
- `asyncpg JSONB`: use `CAST(:x AS jsonb)` NOT `:x::jsonb`
- `asyncpg DATE columns`: pass `CURRENT_DATE` as SQL literal, NOT Python strftime string

---

## 10. VERIFICATION COMMANDS

```bash
# Check positions being created
sudo -u postgres psql -d polymarket -c "SELECT status, count(*) FROM positions WHERE source_bot='WeatherBot' AND opened_at > NOW() - INTERVAL '24h' GROUP BY status;"

# Check fill quality data landing
sudo -u postgres psql -d polymarket -c "SELECT event_data FROM trade_events WHERE bot_name='WeatherBot' AND event_type='ENTRY' ORDER BY event_time DESC LIMIT 5;"

# Check daily counters
sudo -u postgres psql -d polymarket -c "SELECT * FROM daily_counters WHERE bot_id='WeatherBot' AND counter_date=CURRENT_DATE ORDER BY counter_name;"

# Check exits happening
sudo -u postgres psql -d polymarket -c "SELECT event_type, count(*) FROM trade_events WHERE bot_name='WeatherBot' AND event_time > NOW() - INTERVAL '24h' GROUP BY event_type;"

# Check exposure decrements firing
journalctl -u polymarket-ai -f | grep weatherbot_exposure_decremented

# Check bot scanning
journalctl -u polymarket-ai -f | grep weatherbot_scan_done

# P&L report
PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/bot_pnl.py WeatherBot 24

# Taker-side filter evaluation (run after 3-5 days)
sudo -u postgres psql -d polymarket -c "SELECT avg((event_data->>'fill_prob')::numeric) as avg_fill_prob, avg((event_data->>'slippage_bps')::numeric) as avg_slip, count(*) FILTER (WHERE (event_data->>'book_walk')::boolean) as book_walk_ct FROM trade_events WHERE bot_name='WeatherBot' AND event_type='ENTRY' AND event_time > NOW() - INTERVAL '3 days';"
```

---

## 11. FILES MODIFIED IN S104

| File | Changes |
|------|---------|
| `base_engine/execution/paper_trading.py` | Fill metric defaults, event_data mutation, result dict expansion, alpha decay BUY-only gate |
| `bots/weather_bot.py` | daily_counter import, _market_group_cache, PM exit exposure decrement, daily counter write-through, _restore_exposure_from_db() rewrite, _rebuild_market_group_cache(), day boundary cache clear, stale close cache pop |
