# KALSHI MAKER — HANDOFF 2026-07-31 EOD (~19:10Z). BOT LIVE. Receipts due Aug 1-2.

**STATE (19:01Z reads):** equity $289.62 · dd $21.76/$40 · down $10.85/$60 (operator-zeroed
17:44Z after the 17:02Z down-arm halt) · bot quoting, 17 markets, 0 fails · deposits $565
lifetime (operator: +$100 landed 13:19Z as +$98.04) · rewards posted: **$0** (windows close
Aug 1-2; frozen model docs/maker_handoffs/RECEIPT_MODEL_FROZEN_2026-07-30.json, honest range
$60-180/wk, M7 2-6x hot).

## DEPLOYED TODAY (branch claude/maker-kalshi-live, all byte-exact, tests+mutation each)
`11fd9da` governor fills-feed (burn-and-run closed) + one-strike-out → superseded by E ·
`6e33300` exit loss-min calculator (fee canon 0.07·C·P·(1−P) EXACT, receipt-verified 279/279)
+ maker exit ladder + TOTAL CAP = live portfolio · `a402c25` tiered ladder ($3 exit-only day
/ $5 permanent mkt_out, grandfathered 6) + taker-cross governor (3 paid exits + $1 → trip) +
25%-capital family cap · `41ff228` F1 halt N-of-5 window, F2 balance-fail reduce-only, F3
governor fail-closed + last-good snapshot (F4 dd-credit REVERTED — see queue) · `25bf0bf`+
`6048635` fill-cost hourly refresh, sweep veto/trend cross, series probe insurance, NETEV=1 ·
`9ba74c8` re-review fix batch (inert-governor H1, touch-anchor H2, M4 silent fallback, M5
window clamp, mkt_out unconditional + mkt_out_backup.json amnesty guard, telemetry keys) ·
`64e6cc6` M3 trip-snapshot ("3 timeout then open up and if 5 then out") · `99556b1` probe-
size ALL banned-series siblings (M6 incumbent exemption REVERSED on live evidence).
Deployed quoter md5 == `git show 99556b1:kalshi_live/maker_kalshi_quoter.py` (932fdbbe…).
live.env: MARKET=45, ACTIVATE=20, INV_HARD=50, HELD_MAX=40, DOWN=60, EXITONLY=3,
FUNDING_GATE=1, NETEV_GATE=1, SERIES_PCT default 0.25, TOTAL=350 (ceiling; live cap=equity).

## TODAY'S LOSS LEDGER (attribution attached per RULE SEVEN)
Day realized ≈ −$42 morning pre-halt (churn/taker classes, pre-new-governors) + ≈−$8
afternoon structural (measured ≈$4.4/hr busy-hours = same rate as the era baseline) +
−$10.88 18:06-18:51Z = **AGENT DEFECT (M6 incumbent exemption — owned, reversed 18:53Z)**.
Three halts today: 08:28Z dd, 17:02Z down-arm ($61.05>$60, incl morning), operator zeroed +
resumed 17:44Z. MLABELSHARE family exit-only today (operator-named 18:57Z, verified zero
accumulating orders 19:01Z); siblings enter probe-sized (5ct) from Aug 1.

## BANS / LATCHES
mkt_out (permanent, backed up in mkt_out_backup.json): EURUSDAW-1.1410, MLABELSHARE-SME,
MUSKNW-T700, TOPMODEL-CLAU5, TRUMPENDORSE-A5, TRUMPTIME-H2. Day-latched exit-only (clear
00:00Z): MLABELSHARE-UMG/-WMG, TOPMODEL-CLAU5, TRUMPTIME-H2/-H4.

## OPERATOR DECISIONS ON RECORD
Self-naming DELEGATED for the open queue (feedback_kalshi_self_naming_delegated.md); holds
binding: D weekly ratchet = day-by-day · B join-size cut = only if receipts make EV
undeniable · F4 = ledger-grade PLAN to operator BEFORE build · 8-3 re-review (ladder
thresholds, STRIKES_OUT knob) · G taker budget REMOVED · shelved items stay shelved · index
families stay DENIED · relist-all-items-on-every-update rule (feedback memory) · fee canon
saved (project_kalshi_fee_formula_canon.md).

## QUEUE (self-named order)
1.1 (operator-named): BLIND REVIEW FIRST — see kickoff prompt. 2: L2 pair-carry maker unwind
(DO if guarded + blast-radius-verified; needs desired-pipeline integration). 3: F4 plan.
4+: cooldown any-loss feed, fill-shock pause, sign-flip counter, intra-cycle cap re-check,
category caps (/series has categories), scoring share-term bait, velocity breaker retune,
F12 halt-flatten cost, F13 blackout-cancels-exits, mark-based governor arm, state hardening.
TASK K receipts: watcher fires on first credit → operator UI itemization (M2b) →
per-market calibration → CAPRANK_CALIB → ALLOC_KEY enable decision.

## AGENT DEFECTS TODAY (own them; the process lesson)
(1) A-governor shipped INERT (counter clobbered pre-save; both blind reviewers found it;
fixed with cycle-round-trip pin — the test shape my originals lacked). (2) M6 incumbent
exemption cost −$10.88 live (my policy choice inside a fix batch; reversed). (3) fill-cost
refresh wired before `client` existed (NameError swallowed; fixed + cycle-level pin).
(4) reported bot "live" while it was halted (didn't check STOP after restart; watcher now
change-detects STOP both directions). LESSON ENCODED IN 1.1: blind review BEFORE deploy for
any change touching money paths; no unstated policy choices inside fix batches; every
persisted counter needs a cross-cycle round-trip test; every restart verification includes
the STOP file and a FRESH plan row.

## MONITORS
Session watcher (dies with session — REARM): STOP changes both directions, new settlements,
cash jumps >$5, peak regression vs 311.38, journal errors. Panic stop unchanged:
`sudo touch /opt/pa2-maker-kalshi-live/STOP`.
