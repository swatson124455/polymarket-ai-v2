# R2 — SHARE ECONOMICS STUDY, PART 1: EST-FEED SEMANTICS + THE WALL (2026-08-25)

Overhaul-review item R2 (`KALSHI_HANDOFF_2026-08-25_OVERHAUL_REVIEW.md`). $0 study on
tape we hold. Question: does 40ct beside a 2,050ct wall score ~0, or does the est-feed
hide small values? **Answer: the feed does not hide small values; our score was 0.**

All reads 2026-08-25T03:4x-03:5xZ unless dated. Sources: `estimates-202608.jsonl`
(full-history scan), `kalshi_program_map.json` (22,692 entries), D4 raw tape
`/opt/pa2-maker-backups/d4/d4_books-20260824.jsonl.gz`, live journal, quoter blob
`6753e6c3` @ `1d25545`.

## 1. Est-feed zero-row semantics — SETTLED
Full-history scan (123 programs ever seen, 08-06 → 08-25):
- The feed **displays rows at 0 centicents**: 13 programs with first=min=max=0,
  persisting 36-1,192 snapshots. It also shows 1-centicent ($0.0001) rows.
- Rows **appear promptly at sub-cent accrual**: USDJPY program `43b0d4db` first
  appeared at **65 centicents ($0.0065)** 08-22T08:39Z, grew to 1,999, froze at its
  program end (14:00Z 08-24; first frozen snapshot 14:02Z), and persists pending
  payout. DIESELW `5ad73aae` first appeared at 26 centicents ($0.0026).
- Therefore "the feed hides small values" is **REFUTED** (ESTABLISHED). Detection
  granularity demonstrated at ≤ $0.0065, display floor $0.0000.
- Open sub-question (not needed for the conclusion): what puts a 0-value row in the
  list at all (13 programs sat at exactly 0 for days). Candidates: rounded-down tiny
  positives, or enrolled-but-zero. Does not affect §3 — absence is still absence.

## 2. The measured window on KXAAAGASW-26AUG31-3.900 (program `e0269fe5`, $100/day pool)
- **Feed**: the est-feed state has been IDENTICAL since 08-24T14:02Z — exactly one row
  (USDJPY residual 1,999cc). Programs `e0269fe5` (3.900) and `31ead15c`
  (AAAGASD-25-4.0600, $100/day) have **never had a row in the entire feed history** —
  not through the 2.5h genuinely-paired 40ct window (~17:09-19:49Z 08-24), not through
  8h of exit-only presence, not through the profitable 15:16-15:28Z roundtrip.
- **Book (D4 raw depth arrays, 167 snapshots 17-20Z; the broken derived occupancy
  field was NOT used — R4)**:
  - YES-bid touch 0.98: level 1,040-1,060ct **including our 40** → rival wall ~1,020ct
    co-priced with us.
  - YES-ask 0.99 (= NO side): level ~49.3ct total from 18:09Z, **ours ~40 = ~81% of
    touch depth**.
- **Credited accrual for those hours: $0** at ≤$0.0065 granularity (ESTABLISHED via §1).

## 3. What this refutes and what survives
- **Proportional-share-at-touch is REFUTED**: we were ~81% of the NO-side touch and
  ~3.8% of the YES-side touch of a $100/day-pool market for 2.5h paired. A naive
  proportional model predicts a feed-visible row within the first hour (INFERRED
  arithmetic: even 1% × $100/d ≈ $0.04/h ≫ $0.0065). Nothing appeared.
- **"Extreme prices never score" is REFUTED as a blanket**: our launch-day 5ct pairs
  on KXDIESELW-26AUG24-T5.64 / T5.26 ($120/day pools) accrued 26→**1,270cc** and
  89→**820cc**, rows appearing ~35min-2.5h after we began quoting (both ended sub-$1
  → paid $0 per cliff canon; consistent with window credits $0).
- **Surviving mechanism (HYPOTHESIS, parsimonious — explains every observation)**:
  the venue walks each side to **Target size** and scores only the qualifying set:
  - NO side: total touch depth ~49ct < Target → *the side cannot reach Target → $0
    for everyone on that side*. Our own replica models exactly this case
    (`_qualifying_score` :2679-:2680).
  - YES side: level 1,060ct ≥ Target, and the venue truncates the qualifying set **at
    Target in time priority within the price level** — our 40ct behind ~1,020ct of
    earlier co-priced size falls outside the cutoff → score exactly 0. Our replica
    instead pro-rates within the whole level (:2666-:2692) and predicts ~3.8% — this
    is the precise replica-vs-venue divergence, and an R4 instrument-trust finding:
    **`_prospective_capture`/MIN_CREDIT gating is over-admitting wall-dominated books.**
  - DIESELW positive controls fit: thin books where our 5ct sat inside the Target
    window (early/alone at the level) → accrued.
- Alternative still alive (weaker): a per-side min-depth/qualification rule other than
  Target, or pairedness-min composition. Discriminated by the same next steps.

## 4. Decision-relevant consequences (reported, no self-directed action)
1. The strategy's binding selection variable is **queue position within the Target
   window at our price level**, not presence, not touch-share. A saturation check
   (rival depth at-or-ahead of us vs Target) belongs in market selection — this is
   design input for R3-forward and operator decision, not a unilateral gate change.
2. The MIN_CREDIT/expected-credit gate is currently computed with a model the venue
   contradicts on wall books (over-admission). Reported as R4 finding; no change
   without signoff.
3. R1's re-pair value depends on this market-class distinction (see R1 doc §2).

## 5. What settles the remaining mechanism question
- **R3 primary documents** (next work item): the LIP terms/CFTC filing scoring formula
  — Target semantics, within-level allocation, pairedness composition, price bands.
- **Target sizes per program**: join `incentive_programs` data (the quoter already
  consumes Target) against the D4 depth tape to test "NO side < Target" numerically
  for the 08-24 window. (Next session task — data already on the box.)
- **Passive controlled evidence, no new risk**: current DIESELW/TOPMODEL-26AUG31-era
  quoting — whenever we sit inside a thin level, a row should appear within ~35min-2.5h
  (the observed latency band); wall-dominated books should stay row-less. The standing
  est-feed recorder (5min) captures this without any behavior change.

## 6. Per-section adversarial review (incl. EV lens)
- *Could the feed lag longer than the paired window?* Observed row-appearance latency:
  ~35min (5ad73aae quoted from 16:40:53Z launch, row 17:16Z) and ~2.5h bounds. The
  3.900 paired window was 2.5h and the market has been quoted (two-sided or exit-side)
  for 12h+ since with zero row — lag alone cannot explain it. Holds.
- *Could the program be inactive/not-yet-started?* The map lists `e0269fe5` active with
  end 08-31T03:59Z and period_reward 1,000,000; sibling gas programs of the same cohort
  (31ead15c, ends 08-25) also never rowed. Weakness: I did not re-read
  /incentive_programs status=active for e0269fe5 at study time — flagged as a §5
  follow-up (one authed read) rather than asserted.
- *Time-priority claim*: within-level truncation is HYPOTHESIS, clearly labeled; the
  refutations in §3 (proportional, feed-hides) are the ESTABLISHED part. The wall
  number (~1,020ct) is from D4 raw arrays which R4 flagged only for the DERIVED
  occupancy field; raw depth arrays cross-check against the quoter's independent WS
  book reads (journal books=9ws) — second-source requirement satisfied at the
  raw-array level, noted imperfect (no tick-by-tick venue replay).
- *EV lens*: the study's actionable EV is avoiding capital+presence spend on books
  where score=0 by construction (currently: our flagship 3.900 quoting appears to be
  earning $0/h while committing $44.60 of cap and carrying fill risk). That
  reallocation decision is the operator's; the measurement stands either way.
