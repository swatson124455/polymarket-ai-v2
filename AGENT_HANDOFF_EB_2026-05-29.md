# EB Session Handoff — 2026-05-29

**Date:** 2026-05-29 (→ 05-30 UTC)
**Branch:** `eb/main` (HEAD `e53c88e`)
**Worktree:** `C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main/`
**Master HEAD:** `a2fda49` (MB-driven; EB does not own master)
**EB splinter VPS:** release `20260529_183249` (symlink `/opt/polymarket-ai-v2-esports`), process `321736`, up ~3h19m at handoff.

**One-line status:** Diagnosed the EB "trade bottleneck" as a hung V1 scan loop (shared-DB asyncpg corruption); shipped + deployed an EB-side recovery backstop + 403 fix + coordination memo; fixed a time-bomb test blocking the deploy gate. **BUT the bottleneck is NOT resolved at the root** — root cause is shared-DB contention (operator/MB scope), V2 matches 0 markets, and the backstop has not yet proven it fires. See §4 (live state) + §7 (can't-verify).

---

## §1 — What landed this session (5 commits on `eb/main`, since handoff `5d47b11`)

| Commit | Type | Summary |
|---|---|---|
| `18f5bb9` | fix (EB code) | **Scan-stall self-watchdog** in `esports_bot.py` — SIGTERMs the process for a systemd restart if `_scan_start_mono` stalls > `ESPORTS_STALL_RESTART_THRESHOLD_S` (900s). Time-based only (no `wait_for`-on-DB). + 3 regression tests. |
| `f53f5c8` | fix (EB code) | **PandaScore 403 short-circuit** in `pandascore_client.py` — 403 (plan-gated `/teams/{id}/stats`) returns None immediately instead of 3 retries + backoff. |
| `54c27fc` | docs | **`EB_COORDINATION_SCAN_STALL_DBLOAD.md`** — routes the shared root cause (D1/D2/E1/E2) to operator + MB. |
| `5c5cd5c` | fix (test) | **EsportsBotV2 time-bomb** — `_seed_trinity` hardcoded `_team_last_match = datetime(2026,4,14)`; once wall-clock crossed `_STALE_DAYS=45` past it (2026-05-29), `_teams_are_fresh()` failed → 7 v2 tests failed → blocked `deploy.sh`. Now seeds relative to `now`. |
| `e53c88e` | docs | Inline rule-6 safety annotations on `AGENT_HANDOFF_EB_2026-05-28.md` (the `asyncio.wait_for` recommendation). |

Also: appended **§11** to `AGENT_HANDOFF_EB_2026-05-28.md` (post-deploy 20h calibrator verification) earlier in the session.

---

## §2 — Diagnosis: the trade bottleneck (verified)

The original symptom: EB "not trading." Root cause = **V1 scan loop hung** — 0 completed scans in ~36h; the current process before redeploy was stale ~18.75h while alive.

**Failure chain (journal-verified):**
1. EB's scan is the most DB-intensive in the fleet (`phase_c` 42–55s).
2. Under **system-wide shared-Postgres contention** (last 3h sample: ingestion 10 / WB 5 / MB 3 `statement_timeout`; EB 0 only because hung), a monitoring query — `position_manager._monitor_positions` (`base_engine/execution/position_manager.py:305`) — hit the 15s `statement_timeout` (`QueryCanceledError`) + a `DeadlockDetectedError`.
3. The cancellation corrupted the asyncpg connection (`cannot switch to state N; another operation in progress`); pool pre-ping recovery **also failed** → every later DB await blocked → scan never returned.
4. **No recovery existed:** `main.py` watchdog only *alerts* on staleness; its `bot.running`/`alive_count` restart paths don't cover a `running=True`-but-hung bot.

**Ruled out:** not a matcher/gate issue (those don't run on a frozen loop); not a client-side `wait_for`-on-DB bug in EB paths (S161/S162/S166 already hardened — the RULE ZERO ban doc's "remaining violations: prediction_engine L561/591/3131" is **stale**, those are fixed).

---

## §3 — Deploy (succeeded, EB-scoped)

`bash deploy/deploy.sh` → release `20260529_183249`:
- Preflight: full `tests/unit/` suite **2954 passed** (the `5c5cd5c` time-bomb fix cleared the 7 v2 failures that aborted the first attempt).
- **MB / WB / ingestion confirmed untouched** (deploy cross-check + my verify). Atomic symlink swap; drop-in override verified.
- `HEALTH_WARN` (scan_ms not seen in 420s) = expected EB v2 cold-fit; deploy passed (services active).

**First deploy attempt failed** at preflight (the 7 v2 time-bomb failures) — VPS untouched, no harm; root-caused + fixed (`5c5cd5c`), re-deployed clean.

---

## §4 — CURRENT LIVE STATE (read carefully — bottleneck NOT resolved)

At handoff (process `321736`, up ~3h19m, **no wedge in last 15min**, intermittent cold-start asyncpg wedge appears past):

| Bot | State | Detail |
|---|---|---|
| **EsportsBotV2** | ⚠ Functioning, **0 trades** | Scanning ~2min cadence, `scan_ms` 13–42s, completing cycles. Funnel: `upcoming_seen=72, matched=0, queued=0` → **matches 0 Polymarket markets** → no trades. Matcher / market-availability issue, NOT a hang. |
| **EsportsBot (V1)** | 🔴 **Stale ~2.7h** | Watchdog: "Last scan 162.9m ago (threshold 15m)". Not logging "Scan cycle starting" → loop not iterating. |
| **Scan-stall backstop** | ⚠ **Has NOT fired** | 0 `esportsbot_scan_stall_self_restart` despite V1 staleness >> 900s threshold. **Unproven / possible gap** — see §7. |

**Net: EB family is still not trading.** The 18.75h silent hang is gone (process cycling, V2 healthy), but V1 is stale and V2 matches nothing.

---

## §5 — Root cause is SHARED (operator + MB scope) — the actual cure

Per `EB_COORDINATION_SCAN_STALL_DBLOAD.md`. EB cannot fix these unilaterally (RULE ONE-A):
- **D1** Reduce system-wide DB contention (ingestion = top `statement_timeout` source; the deadlock on position-price persist).
- **D2** Pool / PgBouncer right-size + `statement_timeout` budget (ties to S221 pool work, open WB pool-rightsize, EB CLOSE-WAIT memos).
- **E1** `main.py` watchdog → *recover* a heartbeat-stale bot (restart/sys.exit), not just alert; fix per-process "all-dead".
- **E2** `position_manager._monitor_positions` → dispose+reconnect on `cannot switch to state` / `ConnectionDoesNotExist`. **NB:** the post-deploy re-wedge confirms this is asyncpg **connection-sharing across coroutines** ("another operation in progress"), a concurrency bug — likely shared DB-layer.

---

## §6 — Carry-forward (priority order)

| # | Item | Owner | Notes |
|---|---|---|---|
| **P0** | **Verify the backstop actually fires** | EB | V1 stale 2.7h but backstop didn't fire. Check: is `EsportsBot.running` False (→ `while self.running` exits the watchdog)? Did `start()` launch the task? Is `_scan_start_mono` being refreshed? **The one thing shipped-but-unproven.** |
| **P0** | **V2 `matched=0`** | EB | 72 upcoming → 0 matched Polymarket markets. Market-availability vs matcher bug. This blocks V2 trading independent of V1. |
| **P1** | Shared root cause D1/D2/E1/E2 | operator/MB | The actual cure. Memo filed with live re-wedge evidence. |
| P2 | Calibrator valorant/dota2 silent (Anomaly B) | EB | From 05-28 §11. **3rd hang option** (per S-review): connection-acq/semaphore/sync-I/O, not just DB-vs-CPU. **Trace loop entry/exit first** to confirm they even enter the per-game loop. Don't ship `asyncio.wait_for` (rule 6). |
| P2 | Anomaly A (LoL `n=0`) | EB | "Likely cold-start transient" needs **confirmation** (check 7d before the n=0 window), not closeout. |
| P2 | `statement_timeout=30s` math | EB | Count DB calls in the fit path — a "hang" could be death-by-N-cuts (N×30s), not infinite. Changes the diagnosis. |
| P3 | #19 CLOSE-WAIT (PandaScore httpx) | EB | From 05-26 PM handoff. |
| P3 | #20 two stuck markets (`0x73d8e486cc..`, `0x7abae048de..`) | EB | From 05-26 PM handoff. |
| P3 | PandaScore plan upgrade (real 403 fix) | operator | team-stats endpoint needs higher tier. |
| P4 | Correct stale ban-doc | EB/memory | `prediction_engine` L561/591/3131 are fixed, not "remaining violations". |

**Two runbook items to land durably (per S-review — not yet done):**
1. EB deploy-verify path = symlink root `/opt/polymarket-ai-v2-esports`, **NOT** `/opt/pa2-esports-releases/current` (which doesn't exist; I hit this mid-session).
2. Handoff commit convention: `.gitignore:147` ignores `AGENT_HANDOFF_*.md` → `git add -f` required.

**Clarify:** `base_engine/data/ingestion_error_capture.txt` left untracked per 05-28 §10 — is it noise-to-ignore or an artifact under active investigation? Confirm before next commit-scope decision.

---

## §7 — The "Can't Fully Verify" section

**Verified:** the 5 commits; deploy `20260529_183249` (2954 tests pass, MB/WB/ingestion untouched); V2 scanning (`esports_v2_scan_funnel`); V1 stale 2.7h (watchdog); the time-bomb root cause + that `_warmup_complete()` returns True on `_initialized`; the asyncpg wedge signature; the rule-6 ban is real (S162).

**NOT verified (flagged, not asserted):**
- **The scan-stall backstop firing in production** — it has NOT fired despite V1 being stale ~2.7h. Either it's not covering V1's failure mode (e.g. `self.running` False → watchdog loop exits), the task didn't launch, or `_scan_start_mono` is being refreshed. **The headline EB-side deliverable is unproven and current evidence is concerning.** Next session MUST confirm (P0).
- **Why V2 matches 0 markets** — funnel shows it, root cause uninvestigated.
- That the cold-start asyncpg wedge won't recur under load (it was intermittent; absent in last 15min only).

**Operator verify commands:**
```bash
KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem; H=ubuntu@18.201.216.0
ssh -i $KEY $H 'systemctl show -p MainPID --value polymarket-esports; ps -o etime= -p $(systemctl show -p MainPID --value polymarket-esports)'
ssh -i $KEY $H 'journalctl -u polymarket-esports --since "20 min ago" | grep -E "Scan cycle starting|esports_v2_scan_funnel|esportsbot_scan_summary|scan_stall_self_restart|is stale" | tail -30'
# Backstop check: is EsportsBot (v1) starting scans at all, or is self.running False?
```

---

## §8 — Next-session entry protocol

```bash
cd C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main
git rev-parse --abbrev-ref HEAD          # eb/main
git log --oneline -7                     # confirm e53c88e..18f5bb9 present
# 1) P0: prove or fix the backstop (V1 stale, didn't fire) — esports_bot.py _scan_stall_watchdog
# 2) P0: V2 matched=0 — why 72 upcoming → 0 Polymarket matches
# 3) Read EB_COORDINATION_SCAN_STALL_DBLOAD.md; hand D1/D2/E1/E2 to operator/MB
```

---

## §9 — Scope / priority note

All work this session was EB-owned (`eb/main` + EB splinter; `esports_bot.py`, `pandascore_client.py`, EB test, EB docs). No shared-module edit, no `/opt/pa2-shared/.env`, no master commit, no MB/WB resource touched. The shared root cause (§5) is explicitly routed to operator/MB, not actioned here.
