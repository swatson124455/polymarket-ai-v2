# EB COORDINATION — EsportsBot is leaking DB engines into the shared PgBouncer pool

**Filed:** 2026-06-04 by MB session (MB = shared-infra source-of-truth). **Severity: HIGH.** **Owner: EB** (trigger is eb/main code) + **MB** (shared-code hardening). **Read-only diagnosis — nothing changed.**

> **UPDATE 2026-06-05 (root cause refined + LOAD-BEARING reframe).** The "~85s engine re-init" is now root-caused as a **shared-code recovery→init feedback loop**: `recovery.monitor_and_recover` (60s) → `_recover_database` → `db.init()`, fired by an **over-sensitive health probe** that flags UNHEALTHY when the probe times out on a saturated pool; + 3 leak gaps in `Database.init()` (dominant: the init-FAILURE path nulls the engine without `dispose()`). Live: **48 recovery events / 25 min** on esports. Full root cause + stop+guardrail plan: **`DB_LEAK_STOP_AND_GUARDRAIL_PLAN_2026-06-05.md` (authoritative).** Two facts make the WI-21a port LOAD-BEARING, not housekeeping:
> 1. **Deployed esports LACKS Option A + WI-21a** (`grep`=0 in `/opt/polymarket-ai-v2-esports/`), though eb/main worktree `2738183` merged them. So the bot GENERATING the corruption has neither the resilience nor the proactive corruption-eviction (WI-21a). **EB action: redeploy eb/main `2738183`** → brings WI-21a (esports is the corruption SOURCE → eviction matters most here) + Option A. ⚠ This does NOT fix the leak (the recovery→init loop is in `2738183` too) — it adds resilience + self-cleanup while the ROOT fix lands.
> 2. The **ROOT leak fix is MB-owned shared code** (A1 `init()` leak-safety + A2 recovery de-amplification — see the plan doc §A), landing on master then propagating to splinters. **EB-only amplifier:** the stall-watchdog restart loop (`esports_bot.py:793-867`).
>
> **Operator elevation:** the splinter architecture means MB's master pool-fixes don't reach esports (the saturator). The EB session must actively work this — else MB stays hardened against a problem esports keeps regenerating at its source.

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
