# S164 MASTER HANDOFF — Session-Poison + Ingestion Gap + Integrity Audit

**Session:** 164
**Date:** 2026-04-08
**Scope:** ALL BOTS — shared infrastructure fixes (continuation of S163 P1/P2 backlog)
**Commits:** (pending — not yet committed)
**Tests:** 1789 passed, 0 failed
**Branch:** master

---

## WHAT THIS SESSION DID

### Trigger
Continuation of S163 uncovered issues. Full P1+P2 sweep requested by operator.

### Fixes Applied (3 files, 116 lines added, 38 removed)

**Fix 1 — 4 except:pass session-poison blocks → SAVEPOINT + logging** `position_manager.py`, `resolution_backfill.py`

These 4 `except Exception: pass` blocks silently poisoned DB sessions, causing all subsequent SQL in the same session to fail with `InFailedSQLTransactionError`:

| File | Line (pre-edit) | Operation | Fix |
|---|---|---|---|
| position_manager.py | 532 | INSERT seed market_prices_latest (historical fallback) | `session.begin_nested()` SAVEPOINT + `logger.warning()` |
| position_manager.py | 669 | INSERT seed market_prices_latest (CLOB/markets fallback) | `session.begin_nested()` SAVEPOINT + `logger.warning()` |
| resolution_backfill.py | 384 | UPDATE markets end_date_iso | `logger.warning()` only (already isolated session) |
| resolution_backfill.py | 694 | UPDATE positions status='closed' | `_pr_sess.begin_nested()` SAVEPOINT + `_pr_sess.commit()` + `logger.warning()` |

**Why SAVEPOINT:** A plain `session.rollback()` would undo ALL prior work in the session (reads, price lookups, updates). `begin_nested()` creates a SAVEPOINT — if the inner INSERT fails, only the SAVEPOINT is rolled back, keeping the outer session healthy. This is the SQLAlchemy-idiomatic solution.

**Fix 2 — Token ingestion gap: condition_id mismatch + Fallback 2b** `position_manager.py`

Root cause: `_update_current_prices()` Fallback 4 (markets table) queried `WHERE id::text = ANY(:mids)` but `positions.market_id` can store either `markets.id` OR `markets.condition_id`. Throughout the codebase, 15+ queries use `(m.id = X OR m.condition_id = X)` — Fallback 4 was the exception.

Changes:
- **Fallback 4 WHERE clause**: Added `OR condition_id = ANY(:mids)` to match both formats.
- **New Fallback 2b**: For tokens still missing after the direct token_id lookup (Fallback 2), resolves token_id → market_id via the `markets` table, then queries `market_prices` by market_id (which can be `markets.id` or `markets.condition_id`). Handles the case where `market_prices` has rows keyed by market_id but the direct token_id lateral join misses them.
- **Diagnostic logging**: After ALL fallbacks, logs `unpriced_positions` warning with count and sample token IDs for any positions that still have no price data. These have no stop-loss/trailing-edge protection.
- **Fallback 4 silent swallow**: Converted `except Exception: pass` to `logger.warning("price_fallback_markets failed: %s", _mkt_err)`.

**Fix 3 — 4 silent exception swallows (non-DB paths)** `position_manager.py`

| Location | Operation | Fix |
|---|---|---|
| L490 | Timestamp arithmetic in staleness check | `logger.debug("price_staleness_check failed")` |
| L641 | CLOB orderbook parsing | `logger.debug("clob_orderbook_fallback failed")` |
| L761 | Stale price alerting | `logger.debug("stale_price_alert failed")` |
| L882-883 | Exit strategy timeout + exception | `logger.debug("exit_strategy_timeout/failed")` |

**Fix 4 — EXIT side transition documentation** `scripts/bot_pnl.py`

Added docstring block documenting the S163 EXIT side transition:
- Before 2026-04-08T16:01:40Z: EXIT events have `side='SELL'` (hardcoded)
- After 2026-04-08T16:01:40Z: EXIT events have `side='YES'` or `side='NO'` (token-outcome)
- Integrity checks should NOT group by side for EXIT events

---

## AUDIT RESULTS: 185 MirrorBot Integrity Violations

Sampled 10 + categorized all 185. Three root cause patterns:

| Pattern | Count | P&L Impact | Description |
|---|---|---|---|
| **1. Oversized EXIT** | 53 | Cosmetic (pnl field independent of size) | Single EXIT with size >> entry size. Historical bug from early MirrorBot (Mar 16) where EXIT stored position.size (tokens) instead of cost basis |
| **2. Multiple EXITs** | 49 | **Real double-counting** | Re-entries after partial close generate additional EXIT events. Each EXIT uses full position size |
| **3. Duplicate RESOLUTION** | 3 | **UNVERIFIED: ~-$654 double-counted** (source: ad-hoc VPS query `SUM(realized_pnl) WHERE event_type='RESOLUTION' AND side='SELL'`, not bot_pnl.py) | Phase 4b emits RESOLUTION for both correct side (YES/NO) AND legacy side (SELL). Two RESOLUTION events per market |
| **Orphaned (no ENTRY)** | 9 | Unknown (no ENTRY to compare) | `seed_positions_from_db` created positions without ENTRY events |
| **Other (single exit, small excess)** | 71 | Likely rounding/partial fills | Single EXIT slightly larger than ENTRY (~1-10% excess) |

**Key findings:**
- Pattern 3 is fixable: deduplicate RESOLUTION events by `(market_id, bot_name)` in Phase 4b, excluding side='SELL' resolutions
- Pattern 2 needs architectural fix: EXIT event should track the specific ENTRY it closes, not use current full position size
- Pattern 1 is historical (all from Mar 16) — no new instances since then

**Immediate action for Pattern 3:** Delete the 3 duplicate RESOLUTION events with side='SELL':
```sql
DELETE FROM trade_events
WHERE bot_name = 'MirrorBot'
  AND event_type = 'RESOLUTION'
  AND side = 'SELL'
  AND market_id IN (
    SELECT market_id FROM trade_events
    WHERE bot_name = 'MirrorBot' AND event_type = 'RESOLUTION'
    GROUP BY market_id HAVING COUNT(*) > 1
  );
```

---

## P3 CHECK: Dead-Letter Validation

```
traded_markets.resolution_status:
  pending:  11,369
  resolved: 1,781
  dead_letter: 0
```

Zero dead-letters after 24h is expected — the make_interval fix restored exponential backoff which starts at hours, not minutes. Dead-letters will appear over the coming days as genuinely unresolvable markets exceed the retry threshold.

---

## FILES MODIFIED

| File | Changes |
|---|---|
| `base_engine/execution/position_manager.py` | 4 session-poison fixes (SAVEPOINT), Fallback 2b (market_id join), Fallback 4 condition_id, 4 silent swallow fixes, unpriced_positions diagnostic |
| `base_engine/data/resolution_backfill.py` | 2 session-poison fixes (1 SAVEPOINT + 1 logging) |
| `scripts/bot_pnl.py` | EXIT side transition documentation |

---

## WHAT WAS NOT FIXED (Remaining Backlog)

### P2 — Fix Phase 4b duplicate RESOLUTION emission
Resolution backfill Phase 4b can emit duplicate RESOLUTION events when the same market is processed multiple times. Need to add EXISTS check or deduplicate query.

### P2 — Pattern 2 EXIT size tracking
EXIT events should record the delta size being closed, not the full current position size. This is an architectural issue in `paper_trading.py`'s `_persist_exit_event()`.

### P3 — WeatherBot YES-side: 37.8% WR, -$24K
Strategy decision, not a bug. Consider disabling or raising confidence threshold.

### P3 — Local vs VPS schema drift
Local DB has `bot_name/timestamp/closed`, VPS has `bot_id/opened_at/status/source_bot`.

### P3 — Dead-letter re-check (48h)
Re-check dead_letter count in 24h. If still 0, investigate whether the code path is actually reachable.

---

## POST-DEPLOY VERIFICATION CHECKLIST

After deploying these changes:

```bash
# 1. Verify no InFailedSQLTransactionError (should be 0)
journalctl -u polymarket-weather --since "1h ago" | grep -c "InFailedSQLTransaction"
journalctl -u polymarket-mirror --since "1h ago" | grep -c "InFailedSQLTransaction"
journalctl -u polymarket-esports --since "1h ago" | grep -c "InFailedSQLTransaction"

# 2. Check if Fallback 2b finds prices for previously-gapped tokens
journalctl -u polymarket-weather --since "1h ago" | grep "price_fallback_market_id_join"
journalctl -u polymarket-esports --since "1h ago" | grep "price_fallback_market_id_join"

# 3. Check unpriced positions count (should decrease from 27)
journalctl -u polymarket-weather --since "1h ago" | grep "unpriced_positions"
journalctl -u polymarket-esports --since "1h ago" | grep "unpriced_positions"

# 4. Verify bots still scanning normally
journalctl -u polymarket-weather --since "5m ago" | tail -5
journalctl -u polymarket-mirror --since "5m ago" | tail -5
journalctl -u polymarket-esports --since "5m ago" | tail -5
```

---

## ROLLBACK

```bash
git revert <commit-sha>  # Single commit with all S164 changes
```
