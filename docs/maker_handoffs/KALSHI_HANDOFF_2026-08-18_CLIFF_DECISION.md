# KALSHI HANDOFF — 2026-08-18 (R1 closed, $1-cliff canon, scaling recalibrated, DECISION PENDING)

STEP ZERO for the next session: read this doc, then
`KALSHI_R1_VERDICT_AND_CLIFF_CANON_2026-08-18.md` (canon + review dispositions), then
`KALSHI_SCALING_STUDY_AND_CLIFF_BUILD_2026-08-18.md` (NOTE: its $12-16/day projection
is RETRACTED — see 'Recalibration' below; v2 numbers govern). Verify live state
yourself (ritual at bottom). Trust nothing you didn't verify.

## THE PENDING OPERATOR DECISION (asked 2026-08-18 ~17:0xZ, unanswered)
The mandate question "reliable >$10/day?" was answered from measured data: NO at
current universe/capital. Options presented:
  (a) build+run concentrated-cliff mode at the honest $2-5/day net expectation
  (b) KILL (under the operator's $10/day bar; sub-linear size scaling)
  (c) one more $0 pass: universe recount under the plan's exact filters (survivable
      classes, close<=8d, window>=49h) hunting richer qualifying markets, then decide
Do NOTHING live until the operator picks. 08-27 is the pre-registered decision date.

## STATE (all verified 2026-08-18, reads timestamped in session docs)
- Bot DOWN+disabled (since 08-12T19:50Z). NO STOP file (cleared by operator GO
  2026-08-13, archived STOP.cleared-20260813_234503) — NOTE: place-paths refuse only
  on STOP presence; nothing is armed and no probe state blocks are pending EXCEPT
  r1_probe_state.json still EXISTS on the box (probe concluded; archive it before
  any new probe use).
- Balance $246.8126 (read 15:48:09Z). Open: KXNETFLIXTOPVIEWSMOVIE-26AUG17-40 +8 YES
  (marks ~0.015, settles on its own). Legacy TOPMODEL-26AUG17-CLAUM residual settled?
  (was in wind-down list; verify).
- R1 probe: CLOSED. Realized −$10.66 (12 fills, ALL 5 settled markets adverse — D3
  live), credits $0 (cliff-predicted). Hourly kalshi-r1-status.timer STILL RUNNING
  (exit 0; harmless — status no-ops sanely; disable when archiving probe state).
- reward_pnl gauge FIXED+DEPLOYED (md5 1ecd8060 = blob `2b4a5e4`-era commit): history-
  based accrued-at-conclusion + per-program cliff (SUBCLIFF status). First real run:
  n_subcliff 6, true leakage 0, caught TRUMPTIME-26AUG15 paid $1.00 → lifetime paid
  $205.06.
- Scoreboard still scores the DEAD 08-12 window (re-registration required before any
  new scored window — unchanged guardrail).

## ⛔ CANON ESTABLISHED THIS SESSION (docs committed, adversarially reviewed)
1. **PER-PROGRAM $1 CLIFF** (`KALSHI_R1_VERDICT_AND_CLIFF_CANON_2026-08-18.md`):
   payment = accrued iff single program ≥ $1.00 at conclusion (bracket
   $0.9719-$1.0034), else $0; no event aggregation (credit rows are per-program —
   TOPMODEL paid as 2 rows); ratio ~1.0 above. Backtest 38/38 events, pred $6.13 vs
   paid $6.11. 5 above-cliff confirmations incl. TRUMPTIME-H3 $1.0034→$1.00.
   Sub-$1-pays-$0 ESTABLISHED (30+ obs); above-cliff-pays ESTABLISHED-for-class.
2. **R1 verdicts**: sub-target books DO accrue (floor-at-scoring refuted); presence
   must be near-touch PAIRED (wide pairs scored $0); fills stop scoring; quiet
   data-release books at the touch fill ONLY adversely (5/5).
3. **Scaling (v2, hardened per review — v1's $1.56/day + linear CLAIMS RETRACTED)**:
   survivable classes: 1-9ct $0.24/d, 10-29ct $1.10/d (13 tk-days), 30-59ct thin
   (n=3 tk-days). HONEST per-market-day: $0.50-0.63 best real days. Within-ticker
   size contrast: 1.8x accrual for ~4x size = SUB-LINEAR. Existence proof: TOPMODEL-
   26AUG31 pair cleared cliff at 10-50ct over ~3d windows and PAID in full.
   → Portfolio ceiling at current universe: ~$4-6.5/day gross, $2-5/day net (INFERRED
   from measured rates; fill-cost side still unbudgeted per review F14).
   Scripts: workflow_scripts/{size_scaling_study.py (v1, superseded),
   size_scaling_v2.py, r1_cliff_hypothesis_test.py, r1_ratio_study.py}.

## OPEN ITEMS (nothing demoted; Rule Nine)
- F9 (cliff review): re-read credit_history for sub-$1 events ≥7d past ends (~08-21+).
- Scaling review F9/F14/F15: universe recount under exact plan filters; per-market
  fill-cost budget; per-market $ caps + halt semantics at 40-60ct — ALL required
  before any relight ask, if (a)/(c) chosen.
- v2 study findings not yet folded into the build doc (do on decision).
- 1,673 ticker→pid map collisions flagged in v2 (dailies re-key programs — v2 sums
  sibling pids per ticker, but verify no double-count class).
- kalshi-r1-status.timer disable + r1_probe_state.json archive (operator-visible act).
- Old-window §5 scoring formality (was due 08-19 in original calendar).
- Parked items from the 08-13 roadmap §6 remain parked on their named triggers.

## STANDING RULES REINFORCED THIS SESSION
- Per-section adversarial review INCL. EV lens (operator-directed, memory
  feedback_adversarial_review_per_section_incl_ev.md) — it caught: 4 probe blockers,
  the R0b capital-conditioning retraction, the cliff-study provenance gap, and the
  scaling study's 3 inflation biases BEFORE money moved. Non-negotiable.
- Venue lesson: client_order_id rejects '.' (400 invalid_parameters).
- All 13 hook-injected rules bind; 07-27 session stays quarantined.

## VERIFICATION RITUAL (next session, before any work)
1. git worktree list → kalshi-wt; branch claude/maker-kalshi-live; HEAD ≥ `0fcbc45`
   (+ the reward_pnl fix commit + cliff docs; `git log --oneline -8`).
2. SSH: service inactive+disabled; resting orders = 0 (paged read); balance read.
3. md5 deployed reward_pnl_report.py = 1ecd8060; r1_floor_probe.py = f5123eec-era
   (check `git show HEAD:kalshi_live/r1_floor_probe.py | md5sum`); quoter untouched
   (CRLF blob of `122dd44`).
4. pytest kalshi_live → 1381 passed / 2 xfailed exit 0.
5. reward_pnl 07:30Z + scoreboard 07:40Z unit exit codes.
6. Then: get the operator's a/b/c answer and proceed accordingly.
