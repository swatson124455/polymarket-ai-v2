# EB Pool Rebalance Proposal — esports cold-start within the existing PgBouncer budget

**From:** EB session (eb/main)
**Date:** 2026-06-03
**Status:** PROPOSAL. **Primary fix is EB-only (.env.esports) and needs NO MB/PgBouncer change.** A shared-infra fallback is included only for the case the EB-only fix proves insufficient.

---

## §1 — TL;DR

esports cold-start was crashing with `QueuePool limit of size 10 overflow 2 reached` — its **own** SQLAlchemy pool (12) is too small for the cold-start connection burst (warmup heavy reads + V1 + V2 + position_manager + background loops, concurrent). It is **NOT** a global-budget problem: the 4 services' pools sum to **51 < 60** (the PgBouncer `default_pool_size`), so there is headroom in the existing shared pool.

**Fix (EB-only):** raise esports' local pool to **14 + 4 overflow = 18**, leaving PgBouncer at its original 60. New 4-service sum = **57 < 60**. No shared-config change, MB untouched.

A prior step in this session raised PgBouncer 60→80 — that was the wrong lever (it let the *collective* expand to 85 and re-saturate). It has been **reverted**; PgBouncer is back at 60. This proposal does NOT re-touch it.

---

## §2 — Data (measured 2026-06-03)

| Service | env file | DB_POOL_SIZE | DB_MAX_OVERFLOW | max server conns (session mode) |
|---|---|---|---|---|
| mirror | `.env.mirror` | 10 | 3 | 13 |
| weather | `.env.weather` | 10 | 5 | 15 |
| esports | `.env.esports` | 10 | 2 | **12** |
| ingestion | `.env.ingestion` | 8 | 3 | 11 |
| **sum** | | | | **51** |

- PgBouncer: `pool_mode=session`, `default_pool_size=60`, `reserve_pool_size=5` (ceiling ~65), `max_client_conn=200`. One shared `polymarket/polymarket` pool for all 4 services.
- Postgres: `max_connections=100`; admin/TimescaleDB connect directly (~10-14), separate from the PgBouncer 60 pool.
- Code (`base_engine/data/database.py:1180-1218`): SQLAlchemy `pool_size=DB_POOL_SIZE`, `max_overflow=DB_MAX_OVERFLOW`; the `_db_semaphore` limit = `pool_size + max_overflow`. `DB_EFFECTIVE_POOL_SIZE=75` is only a warning threshold (`_warn_pool`), NOT a binding limit.
- Session mode means each pooled connection (even idle) pins a server connection for its lifetime; observed breakdown during saturation was ~56 `idle` / 4 `idle in transaction` (idle pinning, not a leak).

---

## §3 — Primary recommendation (EB-only, no MB touch)

**`.env.esports`: `DB_POOL_SIZE` 10 → 14, `DB_MAX_OVERFLOW` 2 → 4** (local pool 12 → 18), restart esports.

- New 4-service sum: 13 + 15 + **18** + 11 = **57 < 60**. All four services can hold their full pools simultaneously without queueing; ~8 conns of headroom to the 65 ceiling.
- esports cold-start gets the connections it was starved of (it crashed at 12; warmup completed cleanly at 18 in this session's test).
- **No PgBouncer change, no other-service change — MB untouched.** This is EB-owned config; the EB session can execute it.

**Why not raise PgBouncer instead:** raising `default_pool_size` lets the collective expand and re-saturate at the higher ceiling (observed: 85). Keeping the cap at 60 forces tight sharing, which is fine because the all-maxed sum (57) is under it.

**Verification (post-change):** esports completes warmup (`esports_v2_initialized`), runs real scans (`esports_v2_scan_funnel>0`), heartbeats refresh, no `QueuePool limit`/`scan_stall` cycling; MB/WB/ingestion stay active with Postgres conn count <100.

**Rollback:** `.env.esports` → `DB_POOL_SIZE=10, DB_MAX_OVERFLOW=2`, restart esports.

**RESULT (applied + verified 2026-06-03 ~18:09 UTC):** esports set to 14/4 (=18), PgBouncer left at 60 (untouched), restarted. Outcome at ~6min uptime: **STABLE** (pid steady, `scan_stall=0`, no crash-cycle — an improvement over pool 12, which crash-cycled), BUT **still not trading** — `esports_v2_initialized=0`, `esports_v2_scan_funnel=0`, one `QueuePool limit` event, heartbeats growing. So §3 (EB-only, within the existing 60 budget) **stabilizes esports but is insufficient to get it scanning/trading** — esports' cold-start connection demand bumps the cap even at 18. **→ This is a genuine shared-budget shortfall: escalate to §4 (operator/MB).** esports left at 18 (stable, within budget, MB-safe); revert to 10/2 if preferred. Per the hardcoded EB scope rule (`feedback_eb_no_shared_runtime_infra.md`), §4 is NOT for the EB session to execute.

---

## §4 — Fallback (ONLY if §3 is insufficient) — shared infra, MB/operator territory

If esports' *full steady-state* operation needs more than ~18 concurrent connections (i.e., even at 18 it QueuePool-exhausts or starves others under the 60 cap), then it is a genuine shared-budget shortfall and becomes operator/MB's call:

1. **Cross-service rebalance** — trim the lighter services and/or modest PgBouncer bump, keeping the sum within Postgres budget. No restart/mode flip.
2. **Raise Postgres `max_connections`** (100→150) + PgBouncer — one PG restart (brief all-bot blip).
3. **PgBouncer → transaction mode** — the architecturally correct fix (idle pool conns stop pinning servers), but requires app reconfig (`statement_cache_size=0`, `SET LOCAL statement_timeout`) + full cross-bot test. High risk to MB; planned change only.

None of §4 should be done by the EB session.

---

## §5 — Recommendation

Do **§3 (EB-only)** first — it is the least-risk, no-MB-touch fix and the data says it fits. Only escalate to **§4** if §3 is empirically insufficient.

---

## §6 — CONFIRMED 2026-06-03 (post-elite-gate): §3 is insufficient → §4 escalation REQUIRED. **MB OWNS THE DB/CONNECTION FIX.**

§3 (esports 12→18) was applied + left. This session tested whether **any** EB-lane change could get esports trading, and the answer is **no — the blocker is the shared session-mode pool**, re-confirming §10 of `AGENT_HANDOFF_EB_2026-06-03.md` with fresh evidence. Per operator direction, the **MB session is taking over the DB/connection fix.** This section is the technical handoff.

### What EB tried (all shipped, none moved the pool floor)
- **Elite-batch loop gated off** — `ELITE_BATCH_ENABLED=false`, commit `b9e4caf`, release `20260603_150102`. Gate verified active (0 `elite_batch` chunks since restart). **Pool unchanged: still `checked_out=16/18`, `ConnectionDoesNotExist` ~40/30m, scan funnel still 0.** Disabling a loop does NOT release session-mode-pinned connections.
- esports pool right-sized to 18 (§3): esports is now **stable-but-cycling** — completes warmup, runs exactly **1 scan** (`scan_count=1`), then scan #2 wedges waiting for a connection → `esportsbot_scan_stall_self_restart` at the 900s threshold → cold-start → repeat. The scan-stall watchdog is working **as designed**.

### Confirming evidence the shared pool is the floor (infra-state, VPS ~19:40 UTC 2026-06-03)
- **A direct diagnostic `psql` query was QUEUED by PgBouncer and returned `query_wait_timeout` / "No server connection available in postgres backend, client being queued"** — the shared pool had **zero** free server connections.
- `pg_stat_activity`: **46 idle** + 7 active + 5 idle-in-transaction ≈ **65** (the ceiling).
- `pgbouncer.ini` (current, verified): `pool_mode = session`, `default_pool_size = 60`, `reserve_pool_size = 5` → ceiling ≈ **65**.
- 4-service SQLAlchemy pool sum = **57** (mirror 13 + weather 15 + **esports 18** + ingestion 11) + ~10 admin/TimescaleDB ≈ **67 > 65**.

### Metric correction (important for the fix decision)
In **session mode each pooled connection pins a server connection whether idle or not**, so the binding metric is **sum-of-`pool_size`**, NOT `checked_out`. The per-service `checked_out` (mirror 3–4, weather 3, ingestion 0–1) looks low but each still pins 13/15/11 servers via `pool_size`. The earlier "57 < 60, MB-safe" calc (§3/handoff §11) was optimistic — it omitted ~10 admin connections + reserve semantics, so the real total is over the ceiling. The esports 12→18 raise added **+6** to that over-ceiling total.

### The fix — operator/MB territory (RULE THREE), NOT EB to execute
1. **PgBouncer → transaction mode** *(recommended)* — idle connections stop pinning servers. Requires app reconfig: asyncpg `statement_cache_size=0`; replace per-session `SET statement_timeout` with `SET LOCAL` inside transactions; verify advisory-lock reserve usage. High-value, needs full cross-bot test.
2. Raise Postgres `max_connections` (100→150) + PgBouncer ceiling — one PG restart (brief all-bot blip).
3. Cross-service pool rebalance within the existing ceiling (session mode: smaller pools free the ceiling).

### esports pool — MB decides as part of the holistic fix
esports is at **18** (`DB_POOL_SIZE=14` + overflow 4). It **crashed at 12** (§3), so a naive revert to 12 likely trades "wedge on shared pool" for "crash on own pool" — it moves the failure, doesn't fix it. **If MB goes transaction mode, leave esports at 18** (pool_size stops mattering for pinning). **If MB rebalances in session mode, MB sets esports' share — EB will revert on request.** EB is NOT changing esports' pool while MB owns the shared fix (defer per MB-priority).
Backups: `.env.esports.bak.20260603_elitebatch` (pre-flag, 14/4) · `.env.esports.bak-20260603` (original 10/2).
