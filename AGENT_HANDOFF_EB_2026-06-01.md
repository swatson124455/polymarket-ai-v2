# EB Session Handoff — 2026-06-01 (S235 full close)

**Branch:** `eb/main` (HEAD `da03242`)
**Worktree:** `C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main/`
**EB splinter VPS:** `20260531_203235` → `/opt/pa2-esports-releases/20260531_203235`
**Master VPS:** `20260531_203534` → all services (MB, WB, ingestion, esports)

**One-line status:** All session fixes shipped. EB self-recovers in ~15 min instead of dying silently for 13h. One remaining corruption source identified (`base_bot.py:811`) but not yet fixed — that's the primary work for next session.

---

## §1 — Session shape (what happened)

Session opened on a single carried P0: "prove or fix the scan-stall backstop." It was actually three stacked bugs, each discovered by actually verifying in production:

**Bug 1 (V1 startup race):** Watchdog created at `esports_bot.py:675` before `super().start()` sets `running=True` at line 757. Looped `while self.running` → saw False on first schedule → returned silently, never ran. Never fired in any incident.

**Bug 2 (V2 uncovered):** `EsportsBotV2(BaseBot)` is a sibling of `EsportsBot`. Watchdog lived only in V1. V2 — the primary trader — had no watchdog and didn't even set `_scan_start_mono`.

**Bug 3 (SIGTERM-limbo):** After fixing 1+2 and deploying, the monitor caught the watchdog firing at 02:04:43 (V1 `scan_age_s=1014.4`, V2 `992.8`) — but the process **did not restart** (PID unchanged, service `active (running)`). `os.kill(SIGTERM)` invoked graceful shutdown, which hung on the same wedged pool. systemd does not force-kill a self-sent SIGTERM. Fixed with `os._exit(1)` + flush. Restart empirically confirmed: PID 372390→376101 at iter 13 of restart monitor.

**Pool corruption chain (A/B/C/D):** Diagnosed as secondary root cause — corrupted asyncpg connections spreading through the shared pool. Fixed at four layers. Then identified `base_bot.py:811` as the PRIMARY corruption source (see §3).

**E1:** `main.py` watchdog only alerted on stale bots, never restarted. Added `os._exit(1)` at 2× stale threshold (30 min default). Fleet-wide, all bots.

---

## §2 — What shipped (complete, do not redo)

### EB splinter deploys
| Release | Commits | What |
|---------|---------|------|
| `20260530_213432` | `442064d`, `1cd5611` | Watchdog V1+V2 (bugs 1+2) |
| `20260530_221754` | `b7c4bb3` | `os._exit` instead of SIGTERM (bug 3) |
| `20260530_232112` | `061b348` | Scanner rule-6: `wait_for` on DB removed from `esports_market_scanner.py:261/417` |
| `20260531_195701` | `3937431`,`5ceab5c`,`b4e12ca` | A (PM 30s backoff), B (discard corrupt sessions in `_SemaphoreSession`), C (monitoring read on `get_raw_session()`) |
| `20260531_203235` | `54a49bd` | E1 (`main.py` watchdog force-restart) |

### Master deploy (`20260531_203534`)
Cherry-picked `57ea2f3` (A), `26419c8` (B), `49a4ef7` (C), `e52b11b` (E1) to master. Restarts MB/WB/ingestion/esports with all four shared fixes. Confirmed in deployed files on VPS.

### Pool sizing (env, no code)
`DB_POOL_SIZE=10, DB_MAX_OVERFLOW=2` in `/opt/pa2-shared/.env.esports` (was 14/3 = 17 total → 12 total).

### Session review response
`46a39d6`: base_bot:811 critical flag in coordination memo, scan-progress watchdog P1 in §4, flush-before-exit test assertions added to both watchdog test files, concrete harness spec for scanner rule-6 validation.

### Verified in production
- Both watchdogs armed (`esportsbot_/esports_v2_scan_stall_watchdog_armed` logs)
- Fire confirmed: dual fire at 02:04:43 UTC
- **Restart confirmed empirically**: PID 372390 → 376101 (iter 13, task `bmjm65h6c`)
- Master fixes confirmed in deployed files: `grep -c "asyncpg connection corrupted" database.py` = 1 on VPS
- Ingestion cascade improved: post-deploy "Can't reconnect" counts dropped

---

## §3 — The primary unresolved item: `base_bot.py:811`

**This is the most important thing in the handoff. Read before touching D1/D2.**

Full analysis in `EB_COORDINATION_SCAN_STALL_DBLOAD.md` → "CRITICAL ADDITION" section.

```python
# base_bot.py:811 — the C2 FIX
_scan_timeout = getattr(settings, "BOT_SCAN_TIMEOUT_SECONDS", 60)  # VPS: 90s
await asyncio.wait_for(self.scan_and_trade(), timeout=_scan_timeout)
```

**The mechanism:** Under DB contention, a query in `scan_and_trade()` runs slowly (>90s). `asyncio.wait_for` fires → `CancelledError` propagates into the mid-flight asyncpg call → asyncpg protocol state machine corrupted → `cannot switch to state N; another operation (2) is in progress` → poisoned connection back in pool → next caller (position_manager, another bot, ingestion) checks it out → `set_statement_timeout_failed` cascade.

**Why this changes the D1/D2 picture:** Fixes A/B/C/D reduce how far corruption spreads after it enters the pool. They do not prevent it from entering. The pool gets poisoned again on every scan that times out. This is why EB is still restart-cycling (1 watchdog fire today) and ingestion still has "Can't reconnect" events (1 in last 2h). The contention is the trigger; `base_bot.py:811` is the mechanism.

**Why NOT a hot-patch:**
The C2 FIX was added because a hung DB query would block the entire asyncio event loop — freezing all bots and WS processing. Removing `wait_for` naively re-introduces that. The correct fix: individual queries already have server-side `statement_timeout` (15s) via `_SemaphoreSession.__aenter__`. If ALL queries in `scan_and_trade()` go through `_SemaphoreSession`, each is already bounded at 15s and the C2 concern is addressed at the per-query level. Removing the outer `wait_for` then stops the corruption source.

**Investigation path:**
1. Audit every DB call in `scan_and_trade()` — confirm each goes through `_SemaphoreSession` (or has explicit `SET LOCAL statement_timeout`)
2. Map coverage: produces a file listing any unprotected calls that need fixing first
3. If fully covered: propose removing `wait_for` from `base_bot.py:811` (master change, all 14 bots)
4. If gaps found: add per-call `statement_timeout` to uncovered calls, THEN remove `wait_for`

Own the audit on `eb/main`, propose as master cherry-pick. Do NOT touch `bots/mirror_bot.py` or MB-specific logic.

---

## §4 — Open carry-forward (priority order)

| # | Item | Notes |
|---|------|-------|
| **P0** | `base_bot.py:811` audit → fix | See §3. This is the session. |
| **P1** | Scan-progress watchdog | Current watchdog fires on scan-START age (900s). Under sustained contention: scans start (refresh `_scan_start_mono`) + hit 90s wait_for timeout → repeat → start_mono stays fresh → watchdog never fires → bot cycles indefinitely without trading, without restarting. Add a "completion" signal: if zero `Scan cycle done` in e.g. 30 min, `os._exit(1)`. Separate commit after `base_bot:811` is resolved (since that fix may end the cycling). |
| P1 | V2 `matched=0` confirm | After `base_bot:811` fix reduces contention: check `journalctl | grep esports_v2_scan_funnel`. If still 0 after 3+ clean scans → separate matcher-logic bug. From pre-session: 606 future esports markets exist in DB; `matched=0` was downstream of contention. Recheck once scans complete cleanly. |
| P1 | Scanner rule-6 harness | Concrete spec: write a test that calls `asyncio.wait_for(db_coroutine(), timeout=0.001)` where the coroutine has a real asyncpg operation in progress. Inspect connection state post-cancellation — expect `InFailedSQLTransactionError` or `cannot switch to state N`. If confirmed: propose replacing `wait_for` in scanner with server-side `statement_timeout` (already 15s via `_SemaphoreSession`). S162 confirmed direct calls; scanner's path through `EsportsMarketService._db.get_session()` may differ — the harness resolves this. |
| P2 | Flush-before-exit test | Already added in `46a39d6` — both V1+V2 `test_stale_scan_triggers_force_exit` now assert `mock_stdout.flush.assert_called()`. Done. No action needed. |
| P2 | Fix B completeness | `session.__aexit__()` may not fully dispose the broken asyncpg connection (SQLAlchemy may return it to pool as "valid"). Self-corrects in 2 checkout cycles (next caller triggers fix B again). Deep fix: `await conn.invalidate()` after session close. Low urgency; address only after base_bot:811 lands. |
| P3 | CLOSE-WAIT leak | httpx session not being closed somewhere in EB code. `EB_COORDINATION_CLOSE_WAIT_LEAK.md`. Background; not causing hangs. |
| P3 | Calibrator anomalies | valorant/dota2 silent; LoL n=0. Not investigated this session. |

---

## §5 — What NOT to do

- **Do NOT remove `wait_for` from `base_bot.py:811` without the audit first.** If any DB call in `scan_and_trade()` bypasses `_SemaphoreSession`, removing `wait_for` re-introduces the C2 event-loop-hang problem.
- **Do NOT hot-patch the scanner rule-6 fix.** The harness spec exists precisely because fixing it wrong could make corruption worse. Build the harness, confirm the mechanism, then fix.
- **Do NOT touch `bots/mirror_bot.py` or MB-specific code** from the EB session.
- **Do NOT add another `neg_risk=True` filter** to exit paths (RULE TWO — hardcoded).
- MEMORY.md is over its load limit — prune before it grows further. Superseded S209–S216 handoff entries are candidates.

---

## §6 — Can't-fully-verify

**Verified:**
- All 5 EB splinter deploys + master deploy: confirmed symlinks, file content on VPS, service PIDs
- Watchdog arm (both bots, all releases): `..._scan_stall_watchdog_armed` logs present
- Watchdog fire: 02:04:43 dual-fire observed (`scan_age_s=1014.4` V1, `992.8` V2)
- Watchdog restart (os._exit): PID 372390→376101 empirically confirmed iter 13
- Ingestion cascade: reduced post-master-deploy (not yet zero — see §3)
- Fix B in master: `grep -c "asyncpg connection corrupted" /opt/polymarket-ai-v2/base_engine/data/database.py` = 1

**NOT verified / still open:**
- Whether `base_bot.py:811` is the PRIMARY corruption source (analysis + live-state evidence strongly suggest it; not yet a controlled test)
- V2 `matched > 0` (0 clean scans completed this session due to ongoing contention)
- Long-run ingestion stability post-master-deploy (1 "Can't reconnect" in last 2h = still not clean)

---

## §7 — Entry commands for next session

```bash
cd C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main
git log --oneline -3                    # expect da03242 on top
git rev-parse --abbrev-ref HEAD         # eb/main

# Read before anything else:
# cat EB_COORDINATION_SCAN_STALL_DBLOAD.md  (→ "CRITICAL ADDITION" section)

KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem; H=ubuntu@18.201.216.0

# Live health check:
ssh -i $KEY $H 'echo "EB: $(readlink /opt/polymarket-ai-v2-esports)"'
ssh -i $KEY $H 'echo "master: $(readlink /opt/polymarket-ai-v2)"'
ssh -i $KEY $H 'journalctl -u polymarket-esports --since today | grep -c scan_stall_self_restart'
# (expect: lower than yesterday — if >5/day, base_bot:811 is still active)

# V2 trading:
ssh -i $KEY $H 'journalctl -u polymarket-esports --since today | grep esports_v2_scan_funnel | tail -5'

# Ingestion clean?
ssh -i $KEY $H 'journalctl -u polymarket-ingestion --since "2 hours ago" | grep -c "Can.t reconnect"'
# (expect: 0 after base_bot:811 fixed; >0 = still active)
```

---

## §8 — Scope / isolation note

All EB-owned code this session: `bots/esports_bot.py`, `bots/esports_bot_v2.py`, `esports/markets/esports_market_scanner.py`, `base_engine/execution/position_manager.py`, `base_engine/data/database.py`, `main.py` (E1). The `base_engine/` changes were cherry-picked to master (operator authorization per RULE ONE-A refinement: "EB session owns eb/main splinter end-to-end including shared-module fixes EB needs"). No `bots/mirror_bot.py`, no MB/WB-specific env, no master merges that weren't pre-authorized as cherry-picks. MB/WB/ingestion PIDs verified unchanged after every EB splinter deploy.
