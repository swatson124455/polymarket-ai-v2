# F4 — LEDGER-GRADE DRAWDOWN CREDIT-ADJUST: PLAN (no code yet)

Status: PLAN ONLY, per operator naming 2026-07-31 ("F4 = ELEVATED — ledger-grade plan
to operator BEFORE building"). Nothing in this document is built or deployed.

## The problem (why F4 exists)

The $40 drawdown halt and the $60 cumulative-down meter baseline against equity/day-peak.
An operator deposit mid-day (e.g. the +$98.04 cash landing 13:19Z 2026-07-31) inflates
equity, which (a) rebaselines the day-peak upward, so the dd meter demands a larger
absolute loss before halting, and (b) refills down-meter room. The reverse (a withdrawal)
would fire the halt spuriously. The first attempt (built and REVERTED same-day 2026-07-31)
inferred credits from cycle-over-cycle cash jumps — a heuristic that misread ordinary
collateral releases (resting-order cancels return reserved cash) as external credits,
creating a false-halt class. Root cause of the revert: cash deltas alone cannot
distinguish "operator deposited" from "orders released reservation."

## Ledger-grade design (what "ledger-grade" means here)

External credit detection must be an IDENTITY, not a heuristic:

    cash_delta(t1→t2)  ==  settlements_paid(t1→t2)
                         + fill_cash_flows(t1→t2)      (signed, fee-inclusive)
                         + reservation_delta(t1→t2)    (resting-order collateral)
                         + EXTERNAL(t1→t2)             (deposits/withdrawals — the unknown)

Every term except EXTERNAL is independently readable from the venue:
- settlements: portfolio settlements endpoint (id-stamped rows — countable, idempotent)
- fills: fills feed (id-stamped, ACTION-only yes-signed per the API-shape canon)
- reservation: resting orders' reserved dollars (the cash recorder already logs
  `resting_reservation` per row)

EXTERNAL is then the RESIDUAL of an identity whose other terms are all id-stamped venue
records — not a statistical residual. If the identity does not close to cents over the
interval, the adjustment DOES NOT APPLY (fail-closed = current behavior, no adjustment)
and a loud counter/plan key records the reconciliation failure.

## Phase 1 — OFFLINE VALIDATION STUDY (no live code)

Run the identity over the recorded history (cash-*.jsonl rows + venue
fills/settlements pulls) for the scaled era:
- Reproduce every known operator deposit (operator-confirmed lifetime list: $465 + $100
  = $565) at the right timestamps, to the cent.
- Zero false externals across all collateral-release events (the class that killed v1).
- Publish the reconciliation table in the session before any build is proposed.
GATE: if the study cannot close the identity (the naive fill-sign study failed its
identity check on 2026-07-30 — precedent), F4 stops here and reports.

## Phase 2 — LIVE WIRING (only after Phase 1 passes and operator names the build)

- On a cycle where the interval identity closes AND |EXTERNAL| >= $5: shift
  `equity_day_peak` and the down-meter baseline by exactly EXTERNAL, print one loud line
  with the full term breakdown, and stamp a plan key (`dd_credit_adjust_usd`).
- Any ambiguity (identity gap > $0.05, missing feed, first cycle after restart):
  NO adjustment — behavior identical to today, plus `_SILENT["f4_reconcile_fail"]`.
- Persisted in state (survives restarts); day-roll resets with the meters.
- Full protocol: tests incl. the collateral-release false-positive replay, copy-based
  mutation, byte-exact deploy — each gate operator-visible.

## Explicitly out of scope

- No change to halt thresholds, halt semantics, or the cost-basis ratchet.
- No use of the ledger's `rewards_residual` / `rewards_alloc_by_series` (RULE ZERO ban).
- No inference from cash jumps alone (the reverted v1 approach).
