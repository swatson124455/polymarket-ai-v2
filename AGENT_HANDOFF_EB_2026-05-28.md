# EB Session Handoff — 2026-05-28

**Date:** 2026-05-28
**Branch:** `eb/main` (HEAD `9f6de3d` pre-handoff, plus this commit)
**Worktree:** `C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main/`
**Master HEAD:** `09f4719` (= `b16a0c5` phase 4b + MB `59896b4`/`c16a4b6`/`20523cf` Bugs 15/16/17 + `09f4719` visibility — none deployed)
**Active VPS releases:**
- EB splinter: `/opt/pa2-esports-releases/20260528_141518` — carries `6fbba55` + `9f6de3d`
- Master: `/opt/pa2-releases/20260526_205630` (does NOT carry `b16a0c5`, `09f4719`, or MB Bug 15/16/17)

**Status at close:** Splinter deploy successful; phase 4b ORDER BY + cal visibility live in splinter. Master deploy of same 2 commits is pending — MB has 3 unshipped Bug commits sandwiched on master, so master deploy requires operator/MB authorization. Two new anomalies surfaced post-deploy.

---

## §1 — What landed this session

### Commits on `eb/main` (2 new)

| Commit | Layer | Purpose |
|---|---|---|
| `6fbba55` | Shared module — `base_engine/data/database.py:3630` | Phase 4b SELECT: add `ORDER BY pt_pnl.resolved_at ASC NULLS LAST` before `LIMIT 500`. Fixes 722-candidate / 500-LIMIT starvation that hid market `0x7abae048de..` (and ~221 others) from RESOLUTION emission. Regression guard added at `test_trade_events_resolution_backfill.py::test_phase4b_select_has_order_by_resolved_at` (16/16 tests pass). |
| `9f6de3d` | EB code — `bots/esports_bot.py:5570` | Promote per-game `BetaCalibrator` exception logger from `debug` → `info`. Pre-fix, valorant + dota2 fit failures were invisible under journald default. Pure observability; calibration logic unchanged. |

### Cherry-picks to `master` (2 new)

| Master commit | Source | Status |
|---|---|---|
| `b16a0c5` | `6fbba55` | Cherry-picked clean; **not deployed** (MB Bug 15/16/17 landed on top before deploy window). |
| `09f4719` | `9f6de3d` | Cherry-picked clean (auto-merging due to MB Bug 15/16/17 intervening commits); **not deployed**. |

### EB splinter deploy (no MB-side restart)

- Release: `20260528_141518`
- Restarts: `polymarket-esports` only (MB/WB/ingestion untouched)
- Carries: `6fbba55` + `9f6de3d` + everything on eb/main prior
- Health: Gate 1 (services active) ✓, Gate 2 (no error spam) ✓, Gate 3 (scan_ms) WARN — EB v2 cold-start ~5.5 min (expected per script doc); deploy continued
- Master release UNTOUCHED; per-bot services for MB/WB/ingestion still on `20260526_205630`

### Env / shared-state changes (Tier 2)

- `/opt/pa2-shared/.env` line 295: `ESPORTS_DISABLED_GAMES=cod` → commented out (`# ESPORTS_DISABLED_GAMES=cod  # 2026-05-28: enabled per operator directive`)
- Backup at `/opt/pa2-shared/.env.bak.20260528.enable-cod`
- Per operator directive ("do not disable any games")
- Effect: COD signals now flow through EB v1 prediction pipeline; `ESPORTS_V2_GAMES=cs2,lol` left untouched (V2 has no inference code for other games)

### Service restarts (Tier 2 manual)

At ~13:54 UTC, restarted all 4 services to pick up COD enable: `polymarket-mirror`, `polymarket-esports`, `polymarket-weather`, `polymarket-ingestion`. All came back active/running with zero errors. Confirmed COD pipeline active via `EsportsBot: no saved CoD model — will train on first scan` (also RL, SC2, R6) in startup log. Note: `polymarket-ai.service` is masked — architecture has split to per-bot services; my mental model going into the session was wrong (assumed monolithic).

---

## §2 — Diagnostics + verified findings

### Task #20 (phase 4b stuck markets) — RESOLVED

- Market `0x73d8e486cc..` not actually stuck — fully exited (ENTRY=EXIT=882.35, `remaining_size=0`), correctly filtered by phase 4b outer `> 0` predicate. P&L captured by EXIT event. Prior PM handoff misread it.
- Market `0x7abae048de..` IS stuck — 722 candidates competing for 500 phase 4b slots/cycle, no `ORDER BY` → PostgreSQL heap-order nondeterminism starves ~222 candidates. Market 2 was at position 527 by `resolved_at`.
- Candidate distribution: WB 454, MB 245, EB 22, Ensemble 1.
- Fix in `6fbba55` adds `ORDER BY pt_pnl.resolved_at ASC NULLS LAST` — drains oldest-first deterministically.
- Post-fix expectation: cycle 1 drains 500 oldest (all resolved before 2026-04-02 17:24), cycle 2 catches market 2 at position ~27.
- Phase 4b candidate count at session close: 716 (down from 722 — 6 emitted naturally during session).

### COD investigation — RESOLVED (intentional kill)

- `ESPORTS_DISABLED_GAMES=cod` in `/opt/pa2-shared/.env` since session s142 era (long-standing).
- Code gates: `bots/esports_bot.py:786` (`_can_open_position`) and `:2627` (similar gate) reject COD pre-prediction.
- Effect: 0 COD trades since 2026-03-22, 0 COD predictions added to `esports_prediction_log` since.
- Calibrator `insufficient_data game=cod n_samples=1` log spam was benign residual noise — historical sample, no new predictions to accumulate.
- Operator reversed disable 2026-05-28; restart applied; COD pipeline confirmed active.

### Sparse-game cohort — REAL finding (still partially open)

`esports_prediction_log` resolved-prediction counts in 73-day window:

| Game | Resolved | Per-game cal log in 14d | Status |
|---|---|---|---|
| cs2 | 185 | working (n=184) | OK |
| lol | 164 | working (n=163-164 pre-deploy; **n=0 post-deploy** — see §3) | anomaly |
| dota2 | 49 | ZERO events | silent fail |
| valorant | 33 | ZERO events | silent fail |
| cod | 1 | recurring insufficient_data | re-enabled, awaiting data |
| r6 | 0 | ZERO events | legitimately no data |
| sc2 | 0 | ZERO events | legitimately no data |
| rl | (not in table) | ZERO events | legitimately no data |

Dota2 (49 resolved) and valorant (33 resolved) are both well above `min_samples=15` but never appeared in any per-game calibrator log over 14d. They have 87 and 108 lifetime ENTRY rows respectively (sourced from trade_events SQL, infra-state). Visibility fix `9f6de3d` was shipped to surface the underlying exception, but post-deploy observation suggests `fit_from_db` may be HANGING (not raising) — see §3.

### Verification-driven REFUTATIONS (vs prior agent ranking)

- "Frozen per-game calibrators" — REFUTED. 92 cal_fit events across May 13-22; calibrators DO refit on scan cycles when min_samples met.
- "V2 retrain dual-cooldown gate" — REFUTED as blocker. Triple gate fires healthily: 7 retrains over 14d.
- "50-row training data trigger" — REFUTED for v2. Cadence proves retrain pipeline IS firing.

---

## §3 — NEW anomalies surfaced post-deploy (carry-forward priorities)

### Anomaly A: LoL per-game cal returns `n=0` while global pooled returns `n=164`

**Same process, same scan, SAME `esports_prediction_log` table:**
- Post-deploy first scan at 18:28:35 UTC: per-game `esportsbot_beta_cal_insufficient_data game=lol min_required=15 n_samples=0`
- Same scan at 18:29:06: `esportsbot_global_beta_cal_fitted game_counts={'lol': 164, 'cs2': 184, 'dota2': 49, 'valorant': 33, 'cod': 1, 'r6': 0, 'sc2': 0, 'rl': 0} n=431`
- Direct SQL (same query as per-game `fit_from_db`) at same time: 164 LoL rows in 73-day window.

**Pre-deploy LoL was `n=163-164`. Post-deploy LoL is `n=0`.** Either (a) SQLAlchemy param-binding regression in per-game `fit_from_db`, (b) session-state caching issue, (c) something about the path through `_check_monitoring_thresholds`. **Investigation needed before this LoL anomaly is dismissed** — LoL is the highest-traded game per trade_events row count.

### Anomaly B: 6 of 8 per-game calibrators silent (hang hypothesis)

**Post-deploy 30-min observation:** Only `lol` (insufficient_data) and `cs2` (fitted) fired in the per-game loop at `esports_bot.py:5557-5571`. The other 6 games (dota2, valorant, cod, r6, sc2, rl) produced zero log lines despite the visibility fix making `_fit_failed` info-level.

**Hypothesis:** `_cal.fit_from_db(db, _cal_game, ...)` is HANGING (not raising) at dota2 (next in iteration order after cs2). The outer `asyncio.gather(_fetch_positions(), self._check_monitoring_thresholds(db), return_exceptions=True)` at `esports_bot.py:1230-1234` eventually times out the whole scan. lol + cs2 complete before the cutoff; dota2 onward never finish. Visibility fix doesn't help because there's no exception.

**Evidence:** EsportsBotV2 `scan_ms=94812.2` (94.8s) and `scan_ms=161924.7` (161.9s) in `Slow scan cycle` warnings post-restart — well above typical 30-60s budget.

**Suggested next-session fix:** Wrap `_cal.fit_from_db(...)` in `asyncio.wait_for(..., timeout=N)` so the hang becomes a `TimeoutError` that the now-info-level visibility fix catches. Single-commit change in `esports_bot.py:5559`.

---

## §4 — Operator decisions executed this session

| Decision | Status |
|---|---|
| Investigate Task #20 (2 stuck markets) | ✓ Done — root cause identified, fix shipped |
| Bottleneck audit | ✓ Done — verified findings, refuted agent's top 3 |
| Investigate sparse + COD | ✓ Done — COD = intentional kill; sparse-game = silent fail |
| Enable COD ("do not disable any games") | ✓ Done — env edit + 4-service restart |
| Ship phase 4b ORDER BY on eb/main | ✓ Done — `6fbba55` |
| Cherry-pick phase 4b to master | ✓ Done — `b16a0c5` |
| Ship visibility fix on eb/main | ✓ Done — `9f6de3d` |
| Cherry-pick visibility to master | ✓ Done — `09f4719` |
| Deploy EB splinter | ✓ Done — `20260528_141518` |
| Deploy master | **DEFERRED — MB session/operator owns timing** (MB Bug 15/16/17 sandwiched) |

---

## §5 — Operator decisions still open (priority order)

| # | Decision | Owner | Notes |
|---|---|---|---|
| 1 | Master deploy of `b16a0c5` + `09f4719` (carries MB Bug 15/16/17 too) | Operator / MB session | Phase 4b ORDER BY won't fully drain backlog for MB+WB+ingestion paths without master deploy. |
| 2 | Ship `asyncio.wait_for` wrapper for `fit_from_db` (force hang → exception) | EB session | Single-commit fix; needed for the visibility fix to actually catch the dota2/valorant failures. |
| 3 | Investigate LoL `n=0` anomaly (per-game cal vs global pooled disagree) | EB session | High priority — LoL is highest-traded game; an undetected query bug could affect calibration. |
| 4 | Task #21a: 6 active-loop accounting drift markets | EB session (deferred) | Bounded waste (6/500 slot tax). |
| 5 | Task #21b: 17 historical-overflow markets | EB session (deferred) | Already excluded from bot_pnl.py "clean" total. |
| 6 | Task #19: CLOSE-WAIT leak deep dive | EB session | Deferred on `%steal=47%` (still above 25% gate). |
| 7 | Task #5: integration test hardening for phase 4b ordering | EB session (future) | Current regression guard is structural (`inspect.getsource`), not behavioral. |
| 8 | Re-enable consideration: `ESPORTS_V2_GAMES=cs2,lol` (still narrow) | Operator | Not touched this session; V2 has no inference code for other 6 games — expanding would break. |

---

## §6 — P&L snapshot at session start

- **12h window:** 1 entry, 0 exits, 2 resolutions (+$320.27, +$460.00), Net +$785.44
- **720h (30d) window:** 33 entries, 3 exits, 26 resolutions
- **All-time clean realized:** $-5,405.37 (17 contaminated markets excluded)
- **All-time raw realized:** $-6,322.40

All numbers sourced from `bot_pnl.py EsportsBot 12` / `EsportsBotV2 12` / `EsportsBotV2 720` (family-union).

---

## §7 — Post-deploy verification gaps (operator monitor items)

When master deploy lands (whenever MB session deploys), the following should be checked:

1. **Phase 4b ORDER BY active for MB+WB+ingestion paths:** confirm `RESOLUTION` event for market `0x7abae048de..` appears within 2 phase 4b cycles. Grep `journalctl -u polymarket-ingestion` for `trade_events_resolution_backfill` and watch `phase4b=` count behavior.
2. **Phase 4b candidate count:** re-run COUNT(*) query — should drain below 500 within a few cycles. If stays >700 for >24h, LIMIT raise to 2000 becomes a candidate task.
3. **Over-size rejected markets:** baseline is 6. Growth → Task #21a promotes to priority.

For EB splinter (already deployed):

4. **Per-game calibrator coverage:** grep `journalctl -u polymarket-esports --since "<deploy time>"` for `beta_cal_fit_failed` / `beta_cal_insufficient_data` / `beta_cal_fitted` per game. If dota2/valorant still don't appear, Anomaly B hypothesis confirmed → ship asyncio.wait_for wrapper.
5. **LoL n=0 anomaly:** monitor whether subsequent scans show LoL `n=164` or stuck at `n=0`. If stuck, Anomaly A is a real bug.

---

## §8 — Next-session entry protocol

```bash
# Worktree silo
cd C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/eb-main
git rev-parse --abbrev-ref HEAD                     # must print: eb/main
git log --oneline -6                                # confirm 9f6de3d + 6fbba55 + this handoff are present

# Confirm master state
cd C:/lockes-picks/polymarket-ai-v2
git log --oneline master -8 | grep -E '09f4719|b16a0c5|20523cf|c16a4b6|59896b4'

# Check whether MB session has deployed master since handoff
KEY="C:/Users/samwa/.ssh/LightsailDefaultKey-eu-west-1.pem"
ssh -i "$KEY" ubuntu@18.201.216.0 "ls -ltd /opt/pa2-releases/* | head -3"
# If newest is past 20260526_205630, check whether it contains b16a0c5 + 09f4719:
ssh -i "$KEY" ubuntu@18.201.216.0 "grep -c 'ORDER BY pt_pnl.resolved_at' /opt/pa2-releases/<latest>/base_engine/data/database.py"

# VPS health (skip non-critical work if %steal > 25%)
ssh -i "$KEY" ubuntu@18.201.216.0 "uptime && mpstat 1 3 | tail -3"

# Priority entry points (in order):
# 1) Anomaly B: ship asyncio.wait_for wrapper for fit_from_db in esports_bot.py:5559
#    (lets visibility fix catch dota2/valorant hangs)
# 2) Anomaly A: investigate LoL n=0 — diff per-game fit_from_db SQL execution
#    against the global pooled fitter SQL (both at esports_bot.py:5557+ and 5573+)
# 3) Confirm phase 4b drain via journal grep (depends on master deploy timing)
# 4) Task #21a — investigate one of the 6 over-size markets to find divergence pattern
```

---

## §9 — Splinter charter status (unchanged)

EB session continues to own `eb/main` splinter end-to-end per `feedback_eb_owns_splinter_end_to_end.md`. Shared-module fix (`database.py`) shipped on eb/main without prior MB signoff per the refined RULE ONE-A. Master cherry-picks are operator-authorized (`b16a0c5` and `09f4719` explicit per this session's operator directives). MB owns master deploy timing; my commits sit alongside MB Bug 15/16/17 awaiting that decision.

---

## §10 — Loose ends (not blocking)

- `base_engine/data/ingestion_error_capture.txt` shows uncommitted modification in eb-main worktree. Likely scratch state from prior session — not a code file, no impact. Leaving as-is.
- VPS `%steal` averaged 47-48% across session. Multiple operations were slower than ideal but no failures. Task #19 (CLOSE-WAIT) remains deferred until VPS load drops below ~25% steal.
- Architecture change discovered mid-session: `polymarket-ai.service` is masked; per-bot services (`polymarket-mirror` / `polymarket-esports` / `polymarket-weather` / `polymarket-ingestion`) run separately. Older session memory referencing monolithic `polymarket-ai` should be considered stale.

---

## §11 — Post-handoff SSH spot-check (~20h after deploy) + correction flags

> Independent verification run at handoff-commit time, executing the check §7 item 4 specified ("if dota2/valorant still don't appear → Anomaly B confirmed"). Journal counts only (infra-state, cited from `journalctl`); no trading-state numbers introduced. Findings are spot-checks to confirm next session, not overrides of §2/§3.

**Deploy confirmed live.** Running root `/opt/polymarket-ai-v2-esports` → symlink → release `20260528_141518`; deployed `esports_bot.py` carries the `S231` marker. EB process `MainPID=276810`, active/running. (Verify the EB splinter against this symlink root — **not** `/opt/pa2-esports-releases/current`, which does not exist as a symlink on this host.)

**`esportsbot_beta_cal_*` activity, ~20h window since deploy (`journalctl -u polymarket-esports`):**

| Outcome | Count | Games |
|---|---|---|
| `fit_failed` (the newly info-promoted branch) | **0** | — |
| `fitted` | 4 | `lol` |
| `insufficient_data` | 4 | `cod` |
| (any calibrator line for valorant/dota2) | **0** | — though `game=valorant` appears 11× in non-calibrator log contexts, `game=dota2` 1× → both ARE being scanned |

**Implications for the carry-forward:**

1. **Anomaly A (LoL `n=0`) — likely a cold-start transient; downgrade open-decision #3.** The 30-min post-deploy snapshot in §3 caught LoL at `insufficient_data n=0` immediately after restart. Over the full 20h window LoL recovered to `fitted` ×4. So the `n=0` was most plausibly cold-start / first-scan state, not a standing per-game-vs-pooled query bug. Confirm before spending a session on it: if LoL is now consistently `fitted`, decision #3 closes.
2. **Anomaly B (valorant/dota2 silent) — confirmed by §7.4's own criterion, AND the exception hypothesis is now ruled out.** 20h post-deploy, valorant/dota2 produced zero calibrator lines of any kind while being scanned, and `fit_failed` fired **0 times**. Since the info-promotion would now surface any raised exception, the failure is **not an exception** — consistent with §3's hang hypothesis OR with the two games never entering the per-game loop at `:5557-5571`. Next session: read the loop population path above `:5557` to confirm which, before assuming a hang.
3. **⚠ Open-decision #2 (`asyncio.wait_for` wrapper) CONFLICTS with a codified ban — do NOT ship as written.** RULE ZERO feedback rule 6 ("No `asyncio.wait_for` on DB operations") forbids wrapping DB calls in `asyncio.wait_for`: client-side `CancelledError` corrupts asyncpg's protocol state machine → permanent `InFailedSQLTransactionError` loops (origin S162, ~200 errors/10min, reverted). `fit_from_db(db, ...)` is DB-backed, so the §3/§5 recommendation lands squarely on the ban. **Correct path instead:** (a) determine whether the hang is in the DB query — if so, server-side `SET statement_timeout` (already 30s in `_SemaphoreSession.__aenter__`) should already bound it; a hang persisting past 30s implies `fit_from_db` isn't routing through that session, which is the real bug to fix; (b) if the hang is in CPU-bound `BetaCalibrator` math rather than the query, neither `wait_for` nor `statement_timeout` applies and it's a different fix entirely. Resolve the mechanism before shipping any timeout.
