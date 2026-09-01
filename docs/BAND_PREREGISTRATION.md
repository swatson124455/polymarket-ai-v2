# BAND 0.65-0.85 PRE-REGISTRATION — the forward test of the mid-price copy edge

**Registered:** 2026-08-19 (operator: "proceed with all recs"; speed directive:
verdict as soon as evidence honestly suffices — hence an ANYTIME-VALID design
that can reject on any day, not a fixed calendar).
**Status:** ACTIVE. Qualification window: records with
`detect_ts >= 2026-08-19T18:00:00Z` ONLY (everything before was visible when
the hypothesis was found — in-sample by construction).

## Hypothesis under test
Copying roster first-buys whose shadow fill lands in **[0.65, 0.85)** is
positive-edge. In-sample basis (NEVER evidence for this test): +0.0853
flat-fee / +0.0837 venue-fee, P(>0)=0.9935/0.996, 140 markets, survived
LOO/jackknife/split-half — but discovered by searching 46 strata
(P(>=4 hits | null)=0.201), hence this forward test.

## Estimand (canonical, frozen)
Per-market mean edge of OK first-buys with `0.65 <= shadow_fill < 0.85`,
pooled across ALL clean-roster traders, canonical pipeline
(`repair_records` -> `analyze`-equivalent edge atoms) with the VENUE fee
formula (`fee_rate_map`, rate*p*(1-p); unmapped tokens flat 2% —
conservative).

## Test (anytime-valid, from the 2026-08-19 sequential study)
- H0: edge <= 0. Betting e-process, uniform mixture over
  lambda in {0.05, 0.1, 0.2, 0.4, 0.6, 0.8} on per-market mean edges
  (bounded in [-1.02, +1.0]); observation order = the market's first
  detect_ts (fixed, pre-registered ordering).
- **REJECT H0 when e-value >= 20** (alpha=0.05, valid under continuous
  monitoring — measured FPR 1.4-4.7% checked after every market).
- **ECONOMIC GATE (applied only after rejection):** pooled band edge at
  judgment time >= +0.02. The floor is NOT in the null (folding it in
  roughly doubles n; power study 2026-08-19).
- **FUTILITY:** at 600 resolved band markets with e < 20 -> NOT DEMONSTRATED,
  test closed.
- Verdict locks on first threshold crossing (immutable, shared lock helpers;
  lock file `deep_dive/band_lock.json`).
- The running e-value prints in the daily 11:40Z cron block — auditable
  trajectory, no peeking penalty.

## What would be cheating (named so it cannot happen silently)
Counting pre-epoch fills; re-widening the band after seeing forward data;
lowering the e-threshold; retrying with a new epoch after a futility close
without operator sign-off; treating the in-sample +0.085 as evidence.

## Expected timeline (INFERRED, stated to manage expectations)
Band accrual measured ~4 resolved markets/day on the 30-trader roster
(140 markets over ~35 days). At a TRUE edge near the in-sample +0.085
(sd ~0.41), the e-process median detection is ~270 band markets => ~2-9
weeks depending on realized flow; roster growth (probe additions, scout
admits) and the RTDS fixes raise flow and shorten this. It CAN reject much
earlier if the realized edge runs hot; it cannot be forced by the calendar.

## AMENDMENT (2026-08-25, operator-approved hygiene batch) - estimand-RESTORING bug-fix
The live watcher has gated ask > 0.98 as PRICE_NO_UPSIDE since 2026-08-19
17:33Z - BEFORE this test's 18:00Z epoch - so the registered estimand
("gating stays on /price exactly as deployed") always excluded those fills.
analyze_shadow.repair_record, which re-derives verdicts for ladder-armed
records, LACKED that branch and silently re-admitted deterministic-loser
fills up to $1.00 into every readout including this test's. Fixed
2026-08-25 with gate-order parity to evaluate_gates. Direction of the
error while it stood: dragged the measured band edge DOWN (conservative).
This amendment documents a repair-to-charter, not a change of the frozen
estimand; the e-process recomputes over the corrected forward sequence.
