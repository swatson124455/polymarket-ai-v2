# DB Connection Leak — Stop + Guardrail Plan (EB + WB review)

**Date:** 2026-06-05. **Author:** MB session (infra source-of-truth). **Status:** PLAN / PROPOSAL — read-only review done; no code changed. Each fix is its own change-sequence + sign-off. Cross-bot shared-module fixes are **MB-priority/MB-owned** (RULE ONE/THREE); splinter-only items are EB/WB-owned.

## THE REFRAME (most important takeaway)
The connection leak is **NOT esports-specific code** — it's a **SHARED-code feedback loop** that esports merely triggers hardest. So the **durable fix is MB-owned shared code**, not "wait for EB." Esports is the worst victim *and* the worst trigger, but weather (and MB) suffer the same shared pool.

## Verified root cause — recovery→init feedback loop (self-amplifying)
1. `base_engine/base_engine.py:1670-1675` starts `recovery.monitor_and_recover(interval=60)` in every service.
2. Every 60s it runs `health_monitor.check_all_services()`; any `unhealthy` component → `attempt_recovery("database")` → `recovery.py:102-133 _recover_database` → **`await self.db.init()`** (full engine re-create).
3. **Over-sensitive trigger:** `health_monitor._check_database` (`health_monitor.py:138-180`) wraps the probe in `asyncio.timeout`. Under PgBouncer saturation, `_SemaphoreSession.__aenter__` blocks up to 15s on the semaphore/pool → probe times out → **UNHEALTHY** → recovery → `db.init()`. **Loop:** pool pressure → "unhealthy" → re-init → leaked pool → more pressure.
4. **Three leak gaps in `Database.init()` (`database.py:1102-1140`):**
   - **GAP 2 (dominant under saturation):** the init-*failure* path (`:1136-1140`) sets `self.engine=None` **without `dispose()`** — every re-init whose `SELECT 1` verify fails leaks its half-open pool. esports hits this constantly.
   - **GAP 1:** dispose is gated on `current_loop_id == self._engine_loop_id` (`:1111-1112`); a cross-loop re-init drops the engine without disposing.
   - **GAP 3:** `_pool_health_task` (`:1294`) is re-`create_task`'d each init but never cancelled before overwrite (only `close()` cancels) → orphan task pins the old engine, defeating GC.
5. **Esports amplifiers (EB-owned, secondary):** errors more (DB contention) → more probe timeouts → more recovery; the stall watchdog `esports_bot.py:793-867` `os._exit(1)` → systemd restart → another `init()`. **Live: 48 recovery events / 25 min** post-restart.

## Live state (verified 2026-06-05)
| Service | Conns | Engine leak? | Has Option A/WI-21a? | Notes |
|---|---|---|---|---|
| esports (eb/main) | was 50 → 4 (just restarted) | **YES (recovery→init)** | **NO (deployed release older than worktree 2738183)** | 48 recovery events/25min; restart-loop |
| weather (wb/main) | 11 / 15-pool | **NO — clean** | NO (`grep`=0 both) | victim of shared exhaustion; pool right-sized (30→15 landed); entrypoint-drift blocker |
| mirror/ingestion (master) | 9 / 6 | no | YES (deployed `20260605_160212`) | healthy |

## WeatherBot = victim, not leaker (confirmed)
- Idempotent leak-safe `init()`; **zero** fire-and-forget DB-session `create_task`; pooled/reused httpx; sessions tightly scoped. No engine leak.
- Its 18.5h-dark stalls = kill-switch read rides the **semaphore-bounded** `get_session()`; when the shared pool is saturated (by esports) it blocks 15s > the 10s `wait_for` → **fail-closed → skip scan**. Defense gap: kill-switch read should use `get_raw_session()` (bypass semaphore) like `engage` does.
- **Entrypoint drift (S239):** `weather_bot.py` imports the silo (`bots.weather.engine.*`) but the live `ExecStart=…main.py` runs the **top-level** engine → silo-targeted fixes risk being dead code. Resolve with `systemctl show polymarket-weather -p ExecStart --value` before any silo port.

---

## PLAN

### A. STOP THE LEAK — MB-owned shared code (highest leverage; cross-bot verify all 14 bots)
> **⚠ SUPERSEDED IN PART (2026-06-08):** Option-B pinning refuted the A1 "init() dispose-leak" mechanism (same-loop dispose already fires; live watch showed OS conns ≤ pool-max, tracking the pool). **Scope reduced to A2 + A1-GAP-3 only; A1 GAP 1/2 DEFERRED** unless OS conns exceed pool-max post-A2 during a saturation window. See the CORRECTION block in `AGENT_PLAN_A1A2_INIT_LEAK_SAFETY_2026-06-05.md`. Also noted there: ingestion doesn't run RecoveryProcedure (A2 no-op for it); eb/main carries a divergent re-probe fix `ff5b9d4` needing deliberate merge reconciliation; WB silo needs the port before `weather_main.py` activation.
**A1. Make `Database.init()` re-init leak-safe (`database.py`).** The durable fix — caps the leak for *every* service regardless of trigger.
- Failure path (`:1136-1140`): `await self.engine.dispose()` (best-effort) before nulling. *(GAP 2 — dominant.)*
- Re-init path (`:1108-1117`): always dispose-before-recreate; cancel `self._pool_health_task` before overwrite (GAP 3); handle cross-loop dispose (schedule on owning loop or restrict re-init to same loop) (GAP 1).
- Idempotent: no-op when a healthy engine already exists.
- Guard: `test_handle_error_invalidation.py` + full `tests/unit` must stay green; add an init-instance/dispose test.

**A2. De-amplify the recovery→init trigger (`recovery.py` + `health_monitor.py`).** Stops the storm at the source.
- `_recover_database` (`recovery.py:102-133`): don't `db.init()` on a transient probe timeout; gate re-init behind `session_factory is None`/engine-disposed; otherwise retry `SELECT 1` (rely on `pool_pre_ping`).
- `_check_database` (`health_monitor.py:138-180`): classify semaphore/pool-acquire timeout as **DEGRADED** ("pool busy"), reserve **UNHEALTHY** for genuine connectivity loss. *(Behavior change — enumerate callers keyed on `unhealthy`.)*

### B. STOP THE AMPLIFIERS — EB-owned splinter (secondary)
- **B1.** esports stall watchdog (`esports_bot.py:793-867`) restart-loop multiplies inits — make less trigger-happy / let A1+A2 resolve the underlying wedge first.
- **B2.** esports fire-and-forget DB-session `create_task` (`esports_bot.py:1415/1427/1448`) — await or guard.
- **B3.** Residual per-call httpx CLOSE-WAIT churn (`EB_COORDINATION_CLOSE_WAIT_LEAK.md`) — socket pressure, separate from DB pool.

### C. RESILIENCE — so victims survive pressure while A lands
- **C1. Redeploy eb/main `2738183`** → brings Option A + WI-21a to esports (does NOT fix the leak, but lets esports tolerate pressure + self-clean poisoned conns). EB-deploy.
- **C2. Port Option A + WI-21a to wb/main** (weather resilience + self-cleanup). WB — *gated on resolving the C3 entrypoint drift first, else dead code.*
- **C3. Resolve weather entrypoint drift (S239)** — point ExecStart at the intended engine; verify live `ExecStart`. WB.
- **C4. Kill-switch read via `get_raw_session()`** so it survives semaphore saturation (shared base_bot/kill_switch; helps all bots). MB.

### D. GUARDRAILS — detect + prevent recurrence (extends WI-18/19/20)
- **D1. Leak detection = WI-19 made specific:** alert when a process's live PgBouncer connection count **> its configured `pool_size+max_overflow`** (impossible for one pool → multiple engines = leak). Would have caught esports 50-vs-18 instantly.
- **D2. Engine-instance accounting (NEW):** assert/log live SQLAlchemy engine count per process == 1; >1 ⇒ leak alarm. Directly catches the re-init leak class.
- **D3. Recovery-reinit counter (NEW):** alert if `_recover_database` re-inits > N/hour (the feedback-loop signature; live = 48/25min would have screamed).
- **D4. WI-18 budget enforcement:** Σ(per-service pool) ≤ PgBouncer `default_pool_size+reserve`; deploy.sh assertion.
- **D5. WI-20 pool-pressure terminal escalation:** chronic pressure → CRITICAL (not perpetual noise).

### E. SHARED-INFRA LEVER — relieve the 65 ceiling for everyone (MB-decided, sign-off)
- **E1. PgBouncer transaction mode** (now shown tractable — NOTIFY is dead code; only `database_lock.py` session-locks + per-session SET need handling) → multiplies the 65 into serving far more. OR per-service DB users + PgBouncer per-user caps. Design + sign-off required.

## Recommended sequence
1. **A1 + A2** (MB shared code) — the real fix; stops the leak at the source for all services. *Biggest lever; MB's to do.*
2. **D1/D2/D3** (MB) — leak detection so any residual/future leak surfaces in minutes, not sessions.
3. **C1** (EB redeploy 2738183) + **B1** — esports resilience + de-amplify, in parallel.
4. **C2/C3/C4** (WB + MB) — weather resilience + drift.
5. **D4/D5** then **E1** — budget enforcement, escalation, then the structural ceiling relief.

## Ownership / authorization
- A, C4, D1-D5: **MB shared-code/infra** — MB proposes + implements with sign-off + full cross-bot verify.
- B, C1, C2, C3: **EB/WB splinters** — coordination docs (this + `EB_COORDINATION_ESPORTS_DB_ENGINE_LEAK.md`); EB/WB execute.
- E1: shared runtime infra — explicit per-action operator sign-off (RULE THREE).
