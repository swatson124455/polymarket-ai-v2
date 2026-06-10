# PLAN — A1+A2: shared `Database.init()` leak-safety + recovery de-amplification

**Date:** 2026-06-05. **Author:** MB session. **Status:** PROPOSAL — awaiting operator sign-off. **No code changed.** Highest-blast-radius change in this arc (shared engine init + recovery, all 14 bots). Independent review required at implementation (NOT inline-substitutable per the 2026-06-05 review-availability rule).

> ## ⚠ CORRECTION (2026-06-08) — central mechanism REFUTED; scope reduced to A2 + GAP-3
> Option-B mechanism-pinning (code inspection + a 14-min live esports watch) **refuted the "init() dispose-leak" this doc was built on:**
> - **Code:** `init()` already disposes the old engine when same-loop (`database.py:1111-1112`), and recovery runs in the single process loop — so dispose **does** fire. The "GAP 2 dominant / re-init nulls without dispose" claim below is WRONG for the common path (the no-dispose outer-`except` rarely fires).
> - **Watch (2026-06-08 13:20–13:34):** esports OS conns **never exceeded the 18 pool-max** and **tracked the current-engine pool at every tick** → **no lingering disposed-engine connections** in the observed state. The earlier "50 conns" was a longer-lived pid mid-saturation; restarts (~every 20 min) cap accumulation.
> - **Pinned mechanism:** the leak is the **TAIL of the recovery→re-init feedback storm** (recovery ~40 events/50min, confirmed), NOT a standalone `init()` bug.
>
> **Decision (operator-approved 2026-06-08): A2-only + A1-GAP-3. A1 GAP 1/2 (init dispose-restructuring) DEFERRED** — evidence does not justify touching shared `init()`.
> - **A2** (`recovery.py:_recover_database`): probe a present engine WITHOUT re-init; success or transient `DatabaseError`/pool-timeout → success (no re-init, no failure-count); only `session_factory is None` OR a genuine connection error → re-init. Breaks the storm → no churn → conns stay = pool; also stops the `sys.exit(1)` restart loop. `health_monitor` UNTOUCHED (its false-UNHEALTHY is now harmless — recovery no-ops on busy).
> - **A1-GAP-3** (`database.py`): cancel the orphaned `_pool_health_task` on re-init (cheap task-leak hygiene). NOT the dispose-restructuring.
> - **Post-deploy verification is EMPIRICAL, through a SATURATION window:** per-process OS conns stay ≤ pool-max under load + restart frequency drops to ~zero. **If OS conns exceed pool-max post-A2 during saturation, THEN A1's dispose path matters — implement GAP 1/2 then, with evidence.**
>
> The "Verified leak mechanism" + A1 GAP-1/2 sections below are **SUPERSEDED** by this correction (retained for audit trail).

## Goal
Stop the DB-engine leak **at the source, in shared code**, so it's fixed for every service that uses shared `base_engine` (MB + ingestion immediately; esports/weather on their next merge+redeploy). Stops both (a) the re-init leak loop and (b) the recovery-driven `sys.exit(1)` restart loop.

## Verified leak mechanism (file:line, master)
- **The loop:** `recovery.monitor_and_recover` (60s, `recovery.py:185-209`) → on any `"unhealthy"` component → `attempt_recovery` → `_recover_database` (`recovery.py:102-133`) → **`await self.db.init()`** (full engine re-create). The probe `health_monitor._check_database` (`health_monitor.py:138-187`) returns **UNHEALTHY on `asyncio.TimeoutError`** — and under pool saturation `get_session()` blocks ~15s on the semaphore > `check_timeout` → false-UNHEALTHY → recovery fires. Self-amplifying. After 3 consecutive failed recoveries → **`sys.exit(1)`** (`recovery.py:197-203`) → restart loop.
- **The 3 leak gaps in `Database.init()` (`database.py:1102-1140`):**
  - **GAP 2 (dominant under saturation):** failure path `:1136-1140` nulls `self.engine`/`session_factory` **without `dispose()`**. Every re-init whose `_verify_database` SELECT-1 fails leaks the just-created pool.
  - **GAP 1:** re-init `:1108-1117` disposes only when `current_loop_id == self._engine_loop_id`; a cross-loop call drops the engine without disposing.
  - **GAP 3:** `_init_postgres` (`:1294`) re-`create_task`s `_pool_health_task` every init; `init()` never cancels the prior one (only `close()` does) → orphan task pins the old engine, defeating GC.

## "leak-safe ≠ never-replace" — the design answer
The fix is **dispose-before-create on ALL paths**, NOT "never create a new engine." Recovery (and `prediction_engine.py:772`) legitimately need to replace a dead engine; that stays allowed — it just disposes the old one first, so no leak. Callers of `db.init()` that must keep working: `BaseEngine.init()` (startup — no engine exists yet → creates), `recovery._recover_database` (replace dead engine), `prediction_engine.py:772` (session_factory-None re-init), standalone scripts (create once). All are satisfied by dispose-then-create.

## A1 — leak-safe `Database.init()` (`base_engine/data/database.py`)
1. **Failure-path dispose (GAP 2 — highest impact):** in the `except` at `:1136-1140`, `await self.engine.dispose()` (best-effort, guarded) before nulling. Never null a created engine without disposing it.
2. **Cancel `_pool_health_task` before re-create (GAP 3):** in `init()` (`:1108-1117`), cancel `self._pool_health_task` before dropping the engine (mirror what `close()` does at `:1330-1336`). No orphan task pinning the old engine.
3. **Cross-loop dispose (GAP 1):** prefer same-loop `await dispose()`. If `current_loop_id != self._engine_loop_id`, do not silently drop — at minimum cancel the pool-health task and best-effort close; document why awaiting cross-loop dispose is unsafe. (Rarest gap; dominant leak is GAP 2/3 which are same-loop.)
- **Optional idempotent guard:** early-return no-op when a *healthy* engine already exists. DEFERRED unless cheap+safe — "healthy" is exactly what recovery is unsure of; a wrong no-op could skip a needed replacement. The dispose-before-create fix makes init leak-safe without needing this. Decide at implementation.

## A2 — recovery de-amplification (stops the trigger; fixes the restart loop too)
**Primary (recovery layer — low risk, no enum change):** `_recover_database` (`recovery.py:102-133`) should NOT `db.init()` when `session_factory is not None`. If an engine exists, re-verify with a fresh `SELECT 1` (rely on `pool_pre_ping` to recycle dead conns); success ⇒ return success (engine was just busy — no re-init, and recovery doesn't count a failure → no `sys.exit`). Only `db.init()` when `session_factory is None` (engine genuinely gone) or the verify fails for a real connection error (not a pool-busy timeout).
**Secondary (probe layer — optional):** `health_monitor._check_database` (`health_monitor.py:138-187`) classify a semaphore/pool-acquire timeout as **DEGRADED** ("pool busy"), reserve **UNHEALTHY** for `session_factory is None` / genuine connection errors. CAVEAT: requires distinguishing pool-busy timeout from DB-down timeout (a real outage also times out) — and confirming `HealthStatus.DEGRADED` exists / that recovery ignores DEGRADED. If non-trivial, ship the recovery-layer gate alone; it's sufficient.
**Behavior-change note (Rule 4):** recovery will no longer re-init or count a failure for a live-but-busy engine. Callers keyed on `"unhealthy"`: only `monitor_and_recover` acts on it (→ recovery + sys.exit). The change makes both fire only on genuine DB-down, not back-pressure. Enumerate any other `check_all_services` consumers during implementation.

## Blast radius
Shared `base_engine` init + recovery + health-monitor → **all 14 bots** (every service's DB lifecycle). This is why independent review + full cross-bot verification are mandatory.

## shared-vs-vendored → operator elevation (updated)
`base_engine` is **vendored per branch** (deploy tars the branch worktree). So:
- **MB + ingestion:** fixed on the MB master deploy.
- **esports (eb/main):** fixed when EB **merges master + redeploys** the esports splinter (eb/main already merge-tracks master at 2738183 — clean merge, mechanical). This is how A1+A2 reaches the leak source **without MB editing EB code** — but it still requires EB to merge+redeploy (not automatic).
- **weather (wb/main):** same merge+redeploy; lands in wb/main's **top-level** `base_engine` (which the live `main.py` uses — S239); resilience only (weather doesn't leak).

## Test plan (recovery-path coverage is the new requirement)
- New tests: (1) init() disposes the old engine before creating a new one (assert `dispose()` called on re-init); (2) failure-path disposes (simulate `_verify_database` raise → assert dispose, no orphaned engine); (3) `_pool_health_task` cancelled on re-init (no orphan); (4) `_recover_database` does NOT call `db.init()` when `session_factory is not None` and SELECT-1 succeeds (the de-amplification); (5) startup init still creates an engine when none exists.
- Full `tests/unit/` green + existing `test_handle_error_invalidation.py` / kill-switch tests intact. Cross-bot: each bot's DB lifecycle unaffected.

## Change sequence (per CLAUDE.md + the recorded review-availability rule)
1. ✅ Scoping diligence (this doc).
2. **Operator sign-off on this plan** ← we are here.
3. Implement, one fix per commit (A1 and A2 as separate commits).
4. **Independent adversarial + cross-bot review** (Workflow). This change is NOT small/pure → if the subagent API is overloaded, **WAIT** for independent review; do NOT inline-substitute.
5. Full `tests/unit/` gate + the new recovery-path tests.
6. Deploy on explicit operator word (MB channel).
7. Post-deploy verify: **engine-init frequency drops on MB + ingestion immediately** (journalctl "Initializing PostgreSQL database" rate → ~startup-only); recovery `sys.exit` restarts stop; re-run `ss -tnp` per-process conns. esports verification follows EB's merge+redeploy.

## Risks + rollback
- **Risk:** recovery no longer re-inits a truly-wedged-but-session_factory-present engine. Mitigation: the SELECT-1 re-verify still catches a genuinely dead engine (fails → re-init). Net strictly safer than today (today it leaks on every transient).
- **Risk:** cross-loop dispose (GAP 1) — handle conservatively; it's the rarest path.
- **Rollback:** `git revert <A1 sha> <A2 sha>` + `bash deploy/rollback.sh`.
