# KALSHI MAKER — HANDOFF 2026-08-05 EOD. BOT HALTED (operator). RESTART HELD "till fully done".

Supersedes `KALSHI_HANDOFF_2026-08-04_EOD.md` for current state. Canon unchanged:
`KALSHI_SCALE_PLAN_2026-08-04.md` (forward plan) · `KALSHI_MASTER_PLAN_2026-08-02.md`
(defect/money history) · `KALSHI_W10_ZERO_PAYER_STUDY_2026-08-04.md` (payment mechanics).
All 13 hook-injected rules bind. Memory step zero: `project_kalshi_halt_0805.md`.

## 0. STEP ZERO — verify, trust nothing here
- Worktree `…/5dfe0ebf…/scratchpad/kalshi-wt`, branch `claude/maker-kalshi-live`. Suite at
  HEAD: **1164 passed / 2 xfailed, pytest exit 0** (capture the exit code, never grep).
- **HALTED by operator 2026-08-05T01:27:36Z** ("stop the fucking bot and flatten") after
  −$7.97 in the 23:31→01:27Z live window. Flatten journal-verified: 12/12 quotes cancelled,
  book flat (1 ct floored-ladder pair left to settle by design). STOP untouched since.
- **RESTART IS HELD** (operator 2026-08-05: "hold restart till fully done"). Only an
  explicit operator "restart" lifts it. Flagged circularity, unresolved: W9/B8 (net-EV
  rebuild + `NETEV_MIN_MARGIN_PCT` re-rule + W12 haircut re-fit) needs clean LIVE days and
  cannot complete while halted — the operator decides what closes that loop.
- Cash at last read: **$300.409** (recorder row 2026-08-05T02:55:04Z; pre-restart
  2026-08-04T23:29:22Z was $308.383). The −$7.97 decomposition is ESTABLISHED from venue
  fills (n=9): KXNETFLIXTOPVIEWSTV-18 −$6.22 (78%, ONE 30-ct first-touch fill),
  KXRAIN-ATL −$1.03, KXADJOURNRECESS −$0.45 + KXAPRPOTUS −$0.20 (both fee-only exits —
  the de-risk machinery working). Full detail: memory `project_kalshi_halt_0805.md`.
- The 08-04T23:31→08-05T01:27 window is CANON-TAINTED for trading P&L and excluded from
  the P3 verdict (operator "d4 fix"); its credits remain valid receipts. Clean P2 starts
  at the next restart.

## 1. WHAT IS BUILT DARK ON THE BRANCH (all flag-OFF no-ops, test-pinned, blind-reviewed;
NOT deployed — the VPS still runs the 08-04 deploy plus the W8 recorder fix)
| item | commit | flag (ships 0) | one-liner |
|---|---|---|---|
| W3/D2 follow-the-profit rank | `30321fc` | KALSHI_D2_FEEDBACK | receipt verdicts (paid / never_paid_due / insufficient, per-EVENT due evidence @72h settled margin) multiply the capture term; harness: never-paid median rank 16-25→31-36, payers unhurt |
| W4/D3 size ramp + W7 clamp | `d7fc959` | KALSHI_D3_RAMP | 5/10/25/50ct @600s rungs, first-seen persisted; size-trust REQUIRES a credit receipt — unproven AND convicted series hold at 10ct; unwind never ramped; empty feed binds clamp (visible: qstats d3_feedback_empty) |
| W12 price-shape estimator | `90621e9`+`b2781e7` | KALSHI_W12_PRICE_SHAPE | 4p(1−p)^exp on the book MID (reflection-invariant), ONE `_w12_shape()` shared with telemetry capture; ⚠ NETEV_MODEL_HAIRCUT=3.0 must be RE-FIT at B8 before arming |
| W8 recorder scalar fix | `1640cc6` | (none — DEPLOYED) | settlement_payout = venue revenue/100; live-verified 08-04T23:59:22Z, basis marker `gross_venue_revenue` |
| W15 width-recheck pins | `b2781e7` | (pins only) | the ≤8-tick gate IS per-cycle on flat books (quoter:2636-2644); pins stop an entry-only refactor; the fill instant is unclosable by polling — size is the mitigation |
| restart bundle | `b2781e7` | — | `restart_bundle.sh "<FLAGS>" OPERATOR-RESTART-ACK`: git-show deploy + md5, feedback-feed rebuild, arm flags, archive STOP, restart, 70-min checkpoint |
| W6 verify script | this commit | — | `w6_sweep_verify.py --since <restart-iso>`: proves sweep coverage/freshness live |

## 2. SELECTION VERDICT (operator-confirmed 2026-08-05)
The rank key WITHOUT receipt feedback is PROVEN BAD twice: 89.6% of 08-02's recorded
selection slots were never-paid series (w11_replay_report.json, 1,413 cycles), and the
08-04 restart re-picked never-paid + first-touch series live (ledger 00:16:31Z). The fix
IS the dark D2+D3 stack — restarting without those flags reproduces the junk. Recommended
arming: `"D3_RAMP=1 D2_FEEDBACK=1"` (W12 stays dark until B8).

## 3. MEASURED THIS SESSION (sources inline, frozen artifacts in kalshi_live/w10_results/)
- **W10**: payment = score-share × pool with a $1.00 min-credit floor (min credit $1.01,
  0 sub-$1 of n=57); movement REFUTED as requirement; best per-hour payer was a
  motionless book ($12.94 / 2.4 min). Report: KALSHI_W10_ZERO_PAYER_STUDY_2026-08-04.md.
- **W5 breadth** (w5_breadth.json, read ~03:55Z, overnight-hour sample): 4,304 active
  programs, 2,996 tickers in the 8d horizon, 190 series; 159/250 sampled qualify (63.6%)
  → est universe ~1,905 (EXTRAPOLATION, denominator 2,996); avg commit $29.89 → $350≈12
  markets, $1k≈33, $2.5k≈84. Breadth is NOT the binding constraint; measure WRITE_BUDGET
  presence-maintenance and pay-rate instead (post-restart).
- **W11 harness** shipped (w11_replay.py): selection-only replay, no-lookahead credit
  feedback, derived validation sets. W3's sweep artifact: w3_policy_sweep.json.
- **W6**: coverage machinery verified implemented end-to-end (sweeper over all programs,
  full-universe score-ranked selection, ALLOC pcap merge); operator ruled reclassify →
  live verification at restart (w6_sweep_verify.py) + FOOTPRINT_TOP knob at the $2.5k rung.

## 4. OPEN — OPERATOR
- **Restart naming** (held): when named, run
  `bash kalshi_live/restart_bundle.sh "D3_RAMP=1 D2_FEEDBACK=1" OPERATOR-RESTART-ACK`,
  then `w1_restart_checkpoint.py --watch-min 70` (auto-launched) and `w6_sweep_verify.py`.
- **The B8 circularity** (what "fully done" means for items needing live data).
- **W14 HAR** (post-restart: chips need our resting orders; F12→Network→HAR on a
  chip-bearing page). The pool-chip half is already API-covered.
- Standing from 08-04: per-rung envelope dollars at deposit; margin −7.0 re-rule at B8.

## 5. OPEN — NEXT SESSION (no operator needed)
Nothing buildable remains while halted. On restart: P2 watch cadence, checkpoint +
W6 verify, netev/D2 counter watch (`presence_continued_under_floor`, `d3_ramp_capped`,
`d3_feedback_empty`, `netev_skipped_markets`), then B8 when days accumulate.
