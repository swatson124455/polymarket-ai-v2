# EB Session Handoff — 2026-05-30 (→ 05-31 UTC) — S235

**Branch:** `eb/main` (HEAD `1cd5611`)
**Worktree:** `C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main/`
**EB splinter VPS:** release `20260530_213432` (symlink `/opt/polymarket-ai-v2-esports`), process `367955`.
**Master HEAD:** unchanged (EB does not own master).

**One-line status:** The S233 scan-stall self-watchdog (the P0 carried from the 2026-05-29 handoff) **never fired** — root-caused to two EB-owned bugs and **fixed for both esports bots**; deployed; **both watchdogs proven armed in production**. The *hang itself* is unchanged shared-DB contention (operator/MB) and is **active + severe right now**.

---

## §1 — The incident (verified)

At session start the EB family had been **down ~13h**: PID 345158 (up ~13h19m) had completed **zero** scans since its ~11:33 UTC start. Both EsportsBot (V1) and EsportsBotV2 (V2) were `watchdog.heartbeat` stale ~12–13h. The scan-stall self-watchdog shipped last session (`18f5bb9`) **never fired** despite being ~50× past its 900s threshold.

Journal reconstruction:
- V1 last "Scan cycle starting" 11:54:26 → `scan_and_trade()` timed out (90s) 11:55:56 → last log of any kind 12:10:51 ("Kill switch check timed out — failing closed") → silent.
- V2 last "Scan cycle starting" 12:07:27 → timed out 12:09:24 → silent.
- **"Scan cycle done" NEVER logged** under PID 345158 — not one scan completed in 13h.
- Hang signature: `ConnectionDoesNotExistError`, `QueryCanceledError`, `cannot switch to state N; another operation (2) in progress` (asyncpg protocol corruption) — the same shared-DB-contention cascade as the 2026-05-28 ~18.75h hang.

## §2 — Root cause of "the backstop never fired" (two EB-owned bugs)

**Bug 1 — V1 watchdog dies at startup (race).** `EsportsBot._scan_stall_watchdog` is created at `esports_bot.py:675`, but `self.running` is not set `True` until `super().start()` at `esports_bot.py:757`. The watchdog looped `while self.running:`. When the task is first scheduled (at the Redis-restore awaits, ~697), `running` is still the init default `False` → the loop body never executes → the task **returns immediately and silently** (clean return → `_task_error_handler` logs nothing, since `task.cancelled()` is False and `task.exception()` is None). It had **never once run its loop body** — in this hang or the prior one.

Confirming evidence: zero "Bot scan error", zero "Bot stopped after max consecutive failures", zero "Background task failed/cancelled" over 14h; VPS env sets neither `ESPORTS_STALL_*` (defaults 60s/900s — not a misconfig); no `running = False` setter exists in esports code or degradation/state-machine (only base_bot's four paths, none of which logged).

**Bug 2 — V2 has no watchdog at all.** `class EsportsBotV2(BaseBot)` is a **sibling** of `EsportsBot`; the watchdog lived only in V1. V2 — the primary trader — had no recovery path and didn't even set `_scan_start_mono`.

(Both bots wedged together here, so a *working* V1 watchdog would have recovered the process — but a V2-alone wedge would still go uncovered without Bug 2's fix.)

## §3 — What shipped (2 commits on `eb/main`)

| SHA | What |
|---|---|
| `442064d` | **V1 fix** — watchdog loops `while True` (not `while self.running`); exit only on cancellation from `stop()` (already awaited). Covers the startup race **and** base_bot's `running=False`-after-max-failures path. Adds `esportsbot_scan_stall_watchdog_armed` startup log. Updated 2 regression tests (running=False → task.cancel()) + added `test_stale_scan_fires_even_when_running_false`. |
| `1cd5611` | **V2 coverage** — mirror the (fixed) watchdog onto EsportsBotV2: set `_scan_start_mono` at the top of `scan_and_trade` (before the warmup gate), launch the task after `super().start()`, add a `stop()` override to cancel it, `esports_v2_scan_stall_watchdog_armed` log. + `TestScanStallWatchdogV2` (4 tests). |

Design: purely time-based — wraps/cancels **no** DB await (client-side cancellation of a DB await is what corrupts asyncpg — RULE ZERO rule 6 / S162). SIGTERM → systemd (`Restart=always`, `RestartUSec=10s`, `TimeoutStopUSec=30s`) restarts the shared process, recovering both bots.

## §4 — Deploy (succeeded, EB-scoped)

`bash deploy/deploy.sh` from eb-main → release `20260530_213432`:
- Preflight full `tests/unit/`: **2959 passed, 44 skipped, 6 xfailed** (3009 collected) in 205s.
- Atomic symlink swap; **MB/WB/ingestion confirmed untouched** (deploy cross-check + my PID re-check: mirror 325011 / weather 326931 / ingestion 346498 unchanged).
- Health check HEALTH_OK at 120s.

## §5 — Verification

- **PROVEN (startup-race fix):** both `esportsbot_scan_stall_watchdog_armed` (01:41:23) and `esports_v2_scan_stall_watchdog_armed` (01:41:31) present under PID 367955. Their *presence* is the production proof — the watchdogs now run their loop body, the exact thing that was impossible before.
- **PROVEN-BY-COMPOSITION (firing):** fire logic locked by unit tests (8 watchdog tests, incl. fires-when-running-false) + `Restart=always` verified. A real fire on a terminal wedge was being watched live at handoff (background monitor; result appended below if captured).
- **Could NOT force** a real terminal-wedge fire in-session (requires a real wedge). Operator verify:
  ```bash
  KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem; H=ubuntu@18.201.216.0
  ssh -i $KEY $H 'journalctl -u polymarket-esports --since "1 hour ago" | grep -E "scan_stall_self_restart|scan_stall_watchdog_armed"'
  ```

## §6 — Known limitation (documented follow-up, NOT a regression)

The watchdog watches scan **START** (`_scan_start_mono`). It catches the loop **stopping** (terminal wedge — the observed failure). It does **not** catch a loop that keeps *starting* scans but never *completing* them (start_mono refreshes → never stale). That cycling state has not been observed to persist (in both incidents it transitioned to terminal-silent within ~30min, which the watchdog catches). A completion-based signal would be stricter but adds warmup/early-return edge cases — deferred. Scope here matched the observed P0.

## §7 — Root cause of the HANG is SHARED (operator/MB) — unchanged + ACTIVE

The watchdog is a **backstop, not a cure.** The hang is shared-DB contention corrupting asyncpg connections, fully documented in `EB_COORDINATION_SCAN_STALL_DBLOAD.md` (D1/D2/E1/E2). **It is active and severe right now:** the manual-restore process (PID 366175, 01:15) re-wedged within ~6min (kill-switch + scan timeouts by 01:21). Expect the new process to re-wedge similarly → the (now-working) watchdog will fire and restart it ~15min after it goes terminal-silent. **Under sustained contention this becomes a periodic restart cycle until the shared root cause is fixed.** That is the loud-failure tradeoff vs. the prior silent 13h death — and the signal for operator/MB to action D1/D2.

## §8 — Carry-forward (priority order)

| # | Item | Owner | Notes |
|---|---|---|---|
| **P0** | Shared DB contention D1/D2 (the cure) | operator/MB | `EB_COORDINATION_SCAN_STALL_DBLOAD.md` — now with fresh active evidence (§7). Until fixed, EB will restart-cycle. |
| **P1** | **V2 `matched=0`** | EB | Carried from 2026-05-29 §6 — STILL OPEN. 72 upcoming → 0 Polymarket matches → 0 trades. Could not investigate while the process was wedged; revisit once scans complete steadily. |
| P2 | Completion-based watchdog signal | EB | §6 limitation — only if a cycling-without-completing state is ever observed to persist. |
| P2 | Calibrator valorant/dota2 silent (Anomaly B); LoL `n=0` (Anomaly A); `statement_timeout=30s` death-by-N-cuts math | EB | All carried from 2026-05-29 §6, untouched this session. |
| P3 | CLOSE-WAIT leak (PandaScore httpx) | EB | `EB_COORDINATION_CLOSE_WAIT_LEAK.md`. Note: the new V2 `stop()` override does NOT close pandascore/market_service (kept minimal); that leak is unchanged and still owned by this item. |

## §9 — Scope / what I did NOT touch

All work EB-owned: `bots/esports_bot.py`, `bots/esports_bot_v2.py`, their tests, EB-splinter deploy, this handoff. **No** shared-module edit (no base_bot.py — the watchdog stayed in the esports classes deliberately), **no** `/opt/pa2-shared/.env`, **no** master commit, **no** MB/WB resource touched. I restarted `polymarket-esports` twice (manual restore 01:15; deploy 01:41), both verified isolated. `base_engine/data/ingestion_error_capture.txt` left modified-untracked (carried from prior sessions; not mine to resolve).

## §10 — Next-session §0

```bash
cd C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main
git rev-parse --abbrev-ref HEAD      # eb/main
git log --oneline -4                 # expect 1cd5611, 442064d on top
readlink /opt/polymarket-ai-v2-esports   # → .../20260530_213432
# 1) Check whether the watchdog has been restart-cycling (shared contention persisting):
#    journalctl -u polymarket-esports --since "today" | grep -c scan_stall_self_restart
# 2) If contention eased (steady "Scan cycle done"): pick up P1 — V2 matched=0.
# 3) The cure is operator/MB (D1/D2 in EB_COORDINATION_SCAN_STALL_DBLOAD.md).
```
