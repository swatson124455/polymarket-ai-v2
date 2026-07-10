# MirrorBot Rebuild — Living State / Handoff (docs/MB_STATE.md)

**Last updated:** 2026-07-10 · **Branch:** `claude/mirrorbot-persistence-check-oc02tk` (PR #1)
**Read first:** `CLAUDE.md` (binding directives), `MB_REBUILD_PLAN.md` (the plan + operator decisions), then this file.
**Protocol for updating this file:** `docs/MB_HANDOFF_PROTOCOL.md`.

---

## 1a. CURRENT STATE (2026-07-10 — read this first, §1b is background)

**The investigation PIVOTED (operator-directed, 2026-07-09/10): stop testing on
rejected-signals leftovers; pull COMPLETE per-bet trader histories from the
public Polymarket APIs and find COPYABLE traders directly.** Chain of results:

1. **Run 1 of `scripts/find_copyable_traders.py`** (496 leaderboard addrs, full
   histories, qualify-on-P1/judge-on-P2, chain-verified 20/20): first broadly
   positive signal of the whole investigation — P1-qualified traders held
   **+2.3pts P2 edge, P=0.962, across 1,065 markets** (descriptive; PNL-collider
   -tainted, politics cell post-hoc — NOT promotable). Primary was UNDERPOWERED:
   329/496 histories TRUNCATED at the 20k cap and the VOL list is by definition
   the deepest whales → primary cell starved to 1 trader. Named strong-both-half
   candidates emerged (`0x6bab41a0dc` 109 P2 mkts +0.089 P=0.99; `0xd1acd3925d`;
   `0xa6a856a8c8` 593 P2 mkts +0.025 P=0.89).
2. **Grading redesigned with the operator into the WALK-FORWARD rule**
   (`scripts/walkforward_copy_traders.py`) — the deployable strategy tested as
   it would run: HIRE on lifetime record at monthly reviews (>=25 mkts, >=60d
   span, P(edge>0)>=0.90), FIRE only on statistically convincing RECENT decay
   (trailing 90d, >=10 mkts, P(edge<0)>=0.90 — past glory can't shield bleed,
   a GOAT's cold month can't convict), GRADE only post-hire bets. Improvers
   caught, goats not churned, has-beens cut. Locked review grid (anchor
   2025-01-01) + ±15d robustness grids; knowledge gated by resolved_at.
   Primary = VOL-sourced non-truncated universe. Self-test proves all four
   personas (goat/bleeder/improver/streaker).
3. **Pre-run tinker (operator: "tinker before spending 7 hours")**: (a) bot/
   market-maker exclusion `--hft-max-rate 200` bets/day — bots were ~all the
   truncation and most of the download cost, and are mechanically uncopyable;
   (b) **gamma resolution backfill** (`scripts/backfill_resolutions_gamma.py`)
   — DB label coverage was 24%; backfill lifts to ~80-95% via a local JSON the
   graders merge under the DB map (DB wins); (c) truncated caches re-deepen
   when --max-bets rises (`--deepen vol`).
4. **A 3-stage detached pipeline is RUNNING on the VPS** (gamma backfill →
   deepen humans-only → walk-forward). Logs: `/tmp/gamma_backfill.log`,
   `/tmp/copyable3.log`, `/tmp/walkforward.log`. Cache: `/tmp/copyable_cache`.
   THE NEXT ACTION IS READING `/tmp/walkforward.log` (§5 decision tree).

Fill-quality gate (`scripts/backtest_copyable_fills.py`, audited coarse model,
real ask crossed) is built and waiting for whatever roster passes. Per-fill
OrderFilled chain audit is MANDATORY before any money decision on a named
trader. Everything committed/pushed on this branch; 29+ script unit tests green.

## 1b. Background (2026-07-09 and earlier)

MirrorBot's old whale-copy strategy is confirmed dead (no measured edge). The old bot is **paused to paper** (real money off, 2026-07-05) but still collecting signal data. A **clean-silo rebuild** (`mirror_v3/`) is scaffolded, tested, and ready to deploy — safety spine only, strategy slot deliberately empty behind an acceptance gate. **The v3 whale trader-ranking engine (`bots/mirror_scoring/`) reportedly FAILED its Stage-1 gate** (prior-session handoff citing `172d72a`: 2 cutoffs FAIL, placebos 0/20 and 1/20). A 61-agent adversarial review (2026-07-09, this session) established two things about that evidence: (a) **commit `172d72a` is NOT in this clone — the FAIL is currently hearsay** until the commit/branch is recovered and reviewed; (b) **the in-repo validation harness is confirmed CIRCULAR** (admission is selected on post-cutoff outcomes, then "validated" on the same post-cutoff signals — a false-PASS machine), which mechanically explains the earlier miscalibrated "PASS" and means **a PASS from `bots/mirror_scoring/validation.py` must never clear anything**. The circularity biases toward PASS, so the reported FAIL — if the recovered code checks out — is if anything *stronger*. **The lead instrument now is the TAIL BACKTEST** — `scripts/backtest_tail_leaderboard.py` (copy-everyone at the operator-measured ~10s lag, per category, market-clustered bootstrap, pre-registered primary cell, pre-spread screen semantics), hardened against all 26 confirmed review findings; `scripts/check_trader_persistence.py` is the SECONDARY corroboration (its shuffle null is anti-conservative under shared-market overlap — labeled as such). Both await an operator VPS run. The other strategy direction is a **sharp-line reference** (compare whale entries to an efficient outside price); its vendor-independent core is built and tested, waiting on an OddsPapi paid tier for sports data. Everything is on GitHub; nothing is deployed except the pause.

## 2. Current system state (verified)

- **Old MB:** paper mode. `SIMULATION_MODE=true, CANARY_STAGE=0, CANARY_AUTO_ADVANCE=false` appended to `/opt/pa2-shared/.env.mirror` and service restarted. Still writes `mirror_rejected_signals` (the rebuild's data).
- **VPS is LIVE-config by default otherwise** — before the pause it was `SIMULATION_MODE=false, CANARY_STAGE=4`, auto-advance armed by code default. The env-drift-to-live risk is real; `mirror_v3` env_guard exists specifically to end it.
- **Data tier (measured 2026-07-05, `docs/m0_db_results_2026-07-02.md` + this session):** ~5.06M labeled whale signals — **crypto 73%, sports 17%, esports 5%**, rest <5%. orderbook_snapshots 37.7M (aggregated buckets, NOT full L2). mirror_rejected_signals 17.5M. gate-labeled 286k. precise-fill ladder rows (shadow_fills) 12,713.
- **Tests:** 423+ green on the merged tree; each new module ships its own suite.

## 3. Key decisions (all in MB_REBUILD_PLAN.md, do not re-litigate)

1. **Acceptance gate:** no strategy ships without passing the fill-replay backtest (precise model) + edge check. "Algo proposes, backtest disposes."
2. **Clean silo** (`mirror_v3/`), new identity `MirrorBotV3`, own systemd unit, allowlist env, same VPS.
3. **Paper-first, real sizing** (code defaults, not the old flat-$1).
4. **Strategy = sharp-line reference, sports-first.** Crypto (73% of volume) is a **latency** edge → un-tailable on our 60s delay → expected to FAIL the gate (use the harness's latency model as the kill test). Sports/esports are **knowledge** edges → tailable. Pinnacle for sports, OddsPapi for esports.
5. **clob_adapter fill-price fix** landed (S250, `d3d2369`) — defensive, paper-unchanged.

## 4. What's built (all pushed)

| Area | Location | State |
|---|---|---|
| Clean silo | `mirror_v3/{env_guard,guards,state_restore,run}.py` | scaffold + 22 tests; boots, restores fail-closed, strategy idle |
| Silo deploy | `deploy/polymarket-mirror3.service`, `deploy/env.mirror3.example` | ready; needs real `DATABASE_URL` on first install |
| Acceptance gate | `bots/mirror_backtest/{fill_models,replay,gate,data_access}.py` | dual-model harness + 19 tests; DB-execution gated on M0-DB |
| Sharp-line core | `bots/mirror_backtest/sharp_reference.py` | no-vig, point-in-time, gate rule + 19 tests; OddsPapi seam env-key-only |
| Scoring engine | `bots/mirror_scoring/` (from `mb-formula-review`) | 45 tests; runner unblocked (`8ea683d`); validate run pending |
| M0-DB verify | `scripts/verify_salvage_data.py` | read-only; cascade bug fixed |
| Tail backtest (PRIMARY) | `scripts/backtest_tail_leaderboard.py` | read-only; hardened vs 2026-07-09 review (strictly-after-print fills, per-slice coverage gate, fee-scaled pts + ret/$ units, paired tax, pre-registered primary cell `cat:sports@10s` + 30s lag-agreement, LIMIT sentinel, `--sample`/progress, stage/side mix printed, PASS* = pre-spread screen only); self-test + 14 unit tests green; **awaiting operator VPS run** |
| Copyable-trader search | `scripts/find_copyable_traders.py` | full-history P1/P2 grader; leaderboard universe, time-windowed pagination, bot exclusion, gamma-label merge, deepen-aware cache; run-1 results in §1a |
| Walk-forward grader (LEAD) | `scripts/walkforward_copy_traders.py` | hire-on-lifetime / fire-on-recent-decay / grade-post-hire; locked+shifted review grids; resolved_at knowledge gating; primary=VOL non-truncated |
| Gamma label backfill | `scripts/backfill_resolutions_gamma.py` | resumable; lifts label coverage ~24%→80%+; wording-independent token labels; never guesses split/open markets |
| Fill-quality gate | `scripts/backtest_copyable_fills.py` | audited coarse model at real ask for a NAMED roster; SURVIVES/FILL-KILLED/NO-BOOK; runs after a walk-forward PASS |
| Persistence check (secondary) | `scripts/check_trader_persistence.py` | read-only; reworked verdicts (SIGNIFICANT-BUT-SMALL; NULL can't discard underpowered-significant cutoffs), UNION-ALL planner-safe SQL, estimand-faithful first-entry selection, `--since/--until`, LIMIT sentinel; anti-conservative-null caveat printed; self-test + 11 unit tests green; **awaiting operator VPS run** |
| Operator runbooks | `docs/VPS_RUNBOOK_2026-07-02.md`, `deploy/mb_vps_oneshot.sh` | one-paste checks; mktemp-safe |

## 5. Open threads / what's next

- **[NOW — decision tree for /tmp/walkforward.log]** When the running pipeline
  finishes (check: `tail -3 /tmp/gamma_backfill.log /tmp/copyable3.log /tmp/walkforward.log`):
  - **PASS** → the rostered addresses are the deliverable. Next: per-fill
    OrderFilled chain audit on them, then `scripts/backtest_copyable_fills.py
    --from-json` fill-quality gate, then operator decision on a v3 forward
    paper deploy. NOT a live deploy.
  - **FAIL-TERMINAL** → copy thesis closed retrospectively on the best data
    that will ever exist; only a forward shadow test could revive it. Say so.
  - **UNDERPOWERED / INCONCLUSIVE / NO-DATA** → check in order: label coverage
    actually achieved (gamma report), slug-key residue (add slug lookup pass if
    big), primary-universe size after bot exclusion (widen --universe if thin),
    THEN rerun. Do not loosen thresholds — widen data.
  - Interrupted pipeline? Every stage is resumable — rerun the same chained
    command; caches make completed work free.
- **[rules that BIND the next session]** No rework-then-retest until an
  instrument passes; primary verdicts only from the pre-registered cells
  (walk-forward primary = VOL-sourced non-truncated pooled edge); descriptive
  cells (incl. politics' +5pts from run 1) are leads, not results; numbers get
  cited with coverage/sample qualifiers.
- **[superseded]** The tail backtest + persistence check below remain valid
  instruments but are SECONDARY to the walk-forward since the 2026-07-10 pivot
  to full-history data.
- **[operator, GATING — PRIMARY] Run the tail backtest** — the direct "can we tail them
  reasonably?" test (operator's framing, 2026-07-09), hardened per the 61-agent review:
  ```
  # bounded first run (quiet window; the resolution filter is not index-backed):
  cd /opt/polymarket-ai-v2 && sudo -u polymarket env PYTHONPATH=/opt/polymarket-ai-v2 \
    venv/bin/python scripts/backtest_tail_leaderboard.py --by-category \
    --since 2026-06-01 --sample 20000 | tee /tmp/tail_backtest.log
  # full run after the bounded one behaves:
  ... scripts/backtest_tail_leaderboard.py --by-category | tee /tmp/tail_backtest.log
  ```
  Discipline is built in: PRIMARY CELL is pre-registered (`cat:sports` @ 10s, 30s
  lag-agreement required); every other cell is descriptive (multiplicity). PASS* is a
  **pre-spread screen** — a FAIL is final for a slice; a PASS* only licenses the
  PRECISE fill-model gate (`bots/mirror_backtest/gate.py`, shadow_fills ladders) per
  MB_REBUILD_PLAN §2. Read per-slice `cov` and `nr%` before believing any row.
- **[operator, secondary] Run the persistence check** — corroboration only (its shuffle
  null is anti-conservative under shared-market overlap; the output says so):
  ```
  ... scripts/check_trader_persistence.py --by-category --since 2026-03-01 | tee /tmp/persistence.log
  ```
  **Hard rule stands:** do NOT rework-then-retest the ranking until an instrument passes
  (p-hacking); any real verdict needs multi-cutoff agreement, and NULL/MIXED semantics
  are now strict (an underpowered-but-significant cutoff blocks NULL).
- **[operator, evidence] Recover `172d72a`** (the calibrated-permutation FAIL run — likely on
  the `mb-formula-review` lane): push the branch/commit to origin so the Stage-1 FAIL becomes
  auditable instead of hearsay, and grab the 3rd cutoff (05-10) result from `/tmp/val_all.log`
  and record it here. Until then the FAIL is provisional (direction likely correct — see §7
  circularity note: the in-repo harness biases toward PASS, and it still failed).
- **[standing, DO NOT] Re-run `--stage validate` (`deploy/mb_vps_oneshot.sh` / 
  `scripts/mirror_scoring_run.py`) for any decision** — the in-repo kill criterion is
  confirmed circular (§7). A PASS from it means nothing; running it wastes a 300s scan.
- **[gated rework backlog — only if an instrument passes]** catalogued by the 2026-07-09
  review, NOT applied (hard rule above): (1) three-way split fit/select/validate in
  `mirror_scoring` (kills the circularity); (2) per-category stratified scoring + BH
  (MIN_EVENTS=12 pooled starves sparse sports/esports traders); (3) cluster by event, not
  condition_id (neg-risk siblings inflate confidence; schema has no event id — needs one);
  (4) two-group contrast via cluster regression, not the spread/2 recentering
  (anti-conservative); (5) `DELTA_SECONDS` from measured `feed_lag_p95_s` (~10s), not 60.
- **[operator] OddsPapi paid tier** — confirm sports coverage + that `ODDSPAPI_API_KEY` is set in the VPS env (presence only). Then the sharp-line engine wires to live data.
- **[build, blocked on above] Sports sharp-line pipeline:** live OddsPapi fetch, sports team-name → Polymarket condition_id matcher (esports matcher exists in EB, sports is net-new), offline backfill of `sharp_prob` onto signals, then run through the gate.
- **[build, unblocked] Crypto kill-test:** run crypto signals through the harness at realistic latency to confirm the latency-trap hypothesis and formally drop crypto.
- **[build, unblocked] v3 rejection logging + RTDS plumbing** so the silo collects its own signal stream (then old MB can be fully stopped, not just paused).
- **[decision] Merge/PR hygiene:** master is current; direct master pushes are operator-gated by the sandbox.

## 6. Cross-session coordination

- **EB (esports)** owns the OddsPapi vendor integration (esports). Odds-capability report is in this session's history; EB has a team-alias matcher MB can reuse. Registry publish (`EB_ODDS_CAPABILITY.json`) offered, not yet committed.
- **mb-formula-review** branch owns the *statistical* scoring lane; MB owns execution/guards/gate. Three statistical findings (condition-vs-event clustering, validation statistic, EB-shrink pool) handed over as recommendations, not applied.
- **MB has priority** on shared resources (CLAUDE.md). Old poisoned project lives at parent `C:/lockes-picks/` — OUT OF SCOPE.

## 7. Landmines (do not trip)

- **`bots/mirror_scoring/validation.py` is a false-PASS machine** (confirmed 2026-07-09, adversarially verified): `_UNIVERSE_SQL` has no time bound, BH admission keys on the post-cutoff test half, and the "out-of-sample" rejected-signals set shares the same post-cutoff (trader, market, outcome) randomness. Any PASS it emits is selection noise — never clear the UNVERIFIED label with it, never cite it in a go decision. (FAILs are directionally credible: the bias runs the other way.)
- **order_gateway neg-risk block** no-ops for MB by accident (CLAUDE.md "DORMANT LANDMINE"). "Repairing" the index re-creates Bug 14 (election blackout). Leave it.
- **Do NOT add a `neg_risk=True` filter** anywhere (CLAUDE.md).
- **`mirrored_trades` is bookkeeping, not a guard** — the real same-side dedup is the `_open_positions` scan.
- **CANARY_AUTO_ADVANCE unset → true** by code default. Any live-capable path must set it false explicitly.
- **orderbook_snapshots is aggregated buckets, not L2** — precise replay needs `shadow_fills.book_snapshot`.
