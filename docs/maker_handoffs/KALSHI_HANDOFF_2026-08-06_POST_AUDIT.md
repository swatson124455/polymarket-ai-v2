# KALSHI MAKER — HANDOFF 2026-08-06 (POST-AUDIT). BOT LIVE, ALL RULED WORK DONE.

Supersedes `KALSHI_HANDOFF_2026-08-05_LOGIC_AUDIT.md` for current state. Canon unchanged:
`KALSHI_SCALE_PLAN_2026-08-04.md` · `KALSHI_MASTER_PLAN_2026-08-02.md` ·
`KALSHI_W10_ZERO_PAYER_STUDY_2026-08-04.md` · findings+execution record
`KALSHI_LOGIC_AUDIT_FINDINGS_2026-08-05.md`. All 13 hook-injected rules bind, PLUS the
new permanent rule [[feedback-class-not-instance]]: a misbehaving market is evidence of a
CLASS — deliverable = mechanism + standing detector, never the instance fix alone.
Memory step zero: `project_kalshi_halt_0805.md` (the full 08-05→08-06 timeline, newest
entries at top).

## 1. WHAT THIS SESSION DID (2026-08-05T19:30Z → 08-06T03:00Z, all operator-ruled)

**Logic audit (mandate)**: pipeline interaction map A1-A10 + three live collisions found
by measurement. **A1 CONFIRMED+FIXED** (rank explore-tagging probe-sized proven payers;
`d1a9db9`); **A1b BOTH tagging sites** (settled mkt_out convictions permanently tainted
6/23 payer series; expiry-at-close, `3bb2475`+`f8073e4` — deploy-verify caught site 2).
**Ruled batch**: A6 allowlist-beats-deny (`e395fbe`) · A9-F4 grace unwind-tag (`b583f7b`)
· A7 day-baseline marker + bundle stop/touch/start (`2fa5410`,`684d34e`) · A4 w6-verify
→pcap criterion (`09a203a`) · A3 family_dropped_tickers (`2000729`).
**Second wave** ("we should add"): FIX P probe slots (streak-rotation on BOOK-gate
refusal only, series-diverse, series-total-pool ranked; `ccdd52d`+`9c4612a`) · FIX H
far-close paying exception (receipt-proven + program inside horizon rides past the
market clock; `8adb690`) · SERIES_ALLOW += KXADJOURNRECESS (ruled successor).
**Identity review + mindset** ("class not instance… implement that mindset"): 4 BLOCKER
classes found (A-1 credit-string parser single-point trust; B-1 no past-close concept;
B-2 restart warmup fail-open; C-1/C-2 lineage transfers nothing either direction).
**Wave-1** ("1 yes 2 go"): ramp-aware est `_d3_est_ct` (the walk had est'd full size for
ramp-capped rows — measured 208.4/210.25 "used" vs $16.85 real committed, blocking
CHIPBURRITO) + B-1 past-close predicate (selection+belt) + B-2 close-cache state
persistence (jittered) + probe refusal for close-unknown rows + B-3 positive TTL 6h
(`c9e6ecf`+`7f2404b`). **Wave-2**: A-2 metadata event resolution in the feed builder
(kalshi_event_map.json; 179/179 resolved, 0 fallback on the live rebuild) + A-1
feed_integrity_alarms (parse-rate/truncation/paid→convicted regression; live rebuild
ZERO alarms) + B-4 last_credit_ts + C-1/C-2 kalshi_lineage.json + W16 both-direction
scan + grain corpus test + F6a streak harness test (`9390877`).
**Standing detectors (the mindset, running daily 14:00Z, kalshi-w16-report.timer)**:
W16 successor/clone finder (both directions) → w16_report.log · **W17 coverage ledger**
(every trusted-series pool dollar bucketed EARNING/LIMITED/EXCLUDED(named)/UNKNOWN —
UNKNOWN is the headline) + identity census + deny expansion + liveness + feed alarms →
w17_report.log. W17's first run found and resolved its own headline within minutes
(CHIPBURRITO UNKNOWN → drop_budget_full → the est fix).
**Ops**: halt 18.25→10 reverted by one-shot timer at exactly 00:00:00Z; day-baseline
marker mechanism ready for the next operator-named restart.

## 2. LIVE STATE (02:50:34Z plans row — RE-READ AT STEP ZERO, stale by definition)
- footprint 25 · quoted 4 · **committed $66.24 and ramping** (was $11.84 pre-fixes) ·
  equity mark $299.83 · daily_dd $0.88 vs $10 halt · cash $297.63 · 10 resting orders ·
  positions cost $1.99 · farclose_paying_kept=11 (CHIPBURRITO in) · probe_close_unknown
  592 (warmup refusals, decaying as the persisted cache fills) · probe slots = 5 distinct
  live series · zero "systematic failure".
- Deployed md5s vs HEAD `9390877`: quoter `971ea381` · credit_feedback `2d7cf41e` ·
  w16 `5ef3a7cb` · w17 (committed `w17_coverage_ledger.py`) · lineage `11d4b804`.
  Backups *.bak-A1/BATCH/BATCH2/PH/WAVE1/WAVE2-2026080x. Suite **1216 passed / 2
  xfailed, exit 0**.
- live.env deltas this session: SERIES_ALLOW += KXADJOURNRECESS (24 series) ·
  KALSHI_FARCLOSE_PAYING_EXCEPTION=1 · halt=10.
- Feed verdicts (rebuild 08-06 ~02:55Z): 23 paid / 7 never_paid_due / 21 insufficient;
  event map 179/179 metadata-resolved.

## 3. THE VERDICT (unchanged CANON): 5 clean days from 2026-08-05T14:13:28Z →
**due 2026-08-10T14:13Z**; PASS = window credits (credit_history, bot's key) > window
trading drag (position-aware recorder); PASS → widen, FAIL → halt+autopsy. Day-1
annotations: wedge-dead 14:13→19:15, A1 rank-suppression →20:00, TOPMODEL/TRUMPEND
L3-suppressed →23:40. Day-2 (08-06) is the first fully-fixed day. Daily read: credits
since 14:13:28Z vs recorder d_cash (window credits were $0.00 at the 08-05T19:38:42Z
read — payouts lump at close+1).

## 4. OPEN QUEUE
**0a. FIRST (operator-ordered 2026-08-06, "stop assuming, find real answers"): REWARD-CHIP
SEMANTICS.** Operator hovered the TOPMODEL chip: est reward $0.29 (~03:00Z); our model
integral since order placement was $0.296 — but the chip's MEANING (accrual basis, time
window, per-order vs per-market) is UNVERIFIED and I offered guesses instead of an
answer. Establish it from evidence: the HAR of the chip's API call and/or repeated
matched chip-vs-telemetry observations. Report ESTABLISHED only.
**0b. FIRST (same order): FIX H FAR-DATED PAYOUT TIMING + ADMITTED SET.** Operator
flagged the 8-day cap vs KXTOPMODEL-26AUG31 (close 2026-08-31, program to 08-09; venue
read 03:0xZ) admitted via the farclose exception. Unanswered with evidence: (1) WHEN the
venue pays a program whose market closes weeks after the program ends — canon says lump
at close+1, which would lock CHIPBURRITO/TOPMODEL-monthly reward cash until Sep 1-3;
verify from credit_history timing (a program-end-pays vs close-pays discriminator) or
HAR. (2) The FULL current FIX-H-admitted set with close dates. (3) Then present whether
the exception matches operator intent, with options (per-series opt-in / revert / keep).
No assumptions.

(calendar/standing:)
1. **W14 HAR** — chips visible WHENEVER our orders rest (they rest now): operator does
   F12→Network→HAR on a chip-bearing market page; analyze for the per-user accrual
   signal. Highest-value blocked item.
2. **Daily P2 read** each session (credits vs drag, scoped from 14:13:28Z).
3. **Read w16_report.log + w17_report.log daily** (14:00Z timer): UNKNOWN bucket +
   census alarms + successor candidates (KXTRUTHSOCIAL/KXTRUMPACT/KXMAXSHIPSHORMUZ,
   $1k/day pools each, awaiting receipts via rotated probes) are operator decisions.
4. **B8 at window end (~Aug 9-10)**: rebuild net-EV table on clean data, re-rule margin
   −7.0, re-fit NETEV_MODEL_HAIRCUT jointly with W12 before arming W12_PRICE_SHAPE.
5. **W6 widening (gated on the verdict)**: ruled-for-W6 fixes = rank quotability
   discount + near-money family tie-break (may be pre-built DARK; operator approved
   proceeding). Breadth study says universe ~1,905 (EXTRAPOLATION, denominator 2,996).
6. Watch counters: probe rotation health (probe_slots should cycle series),
   probe_close_unknown → ~0, budget_backstop_fired (should stay 0 post-C1),
   grace_unwind_tagged (occurrence = the F4 composition fired), close_past_selected.
7. Trust-expiry POLICY (B-4 detector shipped; whether "paid" should ever expire is an
   UNRULED policy question — present with data when W17 flags stale-paid series).

## 5. NEXT-SESSION PROMPT (copy-paste)
---
KALSHI MAKER LANE — new session. Kalshi venue ONLY. Real money. BOT LIVE on the
24-series pilot (verdict due 2026-08-10T14:13Z, credits>drag, CANON). Branch
`claude/maker-kalshi-live`; find the worktree via `git worktree list` (kalshi-wt under
a Temp scratchpad path); verify `git branch --show-current` before any repo write;
never touch the main checkout or master. STOP file = halt; only the operator lifts.
STEP ZERO — read in order: (1) memory `project_kalshi_halt_0805.md` (newest at top),
(2) `docs/maker_handoffs/KALSHI_HANDOFF_2026-08-06_POST_AUDIT.md` (state + queue),
(3) `KALSHI_SCALE_PLAN_2026-08-04.md` (forward plan). THEN verify live fresh (plans
row, journal "systematic failure" absent, daily_dd vs halt=10, deployed md5 vs HEAD).
THEN the daily P2 read (credits since 2026-08-05T14:13:28Z via credit_history vs
recorder d_cash) and READ w16_report.log + w17_report.log — the W17 UNKNOWN bucket and
census alarms are the work-finder (CLASS NOT INSTANCE rule binds: every finding closes
as mechanism + detector). Queue: W14 HAR when operator provides it; B8 at window end;
W6-gate fixes may be pre-built dark. All 13 hook rules + THE NORM bind (verify-first,
failing-before tests, scratch mutation, blind review, md5 deploys, suite exit-code
1216/2 baseline). Name work items yourself; bring the operator only genuine decisions
with options and a default.
---
