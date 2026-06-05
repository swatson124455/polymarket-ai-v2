# DB & VPS Workload Research — why a 32GB/8vCPU box hits resource failures

**Date:** 2026-06-04 (MB session). **Method:** 3 parallel read-only repo code-audits + 1 live read-only VPS/DB telemetry sweep. **Status:** research only — no code/config changed. Any infra fix (PgBouncer/Postgres/pool sizing) is shared runtime infra → needs explicit operator sign-off (RULE THREE).

---

## HEADLINE VERDICT

**It is not GB and it is not CPU. The binding ceiling is the DB connection budget, and PgBouncer is running in `session` mode, which defeats connection multiplexing.**

The hardware is half-idle at the moment of "failure":
- **RAM:** 21 GiB *available* of 30 GiB; **0 OOM-kills in 3 days**; every service well under its cap (heaviest = esports 1043 MB of a 2560 MB cap).
- **CPU:** load average 3.63 / 3.76 / 4.53 on **8 cores** (~45–57%).
- **Disk:** 44% used.

Meanwhile the connection layer is pinned to its ceiling:
- **PgBouncer `default_pool_size=60` + `reserve_pool_size=5` = 65 server-connection ceiling**, and Postgres shows **exactly 65 `polymarket` connections — 63 of them IDLE**.
- That idle-but-held signature is the fingerprint of **`pool_mode = session`**: PgBouncer pins one Postgres backend to each app connection *for the whole life of the connection*, not per-transaction. With four services each keeping a warm SQLAlchemy pool, ~65 backends are consumed and mostly sit idle — **zero multiplexing benefit**.

When a burst needs the 66th concurrent connection, it waits `DB_POOL_TIMEOUT`/semaphore-acquire (15s) and times out → **"Kill switch check timed out (10s)"**, **"scan_and_trade() timed out"**, scan stalls, watchdog restarts. A gaming-PC CPU cannot help: the wall is a *connection count*, not compute.

---

## ⚠ VERIFIED ADDENDUM (2026-06-04 deep dive — supersedes inference below)

A code+socket deep dive verified the wiring and **corrected two of my earlier inferences**:

1. **Routing — VERIFIED (not assumed):** every app process connects to **PgBouncer:6432**; the only client on Postgres:5432 is PgBouncer itself (65 conns). No app bypasses the pooler. (`ss -tnp`.)
2. **Per-process budget — VERIFIED (replaces the S230-doc estimates):** held connections right now — **esports 50**, mirror 10, weather 10, ingestion 8, orderbook 2 = **80 clients vs the 65 server ceiling** → ~15 perpetually queued in session mode = the timeout source.
3. **ROOT CAUSE refined — esports is leaking DB engines.** esports holds **50** connections against an **18**-max configured pool, and its process re-initializes the DB engine **every ~85s** (same pid; 2280 cumulative inits vs ~130 for the other bots — a 17× outlier). 50 > 18 is impossible for one pool → it stands up new engines without disposing the old ones; session mode pins the leaked backends. **This single leak is the dominant consumer of the shared 65-slot pool.** → filed `EB_COORDINATION_ESPORTS_DB_ENGINE_LEAK.md` (trigger is eb/main code = EB; shared re-init hardening = MB).
4. **CORRECTION — LISTEN/NOTIFY is NOT a transaction-mode blocker.** Production instantiates `ResolutionListener` (30s **polling**, `base_engine.py:847`); `PGNotificationListener` (the LISTEN/NOTIFY class) is **never instantiated** — dead code. The per-market advisory lock (`advisory_locks.py`) is `pg_advisory_xact_lock` (txn-safe) and isn't wired into any bot. The **only** real session-mode dependency is `database_lock.py`'s session-scoped lock, confined to ingestion + learning pipeline coordination. So **transaction mode is far more tractable than the "config contradiction" section below implies** — and fixing the esports leak may make it unnecessary.

**Revised lever order:** (1) fix the esports engine leak (50→~15) — biggest, cheapest, EB-owned; (2) MB-harden the shared re-init paths (leak-safety net); (3) transaction mode as the durable structural option (now shown tractable); (4) raising PgBouncer/`max_connections` remains wrong-alone.

---

## LIVE EVIDENCE (verified this session — source in parens)

| Metric | Value | Source |
|---|---|---|
| Cores / load avg | 8 / 3.63, 3.76, 4.53 | `uptime` |
| RAM total/used/available | 30Gi / 9.1Gi / **21Gi** | `free -h` |
| Swap used | 928Mi / 2.0Gi | `free -h` |
| OOM-kills (3 days) | **0** | `journalctl -k` |
| Postgres `max_connections` | **100** | `SHOW max_connections` |
| Postgres conns by state | **idle 63**, active 4, null 6 | `pg_stat_activity` |
| Postgres conns, app user `polymarket` | **65** | `pg_stat_activity` |
| **idle-in-transaction** | **0** (oldest 0s) | `pg_stat_activity` |
| Longest active queries | **3 concurrent copies** of a `SUM(CASE WHEN side IN ('YES','BUY')…)` aggregate, 3–4s each | `pg_stat_activity` |
| PgBouncer | **`pool_mode=session`**, `default_pool_size=60`, `reserve_pool_size=5`, `min_pool_size=5`, `max_client_conn=200` | `/etc/pgbouncer/pgbouncer.ini` |
| TCP sockets | 535 ESTAB, 132 TIME-WAIT, **32 CLOSE-WAIT**, 12 LISTEN | `ss -tan` |
| Service restarts (NRestarts, cumulative) | **esports 42**, **mirror 22**, ingestion 7, weather 0 | `systemctl show` |
| Service mem (current / cap) | mirror 733M/2560M · ingestion 246M/512M · esports 1043M/2560M · weather 723M/2048M | `systemctl show` |
| Last restart | esports 12:04 & ingestion 12:11 (today) · mirror 06-04 01:27 · weather 06-03 19:14 | `systemctl show` |

**Could NOT get:** PgBouncer `SHOW POOLS` (admin auth requires the pgbouncer credential; `sudo -u postgres` was rejected by password). So live `cl_waiting`/`maxwait` (the direct "clients queued for a server backend right now" metric) is unconfirmed — saturation is *inferred* from the 65/65-held + 63-idle pattern, not measured at the pooler. **This is the #1 next telemetry step.**

---

## ROOT CAUSE — the connection ceiling, three reinforcing layers

### Layer 1 (structural): `pool_mode = session` + warm app pools = no multiplexing
Session mode assigns a dedicated Postgres backend per *client connection* for its entire lifetime. SQLAlchemy keeps `pool_size` connections open persistently (recycled every `DB_POOL_RECYCLE`). So each pooled app connection pins one Postgres backend whether or not it is querying — proven live by **63 idle of 65 held**. The four services' pools collectively saturate the 65-backend PgBouncer ceiling with mostly-idle connections, leaving almost no headroom for bursts.

### Layer 2 (budget): app demand (≤71) > pooler ceiling (65)
Per the most recent committed measurement (`WB_COORDINATION_POOL_RIGHTSIZE.md`, S230): worst-case sum of per-service pools = ingestion 11 + mirror 13 + esports 17 + weather 30 = **71** vs the **65** ceiling. *(Per-service figures are repo-doc, NOT re-verified live this session — see "what's unverified". The aggregate ceiling of 65 IS verified live.)* WeatherBot's 30 is 2.7× its observed peak demand; the filed fix (WB 30→15 → sum 56, ~9 headroom) is **documented but not landed**. The S230 mitigation only raised the in-app *warning threshold* (`DB_EFFECTIVE_POOL_SIZE` 60→75) — noise suppression, not a real ceiling change.

### Layer 3 (waste): corruption burns slots from the scarce 65
`asyncio.wait_for(scan_and_trade(), …)` at **base_bot.py:811** cancels mid-`asyncpg` on any stalled scan; the injected `CancelledError` (a `BaseException`) corrupts the connection's protocol state ("cannot switch to state N / another operation in progress"). The `handle_error` invalidation listener (**database.py:1240-1256**) does **not** match those corruption signatures, so the poisoned connection is **returned to the pool** and burns one of the 65 until recycle. Self-amplifying: pressure → stalls → cancels → corruption → fewer usable slots → more pressure. **Second instance** of the same class confirmed at **ingestion_scheduler.py:337** (`wait_for(run_resolution_backfill, 90s)`).

---

## THE CONFIG CONTRADICTION (highest-leverage, but entangled)

The application is configured **as if for transaction mode** — `statement_cache_size=0` (database.py:1186), per-session `SET statement_timeout`, and every code comment says "PgBouncer transaction mode" — but PgBouncer is actually in **session mode**. This is **worst-of-both-worlds**:
- Pays the transaction-mode cost (prepared-statement caching disabled → more per-query overhead).
- Gets none of the transaction-mode benefit (no connection multiplexing).

**[CORRECTED — see VERIFIED ADDENDUM above]** I originally inferred session mode was required for LISTEN/NOTIFY. That is **false in production**: `PGNotificationListener` is never instantiated (dead code); the live resolution path is `ResolutionListener` polling. The only genuine session-mode dependency is `database_lock.py`'s session-scoped `pg_advisory_lock` (ingestion + learning pipeline coordination — not the trading hot path), plus the per-session `SET statement_timeout` (movable to `SET LOCAL` or role-level `ALTER ROLE`). Transaction mode is therefore a bounded, tractable change — not the entangled blocker this paragraph originally claimed.

**Silver lining / correction to the repo audit:** because it IS session mode, the per-session `SET statement_timeout` / `idle_in_transaction_session_timeout` guardrail **actually works** (the SET persists for the connection's life). Live `idle-in-transaction = 0` confirms it. Agent 1 classified this guardrail "PARTIAL/unreliable" on the assumption of transaction mode — that classification is **void**; in the real (session) mode it is effective.

---

## GUARDRAILS THAT ARE FAILING OR COUNTERPRODUCTIVE (ranked)

1. **`wait_for(scan_and_trade)` corrupts asyncpg connections** — base_bot.py:811 (also ingestion_scheduler.py:337; residual at trade_coordinator.py:552, position_manager.py:952/984/1034). Primary corruption vector. *Code-confirmed by both audit agents + MEMORY.* Fix direction: server-side `statement_timeout`/budget per the S166/S177 pattern already adopted elsewhere; never wrap composite multi-statement asyncpg work in `wait_for`.
2. **`handle_error` listener misses the corruption it most needs to catch** — database.py:1240-1256 matches "connection was closed"/`ConnectionDoesNotExistError` but NOT "cannot switch to state"/"another operation in progress". So #1's poisoned connections survive in the pool. Fix: add those substrings to the invalidation match.
3. **App semaphore provides false accounting, no real backpressure** — database.py:1217 sets the limit = pool_size+overflow (no headroom shed), AND the highest-frequency consumers bypass it via `get_raw_session()` (10s price/trade persister database.py:1753/1905, id_resolver.py:27/53, position_manager.py:308). So "semaphore available" can read healthy while the pool is exhausted.
4. **Infra alerting is wired but DEAD** — `PrometheusExporter` (defines `db_pool_active`, `rss_bytes`) is **never instantiated/started** anywhere in production; `health_runner.py` pool-exhaustion / idle-in-txn checks `logger.warning` to journald and **never call the live `AlertingSystem`** (Slack/Discord/SMS). Every Grafana rule keyed on `polymarket_db_pool_active` (threshold 250 — wrong scale, real ceiling 65) fires on a metric production never emits. **The pool can saturate or a bot can be OOM-killed with zero operator notification.** *Code-confirmed by Agent 3.*

---

## WHAT WE DON'T HAVE (missing, ranked)

1. **Connection multiplexing** — session mode negates it; only present because LISTEN/NOTIFY needs it. Biggest structural lever (see contradiction section).
2. **Out-of-band infra alerting** — no pool-saturation / connection-budget / OOM alert ever reaches a human channel (see failing-guardrail #4).
3. **A circuit breaker on DB acquisition** — the order-execution path has one (`execution_engine.py`, PERMANENT_HALT after N escalations); the DB-acquisition path has none. A saturated pool gets hammered by every scan retry with no back-off.
4. **Continuous idle/leak reaper** — idle-in-txn killing exists only at startup (main.py:155-160) + a prune cron; the hourly HealthRunner check only logs. (Postgres-side `idle_in_transaction_session_timeout=60s` is the live backstop — and it's working: live idle-in-txn = 0.)
5. **Per-service hard cap at the pooler** — no committed `pgbouncer.ini` per-user `max_db_connections`; the 71-vs-65 oversubscription is preventable only by client-side politeness.
6. **Automatic pool-wide recovery from corruption** — database.py:219-251 (S235) quarantines a *single* poisoned session but never rebuilds the pool (`engine.dispose()`) after N corruptions.

---

## SECONDARY FINDINGS

- **Restart churn corroborates the stall family:** esports **42** restarts, mirror **22** (esports & ingestion both restarted within the last ~25 min of the sweep). Consistent with the watchdog force-restarting stalled-but-running bots (commit `e52b11b`) — i.e., the pool-pressure→stall→restart loop is *active*, not historical.
- **3 concurrent copies of a `SUM(CASE WHEN side IN ('YES','BUY')…)` aggregate**, 3–4s each, observed live. An expensive aggregate running redundantly across processes holds connections 3–4s apiece — a query-efficiency + connection-pressure contributor worth isolating (candidate for caching / single-owner computation).
- **CLOSE-WAIT = 32** (not the ~103 from the earlier EB-specific report). Moderate. Agent 2's likely sources: per-call `httpx.AsyncClient()` churn in clob_adapter.py (5 sites) + resolution_backfill.py:22 (per-market in a 500-loop), plus a shared-client double-`aclose()` in resolution_backfill.py:206/409. Not catastrophic now; a slow leak/churn.
- **`event_bus.py:140`** fire-and-forget `create_task` that opens a DB session per emit with errors swallowed at debug — CLAUDE.md-banned pattern; leaks detached session-holding tasks under load. *Agent 2.*
- **ingestion `MemoryMax=512M`** is tight for a Python data process (`OOMScoreAdjust=+100`, `Restart=always`) — but live shows 246 MB used and 0 OOM in 3 days, so it's a **latent** headroom risk, not an active failure.
- **Swap:** 928 MiB in use with 21 GiB RAM available — mild, worth watching but not a driver.

---

## VERIFIED LIVE vs REPO-AUDIT (discipline)

- **Verified live this session:** PgBouncer mode/sizes, Postgres `max_connections`=100 + 65 app conns (63 idle), idle-in-txn=0, OOM=0, load, RAM, restarts, sockets, the 3× aggregate query, per-service cgroup mem.
- **Repo-audit only (NOT live-confirmed):** per-service pool sizes (11/13/17/30 — from S230 doc; I did **not** read `/opt/pa2-shared/.env.{bot}` this session per a prior operator rejection), the full leak inventory (code-grounded with file:line, but not runtime-observed), the alerting-dead finding (code-grounded). The `wait_for`→corruption mechanism is code-confirmed *and* corroborated by the live restart churn + the documented EB/WB stall chain.
- **Unmeasured:** PgBouncer `SHOW POOLS` (cl_waiting/maxwait) — admin auth needed.

---

## PROPOSED NEXT STEPS (all gated on operator sign-off; infra = RULE THREE)

**A. Close the telemetry gap (read-only):** obtain PgBouncer admin creds (or run as the pgbouncer service user) to capture `SHOW POOLS`/`SHOW STATS` and confirm `cl_waiting`/`maxwait` at the pooler; sample over the daily ~20:00 UTC DB-load window the WB stall memo flagged.

**B. Decide the structural lever (design, then sign-off):** evaluate PgBouncer **transaction mode** + a dedicated direct connection for the LISTEN/NOTIFY listener — the only change that actually multiplies the 65-connection ceiling. Entangled; needs a written design + test plan before any change.

**C. Land the filed pool right-size (low-risk, already designed):** WB pool 30→15 drops sum-of-pools 71→56 (~9 headroom under 65). WB-session owned; MB coordinates timing.

**D. Code fixes (shared-module, MB-owned, full sequence + sign-off):** (1) remove `wait_for(scan_and_trade)` → server-side budget; (2) add the asyncpg-corruption signatures to the `handle_error` invalidation match; (3) the kill-switch cache-on-timeout fix already proposed this session.

**E. Wire infra alerting (medium):** start `PrometheusExporter` and/or route `health_runner` pool/OOM conditions through the existing `AlertingSystem`; fix Grafana thresholds to the real 65 ceiling.
