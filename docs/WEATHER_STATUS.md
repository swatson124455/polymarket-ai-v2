# WeatherBot — CANONICAL STATUS (always current)

> **This file is the single always-current WeatherBot status pointer. Its filename never
> changes.** Session-stamped docs (e.g. `WEATHER_S222_STATUS.md`) are archival deep-dives —
> read them for detail, but THIS file is the source of truth for "what is live and what's open."
> Update the three sections below at the end of every WB session (same commit as the work).

**Last updated:** 2026-07-09 (S225 diagnostics on-branch, NOT deployed — bot_pnl f-string fix, calibration_check `--dedup-markets`, manufactured-certainty tripwire; live release still `20260708_151330`, migration 079)
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

4. **S225 — manufactured-certainty leak (CONFIRMED, source not yet localized).** 5 post-deploy
   `weather_temperature` rows logged `predicted_prob = exactly 1.0` (conf 0.8075, `edge = 1 −
   price` ≈ 0.7–0.83 → confident YES entries; 2/2 checked resolved NO). **This is a raw-model
   leak, NOT the calibrator reset** (`predicted_prob` is uncalibrated). Every probability-engine
   path is capped ≤0.999 (verified in the *deployed* engine: deflate-only guard + 4× `min(0.999)`;
   METAR ceiling 0.98, singleton-skip deployed) — so the code as written CANNOT produce 1.0, yet
   it did. Source is not statically reproducible. **A tripwire is committed** (`_is_impossible_
   certainty` + `weatherbot_impossible_certainty` warning in `_log_weather_prediction`) that logs
   the leaking **caller site** when `model_prob ≥ 0.9995`. **NOT deployed** — needs a WB release
   cut. Next VPS session after deploy: `journalctl -u polymarket-weather | grep impossible_certainty`
   → the `caller=` field names the exact leaking path; then fix at that site (test-first). Rare +
   containment gates ON, so not urgent. Investigation trail: this session (S225).

---

## WHAT IS LIVE NOW

- **Deployed:** WeatherBot on its splinter, release **`20260708_151330`** (rollback target
  `20260708_140013`). Paper mode, treated as production. Carries the six S223 fixes PLUS the
  full **S224 batch**: renorm deflate-only, N1 bias sign-flip, V42 circuit breakers, V37 NDFD
  PoP, V34 synthetic sigma, V26 exec-edge floor 0.04, V28 gate (built, OFF), and the
  ground-truth/calibrator cluster (WU-primacy, provenance, 2026-07-01 cutoff, raw-X training).
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
