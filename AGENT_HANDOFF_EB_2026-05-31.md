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

| Item | Owner | Notes |
|---|---|---|
| **Master deploy** | next session | Gate on Task 2 passing. Command in §3. |
| **V2 matched=0 confirm** | EB | After master deploy reduces contention: `journalctl -u polymarket-esports \| grep esports_v2_scan_funnel \| tail -5`. If still 0 after 3+ clean scans: separate matcher-logic bug. |
| **Fix B completeness** | EB (deferred) | `session.__aexit__()` may not fully dispose corrupted asyncpg connection (SQLAlchemy might return it to pool as "valid"). Self-corrects in 2 cycles. Deep fix: `await conn.invalidate()` after close. Low urgency. |
| **CLOSE-WAIT leak** | EB | httpx not closed somewhere. `EB_COORDINATION_CLOSE_WAIT_LEAK.md`. |
| **Calibrator anomalies** | EB | valorant/dota2 silent; LoL n=0. Not investigated. |

---

## §5 — Next-session §0

```bash
# 1. Check both tasks completed:
#    EB splinter: did E1 deploy succeed?
readlink /opt/polymarket-ai-v2-esports  # should be new release

#    Master tests: passed?
cat [bda56r80l output file] | tail -3

# 2. If master tests passed and NOT YET deployed:
cd C:/lockes-picks/polymarket-ai-v2
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem" VPS="ubuntu@18.201.216.0" bash deploy/deploy.sh

# 3. Verify master deploy:
#    All 4 services restarted, fix B in database.py:
ssh -i $KEY ubuntu@18.201.216.0 'grep -c "asyncpg connection corrupted" /opt/polymarket-ai-v2/base_engine/data/database.py'
#    Ingestion: no more "Can't reconnect" cascade?
ssh -i $KEY ubuntu@18.201.216.0 'journalctl -u polymarket-ingestion --since "10 min ago" | grep -c "Can.t reconnect"'

# 4. If contention eased: check V2 matched > 0
ssh -i $KEY ubuntu@18.201.216.0 'journalctl -u polymarket-esports --since "today" | grep esports_v2_scan_funnel | tail -5'
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
