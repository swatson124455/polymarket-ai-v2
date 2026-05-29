# EB → Operator / MB Coordination — Scan-Loop Stall from Shared-DB Contention

**Filed:** 2026-05-29 (EB session, eb/main)
**Severity:** HIGH — EsportsBot made **0 completed scans in ~36h**; new trade entries = 0 during that window.
**Status:** EB shipped a unilateral *recovery backstop* (below). The **root cause is shared infrastructure EB cannot fix unilaterally** — this memo requests operator/MB authorization for the durable fixes.

---

## 1. What happened (verified, journal-sourced)

- Last *completed* EB scan (`esportsbot_scan_summary`): **2026-05-27 02:40 UTC** — ~36h before this filing. Those final scans were already pathological: `phase_c` (market analysis) 42–55s, `total` 57–83s/cycle.
- The current process (pid 276810) logged only **8** "Scan cycle starting" lines in 20h, last at **2026-05-28 20:44 UTC**, then silence — i.e. it entered a scan and never returned.
- The heartbeat watchdog fired continuously: `Bot EsportsBot is stale — Last scan 1125.2m ago (threshold: 15m)` (and the same for `EsportsBotV2`) for ~18.75h, **with no recovery**.

## 2. Root cause (verified)

A cascading DB-connection failure wedged EB's asyncpg pool mid-scan (journal, 2026-05-28 19:41–19:48, the prior pid before it died and respawned):

- `position_manager._monitor_positions` (`base_engine/execution/position_manager.py:305`) query cancelled by the **15s server-side `statement_timeout`** (`QueryCanceledError`), plus a `DeadlockDetectedError` on position-price persist.
- `set_statement_timeout_failed error='cannot switch to state 12; another operation (2) is in progress'` — asyncpg **protocol-state corruption** (the cancellation-corruption signature behind RULE ZERO rule 6 / S162).
- `ConnectionDoesNotExistError`, `InterfaceError` (dead connection); the pool's pre-ping recovery **also failed**.
- Net effect: every subsequent DB await in the scan blocked on the wedged pool → `scan_and_trade()` never returned → scan loop dead.

**The statement timeouts are system-wide DB contention, not an EB-only bug.** Last 3h, `statement timeout`/`QueryCanceled` counts by service: **ingestion 10, weather 5, mirror 3, esports 0** (esports shows 0 only because its loop is hung and issues no queries). EB is the most exposed because its scan is the most DB-intensive in the fleet.

**Ruled out:** not a matcher/gate issue (those never run on a frozen loop); not a client-side `asyncio.wait_for`-on-DB bug in EB's paths — `position_manager:305` (S161 per-op sessions) and `prediction_engine` L561/L591/L3131 are already hardened (S162/S166). **NB:** the RULE ZERO ban doc's "known remaining violations: prediction_engine.py L561, L591, L3131" is **stale** — those lines now use server-side `statement_timeout`, no `wait_for`. Recommend correcting that list.

## 3. The watchdog gap (shared — `main.py`)

`main.py`'s watchdog (L257+) cannot recover this failure mode:
- **`bot.running` restart path (L261-290):** only restarts bots whose `.running` flag is `False`. A hung-inside-scan bot stays `running=True` → invisible. (Exactly the "running=True but scanning zero" case CLAUDE.md warns about.)
- **Heartbeat-staleness check (L292-341):** **only sends an alert** (L333). No recovery action.
- **`alive_count == 0 → sys.exit` (L401-422):** needs *all* bot objects dead; a hung-but-`running=True` bot keeps `alive_count > 0`, so it never fires. In per-bot services this is doubly broken — the shared `bot_heartbeats` table stays fresh from *other* services' processes, so no single hung service ever looks "all-dead."

## 4. What EB shipped unilaterally (eb/main — recovery backstop only)

1. **Scan-stall self-watchdog** (`bots/esports_bot.py`, `_scan_stall_watchdog`): time-based; if `self._scan_start_mono` stops advancing past `ESPORTS_STALL_RESTART_THRESHOLD_S` (default 900s) it SIGTERMs the process for a systemd restart (`Restart=always` confirmed on the unit) with a clean pool. Does **not** wrap/cancel any DB op (avoids the rule-6 corruption it's recovering from). Converts a silent ~36h death into ~15-min auto-recovery. **It is a backstop, not a cure** — it does not stop scans from hanging, only stops them staying hung.
2. **PandaScore 403 short-circuit** (`esports/data/pandascore_client.py`): all 11/11 403s in 24h were on `/lol/teams/{id}/stats` (plan/scope-gated; 403 not 401/429). Now returns `None` immediately instead of burning 3 retries + backoff per call. Restores fast graceful-fallback; does not restore the data.

## 5. Requested durable fixes (shared — need operator + MB authorization)

| # | Fix | Module (shared) | Why EB can't do it alone |
|---|---|---|---|
| D1 | Reduce system-wide DB contention — ingestion is the largest timeout source (10/3h); investigate the deadlock source on position-price persist | ingestion + `base_engine` DB layer | System-wide; affects MB/WB/ingestion; MB-priority per CLAUDE.md |
| D2 | Pool / PgBouncer right-size + the `statement_timeout` budget under load (relates to S221 pool work + open WB pool-rightsize / EB CLOSE-WAIT memos) | `/opt/pa2-shared/.env`, PgBouncer | Shared env — MB decides |
| E1 | Make `main.py` watchdog **recover** a heartbeat-stale bot (restart/sys.exit), not just alert — and make the per-bot-service "all-dead" check process-local | `main.py` (shared entry point) | Shared module — MB signoff |
| E2 | Make `position_manager._monitor_positions` resilient to a wedged pool (dispose+reconnect on `cannot switch to state` / `ConnectionDoesNotExist`) | `base_engine/execution/position_manager.py` | Shared module — MB signoff |

## 6. Verification commands (operator)

```bash
# EB scanning again? (after restart — backstop or manual)
ssh ... 'journalctl -u polymarket-esports --since "30 min ago" | grep -c esportsbot_scan_summary'   # expect > 0
# Backstop armed / fired?
ssh ... 'journalctl -u polymarket-esports --since "1 day ago" | grep esportsbot_scan_stall_self_restart'
# System-wide statement timeouts (the root signal)
ssh ... 'for s in esports mirror weather ingestion; do echo -n "$s: "; journalctl -u polymarket-$s --since "3 hours ago" | grep -c "statement timeout"; done'
```

---
*EB session is out of scope for the shared fixes (D/E) per RULE ONE-A / MB priority. This memo hands them to operator + MB. EB's backstop keeps EsportsBot self-recovering in the meantime.*
