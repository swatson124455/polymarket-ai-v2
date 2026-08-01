# KALSHI MAKER — HANDOFF 2026-08-01 ~00:15Z. BOT LIVE. First receipts due TODAY.

## STATE (verified 00:09–00:11Z reads, post-reboot)
Quoting: plan row 00:09:01Z — equity $286.75, committed $228.78 / 6 markets, day
meters rolled (dd 0.0 / down 0.0, peak $286.75), NO STOP, 0 journal errors since boot.
Branch `claude/maker-kalshi-live` @ `61e8a50`. Deployed quoter md5
`420dc43feb1ed46353e902ecee58b5ae` == `git show b728902:kalshi_live/maker_kalshi_quoter.py`.
Cash $284.27 / settlements 97 (watcher baseline 00:10:51Z).

## ⏰ FIRST TASKS NEXT SESSION (time-ordered)
1. **STEP ZERO as always**: verify md5, env, STOP + fresh plan row yourself.
2. **10:00 UTC (6am ET): cap auto-revert fires** — systemd one-shot
   `kalshi-caps-revert-20260801.timer` runs `/usr/local/bin/kalshi-revert-caps-20260801.sh`
   (sets MARKET 60→45, ACTIVATE 60→40, restarts service, logs to
   `/opt/pa2-maker-kalshi-live/caps_autorevert.log`). **VERIFY it fired AND the bot is
   quoting after its restart** (STOP + fresh plan row — "active" is not "trading").
   If the box rebooted again before 10:00, the transient timer is DEAD — re-create it.
3. **RECEIPTS — rolling, starting TODAY** (operator-corrected 2026-07-31 ~23:45Z:
   payout = PER MARKET, the day AFTER that market closes — the old "windows close
   Aug 1-2" framing is WRONG, do not repeat it). Jul-31 closers pay today:
   EURUSDAW ladder (9), MUSKNW-T700, MLABELSHARE SME/UMG/WMG, AAAGASD-26JUL31-4.105,
   APRPOTUS-26JUL31 (2), GENERICBALLOTVOTEHUB-T5.7. Frozen-model expectations
   (RECEIPT_MODEL_FROZEN_2026-07-30.json; model runs 2-6x hot): MUSKNW $48.15,
   SME $28.94, GENERICBALLOT $23.45, WMG $18.57, EURUSD strikes $0.06-5.23,
   APRPOTUS $0.23-0.41 (these may fall under the $1 payout floor).
   No API reward feed exists — credits arrive as CASH + UI itemization only.
   On credit: ask operator for the UI per-event itemization (M2b) → build
   receipt-vs-frozen-model calibration → CAPRANK_CALIB → bring the ALLOC_KEY enable
   decision. **Also append the per-market REWARD column to
   `docs/maker_handoffs/LOSS_LEDGER.md`** (operator-named ledger, @ 61e8a50;
   maintenance rules are in the file — never edit sourced numbers, add dated lines).
4. Rolling payout calendar (venue reads 23:46-48Z 07-31): Aug 2 = TRUMPTIME H2/H3/H4 +
   AAAGASD-26AUG01 (4) + TRUMPUAP · Aug 3 = TRUMPENDORSEMENTS A3/A5/A10 + MAMDANIEO ·
   Aug 4 = CHINAAI + TOPMODEL · Aug 5 = MCMORROWENDORSE (3) · Aug 8 = APRPOTUS-39.2 ·
   Aug 9 = SENATEADJOURN + CLARITYVOTE.

## DONE 2026-07-31 EVENING SESSION (all byte-exact, tests+mutation each)
- **1.1 (operator-named) blind review BEFORE deploy**: two independent reviewers
  (diff + live runtime) on 235619f..61d08d1. NO CRITICAL; all 12 runtime engagement
  checks ENGAGED (07-30 inert-governor class did not recur; taker counter == journal
  1:1; peak ratchet held across 11 restarts). Fix batch `a295045` deployed 19:37Z
  (suite 872/2xf, mutation 8/8): D1 fam_held gate → matches _series_cap activation
  (LATENT — live.env HAS SERIES_MAX_USD=100, which the prior handoff omitted);
  D2 known-ban enforcement outside governor try (day-aware); D3 realized last-good
  persisted to state; D4 amnesty re-apply loud; D8 dead branch; RF1 fam_top_pct on the
  equity basis (live 14.1% = 40.8/289.49; old gauge read 33.8 pct-of-book).
- **L2 pair-carry maker unwind** `b728902` BUILT DARK + deployed 19:54Z (md5 420dc43f,
  suite 882/2xf, mutation 6/6). KALSHI_PAIR_UNWIND absent = OFF = byte-identical
  (test-pinned; bot's own env audit counts 71 absent knobs confirming dark). Measured
  first: paired book 0.00 contracts at 19:45:11Z — arms for future pairs; real use =
  exit-only/banned markets carrying pairs. **Enable = operator naming.**
- **F4**: PLAN ONLY (`680b74b`, F4_LEDGER_GRADE_PLAN_2026-07-31.md — identity-based
  external-credit detection, offline validation gate first). **No build until named.**
- **LOSS LEDGER** (operator-named, bookkeeping): LOSS_LEDGER.md @ 61e8a50 — per-market/
  per-family losses with sourced reasons (DEFECT vs MECHANISM vs ERA-AGG).
- **Operator-named env changes**: 20:24Z SCORE_EXPLORE 3→10 + ACTIVATE 20→40; 20:54Z
  MARKET 45→60 + ACTIVATE 40→60 OVERNIGHT ONLY (revert timer above). Committed
  measured $106→$190 within 11min (plan rows 20:24-20:49Z), $228.78 post-reboot.
- **VPS OUTAGE ~23:52Z→00:08Z**: box hung (Lightsail state=running, network dead);
  rebooted via LOCAL AWS CLI (works from the dev box:
  `aws lightsail reboot-instance --instance-name Ubuntu-32 --region eu-west-1`).
  Bot cycled til ≥00:06:44Z during the wedge — true unmanaged window ≈2min. Post-boot
  verified clean. **Transient systemd-run timers DIE on reboot** — revert timer was
  re-created 00:10Z.

## REPORT-ONLY FINDINGS AWAITING OPERATOR (from 1.1 review; none demoted)
D5 fresh-$5-ban siblings full-size one extra cycle · D6 venue-forced settle/preclose
takers count as taker-gov episodes + pollute strike_hist (→ 8-3 re-review) · D7
sweep-veto not time-normalized · D9 knob live-refresh asymmetry (SWEEP_VETO_TICKS,
EXPLORE_PROBE_CT, SERIES_MAX_USD, FILLCOST_REFRESH_S restart-only) · D10 no family gate
first cycle after state loss · RF2 taker re-rest FAILED ×10 (naked one cycle each) ·
RF3 strike-parse warning = categorical BY DESIGN (~250/day noise; counter-split is an
option) · RF4 exit-receipt record corrected $5.88→$5.41 (through 17:02Z 07-31) ·
cum_settle_payout in cash rows DECREASED 62.17→60.28 while settlements rose (suspect
sliding-window recompute — trace before anyone quotes it as a lifetime total).

## QUEUE (self-naming DELEGATED; holds binding)
Open audit items: cooldown any-loss feed, fill-shock pause, sign-flip counter,
intra-cycle cap re-check, category caps, scoring share-term bait, velocity breaker
retune, F12 halt-flatten cost, F13 blackout-cancels-exits, mark-based governor arm,
state hardening. HOLDS: D weekly ratchet day-by-day · B join-size cut receipts-gated ·
**8-3 OPERATOR RE-REVIEW DUE 2026-08-03** (ladder thresholds $3/$5, STRIKES_OUT; every
session must surface it) · G removed · shelved stays shelved · index families DENIED.
Churn-control options offered to operator (not yet named): soak-gate new governors
(watch-only N hours), deploy freeze during receipt windows.

## BANS / LATCHES
mkt_out permanent (6, == mkt_out_backup.json): EURUSDAW-1.1410, MLABELSHARE-SME,
MUSKNW-T700, TOPMODEL-CLAU5, TRUMPENDORSEMENTS-A5, TRUMPTIME-H2. Day-latches cleared
at the 00:00Z roll (loss_exitonly 0 on the 00:09:01Z row); banned-series siblings
quote PROBE-SIZED (5ct) per 99556b1.

## MONITORS (die with the session — REARM)
Session watcher armed 00:10:51Z (STOP both directions, settlements, cash >$5, peak
regression vs 286.75, journal errors). Rearm baseline = fresh reads, peak = that day's.
Panic stop unchanged: `sudo touch /opt/pa2-maker-kalshi-live/STOP`.
