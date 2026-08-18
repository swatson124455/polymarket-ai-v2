# R1 VERDICT + THE $1-CLIFF PAYMENT CANON (2026-08-18)

## ⛔ CANON — THE PER-PROGRAM $1 CLIFF (backtested 8/8 exact; ESTABLISHED)

**Rule: each MARKET's liquidity program pays its accrued reward IF AND ONLY IF that
single program's accrued-at-conclusion ≥ $1.00. Sub-$1 programs pay exactly $0.
No aggregation across sibling markets of an event. Above the cliff, paid/accrued
≈ 0.995–1.0 (the estimates feed is honest).**

Evidence (read 2026-08-18T15:42:06Z; denominator = ALL 8 events / 22 programs with
estimates-tape accrual history whose program end + 48h payment envelope had passed;
prediction error ≤ $0.02 per event, $5.13 predicted vs $5.11 paid total):

| event | per-program accrued at end | predicted | paid |
|---|---|---:|---:|
| KXAPRPOTUS-26AUG07 | 0.97, **1.63**, 0.30, 0.48 | 1.63 | 1.63 |
| KXTOPMODEL-26AUG31 | **1.05**, **1.42** | 2.47 | 2.46 |
| KXADJOURNRECESS-26AUG | **1.03** | 1.03 | 1.02 |
| KXACTBLUETOP-26AUG07 | 0.04, 0.82, 0.04, 0.33 | 0 | 0 |
| KXAAAGASD-26AUG07 | 0.55, 0.00, 0.24, 0.21, 0.08 | 0 | 0 |
| KXAAAGASD-26AUG08 | 0.01, 0.64, 0.06, 0.01, 0.19 | 0 | 0 |
| KXGENERICBALLOTVOTEHUB-26AUG14 | 0.59 | 0 | 0 |
| KXDXYDUD-26AUG12 | 0.58 | 0 | 0 |

Consequences (all now design LAW for any future rung):
1. Revenue per market = accrued (feed-verifiable hourly) if ≥ $1/program-period, else 0.
2. The earlier "payment is unreliable / APRPOTUS 0.48 haircut" scare is RESOLVED —
   payment is deterministic; there is no other qualifying condition in this sample.
3. Any entered market MUST be sized/quoted to clear $1/program with margin or it is
   guaranteed dead weight. W10's event-level "$1 floor" canon is REFINED: the floor
   is per-PROGRAM (per-market).
4. The reward_pnl LEAKAGE detector was structurally blind to exactly the stiffed-
   sub-$1 class (it reads the LATEST snapshot; concluded programs vanish from the
   feed → no row at all). Its historical `n_leakage: 0` outputs were vacuous.
   Fix queued (read accrued-at-conclusion from tape history).

## R1 FLOOR-PROBE FINAL VERDICT (probe t0 2026-08-13T23:45:05Z → halt 08-16T21:48Z)

Pre-registered questions and answers:
- **(a) Does sub-target presence accrue? YES — floor-at-scoring REFUTED.** 5/7
  programs accrued nonzero on books far below Target (peaks: DEEP $0.40, OPENSHARE
  $0.29, TENC $0.26, NETFLIX $0.13, UE $0.11; the 2 wide-paired YT markets ~$0 —
  presence must be near-touch paired to score).
- **(b) Do sub-$1 accruals PAY? NO — $0 credits, and the cliff canon above now
  explains it deterministically** (every probe program finished sub-$1).
- **(c) Who fills a lone/joining quote in a quiet data-release market? THE INFORMED
  SIDE, every time.** 12 fills since t0; **all 5 settled markets resolved against
  every position we held** (settlements read 2026-08-18T15:48:09Z: YT-YOU no,
  YT-JUS no, OPENSHARE yes vs our NO, TENC no vs our YES, DEEP yes vs our NO).
  D3's −4.8c/ct maker adverse-selection, measured live at min size: our fills were
  not noise, they were the data release walking through us.

**Final probe accounting (ESTABLISHED, reads 15:42/15:48Z):** realized −$10.66
(12 fills' cash, all settled inventory worthless; NETFLIX +8 YES still open, marks
~0.015 → final lands in [−$10.66, −$2.66], expected ≈ −$10.66). Credits $0
(cliff-predicted). Balance $246.8126. Attribution: this is the STRATEGY's own
adverse-selection cost operating as measured in D3 — no agent-defect class fired;
the probe bought its answers at approximately its designed tail (~$9.2 predicted,
−$10.66 landed, sizeup added the difference).

**Knowledge bought for the ~$10.66:** the $1-cliff canon (via the question the probe
forced), floor-at-scoring refuted, fills-kill-accrual (filled orders stop scoring —
re-quote or die), near-touch-paired scoring requirement, live calibration of
accrual-per-$-presence, and a live confirmation that quiet data-release books fill
ONLY adversely at the touch.

## The honest R2 arithmetic this leaves behind (INFERRED, from R1's own numbers)

R1's whole book: ~$1.19 gross accrual potential vs −$10.66 realized fill cost —
revenue/cost ≈ 0.11 at touch-quoting min-size shape. **Touch-quoting in these
markets is refuted as an earning shape.** The only shape the cliff canon + DF math
leaves open: DEEP-BUT-QUALIFYING quotes at LARGE size (score = DF^N × size can clear
$1 from 2–3 ticks off the touch at target-scale size, where fill probability is far
lower — the sweep must chew through the touch AND the intervening ticks first).
That shape is exactly what the next-rung decision is about; design + sizing math in
the R2 proposal (separate doc / operator decision).

# ============ ADVERSARIAL REVIEW DISPOSITIONS + HARDENED RERUN (2026-08-18 ~15:5xZ) ============
Per the standing per-section review rule. Reviewer findings F1-F15; dispositions:

**Hardened rerun (script `workflow_scripts/r1_cliff_hypothesis_test.py` — the REAL
per-program script, archived; the earlier r1_ratio_study.py was event-level and
could not reproduce the table = review F5, provenance gap CLOSED):**
- **38/38 events match** over the FULL tape denominator (120 tape programs, 12
  dropped unmapped/one-field — disclosed per F7; read 2026-08-18T15:54:48Z).
  Total predicted $6.13 vs paid $6.11.
- **NEW 4th above-cliff case: KXTRUMPTIME-26AUG15-H3 accrued $1.0034 -> paid $1.00**
  -> threshold bracket TIGHTENED to ($0.9719, $1.0034] (F1: stated as "$1.00
  within that bracket", not an exact constant; R2 sizing must dominate bracket +
  feed staleness -> design to >= $1.50/program).
- **F3 ANSWERED: credit rows are PER-PROGRAM** — KXTOPMODEL-26AUG31 paid as TWO
  rows ($1.05 + $1.41, both 08-10T18:25Z). Payment granularity is per-program;
  the event-aggregation alternative is excluded mechanically, not just by the
  ACTBLUETOP instance.
- F6/F8 hardening in the archived script: per-program staleness flagging (none
  tripped), max-ts ordering, malformed-ts tolerance.

**Label corrections adopted (F2/F11/F13/F14/F15):**
- Sub-$1-pays-$0: ESTABLISHED (30+ program observations + W10 lifetime zero
  sub-$1 rows... now zero sub-$1.00 rows with min-ever $1.00).
- Above-cliff-pays-accrued: ESTABLISHED for this class (n=4 programs, politics/
  model series); INFERRED as venue-universal until another series family confirms.
- Mechanism wording: the TRADING RULE is law; whether the venue pays *nobody*
  sub-$1 vs drops *our* row vs redistributes is INFERRED (per-user-indistinguishable
  in this sample) — do not reason about competitor behavior from it.
- "Touch-quoting refuted" is SCOPED: refuted at min size in thin data-release
  books (the probe's class). The lifetime record includes near-touch temp/gas
  presence that earned (W10); the unqualified sentence in the section above is
  superseded by this scoping.
- "Deep-but-qualifying at size" is NOT a residual winner: it is the one untested
  shape, with an UNMEASURED walk-through cost tail (R1's finding (c) is mild
  evidence AGAINST it in data-release books — informed repricing jumps depth).
  Any R2 must bound that tail by design (no-catalyst windows, per-event size caps)
  and is a fresh hypothesis test, not a narrowed-down winner.
- The 0.11 revenue/cost figure is a whole-probe blunt verdict (mixed size regimes,
  F15) — never a per-shape unit economic; use D3's -4.8c/ct for per-unit cost.
**Queued (F9):** re-read credit_history for the sub-$1 events >= 7 days past their
ends (~08-21+) to close the late-payment censoring hole.
