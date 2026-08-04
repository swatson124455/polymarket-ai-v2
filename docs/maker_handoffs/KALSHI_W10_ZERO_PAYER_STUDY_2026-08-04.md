# W10 — ZERO-PAYER MECHANISM STUDY (2026-08-04)

**Question (operator-raised):** why did ~20 settled events with real presence pay $0.00
against a ~$26.04 forecast (master plan §3)?

**Scope echo (RULE TWELVE):** all events the account ever traded, due = >72h past latest
close, presence from the venue's own order history, credits from credit_history. The prior
session's 20-event list had no committed artifact, so the set was RE-DERIVED from primary
sources; this study's set is 22 zero-payers of 47 due events (25 paid). Denominators are
stated per finding. All raw pulls frozen in `kalshi_live/w10_results/` (read
2026-08-04T20:12:40Z); scripts `w10_zero_payer_study.py`, `w10_phase2_programs.py`,
`w10_phase3_mechanisms.py`, `w10_phase4_trades.py`.

## VERDICT (one paragraph)

No single mechanism. The dominant terminal mechanism is the **$1 minimum-credit floor
acting on sub-$1 accruals** (H1) — but the reason accruals land under $1 varies: trivial
presence time (16 of 22 events had <40 min), extreme-price presence that LIP scoring
discounts (KXEURUSDAW), a single far strike/window mismatch (KXMUSKNW), and events with
no observable program at all (KXAMSAVO, KXTRUMPUAP, KXLIUKCOUPLE). The operator's
movement hypothesis is **answered: movement is NOT required to be paid** — the
near-motionless KXTEMPNYCH-26JUL2206 (price range 0.09, 12 lifetime trades) paid $12.94
for 2.4 minutes of presence, the best per-hour payment ever observed on this account.
Payment is **not proportional to presence time**: it behaves like score-SHARE × pool
(you split the pool with whoever else is scoreable; empty books pay outsized), which is
also exactly why the shipped forecast (share×pool×time with assumed share) over-predicted.

## 1. THE FLOOR — H1, tested first per ruling

- ESTABLISHED (n=57 liquidity credits, credit_history read 2026-08-04T20:12:40Z): minimum
  credit ever = **$1.01**, sub-$1.00 credits = **0**. Sum $183.95 over 35 distinct events
  (+$15.00 referral = $198.95 lifetime, matches canon).
- Within-series contrast (KXAAAGASD, 8 due events — same family, same program shape):
  every paid event had ≥2.50h presence (JUL21 2.50h→$2.15 · JUL23 9.42h→$10.09 · JUL24
  12.68h→$8.81 · JUL25 26.33h→$11.99); every zero event had ≤2.15h (JUL22 0.04h · JUL27
  0.10h · JUL31 0.01h · AUG01 2.15h → all $0.00). With pool/family held fixed, presence
  duration separates paid from zero exactly — consistent with continuous accrual +
  sub-$1 truncation. Label: ESTABLISHED pattern (n=8), floor inference INFERRED.
- 16 of 22 zero-payers had <0.65h presence; at the pools observed for their families
  (hourly temp programs: period_reward 800000–1000000 → $80–100/day → $3.33–$4.17 per
  1h window per strike, from the 23 retained program rows), even 100% score share for
  their overlap time yields mostly <$1 (phase-2 upper bounds $0.04–$2.06). The two events
  whose 100%-share bound exceeds $1 (KXTEMPAUSH-26JUL2207 $2.06, KXTEMPLAXH-26JUL2212
  $1.84) need only score share <49%/54% to fall under the floor — plausible but NOT
  proven (share is unmeasurable historically). Label: INFERRED.

## 2. WINDOW NORMALIZATION — H2, tested second

- The venue's closed-program listing (`incentive_programs?status=closed` — discovered
  queryable this session) retains only a FRACTION of history: full cursor scan exhausts
  at 3,260 rows total (1,535 July, 1,702 August, stragglers to March), versus the
  thousands of hourly programs July must have had. **July window intersection is
  therefore only testable where sibling rows survive** (7 temp events — all overlaps
  computed and folded into §1). Honest limit, not a finding.
- KXMUSKNW-26JUL31: rested ONLY strike T700 (17.46h, 2026-07-24→31); the program feed
  (caprank telemetry) carried MUSKNW strikes T700–T1300 from 07-29 onward — a program
  existed at least 07-29→31, i.e. for roughly the last two days of a seven-day presence.
  Presence-before-program-start + a far strike's small pool putting accrual under $1 is
  the consistent account. Label: INFERRED (no July program windows retained).
- KXEURUSDAW-26JUL31: presence entirely INSIDE the observed program-feed era (07-30→31,
  12.84h, 9 strikes) — window mismatch CANNOT explain it. See §4a.

## 3. SHARE DILUTION — H3, tested third

- Direct score-share is NOT measurable historically (no book-depth time series — the
  known telemetry gap). Proxy used: share of each event's lifetime taker flow NOT filled
  by us. Result: median other-fill share **0.993 (zero-payers, n=22) vs 0.986 (paid,
  n=25)** — competition flow existed everywhere and does NOT separate the populations.
  H3 remains OPEN as a contributor (it is the natural partner of the floor: dilution
  pushes small accruals under $1) but it is not the observable separator. Label:
  MEASURED proxy, hypothesis-level for score share.

## 4. MOVEMENT — H4, the operator's hypothesis, tested last as ruled

- **REFUTED as a requirement.** Median lifetime price range: zero-payers **0.770** vs
  paid **0.740** (n=22/25) — zero-payers moved slightly MORE, not less.
- The single best per-hour payment on record is a NO-movement market:
  KXTEMPNYCH-26JUL2206, price range 0.09, 12 trades ever, 0.04h (2.4 min) of presence →
  **$12.94**. Program terms pay for resting liquidity; the data agrees.
- This is the thesis shape working: in a quiet book you are the only scoreable maker, so
  score-share → ~100% and minutes of presence claim whole window pools. Label:
  ESTABLISHED (movement comparison), INFERRED (share mechanism).

### 4a. Discovered mechanism — extreme-price presence (not in the ruled list)

KXEURUSDAW-26JUL31 (12.84h, $0.00): **65.8% of presence-time was quoted at
min(p,1−p) < $0.05** (market at ~97¢, effectively decided). LIP scoring functions
discount extreme prices (the venue's own fee formula is P(1−P)-shaped; at P=0.97 that
shape is 8.6× smaller than at P=0.50). Deep-extreme presence scoring ≈0 → accrual <$1 →
floor. Same signature on KXAAAGASD-26AUG01 (extreme_frac 0.658, 2.15h, $0.00). Label:
INFERRED — the scoring-shape premise is not venue-documented for LIP, only for fees.

### 4b. No-program events

- KXAMSAVO-26JUL24 (28.52h, the largest zero-payer): zero evidence of any program —
  absent from caprank/plans telemetry (which begin 07-29/07-22), absent from closed-
  program retention, series has 0 active programs today. Label: INFERRED no-program.
- KXTRUMPUAP-26MAY (3.79h) and KXLIUKCOUPLE-26AUG31 (0.17h): series carry 0 active
  programs today (probe 2026-08-04); same inference, weaker (their July/August program
  state is unobserved). KXAAAGASW-26JUL27 (15.67h): series IS programmed today (18
  strikes), so no-program is NOT assumable; its account stays with §1/§3 (weekly pool
  small relative to week-long window + share → sub-$1). Label: HYPOTHESIS.

## 5. What this changes upstream (facts for D2/B1 and the forecast model — reported,
not reprioritized; RULE NINE)

1. The shipped forecast's failure mode is now mechanistic: it models share×pool×TIME;
   payment behaves as score-share×pool with sub-$1 truncation. It forecasts SIZE it
   cannot see (competitors' scores) and ignores the floor.
2. A ranking that wants paid events should prize EMPTY scoreable books (the NYCH2206
   shape: quiet + programmed + nobody else resting) over raw pool or presence-hours —
   this is measurable live (book depth is visible at quote time even though it was never
   recorded historically).
3. Fixing sub-$1 waste needs CONCENTRATION of presence (fewer events over the floor
   beats many under it). 16 of 22 zero-payers were sub-40-minute drive-bys.
4. The closed-program endpoint (+`paid_out` field) exists but is retention-limited and
   its `paid_out` flag read False on all 23 retained rows including strikes of events
   that DID pay us — flag observed uninformative in this sample (n=23).

## 6. Honest limits

- Historical score share: unmeasurable (no book-depth history). Live capture going
  forward would close it (candidate for the D-queue — operator's call).
- July program windows/pools: mostly unretained by the venue; sibling templates used
  where they survive (7 events), everything else labeled.
- The $26.04 figure itself: the prior session's model script was never committed; the
  forecast could not be reproduced row-by-row. The re-derived population (22 events)
  matches its shape and the mechanism findings stand on primary data.
- Trade-tape competition proxy is flow, not resting score.

## Artifacts

`kalshi_live/w10_results/`: snapshot (orders 1,075 rows / settlements 152 / credits 58 /
market meta 169 tickers), event table, program harvest, phase-3 features, phase-4 tape
stats, W11 replay report. Scripts in `kalshi_live/`, all read-only against the venue.
