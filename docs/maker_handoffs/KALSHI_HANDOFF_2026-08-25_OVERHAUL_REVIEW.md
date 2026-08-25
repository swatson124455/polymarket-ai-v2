# KALSHI HANDOFF 2026-08-25 (~03:4xZ) — OVERHAUL REVIEW PLAN (operator-ordered)

STEP ZERO next session: this doc, then memory
`project_kalshi_concentrated_cliff_build.md` + `feedback_kalshi_build_rulings_20260824.md`.
Worktree: C:/lockes-picks/polymarket-ai-v2/.claude/worktrees/kalshi-live @ claude/maker-kalshi-live
(HEAD >= 8ee0b8f + this doc). Verify branch before every write. Trust nothing unverified.

## LIVE STATE (verified 03:35:13Z, re-verify on arrival)
- Bot LIVE (polymarket-maker-kalshi-ws active), balance $316.4766.
- HOLDING cheap-side inventory: 40 NO KXAAAGASW-26AUG31-3.900 (@0.01, filled
  08-24T19:49:13Z) + 5 NO KXAAAGASD-26AUG25-4.0600 (@0.01, filled 08-25T01:32:57Z).
  Max further downside = the $0.45 basis already paid; maker EXITS resting (yes
  0.98x40 / 0.99x5) that PROFIT ~1c/ct if filled. Window realized so far:
  -$3.55 USDJPY (strategy adverse-selection) +$0.16 gas -$0.007 fee = -$3.39;
  credits this window $0. Caps 60/60/200, halt $10/d, all intact.
- Deployed md5s: quoter `6753e6c3` (=blob of 5bff8b6-era HEAD), watchlist sync
  `abb51efe`, storm detector `f5c15fd3`. Timers: cliff-shadow DISABLED;
  d4-watchlist-sync 6h; storm-detector 5min; reward-pnl 07:30Z; scoreboard 07:40Z
  (window T0=08-20T16:40:24Z, T7=08-27T16:40:24Z — operator RULED the date is NOT
  a guillotine; scoreboard identity-gap = resting reservation artifact).

## THE OVERHAUL REVIEW — SIX ITEMS, EVIDENCE-RANKED (operator-approved agenda)
R1. PAIRING LOOP (highest dollar impact). MEASURED 08-24/25: one 40c cheap-side
    fill at 19:49Z put 3.900 exit-only => ~8h of flagship 40ct presence earned
    ZERO accrual. "Fills stop scoring" (R1-probe canon) is documented, not
    implemented as re-pairing. Deliverable: hold-state machine map (which states
    earn, which do not, transition latency per event class) + a RE-PAIR-AFTER-
    CHEAP-FILL design (rest the consumed side again within caps; the inventory
    is benign 1c basis) — operator signoff before code.
R2. SHARE ECONOMICS. MEASURED: 2.5h genuinely-paired at 40ct on 3.900 shows NO
    est-feed row (03:33Z snapshot: only evicted USDJPY residual $0.1999).
    Either share vs the 2,050ct rival wall ~ 0, or the feed hides small values —
    UNVERIFIED WHICH; everything depends on it. Deliverable: share study — our
    depth vs total book depth vs credited accrual per hour (D4 tape + est-feed
    + R4-walk replica), incl. est-feed zero-row semantics test.
R3. REWARD FUNCTION FROM PRIMARY DOCUMENTS. We learned the $1 cliff 3 weeks in.
    Deliverable: full CFTC filing + LIP program terms line-by-line (repo has
    partial quotes in KALSHI_INTENT_VS_ACTUAL_2026-07-26.md), derive the
    optimal portfolio FORWARD from the rules; reconcile against every canon
    (cliff, pairedness, fills-stop-scoring, DF walk).
R4. INSTRUMENT TRUST. CAUGHT mid-review: D4 recorder reports 3.900 as 0/210
    two-sided during hours our own orders made it two-sided — occupancy field
    wrong (likely implied-ask semantics). Deliverable: validate every gauge
    (D4 fields, est-feed thresholds, scoreboard) against a second source;
    fix the D4 occupancy parse.
R5. COMPLEXITY vs INCIDENT LEDGER. COUNTED: 140 knobs declared / 77 set in
    live.env; this week 5 code-caused incidents vs 1 strategy-caused.
    Deliverable: map every guard/knob to the incident it prevents; deletion
    list = operator decision (Rule Nine); + typed venue-API layer tested
    against RECORDED REAL responses (kills the parse-defect class: the
    _rest_maker_offset shape bug, anchor v1/v2, all would have been caught).
R6. RISK UNIT. 18 gas strikes = ONE gas bet; caps count markets. Deliverable:
    per-UNDERLYING exposure accounting design (gas/diesel/model-leaderboard).

## SETTLED BY MEASUREMENT (do not re-litigate; listed so nothing silently drops)
- USDJPY-class: sub-hourly sweeps invisible in own tape (606ct/1min, 08-23);
  out until an underlying-price watcher exists. Study D + 1-min read are canon.
- 0.99-touch books: STRUCTURALLY unanchorable on the 1c grid (anchor v3 rule:
  ANCHOR_PRICE < 1 - touch). Anchor coverage = touch<=0.98 subset only; judge
  by anchor_paired counts, currently ~0-4/day.
- Mid-band (0.10,0.90): toxic (19.6%/hr near-strike moves), stays excluded.
- Sub-$1 program pays $0: fully ESTABLISHED (late-payment hole closed 08-21).
  Above-cliff pays in full: 5/5 incl. 3/3 extreme-shell.
- Payment reliability + Aug UI/API reconciliation exact ($57.33 = 22 credits).

## OPEN OPERATOR ASKS (pending answers)
1. MIN_RUNWAY_H=49 blocks RE-ENTRY to already-accruing programs (cost ~1.6d of
   DIESELW-26AUG24, 08-21). Option: exempt when est-feed accrued > $0.50.
2. R5 deletion list (comes back as a decision sheet after the mapping).

## STANDING DISCIPLINE (unchanged; hook-injected rules all bind)
Per-section adversarial review incl. EV lens; verified numbers w/ denominators;
Rule Nine on any demotion; ship discipline (tests+suite exit 0+md5-vs-blob+real
run+negative test); one shadow/live change per observation window; validate
probes against a second source before asserting (08-21/08-24 lesson);
never restart within ~60min of 00:00Z; STOP discipline; 07-27 session quarantined.
