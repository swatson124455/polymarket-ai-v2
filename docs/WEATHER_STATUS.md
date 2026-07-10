# WeatherBot — CANONICAL STATUS (always current)

> **This file is the single always-current WeatherBot status pointer. Its filename never
> changes.** Session-stamped docs (e.g. `WEATHER_S222_STATUS.md`) are archival deep-dives —
> read them for detail, but THIS file is the source of truth for "what is live and what's open."
> Update the three sections below at the end of every WB session (same commit as the work).

**Last updated:** 2026-07-10 (S226 — leak CLOSED; S225 diagnostics deployed in `20260710_165646`; five audit follow-ups landed on-branch NOT deployed — see changelog; V23 promoted to OPEN DECISION 3b)
**Pinned branch:** `claude/new-whiteboard-session-9b23tq` (see `.claude/session-branch`)
**Resume check:** `bash scripts/wb_resume_check.sh` (self-deriving; replaces the hand-typed checklist)

---

## OPEN DECISIONS  ← always at the top, always the first thing a resume reads

1. **WATCH the calibrator reset (S224 just deployed, release `20260708_151330`).** The
   ground-truth cluster is LIVE, so the calibrator now excludes pre-2026-07-01 + self-looped
   data → it will fall to **identity** and re-learn from clean, raw-X data over the next days.
   Expected, safe. Verify it's happening: `journalctl -u polymarket-weather | grep -E
   "calibrator|actual_source|abstain|holdout_valid"`. Watch for the OOS Brier trending sane as
   clean resolutions accumulate — that verdict gates enabling the V28 gate (#3).

2. **S222 post-fix verification (time-gated).** The substrate changed AGAIN at this deploy
   (07-08), so the ≥50-resolution clock effectively restarts here for the fully-fixed code.
   Run `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` from a VPS-access session once ≥50 post-07-08
   resolutions exist. Only after a PASS: retire containment gates per `WEATHER_S222_STATUS.md` §4-B.

3. **Deferred switches (do after the calibrator re-learns + S222 passes):** enable the V28
   calibrated-edge gate (`WEATHER_CALIBRATED_EDGE_GATE_ENABLED=true`); V34 follow-ups
   (synthetic marker / RNG determinism); deeper V26 (orders submitted at midpoint, not
   executable price); optional retro-purge of flipped `bootstrap_gfs` rows (N1 changelog);
   optional WU-only training filter on `actual_source` once the column has populated.

3b. **V23 — `was_correct` YES-frame bug (S226 analysis, needs its own reviewed commit).**
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

- **Deployed:** WeatherBot on its splinter, release **`20260710_165646`** (cut from `5343d56`;
  rollback target `20260708_151330`). Paper mode, treated as production. Carries the six S223
  fixes, the full **S224 batch** (renorm deflate-only, N1 bias sign-flip, V42 circuit breakers,
  V37 NDFD PoP, V34 synthetic sigma, V26 exec-edge floor 0.04, V28 gate built/OFF, ground-truth/
  calibrator cluster), PLUS the **S225 diagnostics** (manufactured-certainty tripwire live;
  bot_pnl f-string fix; calibration_check `--dedup-markets`).
  **Migration 079 applied** (`actual_source` column live).
- **Health (at deploy 07-08):** `service: active`, clean restart, S224 markers=24 on the box.
  All S222 safety gates still **ON as containment**. The calibrator is mid-reset toward
  identity (see OPEN DECISIONS #1) — expected. Quality via **calibration** (Brier/PIT/
  reliability), **never P&L** (CLAUDE.md Forbidden Pattern #11).
- **Deploy parity from a keyless (cloud) session:** compare the branch you're on to
  `deploy/LAST_DEPLOY.json` (`bash scripts/wb_resume_check.sh` does this). Live-VPS health
  still needs the deploy key — see the ssh one-liner in `scripts/wb_resume_check.sh`.

---

## POINTERS (archival detail — do not treat as "current" over this file)

- `docs/WEATHER_S224_STATUS.md` — **the S224 session handoff** (this session): the 7 fixes +
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
