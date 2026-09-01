# COHORT1-UNTESTED AMENDMENT - anytime-valid re-registration (2026-08-25)

**Registered 2026-08-25 (~14:00Z), operator authorization: "proceed with
your rec" (directive item 6, 2026-08-25).** Amends the grading of the
9-trader COHORT1-UNTESTED group ONLY. The original cohort5 twenty keep
their 2026-07-30 charter untouched (single look, flat-2% fee) - their 15
unconsumed looks are NOT changed by this amendment; a separate proposal to
re-register them awaits an explicit operator ruling.

## Why an amendment is legitimate here
- ZERO looks consumed: since the group's epoch (2026-08-24T17:00:00Z) the
  only outputs were count-only "ACCRUING (0/30 resolved)" lines - no edge,
  no P, no e-value for any of the 9 was ever computed or seen. Changing the
  scoring rule before any evidence is observed is a design choice, not
  optional stopping.
- The single-look bar it replaces was measured by the lane's own 2026-08-19
  sequential study at 7-8% one-shot power at realistic edges (P>=0.95 at
  n=30 requires a +0.127 point estimate; the joint bar can never pass for
  true edge <= ~+0.022). Consuming irreversible looks through it burns
  candidates without measuring them.
- The band test already uses this exact design, operator-ratified.

## The amended test (per trader, all 9)
- Estimand: CANONICAL (scripts/mb_canon.py; docs/MEASUREMENT_CANON.md) -
  per-market mean edge of OK first-buys in the trader's forward window
  (detect_ts >= 2026-08-24T17:00:00Z - the epoch does NOT move), venue fee
  formula rate*p*(1-p) via fee_rate_map, flat-2% fallback disclosed.
- H0: edge <= 0. Betting e-process, uniform mixture over
  lambda in {0.05, 0.1, 0.2, 0.4, 0.6, 0.8} on per-market mean edges,
  observation order = market's first detect_ts (band_tracker.e_value - the
  ratified implementation, imported not re-implemented).
- QUALIFY when e-value >= 20 AND pooled canonical edge >= +0.02 (economic
  gate applied at crossing) AND OK-rate >= 0.75. Lock immutable.
- FUTILITY: 300 resolved markets with e < 20 -> NOT DEMONSTRATED, locked.
- Anytime-valid: the daily cron may look every day with no peeking penalty;
  the e-value trajectory prints in the 11:40Z block, auditable.
- Qualifying traders are PROPOSALS; composition remains an operator gate.

## What would be cheating (named)
Moving the epoch; re-widening after seeing forward data; lowering e>=20;
retrying after a futility close without operator sign-off; retroactively
applying this design to the original 20's already-consumed locks; quoting
this group's e-values beside the original 20's P-values without labeling
the different designs.
