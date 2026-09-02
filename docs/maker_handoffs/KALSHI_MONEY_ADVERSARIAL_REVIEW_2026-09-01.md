# MONEY ADVERSARIAL REVIEW — loss channels + earn blockers (2026-09-01 ~22:0xZ)

Operator-ruled scope: money = cash + credited (1b); evidence = current railed config +
measured canon only (2b); yardstick = net positive per week (3a); ruled values attackable
(4a); deliverable = report + fix sheet (5b). Two blind reviewers (loss lens / earn lens),
consolidated + verified against live.env reads 21:07-21:5xZ and session measurements.

## HEADLINE (both reviewers converge; consolidator concurs)
At $294.62 and current knobs, a net-positive week requires near-zero fill events: the
single-event loss quantum ($8-12 measured twice in 49 min) equals or exceeds the honest
weekly credit expectation (single-digit $, money-plan anchors, INFERRED), and ~79% of
tonight's measured accrual died sub-$1 on the first night. The earn engine is real
(+$0.6234/49min, best ever) but cliff + duty-cycle + clamp eat most of it. The structural
answer remains the allocator (specced, not built); the fix sheet below buys down the two
biggest live bleeds meanwhile.

## SOLVED THIS REVIEW: the 42-105s taker-exit mystery — it is the STRAND-CROSS STACK
Verified code diagnosis (loss reviewer, full-file read): no hard-inventory taker exists
(:5496-5503 "hard inventory breach alone does NOT taker"). The actual stack: fill →
exit-only strip (:3538) → maker exit rests at reducing touch → strand clock 15s
(KALSHI_STRAND_CROSS_S=15, env-verified) → exit_calc.decide lanes: spread ≤1 tick =
cross NOW; taker cost ≤ EXIT_CHEAP_CROSS_USD=$0.25 (default, env-absent) = cross NOW
(the 10-25ct events); 2× ≥3-tick adverse moves = trend-arm cross (T5.58 @0.98 in 42s);
else 2-step ladder → forced cross ~50-105s. Size-blind above STOP_TAKER_MIN_CT=5 —
explains CLAU5 at 25ct. The $11.91 flagship exits were the separate halt _flatten_all
escalation (90s grace → capped IOC). Diagnosis closes the open churn item.

## LOSS CHANNELS (ranked; verified labels)
L1 Trend-run → halt gap-through → flatten crystallization: $12-25/event (tonight $16.29
   fired vs $10 armed, −$11.91 leg); halt history ~1 per live session. ESTABLISHED.
L2 Strand-cross churn engine: −$8.04/49min measured; constants above. ESTABLISHED+DIAGNOSED.
L3 Correlated one-underlying book: ≥8 of 12 allowlist series = one fuel complex;
   UNDERLYING_MAP lacks the 6 state series (code gap); family cap $200 = 69% of equity;
   one 56ct rich-side fill ≈ $48 naked > HELD_MAX=$40 whole-book breaker. VERIFIED-CODE.
   Historical shape: 26JUL24 gas-daily −$34.98/7 strikes (in-code record).
L4 Extremes concentration: 0.99-open allowed (MAX_PRICE=0.995) where venue pays $0 —
   guarded only by the armed CAPTURE_GATE=1 (weak); PRECLOSE_FLATTEN=1 armed (env) so
   ride-to-settle is bounded. VERIFIED-ENV.
L5 One-sided leg stranding at the cap margin: 5 slots × ~$59 real ≈ $295 > $290 total cap
   ($4.62 cash headroom); create loop gates per-ORDER → a missing second leg = $0 reward
   with full fill risk. VERIFIED arithmetic + :6390-6402.
L6 Widebook ≥5-tick re-admits banned mid-band books exactly when spreads blow out in price
   discovery; never tested live at 5 ticks. VERIFIED-CODE, untested hole.
L7 Mark-spread halt hair-trigger: liquidation-mark on wide books books −$5 dd from one
   benign 100ct fill with zero adverse motion. VERIFIED mechanism.
L8-L10 Cooldown/ramp/cliff forfeitures, zero-credit probe tier negative-EV (bounded by
   clamp), fee floor ($1.86/49min measured).

## EARN BLOCKERS (ranked)
E1 Churn⋙earn: −$19.95 vs +$0.62 same window. ESTABLISHED.
E2 $1/period cliff: ~$0.49 of $0.62 died at 04:00Z (bot off). ESTABLISHED single-night.
E3 Duty cycle: off = $0; 16 days $0.00 credited (credit_history 13:18:13Z).
E4 25ct clamp catch-22 — SPLIT VERDICT (consolidator): REAL for state dailies (25ct ×
   16h period ≈ sub-$1 at measured paces → never earn the unlocking credit); NOT
   established for diesel (tonight's T5.60 pace scaled to 25ct ≈ $1/day INFERRED → weekly
   cliff clears → clamp self-unlocks); national dailies + topmodel exempt (proven).
E5 One $10-halt trip ≈ a full week's expected credits + accrual blackout to human relight.
E6 Capital scale (ruled-in): earn ceiling single-to-low-double-digit $/wk vs $8-12 loss
   quantum — a variance game at $294.
E7 Picker pool-rank: CLAU5 ($200 pool, $0.0221 earned) held a full slot while CA dailies
   earned $0.42 of the $0.62. ESTABLISHED window.
E8 1ct-fill exit-only + 1h cooldown + ramp reset = the presence tax (the mechanism behind
   the old 0.40/0.40/0.16 presence). E9 dilution near the cliff (25 measured drops).
E10 stop-clock semantics still unruled (D9 pending).

## REVIEWER CLAIMS KILLED/CORRECTED BY CONSOLIDATOR (env/study verification)
- "SERIES_PCT default 0.25 → real family cap $73.66": KILLED — KALSHI_SERIES_PCT=0 in env
  (set 17:20:33Z) → static $200 governs. Band 0.20,0.80 confirmed applied 21:01:28Z.
- "CAPTURE_GATE off-by-default": KILLED — env =1 armed.
- "B4 canon conflict unresolved": STALE — resolved this session (rule survives; doc
  KALSHI_B4_RECONCILIATION_STUDY_2026-09-01.md); reviewer read the pre-study doc.
- "PRECLOSE_FLATTEN armed-state unverified": CLOSED — env =1.

## FIX SHEET (5b — exact values, one-word approvals; NOTHING applied)
F1. KALSHI_EXIT_CHEAP_CROSS_USD (absent→default 0.25) → **0.05** — stop auto-paying the
    spread on every small fill; maker exits get a chance. Counterweight: longer naked
    windows (measured naked $0.13645/ct) — at clamped sizes ≈ small per-minute exposure.
F2. KALSHI_STRAND_CROSS_S 15 → **60** — one minute of maker-exit patience; the trend-arm
    still crosses immediately in real runs. Same counterweight as F1.
F3. KALSHI_MAX_TOTAL_CAPITAL 290 → **240** — kills L5 stranding (4×$59=$236 fits) and
    restores a $54 cash buffer (also answers the standing headroom flag).
F4. KALSHI_MAX_PRICE_DOLLARS 0.995 → **0.985** — can never open at 0.99 (venue pays $0
    there); 0.98, which pays, stays open.
F5. KALSHI_WIDEBOOK_MIN_SPREAD_TICKS 5 → **8** — closes the price-discovery re-admission
    hole; tonight's earner 5.7100 (10 ticks) still qualifies.
F6. NO-CHANGE lines needing your explicit ruling: halt $10 (E5 data attached — one trip
    ≈ a week's earn); 25ct clamp for state dailies (E4 catch-22 — the clean fix is the
    allocator's real-size daily entries per your D8 ruling, not a clamp loosening);
    D9 stop-clock semantics.
PR LIST (code, full ship discipline, separate approvals): UNDERLYING_MAP += 6 state
    series (L3); :3290 comment correction (B4); _qualifying_breakdown 0.99 pin (census);
    ALLOCATOR BUILD — the structural answer to E1/E2/E7.

Bot OFF throughout; raw reviewer outputs in session task transcripts.
