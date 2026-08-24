# POST-DEPLOY REVIEW 2026-08-24 (~17:2xZ) — logic issues + overcompensation
Scope: every delta since the 08-20 relight, judged against live tape.

## DEFECTS FOUND AND FIXED TODAY (all live-verified)
1. Anchor v1 DEAD ON ARRIVAL: pair-sum guard refused the modal 0.99-touch book
   (0/245 fires). v2 (step-down) WRONG: anchor leg crossed the implied opposite
   ask (1-0.99=0.01) -> post-only rejected every cycle -> join leg rested ALONE
   = unpaired presence (earns $0, keeps fill risk; live 17:08-17:11Z, 4 mkts,
   4cr fails/cycle). v3 GRID-FIT (5bff8b6, deployed 6753e6c3): anchorable iff
   ANCHOR_PRICE < 1-touch (touch<=0.98 on the 1c grid); 0.99-touch =
   STRUCTURALLY UNANCHORABLE, refused. First v3 cycle: cancel 4 lone joins,
   0 fails.
2. D4 watchlist sync could cap out markets WE ARE RESTING IN (ranked by pool
   only). Fixed (ee13e4c): resting tickers first, unconditionally; loud
   warning if the resting read fails.

## HONEST CAVEAT (new, from the same measurement)
The venue's 1-cent grid compresses extreme books to 0.99/0.01, where the empty
side has NO legal quote price. The anchor only unlocks one-sided books touching
<=0.98 — today's probed one-sided set was ALL 0.99-touch, so the anchor's real
coverage may be small. Two-sided extreme books DO exist (AAAGASW-26AUG31-3.900
carries a rival 1c side and is our working market). Measure anchor_paired over
the coming days before crediting the feature with anything.

## VERIFIED CORRECT (live behavior matches design)
- Option A sizing: 3.900 re-entered at 40ct after cooldown ($39.60 committed).
- Exit machinery: 08-23 USDJPY naked window closed in 109s; per-market day-loss
  governor tripped + struck USDJPY correctly; reentry cooldown paced 3.900.
- Shape-fix (f5e9d01): no false "no working exit" warnings since deploy.
- Caps/halt: never exceeded; committed max $59.20 of $200 (during the v2 defect
  window), $39.60 steady-state.
- Storm detector + watchlist infra: running, read-only, thresholds labeled
  INITIAL-UNCALIBRATED.

## OVERCOMPENSATION CANDIDATES (report + ask; nothing changed)
1. MIN_RUNWAY_H=49 blocks RE-ENTRY to a market whose program we already accrued
   in (live: DIESELW-26AUG24 blocked 08-21 with ~1.6d of program left). The
   49h rule prices a FROM-SCRATCH entry; a program already part-way to $1 has
   different math. OPTION: exempt markets whose est-feed accrued-so-far > $X
   (e.g. $0.50) from the runway gate. OPERATOR DECISION.
2. Mid-band 0.10-0.90 and USDJPY eviction: both operator-ruled this week;
   no new evidence against either. Listed for completeness, no ask.
3. Anchor extremeness guard (>=0.90/<=0.10 in yes-terms): with the grid-fit
   constraint the effective window is [0.90..0.98] touch — narrow but correct;
   widening it would contradict the near-strike toxicity measurement. No ask.
