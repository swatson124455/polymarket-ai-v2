# EB Session Handoff — 2026-05-31 (S235 continued)

**Branch:** `eb/main` (HEAD `54a49bd`)
**Master HEAD:** `e52b11b` (4 commits ahead of prior master `ca86cd8`)
**EB splinter VPS:** release `20260531_195701` (last confirmed clean deploy)
**Master VPS:** NOT YET DEPLOYED — see §3 for the pending master deploy

---

## §1 — What was done this session (complete)

### Watchdog chain (3 bugs, all proven in production)
| SHA (eb/main) | Fix |
|---|---|
| `442064d` | V1 watchdog startup race — `while True` not `while self.running` |
| `1cd5611` | V2 (primary trader) had no watchdog at all |
| `b7c4bb3` | `os._exit(1)` not `os.kill(SIGTERM)` — SIGTERM hangs on wedged pool |

**Proven:** both armed (`esportsbot_/esports_v2_scan_stall_watchdog_armed`), fire CONFIRMED (PID 372390→376101 iter 13), restart CONFIRMED (os._exit → systemd → new PID).

### EB-specific fixes (already deployed, `20260531_195701`)
| SHA (eb/main) | Fix |
|---|---|
| `061b348` | Scanner rule-6: `wait_for` on DB call removed from `esports_market_scanner.py:261/417` |
| `3937431` | `position_manager._monitor_positions`: 30s backoff on corruption signature |
| `5ceab5c` | `_SemaphoreSession.__aenter__`: discard corrupted connections instead of returning them |
| `b4e12ca` | `position_manager._monitor_positions` read: `get_raw_session()` frees semaphore slots |

### Pool size (already live on VPS)
`DB_POOL_SIZE=10, DB_MAX_OVERFLOW=2` in `/opt/pa2-shared/.env.esports` (was 14/3).

### E1 watchdog (pending EB deploy + pending master deploy)
| SHA | Branch | Fix |
|---|---|---|
| `54a49bd` | eb/main | `main.py` watchdog: stale-but-running bot past 30min → `os._exit(1)` |
| `e52b11b` | master | same (cherry-pick) |

---

## §2 — Two background tasks in flight at handoff

### Task 1: EB splinter E1 deploy (`bcbjjzbgf`)
Deploying `54a49bd` (E1 main.py) to EB splinter. If succeeded: `20260601_xxxxxx` symlink, both watchdogs re-armed, `watchdog_stale_bot_force_restart` log key available.

Verify:
```bash
KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem; H=ubuntu@18.201.216.0
ssh -i $KEY $H 'readlink /opt/polymarket-ai-v2-esports; grep -c "watchdog_stale_bot_force_restart" /opt/polymarket-ai-v2-esports/main.py'
# expect: new release, count=1
```

### Task 2: Master full test suite (`bda56r80l`)
Running `pytest tests/unit/` on master (`e52b11b`) to gate the master deploy. If clean (2959 passed, same as prior runs): deploy master immediately.

If **either task is still running when you pick this up**, check output files:
```bash
# EB splinter deploy result:
cat C:/Users/samwa/AppData/Local/Temp/claude/.../tasks/bcbjjzbgf.output | tail -5
# Master test suite result:
cat C:/Users/samwa/AppData/Local/Temp/claude/.../tasks/bda56r80l.output | tail -3
```
(Full paths in the task notification history)

---

## §3 — Master deploy: PENDING (the critical next action)

**What it ships:** fixes A+B+C+E1 to ALL services — ingestion, MB, WB, and esports.

**Why it matters for ingestion:** The ingestion service (`polymarket-ingestion`) runs from master. It has the same `ConnectionDoesNotExistError` → `Can't reconnect until invalid transaction is rolled back` cascade (observed at 00:20:36 UTC: `audit_result_store_insert_failed`, `audit_store_results_failed`). Fix B (database.py) stops that cascade for all services.

**Deploy command (from main working tree, NOT eb-main worktree):**
```bash
cd C:/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@18.201.216.0" bash deploy/deploy.sh
```

**Pre-condition:** master test suite must pass (Task 2 above). If it passed, deploy immediately. If it failed, investigate before deploying.

**Effect:** restarts all 4 services (esports, mirror, weather, ingestion) with the new fixes. All get:
- Fix A: PM 30s backoff on corruption
- Fix B: `_SemaphoreSession` discards corrupted connections
- Fix C: PM monitoring read on `get_raw_session()`
- Fix E1: main.py watchdog force-exits stale bots after 30min

**Rollback:** `bash deploy/rollback.sh` from main working tree.

---

## §4 — What's still open

| Priority | Item | Owner | Notes |
|---|---|---|---|
| **P0** | `base_bot.py:811` — UPSTREAM corruption source | MB/operator | `asyncio.wait_for(scan_and_trade(), timeout=90)` fires S162 cancellation into mid-flight asyncpg on EVERY bot, EVERY 90s+ scan. This is likely the PRIMARY pool corruption source — contention is the trigger, this is the mechanism. **Read `EB_COORDINATION_SCAN_STALL_DBLOAD.md` "CRITICAL ADDITION" section before actioning D1/D2.** NOT a hot-patch; requires audit of all DB calls in `scan_and_trade()` first. |
| **P1** | Scan-progress watchdog | EB | Watchdog fires on scan-START age (900s). Under sustained contention, scans start and timeout-cancel (refreshing start_mono) → bot cycles indefinitely → never trades, never restarts. Add a "Scan cycle done" count watchdog: if zero completions in e.g. 30min, `os._exit(1)`. Separate commit after base_bot:811 is resolved. |
| P1 | V2 matched=0 confirm | EB | After contention reduces: `journalctl \| grep esports_v2_scan_funnel \| tail -5`. If still 0 after 3+ clean scans: separate matcher-logic bug. |
| P1 | Scanner rule-6 isolated verification | EB | **Concrete harness:** write a test that simulates `asyncio.wait_for(db_coroutine(), timeout=0.001)` where `db_coroutine` is a real asyncpg call in progress, then inspect the connection state post-cancellation. Expected: `InFailedSQLTransactionError` or `cannot switch to state N`. If confirmed: propose server-side `statement_timeout` replacement (already 15s per `_SemaphoreSession`; possibly route through it). This is what S162 confirmed for direct calls; scanner's path may differ. |
| P2 | Flush-before-exit test gap | EB | 8 watchdog tests don't verify `sys.stdout.flush` called before `os._exit`. Add assertion: `mock_flush.assert_called_before(mock_exit)`. Prevents silent regression if flush is removed. |
| P2 | Fix B completeness | EB | `session.__aexit__()` may not fully dispose corrupted asyncpg connection. Deep fix: `await conn.invalidate()` after close. Self-corrects in 2 cycles. Low urgency. |
| P3 | CLOSE-WAIT leak | EB | httpx not closed. `EB_COORDINATION_CLOSE_WAIT_LEAK.md`. |
| P3 | Calibrator anomalies | EB | valorant/dota2 silent; LoL n=0. Not investigated. |

---

## §5 — Next-session §0

```bash
# ── CONFIRMED RESULTS (no need to re-check) ──────────────────────────
# os._exit restart: PROVEN empirically — PID 372390→376101 at iter 13 of restart
#   monitor (task bmjm65h6c). This is NOT composition-only. Documented in §12.
# Master deploy: DONE — release 20260531_203534; A+B+C+E1 on all services.
# Ingestion cascade stopped: 0 "Can't reconnect" / 0 "asyncpg connection corrupted"
#   at post-deploy check.

# ── FIRST 60 SECONDS ─────────────────────────────────────────────────
KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem; H=ubuntu@18.201.216.0

# 1. Read the base_bot.py:811 finding BEFORE touching D1/D2:
#    cat EB_COORDINATION_SCAN_STALL_DBLOAD.md | grep -A 50 "CRITICAL ADDITION"

# 2. Is contention eased? Check watchdog fire rate (should be lower/zero):
ssh -i $KEY $H 'journalctl -u polymarket-esports --since "today" | grep -c scan_stall_self_restart'

# 3. V2 matched > 0?
ssh -i $KEY $H 'journalctl -u polymarket-esports --since "today" | grep esports_v2_scan_funnel | tail -5'

# 4. Ingestion still clean?
ssh -i $KEY $H 'journalctl -u polymarket-ingestion --since "1 hour ago" | grep -c "Can.t reconnect"'
```

---

## §6 — Commit log (eb/main, this session)

```
54a49bd  fix(main): E1 watchdog force-restart
b4e12ca  fix(position_manager): get_raw_session monitoring read (C)
5ceab5c  fix(database): discard corrupted connections (B)
3937431  fix(position_manager): 30s backoff on corruption (A)
691278e  docs: §12
061b348  fix(esports): scanner rule-6
e0dc22a  docs: §11
b7c4bb3  fix(esports): watchdog os._exit
910174e  docs: §10
1cd5611  fix(esports): V2 watchdog
442064d  fix(esports): V1 watchdog startup race
```

---

## §7 — Scope note

All work EB-owned or master-cherry-pick. No MB code touched. EB splinter deploy path used throughout. Master commits (`57ea2f3`, `26419c8`, `49a4ef7`, `e52b11b`) are shared-module fixes benefiting all bots — cherry-picked from eb/main to master. No MB `bots/mirror_bot.py` or MB-specific env touched.

---

## §8 — FINAL STATUS (added post-session)

### All deploys completed

| Release | Path | What |
|---|---|---|
| `20260530_213432` | EB splinter | Watchdog V1+V2 |
| `20260530_221754` | EB splinter | os._exit |
| `20260530_232112` | EB splinter | Scanner rule-6 |
| `20260531_195701` | EB splinter | A+B+C+D |
| `20260531_203235` | EB splinter | E1 (main.py) |
| `20260531_203534` | **Master** | A+B+C+E1 — all services |

### Master deploy (exit code 2 = post-success shell error, NOT a deploy failure)
Symlink → `/opt/pa2-releases/20260531_203534`. Mirror/weather/ingestion all restarted (new PIDs ~10 min uptime). Code verified on VPS: fix B=1, fix A=1, E1=1 in master deployed files.

### Ingestion cascade stopped
`journalctl -u polymarket-ingestion --since "5 min ago" | grep -c "Can.t reconnect"` → **0**
`journalctl -u polymarket-ingestion --since "5 min ago" | grep -c "asyncpg connection corrupted"` → **0**

Fix B is working — corrupted connections no longer spread through the pool.

### Next session §0 (updated)
Master deploy is done. On next session:
1. Check V2 matched > 0 (after contention reduced):
   `journalctl -u polymarket-esports --since "today" | grep esports_v2_scan_funnel | tail -5`
2. Monitor for watchdog fire rate (should be lower/zero with fixes in place):
   `journalctl -u polymarket-esports --since "today" | grep -c scan_stall_self_restart`
3. Check ingestion still clean (no corruption cascade):
   `journalctl -u polymarket-ingestion --since "1 hour ago" | grep -c "Can.t reconnect"`
