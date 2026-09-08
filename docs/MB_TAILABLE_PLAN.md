# MB TAILABLE-TRADER PROGRAM — THE PLAN (operator handoff 2026-09-08 ~18:5xZ)

**Operator, verbatim:** "we need the top traders we can tail verified and on a
list as well as a set up to monitor them and new possible candidates. we need
to track roi based on our wager algo not 100 flatrate. create full set up and
plan to get this the fuck done."

**Operator design rulings (2026-09-08, binding — full text in memory
feedback_dollars_per_day_is_the_test.md, top block):**
- BACKTEST = admission to the list. FORWARD WATCHING = drop-off tripwire.
- ROI basis = OUR WAGER ALGORITHM (mb_sizer stake per wager), NOT $100 flat.
- Sessions stay on THIS deliverable. No unrequested audits or meta-work.

Every $ figure anywhere in this program is HYPOTHETICAL until real orders
exist; real money only via docs/MB_GO_CHECKLIST.md. That is unchanged.

---

## 0. WHERE IT STANDS (all measured 2026-09-08, sources named)

**Machinery that exists and works (verified live today):**
- Discovery board: `mb_backtest.py daily-replay` — cron stage 9, 11:40Z.
  Universe 182,752 wallets, 21,018 tailable candidates (conc<=20,
  position-level), label coverage 80.7% after the window crawl. Re-run on
  correct code 17:58Z; artifacts `backtest/leaderboard_{roster,firehose}.jsonl`.
- Live watcher `polymarket-mirror3` (systemd): roster=127 (`chain_audit.json`
  `clean`), records every roster BUY/SELL with our real shadow fill + gate
  verdict into `mirror3_shadow.jsonl` / `mirror3_shadow_sells.jsonl`.
- Forward grader `cohort5_qualification.py` (cron stage 3) on the ruled ROI
  basis; funnel + hypo ledger + band; canon verifier (ALARMS=0 daily).
- Chain deep-dive `/opt/mirror3/scripts/chain_deep_dive.py` = fraud/skill
  screen (ADMIT / INSUFFICIENT / REJECT; proposals only).
- 1-month eligibility read: data-api `activity?user=..&sortDirection=ASC`
  (query shape proven 09-07 against a known-old wallet).
- Watchdogs: `[chain]` (stage list now OUTSIDE the clone) + `[stall]` every
  15 min (alive-but-not-progressing). Deploy path for `/opt/pa2-shared/
  mb_readout` = MERGE TO MASTER; a hand `install` there dies at the 12:30Z
  refresh.

**The list as of 18:48Z (HYPOTHETICAL $100/wager holdout, 17:58Z board):**
| wallet | board LCB $/wk | n_ho | conc | cov | eligibility | dive | roster | survives selection |
|---|---|---|---|---|---|---|---|---|
| 0x5feea3460c | +14,659 | 134 | 18 | 94 | PASS (2025-12) | ADMIT | cohort5 | YES (0/400 null) |
| 0xe48217d0b7 | +1,312 | 9 | 17 | 91 | PASS | ADMIT | cohort5 | no (p=0.19) |
| 0x2c50852938 | +789 | 8 | 11 | 75 | PASS | ADMIT | cohort5 | no (p=0.47) |
| 0x1c010e69db | +1,265 | 79 | 15 | 94 | PASS | INSUFF (span 57d, clean) | no | untested |
| 0x4ab40f2a49 | +6,704 | 13 | 8 | 97 | PASS | queue running | no | fails e-leg 60x |
| 0xf7d03961dd, 0x75a27d0cc9, 0xe9f5c75ee1, 0x4f453abb65 | +108..+15 | 7-18 | 6-19 | 82-90 | PASS | queue running | no | untested |
Dive queue: `bash /tmp/promo_queue_0908.sh` launched 18:20:04Z (log
`/tmp/promo_queue_0908.log`; dossiers in `deep_dive_promo0907/`). New
cov-FLAGGED high rows (0x1fd5ead7fe 20%, 0xab9555abda 21%, 0x04da946c9f 45%)
are NOT list candidates until coverage clears 50%.

**Three defects that block the deliverable (measured, not fixed):**
- D1 **DATED — 2026-09-13T22:30Z.** `cohort5_qualification.py:396` hardcodes
  `epoch = BASIS_EPOCH` (2026-09-06T22:30Z) with `FUTILITY_DAYS=7.0` (:248,
  pinned by self-test :655-656). Any wallet registered as-is locks NOT
  DEMONSTRATED on 09-13 with n=0 — immutable. Under ruling 1 that is a FALSE
  drop-off. The cohort5 wallets are NOT registered (0 grep hits) so nothing
  has fired yet.
- D2 The reported $ basis is $100 flat (`$ref100`); the sizer column
  (`$sizer`) is $0 because it only fires post-PASS. Ruling 2 says the sizer
  ALGO stake is the basis.
- D3 The replay assumes ~100% fill; only 47.0% of roster first-buys clear
  the live gates (OK 11,807 / NO_UPSIDE 7,441 / SPREAD 3,262 / RAN_AWAY
  2,611 / NO_BOOK 14). Every board RATE is ~2x optimistic. (Haircut PRICE is
  fine — 0 of 11,806 measured pairs exceed +0.03.)

---

## 1. BUILD PLAN — in order, each step has a measurable done-state

### P1 — THE LIST (verified tailable traders), day 1
Build `scripts/mb_tailable_list.py` → writes `docs/MB_TAILABLE_LIST.md` AND
`/opt/pa2-shared/mb_copyable_data/tailable_list.json`. One row per wallet,
generated from artifacts (never hand-typed):
- Inputs: `leaderboard_firehose.jsonl` (+ roster board), `deep_dive*/` dossiers,
  eligibility reads (cache them in `tailable_elig.json` with read timestamp),
  `chain_audit.json`, `firehose/peak_conc.jsonl`.
- VERIFIED = all of: eligibility PASS (first trade <= today−30d, >=25
  trades) · conc<=20 position-level · cov>=50% · holdout ROI LCB > 0 ·
  dive ADMIT. Columns for each, plus: selection-survives (Y/N from the
  null replication; carry the p), $ at OUR sizer stake (P3), label vintage
  (cache key count at run time), last forward fill, forward status.
- Tiers: VERIFIED (all green) · PENDING (dive/eligibility outstanding) ·
  FLAGGED (cov<50% or conc>20) · DROPPED (forward tripwire fired, P2).
- Done when: the script runs from cron (stage 11 after the boards, chain-watch
  stage file updated) and the doc regenerates daily with a provenance header.

### P2 — MONITOR (drop-off tripwire), day 1–2
- Fix D1: `eproc_grade` honors the per-group epoch; new admits get
  `admitted_utc` from `chain_audit.json` as their clock. Update the pinning
  self-test (:655) deliberately, add a test that a group admitted after
  BASIS_EPOCH starts at its own epoch. Mutation-check. OPERATOR-APPROVED
  in the handoff (ruling 1 makes the current behavior a defect).
- Register list wallets in the grader automatically: `eligible_admits()`
  already ingests ADMIT dossiers from `deep_dive/` — point it at
  `deep_dive_promo0907/` too (or copy dossiers in), so registration = dive
  ADMIT + roster, no hand list.
- Drop-off definition (per ruling 1, forward = tripwire): a list wallet is
  DROPPED when its forward ROI LCB (grader) is < 0 at n>=10 OR its forward
  futility lock fires; DEGRADED when forward realized ROI < 0 at n>=5.
  Surface in the daily funnel + the list doc. Never removes from roster
  without operator ruling (report + ask).
- Done when: the 3 cohort5 wallets show a forward line in the 11:40Z grader
  with THEIR epoch (02:56:10Z), and the list doc shows forward status.

### P3 — ROI ON OUR WAGER ALGO (ruling 2), day 2
- In `mb_hypo_ledger.py` and both boards: compute per-wager stake with
  `mb_sizer.recommend_stake*(...)` using the live foursome from
  `/opt/pa2-shared/mb_sizer.env` (funnel line today: bankroll $500 × mult
  0.25 / conc = trader's measured peak, at the trader's median fill),
  AS-IF-APPROVED, and report `$algo` = Σ roi × stake as the HEADLINE money
  column; keep `$ref100` as comparison. Label HYPOTHETICAL. Pin in
  self-test that `$algo` uses the sizer, never a constant.
- Done when: the daily funnel/hypo/boards print `$algo` first, and the list
  doc's money column is `$algo/wk`.

### P4 — NEW CANDIDATES (discovery → verification pipeline), day 2–3
- Daily: after the boards, take every QUALIFIES row with cov>=50% and
  conc<=20 not already on the list → eligibility read → if PASS and no
  dossier, append to a dive queue file. One dive runner (systemd timer or
  the stall-watched `promo_queue` pattern with PID self-exclusion) drains
  the queue serially. ADMITs land as PENDING→VERIFIED on the list
  automatically; roster ADD stays an operator ruling (present the row).
- Re-dive INSUFFICIENT-span wallets when span crosses 60d (0x1c010e69db on/
  after 2026-09-10).
- Done when: a new QUALIFIES on tomorrow's board appears on the list as
  PENDING with its eligibility read, and the queue log shows its dive.

### P5 — FILL REALISM (D3), day 3
- In `synth_records`, apply the measured per-price-bucket gate pass rate
  (or replay the chase/spread gates) so wager COUNTS and rates reflect the
  47% that actually fill. Report the pass-rate table in the board header.
- Done when: board headers show `fill-modeled` and rates drop accordingly.

### P6 — REAL MONEY (unchanged, operator-gated)
GO checklist items 3/5 blanks (depth frac, event cap, pilot executor with
per-fill slippage recording). Not a build order until the operator says.

---

## 2. STANDING FENCES + LANDMINES (do not re-learn these)
- Deploy = merge to master; the clone refreshes 12:30Z. Hand-installs die.
- Never `pgrep -f` a pattern your own cmdline contains; exclude by PID.
- Never put DATABASE_URL on a command line; read it inside a script.
- Never run the readout cron as root (re-contaminates .git ownership).
- Quote every holdout $ with its label vintage (a crawl moved one wallet
  +$1,310 → +$64/wk with zero lookahead).
- Locks are immutable; a false lock is permanent. Fix D1 before any grader
  registration.
- `polymarket-mirror3`: never hand-relaunch; `systemctl restart` only.
