# MB OVERHAUL - WHAT TO ACTUALLY REVIEW (evidence-cited agenda)

**Generated 2026-08-25 (~03:45Z)** at operator request ("do a proper review for
what we should actually review"). Method: 16-agent parallel survey over the
worktree (8 areas: watcher, pipeline, chain screen, charters, legacy, tests,
data, economics), every finding adversarially citation-checked (87 of 92
confirmed), plus direct VPS reads (services, journals, crons, disk) done
outside the agent fleet. Full evidence pack:
`docs/mb_overhaul_review_findings.json` (every finding with file:line
citations and verification verdicts). This agenda is REVIEW-ONLY - nothing
here demotes, removes, or changes anything; every action is operator-gated.

---

## TIER 1 - decisions the overhaul cannot proceed without

### 1. WHICH BOT IS THE FUTURE? Two MirrorBots run today.
- `polymarket-mirror` (legacy) actively paper-trades: 794 paper trades in 7
  days, open_positions=7, elites=300, own RTDS pipe at 22.7M dispatched
  (journal 2026-08-25T03:35Z). The rebuild plan adjudicated its strategy
  core; it was flipped to paper INSTEAD of stopped explicitly to keep a label
  stream alive (MB_REBUILD_PLAN.md Decision 5).
- REVIEW: does ANY current instrument still consume the legacy label stream
  (mirror_rejected_signals), or has mirror3's sink fully replaced it? If
  nothing consumes it, what justifies the compute + the DB noise
  (MirrorBot vs MirrorBotV3 rows accreting in shared tables)?
- LIVE DEFECT surfaced during this review (legacy): a position stuck ~678h
  (28 days) in a force-exit loop because the 10% adverse-slippage guard
  blocks its own force-exit (SELL basis 0.68 vs market 0.31 = 54.4%
  slippage, "Paper trade FAILED", journal 08-25). The guard deadlocks the
  exit. Also ~793 error-lines/24h incl. recurring DB statement-timeouts
  falling back to defaults. Fix is operator-gated; reported here.

### 2. CAN WE TRUST THE NUMBERS THE OVERHAUL WILL BE JUDGED BY?
- **Three different "edge" estimands share one name**: analyze_shadow pools
  PER-FILL, band_tracker PER-MARKET, cohort5 reuses per-fill
  (analyze_shadow.py:214-239; band_tracker.py:75-78). Cross-script edge
  comparisons are apples-to-oranges today.
- **Fee-model divergence, live right now**: cohort5_qualification grades on
  the deprecated flat-2% model (never passes fee_rate_map;
  cohort5_qualification.py:127-130) while analyze_shadow's own docstring
  rules the venue formula governs post-2026-08-19 registrations
  (analyze_shadow.py:176-187). Flat 2% OVERCHARGES high-priced fills (at
  p=0.9: 0.018 vs 0.0063 true) - a trader can be permanently DQ'd by the
  fee model, not the data. The 5 consumed locks were graded under flat fee
  (immutable; disclosed). **The C1_UNTESTED group registered 08-24 is on
  the wrong fee model and its first look is weeks away - fixable before any
  look is consumed, with operator go.**
- **Band population is not roster-gated in code** though the frozen estimand
  says "ALL clean-roster traders" (band_tracker.py:55-100): roster changes
  silently change a locked running test's population.
- **Locks are taken on resolution-timing-selected subsets**; the proven
  precedent (07-22) is the missing slice was systematically negative.
  Locks do not record label-completeness at lock time.
- **No multiplicity control on ~29 single-look arms at P>=0.95** (expected
  false qualifiers under the null ~1.45) while shadow_readout applies
  Bonferroni to its DIAGNOSTIC display for exactly this reason
  (shadow_readout.py:290-299).

### 3. THE COHORT BAR CONSUMES CANDIDATES AT 7-8% POWER - REDESIGN RULING NEEDED BEFORE MORE LOOKS BURN
The lane's own 08-19 analysis (MB_STATE.md:419-437, :513-518): P>=0.95 at
n=30 needs a +0.127 point estimate (6.4x the +0.02 floor); the full bar can
NEVER pass for true edge <= ~+0.022. The band got the anytime-valid
redesign; the cohort bar did not. Since then: 5 of 20 cohort5 looks consumed
(0 qualified), and on 08-24 the 9 cohort1-untested were armed under the SAME
bar ("do it if they pass the test" - the ruling did not surface the power
problem; this session's omission). REVIEW: pause/re-register the remaining
15 + 9 under an e-process design before more one-shot tests are irreversibly
consumed? (Pausing = operator decision; windows are young, nothing is close
to a look.)

### 4. THE ECONOMICS HAVE NO REVENUE MODEL - AND THE SKETCH SAYS "SMALL"
- The $500->$5k funding ruling has no EV computation attached. INFERRED
  sketch from the docs' own numbers (in-sample flow, stated as
  hypothesis-generation only): band-only success at the +0.02 gate at ~4
  resolved band mkts/day x $300 cap = ~$24/day GROSS ceiling at full $5k
  deployment; ~$2-3/day at the $500 pilot. Nothing computes whether that
  covers the operation.
- The +0.02 econ floor was never derived; it approximates the SUPERSEDED
  flat-2% cost (= break-even, not profit) and mid-band venue cost is now
  ~0.7-1.2c.
- The expensive deep-dive screen (~2.2h/trader) selects on chain edge - a
  quantity the lane itself measured to NOT predict copy edge
  (COHORT5_PREREGISTRATION.md:15-25).
- **Exit-side blindness**: every instrument measures first-buy entries;
  the record-only roster-SELL flag proposed 07-14 was never executed - a
  funded bot must pick an exit policy with ZERO forward exit data. The
  clock on that dataset only starts when recording starts.
- REVIEW: pre-register the revenue model per success scenario; derive the
  floor from venue fee + measured tax + required margin; define the
  screen's job (fraud-only?) and unit cost; rule on shipping the SELL
  recorder (cheap, additive, operator-gated).

## TIER 2 - evidence infrastructure the rebuild inherits

### 5. THE EVIDENCE BASE IS SINGLE-COPY ON ONE BOX
All decision-bearing artifacts (sinks, locks, ledger, durable log, caches -
mb_copyable_data alone is 15GB, disk 65%) live only on the VPS; the only
scheduled backup is a pg_dump of the DB to the SAME machine, 7-day
retention. One disk event erases the lane's entire forward evidence.
Also: sinks grow unbounded (113MB + 81MB and daily full reloads); the
canonical loader silently drops ANY undecodable line (mid-file corruption
is invisible); the band e-value trajectory exists only as stdout lines in
an unrotated log; verdict-lock immutability is enforced only by writer code
on plain JSON.

### 6. KNOWN ESTIMAND-STABILITY DEFECTS IN THE WATCHER
- FirstBuyDedup + TrailingMedians are memory-only: every restart can inject
  false first-buys and shift conviction annotations (copy_watcher.py:444-457
  vs the bidsim rehydrate pattern :1057-1098 that DOES persist). Is the
  recorded flag advisory (readout re-derives) or consumed as written?
- Boot cursor = head+1: every restart permanently drops the downtime window;
  gap size never measured; watcher cannot start when the DB is down though
  it does no DB writes.
- shadow_fill = size-blind top-of-book ask quoted up to seconds-minutes
  AFTER detect_ts (sequential receipt->block->quote; no quote_ts recorded).
- mirror3 unit has NO WatchdogSec / Restart / MemoryMax (legacy has all
  three post-S249) - the process producing all forward evidence has less
  hardening than the bot being replaced.
- Single hardcoded RPC endpoint; CLOB book errors recorded as NO_BOOK
  indistinguishable from a real missing book; RTDS task death not
  auto-restarted.

### 7. STRUCTURAL DEBT ITEMS (each cheap now, 10x later)
chain_audit.json triple-role (live roster + immutable ledger + audit
output; 12+ consumers, hand-mutated); C1_UNTESTED membership hardcoded in
script vs ledger; gate/repair constants duplicated across scripts instead
of imported; branch-pinned daily-reset checkout runs the whole measurement
stack; three checkouts share data assets with no contract tests; zero test
coverage on both network loops, the entire readout pipeline, restore SQL,
and the cron shell layer; venue semantics pinned as mocks.

## TIER 3 - standing questions already open with the operator
Fill-side ruling (bidsim as chartered cannot decide: bracket [23.6%, 90.3%]
straddles the 74% bar - what instrument CAN decide, given epsilon-size real
orders would need an explicit ruling against 'never trade for data'?);
scout cadence (82-candidate human-scale pool now exists); 12-probe
expansion (roster 31->43) awaiting explicit confirm; the three shared-infra
threads open since 07-22 (backfill poison-batch, end_date_iso NULLs,
force-exit loop - the last now observed live at 678h); docs-sync to master
(PR #5 open); no tracker for the accumulating operator-decision queue.

---
**Disclosure:** two Tier-1 items implicate this session's own 08-24 work
(C1_UNTESTED fee model; extending a bar the 08-19 analysis had already
shown near-unclearable). Both are fixable before any look is consumed;
both fixes are operator-gated and NOT applied.
