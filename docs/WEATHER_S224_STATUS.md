# HANDOFF — WeatherBot S224 (fallacy-audit fixes + calibrator/ground-truth cluster, DEPLOYED)

**Session:** WeatherBot (WB) silo · **Branch:** `claude/new-whiteboard-session-9b23tq`
**HEAD at handoff:** `a9cfcfa` (+ this doc) · **Deployed release:** `20260708_151330`
**Date:** 2026-07-08 · **Mode:** PAPER (`SIMULATION_MODE=true`) — treated as live per CLAUDE.md
**Scope:** WeatherBot only. Ignore any esports/other-bot artifacts that appear in the tree.

> **Canonical always-current status is `docs/WEATHER_STATUS.md`.** This file is the archival
> deep-dive of the S224 session. Resume path: `bash scripts/wb_resume_check.sh` → read
> `WEATHER_STATUS.md` (OPEN DECISIONS) → this doc for detail.

---

## 0. TL;DR for the next session

The pass-2 fallacy audit is complete and **7 fixes + a full calibrator/ground-truth cluster are
LIVE** (release `20260708_151330`, migration 079 applied). The single most important thing to
understand: **the confidence calibrator is deliberately mid-reset toward identity** — the new
ground-truth cutoff (2026-07-01) + raw-X training exclude the contaminated and self-looped data,
so it will re-learn from clean resolutions over the coming days. This is EXPECTED and SAFE.
**Do NOT "fix" the calibrator falling to identity by loosening the cutoff** — that reintroduces
the contamination. Wait for it to re-learn, watch OOS Brier, then (and only then) enable the V28
gate and run the S222 verification. Communicate quality via calibration metrics, **never P&L**.

---

## 1. What is LIVE right now

- **Deployed:** release `20260708_151330` on the WB splinter (`/opt/polymarket-ai-v2-weather` →
  `/opt/pa2-weather-releases/20260708_151330`, `polymarket-weather.service`). Rollback target:
  `20260708_140013`.
- **Health at deploy:** `service: active`, clean restart, `grep -c S224 …/weather_bot.py` = 24.
- **Migration 079 applied** — `weather_calibration.actual_source` column live (provenance).
- **Carries:** the six S223 fixes + the full S224 batch (see §3).
- **Containment unchanged:** all S222 safety gates still ON (price dampeners, entry-price caps,
  flat-$100 sizing). The V28 calibrated-edge gate is BUILT but **default OFF**.
- **Calibrator state:** mid-reset toward identity (see TL;DR). Watch:
  `journalctl -u polymarket-weather | grep -E "calibrator|actual_source|abstain|holdout_valid"`

---

## 2. The diagnosis / why this work exists

A pass-1 adversarial fallacy audit (S223) confirmed 18 findings; 43 more were credit-limited
mid-verify. This session **re-verified all 43** (raw texts were lost — reconstructed from titles
against the code) and fixed every implementable live-corrupting one. Full register:
`docs/WB_FALLACY_AUDIT_S223.md` ("SECOND VERIFY PASS" table + distilled queue).

The load-bearing themes:
- **Manufactured certainty** — sum-to-1 renormalization over non-exhaustive bucket sets produced
  literal `model_prob=1.0` (the observed live signal).
- **Calibrator can't be trusted** — it trained on its own post-calibration output (feedback loop)
  and on pre-WU-fix data (~96% ERA5) with no way to tell clean rows from dirty. Calibration is the
  right mechanism (learn how wrong the confidence is, correct it) — but only on RAW confidence
  from the CURRENT model against clean market-resolution labels. Both conditions were violated.
- **Blind safety** — the daily loss limit + drawdown halt could never fire intra-day.
- **Wrong-source ground truth** — WU (the resolution source) was silently overwritten by Open-Meteo.

**Binding directive:** never quote P&L (CLAUDE.md Forbidden Pattern #11). Calibration only.

---

## 3. What was done (every fix has a defect-reproducing test; WB suites 327/327)

| Commit | Fix | One line |
|--------|-----|----------|
| `caffc68` | #1 renorm | Deflate-only normalization × 4 engine sites + METAR renorm guard (evidence-gated / singleton-skip / 0.98 cap). Killed the manufactured 1.0. |
| `04185e8` | N1 bias sign-flip | Bootstrap wrote `forecast−actual`; consumer needs `actual−forecast`. Was doubling cold-station forecast error. |
| `5baff62` | V42 circuit breakers | `_daily_pnl` refreshed from DB every scan (was once/day behind a same-day early-return) → loss limit + drawdown halt fire intra-day now. |
| `419df24` | V37 NDFD PoP | No matching NDFD period → pure ensemble (was substituting TODAY's PoP, inflating rain on dry days). |
| `410a89b` | V34 synthetic sigma | Point-forecast fallback spread now lead-time-scaled (NBM σ schedule) vs the old fixed 2°F at all leads. |
| `f910cf6` | V26 exec-edge floor | `WEATHER_MIN_EXECUTABLE_EDGE` default 0.0 → 0.04 (operator-approved). Admitted trades keep ≥4pts at the fill price. |
| `57d54bc` | V28 gate (OFF) | `_calibrated_edge_admits` — calibrated edge must clear min_edge. Symmetric; gives the NO funnel a calibrated admission input. **Default OFF.** |
| `8c778d3` | ground-truth cluster | WS-2 WU-primacy (abstain, never OM); WS-1 `actual_source` provenance (migration 079); WS-3 2026-07-01 training cutoff (conf-cal ×2 + EMOS); WS-4 train on RAW confidence (breaks the self-loop). |
| `8b2cbea` | deploy simplification | `wb-release-cut.sh` auto-applies migration 079 + reports S224 markers. |
| `a9cfcfa` | deploy record | release `20260708_151330` recorded to `deploy/LAST_DEPLOY.json`. |

Also this session (infrastructure, weather-scoped): the resume-integrity harness
(`scripts/wb_resume_check.sh` + `docs/WB_HANDOFF_MANIFEST.json`), the SessionStart branch-pin
hook (`.claude/`), the canonical `WEATHER_STATUS.md`, and the deploy-record mechanism. Operator
also directed removal of the MB-priority / shared-resource section from `CLAUDE.md`.

---

## 4. PENDING WORK — exact next steps

**Step A — WATCH the calibrator re-learn (days).** It is at/near identity now by design. Confirm
via the journal grep above. Watch OOS Brier trend as clean, raw-X resolutions accumulate. **Do
not touch the cutoff or the raw-X logic** — the reset is the intended behavior.

**Step B — S222 post-fix verification (time-gated).** The substrate changed at this deploy, so the
≥50-resolution clock effectively restarts at 2026-07-08. Once ≥50 post-07-08 resolutions exist,
run `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` from a VPS-access session (needs the box DB). On PASS,
retire containment gates in the order in `docs/WEATHER_S222_STATUS.md` §4-B.

**Step C — deferred switches (only after A re-learns + B passes):**
1. Enable the V28 gate: `export WEATHER_CALIBRATED_EDGE_GATE_ENABLED=true` (Tier-2 env, restart).
2. V34 follow-ups: synthetic-ensemble marker + deterministic RNG seed.
3. Deeper V26: submit orders at the executable price, not the midpoint (execution-path change).
4. Optional WU-only training filter on `actual_source` once the column has populated a while.
5. Optional retro-purge of flipped bootstrap rows: `DELETE FROM weather_calibration WHERE
   model_name='bootstrap_gfs';` (they re-bootstrap; applies N1 retroactively).

---

## 5. Gotchas / traps discovered (flag to operator, don't silently change)

- **Calibrator falling to identity is CORRECT** post-cutoff — not a regression. (Repeated because
  it's the #1 way the next session could break this.)
- **Migration 079 now auto-applies** inside `wb-release-cut.sh` (idempotent, non-fatal). Future
  idempotent migrations: add to that script's loop. Non-idempotent: `scripts/run_migrations.py`
  (needs DATABASE_URL; `.env` is excluded from the deploy tarball).
- **Flipped `bootstrap_gfs` rows** persist in the DB until they age out of the 90-day window (N1
  fixed forward only). Optional purge in §4-C.
- **Diverged top-level tree** (`base_engine/weather/**`) is dead (V43) — the bot imports the
  vendored `bots/weather/engine/**`. Fix the vendored copy; the top-level still holds pre-S222 bugs.
- **`WEATHER_YES_MIN_CONFIDENCE` and the exec-edge escape valve** default to 0.0/off in settings.py
  (S153 posture) — the bot ships with no active calibrated admission floor on either side by
  default; V28 is the lever to change that once the calibrator is trustworthy.
- **A stray `scripts/esports_market_shape_probe.py`** rode into record commit `a9cfcfa` (it was
  pre-staged in the operator's local tree). Harmless (weather never runs it); remove if desired.

---

## 6. Deploy / ops mechanics

Tarball splinter release (NOT git-on-VPS). From the operator's Windows machine, key
`~/.ssh/wb_deploy2`, VPS `ubuntu@18.201.216.0`, DB `polymarket`. The single-command PowerShell
block (pull → tar → scp → `wb-release-cut.sh` → PS-inline record → commit → push) is the current
path; `wb-release-cut.sh` now flips the symlink, restarts, verifies `service: active` + S224
markers, applies migration 079, and prints the `ROLLBACK:` line. Success = `service: active | S224
markers … 24` + `migration 079 … applied` + a green `LIVE` line. Rollback = the printed
`sudo ln -sfn <old> … && systemctl restart`.

Deploy parity from a keyless/cloud session: `deploy/LAST_DEPLOY.json` (read by
`scripts/wb_resume_check.sh`). Live-VPS *health* still needs the deploy key.

---

## 7. Scope & constraints (bind the next session)

- **WeatherBot silo.** Touch only WB files (`bots/weather_bot.py`, `bots/weather/engine/**`,
  `.env.weather`, weather scripts/tests, `schema/migrations/*weather*`).
- **Never quote P&L** (Forbidden Pattern #11) — calibration metrics only.
- **One fix per commit; defect-test-first; keep the manifest in sync** (the resume check FAILs if
  an S224 fingerprint count drifts — it caught a real miss this session).
- **Verification/measurement needs the VPS DB** — cannot run in a cloud sandbox; route those steps
  to a VPS-access session with the exact prompt file.
- Note: the CLAUDE.md MB-priority / shared-resource-subordination rules were REMOVED this session
  (operator directive). The DB and shared modules are changed under normal review, not a priority gate.

---

## 8. Key file map

- `bots/weather_bot.py` — bot engine; calibrator class (~67-700); METAR override (~2960-3030);
  renorm/gates (~2760-2900); `_calibrated_edge_admits` + `_check_executable_edge` (~4460+);
  `_resolve_actual_temp` + actuals updater (~5240+); calibrator fit SQL (`fit_from_trade_events`, ~185+).
- `bots/weather/engine/base_engine/weather/probability_engine.py` — deflate-only renorm (4 sites).
- `bots/weather/engine/base_engine/weather/forecast_client.py` — `_synthetic_lead_sigma`; NDFD PoP.
- `bots/weather/engine/config/settings.py` — WB gate defaults (~840-980); the S224 knobs.
- `schema/migrations/079_weather_calibration_actual_source.sql` (+ `down/`).
- `docs/WEATHER_STATUS.md` — canonical current status. `docs/WB_FALLACY_AUDIT_S223.md` — the register.
- `docs/WB_GROUNDTRUTH_CLUSTER_PLAN_S224.md` — the cluster design (IMPLEMENTED).
- `docs/WB_HANDOFF_MANIFEST.json` + `scripts/wb_resume_check.sh` — the resume harness.
- `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` — the time-gated verification.
- `deploy/wb-release-cut.sh` — the deploy script (now auto-migrates).
