# Plan — Calibrator / Ground-Truth Contamination Cluster (S224)

**Status:** PLAN ONLY — no code written. Scopes fallacy-audit findings **V1, V4, V6, V10,
V11** (N1 already fixed in `04185e8`). Prepared 2026-07-08 for operator review before any
implementation. Line numbers are from the S224 verify pass and WILL have shifted — re-confirm
with grep before editing.

## The one-sentence problem

WeatherBot's two learned corrections — the **confidence calibrator** (P(win|confidence)) and
the **EMOS/bias** temperature correction — are trained on data that is (a) partly its own
output (feedback loop) and (b) contaminated by ~96% ERA5/Open-Meteo "ground truth" from before
the 2026-07-01 WU scraper fix, with **no way to tell clean rows from dirty ones** because no
source is recorded.

## Findings in scope

| # | Defect | Load-bearing evidence (re-verify lines) |
|---|--------|------------------------------------------|
| V1 | Calibrator trains on its OWN post-calibration output: the trade_events ENTRY `confidence` column stores `effective_confidence` (post-calibrate + dampener), but serve applies `calibrate()` to the RAW confidence → recursive train/serve loop each 6h refit | fit SQL `weather_bot.py:~210-228`; write site `…/execution/paper_trading.py:~993-1001`; `calibrate()` `weather_bot.py:~659-687` |
| V4 | EMOS trains on `weather_calibration.actual_temp`, ~96% ERA5 pre-07-01; contaminated rows are permanent (backfill only touches `actual_temp IS NULL`, no source column) | `_fit_emos` `weather_bot.py:~5446-5485`; window `~5910-5917`; backfill `~5173, ~5225, ~5237-5248` |
| V6 | No contamination cutoff anywhere — conf-cal 30/90d + EMOS 90d windows have no 2026-07-01 floor | fit SQL windows `weather_bot.py:~213-214, ~407, ~5910-5917` |
| V10 | ERA5/OM silently substitutes as ground truth on ANY WU scrape failure; debug-level logs; sole truth for bootstrap + dynamic stations | `actual_temp = wu_temp if wu_temp is not None else om_temp` `~5225`; WU fetch fails silent `~5303/5351/5354` |
| V11 | WU-vs-OM sanity check inverted: on >10°F disagreement it DISCARDS WU (the resolution source) and writes OM | `weather_bot.py:~5204-5225` |

## Design — 4 workstreams, in dependency order

### WS-1 · Provenance: add a `source` column to `weather_calibration` (enables everything else)
- **Migration** (new file `schema/migrations/NNN_weather_calibration_source.sql`):
  `ALTER TABLE weather_calibration ADD COLUMN actual_source TEXT;`
  (nullable; existing rows stay NULL = "unknown/pre-instrumentation").
- **Writer**: in the actuals updater (`~5225`) set `actual_source = 'wu'` when `wu_temp` used,
  `'open_meteo'` when the OM fallback used; bootstrap inserts (`~1279 area`) set `'era5_bootstrap'`.
- **Backfill decision (OPERATOR):** existing rows can't be re-sourced retroactively (no stored
  provenance). Option A: leave NULL and treat NULL as "dirty" in training filters (safe, simple).
  Option B: mark all rows with `created_at < 2026-07-01` as `'pre_wu_fix'`. Recommend **A**.
- Contract: no consumer reads the column yet → zero behavior change. Pure instrumentation.

### WS-2 · WU-primacy: fix the inverted sanity check (V11) — small, self-contained
- At `~5204-5225`, when `abs(wu - om) > max_diff`, the current code discards WU. Change to
  **abstain**: leave `actual_temp = NULL` (row retried next cycle since the pending query
  selects `actual_temp IS NULL`) and log at WARNING (not debug). Do NOT write OM as the
  resolution truth. When OM is None (ERA5 lag), keep WU unchecked (as today).
- Standalone; no schema dependency. Could ship before WS-1.

### WS-3 · Contamination cutoff (V4/V6) — depends on WS-1
- Add `WEATHER_GROUND_TRUTH_CUTOFF` (date, default `2026-07-01`) to settings.
- In BOTH training paths, add to the WHERE clause: `AND (actual_source = 'wu' OR created_at >=
  :cutoff)` — i.e. train only on WU-sourced rows, or rows after the cutoff (belt-and-suspenders
  while the source column backfills forward).
  - conf-cal fit SQL `~213-228` + YES fallback `~407-410`
  - EMOS/SAMOS/global windows `~5910-5917`
- **Risk:** shrinks the training set — cold stations / long-lead buckets may drop below the
  min-sample thresholds (conf-cal `min_samples`, EMOS ≥20 pairs) and fall back to identity.
  That is the SAFE direction (identity = no correction) but reduces coverage. Quantify the row
  loss before enabling; consider a grace period where the cutoff is configurable and starts loose.

### WS-4 · Break the calibrator feedback loop (V1) — highest care
- Root cause: the `confidence` column persisted at entry is `effective_confidence` (post-cal),
  but training should regress OUTCOME on the RAW pre-calibration confidence.
- **Fix shape (pick one, OPERATOR):**
  - **4a (preferred):** persist RAW confidence in a dedicated field the fit reads. `raw_confidence`
    already exists inside `event_data` as `cal_divergence`'s input (`weather_bot.py:~3728`) — add
    it as a first-class column or read it from event_data in the fit SQL, and train X = raw.
  - **4b:** stop applying `calibrate()` at serve for the purpose of the STORED confidence — store
    raw, apply calibration only in the sizing/gate read path. Larger blast radius.
- Until fixed, every 6h refit composes the calibration mapping on itself. 4a is the smaller change.
- **Cannot be verified without the DB** (needs real trade_events); build with a defect test on a
  synthetic fixture asserting train-X == raw, not effective.

## Sequencing & gating
1. **WS-2** (WU-primacy) — smallest, no deps, ship + test first.
2. **WS-1** (source column migration + writer) — instrumentation, zero behavior change.
3. **WS-3** (cutoff) — after WS-1 has populated `actual_source` for a while; measure row loss first.
4. **WS-4** (feedback loop) — independent of 1-3 but highest care; do last or in parallel.
- **All of this precedes enabling the V28 calibrated-edge gate** and the S222 gate retirements —
  a trustworthy calibrator is the prerequisite for both.

## Cross-cutting risks
- **DB migration + `database.py`/paper_trading touch points:** normal review discipline —
  full pytest, blast-radius check on every consumer of the changed column/SQL. (No cross-bot
  priority gate; the DB is shared and fine to change here.)
- **Shrinking training data** can silently drop stations to identity — instrument coverage before/after.
- **P&L:** evaluate calibration improvement via Brier/PIT/reliability only (Forbidden Pattern #11).
- **Diverged tree:** apply to the vendored `bots/weather/engine/**`; top-level `base_engine/weather/**`
  is dead (V43) — keep them in sync or leave the dead copy alone.

## Verification per workstream
- WS-2: unit test — >10° WU/OM gap leaves `actual_temp` NULL (not OM); ≤10° keeps WU.
- WS-1: migration applies; new rows carry `actual_source`; no consumer reads it (grep).
- WS-3: fit SQL excludes pre-cutoff non-WU rows (fixture); log the row-count delta.
- WS-4: fit trains on raw confidence (fixture asserts X source); re-fit is idempotent (no drift on
  repeated refits of the same data).
- End-to-end: after a clean-data refit, re-run `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` — PIT/Brier
  should improve vs the contaminated baseline.

## Operator decisions (RESOLVED 2026-07-08)
1. Backfill strategy for existing rows — **A (NULL = dirty)**. ✅ chosen.
2. V1 fix shape — **4a (train on raw confidence)**. ✅ chosen (elaboration in commit / status).
3. Contamination cutoff — **2026-07-01 hard**. ✅ chosen.
4. ~~MB signoff for shared modules~~ — REMOVED. No cross-bot priority rule (operator directive
   2026-07-08; the CLAUDE.md "SESSION PRIORITY / shared resource" section was deleted). The DB
   is shared and changed under normal review, not a priority gate.
