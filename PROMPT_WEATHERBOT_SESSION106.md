# WeatherBot Session 106 — Scope-Locked Task Prompt
# Copy-paste this into a fresh session. DO NOT bleed into MirrorBot or EsportsBot.

---

## SCOPE LOCK
You are working on **WeatherBot ONLY**. Do not touch MirrorBot, EsportsBot, or any other bot's files. If a shared module needs changes, justify it explicitly and verify all 14 bots.

---

## READ FIRST (in this order)
1. `CLAUDE.md` — development rules (surgical fixes, zero collateral damage)
2. `AGENT_HANDOFF_WEATHERBOT_SESSION104_2026_03_18.md` — latest handoff (S104 + S104b)
3. `AGENT_HANDOFF_WEATHERBOT_SESSION102_2026_03_17.md` — prior session (S102 + S102b)
4. `bots/weather_bot.py` — the bot (~4,000 lines)
5. `base_engine/weather/forecast_client.py` — ensemble fetching
6. `base_engine/weather/probability_engine.py` — EMOS + skew-normal
7. `base_engine/execution/paper_trading.py` — fill model (shared, read-only unless bug found)
8. `config/settings.py` — all config
9. `tests/` — relevant weather tests

---

## CURRENT STATE (as of S104b deploy 2026-03-18)
- **P&L**: +$2,888 realized (2698 entries, 260 exits +$606, 675 resolutions +$2,282)
- **Open positions**: ~96 (restored from DB on latest deploy)
- **Key config**: kelly=0.25, capital=$20K, max_bet=$300, max_daily=$10K, RISK_MAX=$100 (final cap)
- **S104 changes**: Fill quality logging, exposure leak fix (only went UP), daily counter write-through, _market_group_cache for exit decrements
- **S104b changes**: Alpha decay BUY-only gate, cache cleanup on day boundary + stale close
- **S102b changes**: GEFS numeric sort fix, lead-time timezone fix, exposure revert race fix, BotBankrollManager defaults aligned

---

## TASK LIST — IN PRIORITY ORDER

### P1: Taker-Side Filter Evaluation (3-5 days post S104)
S104 deployed fill quality logging. After 3-5 days of data collection, evaluate whether a taker-side filter is warranted.

**Run this query on VPS:**
```sql
SELECT avg((event_data->>'fill_prob')::numeric) as avg_fill_prob,
       avg((event_data->>'slippage_bps')::numeric) as avg_slip,
       avg((event_data->>'fill_frac')::numeric) as avg_fill_frac,
       count(*) FILTER (WHERE (event_data->>'book_walk')::boolean) as book_walk_ct,
       count(*) as total_entries
FROM trade_events WHERE bot_name='WeatherBot' AND event_type='ENTRY'
  AND event_time > NOW() - INTERVAL '3 days';
```

**Decision tree:**
- If avg_fill_prob < 0.50 → implement taker-side filter (single line in `_fill_probability()`, config via `PAPER_TAKER_SIDE_FACTOR`)
- If avg_fill_prob 0.50-0.70 → monitor another 3 days
- If avg_fill_prob > 0.70 → no filter needed

**Also run fill quality by city:**
```sql
SELECT
  COALESCE(event_data->>'city', 'unknown') as city,
  avg((event_data->>'slippage_bps')::numeric) as avg_slip,
  avg((event_data->>'fill_frac')::numeric) as avg_frac,
  count(*) as entries
FROM trade_events WHERE bot_name='WeatherBot' AND event_type='ENTRY'
  AND event_time > NOW() - INTERVAL '3 days'
GROUP BY city ORDER BY entries DESC;
```

**Deliverable**: Data analysis + decision. If implementing, ONE commit, ONE line change.

### P2: Config Contradiction Audit + Fix
WeatherBot has multiple overlapping caps. Audit and align:

**Known contradictions:**
- `WEATHER_TOTAL_CAPITAL` was $25,000 in settings.py (S105 fixed to $20K) — **verify VPS .env is aligned**
- `WEATHER_DAILY_LOSS_LIMIT` was $2,000 (S105 fixed to $10K) — **verify VPS .env**
- `RISK_MAX_POSITION_SIZE_USD=100` (final cap in order_gateway) vs `max_bet_usd=$300` (BotBankrollManager) — $300 is upstream, $100 is the real cap. Every trade hits $100. Is this intentional? If so, why is max_bet $300?
- `group_cap=$200` vs `city_cap=$500` vs `RISK_MAX=$100` — the $100 cap makes group and city caps irrelevant for individual trades. They only matter for aggregate exposure. Document clearly.
- Baker-McHale factor typically = 0.5 at 3F spread, meaning Kelly output is halved BEFORE hitting $100 cap. Most trades are therefore $50-ish. Is this the intended behavior?

**Full cap inventory to build:**

| # | Cap/Limit | Value | Enforced In | Notes |
|---|-----------|-------|-------------|-------|
| 1 | RISK_MAX_POSITION_SIZE_USD | $100 | order_gateway.py | **THE REAL CAP** — final arbiter |
| 2 | BotBankrollManager max_bet | $300 | bankroll_manager.py | Never reached due to #1 |
| 3 | BotBankrollManager max_daily | $10,000 | bankroll_manager.py | |
| 4 | WEATHER_DAILY_LOSS_LIMIT | $10,000 | settings.py | Redundant with #3? |
| 5 | Group exposure cap | $200 | weather_bot.py | Per (city, date) |
| 6 | City exposure cap | $500 | weather_bot.py | Per city aggregate |
| 7 | Baker-McHale damper | 0.2-1.0 | weather_bot.py | Typically 0.5 |
| 8 | Combined boost cap | 2.0 | weather_bot.py | Caps all boosts |
| 9 | Penny bet floor | 0.04 | weather_bot.py | Min price |
| 10 | Penny bet ceiling | 0.97 | weather_bot.py | Max price |
| 11 | MIN_EDGE | 0.08 US / 0.12 intl | weather_bot.py | |
| 12 | MAX_POSITIONS | 500 | settings.py | |
| 13 | Tail discount | 0.5-1.0 | weather_bot.py | Extreme bucket reducer |
| 14 | Slippage cap | varies | weather_bot.py | Book-depth adjusted |
| 15 | Fill cooldown | 2 scans / 120s | weather_bot.py | After fill failure |

**Key question**: Should RISK_MAX be raised from $100 to $300 to let the sizing pipeline breathe? Or is $100 the right cap for WeatherBot given its 62% WR and +$2,888 P&L on ~2700 entries?

**Deliverable**: Complete table + recommendation. Do NOT change RISK_MAX without explicit approval — it affects all bots.

### P3: Exposure Counter Negative Values Fix
Daily counters show negative city values (e.g., city_Ankara = -$1704).

**Root cause**: Counters start at 0 on deploy day, but exits from yesterday's positions push them negative.

**Fix**: In `_restore_exposure_from_db()`, clamp restored values to `max(0.0, value)`.

**Deliverable**: ONE commit, one line change, test it.

### P4: _close_stale_positions Uses Wrong Table
Pre-existing bug at weather_bot.py line 524-527. Currently checks `paper_trades.realized_pnl IS NOT NULL` to detect closed positions.

**Should use**: `trade_events WHERE event_type='EXIT'` (paper_trades doesn't have SELL records — trade_events is P&L authority).

**Impact**: Low — age-based fallback (20h) catches most cases anyway. But it's a correctness issue.

**Deliverable**: ONE commit. Read the full function before changing. Grep for other `paper_trades` references in weather_bot.py and flag any others.

### P5: Dallas City P&L Investigation
Dallas is the worst city by P&L (-$185). Investigate:

1. Station calibration quality — is EMOS fit poor for Dallas?
2. Entry price bucket distribution for Dallas — is it trading in bad buckets?
3. Lead time distribution — are Dallas trades concentrated at bad lead times?
4. Compare Dallas forecast accuracy vs Atlanta (+$160, best city)

**Deliverable**: Data analysis + recommendation. If station calibration is poor, propose either removing Dallas or adjusting min_edge for it.

### P6: Build Fill Quality Analytics Script
S104 added fill quality data to event_data. Build `scripts/fill_quality.py` to answer:

1. What % of fills use book walk vs heuristic?
2. Average slippage by city
3. Fill probability distribution (histogram)
4. Alpha decay impact (avg bps by latency bucket)
5. Kyle lambda impact (avg bps)

**Deliverable**: Script file + sample output. No bot code changes.

### P7: probability_engine Fallback Risk
`probability_engine.py` fallback returns uniform distribution when scipy fails. This silently enables "doom loop" behavior (treating all buckets as equal, generating fake edges everywhere).

**Fix**: Change fallback to return `{}` (empty dict) instead of uniform distribution. The caller in weather_bot.py should handle empty dict by skipping the group.

**Deliverable**: ONE commit. Read the full function. Verify the caller handles `{}`.

---

## CAP/LIMIT SIMPLIFICATION PROPOSAL

WeatherBot's caps layer like this:
```
Kelly raw output (often $200-500 for good edges)
  → Baker-McHale halves it (~$100-250)
  → Combined boost adjusts (0.5x-2.0x)
  → min(group_cap=$200, city_cap=$500)
  → min(slippage_cap)
  → RISK_MAX=$100 ← almost everything truncated here
```

**The problem**: 6 layers of sizing, but RISK_MAX=$100 makes layers 1-5 mostly irrelevant. The sizing pipeline is doing sophisticated work that gets thrown away.

**Options:**
- A) Raise RISK_MAX to $300 and let the pipeline breathe. More variance but bigger positions on strong edges.
- B) Keep RISK_MAX=$100 but remove redundant upstream caps (group_cap, city_cap become the only non-$100 controls).
- C) Keep everything as-is. $100 is a safe paper-trading cap. Revisit when going live.

**Recommendation**: Option C for now. Document that RISK_MAX is the binding constraint. When going live, revisit whether $100 or $300 is right.

**Do NOT implement any cap changes without explicit approval.**

---

## CROSS-BOT FEATURES TO CONSIDER (from other bots)

1. **Price bucket dampeners** (MirrorBot S102) — MirrorBot dampens 30-50c (0.50x) and ≥70c (0.40x) based on data. WeatherBot doesn't have price-based dampeners. Evaluate whether weather markets show similar bucket P&L patterns.
2. **Per-market entry cap** (MirrorBot S101) — MirrorBot caps at 2 entries per market. WeatherBot can stack entries on the same market. Evaluate whether stacking is profitable or dilutive.
3. **Daily counter pattern** (EsportsBot) — WeatherBot adopted this in S104. Verify it's working correctly by checking VPS daily_counters table.
4. **Drift detection maturity** (EsportsBot has ADWIN) — WeatherBot has DDM/EDDM. Consider whether ADWIN is better.

**Deliverable**: Assessment only. No code changes.

---

## VERIFICATION AFTER ANY CHANGES
1. `pytest` — full suite, all 1623+ pass
2. List every file modified
3. One fix per commit
4. Write change log per CLAUDE.md format
5. Verify on VPS after deploy:
   - `journalctl -u polymarket-ai -f | grep weatherbot_scan_done`
   - `journalctl -u polymarket-ai -f | grep weatherbot_exposure_decremented`

---

## CRITICAL TRAPS (DO NOT BREAK)
- `trade_events` is P&L authority — never paper_trades
- `place_order()` requires `side="YES"/"NO"`. NEVER "BUY"/"SELL".
- Alpha decay is BUY-only (S104b fix). DO NOT remove the `side == "BUY"` gate.
- `_market_group_cache` stores 3-tuple `(group_key, city, cost_usd)`. NEVER expand.
- Daily counters use `CURRENT_DATE` (UTC) — auto-reset at midnight.
- `_restore_exposure_from_db()` now uses daily_counter, NOT paper_trades JOIN.
- `_close_stale_positions()` does direct DB UPDATE — no trade_events EXIT record. By design.
- Exposure reserved BEFORE `place_order()` under `_exposure_lock`, reverted on failure.
- `event_data` dict mutated in-place by paper_trading.py. DO NOT copy before passing.
- `WEATHER_SKIP_COORDINATOR_BUY=True` — confirm_position() does direct INSERT. Do not re-enable coordinator.
- Alpha decay requires `scan_start_mono` in event_data — do NOT remove.
- `asyncpg JSONB`: `CAST(:x AS jsonb)` NOT `:x::jsonb`.
- Baker-McHale factor `1/(1+sigma^2)` is INTENTIONAL. NOT a bug.
- `RISK_MAX_POSITION_SIZE_USD=100` is the FINAL arbiter — everything upstream is advisory.
- Paper engine positions key: `(bot_name, market_id)` tuple (S105 fix).
- `realized_pnl_today` is now `Dict[str, float]` not `float` (S105 fix).
- Python 3.13: `from X import Y` inside function → local for ENTIRE function.
