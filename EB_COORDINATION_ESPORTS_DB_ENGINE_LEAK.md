# EB COORDINATION — EsportsBot is leaking DB engines into the shared PgBouncer pool

**Filed:** 2026-06-04 by MB session (MB = shared-infra source-of-truth). **Severity: HIGH.** **Owner: EB** (trigger is eb/main code) + **MB** (shared-code hardening). **Read-only diagnosis — nothing changed.**

## One-line
EsportsBot holds **50 of 80** total app→PgBouncer connections (63%) against a configured pool max of **18**, because its process **re-initializes the DB engine ~every 85s without disposing the old one**. The leaked engines pin Postgres backends in PgBouncer's **session-mode** pool, exhausting the shared 65-connection ceiling and starving MirrorBot / WeatherBot / ingestion → MB's "Kill switch check timed out" / scan-stall family.

## Verified evidence (live VPS, 2026-06-04 — all read-only)
| Fact | Value | Source |
|---|---|---|
| esports connections to PgBouncer:6432 | **50** | `ss -tnp dport=:6432`, pid 654003 → `/opt/polymarket-ai-v2-esports/venv` |
| mirror / weather / ingestion / orderbook | 10 / 10 / 8 / 2 | same |
| Total app→PgBouncer clients | **80** | `ss` |
| PgBouncer server ceiling | **65** (`default_pool_size=60` + `reserve_pool_size=5`, `pool_mode=session`) | `/etc/pgbouncer/pgbouncer.ini` |
| esports configured SQLAlchemy pool | **18** (`pool_size=14, total=18`) | journalctl "DB semaphore initialized" |
| esports engine re-init cadence | **new engine every ~75–90s, same pid 654003** (15:00:06 → 15:01:30 → 15:06:10 → 15:07:24) | journalctl "DB semaphore initialized" |
| Cumulative engine inits (journal) | **esports 2280** vs mirror 132 / weather 136 / ingestion 83 (**~17× outlier**) | journalctl `grep -c "Initializing PostgreSQL database"` |
| esports restarts (NRestarts) | **42** (mirror 22, weather 0) | `systemctl show` |

## Mechanism (verified) + trigger (for EB to pin)
- **Mechanism — verified:** 50 held connections is impossible for a single 18-max SQLAlchemy pool. The journal shows the same pid creating fresh engines (`create_async_engine` → "DB semaphore initialized") repeatedly. So the esports process **stands up new engines/pools without releasing the prior ones** → connections accumulate in PgBouncer (session mode pins them until the client disconnects/GCs).
- **Ruled OUT as the trigger (shared schedulers, reuse the engine — verified):** `health_scheduler._run_health_check` (60s) calls an injected `health_monitor.check_all_services()`, no new engine (`health_scheduler.py:82-88`); `ingestion_scheduler` health check is 60-min and passes the existing `db` (`ingestion_scheduler.py:250-258`).
- **Leads for EB to trace (in eb/main, the running esports code — NOT master):**
  1. **Error-driven recovery re-init.** `base_engine/monitoring/recovery.py:109` `_recover_database()` calls `self.db.init()` to "reinitialize" on DB failure. If esports' instability (42 restarts, the documented scan-stall/DB-corruption chain) fires recovery on a loop (`recovery.py:191 attempt_recovery`), each call may re-create the engine. Confirm whether `Database.init()` disposes the prior engine before creating a new one — if not, every recovery leaks a pool.
  2. **eb/main divergence.** The master working tree shows no per-cycle engine creation in esports code, so the per-85s re-init is either (1) above or a code path that exists/behaves differently on `eb/main`. Grep the eb/main splinter for `Database(`, `.initialize(`, `.init(`, `create_async_engine` on any scan/loop/error path.
  3. **`Database.init()` / `initialize()` idempotency.** Verify it guards against re-creating an existing live engine and disposes the old one. (`base_engine/data/database.py` initialize() has dispose() calls at :1088/:1160 — confirm they actually run on re-init rather than being skipped.)

## What does NOT fix this
- **Right-sizing `.env.esports` pool down** — won't help; this is a *leak*, not an over-sized pool. esports already holds 50 against an 18 config.
- **Raising PgBouncer `default_pool_size`** — already tried 60→80 on 2026-06-03, reverted (re-saturated in session mode). A leak refills any ceiling.

## Asks
1. **EB:** trace the per-~85s engine re-init in the eb/main esports process (leads above) and stop it (reuse a single engine / dispose-before-reinit). This is the dominant consumer of the shared pool.
2. **MB (shared code, coordinated):** harden the shared re-init paths so ANY caller is leak-safe — make `Database.init()/initialize()` dispose the existing engine before re-creating (or no-op if healthy), and make `health_runner.run_health_check(db=None)` (`health_runner.py:620-626`, creates a `Database()`+`initialize()` and never disposes) reuse an injected engine. MB owns this; EB does not touch shared runtime infra (RULE THREE).
3. **Sequencing:** EB-side trigger fix is the high-impact item; MB-side hardening is the safety net. Neither touches PgBouncer/Postgres config.

## Why this matters beyond esports
With esports behaving (≈10–18 like the others), total demand would be ~30–48 vs the 65 ceiling — comfortable headroom, and the cross-bot "pool-pressure → kill-switch / scan-stall" family (MB kill-switch timeouts, WB scan-loop stall, EB scan-stall chain) likely collapses. This single leak is the most probable common driver. See `DB_VPS_WORKLOAD_RESEARCH_2026-06-04.md` for the full system picture.

---

# ADDENDUM — EB diligence verdict (2026-06-08): trigger re-rooted; fix is shared-module; operator chose DIAGNOSIS-ONLY

**Filed by EB session 2026-06-08 (eb/main).** Read-only diligence per the standing "re-verify, don't trust" rule. **No code changed this session.** Operator decision on the fix: **deferred (diagnosis-only)** — this section is the decision input.

## A. The original hypothesis is partially WRONG — verified
The "per-85s engine re-init" is **NOT** a scan-loop/scheduler constructing a fresh engine each cycle. Verified on eb/main:
- Engine is constructed **once at startup** — `base_engine.py:442` (`await self.db.init()`), reused thereafter.
- **No per-cycle engine construction** exists in EB code: grepped `bots/esports_bot.py`, `bots/esports_live.py`, `esports_v2/` (only `esports_v2/scripts/shadow_report.py`, a standalone script, calls `create_async_engine`), and `prediction_engine.py:772` `db.init()` is a one-time startup-race guard in `_background_train`, not periodic.
- No EB-only commit (the 53 eb/main deltas) touched engine/recovery/health construction.

**Lead #1 in the original diagnosis was the correct one** (`recovery.py`).

## B. Verified root chain (file:line)
1. EB scan-path DB pressure saturates the pool → shared health check `_check_database()` wraps `get_session()`+`SELECT 1` in `asyncio.timeout(check_timeout_seconds)` (`health_monitor.py:160`) → times out → returns **UNHEALTHY** (`health_monitor.py:174-180`). *(This `asyncio.timeout`-around-`get_session` is itself a WI-21 corruption site — injected `CancelledError` poisons a connection on each timeout.)*
2. Shared recovery loop `monitor_and_recover(interval_seconds=60)` (`base_engine.py:1672` → `recovery.py:175`) sees UNHEALTHY → `attempt_recovery("database")` → `_recover_database()` → **`self.db.init()`** (`recovery.py:109`) = full engine teardown+rebuild.
3. `Database.init()` attempts `dispose()` of the old engine (`database.py:1112`); on poisoned/checked-out connections `dispose()` raises → caught at `database.py:1113` → **old engine orphaned undisposed** at `database.py:1115` → its connections pin in **session-mode** PgBouncer → accumulate (~50). *(Terminal mechanism is `database.py` — assigned to **MB** in the original Asks §2.)*

## C. Empirical proof (live VPS, 2026-06-08, read-only — infra-state)
Journal shows the exact chain repeating: `Attempting recovery for database: Database query timeout` → `Initializing PostgreSQL database` → `DB semaphore initialized`.
| Metric (last 6h, `journalctl -u polymarket-esports`) | Count |
|---|---|
| `Attempting recovery for database` (all "Database query timeout") | 62 |
| health-check UNHEALTHY (`Database query timeout` / `check failed`) | 62 |
| `Initializing PostgreSQL database` (engine re-init) | 78 |
- Δ(78−62)=16 ≈ startup inits across process restarts (PIDs cycled 870387→871309→872163→873495 in the 3h sample).
- Cadence is **irregular/event-driven** (e.g. 11:32, 11:36, 11:48, 11:51, 11:55, 12:11…), consistent with health-triggered recovery — NOT a fixed per-cycle timer.

## D. WI-21a + Option A are now LIVE in eb/main code (via merge `2738183`, 2026-06-08) — NOT yet deployed
The eb/main↔master reconciliation merge ported both MB fixes onto eb/main; verified correct & complete:
- **WI-21a**: `_handle_error_should_invalidate()` (`database.py:163`) — preserves original sigs + adds `"cannot switch to state"`/`"another operation"`; wired at `database.py:1271`.
- **Option A**: scan-loop kill-switch cached fallback (`base_bot.py:762-813`; `kill_switch.py:65/82`), `kill_switch_cache_fallback` log emitted.
- These are **in the branch but the splinter has NOT been redeployed**, so the *running* esports process does not have them yet. A deploy is needed to activate them (separate decision; MB-deploy-priority gate applies).

## E. Fix options (all shared `base_engine/**`; splinter-isolated; master cherry-pick = MB sign-off)
| # | Change | File | Stops leak? | MB risk |
|---|--------|------|-------------|---------|
| 1 | `_recover_database()`: probe existing engine first; only `db.init()` if genuinely dead — don't rebuild a live engine on a transient timeout | `recovery.py` | Yes, at the trigger | Low (only the recovery path changes) |
| 2 | Require N consecutive UNHEALTHY before DB re-init | `recovery.py` | Mostly | Low |
| 3 | Health probe: transient timeout → DEGRADED not UNHEALTHY + drop the `asyncio.timeout`-around-`get_session` (kills a WI-21 site) | `health_monitor.py` | Yes (removes false trigger) | Low–med (changes health semantics for all bots) |
| 4 | Hand entire fix to MB | — | — | None for EB |

## F. Recommendation + ownership split
- **EB lane (the trigger):** Option 1 in `recovery.py` (deploy to esports splinter only; propose cherry-pick to master).
- **MB lane (the leak mechanism + health semantics):** `database.py` dispose-on-reinit hardening (orphan-without-dispose at `:1115` when `dispose()` raises) per original Asks §2; Option 3 health-probe change if pursued.
- Neither touches PgBouncer/Postgres config (RULE THREE intact).

## G. Status (superseded — see H)
No code changed. Full suite green (3765 passed post-merge); EB scan/prediction-log tests 34 passed. Awaiting operator/MB decision on which option to implement.

---

# H. FIXED + DEPLOYED (2026-06-08) — Option 1 shipped to esports splinter

Operator reversed diagnosis-only → "fix then deploy". **Option 1 implemented + deployed.**

**Fix:** `recovery.py` `_recover_database()` now re-PROBES a live engine (one `SELECT 1`) and returns success WITHOUT rebuilding when it passes; full `db.init()` only on probe-failure or no engine. Commit `ff5b9d4` (eb/main) + 4 regression tests (`tests/unit/test_recovery_db_reprobe.py`). Full suite **3769 passed**.

**Deploy:** EB splinter release `20260608_090017` (esports-only restart; MB/WB/ingestion untouched, verified; PgBouncer left at 60 — RULE THREE intact).

**Post-deploy verification (live, infra-state):**
| Signal | Before | After |
|---|---|---|
| esports→:6432 conns (`sudo ss`, pid 876002) | ~50 | **16** (bounded to 18 cap) |
| total app→PgBouncer python clients | 80 | **42** (< 65 ceiling) |
| engine re-inits since start (~9 min) | ~13/hr | **1** (startup only) |
| recovery on health-timeout | rebuild→leak | **"skipped engine re-init"** (re-probe success) |
| NRestarts | 167 | **0** |
| paper-mode | — | `simulation_mode=True` ✓ |

Live chain confirmed: `Attempting recovery for database: Database query timeout` → `Recovery successful for database: Existing DB engine healthy on re-probe — skipped engine re-init`.

**Remaining (separate from the cross-bot leak; NOT regressions):**
1. esports still saturates its OWN 18-pool (`db_pool_health ... semaphore_available=0`; health intermittently "unhealthy"). Bounded now (no leak). Likely esports workload vs an 18-pool — candidate for the WI-21 scan-path `wait_for` removals (base_bot.py:853 etc.) and/or pool right-size within the shared budget.
2. The re-probe shares the saturated pool; if it ever fails to get a slot it falls through to a rebuild (the leak path). Observed probes succeed; "re-inits stays ~1" is a ~9-min window.

**MB coordination:** esports' shared-pool footprint dropped ~50→16 and total clients 80→42, so the cross-bot "pool-pressure → kill-switch timeout" family should relieve. **MB: please confirm via your SHOW POOLS before/after baseline and watch `kill_switch_cache_fallback` frequency — it should drop.** Also confirm over a multi-hour window that esports "Initializing PostgreSQL database" stays ~1/process (re-probe not falling through to rebuild under sustained saturation). `database.py` dispose-on-reinit hardening (Asks §2, MB-owned) remains the defense-in-depth net.
