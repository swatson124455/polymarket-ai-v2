# MirrorBot Audit Report — Session 127

**Date**: 2026-03-24
**Scope**: Full line-by-line audit of MirrorBot + all supporting modules
**Files audited**: `mirror_bot.py`, `mirror_ml_selector.py`, `mirror_calibration.py`, `mirror_adaptive_safety.py`, `elite_watchlist.py`, `elite_reliability.py`, `elite_detector.py`

---

## BUGS

### BUG-1 — Wash Detection O(n²) Nested Loop [P3]
**File**: `elite_watchlist.py:389-393`
**What**: For each BUY, iterates ALL SELLs checking `abs(b_time - s_time) <= 3600`. With 100 buys and 100 sells, that's 10,000 comparisons per trade event.
**Why it hurts**: On high-volume markets where a trader has hundreds of trades, this burns CPU on every RTDS event. In hot periods the scan loop stalls, increasing copy latency.
**Fix**: Sort sells by time, binary-search for the 1h window boundary. Reduces to O(n log n).

### BUG-2 — Wash Detection Over-Counts Round-Trips [P3]
**File**: `elite_watchlist.py:387-392`
**What**: `_buys` filter is `t[0] in ("YES", "NO")` — this matches ALL entry trades, not just buys. Every entry+sell pair within 1h counts as a round-trip, inflating the count.
**Why it hurts**: Legitimate traders with normal activity patterns get falsely flagged as wash traders, permanently blocked from the copy pipeline.
**Fix**: Track entry vs exit explicitly in the tuple (add a `is_entry` flag), not by side name.

### BUG-3 — `_trader_market_trades` Memory Leak [P3]
**File**: `elite_watchlist.py:377-383`
**What**: Pruning removes entries older than 24h, but the outer dict key `(trader, market)` is never removed even when its list becomes empty. Over weeks, this accumulates thousands of empty or near-empty entries.
**Why it hurts**: Slow memory growth over bot lifetime. On 16GB VPS, not critical short-term, but after weeks of uptime the dict consumes meaningful memory.
**Fix**: After pruning, delete the key if the list is empty: `if not _trades: del self._trader_market_trades[_wash_key]`

### BUG-4 — Cross-Channel Dedup Gap [P4]
**File**: `elite_watchlist.py`
**What**: RTDS WebSocket can deliver the same trade via different channels (position updates, live feed). The dedup set uses `transaction_hash` from RTDS, but some channels don't include it.
**Why it hurts**: Same whale trade could be executed twice — once per channel. Low frequency but real P&L risk.
**Fix**: Build a composite dedup key: `f"{trader}:{market_id}:{side}:{size}"` with a 60s TTL, in addition to transaction_hash dedup.

### BUG-5 — False Orphan Exits on Restart [P3]
**File**: `mirror_bot.py` — exit management
**What**: After restart, `_open_positions` is restored from DB but `_entered_market_sides` was rebuilt from trade_events. If a position was opened AND closed between restarts (resolved while offline), the market appears in `_entered_market_sides` but not `_open_positions`, potentially confusing the exit scanner.
**Why it hurts**: Edge case. The exit scanner already checks DB positions, so damage is limited to unnecessary log noise and wasted scan cycles.
**Fix**: No code change needed — the existing guards handle this. Downgrade to monitoring item.

### BUG-9 — Datetime Scoping Fragility in ML Selector [P4]
**File**: `mirror_ml_selector.py:91`
**What**: `from datetime import datetime as _dt` inside a function body. If `datetime` is also imported at module level (it is, at line 20), this works but is fragile — any refactor that uses `datetime` before this line in the same function will trigger `UnboundLocalError` in Python 3.13.
**Why it hurts**: Not broken today, but the next person who edits `load_xgb()` and references `datetime` before line 91 gets a runtime crash.
**Fix**: Remove the local import. Use the module-level `datetime` import that already exists at line 20.

### BUG-11 — `set('{}')` Creates `{'{', '}'}` Not Empty Set [P2]
**File**: `mirror_bot.py:281`
**What**: `r.trader_addresses` comes from a DB TEXT column. When the column contains the string `'{}'` (Postgres empty array literal), `set('{}')` iterates the string and creates `{'{', '}'}` — a set with two characters.
**Why it hurts**: Every restored position has 2 phantom traders. `len(pos["traders"])` returns 2 instead of 0, which inflates trader counts and could affect consensus logic.
**Fix**: Parse the Postgres array literal properly:
```python
"traders": set() if not r.trader_addresses or r.trader_addresses in ('{}', '[]', '') else set(r.trader_addresses),
```

### BUG-12 — Reconciliation Runs Once Then Dies [P3]
**File**: `mirror_bot.py:525-526`
**What**: `self._recon_done = True` is set immediately on scan 3, then reconciliation never runs again regardless of success/failure.
**Why it hurts**: If the Gamma API call fails on scan 3 (timeout, rate limit), leader reconciliation never retries. Positions that drifted from reality stay drifted forever.
**Fix**: Only set `_recon_done = True` inside the success path of `_bg_recon()`, not before the task launches. Or better: run reconciliation every N scans (e.g., every 100 scans ≈ 75 min).

### BUG-13 — Exit Size Uses Wrong Reference [P2]
**File**: `mirror_bot.py` — exit execution
**What**: When calculating exit cost for exposure tracking, the code uses `size * entry_price` from the position dict. But for partial exits, `size` may have already been decremented by a prior partial exit.
**Why it hurts**: Exposure tracking drifts from reality. Over time, `_daily_exposure` becomes inaccurate, potentially allowing more trades than intended or blocking legitimate ones.
**Fix**: Track the actual exit size separately from the position's remaining size. Use the exit amount in exposure calculations.

### BUG-14 — Adaptive Safety Drawdown Calculated on Last-50 P&L, Not Capital [P2]
**File**: `mirror_adaptive_safety.py:80-86`
**What**: Drawdown is computed as `(peak - cum) / max(peak, 1.0)` where `peak` and `cum` are cumulative sums of the last 50 trade P&Ls. This measures drawdown relative to the P&L curve's peak, not relative to total bot capital.
**Why it hurts**: After a hot streak of +$500 followed by -$100, drawdown shows 20% even though bot capital dropped only 0.5%. The `2.5x` multiplier at line 115 then aggressively cuts max_positions — the bot becomes overly conservative after completely normal P&L variance.
**Fix**: Normalize drawdown against actual bot capital (BotBankrollManager.get_available_capital()). Or: raise the drawdown sensitivity threshold from 5% to 15%.

### BUG-15 — `OR`-JOIN Full Table Scan in Elite Detector [P2]
**File**: `elite_detector.py:54`
**What**: `JOIN markets m ON (t.market_id = CAST(m.id AS TEXT) OR t.market_id = m.condition_id)` — the `OR` prevents Postgres from using any index. This is a full nested-loop join on every call.
**Why it hurts**: This query runs on elite refresh (every N scans). With 100k+ trades and 50k+ markets, it can take 30+ seconds, blocking the scan loop (the 10s timeout at line 549 mitigates but doesn't fix).
**Fix**: Use two separate queries with UNION ALL, each hitting one index:
```sql
SELECT ... FROM trades t JOIN markets m ON t.market_id = CAST(m.id AS TEXT) ...
UNION ALL
SELECT ... FROM trades t JOIN markets m ON t.market_id = m.condition_id ...
```

### BUG-16 — `math.exp()` Overflow in ML Combo Score [P3]
**File**: `mirror_ml_selector.py:287`
**What**: `1.0 / (1.0 + math.exp(-q_adv))` — when Q-advantage is very negative (e.g., -800), `math.exp(800)` raises `OverflowError`.
**Why it hurts**: One pathological Q-table entry crashes the entire score_trade() call. The except clause returns a default, but the error is silent and all three strategy scores are lost.
**Fix**: Clamp input: `math.exp(-max(-500, min(500, q_adv)))`.

### BUG — Price Direction Filter Only Checks One Direction [P3]
**File**: `mirror_bot.py:1377-1383`
**What**: The filter checks `_move_pct > _dir_thresh` (price moved UP), which blocks YES trades where the market already moved up. But it doesn't check the inverse for NO trades — if market moved DOWN 10% since the whale's fill, copying a NO trade also has consumed edge.
**Why it hurts**: NO-side copies execute after the edge is already consumed by other copiers, resulting in worse-than-expected entry prices.
**Fix**: For NO trades, check `_move_pct < -_dir_thresh` (market moved down = toward NO).

### BUG — `_market_cooldown` Dict Unbounded [P4]
**File**: `mirror_bot.py`
**What**: Cooldown entries are set with monotonic timestamps but never pruned. After months, thousands of stale cooldown keys accumulate.
**Why it hurts**: Slow memory growth. Not critical but wasteful.
**Fix**: Prune cooldowns older than 2x the cooldown period during periodic maintenance.

---

## INEFFICIENCIES

### INEFF-1 — New aiohttp Session Per Elite Refresh [P4]
**File**: `elite_watchlist.py:95`
**What**: Every refresh creates a new `aiohttp.ClientSession()`. Session creation includes TCP connection pool setup, cookie jar init, etc.
**Why it hurts**: Unnecessary overhead on every refresh cycle. With connection reuse disabled, each API call pays full TCP+TLS handshake cost.
**Fix**: Create session once in `__init__`, reuse across refreshes. Close in cleanup.

### INEFF-2 — Pipe-Delimited Serialization in Reliability Tracker [P4]
**File**: `elite_reliability.py:90`
**What**: Uses `|`-delimited string serialization for persistence. If any field (trader address, category name) contains `|`, deserialization corrupts the data.
**Why it hurts**: Low risk today (addresses are hex, categories are English words), but any future category with `|` silently corrupts the tracker.
**Fix**: Use JSON serialization instead of pipe-delimited.

### INEFF-6 — Dead Code in Reliability Tracker [P5]
**File**: `elite_reliability.py:175`
**What**: The condition `a + b >= 2` is always true because `a` and `b` are initialized to 1.0 (Beta prior) and only increment.
**Why it hurts**: Dead branch. No runtime impact but misleading to readers.
**Fix**: Remove the dead branch.

---

## DATA FLOW ISSUES

### DATA-1 — Mirror Calibration Reads `paper_trades` (Legacy) [P3]
**File**: `mirror_calibration.py:56`
**What**: Calibration queries `paper_trades` for historical trade outcomes. Per CLAUDE.md, `trade_events` is the P&L authority. `paper_trades` is missing EXIT events and has stale data.
**Why it hurts**: Calibration is trained on incomplete/stale data, producing worse confidence adjustments.
**Fix**: Change query to read from `trade_events` with `event_type IN ('ENTRY', 'EXIT', 'RESOLUTION')`.

### DATA-2 — `_entered_market_sides` Timestamp Reset [P4]
**File**: `mirror_bot.py` — state restoration
**What**: On restart, `_entered_market_sides` is rebuilt from trade_events but without timestamps. The set grows indefinitely (every market ever traded), never pruning old entries.
**Why it hurts**: After months of trading, opposing-side checks run against thousands of historical entries. The set correctly prevents re-entry on resolved markets, but the lack of expiry means it grows unbounded.
**Fix**: Add a lookback window (e.g., 30 days) to the trade_events query that rebuilds the set.

### DATA-5 — Opposing Pair Cleanup on Resolution [P4]
**File**: `mirror_bot.py`
**What**: When a position resolves, `_open_positions` is cleaned up but `_entered_market_sides` retains the entry forever. This is by design (prevents re-entry on already-resolved markets), but the docstring doesn't explain this.
**Why it hurts**: No runtime impact — this is correct behavior. Just missing documentation.
**Fix**: Add comment explaining why `_entered_market_sides` entries are never removed.

---

## LOGGING GAPS

### LOG-2 — Reconciliation Errors Swallowed [P4]
**File**: `mirror_bot.py:533-534`
**What**: `_bg_recon()` catches all exceptions with `logger.warning()` but doesn't include the traceback.
**Why it hurts**: When reconciliation fails, you see "mirror_leader_recon error: [msg]" but can't diagnose the root cause without the stack trace.
**Fix**: Add `exc_info=True` to the warning log.

---

## RACE CONDITIONS

### RACE-1 — `create_task(_bg_recon)` Fire-and-Forget [P3]
**File**: `mirror_bot.py:536`
**What**: Reconciliation runs via `asyncio.create_task()`. Per CLAUDE.md: "Do NOT use asyncio.create_task() for financial write-throughs." If the task writes position corrections to DB, errors are silently lost.
**Why it hurts**: If reconciliation discovers a drift and tries to correct positions in DB, a failed write goes unnoticed. Positions stay incorrect.
**Fix**: Store the task reference. Check for exceptions in the next scan loop iteration.

---

## STORAGE CONCERNS

### STORE-1 — Stop-Loss Event Type Naming [P5]
**File**: `mirror_bot.py` — stop-loss handler
**What**: Stop-loss exit events use `event_type='EXIT'` with `reason='stop_loss'` in event_data. This is correct but the naming was flagged in audit as inconsistent — some code paths check for `reason` in event_data, others don't.
**Why it hurts**: Not a bug. The EXIT event is correct. Just inconsistent metadata querying patterns across analysis scripts.
**Fix**: Standardize analysis scripts to always check `event_data->>'reason'` when filtering EXIT types.

---

## SUMMARY TABLE

| ID | Severity | Description | Est. Fix |
|----|----------|-------------|----------|
| BUG-11 | **P2** | `set('{}')` phantom traders | 5 min |
| BUG-13 | **P2** | Exit size wrong reference | 15 min |
| BUG-14 | **P2** | Drawdown calc vs bot capital | 10 min |
| BUG-15 | **P2** | OR-JOIN full table scan | 20 min |
| BUG-1 | P3 | Wash detection O(n²) | 15 min |
| BUG-2 | P3 | Wash overcounting entries | 10 min |
| BUG-3 | P3 | trader_market_trades leak | 5 min |
| BUG-12 | P3 | Reconciliation one-shot | 10 min |
| BUG-16 | P3 | math.exp overflow | 5 min |
| BUG-DIR | P3 | Price direction one-sided | 10 min |
| RACE-1 | P3 | create_task fire-and-forget | 10 min |
| DATA-1 | P3 | Calibration reads paper_trades | 10 min |
| BUG-4 | P4 | Cross-channel dedup gap | 15 min |
| BUG-9 | P4 | datetime scoping fragile | 5 min |
| BUG-COOL | P4 | _market_cooldown unbounded | 5 min |
| DATA-2 | P4 | _entered_market_sides unbounded | 5 min |
| INEFF-1 | P4 | aiohttp session per refresh | 10 min |
| INEFF-2 | P4 | Pipe-delimited serialization | 15 min |
| DATA-5 | P4 | Missing doc on opposing pair | 2 min |
| LOG-2 | P4 | Reconciliation no traceback | 2 min |
| BUG-5 | P5 | False orphan exits (non-issue) | 0 min |
| INEFF-6 | P5 | Dead code reliability | 2 min |
| STORE-1 | P5 | Stop-loss naming consistency | 5 min |

**Total bugs**: 13 | **Inefficiencies**: 3 | **Data flow**: 3 | **Other**: 4
**Critical P2 fixes**: 4 items, ~50 min total
