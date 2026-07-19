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

**Last updated:** 2026-07-17 (local steward session, ~12:40 UTC — run-3 killed, run-4 merged fair-params batch launched after 3-round blind review; hft-cache + stale-gamma landmines found+mitigated) · **Branch:** `claude/repo-setup-docs-fq9bhn` (head = this commit)
**Read first:** `CLAUDE.md` (binding directives), then this file, then **`docs/MB_COPYTRADER_CONTEXT.md` (FULL context brief for the live copy-trader investigation — the complete reasoning chain, API gotchas, and decision tree)**. `MB_REBUILD_PLAN.md` holds the older plan + operator decisions.
**Protocol for updating this file:** `docs/MB_HANDOFF_PROTOCOL.md`.

---

## 0. IMMEDIATE RESUME (2026-07-14 local steward session — read this block first)

> **2026-07-17 UPDATE (local steward session; operator-approved "proceed with
> all action items") — RUN-3 KILLED, RUN-4 (merged, fair-params) LAUNCHED
> 12:34:57Z after a 3-round blind adversarial review chain. READ THIS FIRST.**
>
> 1. **RUN-4 IS THE ACTIVE BATCH**: 28 traders (probe `0xf705fa` line 1 →
>    band 8 → 19 not-yet-done), code `/tmp/mbre`@`27ee79b` (new-params
>    defaults: receipt-free >1000, decisions/day ≤25, flat-share <0.60),
>    python `/opt/polymarket-ai-v2/venv/bin/python` (NOT /tmp/mbre/venv —
>    doesn't exist; run-3's cmdline used a RELATIVE venv path), log
>    `/tmp/deep_dive_run4.log`, summary `_summary_run4.json` (spans ALL runs
>    sharing the out-dir — not run4-only). First-minute checks passed
>    (28 traders, SKIPDB=0, PID 3269649 detached). Local watchers: batch-end
>    poller + probe-JSON watcher (steward session scratchpad).
> 2. **WHY the kill (operator-approved)**: run-3 ran pre-rework code
>    (`07e7296`, old cap-200) — its band rejects needed re-testing anyway —
>    AND the blind review found run-3's INSUFFICIENTs confounded (see 3).
>    Run-3 got through 8/27 before the kill (traders 1-8 have JSONs; last
>    3: `0xa58d4f` REJECT 1194/day [stays — >1000 in both regimes],
>    `0xdf2e12` REJECT 390/day [band → in run-4], `0x0c0e27` INSUFFICIENT).
> 3. **NEW LANDMINES (blind-review chain findings, ALL MITIGATED for run-4)**:
>    (a) **status="hft" API cache (~500 rows) makes ADMIT deterministically
>    unreachable** — tok2cond comes ONLY from cached API rows → lifetime
>    first-buys unlabelable → span<60d → skill can't clear; AND unmapped
>    tokens inflate decisions/day (each token = a "market") → FALSE-REJECT
>    through the hard decision gate. ALL 34-HFT-borderline caches were hft.
>    Fix applied: renamed 27 starved caches to `.hft-bak` → run-4 re-fetches
>    full histories (missing-cache path disables the HFT short-circuit).
>    (b) **gamma_resolutions.json was 6 days stale** — suppresses (never
>    corrupts) recent labels; refreshed 12:24Z via
>    `backfill_resolutions_gamma.py` (+699 labels, 198,832 total, 0 errors).
>    ⇒ **run-1/2/3 INSUFFICIENT "un-gradeable/underpowered" verdicts are
>    confounded by (a)+(b) — they are DEEPEN candidates under fresh
>    caches/labels, backlog, operator word.**
>    (c) `ssh 'cmd &'`-style launches: stale /tmp logfile owned by another
>    user = silent no-launch (bit us 12:12Z; use fresh log names + the
>    LAUNCHED/ABORTED marker pattern + alive-check after 10s).
> 4. **ROSTER LEDGER DELTA (deep-dive candidate roster, protocol-logged)**:
>    ADD `0xdf2e12c6a5…` to the band re-test (390/day, run-3 old-cap
>    REJECT). ADD `0xed107a85a4…` (trader 5) to run-4 (its INSUFFICIENT was
>    (a)+(b)-confounded; verdict NOT code-invariant under new gates). No
>    live-cohort changes — cohort-2 (8) + probe (1) unchanged, watcher
>    untouched (canary 0 throughout).
> 5. **Verified this session**: 8 ADMIT JSONs ≡ cohort-2 ledger (set
>    identity); deployed watcher blob ≡ `336f6a4` (hash match); cohort-2
>    OK-rate 69.2%/conc 69% SURVIVES (trader,token)-dedup; 47-list files
>    durably copied into `deep_dive/`; 9 pre-rerun verdicts backed up to
>    `deep_dive/pre_band_rerun_20260717/`.
 > 6. **OPEN**: [operator] cohort-2's 8 live ADMITs never faced the new
>    decision/flow gates — shadow measures copyability empirically (chosen
>    for now); uniform re-dive = backlog. [next] run-4 completion → tally →
>    ADMIT proposals by NAME only; probe cross-check vs 7.6 decisions/day,
>    0.16 flat_share on first JSON. Full procedure + review findings:
>    steward scratchpad `band_rerun_runbook.md` (v3).
> 7. **PROBE `0xf705fa` = ADMIT under fair params (run-4 trader 1, ~13:45Z
>    2026-07-17) — CROSS-CHECK PASSED, PROMOTION QUEUED TO BATCH BOUNDARY
>    (operator "proceed").** Verdict: complete sweep 135,493 fills, 0
>    mismatch, 100% of 28,926 API-BUYs chain-backed, skill +0.036 P=1.00 on
>    1,838 mkts, decisions/day 7.66 (exact match vs pre-registered 7.6),
>    flat_share 0.39 < 0.60 (differs from the ledgered 0.16 — BENIGN: old
>    figure was the recent-window API sample, run-4 computes lifetime chain
>    positions; both far under the bar). Live shadow agrees: 82% OK-rate at
>    0.9s lag since its 00:50Z probe epoch. PROTOCOL: promotion (probe →
>    cohort-3 w/ own epoch) happens in ONE batched watcher restart with any
>    further run-4 ADMITs at batch end — a mid-run restart would reset
>    FirstBuyDedup for zero informational gain (probe already has its own
>    epoch + readout line; collection is identical under either label).
> 8. **STACK-VS-FIRST-BUY TEST (operator-requested 2026-07-17) — PRE-REGISTERED,
>    retrospective arm VOID, forward arm ARMED.** Question: for a stacker, does
>    the edge live in the first entry or the accumulation (would re-buying beat
>    our one-bet-per-market policy)? Retrospective attempt on the probe's API
>    cache FAILED its own cross-check gate twice (could not reproduce the
>    deep-dive's 1,838 mkts @ +0.036) — ROOT CAUSE: the data-api record is a
>    structural SUBSET of chain truth (28,926 API BUYs vs 60,576 chain BUYs for
>    0xf705fa; deep-dive tier-2 verifies the subset is honest, NOT complete).
>    NO VERDICT from API-based retrospectives — landmine: never grade a
>    high-rate trader's entry pattern from /activity alone. UNVERIFIED
>    descriptive residue (both estimand framings agreed): his stack VWAP sits
>    ~1.5c WORSE than his first price (P(better)=0.000) but dollar-weighting
>    his sizing BEATS equal-weight — i.e., size-as-conviction looks real,
>    price-improvement-by-stacking does not. Do not act on this.
>    **FORWARD TEST (pre-registered, runs on shadow data):** when >=30 resolved
>    (trader,token) positions carry >=2 BUY records, compute per position
>    Delta_exec = (outcome - VWAP of recorded asks) - (outcome - first ask),
>    market-clustered bootstrap, seed 7. Delta>0 @ P>=0.95 -> re-buy policy
>    becomes a DESIGN PROPOSAL (touches the one-bet-per-market guard: operator
>    decision); else first-buy-only validated. Every re-buy is already recorded
>    with executable quotes — zero new collection needed.
> 9. **OPERATOR APPROVALS (2026-07-17 ~15:20Z, "proceed") — EXECUTE AT RUN-4
>    BATCH BOUNDARY (the local batch-end watcher fires the sequence):**
>    (a) PROMOTE `0xf705fa` (probe ADMIT) + `0x7c3db723f1d4…` (run-4 fresh
>    ADMIT, 395/day: sweep complete, 0 mismatch, 100%/118,606 API-BUYs
>    backed, skill P=1.0 on 4,280 mkts) — implementation: clean 25→26,
>    extend `probe.addresses` with 0x7c3db7 (readout's multi-address probe
>    group VERIFIED to work; group epoch stays 00:49:56Z — 0x7c3db7 has zero
>    prior shadow records so nothing pools, F1-safe), ONE
>    mirror3_shadow_deploy.sh restart, verify roster=26 + canary + next
>    12:30Z readout runs clean. Any FURTHER run-4 ADMITs join the same
>    single restart. (b) DEEPEN WAVE after run-4 exits: the 9 confounded
>    INSUFFICIENTs (list in steward runbook) — rename non-ok caches, bare
>    roster file, detached launch, --max-receipts 30000. (c) THEN
>    `0x70d94a` solo deepen at --max-receipts 120000 (~4h receipts).
>    Sequence strictly serial (one batch at a time on the shared RPC).
> 10. **SPEEDUPS 1+2 BUILT (operator-approved 07-19; option 3/paid endpoint
>    REJECTED): `scripts/chain_fill_cache.py` (`b67fe20`)** — persistent
>    per-address chain-fill cache (+receipt-side memory, key tx|token_id)
>    + populate_multi ONE-sweep-for-N-addresses; wired into chain_deep_dive
>    behind `--fill-cache-dir` (default OFF = byte-identical old path,
>    differentially proven by adversarial review). 5 review findings fixed
>    (silent coverage hole on non-adjacent merge; reorg margin on the write
>    path; cache-file collision with API caches; gap error-frac denominator;
>    malformed-blob fallback). 65 tests green.
>    **EMPIRICAL PROOF GATE (pre-registered, MUST pass before any batch uses
>    the flag):** at run-4 exit, on the idle RPC: (i) bounded A/B — multi
>    sweep vs per-addr sweeps over the same block range for 2-3 addrs →
>    fill sets must be IDENTICAL; (ii) full re-dive of one completed trader
>    via populate+cache → verdict AND tier-1/2 counts must match its
>    existing JSON exactly. Only then does the deepen wave run with
>    `--fill-cache-dir` (expected ~10-25x cheaper sweeps). Failure of
>    either → deepen wave runs FLAG-OFF (old path), no function lost.
> 11. **COHORT-3 PROMOTION PREREQUISITE BUILT (operator "go" 07-19; `cdf01fb`):**
>    `shadow_readout.load_cohorts` generalized from hardcoded cohort1/cohort2/
>    probe to read any `cohort<N>` key (own epoch, never pooled) — needed
>    because the daily readout couldn't represent a 3rd cohort. Differential-
>    IDENTICAL on the live 16+8+1probe roster (offline AND a VPS dry-run in
>    the real venv). Adversarial workflow (4 lenses × verify) caught a REAL
>    HIGH defect I introduced — an empty `cohort<N>` addresses list slipped
>    every guard → `filter_traders("")` pools the WHOLE roster mislabeled →
>    could fire a false POWERED go/no-go alert (the 2026-07-15 finding-A
>    silent-pooling class). FIXED at root (empty admitted cohort now raises,
>    matching HEAD's `if not c2`); self-test PASS incl the new case; a
>    simulated real promotion (16+8+6, probe emptied) loads clean. Cron
>    auto-adopts at 12:30Z (branch-pinned refresh THEN roster read = new code
>    + new roster always consistent). **BATCH-BOUNDARY PROMOTION (armed):**
>    at run-4 exit, the run-4 ADMITs (6 so far: 0xf705fa graduates from probe
>    + 0x7c3db7/0xe542af/0x216509/0x2ee04b/0xa6a856; +any more before t28)
>    become cohort3 via ONE fenced mirror3_shadow_deploy.sh restart —
>    procedure in steward scratchpad `cohort3_promotion_procedure.md`
>    (chain_audit.json: clean 25→30, add cohort3 key w/ own epoch, empty the
>    probe key; invariant clean==union checked offline before deploy).
> 12. **READOUT (07-19 14:56Z VPS dry-run, fresh labels):** cohort1(16)
>    28/30 resolved edge +0.0440 P(>0)=0.720 conc 0x448861…37%; cohort2(8)
>    14/30 edge +0.0432 P(>0)=0.648 conc 0xbaa2bc…35%; probe 0 resolved.
>    Both UNDERPOWERED, both drifting mildly POSITIVE as markets resolve
>    (cohort1 +0.031→+0.044 within the day). No verdict; no alert.
> 13. **TRIPLE-BLIND REVIEW of all session code (07-19; 3 blind lenses ×
>    adversarial verify): 5 confirmed findings, ALL in NOT-YET-EXERCISED code
>    — the live-critical paths (running readout on the live roster, flag-off
>    run-4) came back CLEAN. All 5 FIXED + committed (`7f5c771`,`d2bca15`,
>    `8b3ce27`):**
>    - [med, stack_vs_firstbuy_forward #2/#4/#5] the forward test read RAW
>      /price best_ask+verdict, bypassing `az.repair_records` (the /book-ladder
>      repair the money-gate readout treats as ground truth) → priced the
>      estimand off a flattered/gate-dodging quote; AND powered the verdict on
>      POSITION count while the bootstrap clusters by TOKEN → cross-trader
>      token overlap could fire a "POWERED" verdict on ~2 markets; no
>      concentration disclosure. FIX: canonical repair pipeline + power on
>      DISTINCT token-clusters + inline concentration. Pre-registration
>      corrected BEFORE any data (tool not yet run). 4 new self-test asserts.
>    - [low, shadow_readout #1] an intra-group DUPLICATE address passed the
>      cross-group set() guards but broke the leave-one-out `rest` →
>      filter_traders("") → whole-roster pooling in the LOO line. FIX: fail
>      loud on any intra-group dup.
>    - [low, chain_fill_cache #3] a superset re-populate summed leaf_ok
>      (double-count) → could understate the lossy-gap rpc_err_frac and mask
>      an incomplete sweep. FIX: replace-on-superset (don't sum).
>    Nothing here changed a currently-live number. Method note: the two
>    workflow reviews this session (readout cohort<N>, then this triple-blind)
>    each caught a real defect the single-pass reviews missed — the money-gate
>    surface warrants the multi-lens adversarial pass.

> **2026-07-14 PM UPDATE (local steward session; VPS-direct SSH, operator-
> approved per-command) — CHAIN DEEP-DIVE GATE BUILT, REVIEWED, VALIDATED,
> AND THE 47-BATCH IS RUNNING.**
>
> 1. **`scripts/chain_deep_dive.py` — the roster-admission gate — is DONE**
>    (§5 TO-DO item 1). Four tiers: T1 lifetime dual-era fill reconstruction
>    (V1 OrderFilled maker+taker both exchanges + V2 fill topic owner-filtered;
>    V1 direction implicit in the USDC leg, V2 direction from tx receipts,
>    capped); T2 API↔chain reconciliation BOTH directions (tx-exact matcher,
>    BUY-only candidates); T3 skill re-grade on chain data vs the SAME
>    walk-forward hire bar; T4 forensics (counterparty/wash, maker-taker +
>    TRUE lifetime rate = the fair HFT test, sampled copier probe, pUSD funder).
>    Reuses the audited siblings as-is; read-only; no shared-module edits.
> 2. **Pre-registered verdict (locked in the docstring): REJECT only on an
>    AFFIRMATIVE contradiction (mismatch / fabrication / adequately-powered
>    NEGATIVE chain edge) or a MEASURED infeasibility (true rate > cap);
>    every evidence gap or unverified forensic suspicion (short-span/underpowered
>    skill, thin backing, too-few API BUYs, ts-uncomputable, receipt-cap, wash,
>    copier) → INSUFFICIENT-EVIDENCE. ADMIT is a PROPOSAL to the operator for a
>    cohort — never auto-add, never pooled with cohort-1.** Chain wins; a gap is
>    never an accusation (binding operator rule 2026-07-14).
> 3. **Validation:** 61-agent-style adversarial review (5 lenses → adversarial
>    verify) surfaced **16 confirmed findings; all fixed** (top: T2A folded
>    SELLs/unknown into BUY candidates → could mask a lie / false-REJECT — the
>    core chain-wins bug). A bounded integration smoke on `0xd1acd3925d` then
>    caught **2 more** (API-buy windowing → false 99% FABRICATION; receipt-cap
>    → false not_found) — both fixed. Re-smoke CLEAN: tier-2 backing 1.00,
>    direction fully resolved, correct INSUFFICIENT (recent markets unresolved).
>    `--self-test` (16-case verdict table) + 23 pytest green LOCAL + VPS venv
>    (web3 7.5.0); siblings unregressed (53). Commits on this branch through
>    `0231f2c`.
> 4. **[SUPERSEDED by the ROSTER LEDGER's EXECUTION UPDATE below — run-1 was
>    killed at 12/47 and relaunched as run-2 (rps 8, receipt short-circuit);
>    the summary is now `deep_dive/_summary_run2.json`. The summary CODE since
>    `5eae137` rebuilds from the on-disk JSONs after every trader (crash-
>    durable, spans all runs sharing the out_dir) — but the IN-FLIGHT run-2
>    loaded pre-fix `d6276f7`, so its file covers only its own 35 at run end;
>    the 47-wide view = the per-trader tally one-liner below, or any later
>    run under `5eae137`+.]**
>    ~~THE 47-BATCH IS RUNNING~~ (originally launched 2026-07-14 19:31 UTC, detached
>    setsid+nohup, reparented to PID 1, rps=4, max_receipts=30000): 9 cohort-2
>    (`readjudicate.json` VINDICATED) + 38 (`deep_dive_extra_38.txt` = grey-4 +
>    the 34 HFT-borderline incl `0xa6a856a8c8…`) = 47. Log `/tmp/deep_dive_batch.log`;
>    per-trader JSONs + `_summary.json` land in the polymarket-owned
>    `/opt/pa2-shared/mb_copyable_data/deep_dive/` (mb_copyable_data itself is
>    root-owned — see landmine). Est. ~17-25h. **NEXT SESSION: collect
>    `deep_dive/_summary.json`, review the ADMIT/REJECT/INSUFFICIENT split;
>    admissions to any cohort need the OPERATOR'S WORD (own start date, separate
>    readout, never pooled with cohort-1). INSUFFICIENT = deepen (raise
>    --max-receipts / widen window / --refresh cache), NEVER accuse.**
>    Monitor cmd: `wc -l /tmp/deep_dive_batch.log; ls
>    /opt/pa2-shared/mb_copyable_data/deep_dive/0x*.json | wc -l;
>    journalctl -u polymarket-mirror3 --since '1 hour ago' | grep -cE 'CANARY ALARM|QUOTE SANITY'`
>    (the batch shares the tenderly endpoint with the LIVE mirror3 watcher —
>    watch that canary count stays 0).
>
> **ROSTER LEDGER + add/subtract protocol (operator directive 2026-07-14):**
> the steward MAY add/subtract candidate traders from the deep-dive roster
> autonomously, but EVERY add/subtract MUST be logged here + in
> `MB_DEEP_DIVE_NEXT_PROMPT.md` with the delta + reason (nothing enters/leaves
> the roster invisibly). Admissions still need the operator's WORD (this
> authority is over the CANDIDATE roster, not who joins a live cohort).
>   - **WAVE-1 = 47** (roster UNCHANGED): 9 cohort-2 (`readjudicate.json`
>     VINDICATED) + 4 grey (`readjudicate_grey2.json` VINDICATED) + 34
>     HFT-borderline (`deep_dive_extra_38.txt` = grey-4 ∪ the 34, dedup).
>     **EXECUTION UPDATE 2026-07-15:** run-1 (rps 4, code `0231f2c`) was KILLED
>     at 12/47 done (results preserved) and RELAUNCHED as **run-2** on the
>     remaining 35 (`/tmp/deep_dive_remaining.txt`) at **rps 8** with the
>     receipt short-circuit (`d6276f7`). Run-1 tally: **8 ADMIT / 3
>     INSUFFICIENT / 1 REJECT-uncopyable** (all 12 JSONs parse-verified).
>     Run-2 writes `deep_dive/_summary_run2.json` (its 35 ONLY) — **NO durable
>     artifact aggregates all 47**; tally from the per-trader JSONs:
>     `python3 -c "import json,glob,collections; print(collections.Counter(json.load(open(p))['verdict'] for p in glob.glob('/opt/pa2-shared/mb_copyable_data/deep_dive/0x*.json')))"`
>     The 3 INSUFFICIENT (skill under the P bar on full data) are DEEPEN
>     candidates, not re-run-blindly: their gap is resolved-market count, so
>     re-dive them only after more of their markets resolve.
>   - **CANDIDATE-ADD MENU for WAVE-2 (identified 2026-07-14 PM, NOT yet run —
>     deferred so a 2nd batch doesn't double RPC load on the shared tenderly
>     endpoint while wave-1 + the live mirror3 watcher run):**
>     `0x6bab41a0dc40d6dd4c1a915b8c01969479fd1292` (run-1 strong: 264 P1 mkts
>     +0.067 → 109 P2 +0.089 P=0.99) and
>     `0x4dfd481c16d9995b809780fd8a9808e8689f6e4a` (run-1 politics: 17 mkts
>     +0.410 P=1.00) — both cached, both confirmed NOT in wave-1; plus the
>     ~38 ALL-universe scope-outs (§0 item 5, less concrete). Run wave-2 AFTER
>     wave-1 completes; log the exact added addresses here when you do.
>   - **COHORT-2 ADMISSIONS — LIVE (2026-07-15, operator go "batch all 8"):**
>     the first 8 deep-dive ADMITs (all VERIFIED: 100% API-BUY backing, 0
>     mismatch, chain skill P 0.925-1.000 on 175-8386 mkts, rate 38-170/day
>     tailable, no wash/copier) were ADMITTED to the live mirror3 shadow
>     watcher. Roster `chain_audit.json` `clean` 16→24 (backup
>     `chain_audit.json.pre-cohort2-20260715`; cohort split kept in its
>     `cohort1_original`/`cohort2` keys). Restarted via
>     `mirror3_shadow_deploy.sh` (→ /opt/mirror3 d6276f7; mirror_v3 BYTE-
>     IDENTICAL to the prior 25b54d4, verified — roster-only in effect).
>     Watcher healthy: roster=24, first canary 1765 fills, 0 quote/canary
>     alarms. **COHORT-2 START EPOCH = 1784143245 (2026-07-15 19:20:45 UTC).**
>     The 8: `0x0e5bd767…`, `0x4ad6cade…`, `0x7744bfd7…`, `0xa2f1fecf…`,
>     `0xbaa2bcb5…`, `0xc660ae71…`, `0xd1acd392…`, `0xe25b9180…`.
>     **SEPARATE READOUT (never pool w/ cohort-1):** `analyze_shadow.py
>     --trust-quotes-after 1784143245` FILTERED to the 8 cohort-2 addresses.
>     **Cohort-2 pool (13 = the nine + grey-4) accounting, corrected
>     2026-07-15 PM (session-close review finding #16):** 8 ADMITTED (6 of the
>     nine: 0x0e5bd7/0x7744bf/0xa2f1fe/0xbaa2bc/0xd1acd3/0xe25b91 + 2 grey:
>     0x4ad6ca/0xc660ae) · 3 INSUFFICIENT (0x481858, 0x92672c of the nine;
>     0xea8ee3 grey — skill under the P bar on FULL data; deepen = wait for
>     more of their markets to resolve, then re-dive) · 1 REJECT-uncopyable
>     (0xf705fa, 461/day — honest, skilled, un-tailable) · 1 PENDING
>     (0xfbf3d5 grey, run-2 trader 1). Add later ADMITs (run-2 remainder +
>     wave-2) the SAME way — batch, one restart, extend the cohort2 ledger key
>     (shadow_readout REFUSES a readout if clean != cohort1+cohort2, so an
>     admission without the ledger update now fails loud).
>   - **ROSTER DELTA 2026-07-17 00:49:56Z (operator-agreed "fix not bend"):**
>     `0xf705fa…` added as a **PROBE** (observation-only, ledger key `probe`,
>     own epoch/readout line, never pooled; roster clean 24→25, watcher
>     restarted 00:50:21Z, backup `chain_audit.json.pre-probe-20260716`).
>     Why: the 461-fills/day REJECT measured the wrong unit — he is a STACKER
>     (7.6 decisions/day, 16% net-flat, 57% hold; round-trip component wins
>     only 38%) with +0.0368 P=1.000 on 1,835 mkts and 100% chain backing.
>   - **COPYABILITY PARAMS REWORKED (`27ee79b`, pre-registered BEFORE any
>     re-run):** receipt-free REJECT band now >1,000 fills/day; the 200-1,000
>     band is judged post-receipts on `--max-decisions-per-day 25` (chain
>     first-buys/day) + `--max-flat-share 0.60` (flow_shape net-flat share,
>     >=20 positions) — the DIRECT market-maker test. `--hft-max-rate` is
>     reporting-only now. Adversarial review: no confirmed defects; overlap
>     rejection + strict probe epoch added.
>   - **QUEUED [next session or run-3 completion]: BAND RE-RUN** — re-run all
>     rate-rejects with true_rate in (200,1000] under `27ee79b`+ (they were
>     rejected under the old 200 cap): currently 938, 749, 461(=probe, gets a
>     formal verdict), 395, 371, 288(0xfbfd14dd, had a ts issue) + any run-3
>     additions. Whoever passes -> PROPOSED as probes (operator word each).
>
> *(The 2026-07-14 ~02:45 block below is prior state from earlier the same day;
> its deep-dive-gate TO-DO is DONE per the above.)*

> **2026-07-14 UPDATE (local steward session; VPS-direct SSH, operator-
> approved per-command).** Five instrument bugs found & root-fixed in one
> day; ZERO trader lies ever confirmed (~800 receipt-level checks). State:
>
> 1. **QUOTE-SWAP (deployed watcher read /price sides REVERSED).** side=BUY
>    returns the best BID, side=SELL the best ASK — the watcher had it
>    backwards, so every pre-fix record's bid/ask were swapped and
>    shadow_fill quoted the BID (median +1.5c flattery vs the +0.02 floor;
>    counterfactual: 5/31 "OK" were really PRICE_RAN_AWAY; spread gate
>    could never fire). Fixed `2686e5c` + crossed-book runtime alarm
>    `5ce37ba` + live verify method `scripts/verify_clob_price_sides.py`
>    `875e389` (ran 5/5 AGREES) + readout repair `25b54d4`
>    (analyze_shadow re-derives every ladder-armed record; ladderless
>    pre-fix records EXCLUDED unless `--trust-quotes-after 1783985376` —
>    THE FIX-DEPLOY EPOCH, memorize it). Deployed 2026-07-13 23:29:27 UTC
>    (`/opt/mirror3` = `25b54d4`+); first post-fix record verified
>    (ask>=bid, fill=ask, ladder MATCH). Records are trustworthy from the
>    epoch; pre-fix records are ladder-repairable (all 83 had ladders).
> 2. **DUAL-ERA RE-ADJUDICATION — ALL 29 AUDITED TRADERS CLEAR.** The
>    audit toolchain searched V1 exchanges only (predates the 07-12 V2
>    discovery) → every post-migration fill was a structural not_found.
>    Fixed in `readjudicate_discrepant.py` (`fa21111`: V1 OrderFilled +
>    V2 fill topic, V2 candidates receipt-confirmed per (tx,token)).
>    Results: original 12 DISCREPANT → 9 VINDICATED (0 mismatch); grey-4
>    re-run dual-era n=60 ±3600s → 4/4 VINDICATED, 239/240 verified,
>    **0 not_found**; CLEAN-16 symmetric check → 16/16, 320/320 verified.
>    Artifacts: `mb_copyable_data/readjudicate{,_grey2,_clean2}.{json,log}`.
> 3. **OPERATOR RULES (2026-07-14, binding):** (a) the not-found quota is
>    REMOVED — not_found is an evidence gap, NEVER an accusation; the
>    response is a deeper search (window, samples, second RPC, dual-era),
>    never a threshold change; a lie exists only when the chain SAYS so
>    (size-matched tx at a different price) or when silence survives an
>    EXHAUSTIVE search of a complete record. (b) **CHAIN DEEP-DIVE GATE:
>    nobody joins any roster until they pass a full chain-native deep
>    dive** (see §5 TO-DO). API data is demoted to candidate-finding only.
> 4. **CRYPTO: UNRESOLVED, out of scope BY DEFAULT (never "killed").**
>    Kill-test ran (db.init bug fixed `23200e9`): INCONCLUSIVE by
>    construction — 0/2,720 crypto signals had ANY orderbook_snapshots
>    coverage at any lag. Retrospective crypto measurement is CLOSED;
>    only the forward shadow (correct books post-fix) can answer it.
> 5. **FUNNEL TRIPLE-CHECK:** hire→audit census perfect (29=29, 0 fetch
>    drops). BUT the HFT/bot filter judged on a 1-2.5 day burst page —
>    **34 borderline dismissals (203-469/day page rate) incl
>    `0xa6a856a8c8a7…` (run-1 named strong candidate, +2.5pts/593 mkts)**
>    never got the lifetime-rate test. They join the deep-dive batch.
>    Also: 38 traders rostered in the ALL universe never got a primary
>    shot (truncation scope); 496-leaderboard universe misses small
>    traders (documented scope limits).
> 6. **Shadow probes S1-S5 ran** (pre-registered 2026-07-13, descriptive):
>    83 records → **9 distinct (trader,token) firsts** (flow is heavily
>    concentrated — power = distinct positions, not detections). Capacity
>    CLEAR at paper size ($300 slip med +0.19c p90 +0.96c; >=$5k at <=1c
>    median). First-buy spread med 1c. Copy tax med +1c p90 +2c (n=7).
>    S1/S2 0/9 resolved — rerun when markets close. Artifacts:
>    `mb_copyable_data/shadow_probes_20260713.{py,out}`.
> 7. **Cohort-2 pool = 13** (9 + grey-4), NONE admitted — all gated on the
>    deep dive. Boundary-pass caveats retired (were the V1 blind spot).
> 8. Local-session logistics: work from the dedicated worktree
>    `C:/lockes-picks/mb-steward` (operator-directed exception to the
>    parent-dir fence — the Claude app yanks the main checkout between
>    branches); `git pull --ff-only` before EVERY commit (two writers).
>
> *(The 2026-07-12 block below is prior state; its "NEEDS REDEPLOY" and
> deploy-version questions are RESOLVED by the above.)*

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
>
> **2026-07-12 LATE UPDATE — THE SHADOW WAS BLIND FROM BIRTH; FIXED, NEEDS
> REDEPLOY.** The watcher's first ~33h produced ZERO records while the
> data-api showed **179 roster BUYs in 40h** (probe `roster_activity_check.py`,
> 16 traders, 0 fetch errors). Root cause chain, each step receipt-verified:
> (1) Polymarket moved trading to the **V2 exchanges** (Exchange V2
> `0xE111180000d2663C0091e4f400237545B87B996B`, NegRisk V2
> `0xe2222d279d744050d28e00520010520000310F59`) — WI-24 verified this
> 2026-06-11, BEFORE the watcher ever deployed; (2) V1 `OrderFilled` never
> fires for current flow (all probes zero across 4 RPCs — the RPC was
> innocent); (3) V2 fills emit an UNNAMED event, topic0
> `0xd543adfd945773f1a62f74f0ee55a5e3b9b1a28262980ba90b1a89f2ea84d8ee`,
> layout reverse-engineered from known trades and validated to 4 decimals
> (`scripts/decode_v2_fill.py`): topics[2]=order owner (server-side
> filterable), data=[?, token_id, usdc*1e6, tokens*1e6, 0,0,0]; (4)
> **BUY/SELL is NOT in the V2 event** — direction read from the tx
> receipt's ERC-1155/pUSD transfers on roster hits (`side_from_receipt_logs`).
> Watcher reworked accordingly (`c77e2dd`), plus a blind-RPC canary
> (10-min unfiltered fill count, alarms after 2 zero cycles, first result
> always logged) so silent blindness is structurally impossible now.
> Diagnostic toolchain kept: `diagnose_watcher_detection.py`,
> `rpc_logs_probe.py`, `trace_real_fill.py`, `decode_v2_fill.py`.
> **The shadow readout clock starts at the post-fix redeploy, not at
> 2026-07-11 12:46.** The walk-forward/audit are NOT invalidated (their
> fills genuinely lived on V1-era history).

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
| Crypto kill-test runner | `scripts/crypto_kill_test.py` | RAN 2026-07-13 (after db.init fix `23200e9`): INCONCLUSIVE by construction (0/2,720 orderbook coverage) → crypto UNRESOLVED, out of scope by default |
| Tx-exact re-adjudication v2 | `scripts/readjudicate_discrepant.py` | DUAL-ERA (`fa21111`): V1+V2 events, V2 receipt-confirmed; 10 unit tests; all 29 traders cleared (see §0.2) |
| /price semantics pin | `scripts/verify_clob_price_sides.py` | live PASS/FAIL vs /book (`875e389`); ran 5/5 AGREES pre-deploy; run before any watcher deploy or on QUOTE SANITY alarm |
| Readout repair | `scripts/analyze_shadow.py` | ladder re-derivation default-ON (`25b54d4`); `--trust-quotes-after 1783985376` for post-fix ladderless records |
| Quote sanity alarm | `mirror_v3/copy_watcher.py` `quote_sanity_msg` | crossed-book LOUD alarm (`5ce37ba`); 27 watcher tests total |
| Shadow probe battery | `mb_copyable_data/shadow_probes_20260713.{py,out}` | S1-S5 pre-registered descriptive; capacity/spread/tax measured; S1/S2 await resolutions |
| Stress suite | `tests/unit/test_mirror3_stress.py` | 9 tests/10 invariants (cloud session `d7fa2bf`); full mirror_v3 surface 93+ green |
| **Chain deep-dive gate (roster admission)** | `scripts/chain_deep_dive.py` + `tests/unit/test_chain_deep_dive.py` | NEW 2026-07-14 (`0231f2c`): 4-tier chain-native gate (lifetime dual-era reconstruction → API↔chain reconcile both ways → chain skill re-grade → forensics/fair-HFT); adversarially reviewed (16 findings fixed) + smoke-validated; `--self-test` 16-case verdict table + 23 pytest green (local+VPS venv); read-only, reuses siblings as-is. **47-batch RUNNING** (see §0). |
| **Shadow readout (fresh-label, per-cohort)** | `scripts/shadow_readout.py` + `scripts/analyze_shadow.py --traders` | NEW 2026-07-15: rebuilds token→outcome FRESH from `markets` each run (default gamma cache is stale — §7 landmine), splits cohort-1 / cohort-2 (never pooled), writes an ALERT on power-bar / negative-firming. Both `--self-test` green. Runs daily on the VPS (durable clone `/opt/pa2-shared/mb_readout`); log `shadow_readout_log.txt`, alert `shadow_readout_ALERT.txt`. |

## 5. Open threads / what's next

### TO-DO (2026-07-14 plan — next session starts HERE)

> **STATUS 2026-07-14 PM:** item 1 **DONE** (`chain_deep_dive.py` built,
> reviewed, smoke-validated, `0231f2c`); item 2 **RUNNING** (47-batch launched
> 19:31 UTC — see §0 for collect/monitor + admission-gate instructions); item 3
> (operator admission word) pending the batch results.

1. **[build, DONE `0231f2c`] `scripts/chain_deep_dive.py` — the roster-admission
   gate (operator-mandated: no trader joins any roster without it).**
   - Tier 1: lifetime fill reconstruction from chain, BOTH eras (V1
     OrderFilled + V2 fill topic, server-side owner-topic filter; reuse
     `mirror_v3/copy_watcher` decoders + `readjudicate_discrepant.py`
     dual-era pattern). ~40-60 min/trader at 6 rps on tenderly.
   - Tier 2: API↔chain reconciliation BOTH directions (API claim absent
     on-chain after exhaustive sweep = fabricated; chain fill absent from
     API = hidden activity).
   - Tier 3: skill re-grade on the chain-reconstructed record (same
     walk-forward hire bar; resolutions from the CLOB label cache).
   - Tier 4 forensics: counterparty concentration (wash), copier-latency
     (are THEY copying someone — double-lag alpha), funding lineage
     (sybils), maker/taker + rate profile (true lifetime bets/day — the
     fair HFT test the burst-page filter never ran).
   - Admission = zero contradictions + chain-graded skill clears the bar
     + no forensic flag. Evidence gaps → deeper search, never quotas.
2. **[run] Deep-dive batch (~47):** 13 cohort-2 candidates (9 VINDICATED
   + grey-4) **+ 34 HFT-borderline** (incl `0xa6a856a8c8a7…`). Optional
   wave 2: the 38 ALL-universe scope-outs. Overnight batch, read-only.
3. **[operator] Cohort-2 admission word** AFTER deep-dive results — own
   start date, separate readout, never pooled with cohort-1.
4. **[analysis, when markets resolve] Rerun probes S1/S2**
   (`shadow_probes_20260713.py`) — gate-optionality (OK vs RAN_AWAY
   win-rate) + conviction-signal cells; both pre-registered 2026-07-13.
5. **[readout, ~2-4wk from 2026-07-13 23:29 UTC] `analyze_shadow.py
   --trust-quotes-after 1783985376`** — the pre-registered verdict.
   Pre-fix records auto-repair from ladders; criteria unchanged.
6. **[flag-flip, proposed] Record roster SELLs** in the watcher (record-
   only, no strategy) — starts the exit-follow dataset clock.
7. **[build, before any real order flow] Per-event exposure caps in
   sizing** (neg-risk sibling correlation — the one guard gap; belongs in
   sizing, NEVER market gating per CLAUDE.md Bug-14 ban).
8. **[monitor, ~daily] `systemctl is-active polymarket-mirror3; wc -l
   /opt/pa2-shared/mirror3_shadow.jsonl`** + journal grep for
   `QUOTE SANITY` (must be absent) and `CANARY ALARM`.

- **[superseded — resolved threads]** The walkforward3 decision tree ran
  to PASS; audit + re-adjudication + symmetric check complete (all 29
  clear); deploy-version question resolved (`25b54d4` live); crypto
  kill-test ran → UNRESOLVED (out of scope by default, never "killed").

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

- **The V1 exchanges are DEAD for live flow (2026-07-12).** Any forward-
  looking on-chain detection MUST use the V2 exchanges + topic0
  `0xd543adfd…` (constants in `mirror_v3/copy_watcher.py`); V1
  `OrderFilled` via `blockchain_client` constants is history-only (audits
  of pre-migration fills). A watcher/canary pointed at V1 reads as
  "running, zero events" — silently. Also: the V2 fill event does NOT
  carry BUY/SELL; direction needs the receipt's transfer logs.
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

### Added 2026-07-14 (local steward session)

- **CLOB `/price` `side` names the BOOK SIDE read: BUY=best bid, SELL=best
  ask.** The watcher shipped with it REVERSED (every record's bid/ask
  swapped, fills at the bid). Pin it any time with
  `scripts/verify_clob_price_sides.py`; the watcher now alarms LOUDLY on a
  crossed book. Fix-deploy epoch `1783985376` — analyze_shadow needs
  `--trust-quotes-after` that value for ladderless post-fix records.
- **`scripts/audit_roster_chain.py` is V1-ONLY — its not_found column is
  structurally inflated for post-migration fills.** Superseded by the
  dual-era `readjudicate_discrepant.py` (`fa21111`). Never adjudicate
  anyone on the old audit's not_found numbers.
- **not_found is an evidence gap, NEVER an accusation (operator rule
  2026-07-14).** The quota rule is gone; escalate the search (window,
  samples, second RPC, dual-era) until silence survives an EXHAUSTIVE
  sweep — only then is it fabrication evidence.
- **The HFT/bot filter (`looks_like_market_maker`) judges a 1-2.5 day
  burst page, not lifetime rate** — it can dismiss bursty humans (34
  borderline, incl a run-1 strong candidate). The fair lifetime test only
  exists for FETCHED histories; chain deep-dive Tier 4 replaces it.
- **The local Claude app yanks the MAIN checkout between session branches**
  (mis-branched a commit 2026-07-13). MB local work happens in the
  dedicated worktree `C:/lockes-picks/mb-steward` (operator-directed
  exception to the parent-dir fence; the checked-out branch is thereby
  locked). TWO WRITERS share the branch — `git pull --ff-only` before
  every commit.
- **`pgrep -f` waiters self-match their own command line** — bracket the
  pattern (`discrepan[t]`) in the WAITER too, or wait on output files
  (bit us 2026-07-13: a waiter hung 3+ hours on itself).
- **Shared `.env` line 367 is malformed** — python-dotenv aborts there;
  scripts needing DATABASE_URL must `set -a; . /opt/pa2-shared/.env` in
  the shell (runbook pattern) AND call `await db.init()` (crypto_kill
  shipped without it — any new DB runner needs an integration smoke-run
  `--days 1` before handover, not just unit tests).
- **`detect_lag_s` can be legitimately negative** (~-1s; producer-set
  block timestamps) — clamp to 0 in ANALYSIS, never "fix" the recorder.
  `block_ts` falls back to detect-time on fetch error (lag reads 0).

### Added 2026-07-14 PM (chain deep-dive build + batch launch)

- **`/opt/pa2-shared/mb_copyable_data` is ROOT-owned** (`drwxr-xr-x root
  root`) — `polymarket` (which runs the batch via `sudo -u polymarket`)
  CANNOT create files/dirs there. The deep-dive batch writes into a
  pre-created `polymarket`-owned subdir `.../deep_dive/` (both `--out-dir`
  AND `--out` live inside it). Any new polymarket-run job writing durable
  output needs a `sudo mkdir + sudo chown polymarket:polymarket` subdir first
  (first batch launch crashed at `os.makedirs` PermissionError).
- **`pgrep` without `-f` does NOT match python-script jobs** — the process
  `comm` is `python`/`python3`, not the script name, so `pgrep 'chain_deep_
  dive[.]py'` returns 0 (false "it died!"). ALWAYS use `pgrep -f` (or
  `pgrep -fc`) for these; the batch was falsely reported dead once this way.
  A `setsid`-launched job is reparented to PID 1 — verify with `ps -ef`.
- **`sudo env DATABASE_URL=...` exposes the DB password in the process
  table** (`ps -ef`). It is a localhost pgbouncer credential and this matches
  the deployed-service pattern (sudo scrubs env, so the value must be passed
  as an argv assignment) — acceptable on the single-tenant VPS, but do NOT
  echo `ps`/pgrep output containing it into chat/logs. Extract it at runtime
  (`grep '^DATABASE_URL=' /opt/pa2-shared/.env`) so it never lands in a file.
- **chain_deep_dive reconciliation must PRESERVE reconstructed direction** —
  reconcile_api_to_chain only takes BUY-side chain fills as candidates; folding
  SELLs/direction-unknown V2 fills into BUY-shaped candidates would let an API
  BUY 'verify' against a SELL (mask a lie) or false-mismatch (false REJECT).
  Same discipline: `direction_complete` (v2_receipts >= v2_txs) gates the
  direction-dependent tiers — a receipt-capped sweep reads real BUYs as unknown
  and would manufacture false not_found → INSUFFICIENT (raise --max-receipts),
  never REJECT. And reconcile ONLY API BUYs inside the SWEPT block window
  (`window_api_buys`) — a bounded/narrow sweep vs full-history API BUYs invents
  false FABRICATION (both bugs were smoke-caught 2026-07-14, now unit-tested).
- **UNCOPYABLE (fill-rate) is checked BEFORE the direction-complete gate** —
  it needs no receipts, so a genuinely un-tailable HFT account rejects fast
  without paying for full receipts; and it uses FRACTIONAL span-days (integer
  floor inflated the rate for the 1-2.5-day-history borderline cohort).

### Added 2026-07-15 PM (shadow readout — stale-label trap)

- **`analyze_shadow.py --gamma-cache` SILENTLY GOES STALE → false "0
  resolved / UNDERPOWERED" that MASKS the real edge.** The gamma resolution
  cache (`copyable_cache/gamma_resolutions.json`) is from 2026-07-10 and
  covers ZERO of the shadow markets (07-13+), so the readout reported 0/30
  resolved when the live `markets` table already knew ~10 resolved — AND the
  early edge on those was NEGATIVE (the stale cache hid a real signal).
  Operator caught it ("0% chance 0 are closed after 3 days"). **NEVER trust
  the default gamma cache for a readout.** Use `scripts/shadow_readout.py`
  (rebuilds token→outcome FRESH from `markets` every run; per-cohort split via
  `analyze_shadow --traders`; writes an ALERT on power-bar / negative-firming).
  This is the Forbidden-Pattern-9 discipline: an impossible number (0 resolved)
  means the QUERY is wrong — fix the source, don't explain it away.
- **EARLY FORWARD SIGNAL (2026-07-15, DESCRIPTIVE, n=10 UNDERPOWERED):**
  cohort-1's shadow edge on the ~10 resolved-so-far is **NEGATIVE**
  (edge ≈ -0.048, P(edge>0) ≈ 0.37) net of the ~1c copy tax. NOT a verdict
  (need ≥30), but it leans the WRONG way — the retrospective +edge may not
  survive our spread/latency. Watch as resolved climbs; the same tax applies
  to the 8 cohort-2 admits, so their forward shadow is the real test.
- **Shadow token→outcome join:** `markets` rows key outcomes by
  `yes_token_id`/`no_token_id` (resolution YES ⇒ yes-token won). The shadow
  records carry only `token_id` (no condition_id), so resolve via those two
  columns, not condition_id.
- **Watcher-fidelity audit 2026-07-15 PM (operator-challenged, MEASURED
  CLEAN):** operator challenged the low shadow wager counts ("0% chance
  elites trade this little"). Head-to-head vs the independent data-api per
  trader over each cohort's own window: cohort-1 matches near-exactly
  (197=197, 1171=1171, 82=82; the 9 zero-record C1 traders show 0 API buys
  too — genuinely idle 2 days), cohort-2 windowed at its 19:20:45Z start
  matches exactly (2=2, 1=1, rest truly 0 fills). 0 dropped-window markers, 0
  side-unknown skips. VERDICT: instrument faithful; the confusion was
  cohort-mixing (the 38-170/day rates are COHORT-2 machines; cohort-1 are
  slow/idle humans) + units (records ≈ re-buys; graded unit = first-buys).
  CAVEAT THIS SURFACED (precise form): cohort-1 flow is EXTREMELY
  concentrated, on BOTH axes — RECORDS are 72% one trader (0x84dbb7,
  1,171/1,627 — mostly re-buys), while the EDGE ESTIMAND (first-buys) is
  led by a different one (0x448861, ~20/51 ≈ 39%). Either way a pooled
  cohort number can be one trader's story. STANDING OPERATOR RULE
  (2026-07-15): every readout DISCLOSES concentration inline and auto-
  prints a leave-one-out line when the top trader ≥ 50% of first-buys
  (shadow_readout `concentration()`/`--conc-threshold`); every ALERT
  carries it; NO aggregate is presented without its composition checked
  first. Even the C2 machines are bursty (0x0e5bd7: 0 fills in its first
  5.5h despite a ~111/day lifetime rate).
- **The daily readout cron is BRANCH-PINNED + leaves a root gitconfig
  mutation (session-close review #17/#30):** `deploy/shadow_readout_cron.sh`
  hard-resets `/opt/pa2-shared/mb_readout` to `claude/repo-setup-docs-fq9bhn`
  daily — if the MB lane moves branches, UPDATE the BR pin or the readout
  runs frozen code forever (a refresh failure now writes a WARN line into
  `shadow_readout_log.txt`). Cohort membership is read from
  `chain_audit.json` at runtime (not code), so admissions don't need a code
  change — but they DO need the cohort ledger keys extended or the readout
  refuses to run. Setup also left a `safe.directory /opt/pa2-shared/mb_readout`
  entry in ROOT's global gitconfig (harmless, recorded here).
- **An RPC await with no read-timeout can park a batch FOREVER — and
  process-liveness monitoring cannot see it (2026-07-16):** run-2 hung ~13h
  on ONE `get_transaction_receipt`/`get_logs` await (zero CPU, ZERO open
  sockets) while the event loop stayed alive — `db_pool_health` heartbeats
  kept printing, so `ps`/pgrep checks looked healthy. Fixed `07e7296`:
  `rpc_call()` wraps EVERY chain RPC in `asyncio.timeout(90)` (hang → counted
  retryable error). Monitoring rule: watch LOG GROWTH (the code heartbeats
  through every phase), never just process existence. Timeout-guard every
  network await in any new long-running chain runner. **The LIVE WATCHER
  shared this class (6 unguarded web3 awaits, no systemd watchdog) — fixed
  `336f6a4` (rpc_call wrapper, 43 tests green), **DEPLOYED 2026-07-16 16:40:53
  UTC (operator go): /opt/mirror3 = `5c91261`, watcher blob verified
  byte-identical to tested, roster=24 reloaded, canary 1216, 0 alarms.
  Restart boundary note: FirstBuyDedup reset at 16:40:53Z.** Sibling one-shot
  scripts (audit_roster_chain, readjudicate) share the class but are
  operator-attended — a hang is visible, not silent (documented, not churned).
  Restart side-note: each watcher restart resets FirstBuyDedup, so a token
  seen before restart can record first_buy=True again — token-clustered
  analysis absorbs it, but don't be surprised by duplicate firsts at restart
  boundaries.
- **pkill self-match, VARIANT 2 (bit TWICE 2026-07-15):** bracketing the
  pkill pattern (`chain_deep_di[v]e`) is NOT enough when ANY OTHER clause of
  the same SSH command contains the literal name — a `pgrep -fc
  'chain_deep_dive[.]py'` check, or even a file path (`git hash-object
  scripts/chain_deep_dive.py`). pkill -f matches the whole remote shell's
  command line, which includes those literals → kills your own session
  mid-command (exit 255; the rest of the command never runs — our /tmp/mbre
  refresh silently didn't happen). RULE: a kill command contains the pkill
  and NOTHING ELSE that names the target; verify/refresh in a SEPARATE
  ssh command afterwards (plain `ps -ef | grep` there is safe — no pkill in
  that shell).
