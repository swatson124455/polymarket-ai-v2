# MirrorBot Rebuild — Living State / Handoff (docs/MB_STATE.md)

> **⚠ BRANCH-VERSIONED DOCUMENT — the copy you are reading may be stale.**
> This doc advances on session branches; master's copy lags until the
> end-of-session docs-sync PR merges. Before trusting ANY fact here, find
> the newest copy: `git ls-remote origin 'refs/heads/claude/*'` then
> compare `Last updated` lines via
> `git fetch origin <branch> && git show FETCH_HEAD:docs/MB_STATE.md | head -5`.
> Newest wins. (Protocol: CLAUDE.md "STATE DOCS ARE BRANCH-VERSIONED";
> 2026-07-11 incident: a fresh session read master's stale copy and
> recommended the BANNED circular validate rerun it found there.)

**Last updated:** 2026-07-12 (shadow-steward session) · **Branch:** `claude/repo-setup-docs-fq9bhn` (merge superset of `irq7r5` + master `e2a406d`; head = this commit)
**Read first:** `CLAUDE.md` (binding directives), then this file, then **`docs/MB_COPYTRADER_CONTEXT.md` (FULL context brief for the live copy-trader investigation — the complete reasoning chain, API gotchas, and decision tree)**. `MB_REBUILD_PLAN.md` holds the older plan + operator decisions.
**Protocol for updating this file:** `docs/MB_HANDOFF_PROTOCOL.md`.

---

## 0. IMMEDIATE RESUME (2026-07-11 ~03:30 UTC — the pipeline is DONE; results below)

> **2026-07-12 UPDATE (shadow-steward session, `claude/repo-setup-docs-fq9bhn`):**
> the `irq7r5` session ("king") is FROZEN; this session stewards the shadow
> (operator-authorized, all four items). Everything king pushed survives
> (head `1c08793`, incl. A+D sizing `4d6c3da`); its UNPUSHED ladder-capture
> patch is presumed lost with the container and was REBUILT (below). New
> since 2026-07-11, all on this branch, none deployed yet:
> 1. **Ladder capture in the shadow watcher** — `mirror_v3/copy_watcher.py`
>    now records `book_asks`/`book_bids` (top 20 CLOB `/book` levels, shaped
>    for `fill_models.precise_fill`) per record; gates still quote `/price`
>    unchanged, `/book` failure = null ladders, never a verdict change.
>    17 unit tests. NEEDS A REDEPLOY to take effect.
> 2. **DEPLOY-VERSION QUESTION (UNVERIFIED):** §0 says the service runs
>    commit `eac8a92`, but A+D sizing landed AFTER it (`4d6c3da`) — if no
>    redeploy happened, live records lack `conviction_r`/`size_multiplier`.
>    Operator check + the ladder redeploy resolve this together (see §5).
> 3. **Tx-exact re-adjudication of the 9 DISCREPANT** —
>    `scripts/readjudicate_discrepant.py` (pre-registered VINDICATED /
>    STILL_DISCREPANT rule in its docstring; vindicated traders only ever
>    join as a SECOND cohort, operator-gated). Awaiting operator VPS run.
> 4. **Crypto kill-test runner** — `scripts/crypto_kill_test.py`
>    (pre-registered KILLED/SURVIVES/INCONCLUSIVE at lag 10s vs +0.02
>    floor). Awaiting operator VPS run, after (3).
> A daily 13:00 UTC steward check-in Routine now fires into this session
> (king's wake-ups are dead with it).

**The operator runs all VPS commands** via single-line SSH one-liners from
Windows PowerShell (he cannot paste after connecting; never give multi-line).
Template: `ssh -t -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "..."`.
PowerShell EATS `$` and `"` inside the quoted command — never put either in a
one-liner (the 2026-07-10 probe false-negative was PS mangling `\"` JSON).

**RESULTS CHAIN (2026-07-10/11 overnight, all artifacts snapshotted to
`/opt/pa2-shared/mb_copyable_data/`):**
1. **Walk-forward PASS** (full-coverage run, header-gated: labeled
   first-buys=100,180, gamma merged=69,977 market keys, coverage 97%):
   PRIMARY edge **+0.0237, P(edge>0)=1.000, upper95 +0.0284**, 24,919 bets /
   19,281 mkts / 28 traders; robustness +0.0224/+0.0203; econ floor +0.02
   cleared, thinly. `/tmp/walkforward3.{log,json}` + snapshot.
2. **Chain audit (fixed × 3, see §7 landmines): 16/29 CLEAN** — 580 samples:
   505 verified / 20 mismatch / 55 not-found / 0 rpc-err.
   DISCREPANT (9) EXCLUDED (chain wins; some may be ±30min window-blend
   artifacts — a tx-hash-exact matcher would settle it, later). THIN (1)
   excluded pending wider window. `/tmp/chain_audit.{log,json}` + snapshot.
3. **Fill gate: NO VERDICT — GENUINE coverage gap, probe-CONFIRMED
   2026-07-11:** only 164/34,507 roster tokens (0.5%) exist in
   orderbook_snapshots at all (token shapes match — no key bug, no window
   bug). Retrospective fill measurement is CLOSED for this roster; the
   forward shadow is the only instrument. NOT fill-killed.
4. **Operator decision (2026-07-11): build the forward shadow instrument.**
   BUILT: `mirror_v3/copy_watcher.py` — on-chain OrderFilled polling of the
   CLEAN roster (~2-4s detection vs ~10s REST), pre-trade gates
   (NO_BOOK / SPREAD_TOO_WIDE / PRICE_RAN_AWAY / OK), shadow fill = real CLOB
   ask, JSONL sink + detect-lag metrics. NO orders, NO DB writes. Wired into
   `mirror_v3/run.py` behind `MIRROR3_COPY_WATCHER=true` (default OFF,
   fail-loud). Env template: `deploy/env.mirror3.example`.

**SHADOW IS DEPLOYED AND RUNNING (2026-07-11 12:46 UTC, commit `eac8a92`):**
`polymarket-mirror3.service` on the VPS — env-guarded paper silo, watcher
polling both exchanges at 2s for the 16 CLEAN traders, retry-don't-skip
cursor (Tenderly head-race absorbed; only `SKIPPING (dropped window)` log
lines mean lost samples), sink `/opt/pa2-shared/mirror3_shadow.jsonl`
(world-readable), code at `/opt/mirror3`, redeploy = rerun
`deploy/mirror3_shadow_deploy.sh` (idempotent; never touches an existing
`.env.mirror3`).

**NEXT ACTIONS:**
1. [operator, ~daily glance] `systemctl is-active polymarket-mirror3;
   wc -l /opt/pa2-shared/mirror3_shadow.jsonl` — count should grow as the
   roster trades (humans: hours of silence normal).
2. [analysis, ~2-4 weeks of records] `scripts/analyze_shadow.py --log
   /opt/pa2-shared/mirror3_shadow.jsonl --gamma-cache
   /tmp/copyable_cache/gamma_resolutions.json` — the PRE-REGISTERED readout:
   OK-rate ≥50% on first-buys; pooled shadow edge net of fee ≥ +0.02 with
   P(>0) ≥0.95 on ≥30 resolved mkts (it refuses a verdict when
   underpowered). SURVIVES → operator decision on paper trading with real
   order flow. Never live from a backtest.
3. [later, optional] WSS subscription upgrade (Alchemy free tier) to cut
   detection to ~2-3s; tx-hash-exact audit matcher to re-adjudicate the 9
   DISCREPANT traders.

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
4. **A 3-stage detached pipeline RAN on the VPS** (gamma backfill → deepen
   humans-only → walk-forward). The deepen took ~13h (01:15→~14:30 UTC,
   0 partial-failed); its walk-forward stage never fired (stale-log ownership
   collision) and was re-run standalone at 15:02.
5. **2026-07-10 second session (branch `irq7r5`) — run-2 verdict + two root
   causes found and fixed.** The 15:02 walk-forward printed **PASS on the
   pre-registered primary** (VOL-sourced non-truncated: edge +0.0145,
   P(edge>0)=0.985, 5,338 bets / 3,947 mkts / 19 traders, robustness splits
   +0.0137/+0.0130, `/tmp/walkforward2.log`) — **PROVISIONAL**: it graded on
   DB-only labels (~24% coverage; header `gamma-backfilled labels merged=0`),
   and the point estimate sits under the +0.02 econ floor (upper95 +0.0250
   above it). DECLARED BEFORE THE RERUN: the full-coverage rerun REPLACES
   this verdict whichever way it goes. Root causes of merged=0, both fixed:
   (a) `merge_gamma_cache` refused labels for DB-known-but-unresolved rows
   (`aa6bbc1`); (b) the gamma backfill NEVER labeled anything — gamma's
   `/markets?condition_ids=` filter is silently ignored (probe-verified live:
   CLOB echoes the exact market, gamma returns `[]`); ported production
   `resolution_backfill.py`'s per-key CLOB endpoint (`b609c14`).
6. **THE FULL-COVERAGE PIPELINE IS RUNNING** (launched ~16:59 UTC from
   `/tmp/mbpc2` = `b609c14`): CLOB label backfill (`/tmp/gamma2.log`; at
   20:15 UTC: chunk 5,760/8,189, 139,357 labeled, errors=2; ETA backfill
   ~21:40 UTC) `&&` walk-forward → `/tmp/walkforward3.{log,json}`. See §0
   IMMEDIATE RESUME for the exact next command + accept criteria + PASS
   sequence. Durable 3.4GB snapshot already taken (`/opt/pa2-shared/
   mb_copyable_data`); re-copy after finish for the authoritative version.
   Also NEW this session: mandatory per-fill chain audit
   (`scripts/audit_roster_chain.py`, `28a447d`) + atomic JSON writes across
   all 4 scripts (`0bdd4de`) + this handoff.

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
| Label backfill (CLOB) | `scripts/backfill_resolutions_gamma.py` | REWORKED `b609c14`: per-key CLOB `/markets/{cid}` (gamma batch filter is a no-op — §7); prices-first resolution, winner-flag fallback; resumable, atomic checkpoints; live run ~96% label rate, 0 errors |
| Chain audit (mandatory pre-money) | `scripts/audit_roster_chain.py` | NEW `28a447d`: per-fill OrderFilled audit of the walk-forward roster, BOTH exchanges (main + NegRisk), CLEAN/DISCREPANT/THIN/ERROR + AUDIT-INCONCLUSIVE tripwire; self-test + 11 unit tests |
| Fill-quality gate | `scripts/backtest_copyable_fills.py` | audited coarse model at real ask for a NAMED roster; SURVIVES/FILL-KILLED/NO-BOOK; runs after a walk-forward PASS |
| Persistence check (secondary) | `scripts/check_trader_persistence.py` | read-only; reworked verdicts (SIGNIFICANT-BUT-SMALL; NULL can't discard underpowered-significant cutoffs), UNION-ALL planner-safe SQL, estimand-faithful first-entry selection, `--since/--until`, LIMIT sentinel; anti-conservative-null caveat printed; self-test + 11 unit tests green; **awaiting operator VPS run** |
| Operator runbooks | `docs/VPS_RUNBOOK_2026-07-02.md`, `deploy/mb_vps_oneshot.sh` | one-paste checks; mktemp-safe |
| Shadow ladder capture | `mirror_v3/copy_watcher.py` (`trim_book`/`fetch_book`, `book_asks`/`book_bids` fields) | additive, gates untouched; 17 unit tests; **needs redeploy to take effect** |
| Tx-exact re-adjudication | `scripts/readjudicate_discrepant.py` | per-tx/per-event size+price matcher (kills the ±window blend artifact); pre-registered VINDICATED rule; self-test + 8 unit tests; **awaiting operator VPS run** |
| Crypto kill-test runner | `scripts/crypto_kill_test.py` | coarse fills at lag 0/10/30s over `mirror_rejected_signals` crypto prints; pre-registered KILLED/SURVIVES/INCONCLUSIVE; self-test + 6 unit tests; **awaiting operator VPS run** |

## 5. Open threads / what's next

- **[NOW — decision tree for /tmp/walkforward3.log]** (the OLD
  `/tmp/walkforward.log` is a stale pre-pipeline artifact — ignore it; use
  `tail -n 4 /tmp/gamma2.log` for backfill progress, table lands in
  `/tmp/walkforward3.log`). First check the header: `gamma-backfilled labels
  merged=` must be ≫0 and `labeled first-buys` ≫ 29,635, else it's not the
  full-coverage run. On **PASS**: (1) re-copy the durable snapshot (below),
  (2) re-clone `/tmp/mbpc2` (the running clone predates the audit script),
  (3) `scripts/audit_roster_chain.py --from-json /tmp/walkforward3.json`
  (mandatory chain audit; ~15-20 min), (4) fill-quality gate
  `backtest_copyable_fills.py --traders <CLEAN roster> --lags 5,10,30`
  (verdict at the measured 10s; 5s is a sensitivity lead only), (5) operator
  decision on a v3 forward PAPER deploy. Everything else per the original
  tree below:
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
- **[operator, FIRST — one paste] Deploy-version check + ladder redeploy:**
  `ls /opt/mirror3/mirror_v3` — if `sizing.py` is absent the box predates
  A+D sizing AND the ladder capture; either way one rerun of
  `deploy/mirror3_shadow_deploy.sh` from this branch picks up both
  (idempotent; never touches `.env.mirror3`). Until then, live shadow
  records carry no `conviction_r` (UNVERIFIED which commit runs) and no
  ladders (VERIFIED — capture merged 2026-07-12, post-deploy).
- **[operator] Run the tx-exact re-adjudication** (`readjudicate_discrepant.py`,
  from a /tmp clone of this branch; roster = the audit json's 9 DISCREPANT).
  VINDICATED traders are PROPOSED for a SECOND shadow cohort with its own
  start date — operator decision, never automatic. Ceiling is 16→25; the
  matcher may equally confirm real discrepancies.
- **[build DONE 2026-07-12 → operator run] Crypto kill-test:**
  `scripts/crypto_kill_test.py`, pre-registered verdict at lag 10s vs the
  +0.02 econ floor; INCONCLUSIVE can never kill. Run after the
  re-adjudication (lower priority; it buys focus, not money).
- **[build, unblocked] v3 rejection logging + RTDS plumbing** so the silo collects its own signal stream (then old MB can be fully stopped, not just paused).
- **[decision] Merge/PR hygiene:** master is current; direct master pushes are operator-gated by the sandbox.

## 6. Cross-session coordination

- **EB (esports)** owns the OddsPapi vendor integration (esports). Odds-capability report is in this session's history; EB has a team-alias matcher MB can reuse. Registry publish (`EB_ODDS_CAPABILITY.json`) offered, not yet committed.
- **mb-formula-review** branch owns the *statistical* scoring lane; MB owns execution/guards/gate. Three statistical findings (condition-vs-event clustering, validation statistic, EB-shrink pool) handed over as recommendations, not applied.
- **MB has priority** on shared resources (CLAUDE.md). Old poisoned project lives at parent `C:/lockes-picks/` — OUT OF SCOPE.

- **[operator, after pipeline finishes] Re-run the durable snapshot copy** —
  ALL investigation data lives in `/tmp` (reboot-ephemeral). A parallel
  snapshot was taken mid-run 2026-07-10 (`/opt/pa2-shared/mb_copyable_data`)
  but may hold a torn gamma checkpoint (running code predates the atomic-
  write fix `0bdd4de`); the post-finish re-copy is the authoritative one:
  `sudo cp -a /tmp/copyable_cache /tmp/walkforward3.json /tmp/walkforward3.log /tmp/gamma2.log /opt/pa2-shared/mb_copyable_data/`

## 7. Landmines (do not trip)

- **A frozen session's unpushed work is GONE (2026-07-12).** Remote session
  containers are ephemeral; when the `irq7r5` session froze, its built-but-
  unpushed ladder-capture patch died with it and had to be rebuilt from
  scratch. Push after every completed unit of work, before idling or waiting
  on the operator — "built, tested" means nothing until it is on origin.
- **The chain audit's ±window matcher BLENDS same-token trades** — two real
  trades at different prices inside the window produce a chain price that
  matches neither API row → false DISCREPANT. Any future audit verdict
  should use (or cross-check with) the tx-exact matcher
  (`scripts/readjudicate_discrepant.py`); the audit's own mismatches are an
  upper bound on real discrepancies, not a count of them.
- **web3 v7 renamed `get_logs` kwargs to `from_block`/`to_block`** — the
  camelCase spelling TypeErrors on EVERY call and a bare `except` can launder
  that into "rpc_error" (2026-07-10: 580/580 dead samples). Use
  `get_logs_compat`; the real-library binding test in
  `test_audit_roster_chain.py` guards the regression. Same latent bug still
  in shared `base_engine/data/{blockchain_client,uma_proposal_monitor,
  oracle_monitor}.py` — NOT fixed (shared-module protocol).
- **`get_block_number_from_timestamp` (shared client) is a one-shot linear
  estimate at 2.0s/block — off by ~1-2M blocks a year back.** Never use it
  to window a chain search. Use `locate_block_by_ts` (Newton on real block
  timestamps, audit script) instead.
- **publicnode 403s archive eth_getLogs; polygon-rpc.com is key-gated;
  blastapi discontinued.** Probe-verified working free archive-logs endpoint:
  `https://polygon.gateway.tenderly.co`. Probe with a curl BODY built
  server-side (sed placeholder trick) — PowerShell mangles `\"` JSON.
- **PowerShell one-liners: never include `$` or `"`** inside the SSH command
  string ($var interpolates to empty; `\"` arrives as literal backslashes).
- **Two detached runs writing the same log/JSON corrupt both** — always kill
  the previous run (exact PID, not pattern) before relaunching a pipeline
  that writes the same output paths.
- **gamma `/markets?condition_ids=` is a silent no-op** — HTTP 200, `[]`,
  zero errors; it burned two full backfill runs before being caught
  (probe-verified 2026-07-10). Per-key CLOB `/markets/{condition_id}` is the
  production-proven path (`resolution_backfill.py:17`). Never batch-filter
  gamma by condition id.
- **`pkill -f <pattern>` where the pattern appears in your own SSH command
  string kills your own session** before the rest of the command runs (bit
  us 2026-07-10: the kill+re-clone one-liner died at the clone). Use a
  bracket pattern (`backfill_resol[u]tions`) in operator one-liners.
- **All copy-trader investigation data is in `/tmp`** — one VPS reboot erases
  ~14h of API downloads + labels. Durable copy: §5 last item.
- **`/tmp/walkforward.log` + `/tmp/walkforward2.log` are superseded
  artifacts** (stale pre-pipeline run; provisional DB-only-label PASS).
  The citable run is `/tmp/walkforward3.*` only.

- **`bots/mirror_scoring/validation.py` is a false-PASS machine** (confirmed 2026-07-09, adversarially verified): `_UNIVERSE_SQL` has no time bound, BH admission keys on the post-cutoff test half, and the "out-of-sample" rejected-signals set shares the same post-cutoff (trader, market, outcome) randomness. Any PASS it emits is selection noise — never clear the UNVERIFIED label with it, never cite it in a go decision. (FAILs are directionally credible: the bias runs the other way.)
- **order_gateway neg-risk block** no-ops for MB by accident (CLAUDE.md "DORMANT LANDMINE"). "Repairing" the index re-creates Bug 14 (election blackout). Leave it.
- **Do NOT add a `neg_risk=True` filter** anywhere (CLAUDE.md).
- **`mirrored_trades` is bookkeeping, not a guard** — the real same-side dedup is the `_open_positions` scan.
- **CANARY_AUTO_ADVANCE unset → true** by code default. Any live-capable path must set it false explicitly.
- **orderbook_snapshots is aggregated buckets, not L2** — precise replay needs `shadow_fills.book_snapshot`.
