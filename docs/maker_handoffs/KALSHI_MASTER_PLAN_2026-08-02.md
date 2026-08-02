# KALSHI MAKER — MASTER PLAN + HANDOFF, 2026-08-02. BOT HALTED. READ §0 FIRST.

This document supersedes the plan fragments produced during the 2026-08-02 session and is the
single handoff for the next session. Every figure carries its source and denominator. Labels:
ESTABLISHED / INFERRED / HYPOTHESIS / UNVERIFIED. All 13 hook-injected operator rules bind.

## 0. STEP ZERO — new session verifies, trusts nothing here

- Worktree: `C:/Users/samwa/AppData/Local/Temp/claude/C--lockes-picks-polymarket-ai-v2/5dfe0ebf-2821-475d-946c-72012db34c3b/scratchpad/kalshi-wt`
  branch `claude/maker-kalshi-live`. The main checkout `C:/lockes-picks/polymarket-ai-v2` belongs
  to another lane — NEVER touch it or master. Verify with `git worktree list` + `git branch --show-current`.
- **The bot AUTO-HALTED 2026-08-02T10:26:37Z and must stay halted until the operator names a
  restart.** STOP written 10:26:53.414659Z (uid 0, size 230, mtime==ctime — verified unmodified).
  Verify: STOP present · service `polymarket-maker-kalshi-ws.service` active (it idles under STOP
  and rests maker EXIT offsets — that is canon-confirmed behaviour, not a violation) ·
  deployed quoter md5 `9bfac08f6c9251b57749e1c80ddc356a` == `git show HEAD:kalshi_live/maker_kalshi_quoter.py | md5sum`
  (LF; worktree file is CRLF — always deploy from `git show`).
- Test baseline at HEAD e660620+: **981 passed / 2 xfailed** (`python -m pytest kalshi_live/ -q`,
  verified 2026-08-02 this session). Any change ships only with tests + copy-based mutation +
  blind review, per THE NORM.
- VPS: ubuntu@18.201.216.0, key `~/.ssh/LightsailDefaultKey-eu-west-1.pem`, dir
  `/opt/pa2-maker-kalshi-live` (root-owned → `sudo -n bash -c`). Journal = UTC. Deployed layout is
  FLAT (`/opt/pa2-maker-kalshi-live/maker_kalshi_quoter.py`), not `kalshi_live/…`.
- Panic stop: `sudo touch /opt/pa2-maker-kalshi-live/STOP`

## 1. WHY IT HALTED (all ESTABLISHED unless noted)

- Trigger: cumulative-down ratchet **$68.68 > $60** (`DAILY_DOWN_HALT_USD`). The $40 drawdown arm
  never exceeded $25.77 clean. STOP file text preserved verbatim in the session log.
- **$23.70 of the $68.68 (34.51%) was a torn-read accounting artifact** across 3 cycles:
  positions read at quoter `:3131`, balance ~230 lines later at `:3364`, summed at `:3445`; a fill
  landing between the reads is seen by one half only. Signature: equity round-trips to the cent on
  the next row (largest instance: 50 ct × $0.4300 = $21.50 exactly, 05:24:25Z). Artifact-free the
  meter read **$44.98 — the halt would not have fired**. The ratchet also never nets recoveries
  (`:3471`, `+ max(0, prev − now)`): $68.70 of down-moves vs $45.84 of discarded up-moves.
- The day was still genuinely negative: cost-basis equity **251.86 → 229.00 = −$22.86**
  (two independent channels agree to the cent; unrealized only −$0.46 — 98% realized cash).
- Session trading P&L **≈ −$45.7 to −$47.1** (three channels: mark-equity minus pre-halt credits
  −$46.11; cost-equity minus credits −$45.67; attribution-ledger session fills −$47.07). The
  $22.81 of rewards credited before the halt was earned by PRIOR sessions' presence — all 7
  credited events were last quoted 2026-07-26..08-01, none appears in the 08-02 quote tape.
- Six markets tripped the $3 rung (frozen snapshots total −$22.0989 — a FLOOR; final realized was
  worse, e.g. KXRAIN-26AUG02-CHI −$1.28 frozen → −$7.29 final). Five of six are weather/temp.
  Largest: KXTEMPAUSH-26AUG0203-T81.99, exactly 2 lifetime fills — maker BUY 50 ct @0.3500
  (06:07:10.161Z), TAKER close 50 ct @0.1700 (06:08:58.975Z, fee $0.4939) — 108.81 s, −$9.4939
  all-in. Loss decomposition (3 markets / 8 crosses / 123 ct): adverse move $17.254 (82%),
  half-spread $3.02 (18%), fees $0.78. Entry problem, not exit problem.
- Selection attribution: pool order did NOT put us there (all six ≤40th pct of pool; **5 of 6
  entered via the explore quota**, least-recently-attempted). The probe clamp is one-shot: 5-ct
  probe → full 50-lot in 60.5 s / 61.4 s / 24.2 s.
- RULE SEVEN framing: no defect-vs-structural decomposition has been run on this event or on the
  lifetime basis below. The historical −$122.57 percentages must NOT be transplanted.

## 2. THE MONEY (full history to 2026-08-02T16:12Z; 1,231 fills, 127 settlements, 43 series)

- Rewards **+$191.67** (55 credits: $176.67 incentive across 54 credits / 33 events / 23 series +
  $15.00 referral; cursor empty = complete) · trading **−$517.56** · net **−$325.89**.
  Attribution inline per RULE SEVEN: undecomposed; contains known agent defects (torn meter,
  one-shot probe ramp, no reward feedback) and structural maker cost in unmeasured proportion.
- 20 traded series never earned a cent: −$156.12; **defensibly-never-paid subset 14 series /
  −$127.10** (six were 08-02-only and not yet due). 13 series net positive, +$44.87 combined,
  none clears +$14. Worst five: KXAAAGASD −$44.60 · KXAAAGASW −$39.62 (zero rewards ever) ·
  KXMUSKNW −$33.92 · KXTRUMPENDORSEMENTS −$31.37 · KXTRUMPTIME −$28.36.
- Only reliably profitable shape observed: **presence with zero fills** — 5 series, +$7.51 on
  $0.00 notional (n=5 credits; do not generalise). Matches whale-study B11: for a reward-earning
  maker "every fill is a cost."
- Break-even reward pool measured **under the defective 08-02 configuration**: $1,995–$2,056/day
  (most favourable basis) vs actual pools p50 $140 / max $1,000 across 475 quoted tickers.
  This is an UPPER BOUND on the fixed configuration's cost, not a strategy verdict.

## 3. REWARD MECHANICS (newly ESTABLISHED this session)

- **credit_history is readable with the bot's own signed key — no browser needed.**
  `GET https://api.elections.kalshi.com/v1/users/3aa87f0f-3360-4584-983e-89e479efc6e5/credit_history?limit=1000`
  signed RSA-PSS-SHA256 over `{ts_ms}{METHOD}{path}` (path EXCLUDES query), headers
  KALSHI-ACCESS-KEY / -SIGNATURE / -TIMESTAMP; key id in live.env `KALSHI_API_KEY_ID`, pem
  `/opt/pa2-maker-kalshi-live/prod_key.pem`. Same signing works for /trade-api/v2/portfolio/*.
  Fields: amount_cents, created_at, credit_id, reason ("Liquidity Incentive for event
  <EVENT_TICKER>" — per-EVENT, never per-strike), status, type. Never print the key/key id.
- **Ban-before-close→$0 is REFUTED**: event-clean KXTOPMODEL-26AUG03 (only CLAU5 ever rested;
  banned pre-close) paid **$2.15** at 2026-08-02T07:17:04Z.
- **Payout keys on the REWARD-PROGRAM WINDOW END, not market close.** Close+1 held for only
  24 of 33 credited events; 9 of 33 paid BEFORE market close (by 30.7–727.0 h), all aligning with
  program `end_date`. 53 of 54 credits post 05:17–07:49Z. Hard floor: no credit under $1.00
  (min $1.01 of 54; the $1 floor is already enforced live, quoter :779-780/:2152-2196).
- Forecastability: shipped model 6.33× over (median, n=17 credited events with quote coverage).
  Resting-cycles-only → 4.35×; program-window-normalised → 1.35×; **both → 1.07× (n=16), 68% band
  1.6× wide (n=10)**. All inputs already on the wire (`/trade-api/v2/incentive_programs`,
  start_date/end_date parsed every cycle). It forecasts SIZE not WHETHER: 20 settled events with
  real presence paid $0.00 against a $26.04 forecast.
- The pool-canon "falsifier" I raised is DEAD: KXINXHUD's $11.46 was not a primary datum (the
  program's raw period_reward is $285.00 over a 0.0402-day window; a sub-day window makes both
  readings identical). Canon (`period_reward/10000` = daily pool) stands unchallenged by it.
  Residual open oddity: KXRT-MOR-65 out of band under BOTH readings.
- Ticker-stem trap (4 confirmed instances): stems carry the EVENT date; close lands the next day
  (04:00–05:00Z for weather). Any date logic must read `close_time` from
  `/trade-api/v2/markets/{ticker}` (unauthenticated), never the stem.

## 4. VOLATILITY / SELECTION MEASUREMENTS (for directive 6)

- Pre-entry volatility does NOT separate losers: AUC 0.626, 95% CI [0.492,0.750]; 0.546–0.626
  across 5 definitions, every CI straddling 0.5 (n=167 markets). 29/167 had exactly ZERO
  pre-entry volatility carrying −$167.64; the 5 worst markets sit at the 0/19/0/0/0th pct.
  Canon independently refused a pre-entry toxicity gate 2026-07-30 ("do not resurrect without
  new evidence", Spearman 0.19 n=68). Today's result reproduces that refusal.
- **SIZE separates: contracts filled AUC 0.982 at a −$1 cut (n=30 lots).** The binding cap on
  08-02 was INV_HARD_CT=50 (a contract count) at a median 50-lot price of $0.13 ≈ $6.50 notional —
  every dollar cap inert (MAX_TOTAL_CAPITAL=350 bound in 0 of 707 rows). No volatility- or
  dollars-at-risk-aware sizing control exists anywhere in the configuration.
- The loss-class markets were **minutes old**: median first fill 15 min after listing (weather
  lane), 64/169 markets under 1 h old at first fill. For them a 24 h volume OR volatility window
  structurally does not exist. The high-activity gate (MAX_VOL24H_CT=1000) caught 0 of 6 losers.
- Candlesticks endpoint: unauthenticated, per-minute OHLC including zero-trade minutes;
  `period_interval` ∈ {1,60,1440}; 1-min span ≤72 h. Coverage 126/220 random active-program
  tickers (57.3%) on a 6 h lookback.
- Score cache at the 10:26:32Z freeze: 838 ts-bearing rows of 7,772 (10.78%); **25 rows (0.32%)
  fresher than STALE_S=1800 s** → score() returns pool×0.06 for ~99.7% of the universe. (The
  same-row `scored_markets`=7,562 is the session-START epoch — different denominators, both real.)
  KALSHI_INCUMBENCY_BONUS=0.25 IS live (scores :225-226) — ranking is pool-prior + incumbency,
  not pure pool order. Sweeper writes pts/pcap which score() does NOT read; wiring it in is the
  documented "Phase 3, operator-gated" step. pcap_age_p50_m ended at its session MAX (413.1 min).
- Funnel (one warm cycle): 2,608 programs → 1,224 → 234 → 40 picked → 13 footprint → 11 quoted.
  Of 475 tickers with telemetry, 55 (11.6%) ever rested, 27 (5.7%) ever filled — largest
  uncounted stage, no plan-row counter exists for it.

## 5. CONSOLIDATED DEFECTS (verified; root cause → root-fix requirement)

CRITICAL
1. Halt meter: torn read (:3131/:3364/:3445) + no netting (:3471) + basis mixing. Fix: drive from
   realized+settled fill/settlement deltas (atomic, event-counted); keep the anti-treadmill
   ratchet property. Note: a consistency guard must key on the MIRROR condition (large cash move,
   zero held-cost move) — the originally proposed direction fires on none of the damaging cycles.
2. Loss ladder reads venue realized P&L (0.0 while a position is open) once per cycle (:3168):
   a single round trip bypasses the $3 rung entirely (KXTEMPAUSH first seen at −$9.00). A live
   inventory meter buys honest measurement + earlier cross-market consequences, NOT loss
   limitation on a one-tick adverse fill (the reduce path had already flipped same-cycle).
3. No reward feedback in selection (documented unfixed since 07-23/07-26; REWARD_VS_FILL §5
   "nothing in the rank key sees fill risk"). 14 series defensibly never paid = −$127.10.
4. Rung 2 evaluates the FROZEN trip snapshot (_eff4): a market bleeding after its trip can never
   reach permanent-out that day (KXRAIN-26AUG02-CHI −$1.28 → −$7.29, never banned). Fix: snapshot
   INVENTORY at trip; evaluate rung 2 on live delta minus unwind-attributable loss.
5. Governor's realized read (realized_last_good) OMITTED the largest single loss (KXTEMPAUSH)
   entirely — feed drops finalized markets. Fix: reconcile the feed or backstop from fills.

HIGH
6. Probe ramp one-shot (:3853): 5→50 ct in ≤61 s. Fix: size ramp keyed on a counter INDEPENDENT
   of the explore queue (10 slots/cycle vs 475 tickers would pin the venue at 5 ct otherwise).
   Do NOT key on probe-cycle capture (highest reading = fastest-losing market, measured).
7. Net-EV gate ran with an EMPTY table, silently (table + calibrator exist at HEAD, NOT deployed;
   loader fail-opens to {}). It skipped 640/2,195 times on 07-31/08-01 incl. 71 skips of a family
   its own table rates net-positive. Fix: deploy both; alarm key on every plan row when gate on +
   table empty; rebuild calibration from receipts (credit_history + /portfolio/fills replaces the
   CSV + screenshots; one gap: per-TRADE realized P&L has no direct API substitute).
   NETEV_GATE was flipped 1→0 at ~00:05Z 2026-08-02 (~3 h pre-relaunch) — operator decision on
   re-arming. PROOF CRITERION (corrected): regenerated table reproduces the shared-epoch
   KXAAAGASD-26JUL21 = −$5.27 to the cent; no sign pre-committed for any family. The earlier
   "gas −7.93% sign flip" is UNVERIFIED/refuted-as-contradiction — do not inherit it.
8. Settlement fee double-subtracted → cum_settle_payout DECREASES (8 decreases, 0 increases on
   08-02). Fix: drop the fee term (settlement fee == fill fee to the cent on 127/127); recompute
   historical columns.
9. Emit-when-nonzero plan counters make absence ambiguous (~60 keys); score_age_p50_m carries an
   undisclosed 838-row denominator next to scored_markets. Fix: always emit; label denominators.
10. Blocked exits silent: HTTP 400 on an UNWIND repeats identically (KXRAIN-26AUG02-BOS, 19 min).
    Fix: log response body into first_create_err; escalate repeated unwind failures. KXYTVIEWSW
    family 400 (03:47:59Z) — structural handling question OPEN.
11. STOP-flat churn: identical exit offsets cancelled/re-created every 30 min (new client ids).
    Fix: diff-and-keep. (Resting exit offsets under STOP per se is canon-confirmed, NOT a defect.)
12. Stale comment :616 "STRIKES_OUT=1 (the live setting)" contradicts code (:626 → 0, count bans
    OFF; all 7 mkt_out entries came via the $5 rung or grandfather). Fix at the 8-3 re-review.

## 6. THE MASTER PLAN (one sequence, dependency-ordered; each step: tests+mutation+blind review)

Phase A — instruments (nothing later is trustworthy first):
  A1. Halt-meter root fix (defect 1) — replay-proven: 08-02 series → $44.98 artifact-free;
      phase-averaged replay agreement across offsets (current spread $34.08–$62.93).
  A2. Settlement-fee fix (defect 8) — cum_settle_payout monotone.
  A3. Telemetry honesty (defect 9) — all counters always-emitted, denominators labelled.
Phase B — safe operation:
  B1. STOP diff-and-keep (defect 11). B2. Loud blocked exits (defect 10).
  B3. Rung-2 inventory-snapshot fix (defect 4). B4. Governor feed reconciliation (defect 5).
Phase C — missing measurements:
  C1. Live per-market inventory meter (defect 2) — replay-proven on KXTEMPAUSH.
  C2. Persist selection inputs: vol24h onto rows, below-cut candidates, pool histogram, PLUS the
      08-02 pairedness split (canon's lever — paired vs naked ct and cost; gap repaired here).
  C3. Net-EV rebuild + deploy + empty-table alarm (defect 7, corrected proof).
Phase D — selection/sizing changes (each gated on C):
  D1. Score-coverage fix (split never-measured vs stale; widen measurement path; swing penalty on
      the prior branch). NOTE: consuming sweeper pts in ranking is Phase-3/operator-gated — name it.
  D2. Wire reward feedback + fill cost + hours-to-close into ranking; lag exclusion keyed on
      PROGRAM end_date (not close+1 — gap repaired); proof: the 14 never-paid series rank below
      comparable payers, the 5 zero-fill earners are not deranked.
  D3. Size ramp + dollars-at-risk term (defects 6 + the sizing hole). Proof: KXTEMPAUSH replay
      goes 5→50 across multiple cycles; dollar caps bind on some of the 2,176 50-ct side quotes.
Phase E — restart:
  E1. Operator-named restart per directive 2 ("reopen when all bugs fixed, no markets >2 days
      out for now") — horizon computed from close_time, NEVER stems; direction question still
      open with the operator (short-dated markets were the losers). Clean-measurement session
      size/duration = operator decision; the break-even arithmetic above is the honest prior.

## 7. OPERATOR DIRECTIVES OF 2026-08-02 (verbatim mapping, none dropped)

1 credit_history capture — DONE (bot-key method; $24.32 on 08-02; full history $191.67/55).
2 reopen when all bugs fixed, ≤2 days out — Phase E1; direction question OPEN.
3 ratchet root fix — Phase A1. 4 netev fix + data recovery — Phase C3 (data recovered: table at
HEAD, never deployed). 5 sizing re-review + root fix + stress/smoke — Phase D3. 6 activity gate
root fix — measurement complete; mechanism decision OPEN (volatility gate refuted twice; size +
market-age are the measured separators). 7 8-3 re-review — DUE 2026-08-03, material assembled
(ladder $3/$5, STRIKES_OUT=0 already, Gov-D6 ≥15 of 21 strike pairs through the path-blind door).
8 verify close dates + payouts empirically — DONE (program-window-end finding). 9 fix all known
defects at root then verify they hid nothing else — §5 list + Phase ordering.

## 8. TIME-SENSITIVE / LOOSE ENDS (nothing here may be dropped)

- **8-3 OPERATOR RE-REVIEW DUE 2026-08-03** (ladder $3/$5, STRIKES_OUT, Gov-D6). STRIKES_OUT=0;
  strike distribution at 03:50Z: 15 tickers @1, 3 @2; strikes prune on a rolling 14-day window.
- **Naked short KXRAIN-26AUG03-PHIL −3.00** ($0.72), closes **2026-08-04T04:00:00Z** (not Aug 3 —
  stem trap); stopflat resting buy-yes walked 0.94→0.96. Operator call: keep passive / cross / settle.
- **$0.1093 of finalized-NO positions** still carried at mark (A3 +0.13, A5 +0.11, A10 +0.55,
  KXMAMDANIEO-T0 +0.11 — all resolved "no" 2026-08-02, API read 17:13:47Z).
- **Live prediction**: KXTRUMPENDORSEMENTS-26AUG01 closed 08-02T14:00Z → if close+1 holds its
  credit posts 2026-08-03 (05:17–07:49Z band). Check credit_history.
- Free test remaining captures: TOPMODEL-CLAU5 closes 08-03T14:00Z → capture 08-04.
- **7 scratch files in /tmp on the VPS** left by a sweep that violated read-only (disclosed):
  `_tape_dump.json kmeas.json __fills_dump.json __setts.json __lots.json __eps.json __study.json`.
  Removal is a write — operator names it.
- mkt_out = 9 (grew +2 on 08-02: KXRAIN-26AUG03-BOS, KXTEMPAUSH-26AUG0203-T81.99).
- Memory canon updated this session: credit_history file (stem trap + free-test limits + $191.67
  supersedes $167.35 + program-window-end payout + bot-key API method). Loss meters reset 00:00Z;
  STOP does NOT self-clear.

## 9. DECISION POINTS AWAITING OPERATOR NAMING (restated, no recommendation)

1 Restart gating: all §5 defects, or a named subset. 2 Directive-2 direction (≤2d keeps the
weather losers, cuts the political book). 3 Halt arms: $40 kept (operator-set, 3-cycle breach) vs
the $60 ratchet post-fix. 4 Arm net-EV after rebuild? (was deliberately off). 5 Directive-6
mechanism: volatility gate as named vs size/age controls as measured. 6 Selection-term weights +
fill-cost feed refresh timer (latency ~15 min–never). 7 Deny-list prefix matching (catches
KXINXHUD +$7.56, n=1). 8 Size-ramp shape (5→50 steps, hold criterion). 9 Clean-measurement
session: run at all, at what size. 10 Presence-gate calibration table granularity (absent →
default 1.0 vs measured 55/475 resting). 11 STRIKES_OUT comment-vs-setting. 12 Naked short.
13 /tmp cleanup. 14 Phase-3 (sweeper pts into ranking) — explicitly operator-gated.

## 10. SESSION ERRATA (so the next session inherits no dead claims)

Refuted/corrected this session — do not re-propagate: "close_unchecked_tail reached 0" (absent ≠
zero; one warm recurrence at 09:19:41 = 126) · "sweeper working, pcap 291→60" (60.2 was the
session MIN; ended at MAX 413.1) · "ranking is pure pool order" (incumbency bonus live) ·
"902/7,562 = 11.93%" (838/7,772 = 10.78%, epochs matter) · "book is flat" (26 positions incl. a
−3.00 naked short) · "mark artifact" day-scale (98% realized) · "gas sign flip −7.93%"
(UNVERIFIED; canon agrees −$5.27 on shared epoch) · "KXINXHUD falsifies pool canon" (dead) ·
"53% taker = entry crossing" (100% of crossings were reducing/flipping; 0/32 opening fills taker) ·
"H4 is a fourth banned ticker" (1 strike, not banned) · KXRAIN-26AUG03-PHIL "closes tomorrow"
(closes 08-04T04:00Z). Monitor reliability: the session's own watch was dark 04:36:29Z→10:26:37Z
through the entire loss event — do not treat an agent monitor as a pager; the bot's own guard is
the protection layer.
