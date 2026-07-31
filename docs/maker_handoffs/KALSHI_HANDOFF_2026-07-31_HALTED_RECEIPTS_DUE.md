# KALSHI MAKER — HANDOFF 2026-07-31 (~13:00Z). BOT HALTED. Receipts due. Governor hole found.

**BOT IS HALTED** since 08:28:38Z — the $40 drawdown breaker fired correctly (dd $41.77 from
the midnight-rearmed day-peak $255.29; 3-cycle confirm; STOP auto-written). Second self-halt in
~13h. Equity at 12:54:41Z read: **$213.38** (cash $211.09 + $2.30 positions). Operator has NOT
named a resume. Resume = clear STOP + (probably) "rearm" the day-peak, operator words only.
Panic stop unchanged: `sudo touch /opt/pa2-maker-kalshi-live/STOP`.

## THE ACCOUNT ARITHMETIC (operator-confirmed basis)
Deposits $465 total, ZERO withdrawals (operator-stated) + rewards $132 lifetime (operator UI
read 07-30) − equity $213.38 ⇒ lifetime trading net ≈ **−$384** (identity; ledger-method
cross-check 07-31 00:08Z gave −$358.57 through that hour + ~−$26 since). Era split (ONE method,
ledger cash model, 07-31 00:08Z): launch ≤07-24 = −$230.18 (the −$122.57 canon decomposition
covers only its earlier audit basis; RULE SEVEN shares apply ONLY to that basis) · middle
07-25→29 = **+$16.10** (retract any "−$82 residual" talk) · scaled ≥07-29 = −$144.49 then,
≈−$170 now (est; re-derive at speaking time).

## WHY IT HALTED (fills-verified 12:54Z)
02:41→08:28Z: −$31.57 across 18 fills. KXMLABELSHARE-W3026JUL30-SME **−$21.05 alone** (final
trading day, gap + 65ct taker exit — the MUSKNW no-warning class, biggest instance yet, within
the designed full-size envelope: $75/mkt cap × gap ≈ $20 worst case). TRUMPTIME-H2 −$7.40 and
TOPMODEL-CLAU5 −$2.76 — both re-tripped, **strike ladder fired: both strike-2 banned through
Aug 1** (working as built).

## NEW DEFECT — GOVERNOR/STRIKE BLINDNESS (unfixed, operator aware, fix needs naming)
The $5/day governor reads `realized_pnl_dollars` from OPEN positions only. A market that burns
and goes FULLY FLAT within a cycle drops out of the feed before the loss is seen: MLABELSHARE
realized ~4× the threshold and has **zero trips/strikes on record**. Burn-and-run escapes the
entire ladder. Fix: feed the governor from the FILLS record (per-market realized from fills),
not the positions read. Recommended FIRST action after operator naming, before any resume.

## STRIKE LADDER (deployed + LIVE this morning, quoter md5 0dc8e118…)
Per SPECIFIC market (full ticker, never family): strike 1 = $5/day latch to midnight (existing)
· strike 2 within 14d memory = exit-only through END of day after trip (2×) · strike 3 = OUT,
no expiry, prune-exempt (operator: "3x youre out"). History: `quoter_state.mkt_strike_hist`
(seeded 07-30 for the 5 that tripped that day). Plan key `strike2_exitonly`. Tests+mutation 5/5.
KNOWN GAPS (operator-acknowledged, queued): sibling substitution (family plan v2 QUEUED — design
only AFTER receipts+data; lumps ledger/fam gauges/strike hist/sweeper accumulate the inputs);
strike ledger lives in the fragile quoter_state (hardening queued, priority RAISED).

## RECEIPTS (task K — THE decision input, NOTHING posted yet as of 12:54Z)
MUSKNW window closed 03:59Z, APRPOTUS 15:00Z today, TRUMPTIME 14:00Z Aug 1, TOPMODEL 03:59Z
Aug 2, MAMDANIEO 13:59Z Aug 2; venue-wide 999 windows end Aug 1, 1,573 Aug 2. NO credit visible
via API yet (balance flat net of fills; only a $0 gas settlement 12:05Z). Per-event itemization
exists ONLY in the Kalshi UI (M2b) — operator pull required when credits show. Pre-registered
model FROZEN pre-receipt: docs/maker_handoffs/RECEIPT_MODEL_FROZEN_2026-07-30.json (53 mkts,
$361 raw, M7 2-6× hot ⇒ honest range $60-180). Calibration = receipt ÷ prediction per market →
KALSHI_CAPRANK_CALIB → then the Phase-3 flip decision.

## DEPLOYED vs DARK (all md5s == commit blobs; branch claude/maker-kalshi-live)
LIVE: background sweeper (venue sweep ~hourly; paginated, per-item 404 isolation, rename
counter) · family dollar cap $100 (held-$ counted, tail-cut/family counters split, solo+footgun
warn) · strike ladder · presence/NETEV mtime reload · freshness gauges SPLIT (score_age_* =
ACTUAL only — first read: actual p50 ~4 days vs pcap 32min; the old combined gauge lied) ·
peak audit trail + regression monitor (mechanism of 07-30 regression UNCONFIRMED; relic timer
quoter + one-shot -live unit QUARANTINED; dup REQ_SPACING 0.05 line deleted) · 4 field-rename
tripwires · lumps ledger tool · REQ_SPACING 0.3.
DARK (flags OFF, deploy carried code): ALLOC_INCUMBENT_FIRST (phased migration) · ALLOC_KEY
(unified capital-aware key = the shadowed cap_score at capital cut + create budget + series
rotation incl. PIVOT branch, sweeper pcap merged w/ 6h cutoff + newer-wins, risk_lambda =
lost-money-can't-compound term). Enable sequence: receipts → CALIB → operator names.

## DOUBLE-BLIND REVIEW (07-31, both reports in session; ALL findings remediated + mutation-killed)
Money math/tests/studies CLEAN. Everything found was fixed same-session (commit trail:
2a5ba84→dfdfb0f→…→strike-ladder commit). Suite 795/2xf at handoff.

## OPERATOR DECISION MENU (open, unnamed)
1. Fix governor burn-and-run hole (rec: BEFORE resume). 2. Resume (needs "resume/rearm" words;
pre-midnight rearm re-baselines peak). 3. Revisit $40 halt (= 19% of equity at $213 vs 11% at
$350) and/or MARKET cap $75 vs capital. 4. Final-day-market policy (MLABELSHARE + 2× MUSKNW =
the class; receipts quantify the other side). 5. Family plan v2 (QUEUED, after data). 6. Enable
sequence for dark features (after receipts).

## AGENT DEFECTS THIS SESSION (own them; do not repeat)
Git-checkout wiped uncommitted work TWICE during mutation harnesses (recovered fully both
times; harness must be COPY-based with timeouts, never `git checkout` on files with uncommitted
work). Also: `-p no:cov` breaks this repo's pytest config (false SURVIVED verdicts).

## TOOLING (reuse, do not rebuild)
Repo: kalshi_lumps_ledger.py (rewards column = manual UI edit), kalshi_market_sweeper.py,
frozen receipt model json, allocation-key audit + freshness plan docs (both 2026-07-30).
VPS /tmp: session_econ.py (needs venv python!), est_rewards.py, live_summary.py, etc.
Monitors from the old session die with it — REARM: guard watch (fails/429/dd≥30/fam>60%/peak
regression) + receipt watch (settlements + cash jumps >$5).
