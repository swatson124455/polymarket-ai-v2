# WeatherBot — CANONICAL STATUS (always current)

> **This file is the single always-current WeatherBot status pointer. Its filename never
> changes.** Session-stamped docs (e.g. `WEATHER_S222_STATUS.md`) are archival deep-dives —
> read them for detail, but THIS file is the source of truth for "what is live and what's open."
> Update the three sections below at the end of every WB session (same commit as the work).

**Last updated:** 2026-07-08 (S224 — renorm fix shipped to branch + fallacy-audit verify phase completed)
**Pinned branch:** `claude/new-whiteboard-session-9b23tq` (see `.claude/session-branch`)
**Resume check:** `bash scripts/wb_resume_check.sh` (self-deriving; replaces the hand-typed checklist)

---

## OPEN DECISIONS  ← always at the top, always the first thing a resume reads

1. **Deploy the S224 renorm fix (`caffc68`).** Fallacy-audit #1 is FIXED on the branch
   (deflate-only normalization × 4 engine sites; METAR renorm: evidence-gated, singleton-skip,
   0.98 conditioning cap; 12 defect-reproducing tests; WB suites 303/303) but **NOT deployed**.
   Operator call: cut a WB splinter release (and note it moves the S222 verification substrate —
   see #2). Run the full 1090+ suite on a full env first (cloud sandbox can't — missing
   other-bot deps). After deploy: `bash deploy/wb-record-deploy.sh <STAMP>` + commit.

2. **S222 post-fix verification (time-gated, ~now).** ≥50 resolved predictions on post-fix
   code (~1 week from 2026-07-06), then run `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` from a
   VPS-access session. NOTE: if `caffc68` deploys mid-window, the substrate changes again —
   decide whether to restart the clock at that deploy or read the verdict on the 07-06 code.
   Only after the verdict: retire containment gates per `WEATHER_S222_STATUS.md` §4-B.

3. **Triage the REMAINING pass-2 live-corrupting queue.** Verify phase COMPLETE (43
   re-verified 2026-07-08). Fixed/built on branch this session: **#1 renorm** (`caffc68`),
   **N1 bias sign-flip** (`04185e8`), **V42 circuit breakers** (`5baff62`), **V37 NDFD PoP**
   (`419df24`), **V34 synthetic sigma** (`410a89b`), **V26 exec-edge floor 0→0.04**
   (`f910cf6`), **V28 calibrated-edge gate built default-OFF** (`57d54bc`). **V1/ground-truth
   cluster PLANNED** (no code) → `docs/WB_GROUNDTRUTH_CLUSTER_PLAN_S224.md` — 4 workstreams
   (WU-primacy fix, source-column migration, contamination cutoff, break the calibrator
   feedback loop) with 4 operator decision points; needs MB signoff (shared-module + migration).
   STILL OPEN: that cluster's implementation, V34 follow-ups (synthetic marker / RNG
   determinism), the deeper V26 (orders at midpoint not executable price), enabling the V28
   gate. Then: deploy + S222 verdict.

---

## WHAT IS LIVE NOW

- **Deployed:** WeatherBot on its splinter (`/opt/polymarket-ai-v2-weather` →
  `/opt/pa2-weather-releases/<stamp>`, `polymarket-weather.service`). Paper mode, treated
  as production. Carries the six S223 root-cause fixes (dead tag, DB-semaphore leak,
  watchdog startup-grace, YES-bias exec-edge, mid-life-exit config, exits-must-SELL).
- **Health (per last verification):** service `active`, `NRestarts=0`, funnel restored by
  the tag fix; all S222 safety gates left **ON as containment**. Quality is
  "accurate-but-leaking-edge" — communicate via **calibration** (Brier/PIT/reliability),
  **never P&L** (CLAUDE.md Forbidden Pattern #11).
- **Deploy parity from a keyless (cloud) session:** compare the branch you're on to
  `deploy/LAST_DEPLOY.json` (`bash scripts/wb_resume_check.sh` does this). Live-VPS health
  still needs the deploy key — see the ssh one-liner in `scripts/wb_resume_check.sh`.

---

## POINTERS (archival detail — do not treat as "current" over this file)

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
