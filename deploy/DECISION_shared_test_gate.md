# DECISION — deploy.sh shared-test gating (WORK_PROGRAM WI-2b)

**Status:** ACCEPTED (option B) — S235, 2026-05-30.
**Scope:** `deploy/deploy.sh` preflight test gate. Process/policy decision, not a code change.

## Context (verified S235)

`deploy/deploy.sh:46` runs the **entire** `tests/unit/` suite as a deploy preflight:

```bash
python -m pytest tests/unit/ --tb=short -q 2>&1 || { echo "ABORT: Unit tests failed — deploy cancelled"; exit 1; }
```

and `deploy.sh:213` restarts **all four** services (`polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion`). Consequence: **any** bot's red unit test blocks **every** bot's deploy. This is what blocked the S234 Bug-21 (MirrorBot) deploy — 7 EsportsBot test failures, root-caused as time-dependent test rot (`195a6fc`).

`tests/unit/` is a flat directory of 147 `.py` files. Only 19 are bot-name-prefixed (7 mirror / 10 esports / 2 weather). ~12 more esports tests are **not** named "esports" (`test_dota2_model`, `test_valorant_model`, `test_aligulac_client`, `test_elo_v2`, `test_glicko2_v2`, `test_openskill_v2`, `test_trinity`, …).

## Options considered

- **A — per-bot test gating.** Gate the MB deploy on MB + shared tests only. **Rejected (for now):** unsafe-cheap. A naive `pytest -k "not esports and not weather"` filter silently drops the ~12 non-prefixed esports tests → real coverage loss on every MB deploy. A *correct* split needs either per-bot pytest markers applied across all 147 files (large cross-bot change) or a hand-maintained deny-list in `deploy.sh` (which silently rots when new tests are added without updating it). The failure mode of A is **silent loss of deploy-gate coverage** — worse than the failure mode of B (a visible, bounded deploy block).
- **B — accept shared-test gating (CHOSEN).** All bots' unit tests must be green to deploy any bot. The remedy for a block is to fix/de-rot the failing test. S234 proved the tax is bounded: the fix was 1 line (de-rot freshness seeds relative to `now`). No `deploy.sh` change.
- **B+ — B plus a proactive test-debt backlog.** See WI-17 below. Deferred to its own scoped build; gated on the escalation trigger.

## Decision

Adopt **B**. The all-`tests/unit/` deploy gate stands as intentional shared-test-debt policy. Rationale: maximum deploy-gate safety, zero new code/complexity, and the observed tax is rare and cheap to clear. A's complexity is not justified at the current block frequency (~once, S234).

## Escalation trigger → promote B+ (WI-17)

B is acceptable **only while shared-test blocks stay rare**. Explicit promotion gate:

> **If the shared-test deploy gate blocks an MB deploy more than once per month for two consecutive months, WI-17 (proactive test-debt backlog) promotes to next-priority work.**

Without an explicit trigger, "trust bot sessions to fix rot promptly" can fail silently. This trigger converts that into a measurable condition. Track block events in the WORK_PROGRAM change log (date + which bot's test + how it was cleared) so the monthly count is auditable.

## WI-17 scope (the B+ mechanism, to build when promoted)

Proactive test-debt backlog so test rot surfaces *before* it blocks a deploy:

1. **Weekly cron** runs the full `tests/unit/` suite outside any deploy context.
2. **Per-test failure age tracking** — record first-seen-failing date per test (persist across runs).
3. **Any test failing >7 days → CRITICAL alert**, routed to the **owning bot** (by file prefix where possible; a maintained `test → bot` mapping for the non-prefixed cases — same mapping problem as option A, but here a stale mapping only mis-routes an alert, it does not silently drop deploy-gate coverage).
4. **Owning bot's next session** gets an "address failing tests before deploy" gate.

Estimated ~50 lines + a routing config. Owns its own session when promoted.

## Rollback / reversal

Pure policy + docs. To reverse: delete this file and revisit WI-2b in WORK_PROGRAM. No deployed artifact, no `deploy.sh` change to revert.
