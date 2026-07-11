# WeatherBot — CANONICAL STATUS (always current)

> **This file is the single always-current WeatherBot status pointer. Its filename never
> changes.** Session-stamped docs (e.g. `WEATHER_S222_STATUS.md`) are archival deep-dives —
> read them for detail, but THIS file is the source of truth for "what is live and what's open."
> Update the three sections below at the end of every WB session (same commit as the work).

**Last updated:** 2026-07-11 (S228 — latency work built on-branch, NOT deployed: priority-wake
(flag-gated OFF), tunable model-run poll cadence (default unchanged), release-cut data/-skeleton
fix folded into `deploy/wb-release-cut.sh`. Everything inert-by-default — activation is an
operator env flip AFTER the S222 verdict. Prior: S227 calibrator fix LIVE, release
`20260711_002634` @ `6770883`, effective restart 00:47:00Z. Handoff: `docs/WEATHER_S227_STATUS.md`)
**Pinned branch:** `claude/new-whiteboard-session-9b23tq` (see `.claude/session-branch`)
**Resume check:** `bash scripts/wb_resume_check.sh` (self-deriving; replaces the hand-typed checklist)

---

## OPEN DECISIONS  ← always at the top, always the first thing a resume reads

1. **WATCH the calibrator re-learn — NOW ACTUALLY RUNNING (S227 fix deployed 2026-07-11
   00:47:00Z).** Backstory: the S224 WS-3 cutoff was bound as a **str** into
   `CAST(:gt_cutoff AS timestamptz)` → asyncpg DataError → since 07-08 EVERY calibrator fit
   crashed (1,109 warning-level failures) and EVERY EMOS/bias/tail reload crashed (debug —
   silent; 0 successful reloads journal-wide). The observed "reset toward identity" was the
   crash, not the designed re-learn. Fixed in `92740f3` (datetime bind; reload swallow
   elevated debug→warning), deployed in `20260711_002634`, proof-of-life verified 00:48:
   `weatherbot_calibration_reloaded` (41 stations / 571 rows, EMOS-ready: EDDM+LIML),
   `weatherbot_confidence_cal_insufficient_data n=0 need=200` (fit path executing; re-learn
   accumulates from zero). **The re-learn clock starts 2026-07-11, not 07-08.** The 53
   leak-era-entry contamination (ages out ~2026-08-07) is unchanged. Watch:
   `journalctl -u polymarket-weather | grep -E "calibration_reloaded|calibration_reload_failed|cal_fit|insufficient_data|holdout_valid"`
   — reload_failed / cal_fit_failed must STAY 0 (both are warning-level now).

2. **S222 post-fix verification — clock RESTARTED at 2026-07-11 00:47:00Z (the S227 fix
   restart); ETA to the ≥50 gate ~07-13/07-14.** The entire 07-08→07-11 window is DISCARDED
   for verification: it ran without EMOS/bias/tail calibration and without a fitted
   confidence calibrator (see #1), while the 07-02 baseline had them working — not
   comparable. Rows 07-11 00:26→00:47 are also old-code output (crash-loop + rollback
   interlude; stamp≠restart, third occurrence). `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` is
   fully re-pointed: cutoff `--since 20260711_004700`, S227-marker precondition, calibration-
   alive check (reload_failed/fit_failed must be 0), leak-regression check, PSW-label check.
   Gate count query:
   `SELECT count(DISTINCT market_id) FILTER (WHERE resolution IS NOT NULL) FROM prediction_log
    WHERE bot_name='WeatherBot' AND prediction_time > '2026-07-11 00:47:00';`
   When ≥50: run the prompt from a VPS session. Only after a PASS: retire containment gates
   per `WEATHER_S222_STATUS.md` §4-B (order: A1/A3 → dampeners → caps; C0 Kelly stays
   deferred until the calibrator re-learn verdict regardless).

3. **Deferred switches (do after the calibrator re-learns + S222 passes):** enable the V28
   calibrated-edge gate (`WEATHER_CALIBRATED_EDGE_GATE_ENABLED=true`); V34 follow-ups
   (synthetic marker / RNG determinism); deeper V26 (orders submitted at midpoint, not
   executable price); optional retro-purge of flipped `bootstrap_gfs` rows (N1 changelog);
   optional WU-only training filter on `actual_source` once the column has populated.

3a. **S228 latency package — ON BRANCH, inert-by-default, activate AFTER the S222 verdict.**
   Code ships at the next release cut but changes nothing until env-flipped (protects window
   comparability; an emergency hotfix cut mid-window stays safe). Activation block (Tier-1/2,
   add to the WB service env + restart):
   `WEATHER_PRIORITY_WAKE_ENABLED=true` (scan loop wakes on model-run/METAR priority events —
   the queue events previously sat up to a full 300–600s interval; min quiet period
   `WEATHER_PRIORITY_WAKE_MIN_SLEEP_S=20`); `WEATHER_MODEL_RUN_POLL_INTERVAL_S=120` (new-run
   detection, default 300); plus existing knobs to consider at the same time:
   `SCAN_INTERVAL_WEATHER=120–180` (default 300), `WEATHER_MAX_SCAN_INTERVAL=300` (caps no-edge
   backoff, default 600), `WEATHER_PSW_SCAN_DIVISOR=1` (PSW every scan, default 2),
   `WEATHER_FORECAST_CACHE_TTL=900` (default 1800 — the one knob that raises API volume; watch
   `api_calls` in `weatherbot_scan_done` + 429 events after). Verify wake-ups via
   `grep weatherbot_priority_wake` (logs `woke_early_by_s` + `event_source`). Rollback: remove
   the env lines + restart. NOT-DONE by design: HRRR-window cache invalidation — the forecast
   mix is GFS/ECMWF-IFS/ECMWF-AIFS only (`forecast_client.py:408-411`), refetch would return
   identical data.

3b. **V23 — FULLY FIXED AT ROOT (on-branch NOT deployed): `95c732c` (P(YES) predicted_prob) +
   `14006b0` (durable `prob_frame` label, migration 080, BOTH graders guard unlabelled PSW rows)
   + market_price→YES-frame (realized_edge now correct on labelled rows).** Historical PSW rows
   are now MACHINE-LABELLED ambiguous (`prob_frame IS NULL`) — migration 080 retro-NULLs their
   was_correct/realized_edge and both graders (vendored + top-level database.py; the main
   14-bot service also grades the shared table) permanently refuse to grade unlabelled PSW
   rows, deploy-order-safe via a runtime column check (079 pattern). Requires the next WB
   release cut (auto-applies 080). Post-deploy: `grep 'prob_frame missing'` should go quiet.
   ORIGINAL PLAN + optional manual SQL below are SUPERSEDED by migration 080 (kept for context): Writers normalized: every opp now carries
   `model_prob_yes` (P(YES)) and all four `_log_weather_prediction` opp call sites pass
   `_yes_frame_prob(opp)`; trading fields untouched. The grader's YES-frame assumption is now
   true for all rows written after the next deploy. UNFIXABLE HISTORY: pre-fix PSW NO rows
   stored chosen-side predicted_prob and side was never persisted, so they cannot be re-framed
   row-by-row. Optional operator remediation (removes the poison from `was_correct` consumers,
   which filter on IS NOT NULL; sacrifices historical PSW YES rows too):
   `UPDATE prediction_log SET was_correct = NULL WHERE bot_name = 'WeatherBot' AND model_name
   IN ('weather_precipitation','weather_snowfall','weather_wind') AND prediction_time <
   '<next-deploy-time>';` — predicted_prob-based analysis (calibration_check) still sees those
   rows; treat pre-deploy PSW rows as contaminated for calibration purposes (temperature rows,
   the large majority, are unaffected). ALSO NOTED (pre-existing, all market types, NOT changed):
   `market_price` is chosen-side, so stored `edge`/`realized_edge` mix frames on NO rows —
   measurement-only columns; fix would need side persisted; separate decision.
   ORIGINAL FINDING (for context):
   The backfill (`backfill_prediction_log_resolution`, vendored database.py ~3934) computes
   `was_correct = (predicted_prob >= 0.5) == (resolution = 'YES')` — a YES-frame read. PSW
   (precip/snow/wind) NO-side call sites log CHOSEN-SIDE predicted_prob (e.g. weather_bot.py
   ~2575: `1 − model_prob`), so winning NO calls with P(NO) ≥ 0.5 are stored as MISSES.
   `realized_edge` shares the assumption. Temperature rows are YES-frame → unaffected.
   Consumers of the poisoned field: calibration_tracker, phase-tracker Brier, venn_abers,
   prediction_accuracy_check, gate_score_expectancy, cooldown_analysis, and the WB
   consecutive-loss compress feed. Fix options (pick one, defect-test-first): (a) side-aware
   backfill (requires persisting prediction side; WB rows leave trade_side NULL), or (b)
   normalize PSW call sites to YES-frame + cutoff for mixed-frame history. Do NOT fix casually.

4. **S226 — manufactured-certainty leak: CLOSED (root cause CONFIRMED via journal — timestamp misattribution;
   the S224 renorm fix already killed it).** Full DB pull (S226, 2026-07-10): **58** rows at
   `predicted_prob = exactly 1.0` since 07-08 (30 resolved, 3 correct — 10% hit-rate at claimed
   certainty), ALL `weather_temperature`, last one at **07-08 17:59:38**. S225 called 5 of these
   "post-deploy" by comparing against the tarball STAMP (`20260708_151330` = 15:13:30Z) — but
   `deploy/LAST_DEPLOY.json` records the S224 deploy completing at **19:18:44Z**; the service
   restart is at the END of the cut, so all 58 rows (incl. the "5 post-deploy") came from the
   **pre-S224 code**, whose unconditional inflate-renorm (singleton `p/p = 1.0`) produces the
   exact observed signature: prob 1.0, raw conf `min(0.95, 1.0)=0.95`, ×0.85 YES dampener =
   **0.8075**, `edge = 1 − price`. The fixed code (caffc68: per-bucket [0.001,0.999] clamps +
   deflate-only renorm + METAR ≤0.98) was re-traced end-to-end in S226 and statically cannot
   emit ≥0.9995 on any temperature path. **Zero occurrences in ~9,656 prediction rows over the
   2 days since the real restart**; tripwire (live since release `20260710_165646`) also silent.
   VERIFIED CLOSED (operator journal pull, 2026-07-10): the box restarted at **18:08:41**
   and **19:18:38** (the S224 cut; deploy record 19:18:44Z) — the last leak row (17:59:38)
   predates BOTH restarts, and zero leak rows exist from any process after 18:08. Root cause
   confirmed: pre-S224 inflate-renorm; already fixed. Tripwire STAYS as a regression guard. ADDENDUM (bears on #1): 53 leak-era ENTRY events sit in the calibrator's rolling
   30-day fit window (avg raw conf 0.95, mostly resolved NO) → the re-learn is MILDLY
   contaminated until they age out (~2026-08-07). Self-clears; do NOT filter them out by hand.
   Investigation trail: S225 (tripwire) → S226 (root cause).

---

## WHAT IS LIVE NOW

- **Deployed:** WeatherBot on its splinter, release **`20260711_002634`** (cut from `6770883`;
  rollback target `20260710_204822`). Paper mode, treated as production. Carries everything
  S223→S226 PLUS the **S227 calibrator fix** (`92740f3` — gt_cutoff datetime bind; reload
  swallow warning-level). Verified live 00:48Z: calibration reloading again, fit path
  executing, failure counters 0. Migrations 079+080 remain applied (no new migrations in S227).
- **⚠ NEW RELEASE-CUT RECIPE (learned the hard way 07-11):** this release was cut with
  `git archive` (clean, 39M, tracked-files-only) instead of the old tar-the-working-tree
  flow (~4G with ~250 untracked files swept in, incl. `wallet.txt` — all 11 release-dir
  copies shredded 2026-07-11 01:0xZ; local original + wallet-rotation question with operator). The
  service runs under `ProtectSystem=strict` (whitelist: `/opt/pa2-shared/data`,
  `/opt/pa2-shared/saved_models`, `/var/log/polymarket`), so the release tree is READ-ONLY
  at runtime and the engine cannot mkdir — **a clean tarball MUST pre-create the `data/`
  skeleton** (`data/backups`, `data/wb_snapshots`, etc.; mirror the previous release:
  `find <old>/data -type d -exec mkdir -p <new>/{} \;` + chown) or the service crash-loops
  on `Read-only file system: 'data/backups'`. First cut attempt did exactly that (43
  restarts, 00:26→00:42), was rolled back, repaired, re-flipped at ~00:46.
- **Health (at deploy 07-08):** `service: active`, clean restart, S224 markers=24 on the box.
  All S222 safety gates still **ON as containment**. The calibrator is mid-reset toward
  identity (see OPEN DECISIONS #1) — expected. Quality via **calibration** (Brier/PIT/
  reliability), **never P&L** (CLAUDE.md Forbidden Pattern #11).
- **Deploy parity from a keyless (cloud) session:** compare the branch you're on to
  `deploy/LAST_DEPLOY.json` (`bash scripts/wb_resume_check.sh` does this). Live-VPS health
  still needs the deploy key — see the ssh one-liner in `scripts/wb_resume_check.sh`.

---

## POINTERS (archival detail — do not treat as "current" over this file)

- `docs/WEATHER_S227_STATUS.md` — **the S227 session handoff (latest)**: the gt_cutoff
  str-bind crash (calibrator/EMOS dead since 07-08), the fix + deploy saga (data/ skeleton,
  ProtectSystem=strict), the new git-archive release recipe, S222 clock restart.
- `docs/WEATHER_S226_STATUS.md` — the S226 session handoff: leak closure proof,
  V23 root fix + prob_frame label, the runtime-binding trap, deploy verification.
- `docs/WEATHER_S224_STATUS.md` — the S224 session handoff (this session): the 7 fixes +
  calibrator/ground-truth cluster, the deploy, the calibrator-reset caveat, pending steps.
- `WEATHER_S222_STATUS.md` — the S222/S223 session's full handoff (fixes, diagnosis,
  pending-work order, deploy mechanics, config gotchas, file map).
- `docs/WB_FALLACY_AUDIT_S223.md` — the fallacy audit; verify phase COMPLETE (18 pass-1 +
  43 pass-2 re-verified). See its "SECOND VERIFY PASS" table + "Live-corrupting queue" for
  what's still open. `docs/WB_FALLACY_AUDIT_S223_raw.json` — raw workflow dump (titles only).
- `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` — the time-gated verification to run in ~1 week.
- `docs/SESSION_HANDOFF_PROTOCOL.md` — how to write the next handoff.
- `docs/WB_HANDOFF_MANIFEST.json` — machine-readable state consumed by the resume check.

---

## CHANGELOG (newest first — one line per session-end update)

- **2026-07-11 (S228, on-branch NOT deployed):** latency package, inert-by-default (see OPEN
  DECISION 3a): `be7dd93` priority-wake — inter-scan sleep is now an overridable hook (vendored
  `base_bot.py`; base = plain sleep) and WeatherBot's override wakes on `_priority_queue` events
  with a min quiet period, re-queuing the event for the unchanged scan-top drain — flag
  `WEATHER_PRIORITY_WAKE_ENABLED` default OFF; `86c6bcb` `WEATHER_MODEL_RUN_POLL_INTERVAL_S`
  (default 300 = old hardcoded ModelRunMonitor cadence); `58488d7` `wb-release-cut.sh` now
  pre-creates the `data/` skeleton (S227 crash-loop recipe folded in — closes S227 pending #5).
  8 defect tests red→green; WB suites 362 passed (354 baseline, zero delta). HRRR-window cache
  invalidation investigated and rejected (forecast mix has no HRRR). Session ran on env branch
  `claude/weatherbot-s228-h6wq0y` (whiteboard branch fast-forwardable).
- **2026-07-11 (S227 DEPLOYED + VERIFIED):** release `20260711_002634` @ `6770883`, effective
  restart 00:47:00Z (record `82302b7`). First cut crash-looped 43× — clean `git archive`
  tarball lacked the `data/` skeleton the `ProtectSystem=strict` sandbox requires to pre-exist
  (release tree is read-only at runtime); rolled back to `_204822`, mirrored the data/ dir
  skeleton, re-flipped: `service: active`. Proof-of-life 00:48: first
  `weatherbot_calibration_reloaded` since 07-08 (41 stations/571 rows, EMOS-ready EDDM+LIML),
  `cal_fit` path executing (`insufficient_data n=0 need=200`), reload_failed/fit_failed = 0.
  Verification prompt re-pointed to `--since 20260711_004700`. Box hygiene DONE same night:
  all 11 swept-in `wallet.txt` release-dir copies shredded (code references grep-verified
  zero first; never git-tracked), stale /tmp tarball removed. STILL OPEN (operator): Pinnacle
  key rotation (URGENT: echoed into journal + chat), check the local `wallet.txt` original
  (172B, world-writable copies sat on box for weeks — consider wallet rotation; move the
  file out of the repo dir), prune old releases only after the S222 verdict.
- **2026-07-11 (S227 FIX, needs deploy):** `92740f3` — gt_cutoff bound as str into
  `CAST(:gt_cutoff AS timestamptz)` = asyncpg DataError on all 3 WS-3 cutoff sites: every
  confidence-calibrator fit crashed (warning) and every EMOS/bias/tail calibration reload
  crashed (debug — silent) since the 07-08 deploy. The "reset toward identity" was the crash.
  Fix: `_gt_cutoff_datetime()` tz-aware bind at both call sites; reload swallow elevated
  debug→warning (S177 precedent). 3 defect tests (bind-param capture, red→green); WB suites
  361 passed. Found during 07-11 stress-test error triage (stress test itself: box PASSED,
  services survived combined cpu+mem+io, window 00:01:38→00:08:30Z recorded). Consequence:
  S222 verification clock restarts at this fix's deploy; OPEN DECISIONS #1/#2 rewritten.
- **2026-07-10 (S227):** S222 verification gate OPEN — operator count 50/50 distinct resolved
  markets post-07-08. `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` trued up: verdict cutoff moved
  from the tarball stamp (15:13:30) to the real restart (19:18:38, avoids pre-fix leak rows in
  the [0.9,1.0) bin), `--dedup-markets`, window-integrity checks (leak regression / PSW frame
  ambiguity / 07-10 log outage). Docs only; no bot behavior changed; awaiting VPS run.
- **2026-07-10 (S226 HOTFIX, needs deploy):** `535ec86` — prediction logging went SILENT at
  the 20:12 deploy: WB's runtime db is the TOP-LEVEL Database (main.py -> BaseEngine), which
  lacked the new prob_frame kwarg -> TypeError on every log call, swallowed at debug level
  (invisible at journal info). Zero prediction_log rows post-restart; trading unaffected.
  Fixed: top-level Database/PredictionLog mirror the vendored prob_frame addition; the
  swallowing catch elevated debug->warning (S177 precedent). LESSON (blast-radius): the
  vendored tree owns the weather ENGINE imports, but the DB object binds TOP-LEVEL — DB-layer
  changes must land in BOTH database.py files. Deploy: next release cut; then expect
  prob_frame='yes' rows within ~2 scans of an edge.
- **2026-07-10 (S226 V23 completion):** `14006b0` durable frame label — migration 080 adds
  `prediction_log.prob_frame` ('yes' = P(YES)), retro-NULLs historical WB PSW grades, and BOTH
  graders (vendored + top-level, weather-model_name-scoped) refuse unlabelled PSW rows; writers
  stamp 'yes'. Sibling fix: market_price→YES price at all WB log sites (realized_edge correct on
  labelled rows; edge column coherent). Full suite 3862 passed, zero-delta failure diff vs
  stashed baseline. NOT deployed — next WB release cut auto-applies 080.
- **2026-07-10 (S226 V23 root fix, on-branch NOT deployed):** `95c732c` — prediction_log
  predicted_prob normalized to P(YES) on every WB row (PSW NO opps were logging chosen-side
  prob → winning NO calls graded as misses). Writers fixed via explicit `model_prob_yes` +
  `_yes_frame_prob` at all four log sites; trading fields untouched; grader unchanged (its
  YES-frame assumption is now valid). 4 defect tests; WB suite 316 passed. Historical PSW
  rows frame-ambiguous — optional operator SQL in OPEN DECISION 3b.
- **2026-07-10 (S226 batch, on-branch NOT deployed):** five audit follow-ups landed as five
  commits, 319/319 WB tests (302 baseline + 17 new defect tests): V37 null-NDFD-PoP = 0% on
  matching day (dry-day signal restored); V34 synthetic-ensemble marker (`synthetic_ensemble`
  field + `synthetic` in models_used) + deterministic member RNG (sha256 seed, was PYTHONHASHSEED-
  unstable); V28 follow-up top-N bucket selection ranks by calibrated edge ONLY when the V28 gate
  flag is ON (proven inert at default OFF); telemetry truth V16/V20/V21 (S-T docstring/log field
  `proposed_usd`+`applied`, dampener reciprocals documented, `forecast_delta` re-labeled
  fetch-over-fetch with `delta_basis` field); stray esports probe removed. V23 analyzed and
  deliberately SKIPPED as behavioral → new OPEN DECISION 3b. Deeper V26 deferred (execution-path,
  operator sign-off). Reaches the box at the next WB release cut.
- **2026-07-10 (S226):** (a) S225 diagnostics DEPLOYED — release `20260710_165646` cut from
  `5343d56` (tripwire + 2 measurement fixes live; `service: active`; recorded `16646f5`).
  (b) Leak ROOT-CAUSED: the "5 post-deploy" 1.0-rows predate the ACTUAL 07-08 service restart
  (~19:15Z per deploy record 19:18:44Z) — tarball stamp 15:13:30 was misread as restart time.
  All 58 rows are pre-S224-code output (inflate-renorm singleton p/p=1.0); fixed code re-traced
  statically airtight; 0 occurrences in ~9,656 rows / 2 days since; tripwire silent. Kept as
  regression guard. (c) Calibrator fit-window note: 53 leak-era entries contaminate the re-learn
  until ~08-07 (self-clears). (d) Post-07-08 distinct resolved markets: 42/50 toward the S222 gate.
- **2026-07-09 (S225 diagnostics):** two measurement fixes + one tripwire, all on-branch (NOT
  deployed). (1) `bot_pnl.py` WB conf-bin query f-prefixed — literal `{mode_exec_clause_r}` was
  crashing the report (`fcca023`-adjacent). (2) `calibration_check.py` gained `--dedup-markets`:
  the "82 resolved predictions" were really **9 distinct markets** (one logged 42×) — per-log
  counting inflated Brier/PIT and manufactured an apparent "confidence inversion" that is NOT
  real. (3) Manufactured-certainty tripwire (`_is_impossible_certainty` +
  `weatherbot_impossible_certainty` warning) added to catch the `predicted_prob=1.0` leak
  (OPEN DECISION #4) — deploy then grep the `caller=` field. No live-trading behavior changed.
- **2026-07-08 (S224 DEPLOYED):** full S224 batch cut to release `20260708_151330` (rollback
  `20260708_140013`); `service: active`, S224 markers=24; **migration 079 applied by the
  release-cut script itself** (auto-migration folded in). Calibrator now mid-reset toward
  identity (ground-truth cutoff + raw-X training live) — re-learning from clean data over the
  coming days. Deploy recorded `a9cfcfa`. (That record commit also inadvertently swept in a
  staged esports probe `scripts/esports_market_shape_probe.py` — harmless, weather never runs it.)
- **2026-07-08 (S224 cluster):** ground-truth/calibrator cluster IMPLEMENTED (`8c778d3`) —
  WS-2 WU-primacy (extreme WU-vs-OM disagreement now abstains, never writes OM as truth);
  WS-1 provenance column `weather_calibration.actual_source` (**migration 079 — RUN ON VPS**,
  code falls back + warns until applied); WS-3 hard training cutoff 2026-07-01 (conf-cal ×2 +
  EMOS SQLs); WS-4 calibrator now trains on RAW pre-calibration confidence (self-training loop
  broken; **calibrator resets toward identity and re-learns from clean data** — expected, safe
  direction). V28 gate can be enabled once the re-learned calibrator shows sane OOS Brier.
  NOT deployed.

- **2026-07-08 (S224 V28):** calibrated-edge admission gate BUILT (`57d54bc`) — symmetric
  `_calibrated_edge_admits` requires the calibrated edge (P(side)_cal − price) to clear the
  same min_edge (0.08/0.12) the raw edge did, giving the NO funnel the calibrated admission
  input it lacked. **Default OFF** (`WEATHER_CALIBRATED_EDGE_GATE_ENABLED`) — the gate is only
  as good as the calibrator, which has known contamination (V1/V4/V6); enable after those +
  S222 verdict. NOT deployed.
- **2026-07-08 (S224 V26):** executable-edge floor raised 0.0→0.04 (`f910cf6`, operator-
  approved) — admitted trades must now keep ≥4pts of edge at the price actually paid, not just
  at the midpoint. Tier-2 gating change; blocks thin-positive fills. Rollback:
  `WEATHER_MIN_EXECUTABLE_EDGE=0.0`. Deeper V26 (orders still submitted at midpoint, not the
  executable price) remains open. NOT deployed.

- **2026-07-08 (S224 V34):** synthetic-ensemble spread now lead-time-scaled (`410a89b`) —
  the point-forecast-only fallback used a fixed 2°F/1.1°C day-1 error at every lead (a 120h
  point high got a 2°F cloud → overconfident tail edges); now uses the NBM σ schedule
  (1.5/2.5/3.5/5.0°F by lead). Follow-ups open: synthetic marker, RNG determinism. NOT deployed.

- **2026-07-08 (S224 V37):** NDFD wrong-day PoP fallback FIXED (`419df24`) — when no NDFD
  period matches the target day, use None (pure ensemble) instead of substituting TODAY's
  PoP; the old fallback inflated p_rain on dry target days (NWS nulls ~0% periods, which
  get_ndfd_pop drops). Follow-up left open: treat null-PoP as 0% to recover the dry-day
  signal (changes shared get_ndfd_pop semantics). NOT deployed.
- **2026-07-08 (S224 V42):** intra-day-blind circuit breakers FIXED (`5baff62`) —
  `_handle_daily_boundary` now refreshes `_daily_pnl` from the DB every scan (was once/day
  behind a same-day early-return), so the daily loss limit + 20% drawdown halt can fire
  intra-day. Reset stays once/day. Behavior change: breakers now actually bind on real
  intra-day losses (intended; default limit is high so practical change is bounded). NOT deployed.
- **2026-07-08 (S224 N1):** cold-station bias SIGN-FLIP fixed (`04185e8`) — bootstrap
  now writes `bias = actual − forecast` matching the actuals updater + consumer convention;
  was doubling forecast error for cold stations on the simple-bias fallback. NOT deployed.
  ⚠ Existing `bootstrap_gfs` rows in the VPS DB retain the flipped sign until they age out
  of the 90-day window — operator may purge them to apply the fix retroactively (see commit).
- **2026-07-08 (S224):** fallacy-audit #1 FIXED on branch (`caffc68` — deflate-only renorm
  ×4 engine sites + METAR renorm guard; 12 defect tests; 303/303 WB tests; NOT deployed).
  Verify phase COMPLETED for the 43 credit-limit-orphaned findings (raw texts were lost —
  reconstructed from titles and adversarially re-verified; ~28 confirmed + 2 new adjacent
  findings incl. cold-station bias sign-flip N1 and intra-day-blind circuit breakers V42).
  Register: `docs/WB_FALLACY_AUDIT_S223.md` "SECOND VERIFY PASS".
- **2026-07-08 (handoff hardening):** committed the resume-integrity harness
  (`scripts/wb_resume_check.sh` + `docs/WB_HANDOFF_MANIFEST.json`), the SessionStart
  branch-pin hook (`.claude/`), this canonical status file, and the deploy-record mechanism
  (`deploy/wb-record-deploy.sh` + `deploy/LAST_DEPLOY.json`); documented all four in
  `docs/SESSION_HANDOFF_PROTOCOL.md`. No bot behavior changed.
- **2026-07-06 (S223):** six root-cause fixes deployed; fallacy audit started (18/62
  verified, credit-limited mid-verify). See `WEATHER_S222_STATUS.md` S223 addendum.
