# COHORT5 PRE-REGISTRATION — copy-edge-selected roster (draft for operator sign-off)

**Registered:** 2026-07-30 (criteria registered BEFORE composition; composition
happens only after operator sign-off, from data that post-dates this file).
**Status:** DRAFT — awaiting operator approval of the criteria. No roster
change until then.

## Why this cohort exists

The 2026-07-30 audit established the lane's core structural finding: **whale
chain edge does not predict our copy edge.** All 20 ADMITs survived complete
labels with chain edges +0.0101 → +0.0749 (re-review 20/20, FLIPPED: 0), yet
the copy of them measures near zero (locked verdicts: cohort2 NOT DEMONSTRATED
at +0.0210/P=0.648; cohort3 at −0.0131/P=0.307), and per-trader diagnostics
show the spread: one trader chain +0.0199 but copy −0.0432 on 57 mkts, others
+0.04 → +0.08 copied (copyability_snapshot_20260730.txt). Edge that evaporates
inside our ~1s detection lag is real for the whale and unreachable for us.
Cohort5 therefore selects on OUR forward copy results — the quantity we
actually earn — with the chain deep-dive retained as the fraud/skill screen
(unchanged, still mandatory for admission eligibility).

## Admission criteria (the pre-registered selection bar)

A trader is cohort5-eligible when ALL of the following hold on their
FORWARD per-trader shadow line, measured from this file's registration date
(records with `detect_ts` ≥ 2026-07-30T17:00:00Z only — nothing before):

1. **Chain screen (unchanged):** a chain deep-dive ADMIT verdict on complete
   labels (any run ≥ 2026-07-22 supplement; the 20 current ADMITs qualify).
2. **Copy edge:** per-trader forward shadow edge ≥ **+0.02** on ≥ **30**
   resolved markets, computed by the canonical pipeline
   (`cohort_readout` → `analyze`) with the **per-market fee equation**
   (fee_map: measured zero-fee tokens exempt; all others flat 2%).
3. **Significance:** per-trader P(>0) ≥ **0.95** (single pre-registered look —
   the trader's test is consumed at first crossing of 30 resolved, same
   verdict-lock discipline as cohorts; no daily re-testing).
4. **Execution reality:** OK-rate ≥ **75%** over the same window (an edge the
   gates reject isn't collectable).
5. **No dominance:** at composition, no member may account for > **50%** of
   the candidate cohort's projected first-buys (concentration measured over
   the qualification window).

## Verdict discipline (inherits everything already built)

- Own epoch at composition; `forward_only: true` in the ledger entry.
- One pre-registered cohort-level test, consumed at first crossing of 30
  resolved markets (verdict lock, `verdict_locks.json`).
- Concentration disclosure + LOO diagnostic lines as with all cohorts.
- Equation: per-market fee map + flat 2% for fee-bearing/unmapped tokens
  (registered HERE, before any cohort5 data exists). A calibrated per-market
  RATE for fee-bearing markets is a separate measurement + a separate
  operator gate, and would apply only to cohorts registered after it.

## What this is NOT

- NOT a re-cut of cohorts 1–4: their registered equations and locked verdicts
  stand. Cohort5 is a new, additive experiment.
- NOT admission by the 2026-07-30 copyability snapshot: that snapshot
  motivated the DESIGN but is pre-registration data — using it for
  COMPOSITION would select and verify on the same window. Qualification
  windows start at 17:00Z 2026-07-30.

## Open items at registration time

- RTDS A/B (running): if the source swap is approved, detection lag drops
  (delivery p50 0.82s vs chain 1.30s) — cohort5 members qualify under
  whichever detection source is live at their qualification time; the source
  in force is recorded in each shadow record (`source` field).
- Operator sign-off on THIS file's criteria (numbers 1–5) is the gate for any
  composition proposal.
