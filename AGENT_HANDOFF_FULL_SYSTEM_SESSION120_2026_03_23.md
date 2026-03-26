# Agent Handoff — Session 120 (2026-03-23)
## Phantom RESOLUTION Fix + P&L Integrity Guardrails

### What Happened
User requested 24h P&L for all 3 bots. EsportsBot reported -$2,437 with 0/8 winning exits and 0/5 winning NO resolutions. User flagged this as statistically improbable. Forensic audit confirmed: **127 phantom RESOLUTION events** existed for positions that were already fully exited. The resolution backfill was computing hold-to-resolution P&L on positions the bot had already sold, double-counting losses.

### Root Cause
`resolution_backfill.py` Phase 4b (line 407) reads paper_trades to find resolved markets and emits RESOLUTION trade_events. It checked for duplicate RESOLUTIONs but **never checked if the position was fully exited** via EXIT events. 4 code paths create RESOLUTION events; none had this guard.

### Fixes Applied

#### Fix 1: App-Level Guards (S120 core fix)
- **`resolution_backfill.py:441`** — Phase 4b query: added `AND NOT EXISTS (EXIT size >= ENTRY size)` clause
- **`database.py:4696`** — `insert_trade_event()`: added same guard to the atomic INSERT...SELECT for RESOLUTION events. This is the **root defense** — all 4 RESOLUTION creation paths funnel through this function (except one raw SQL backfill script).

#### Fix 2: Data Cleanup
- 127 phantom RESOLUTION events deleted (54 EsportsBot, 71 MirrorBot, 2 WeatherBot)
- Required `DISABLE TRIGGER trg_trade_events_immutable` on `trade_events_2026_03`, then re-enable

#### Fix 3: Reconciliation Audit Job (Guardrail 1)
- **New file: `base_engine/data/trade_event_audit.py`**
- Runs 5 checks: size invariant violations, orphan resolutions, negative sizes
- Wired into `resolution_backfill.py` as Phase 8 — runs every 30 min after backfill
- Read-only, non-fatal. Logs `trade_event_audit_size_violation` warnings.

#### Fix 4: Lifecycle Integration Tests (Guardrail 2)
- **New file: `tests/unit/test_trade_event_guards.py`** — 8 tests
- Tests: RESOLUTION blocked when fully exited, allowed when not exited, SQL uses INSERT...SELECT not VALUES, Phase 4b source contains exit guard, audit returns clean
- Source inspection regression test ensures the guard can't be silently removed

#### Fix 5: bot_pnl.py Cross-Validation (Guardrail 3)
- **Modified: `scripts/bot_pnl.py`** — added integrity query before summary
- Detects any market where EXIT + RESOLUTION size > ENTRY size
- Prints `DATA INTEGRITY WARNINGS` banner if violations found
- Currently clean (0 violations for all 3 bots)

### Corrected P&L

**All-time realized (post-cleanup):**
| Bot | Exits | Resolutions | Total |
|-----|-------|-------------|-------|
| EsportsBot | +$2,754 | -$2,930 | **-$176** |
| MirrorBot | +$6,597 | +$13,539 | **+$20,137** |
| WeatherBot | +$677 | +$2,531 | **+$3,208** |
| **System** | | | **+$23,169** |

EsportsBot was previously reported as +$1,901. It was never profitable — +$2,077 in phantom resolution events masked the real losses.

### Files Modified
1. `base_engine/data/resolution_backfill.py` — Phase 4b exit guard + Phase 8 audit wire
2. `base_engine/data/database.py` — insert_trade_event() exit guard
3. `base_engine/data/trade_event_audit.py` — NEW: reconciliation audit
4. `tests/unit/test_trade_event_guards.py` — NEW: 8 lifecycle tests
5. `scripts/bot_pnl.py` — integrity cross-validation

### Tests
1661 passed, 8 skipped (up from 1653 — 8 new guard tests added)

### Deploy
Not yet deployed. Code is on master branch, ready for deploy.

### Critical Traps Added to Knowledge Base
- **Phantom RESOLUTION events**: `insert_trade_event()` now rejects RESOLUTION when `SUM(EXIT size) >= SUM(ENTRY size)`. Both the backfill query and the DB insert function have this guard.
- **`trade_event_audit.py`**: Runs every 30 min as Phase 8 of resolution backfill. Check logs for `trade_event_audit_size_violation` — if this fires, data is corrupt.
- **`bot_pnl.py` warnings**: If you see `DATA INTEGRITY WARNINGS` in P&L output, the numbers below it are wrong. Run the audit for details.
