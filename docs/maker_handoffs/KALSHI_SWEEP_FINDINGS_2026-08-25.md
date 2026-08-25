# SWEEP FINDINGS — 2026-08-25 (operator: "scan all you can for any other possible findings")

Scope: our live data re-tested against the newly-obtained official rules + remaining
public sources (CFTC filings, help center, program feed). All reads timestamped.

## F1 ⭐ LIVE ACCRUAL CONFIRMED — first real earnings of the window (ESTABLISHED)
Est-feed 16:04:47Z: KXDIESELW-26AUG31 **T5.44 = 395cc ($0.0395), T5.82 = 650cc
($0.0650), T5.42 = 307cc ($0.0307)** — programs $120/day pools, Target 1000, DF 0.5,
period 08-24T12:03Z→08-30T04:00Z (venue read 16:08Z). The full mechanism chain now
validates end-to-end on our own orders: telemetry both-sides-qualified → official
rules say we score → feed rows appeared. (Window credits remain $0 — accrual ≠ credit
until period end + $1 floor.)

## F2 Est-feed is BATCHED and (probably) LAGGED (mechanism finding)
Value-change points today: 12:29:16 / 14:29:23 / 15:24:26 — step function between
recomputes (confirms + times the 0a "batch recompute" note). **T5.42 gained +103cc in
the 15:24 batch despite having ZERO resting orders since its ~13:04Z loss-governor
strip** (venue resting read 13:5xZ: no T5.42 orders). Two candidate readings:
(a) the feed lags real accrual ~1-2h (batches cover earlier windows) — most likely;
(b) one-sided/absent presence still accrues — would reshape R1's earning model.
DISCRIMINATOR (passive): if T5.42 freezes within the next 1-2 batches, (a); if it
keeps climbing, (b). No action needed — the recorder captures it.

## F3 Sub-$1 residual REMOVED UNPAID — est-feed lifecycle refined (ESTABLISHED)
USDJPY program 43b0d4db (accrued $0.1999, ended 08-24T14:00Z): row last seen
12:24:15Z, GONE at the 12:29:16Z batch — ~22h after program end — and **no credit
appeared** (credit_history read ~16:08Z: latest credit still 08-16 KXTRUMPTIME).
Sub-$1 → removed without payment. Canon refinement: rows do NOT "persist until
payout" — they persist ~a day post-end, then are dropped, unpaid if sub-$1.

## F4 Gas row at 0 appeared (feed-semantics corroboration)
eaa23a75 = KXAAAGASD-26AUG26-4.0550 ($100/day) appeared at 0cc from 12:29:16Z — we
had fills/presence there this morning. Row-appearance = first scored/registered
snapshot, even when the value rounds to 0. Kills any remaining "feed hides us".

## F5 Volume Incentive Program — we are eligible; extreme fills are NOT (rules read)
Original combined filing (Aug 8 2025, read in full; local PDF): Volume component
pays each Eligible Term a pro-rata share of a per-market Volume Reward by "Eligible
Volume" = **CLOB trades priced between $.03 and $.97**, capped at **$.005 per
contract**. Same eligibility exclusions as LIP (we qualify). Consequences:
- Our extreme-shell fills (0.01/0.02/0.98/0.99) earn ZERO volume credit by the band.
- Diesel-band fills (e.g. 0.92/0.08) DO qualify — pennies at our size, but nonzero.
- No volume-type credit has ever appeared in our credit_history.
- Secondary source (navnoorbawaresearch.com): VIP refiled 08-04-2026 → Sept 2027;
  CFTC DMO asked all DCMs to amend incentive filings by 09-14. Current VIP terms
  doc not yet pulled (kalshi.com/regulatory/notices returned HTTP 429 — retry).

## F6 incentive_programs endpoint is UNPAGEABLE (R4 instrument note)
limit=200 → exactly 200 rows, NO cursor; limit=1000 → exactly 1000 rows, NO cursor
(reads 15:24Z/16:1xZ). Any single-request "active programs" count/histogram is a
TRUNCATED denominator (yesterday's 211-late-ending count = "of the first 1000").
Matches the 08-12 memory trap. F9's 3,542 count used some other enumeration — verify
its method before reuse.

## F7 Historical: the both-sides exclusion was ADDED 2026-02-28
The original Aug-2025 program had NO snapshot-exclusion rule; the Feb-11-2026
amendment added it ("exclude Snapshots where there are no qualifying Yes bids or no
qualifying No bids"). Any pre-Feb-28 reward observations come from a DIFFERENT rule
regime — era-split before comparing.

## F8 First cliff-clearing projection on live accrual (INFERRED, pre-registered check)
Measured step rates so far (~3-4h of covered accrual windows): T5.44 ≈ $0.31/day-
scale, T5.82 similar-or-better. With ~4.7d left to 08-30T04:00Z, T5.44/T5.82 project
~$1.5 each IF presence and book-shape hold — first projected $1-cliff clears of the
window. CHECK AGAINST: daily 07:30Z reward-pnl reads; refuted if pace decays.
Model note: quoter pc for T5.44 was $1.53/day vs ~$0.3/day actual → M7 over-
prediction ~5x here, inside the canonical 2-6x band.

## Open follow-ups from this sweep (additive, no reordering)
1. F2 discriminator read next batch (passive).
2. kalshi.com/regulatory/notices retry → current LIP extension + current VIP terms.
3. R1-probe archive full-depth-cum confirmation (optional now, per R3 doc).
4. Help-vs-filing Reference-Price discrepancy (Target/5 vs best bid) — est-feed
   empirics; affects replica precision, not the both-sides mechanism.
5. F9 enumeration method verify (F6).
