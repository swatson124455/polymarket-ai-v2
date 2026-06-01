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

---

## CRITICAL ADDITION 2026-05-31 (S235 session review): `base_bot.py:811` is likely the PRIMARY corruption source

**Filed at operator request after session review. Read before actioning D1/D2.**

The S235 session fixed `esports_market_scanner.py:261` (rule-6 violation: `asyncio.wait_for` on a DB call). The session review flagged that `base_bot.py:811` is the same class of bug with broader blast radius:

```python
# C2 FIX (base_bot.py:811)
_scan_timeout = getattr(settings, "BOT_SCAN_TIMEOUT_SECONDS", 60)  # VPS: 90s
await asyncio.wait_for(self.scan_and_trade(), timeout=_scan_timeout)
```

**The mechanism (identical to S162/RULE ZERO rule 6):**
1. Under DB contention, a query inside `scan_and_trade()` runs slowly (>90s)
2. `asyncio.wait_for` fires at 90s → `CancelledError` propagates into whatever asyncpg call is mid-flight
3. asyncpg's protocol state machine is interrupted mid-operation → connection enters `cannot switch to state N; another operation (2) is in progress`
4. The poisoned connection returns to the pool
5. The next caller (position_manager, another bot, ingestion) checks out the corrupted connection
6. `set_statement_timeout_failed` → cascade begins

**Why this changes the D1/D2 diagnosis:**
The current framing is "ingestion hammers DB → contention → corruption." That's *half* the story. The full chain is: **contention triggers slow query → `wait_for` cancels mid-asyncpg → corrupts pool → all bots and services affected.** Ingestion provides the trigger; `base_bot.py:811` provides the corruption mechanism.

The `set_statement_timeout_failed … cannot switch to state 12; another operation (2) is in progress` errors observed across EB/MB/WB/ingestion could be primarily from `base_bot.py:811` firing on every scan that exceeds 90s — not from the individual ingestion queries.

**Why this is NOT a hot-patch:**
The C2 FIX was added for a real reason: a hung DB query would block the entire event loop, freezing all bots and WS processing. Removing `wait_for` naively re-introduces that. The correct fix direction:
- Individual queries already have server-side `statement_timeout` (15s) via `_SemaphoreSession.__aenter__`. If all DB-touching code in `scan_and_trade()` goes through `_SemaphoreSession`, each query is already bounded at 15s.
- With per-query 15s timeouts in place, `scan_and_trade()` cannot hang indefinitely at the DB level — the original C2 concern is already addressed at the individual query level.
- Removing `wait_for` at line 811 would stop the client-side cancellation from corrupting asyncpg connections on every 90s+ scan.
- **Before removing**: audit every DB call in `scan_and_trade()` to confirm they all go through `_SemaphoreSession`. Any that bypass it (raw asyncpg calls, direct pool access) need per-call `statement_timeout` added first.

**Investigation path:**
1. Confirm: does `asyncio.wait_for(scan_and_trade(), timeout=90)` firing correlate with the `cannot switch to state N` errors in the logs? (Check: do the corruption events happen at ~90s marks after scan starts?)
2. Audit all DB calls in `scan_and_trade()` for `_SemaphoreSession` coverage
3. If covered: propose removing `wait_for` from `base_bot.py:811` (shared module, master change, all 14 bots)
4. If not covered: add per-call `statement_timeout` first, then remove `wait_for`

**Who owns this:** MB session (shared module, all-bots blast radius). EB can do the audit on eb/main and produce the coverage map, then surface as a master cherry-pick proposal. Do NOT hot-patch.

---

## UPDATE 2026-05-30 (S235) — backstop was BROKEN, now fixed; contention still ACTIVE

**The "backstop keeps EsportsBot self-recovering" claim above was false** — the S233 watchdog never once fired. It recurred: a fresh wedge left both esports bots dead **~13h** (2026-05-30, PID 345158). Root cause = two EB-owned watchdog bugs (now fixed, `eb/main` `442064d` + `1cd5611`, deploy `20260530_213432`):
1. V1 watchdog created before `super().start()` set `running=True` → `while self.running` saw False on first schedule → task returned silently, never ran. (Never fired in any incident.)
2. EsportsBotV2 (sibling of EsportsBot) had **no watchdog at all** — the primary trader was uncovered.

Fix: watchdog loops `while True` (exit only on cancellation); V2 gets its own. Both **proven armed in production** (`esportsbot_/esports_v2_scan_stall_watchdog_armed` logs under PID 367955). Now the failure is **loud + self-recovering** instead of a silent multi-hour death.

**The shared root cause (D1/D2) is UNCHANGED and ACTIVE/SEVERE:** the manual-restore process (PID 366175, 01:15 UTC) re-wedged within **~6 min** (kill-switch + `scan_and_trade()` timeouts by 01:21). With the watchdog now working, sustained contention → EB will **restart-cycle** (terminal wedge → SIGTERM → restart → re-wedge), each restart adding cold-start DB load. **This raises, not lowers, the urgency of D1/D2.** The watchdog converts silent death into a visible restart loop — please action the shared fixes.
