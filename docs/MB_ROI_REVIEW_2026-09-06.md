# ADVERSARIAL REVIEW — ROI/NET-WINNINGS MEASUREMENT MACHINERY
**Date: 2026-09-06 (operator "go"). Scope: the two ruled reviews — (A) the
ROI statistical machinery (the named gate before real-money composition)
and (B) the Bayes head start. Every number below is from a seed-pinned
simulation or a named data read this session; simulations live inline in
the session transcript, artifacts in /opt/pa2-shared/mb_copyable_data/backtest/.**

## A. ROI machinery

### A1. Anytime-validity of the subgrid e-process/LCB — CONFIRMED SOUND
Simulated fair-market null traders (outcome ~ Bernoulli(fill), real
OK-fill distribution n=105,274 sampled 5k, anytime peeking over every
prefix, seeds 20260906/20260907):
- flat-2% fee null: false-pass 33/2000 = 1.65%; LCB>truth 33/2000 = 1.65%
- boundary null (zero fee, true mean exactly 0, cheap fills <0.25):
  false-pass 34/2000 = 1.70%; LCB>0 34/2000 = 1.70%
Guarantee is <=5% — holds with margin. The per-shift lambda-subgrid is
selected by the PHYSICAL floor (-1.10), data-independent, so no
hindsight betting.

### A2. Haircut realism (crypto concern) — NOT SUPPORTED; conservative
Follow-cost (shadow_fill - whale_price) over 10,842 roster OK first-buy
pairs, split by resolutions-cache category: crypto med +0.0035 / p90
+0.0138 (n=624) vs global med +0.0100. The sweep's global-median charge
OVERSTATES crypto cost on available data. SCOPE: roster crypto markets;
the discovery wallets' 5-minute micro-markets are not directly measured.
Residual: book-depth at $100/wager remains unmodeled — pilot-day
measurement per docs/MB_GO_CHECKLIST.md item 3. LIMITATION, disclosed.

### A3. Label-coverage survivorship — MOSTLY CLEAR, one wallet flagged
Top-10 discovery wallets: 125/151 distinct entry tokens labeled (82.8%)
vs 36.3% board-wide; unlabeled split = 3 cid-in-DB-unresolved (time
censoring) + 23 outside-DB (structural). FLAG: 0x3382c5c6c6 at 33%
coverage with 22/33 tokens outside the DB — its row is judged on a third
of its markets. RECOMMENDED (additive build): per-wallet coverage%
column on the leaderboard; sub-threshold rows surfaced as
UNKNOWN-flagged.

### A4. CORRELATED-ATOM DEFECT — FOUND, MEASURED, FIXED, VERIFIED
The review's central catch. Per-wager atoms let same-market ladder
wagers multiply as independent evidence, but they share ONE outcome.
Measured on fair nulls (seed 20260908, 500 traders x 30 markets):

    ladder depth K=1:  false-pass  1.6%   (the guarantee's <=5% holds)
    K=5:  37.0%   K=20: 65.0%   K=44: 73.6%   (BROKEN)

K=44 is the real density of the then-top board wallet (1,236 wagers /
28 markets). Every e-value/LCB/QUALIFIES on the 21:19-21:22Z boards was
inflated for ladder-heavy wallets; realized-$ columns were honest
bookkeeping and stand. No live-grader lock was ever written on the
defective basis (trials at 0 resolved when fixed).
FIX (operator "fix go"): mb_canon.market_position_rois — ONE atom per
market = the equal-stake mean of that market's wager ROIs (every ladder
fill still prices the position; the ladder ruling honored); e-process/
LCB consume market atoms; wager_rois remains canon for MONEY bookkeeping
only; money rate = conservative markets/day. Converted in the same
commit: grader, funnel, band, backtest replay+holdout, hypo stake-gate,
bayes moments. Regression test pins fair-null false-pass <=5% under
ladders; source pins REJECT mc.wager_rois( in every evidence path.

### A5. Verdict on the machinery
SOUND FOR GATING after A4's fix, with the named residuals: depth
(pilot-day), discovery-wallet haircut scope (A2), coverage column (A3),
and the standing HYPOTHETICAL label on all reference-stake dollars.

## B. Bayes head start (review of the converted module)
Moments now computed on market-position ROI atoms (independence restored
— the same correlation defect would have corrupted its variance
estimates). Refit results recorded in
backtest/bayes_prior_atomfix_{med,p90}.json (see MB_STATE for the
numbers read at fit time). Standing agenda items that remain OPEN even
after this review: 11-day window vs the 1-month eligibility horizon;
per-category priors; normal-normal adequacy under heavy-tailed ROI atoms
(tau2=0 clipping is the honest-disclosure mechanism, not a resolution).
STATUS: reviewed to this point; still NOT deployed to any cron/gate —
deployment needs an explicit operator go naming what it would be used
for.

## Review discipline note
The A1 simulation (independent wagers) could not see A4 — the defect
surfaced only when A3's coverage read exposed the wager/market density.
Lesson recorded: validity simulations must model the DEPENDENCE
STRUCTURE of real data, not just its marginals.
