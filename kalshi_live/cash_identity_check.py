#!/usr/bin/env python3
"""CASH IDENTITY CHECK — is the unexplained residual a LEAK or a fixed OFFSET?

    cash  ==  deposits + credits + settlement_revenue + fill_cashflow

Runs offline against a committed single-instant snapshot (balance + positions + fills +
settlements + credits read together, so no cross-timestamp drift). Read-only, no venue access.

RESULT 2026-08-03 — THE RESIDUAL DOES NOT DRIFT. Measured at two instants 6h27m apart across
1 new fill and 4 new settlements: the model predicted every cash movement to the cent
(+$0.6700 predicted vs +$0.6700 actual) and the residual was IDENTICAL at both, -$10.7970.
That REFUTES the standing lead that the drift "points at the settlement leg" — the $1.50 drift
that lead was based on was measured with the DEPLOYED recorder, which is the defect-13
position-blind fill model and is exactly the instrument now known to be wrong.

A constant offset with perfect dynamics points at the INITIAL CONDITION, not at any flow.
Deposits implied by a zero residual: $629.2030 against an operator-stated $640.00.
Credits are clean (58/58 status=applied, 0 clawbacks, no single row near the residual), so the
remaining candidates are the deposit total itself or a one-time account adjustment that no API
feed exposes. That is an operator record-check, not a code question.

    python cash_identity_check.py [snapshot.json]
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kalshi_attribution_ledger import replay_fills

DEPOSITS = 640.00          # operator-stated: $565.00 venue-verified + $75 added 2026-08-03

snap = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "cash_identity_snapshot_2026-08-03T233338Z.json")
D = json.load(open(snap))

cash = float(D["balance"].get("balance_dollars") or float(D["balance"]["balance"]) / 100.0)
events, _ = replay_fills(D["fills"])
fill_cash = sum(float(e["cash"]) for e in events)
settle = sum(float(s.get("revenue") or 0) / 100.0 for s in D["settlements"])
credits = sum(float(c.get("amount_cents") or 0) / 100.0 for c in D["credits"])
pred = DEPOSITS + credits + settle + fill_cash

print(f"snapshot read {D['read_started_utc']}")
print(f"  deposits (operator-stated)     {DEPOSITS:>12.4f}")
print(f"  credits            (n={len(D['credits']):>3d})     {credits:>12.4f}")
print(f"  settlement revenue (n={len(D['settlements']):>3d})     {settle:>12.4f}")
print(f"  fill cashflow      (n={len(D['fills']):>4d})    {fill_cash:>12.4f}")
print(f"  {'':31s}{'-' * 12}")
print(f"  PREDICTED cash                 {pred:>12.4f}")
print(f"  ACTUAL cash (venue balance)    {cash:>12.4f}")
print(f"  RESIDUAL                       {cash - pred:>12.4f}")
print(f"\n  deposits implied by a zero residual: {DEPOSITS - (cash - pred) * -1:>.4f}")
