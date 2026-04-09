---
name: S163/S164 Uncovered Issues
description: Remaining P2/P3 backlog from S163 infrastructure review, updated after S164 sweep
type: project
---

## Status: Partially resolved by S164

### Resolved in S164:
- P1 #1: Token ingestion gap — condition_id mismatch fixed + Fallback 2b added
- P1 #2: 4 except:pass session-poison blocks — SAVEPOINT + logging
- P1 #3: EXIT side transition — documented in bot_pnl.py
- P2 #2: 4 silent exception swallows — converted to logger.debug/warning
- P2 #1: 185 MirrorBot integrity violations — audited and categorized

### Remaining:

**P2 — Phase 4b duplicate RESOLUTION emission**
Resolution backfill can emit duplicate RESOLUTION events for same market. 3 current duplicates with side='SELL' (UNVERIFIED P&L impact — from ad-hoc VPS query, not bot_pnl.py).
**Fix:** Add EXISTS dedup check in Phase 4b query, or delete side='SELL' RESOLUTION events.

**P2 — EXIT event size tracking**
EXIT events store full position size instead of delta being closed. 49 violations with multiple oversized exits.
**Fix:** Architectural change in paper_trading.py _persist_exit_event() — track which ENTRY the EXIT closes.

**P3 — WeatherBot YES-side: 37.8% WR, -$24K**
No edge. Strategy decision, not a bug.

**P3 — Local vs VPS schema drift**
Local DB columns differ from VPS canonical.

**P3 — Dead-letter re-check (48h)**
make_interval fix deployed 2026-04-08. Re-check dead_letter count 2026-04-10.

**Why:** These are infrastructure debt items from the S163 root-cause investigation. The P2 items affect P&L accuracy but not bot operation.
**How to apply:** Next infra session should tackle Phase 4b dedup first (highest P&L impact per effort).
