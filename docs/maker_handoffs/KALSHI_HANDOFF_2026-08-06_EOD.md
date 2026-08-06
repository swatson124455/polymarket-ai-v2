# KALSHI MAKER — HANDOFF 2026-08-06 EOD. BOT LIVE. FIRST JOB = ADVERSARIAL REVIEW (ruled).

Supersedes `KALSHI_HANDOFF_2026-08-06_POST_AUDIT.md` for current state. Canon unchanged:
`KALSHI_SCALE_PLAN_2026-08-04.md` · `KALSHI_MASTER_PLAN_2026-08-02.md` ·
`KALSHI_REWARD_GAME_AUDIT_2026-08-06.md` (this session's game audit — read §1 formula
first). All 13 hook rules + CLASS-NOT-INSTANCE bind. Memory step zero:
`project_kalshi_estimates_feed.md` (newest entries at top), then `project_kalshi_halt_0805.md`.

## 0. FIRST JOB (operator-ruled 2026-08-06, scope 1a+, ALL RULINGS BINDING) —
## FULL ADVERSARIAL REVIEW, run at session START with fresh context

Operator mandate: full adversarial review of the 08-06 session's work and the live bot;
verify fixes didn't break/misalign other functions or HIDE other bugs; verify logic flow
top-to-bottom; catalog ALL remaining elevation/logic opportunities ("meat on the bone",
easy or hard). Scope rulings:
- **1a+**: full line-depth on the RUNTIME MONEY PATH (10 files, ~10,900 lines:
  maker_kalshi_quoter.py 7,317 · maker_kalshi_ws_daemon.py 766 · maker_kalshi_client.py
  480 · kalshi_ws_feed.py 395 · kalshi_cash_recorder.py 343 · kalshi_attribution_ledger.py
  487 · kalshi_market_scores.py 303 · kalshi_capital_rank.py · kalshi_credit_feedback.py ·
  kalshi_estimates_recorder.py) AND all CANON-FEEDING measurement tools (~15 files:
  netev_rebuild/calibrate, settlement_pnl, ledger(s), w16_successor_finder,
  w17_coverage_ledger, drift tools). EXCLUDED by ruling (named, not silently dropped):
  the ~160 frozen one-shot study/diagnostic artifacts (VER*, AUD_*, _refute*, census/
  study scripts) — reviewable later on operator order.
- **Order**: this session's work FIRST (commits `ae89543..7e43554`, 9 commits: 0b tool,
  estimates recorder, game-audit doc, D-B+D-G fixes `4d76994`+`f932797`, D-A `5d9266f`,
  D-C `1dd427e`, review follow-ups `7e43554`), THEN the whole live-bot logic flow
  (selection → gates → sizing → budget walk → execution/diff → exits/strand/settle →
  persistence/restart).
- **Decision batch**: findings-first; NOTHING is fixed without operator ruling.
- **4a**: opportunities are CATALOGED with effort + expected value; nothing built.
- **Multi-agent fan-out AUTHORIZED** (~10-12 reviewers + adversarial verify pass).
- Feed the reviewers the KNOWN-OPEN notes so they're assessed, not rediscovered:
  D-C #6-part2 (designated tickers have NO reserved footprint seat), #8 (select_footprint
  macro hunks untested), #12 (3ct dust fill suspends a macro probe on an empty book until
  strand clears), #16 (ladder count churn on ext change); D-B NOTEs (sweeper pcap
  own-threading unthreaded — consumer gated off; settle-ramp absolute anchor; F3
  asym-join conservative). Also verify the fixed items against THEIR failing-before
  tests: test_db_reward_fixes.py (9), test_da_est_feed.py (4), test_dc_macro_probe.py (4).

## 1. LIVE STATE AT HANDOFF (verify fresh at step zero — stale by definition)
- **BOT LIVE** on the 24-series pilot. Deployed quoter md5 `5c7aed6f` = HEAD `7e43554`
  (backups: .bak-DARKDAC-20260806 = `7b0b9122` [D-B/D-G build], .bak-DB-20260806 =
  `971ea381` [pre-session]). Suite baseline **1233 passed / 2 xfailed, exit 0**.
- **Day timeline 08-06 (UTC)**: auto daily-loss halt 15:54:54Z (dd $10.31 > $10 from
  day-peak $300.71; STOP self-placed; flattened ~15:56:44Z; drag class = STRUCTURAL DRIP
  per canonical replay decomp — gas family ≈ −$9.42 of −$11.96 window net, NO defect
  class) → operator REARM 17:36:23Z with a KNOWING fresh $10 (marker consumed 17:37:20Z)
  → D-B/D-G deploy 17:31Z → dark D-A/D-C deploy ~18:20Z; last verified cycle 18:21:57Z
  clean (footprint 33, quoted 7, daily_dd $0.06 persisted through restart).
- **Flags**: D3_RAMP=1, D2=1, PRESENCE_GATE=1, NETEV_GATE=1, FARCLOSE_PAYING_EXCEPTION=1,
  PIVOT_SELECT=1 · **EST_FEED=0 (dark)** · **MACRO_PROBE_TICKERS empty (dark)** ·
  W12_PRICE_SHAPE=0 (B8-gated). Halt=10.
- **P2 verdict** (CANON, due 2026-08-10T14:13Z, credits>drag): day-2 annotations = halt
  15:54:54Z, deploy 17:31:15Z, rearm 17:36:23Z, dark deploy ~18:20Z. Window credits
  $0.00 at last read (14:14:00Z); recorder d_cash basis in memory.

## 2. THE 08-06 DISCOVERIES (read the game audit doc for full detail)
- **Estimates feed**: GET /v1/incentives/users/{uid}/estimates = the chip's source,
  bot-key readable, 10000cc=$1; 5-min recorder DEPLOYED (kalshi-estimates-recorder.timer,
  estimates-YYYYMM.jsonl + kalshi_program_map.json). Backend recomputes ~hourly.
- **Full LIP formula** (venue doc updated 08-05): 1s random snapshots; per-side reference
  = depth walk to Target/5; side qualifies ONLY at Target Size (1000ct on 3,755/3,802
  programs); either side short → snapshot excluded for EVERYONE; DF 0.5/tick; per-side
  share normalization; $1 floor per market-PROGRAM; payment at program conclusion
  (0b: 9/57 credits BEFORE close, 0 late; SENATEADJOURN watertight).
- **Competition map**: venue rates all 5 of our active series HIGH; 52 LOW series exist;
  big farmers rest deep cheap ladders, not at-touch size.
- **Pole position**: median entry latency 34.2h (n=215 ever-quoted active-program mkts);
  known series ≈0h; new series blocked by receipts-gating + formula-invisible 5ct probes.

## 3. QUEUE AFTER THE REVIEW (operator-ruled sequence)
1. **APRPOTUS est→credit checkpoint**: program ended? (end 2026-08-07T15:00Z); credit
   expected in the following ~05-07Z batch ≈ its estimate row (was ≥$1.63 at 08-06
   03:25Z). This verdict gates D-A arming ("verify the tool has value" — operator).
2. **Arm D-A + D-C on operator naming**: env edit + restart. D-C needs 2 tickers (fresh
   LOW-competition thin-book census at arm time) + coherent MACRO_PROBE_USD/HELD_MAX_USD
   pair (defaults 60/20 → $18/side effective = deliberately unaffordable until ruled).
3. **D-D symbiote build** after D-C's first estimates-feed response (scan done: 25/83
   census markets one-sided-≥target incl. our TRUMPEND/TOPMODEL strikes — those pay
   NOBODY today).
4. **D-E fills-vs-distance study** (study-then-report, ruled).
5. Knob-tuning options from post-deploy data (D3_KEEP_S, WIND_DOWN_FRAC, floor guards).
6. **B8 at window end (~Aug 9-10)**: net-EV rebuild on clean data, margin re-rule,
   NETEV_MODEL_HAIRCUT re-fit jointly with W12 — folding estimates-feed calibration.
7. Daily: P2 read, w16/w17 report logs (14:00Z timer), estimates-recorder data reads
   (cadence/monotonicity/est→credit), FIX-H credits watch (~08-09/10), counters.

## 4. NEXT-SESSION PROMPT (copy-paste)
---
KALSHI MAKER LANE — new session. Kalshi venue ONLY. Real money. BOT LIVE (verdict due
2026-08-10T14:13Z, credits>drag, CANON). Branch `claude/maker-kalshi-live`; worktree via
`git worktree list` (kalshi-wt under Temp scratchpad); verify `git branch --show-current`
before any repo write; never touch main checkout or master. STOP file = halt; only the
operator lifts. STEP ZERO — read in order: (1) memory `project_kalshi_estimates_feed.md`
(newest at top) + `project_kalshi_halt_0805.md`, (2)
`docs/maker_handoffs/KALSHI_HANDOFF_2026-08-06_EOD.md`, (3)
`docs/maker_handoffs/KALSHI_REWARD_GAME_AUDIT_2026-08-06.md`. Verify live fresh (plans
row, journal, daily_dd vs halt=10, deployed quoter md5 `5c7aed6f` vs HEAD `7e43554`,
suite 1233/2 exit 0). THEN the FIRST JOB: the operator-ruled 1a+ adversarial review in
handoff §0 — run it at full fan-out BEFORE any other work; deliver ONE decision batch.
Then §3 queue in order (APRPOTUS checkpoint first — it gates D-A arming). All 13 hook
rules + THE NORM + CLASS-NOT-INSTANCE bind. Name work items yourself; bring the operator
only genuine decisions with options and a stated default.
---
