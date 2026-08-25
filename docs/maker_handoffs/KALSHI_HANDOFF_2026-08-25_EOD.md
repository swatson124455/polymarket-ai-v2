# KALSHI HANDOFF 2026-08-25 EOD (~23:5xZ) — OVERHAUL REVIEW COMPLETE, ALL RULINGS EXECUTED

STEP ZERO next session: this doc, then memory `project_kalshi_concentrated_cliff_build.md`
(top banners) + the RULES CANON `KALSHI_R3_OFFICIAL_RULES_2026-08-25.md`. Worktree:
`.claude/worktrees/kalshi-live` @ `claude/maker-kalshi-live` HEAD `fd25d9c` (PUSHED to
origin — the branch is backed up). Verify branch before every write. Trust nothing unverified.

## LIVE STATE (verified 23:53:12Z — re-verify on arrival)
- Service active; cycle ok fails=0; committed $80.40/200; held $1.20.
- Deployed md5s: quoter `1801341d` (=HEAD blob of `19e55a0`-era; R6-meters build),
  ws_daemon `9ddc176e`. The typed API layer (`kalshi_api_types.py`) is repo-only,
  deliberately NOT on the box (unused by live path).
- Positions: −40 NO KXAAAGASW-26AUG31-3.900 (1c basis) + −40 NO KXDIESELW-26AUG31-T5.42
  (2c basis); exits resting yes@0.99 each; max further downside = the $1.20 held.
- Est-feed 23:50:20Z: T5.44 402cc / T5.42 312cc / T5.82 650cc / AAAGASD-4.0550 0cc.
  ⚠ FLAT since the ~16:14Z batch (~7.7h) — candidate causes: batch cadence longer than
  the 12:29/14:29/15:24/16:14 pattern suggested, T5.44 book de-qualified, or our
  post-restart ramp sizes (5-10ct) too small to move the meter. FIRST JOB next session:
  the 07:30Z reward-pnl read + est-feed trajectory + T5.44 qual flags decide which.

## EVERYTHING DEPLOYED TODAY (all with .baks + suite green at each step; last suite 1467/2)
1. 13:53Z CAPTURE_GATE=1 (floor $1.00 after the $2 misread correction 16:40Z).
2. 16:34Z D1 QUALIFIABLE_GATE=1 + INV_HARD-clamped `_addable` (void hole closed);
   D2 RUNWAY_ACCRUED_EXEMPT_USD=0.50.
3. 16:56Z D3 armed: REPAIR_CHEAP_FILL=1 (basis≤$0.02, gates-first) +
   EVENT_DELTA_DOLLARS=1 ($5.25/$17.50; sibling-muting fix observed live immediately).
4. 20:03Z R4 99c-max-price rule in the qualifying walk (0.99-touch = $0 model).
5. 20:17Z R6 METERS live (plan key `underlying_exposure`; first read verified vs known
   positions: aaa_gas held $0.40, diesel held $0.80/committed $10.55); caps DARK
   (UNDERLYING_MAX_COMMITTED/HELD_USD=0 — arm from observed p95, operator-gated).

## RULES CANON (obtained today — the lane's ground truth)
`KALSHI_R3_OFFICIAL_RULES_2026-08-25.md`: snapshot excluded unless cum depth ≥ Target
BOTH sides; ref = best bid; DF^ticks pro-rata within qualifying set (NO time priority);
best-bid-at-0.99 = side disqualified; $1/period floor, rounded down; feed semantics in
`KALSHI_SWEEP_FINDINGS_2026-08-25.md` (batched, lagged ~1-2h, sub-$1 rows dropped
unpaid, 0-rows real). GAS ZERO fully explained; DIESELW = the earners.

## R5/R6 STATUS
- R5: inventory (`KALSHI_R4_R5_STATUS...md`) + incident map/deletion sheet
  (`KALSHI_R5_PART2...md`, sheet RULED 08-25: rows 2/4/6 keep; row 1 delete after
  D3-A clean week ~09-01; row 3 NETEV revisit with 08-30 receipts; row 5 AMEND_DECREASE
  **VERIFIED LIVE 20:21Z** — same order_id, queue preserved, $0 cost → KEEP, unarmed)
  + typed API layer (`kalshi_api_types.py` + fixture pins; adoption per-callsite later).
- R6: design (`KALSHI_R6_UNDERLYING_RISK_DESIGN...md`) + meters LIVE; cap values await
  meter distribution (read `underlying_exposure` in plans-*.jsonl).

## OPEN ITEMS (complete list — nothing dropped)
1. FIRST JOB: diagnose the flat est-feed (07:30Z reads + T5.44 y/n_qual + batch cadence).
2. Watch `repair_rested` (first wild re-pair) + `runway_exempt_accrued` firings.
3. 08-30T04:00Z diesel period ends → receipts: cliff verdict (T5.44 $0.0402 / T5.82
   $0.0650 accrued so far — both need the pace question in item 1 answered), then:
   diesel SIZE-UP decision, NETEV revisit, M7 haircut recalibration.
4. ~09-01: delete REDUCE_ONLY_KEEP_BOTH if D3-A week clean (ruled, dated).
5. R6 cap arming (operator) once meters show p95; hot-path re-gate note in code comment.
6. Typed-layer adoption: migrate _rest_maker_offset + create/amend/cancel call sites
   first (the incident class it kills), one callsite per commit with pins.
7. Ref-price detector (best-bid vs Target/5): fires on the first thin-top qualifying book.
8. LIP-extension paper trail: kalshi.com/regulatory/notices hard-429s; May-1 filing
   eliminated (block rebates); operative answer = venue schedules programs to 09-15+.
9. F9 3,542-count method verify before reuse (endpoint is unpageable).
10. R1-probe archive full-depth-cum confirmation (optional; conflict dissolved by rules).
11. WB branch `claude/wb-fix-date-rollover-test` (36a5cb8) awaits WB-lane merge.

## TRAPS (new today)
- create_order_v2 side param = "bid"/"ask" NOT "yes"/"no" (400 otherwise); use
  create_quote wrapper. Venue order_ids are time-ordered (same-second prefix collisions
  are normal).
- incentive_programs: NO cursor at any limit — single-request counts are truncated.
- Restarts reset the d3 size ramp (5→40ct over ~30min) — batch deploys; 4 restarts today
  cost ramp time on T5.44.
- Est-feed lags its batches ~1-2h; never read a fresh snapshot as current-instant truth.

## STANDING DISCIPLINE (unchanged; all 13 hook rules bind)
Per-section adversarial review incl. EV; verified numbers w/ denominators; Rule Nine;
ship discipline; one live change per observation window (today's density was operator-
ordered); second-source before asserting; never restart ~60min of 00:00Z; 07-27 session
quarantined; operator ruled 08-27 is NOT a guillotine.
