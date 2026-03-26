# AGENT HANDOFF — WeatherBot Session 106 (S105b + S106 items)
**Date**: 2026-03-18
**Scope**: WeatherBot only — no MirrorBot, no EsportsBot (MirrorBot changes in diff are from prior uncommitted S105 work, not this session)
**Previous WeatherBot handoff**: `AGENT_HANDOFF_WEATHERBOT_SESSION104_2026_03_18.md`
**S106 Prompt**: `PROMPT_WEATHERBOT_SESSION106.md` (7 tasks, all completed or assessed)
**Status**: ALL CODE CHANGES VERIFIED (1622 passed, 1 pre-existing flaky MirrorBot test). NOT YET COMMITTED.

---

## 1. SESSION IDENTITY

- **Session**: S106 (WeatherBot only, scope-locked)
- **Date**: 2026-03-18
- **Commits**: NONE YET — all changes are uncommitted, verified, ready to commit
- **Tests**: 1622 passed, 1 pre-existing flaky (TestExecuteMirrorTrade::test_entry_trade_success — passes in isolation)
- **Files modified by this session**: `base_engine/execution/paper_trading.py`, `base_engine/weather/probability_engine.py`, `bots/weather_bot.py`, `config/settings.py`, `CLAUDE.md`

---

## 2. WHAT WAS DONE — ALL 7 S106 ITEMS

### P1: Taker-Side Filter — IMPLEMENTED (was "evaluate after 3-5 days")

**Data collected from VPS** (post-S104, 3 days):
```
avg_fill_prob = 0.42 (< 0.50 threshold → IMPLEMENT)
avg_slippage  = 13.2 bps
avg_fill_frac = 0.65
book_walk_ct  = 183 / 191 total entries (96% use book walk)
```

**Decision**: avg_fill_prob=0.42 < 0.50 → taker-side filter warranted per S106 decision tree.

**Implementation** (2 files):
1. `base_engine/execution/paper_trading.py` line ~764: Added flat statistical discount when no taker_side data available in event_data. When `PAPER_TAKER_SIDE_FILTER=true` and no taker_side field exists, multiply fill_prob by `PAPER_TAKER_SIDE_FACTOR` (default 0.55).
2. `config/settings.py` line ~230: Added `PAPER_TAKER_SIDE_FACTOR = 0.55` setting.

**Logic**: ~45% of trades have taker on same side as our order. The existing `PAPER_TAKER_SIDE_FILTER` only fires when `_taker_side` data exists (from RTDS). WeatherBot has NO RTDS data, so the filter was dead code. Now applies flat 0.55x discount statistically.

**Fill quality by city** (post-S104, 25 cities):

| City | Entries | Avg Fill Prob | Avg Slip (bps) |
|------|---------|---------------|----------------|
| Chicago | 133 | 0.251 | 10.4 |
| Dallas | 106 | 0.313 | 7.2 |
| NYC | 93 | 0.359 | 6.2 |
| Seattle | 92 | 0.283 | 8.3 |
| Miami | 91 | 0.269 | 11.6 |
| Atlanta | 87 | 0.311 | 7.9 |
| Toronto | 78 | 0.238 | 7.6 |
| London | 67 | 0.287 | 7.4 |
| Seoul | 67 | 0.274 | 10.8 |
| Tokyo | 65 | 0.296 | 9.7 |
| Milan | 58 | 0.314 | 6.8 |
| Sao Paulo | 52 | 0.212 | 9.9 |
| Paris | 51 | 0.329 | 6.3 |
| Munich | 51 | 0.411 | 6.0 |
| Madrid | 49 | 0.280 | 7.5 |
| Buenos Aires | 45 | 0.246 | 6.8 |
| Warsaw | 42 | 0.290 | 6.7 |
| Wellington | 34 | 0.438 | 7.7 |
| Tel Aviv | 34 | 0.313 | 7.0 |
| Singapore | 32 | 0.354 | 6.4 |
| Shanghai | 26 | 0.322 | 6.0 |
| Ankara | 26 | 0.436 | 7.6 |
| Lucknow | 24 | 0.374 | 6.9 |
| Taipei | 16 | 0.326 | 9.4 |
| Hong Kong | 7 | 0.458 | 5.7 |

---

### P2: Config Contradiction Audit — COMPLETED (documentation fix)

**CRITICAL FINDING**: S106 prompt claimed `RISK_MAX_POSITION_SIZE_USD=$100` — this is **WRONG**. Actual value is **$1,000** in both local settings.py and VPS. The real binding per-trade cap is **BotBankrollManager max_bet_usd=$300**.

**VPS Config Misalignment Found**:
| Setting | Local settings.py | VPS Live | Issue |
|---------|-------------------|----------|-------|
| `WEATHER_DAILY_LOSS_LIMIT` | **$10,000** (S105) | **$2,000** (old) | VPS NOT DEPLOYED since S105 |
| `WEATHER_TOTAL_CAPITAL` | **$20,000** (S105) | Unknown (old default) | Need deploy |
| `RISK_MAX_POSITION_SIZE_USD` | **$1,000** | **$1,000** | Correct — S106 prompt was wrong |

**Corrected Cap Hierarchy** (actual, verified from code):

| # | Cap | Value | Enforced In | Binding? |
|---|-----|-------|-------------|----------|
| 1 | Kelly x 0.25 x $20K | ~$200-500 typical | bankroll_manager.py | Often |
| 2 | Baker-McHale damper | 0.2-1.0 (typ 0.5) | weather_bot.py | Halves Kelly |
| 3 | Combined boost cap | 2.0x max | weather_bot.py | Rarely |
| 4 | **BotBankrollManager max_bet** | **$300** | bankroll_manager.py:42 | **THE REAL PER-TRADE CAP** |
| 5 | RISK_MAX_POSITION_SIZE_USD | $1,000 | risk_manager.py | Never binding |
| 6 | Group cap (city:date) | $1,000 | weather_bot.py | Aggregate |
| 7 | City cap (all dates) | $2,000 | weather_bot.py | Aggregate |
| 8 | Daily loss limit | $10K local / $2K VPS | settings.py | VPS misaligned |
| 9 | Bot-wide exposure | $50,000 | settings.py | Rarely |
| 10 | Max positions | 500 | settings.py | Rarely |

**Typical trade size flow**: Kelly($400) x Baker-McHale(0.5) = $200 x boost(1.0) = $200 -> capped by max_bet to **$200** (or $300 if Kelly is high enough). Most trades land at $100-$250.

**Fix applied**: CLAUDE.md line 142 corrected from "max_bet_usd=$100 is the real cap" to "$300 for Weather/Mirror/Esports, $100 fallback for unknown bots".

**ACTION REQUIRED**: VPS needs a fresh deploy to pick up `WEATHER_DAILY_LOSS_LIMIT=$10,000` and `WEATHER_TOTAL_CAPITAL=$20,000`.

---

### P3: Negative Exposure Counter Clamp — IMPLEMENTED

**Root cause**: Daily counters start at 0 on deploy day. Exits from yesterday's positions decrement today's counters below 0 (e.g., city_Ankara = -$1704).

**Fix** in `bots/weather_bot.py` `_restore_exposure_from_db()`:
1. Skip negative values (don't load into `_group_exposure`/`_city_exposure`)
2. After loading, UPDATE daily_counters SET counter_value=0 WHERE counter_value < 0 (cleans DB)
3. Log count of clamped counters

---

### P4: _close_stale_positions Table Fix — IMPLEMENTED

**Root cause**: Query at weather_bot.py line 524 used `paper_trades.realized_pnl IS NOT NULL` to detect exited positions. But paper_trades has NO SELL records (trade_events is P&L authority).

**Fix**: Changed subquery from:
```sql
-- BEFORE:
SELECT pt.market_id FROM paper_trades pt
WHERE pt.realized_pnl IS NOT NULL AND pt.created_at > NOW() - INTERVAL '24 hours'

-- AFTER:
SELECT te.market_id FROM trade_events te
WHERE te.bot_name = 'WeatherBot' AND te.event_type = 'EXIT'
AND te.event_time > NOW() - INTERVAL '24 hours'
```

---

### P5: Dallas City P&L Investigation — ASSESSED, NO ACTION

**Findings**:
- Dallas fill quality is mid-pack (fill_prob=0.31, slip=7.2bps) — NOT an outlier
- Dallas has more entries than most cities (106 vs median ~65) — higher variance expected
- **Cannot get per-city resolution P&L**: pre-S104 RESOLUTION events lack `city` in event_data
- The -$185 Dallas figure from S106 was from an older analysis that may have used position-level joins

**Recommendation**: Wait 2 weeks for RESOLUTION events with city tags to accumulate. Then re-run per-city P&L. If Dallas still worst by >$200, raise min_edge from 0.08 to 0.12.

---

### P6: Fill Quality Analytics Script — CREATED

**File**: `scripts/fill_quality.py` (new file, created this session)

Answers:
1. % of fills using book walk vs heuristic
2. Average slippage by city
3. Fill probability distribution
4. Alpha decay impact by latency bucket
5. Kyle lambda impact

Run: `PYTHONPATH=. python scripts/fill_quality.py [hours]` (default 72h)

---

### P7: probability_engine Fallback Fix — IMPLEMENTED

**Root cause**: `_bucket_probabilities_fallback()` at probability_engine.py:214-217 returned a **uniform distribution** when ensemble is degenerate (total prob <= 0.01). This creates fake 45%+ edges on every bucket — a doom loop. The main scipy path (line 158-168) already returns `{}` (M1 fix), but the fallback was never updated.

**Fix**: Changed lines 214-217 from uniform assignment to `return {}`.

**Caller safety**: Both callers handle `{}` gracefully:
- Main caller (`_scan_temperature_markets` ~line 1754): `compute_edges()` iterates `.items()` -> `[]`. `if edges:` guard prevents crash.
- Quick caller (~line 998): `.get(market_id, 0.0)` -> 0.0 -> negative edge -> skips trade.

---

## 3. VPS DIAGNOSTIC SNAPSHOT (taken this session)

### P&L Summary (all-time)
```
ENTRY:      2889 events, $0 realized (expected)
EXIT:        267 events, $0 realized (realized_pnl in event_data, not top-level)
RESOLUTION:  677 events, $0 realized (same)
```

### Open Positions
```
open:   96 (actively managed)
closed: 2698 (historical)
```

### Scan Performance
- Scan cycles running ~120s intervals (5-min config, actual 2min due to WEATHER_PSW_SCAN_DIVISOR=2)
- ~25 cities active, ~45 groups per scan
- Fill rejection rate: majority of attempted trades rejected by fill probability model (working as designed)

### Daily Counters (sample)
- Negative counters present pre-fix (city_Ankara=-$1704, etc.)
- Post-fix: will clamp to 0 on next restart

---

## 4. CURRENT STATE OF ALL FILES (uncommitted changes)

### `base_engine/execution/paper_trading.py`
- Taker-side flat discount (lines 757-768): When `PAPER_TAKER_SIDE_FILTER=true` and no taker_side data in event, apply `PAPER_TAKER_SIDE_FACTOR` (0.55) to fill_prob

### `base_engine/weather/probability_engine.py`
- Fallback degenerate case (lines 214-216): Return `{}` instead of uniform distribution

### `bots/weather_bot.py`
- `_close_stale_positions()` (lines 467, 516-530): Comments and SQL updated from paper_trades to trade_events EXIT
- `_restore_exposure_from_db()` (lines 2903-2937): Negative counter clamping on restore + DB cleanup

### `config/settings.py`
- `PAPER_TAKER_SIDE_FACTOR` (line ~230): New setting, default 0.55

### `CLAUDE.md`
- Line 142: Corrected "$100 is the real cap" to "$300 for Weather/Mirror/Esports, $100 fallback for unknown bots"

### Also in diff (NOT from this session):
- `bots/mirror_bot.py`: S103/S105 MirrorBot changes (min_confidence gate, log level changes) — **pre-existing uncommitted work, not this session**
- `tests/unit/test_mirror_bot_logic.py`: MirrorBot test fix — **pre-existing**
- `base_engine/data/ingestion_error_capture.txt`: Line number shift — **cosmetic**

---

## 5. KEY ARCHITECTURE FACTS (WeatherBot-specific, carried from S104)

### Scan Loop
- **Interval**: 120s (5-min config / PSW_SCAN_DIVISOR=2)
- **Trade concurrency**: 8 parallel place_order calls per scan
- **25 cities**, ~45 temperature groups per scan

### Exposure Tracking
- `_group_exposure: Dict[str, float]` — "city:date" to USD. Protected by `_exposure_lock` (asyncio.Lock)
- `_city_exposure: Dict[str, float]` — city to USD
- Reserved BEFORE `place_order()`, reverted on failure. Lock MUST be held for both.
- Restored from `daily_counters` table on startup (<10ms)
- Decremented on exit via `_market_group_cache` lookup

### Position Lifecycle
- **Entry**: `WEATHER_SKIP_COORDINATOR_BUY=True` -> confirm_position() does direct INSERT
- **Exit**: Position_manager handles TP/SL/model-reversal. WeatherBot detects via `_known_open_markets` diff and decrements exposure
- **Stale cleanup**: Every 10 scans. Closes positions where target date passed OR age >20h. Direct DB UPDATE, NO trade_events EXIT record.
- **Exit cooldown**: `_recently_exited` dict, 900s TTL, persisted to Redis

### Fill Model (paper_trading.py BUY path)
1. Alpha decay (BUY-only, S104b)
2. L2 Book walk (VWAP from orderbook)
3. Size-dependent slippage (4 tiers: 35/50/75/120 bps)
4. Square-root market impact
5. Kyle's lambda adverse selection
6. Cross-scan cumulative impact
7. Resolution proximity penalties
8. **Taker-side discount** (S106 NEW: flat 0.55x when no RTDS data)
9. Fill probability check (5 multiplicative factors)
10. Rejection roll
11. Partial fill
12. Cash deduction + position creation

SELL: ALWAYS succeeds. No slippage, no alpha decay, no fill probability.

### Day Boundary
- `_handle_daily_boundary()` clears exposure dicts + `_market_group_cache` at UTC midnight
- Daily counters auto-reset via `CURRENT_DATE` key

### Caches
- `_market_group_cache: Dict[str, Tuple[str, str, float]]` — market_id to (group_key, city, cost_usd). NEVER expand tuple.
- `_market_meta_cache` is MirrorBot's — DO NOT confuse

---

## 6. CRITICAL TRAPS (DO NOT BREAK)

- **trade_events is P&L AUTHORITY** — never paper_trades for P&L
- **place_order()** requires `side="YES"/"NO"`. NEVER "BUY"/"SELL"
- **Alpha decay is BUY-only** (S104b). DO NOT remove the `side == "BUY"` gate
- **`_market_group_cache`** stores 3-tuple `(group_key, city, cost_usd)`. NEVER expand
- **Daily counters** use `CURRENT_DATE` (UTC) — auto-reset at midnight
- **`_restore_exposure_from_db()`** uses daily_counter, NOT paper_trades JOIN
- **`_close_stale_positions()`** does direct DB UPDATE — no trade_events EXIT. By design
- **Exposure lock**: Reserved BEFORE place_order(), reverted on failure. Lock MUST be held for both
- **`event_data` dict** mutated in-place by paper_trading.py. DO NOT copy before passing
- **`WEATHER_SKIP_COORDINATOR_BUY=True`** — confirm_position() does direct INSERT
- **`scan_start_mono`** in event_data — required for alpha decay. Do NOT remove
- **`asyncpg JSONB`**: `CAST(:x AS jsonb)` NOT `:x::jsonb`
- **`asyncpg DATE`**: Pass `CURRENT_DATE` as SQL literal, NOT Python strftime
- **Baker-McHale** `1/(1+sigma^2)` is INTENTIONAL. NOT a bug
- **RISK_MAX_POSITION_SIZE_USD=$1,000** (not $100). Real per-trade cap is **BotBankrollManager max_bet=$300**
- **Paper engine positions key**: `(bot_name, market_id)` tuple (S105)
- **`realized_pnl_today`** is `Dict[str, float]` not `float` (S105)
- **Python 3.13**: `from X import Y` inside function -> local for ENTIRE function
- **`PAPER_TAKER_SIDE_FILTER=true`** must be on for flat factor to apply (S106)
- **probability_engine fallback** now returns `{}` for degenerate case (S106). Both callers handle this.
- **VPS deploy needed**: `WEATHER_DAILY_LOSS_LIMIT` is $2,000 on VPS (should be $10,000)

---

## 7. OUTSTANDING ITEMS / NEXT STEPS

### IMMEDIATE (before next session)
- [ ] **Commit all changes** (this session produced 0 commits — all changes are uncommitted)
- [ ] **Deploy to VPS** — picks up: taker-side factor, probability_engine fix, stale positions fix, counter clamp, AND the S105 config fixes ($10K daily loss, $20K capital)

### P2 (2 weeks out): Dallas City P&L Re-evaluation
- Wait for RESOLUTION events with city tags to accumulate (~2 weeks post-S104 deploy)
- Query: `SELECT city, sum(realized_pnl) FROM trade_events WHERE bot_name='WeatherBot' AND event_type='RESOLUTION' AND event_data ? 'city' GROUP BY city ORDER BY sum ASC;`
- If Dallas still worst by >$200, raise min_edge from 0.08 to 0.12

### P3: Cross-Bot Feature Assessment (deferred from S106)
- Price bucket dampeners (MirrorBot pattern) — evaluate for weather markets
- Per-market entry cap — evaluate stacking profitability
- ADWIN vs DDM/EDDM drift detection
- Read-only analysis, no code changes

### P4: Probability Engine Fallback Test
- The P7 fix has no dedicated unit test. Consider adding one for `_bucket_probabilities_fallback()` degenerate case.

### P5: Fill Quality Monitoring
- Run `scripts/fill_quality.py` weekly to track fill model accuracy
- After taker-side factor deploy, compare avg_fill_prob before/after (expect ~0.42 -> ~0.23)
- If fill rejection rate exceeds 85%, consider lowering PAPER_TAKER_SIDE_FACTOR from 0.55 to 0.70

---

## 8. P&L SNAPSHOT (as of session start)

```
All-time:
  ENTRY:      2889 events
  EXIT:        267 events, ~+$606 realized
  RESOLUTION:  677 events, ~+$2,282 realized
  Total:      ~+$2,888 realized

Open positions: 96
Active cities: 25
```

Hold duration sweet spot: 24-48h (73% WR, +$432 from exits, +$1,054 from resolutions)
Side split: NO positions vastly outperform YES (81% vs 15% resolution WR)

---

## 9. VERIFICATION COMMANDS (for next session)

```bash
# Check taker-side factor is working (after deploy)
journalctl -u polymarket-ai --since "1 hour ago" | grep paper_taker_side

# Check negative counters are clamped
sudo -u postgres psql -d polymarket -c "SELECT * FROM daily_counters WHERE bot_id='WeatherBot' AND counter_date=CURRENT_DATE AND counter_value < 0;"

# Check stale positions closing via trade_events
journalctl -u polymarket-ai --since "1 hour ago" | grep weatherbot_stale_closed

# Full P&L
PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/bot_pnl.py WeatherBot 24

# Fill quality report
PYTHONPATH=/opt/polymarket-ai-v2 /opt/pa2-shared/venv/bin/python scripts/fill_quality.py 72

# Scan health
journalctl -u polymarket-ai -f | grep weatherbot_scan_done
```

---

## 10. CONFIG REFERENCE (live values after deploy)

```
WEATHER_KELLY_FRACTION=0.25
WEATHER_TOTAL_CAPITAL=20000
WEATHER_MAX_POSITIONS=500
WEATHER_MAX_PER_GROUP_USD=1000
WEATHER_MAX_CORRELATED_EXPOSURE=2000
WEATHER_DAILY_LOSS_LIMIT=10000  (currently $2000 on VPS — DEPLOY NEEDED)
WEATHER_MAX_TOTAL_EXPOSURE_USD=50000
WEATHER_MIN_EDGE=0.08
WEATHER_INTL_MIN_EDGE=0.12
WEATHER_DEFAULT_SIZE=25
WEATHER_FILL_FAIL_COOLDOWN_SCANS=2
WEATHER_FILL_FAIL_COOLDOWN_SECS=120
WEATHER_MIN_FILL_PROB_ESTIMATE=0.15
WEATHER_SKIP_COORDINATOR_BUY=true
WEATHER_TRADE_CONCURRENCY=8
SCAN_INTERVAL_WEATHER=300
WEATHER_PSW_SCAN_DIVISOR=2
SIMULATION_MODE=true

# BotBankrollManager (bankroll_manager.py:42)
WeatherBot: capital=20000, kelly_fraction=0.25, max_bet_usd=300, max_daily_usd=10000

# Risk manager (settings.py)
RISK_MAX_POSITION_SIZE_USD=1000  (NOT $100 — S106 prompt was wrong)

# Paper trading (settings.py)
PAPER_TAKER_SIDE_FILTER=true
PAPER_TAKER_SIDE_FACTOR=0.55  (NEW — S106)
```

---

## 11. READ ORDER FOR NEXT SESSION

1. This handoff document
2. `CLAUDE.md` — development rules
3. `bots/weather_bot.py` — the bot (~4,000 lines)
4. `base_engine/weather/probability_engine.py` — EMOS + skew-normal
5. `base_engine/execution/paper_trading.py` — fill model
6. `config/settings.py` — all config
7. `scripts/fill_quality.py` — analytics script (new)
8. `PROMPT_WEATHERBOT_SESSION106.md` — original task list (all items completed/assessed)
