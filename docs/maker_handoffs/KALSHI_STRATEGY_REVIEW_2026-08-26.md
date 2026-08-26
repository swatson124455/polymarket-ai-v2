# STRATEGY REVIEW — WHY NOTHING ACCRUES, AND WHERE THE MONEY ACTUALLY IS (2026-08-26 ~03:0xZ)

Operator: "nothing is accruing in the markets we are in — review strategy in detail."
Scope: full measured decomposition (reads 02:45-02:50Z) + options. NO live changes made —
every option below is an operator decision (several reverse ratified settings).

## 1. Measured decomposition of the zero (all ESTABLISHED, sources inline)
1. **Est-feed flat 16:14Z(08-25)→02:45Z(08-26) on all four programs** (feed read 02:45Z).
   Gauge caveat (own census, full feed history): the venue emits only ~3 updates per
   program per DAY (08-25: 7 change-events across 4 programs) at all hours (no overnight
   blackout) — the feed cannot distinguish slow-vs-zero at sub-day resolution. Period-end
   credits (08-30) are the only ground truth. BUT the structural findings below hold
   regardless of the gauge.
2. **Overnight footprint truth (quotes tape 16:30-02:45Z)**: gas one-sided all night
   (refused, correct); T5.42/3.900 exit-only (held); T5.82 book qualified 10/464 rows yet
   ANCHOR paired it 405 rows (presence + fill risk on a book the rules pay $0 — anchor
   predates the rules canon); T5.44: book qualified 450/543 (83%) and we were two-sided
   398 rows BUT at avg 14ct (F-A ramp resets; fix staged) — the one place we plausibly
   accrued, pending the venue's sparse recompute.
3. **Qualifying-uptime census (D4 watchlist, 08-25..26, both-side cum >= 1000)**:
   **33/41 watched tickers = 0% uptime.** The payers exist in our own watch data:
   KXDIESELW-26AUG31-**T5.62 90.2%** / **T5.64 89.6%** ($120/d pools, ~1,200-1,350ct
   standing both sides) and KXAAAGASD-26AUG26-**4.1050 100%** (81-snap partial day,
   $100/d). **We quote NONE of them.**
4. **Why our selectors skip exactly the payers** (prices read 02:48Z):
   - T5.62 mid 0.495 / T5.64 mid 0.485 → inside MID_BAND_OUT (0.10,0.90) [ratified
     08-19]; books ~75 ticks wide → MAX_SPREAD_TICKS=8 refuses them too.
   - 4.1050 (mid 0.035, passes all gates) loses the gas series slot to FARTHER strikes
     under PIVOT_FAR_FIRST=1 [ratified 08-19 3A] + incumbency.
   The whole selection stack encodes the pre-rules extreme-tail thesis; the official
   rules pay the opposite shape (deep two-sided books).

## 2. The strategic reframe the official rules force
The reward is a share of a fixed pool on books that ALREADY hold Target depth both
sides. Two consequences:
- "Be the depth on empty tails" cannot work: our 50ct clamp can never bridge a
  1,000ct Target. (This killed the gas leg of concentrated-cliff.)
- The venue pays DF^ticks-below-reference — **you do not need to be at the touch.**
  On a WIDE qualifying book (the T5.62/T5.64 class), resting 2-5 ticks inside the book
  earns a discounted-but-real share with FAR lower fill risk than touch-quoting — the
  mid-band toxicity canon was measured for at-touch fills near the strike; parked
  depth behind a 75-tick spread is a different risk animal. This may be the actual
  small-level money-printer: paid for patient depth, rarely filled.

## 3. Options (decisions only — nothing enacted)
| # | change | mechanics | expected (INFERRED, labeled) | reverses a ratified setting? |
|---|---|---|---|---|
| S1 | Rank selection by MEASURED qualifying-uptime x pool x achievable share (daily census from D4 tape) instead of extreme-shape/far-first | code, medium | portfolio aimed at payers permanently | supersedes far-first (3A) — ask |
| S2 | Quick partial: PIVOT_FAR_FIRST=0 so near-money strikes take series slots (captures 4.1050-class today) | env + restart | 4.1050 at 40ct: share ~0.8-1% of ~4-5k books x $100/d ~ **$0.8-1/day**, cliff in ~1-2d | yes (3A) — ask |
| S3 | Wide-book discounted-depth mode: admit mid-band books IFF both-side-qualifying AND spread >= N ticks; rest M ticks inside (never touch), join sizes capped, exits unchanged | code + design, needs signoff | T5.62+T5.64: ~**$0.6-1.8/day each** at low fill risk | yes (mid-band canon scope) — ask |
| S4 | Anchor Target-gate: fire anchor only when the anchored side can reach Target within the clamp (same D1 bridge test) | small code | stops risk-without-reward (405 rows overnight) | narrows anchor (operator-named) — ask |
| S5 | Already staged: F-A ramp fix (restart ~07:43Z) + 40ct size | none | T5.44 share x3 when book qualifies | no |
All are additive to the risk stack: caps/halt/exits/governors untouched; S3 sizes and
tick-offsets are knobs with the usual default-off ship discipline.

## 4. Honest portfolio math (INFERRED from census + rules; receipt-checked 08-30)
S2+S3+S5 together ≈ $2-4/day gross model at today's books — the first configuration
that honestly reaches the original $2-5/day band. Fill risk shifts from 1-2c tails to
discounted mid-band depth (bounded per-fill by the tick offset; sized by caps).
Without S1-S3 the current footprint's ceiling is T5.44 alone (~83% uptime x share).
