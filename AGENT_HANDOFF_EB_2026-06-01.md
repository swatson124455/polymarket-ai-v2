# EB Session Handoff — 2026-06-01

**eb/main HEAD:** `46a39d6`
**Master HEAD:** `e52b11b` → deployed `20260531_203534` (all services)
**EB splinter:** `20260531_203235` (E1 + A/B/C/D)

---

## Live state at handoff

- EB: PID 424260, uptime ~1min (just restarted — watchdog fired). 1 fire today.
- Ingestion: 1 "Can't reconnect" in last hour. Not zero.
- Both expected: `base_bot.py:811` is still active (see §1).

---

## §1 — The one thing that matters most

**`base_bot.py:811` is the primary corruption source.** Session review identified this. Read `EB_COORDINATION_SCAN_STALL_DBLOAD.md` "CRITICAL ADDITION" section in full before touching D1/D2.

Short version: `asyncio.wait_for(scan_and_trade(), timeout=90s)` fires `CancelledError` into a mid-flight asyncpg call on every bot, every scan >90s. This is the S162 mechanism — same class as the scanner rule-6 fix, but every bot, every contention event. Ingestion pressure is the trigger; this line is the corruption. Fixes A/B/C/D+E1 reduce spread; they don't eliminate it.

**Not a hot-patch.** Requires audit of all DB calls in `scan_and_trade()` for `_SemaphoreSession` coverage first. All-14-bot blast radius. Own it on eb/main, propose as master cherry-pick.

---

## §2 — Everything that shipped this session (don't redo)

| What | Where | Status |
|------|-------|--------|
| Watchdog V1+V2 (3 bugs) + os._exit | EB splinter | ✅ proven in prod |
| Scanner rule-6 (wait_for on DB removed) | EB splinter | ✅ |
| A: PM 30s backoff, B: discard corrupt sessions, C: get_raw_session, D: pool 10/2 | EB splinter + master | ✅ |
| E1: main.py stale-bot → os._exit | EB splinter + master | ✅ |
| base_bot:811 flag, scan-progress P1, flush test, harness spec | eb/main docs | ✅ |

Full detail: `AGENT_HANDOFF_EB_2026-05-31.md`.

---

## §3 — Next actions (priority order)

**1. `base_bot.py:811` audit** — Map every DB call in `scan_and_trade()` and confirm each goes through `_SemaphoreSession`. Produce the coverage map. If covered: propose removing `wait_for`. This fixes the root corruption source.

**2. Scan-progress watchdog (P1)** — Current watchdog fires on scan-START age. Under sustained contention, scans start + timeout → start_mono refreshes → watchdog stays silent → bot cycles indefinitely. Add: if zero "Scan cycle done" in 30 min → `os._exit`. Separate commit.

**3. V2 matched > 0** — After base_bot:811 is fixed and contention drops: check `journalctl | grep esports_v2_scan_funnel`. If still 0 after clean scans: matcher-logic bug (separate investigation).

**4. Scanner rule-6 harness** — Simulate `wait_for(asyncpg_call, timeout=0.001)`, confirm `InFailedSQLTransactionError` post-cancellation, then propose server-side `statement_timeout` replacement.

---

## §4 — §0 entry commands

```bash
cd C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main
git log --oneline -3  # expect 46a39d6 on top

# Read the critical flag before anything else:
# EB_COORDINATION_SCAN_STALL_DBLOAD.md — "CRITICAL ADDITION" section

KEY=~/.ssh/LightsailDefaultKey-eu-west-1.pem; H=ubuntu@18.201.216.0
# Watchdog fire rate (should drop after base_bot:811 fixed):
ssh -i $KEY $H 'journalctl -u polymarket-esports --since today | grep -c scan_stall_self_restart'
# Ingestion cascade (should hit zero after base_bot:811 fixed):
ssh -i $KEY $H 'journalctl -u polymarket-ingestion --since "1 hour ago" | grep -c "Can.t reconnect"'
# V2 scan funnel:
ssh -i $KEY $H 'journalctl -u polymarket-esports --since today | grep esports_v2_scan_funnel | tail -5'
```
