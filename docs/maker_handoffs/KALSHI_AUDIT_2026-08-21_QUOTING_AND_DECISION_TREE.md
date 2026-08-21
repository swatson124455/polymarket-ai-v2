# SCHEDULED AUDIT 2026-08-21 (~13:3xZ) — quoting + decision-tree-vs-profit
Operator-ordered 2026-08-20. Window T0=2026-08-20T16:40:24Z. Reads 13:32-13:34Z.

## 1. QUOTING AUDIT (denominator: 1,571 cycles T0→13:32Z)
- Uptime: 1,571 cycles, ZERO gaps >5min. quoted p50 = 2 markets, currently 0.
- Churn: 18 creates / 7 cancels total (low, as designed). Committed p50 $14.55,
  max $19.30 — all caps honored (60/60/200). No halt, no STOP.
- Ramp: T5.26 5→10ct at 17:33Z. ⚠ OPEN QUESTION: rung advanced with accrued
  ~$0 vs OBS_HOLD_MIN_USD=1.20 — freshness definition to be checked (bounded:
  $10 exposure).
- Gate distribution since T0 (row basis): one_sided 14,001 · min_runway 3,638 ·
  d3_ramp_capped 2,862 · obs_hold_bound 1,344 · mid_band 943 · unqualifiable 307
  (telemetry-only, flag=0) · wide_or_asym 1.
- FILL EVENT (the day's one incident): 12:23:27Z our 1c NO bid on T5.26 lifted
  (10ct, $0.10); position NAKED −10 for 55 MINUTES — strand exit re-rest FAILED
  every cycle (opposite book side EMPTY, taker-cross had nothing to cross);
  13:18:34Z unwind bought 10 YES @0.99 → $1.00 pairs. NET COST −$0.0070
  (balance 320.3126→320.3056; = taker fee 0.07·10·.99·.01 exactly). Tail risk
  during the naked hour was bounded at ~$9.90 (per-market cap). DEFECT-CLASS
  FINDING (naked-exit-unrestable-on-empty-book), fix candidates below.

## 2. MONEY AUDIT (est-feed read 13:34:01Z; per-user feed)
- Accrual since T0: T5.64 +$0.1270, T5.26 +$0.0820 (combined ~$0.21 over ~21h
  ≈ $0.24/day pace). ⚠ PROGRAM END IS 2026-08-23T03:59:59Z (not market close
  08-24) → ~1.6d runway left → projected at pace: T5.64 ≈ $0.36, T5.26 ≈ $0.23
  at conclusion — BOTH FAR SUB-$1 → cliff-predicted payout $0.
- Fill cost realized: −$0.0070 (above). reward_pnl 07:30Z: accrued_open $0.1692,
  n_leakage 0. Scoreboard 07:40Z: credits $0 / drag $0; identity_gap +14.60 =
  resting reservation at that hour (documented artifact; alarm text fired as
  expected).
- UI cross-check: Kalshi UI "Rewards (Aug) $57.33" = credit_history August sum
  $57.33 (22 rows) EXACT — UI/API reconciled; credit_history completeness
  validated.
- Opportunity measure (audit item 3 as ordered): NOT MEASURABLE — the estimates
  feed is PER-USER; it cannot show rivals' accrual in markets we sat out.
  Reported honestly rather than proxied.

## 3. DECISION-TREE-vs-PROFIT VERDICT
Every gate behaved as specified (no misfires found; the one-sided gate is
correctly refusing both books right now — 0 quotes standing at 13:33Z). But the
tree has ONE STRUCTURAL CONTRADICTION with the profit objective, now measured:
**the OBS_HOLD ramp (size up only after accrued ≥ $1.20) and the per-program $1
cliff are mutually incompatible at 2-3 day program windows.** At rung-1/2 size
the accrual pace (~$0.09-0.15/day/market measured this window) can NEVER reach
$1.20 before the program ends → rungs never advance → every program concludes
sub-$1 → pays $0, by construction. The safety ramp guarantees the revenue
experiment fails. Options (operator decision, Rule Nine — nothing changed):
  (A) Cliff-entry exception: enter cliff-qualified markets AT TARGET SIZE
      (40ct) from cycle 1, skipping rungs — fill exposure day-1 rises to
      ~$40/market worst-case (still under the $60 cap + $10/day halt).
  (B) Lower/re-key the rung-advance threshold for short-window programs
      (e.g. advance on pace ≥ $0.50/day rather than level $1.20).
  (C) Change nothing — accept this window's near-certain $0 credits as the
      experiment's measured answer, decide the mandate 08-27 on that.
Also: naked-exit defect fix candidate (empty-book side): rest the exit at the
venue price bound immediately instead of failing the re-rest (code + review).

## 4. CLOSED READS (both due after 08-21T01:40:43Z; credit_history 13:34Z)
- OLD-WINDOW §5 FINAL VERDICT: counted credits $1.00 (1 row, TRUMPTIME-26AUG15,
  program concluded in-window) vs |drag| $28.6565 → **FAIL** per the §2
  pre-registration. Attribution note: the window contains the R1 probe's
  designed cost (−$10.66, strategy adverse-selection, no agent-defect class
  fired) and wind-down inventory basis; per Rule Seven this FAIL is not
  re-decomposed here.
- CLIFF-REVIEW F9 SUB-$1 RE-READ: all five zero-events still $0 at ≥7d past
  program ends → the late-payment censoring hole is CLOSED;
  **sub-$1-pays-$0 is now fully ESTABLISHED.** Cliff canon complete.
