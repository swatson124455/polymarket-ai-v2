# HANDOFF — WeatherBot S226 (leak closed · audit follow-ups · V23 frame fix — ALL DEPLOYED)

**Session:** WeatherBot (WB) silo · **Branch:** `claude/new-whiteboard-session-9b23tq`
**HEAD at handoff:** `95114a6` · **Deployed release:** `20260710_204822` (verified live)
**Date:** 2026-07-10 · **Mode:** PAPER (`SIMULATION_MODE=true`) — treated as live per CLAUDE.md
**Scope:** WeatherBot only (one sanctioned shared-module touch: `base_engine/data/database.py`, see §3/§5).

> **Canonical always-current status is `docs/WEATHER_STATUS.md`.** This file is the archival
> deep-dive of the S226 session. Resume path: `bash scripts/wb_resume_check.sh` → read
> `WEATHER_STATUS.md` (OPEN DECISIONS) → this doc for detail.

---

## 0. TL;DR for the next session

S226 closed two investigations and shipped one root fix, all live and verified on the box:
(1) the **manufactured-certainty leak is CLOSED** — it was the pre-S224 renorm bug, misattributed
because the S224 tarball STAMP (15:13) was read as the restart time (actual restarts 18:08/19:18,
journal-confirmed); the tripwire stays as a regression guard. (2) The **V23 frame bug is fixed at
root**: `prediction_log.predicted_prob`/`market_price` are now P(YES)/YES-price on every WB row,
stamped `prob_frame='yes'` (migration 080); BOTH graders refuse to grade unlabelled PSW rows and
177,275 historical rows are machine-labelled ambiguous. (3) Five audit follow-ups landed (V37
null-PoP, V34 marker+RNG, V28 calibrated top-N gated OFF, telemetry truth, cleanup). The single
most important don't: **do NOT touch the calibrator** — it is still mid-re-learn (and mildly
contaminated by leak-era entries until ~2026-08-07, self-clearing). Also: a DB-layer lesson that
cost an outage — the WB service binds the **TOP-LEVEL** `base_engine.data.database.Database` at
runtime (via `main.py`), NOT the vendored copy; any insert-signature change must land in BOTH.

---

## 1. What is LIVE right now

- **Deployed:** release **`20260710_204822`** on the WB splinter (`/opt/polymarket-ai-v2-weather`
  → `/opt/pa2-weather-releases/20260710_204822`, `polymarket-weather.service`). Rollback:
  `sudo ln -sfn /opt/pa2-weather-releases/20260710_201020 /opt/polymarket-ai-v2-weather && sudo systemctl restart polymarket-weather`.
- **Verified live (operator queries, 2026-07-10 ~20:53Z):** `service: active`; **14 rows with
  `prob_frame='yes'`** written within 2 scans of the cut (newest 20:52:38); historical 177,275
  rows `prob_frame IS NULL`; `ungraded_psw_ok = 0` (retro-null landed, guard holding); zero
  `prediction_log_write_failed` / `weatherbot_prediction_log_failed` warnings.
- **Migrations applied:** 079 (`actual_source`) + **080 (`prediction_log.prob_frame`)** — both
  auto-applied by `wb-release-cut.sh`.
- Carries: S223 fixes + S224 batch + S225 diagnostics/tripwire + the full **S226 batch** (§3).
- Containment unchanged: all S222 safety gates still ON. V28 gate still **OFF**.
- Earlier same-day releases (`20260710_165646`, `_200742`, `_201020`) are intermediate; `_201020`
  had a **silent prediction-log outage** 20:12→20:48 (see §5.1) fixed by the hotfix in `_204822`.

## 2. The diagnosis / why this work exists

Three threads, all resolved this session:

1. **Manufactured-certainty leak (OPEN DECISION #4, now CLOSED).** 58 rows at
   `predicted_prob = exactly 1.0` (30 resolved, 3 correct — 10% hit-rate at claimed certainty).
   All predate the REAL S224 restart; signature matches the pre-S224 inflate-renorm exactly
   (singleton `p/p=1.0` → conf `min(0.95,1.0)=0.95` → ×0.85 dampener = the observed **0.8075** →
   `edge = 1−price`). Journal-confirmed: restarts 18:08:41 & 19:18:38, last leak row 17:59:38.
   Zero occurrences since across ~9.7K rows; tripwire silent. S225's "5 post-deploy rows" were a
   **timestamp misattribution** (tarball stamp ≠ restart time).
2. **V23 frame bug (OPEN DECISION 3b, now FIXED AT ROOT).** `predicted_prob` carried two
   conventions — P(YES) on temperature rows, P(chosen side) on PSW NO rows — while the single
   grader reads YES-frame. Winning PSW NO calls were stored as misses, poisoning
   calibration_tracker, Brier feeds, and the consecutive-loss sizing compress. `market_price` had
   the sibling bug (chosen-side price → realized_edge wrong on every NO entry).
3. **Audit follow-up queue** — the deferred small items from the S223/S224 fallacy audit.

**Binding directive:** never quote P&L (CLAUDE.md Forbidden Pattern #11). Calibration only.

## 3. What was done (every fix defect-test-first; 29 new tests this session)

| Commit | Change | One line |
|--------|--------|----------|
| `21c0fd5` | cleanup | stray esports probe script removed (grep-verified unreferenced) |
| `4df2fca` | V37 follow-up | null NDFD PoP on a MATCHING day = 0% (NWS dry-day convention), not dropped; no-matching-day → None invariant test-locked |
| `7eb7e5e` | V28 follow-up | top-N bucket selection ranks by calibrated edge ONLY when `WEATHER_CALIBRATED_EDGE_GATE_ENABLED` is ON (proven inert at default OFF; `_select_top_buckets`) |
| `76fd061` | V34 follow-ups | `synthetic_ensemble` marker field + `"synthetic"` in models_used + deterministic sha256-seeded member RNG (was PYTHONHASHSEED-unstable); spread math untouched |
| `b408453` | telemetry truth | V16 (S-T log: `proposed_usd`+`applied`, truthful docstring), V20 (dampener reciprocals documented), V21 (`forecast_delta` = fetch-over-fetch, `delta_basis` field). V23 correctly refused as behavioral |
| `95c732c` | **V23 root fix** | `predicted_prob` = P(YES) on every row: opps carry `model_prob_yes`; all 4 opp log sites use `_yes_frame_prob(opp)`; trading fields untouched |
| `14006b0` | **V23 label** | migration 080 (`prob_frame` column + retro-NULL of historical PSW grades); writers stamp `'yes'`; BOTH graders (vendored + top-level, weather-model_name-scoped, runtime column check) refuse unlabelled PSW rows |
| `de09424` | V23 sibling | `market_price` = YES price at all 5 WB log sites (`_yes_frame_price`); realized_edge correct on labelled rows; stored `edge` coherent |
| `535ec86` | **HOTFIX** | top-level `Database.insert_prediction_log` + ORM gain `prob_frame` — the WB service binds the TOP-LEVEL class (main.py → BaseEngine), so the vendored-only param killed all prediction logging 20:12→20:48 (TypeError swallowed at debug). Swallow elevated debug→warning (S177 precedent) |
| `69065e0` | deploy | `exit 0` terminator in wb-release-cut.sh (PowerShell pipe appends phantom CRLF line → false exit 127) |
| `95114a6` | record | deploy `20260710_204822` recorded |

Also: leak-closure docs (`014ee5e` + prior), manifest updates. Tests: WB suite 325 in
test_weather_bot.py + 7 synthetic + others; full suite **3862 passed** with zero-delta failure
diff vs stashed baseline (remaining failures are pre-existing sandbox/dep gaps).

## 4. PENDING WORK — exact next steps

1. **(Operator, urgent-ish) Rotate the Pinnacle API key.** It sits in a pasted-command garbage
   line in `/opt/pa2-shared/.env` (~lines 360/367), echoed by systemd into the journal. Rotate at
   the provider, delete both junk lines. Also silences every dotenv/bash parse warning.
2. **Watch the calibrator re-learn (OPEN DECISION #1).** No action — journal grep
   `calibrator|actual_source|abstain|holdout_valid`. NOTE: 53 leak-era ENTRY events sit in its
   30-day fit window (avg raw conf 0.95, mostly resolved NO) → mildly contaminated until
   **~2026-08-07**, self-clears. Do NOT hand-filter; do NOT judge the re-learn harshly before then.
3. **S222 post-fix verification (OPEN DECISION #2, time-gated).** At last check **42/50** distinct
   resolved markets post-07-08. When ≥50: run `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` from a
   VPS session, using `calibration_check.py WeatherBot --since 20260708_151330 --dedup-markets`.
   PASS → retire containment gates per `WEATHER_S222_STATUS.md` §4-B.
4. **Then OPEN DECISION #3** (V28 gate ON, deeper V26, bootstrap purge, WU-only filter) — gated
   on #2 + #3.
5. **Optional spot-check in a few days:** `yes`-labelled rows accumulating
   (`SELECT prob_frame, count(*) FROM prediction_log WHERE bot_name='WeatherBot' GROUP BY 1;`)
   and `journalctl | grep weatherbot_prediction_log_failed` stays empty.

## 5. Gotchas / traps discovered (the expensive lessons)

1. **THE RUNTIME-BINDING TRAP (cost: 36-min silent outage + hours of diagnosis).** The WB service
   runs `main.py` → **top-level** `base_engine.base_engine.BaseEngine` → `db` is the **top-level**
   `base_engine.data.database.Database`. The vendored `bots/weather/engine/**` tree owns the
   weather ENGINE imports (probability/forecast/precipitation), **NOT the DB layer**. Any change
   to `insert_*` signatures or ORM models must land in BOTH database.py files. The V43 "top-level
   tree is dead" lore is true only for `base_engine/weather/**`.
2. **Debug-level swallows are invisible in production** (journal runs at info). The prediction-log
   outage was caught only by DB-side absence. `_log_weather_prediction`'s catch is now warning-level;
   audit other `logger.debug` swallows on financial-adjacent paths before trusting their silence.
3. **Tarball STAMP ≠ restart time.** The release stamp is tar-creation time; the service restarts
   at the END of the cut (possibly hours later — S224: stamp 15:13, restarts 18:08/19:18). Never
   attribute DB rows to a release by comparing against the stamp; use
   `journalctl … | grep -iE 'Started|Stopped'`.
4. **PowerShell deploy quirks:** `Get-Content -Raw | ssh "bash -s"` appends a phantom CRLF line
   (now defused by `exit 0`); raw `psql` SQL must be piped via here-string to `psql -f -`;
   `tar` takes ~1-2 min silently (~270 MB tarball) — not hung.
5. **`groups_with_edge` in scan_done counts groups with non-empty TRADEABLE lists** (post-gate),
   not raw edges — it's the correct "should prediction logs exist?" signal.
6. **Historical PSW rows are permanently frame-ambiguous** (side never persisted). They are
   labelled (`prob_frame IS NULL`) and ungraded — do not attempt row-level repair, and treat
   pre-080 PSW rows as contaminated for any predicted_prob-based analysis.
7. **`base_engine/data/ingestion_error_capture.txt` is a tracked file that test runs overwrite**
   — restore it (`git checkout --`), don't commit sandbox paths. Repo-hygiene wart, non-WB scope.
8. **PSW pipeline logged zero edge events all day 07-10 pre- and post-deploy** — quiet on its own
   (market conditions), NOT a regression. Baseline before alarming.

## 6. Deploy / ops mechanics

Unchanged tarball splinter release; the working PowerShell path is embedded in this session's
chat and in §4 of `WEATHER_S224_STATUS.md`. Notables: `wb-release-cut.sh` now auto-applies
**079 + 080** and ends with `exit 0`; record deploys via the inline JSON block (or
`deploy/wb-record-deploy.sh`) and commit `deploy/LAST_DEPLOY.json` — the S226 hotfix cut
initially skipped this and the record was trued-up after (`95114a6`). Old tarballs accumulate in
`%TEMP%` (~2 GB) — `Remove-Item $env:TEMP\wb-*.tar.gz`.

## 7. Scope & constraints

- WB silo. One sanctioned shared-module exception this session: `base_engine/data/database.py`
  (grader guard + insert param), scoped to weather model_names, full-suite verified. Future DB
  work: both copies, cross-bot verification per CLAUDE.md.
- Never quote P&L (Forbidden Pattern #11). Calibration metrics only.
- One fix per commit; defect-test-first; manifest in sync (S226 fingerprints added).
- VPS/DB steps need the deploy key (`~/.ssh/wb_deploy2`, box `ubuntu@18.201.216.0`, DB
  `polymarket` via `sudo -u postgres psql`); cloud sandboxes cannot reach the box — route those
  to the operator or a VPS session. SQL from PowerShell: pipe a here-string to `psql -f -`.
- Do NOT re-litigate: the leak (closed, journal-proven), the "confidence inversion" (counting
  artifact, fixed by `--dedup-markets`), or the calibrator reset (deliberate, mid-re-learn).

## 8. Key file map

- `bots/weather_bot.py` — bot. S226: `_yes_frame_prob`/`_yes_frame_price` (~4655+), tripwire
  (~60-90 + in `_log_weather_prediction` ~941), `_select_top_buckets`, warning-level log swallow.
- `base_engine/data/database.py` — **the DB class WB actually binds at runtime**: insert
  `prob_frame` param, ORM column, frame-guarded grader (~4083).
- `bots/weather/engine/base_engine/data/database.py` — vendored twin, kept signature-identical.
- `bots/weather/engine/base_engine/weather/precipitation_engine.py` — `model_prob_yes` in opps.
- `bots/weather/engine/base_engine/weather/forecast_client.py` — V37 null-PoP, V34 marker+RNG,
  V21 fetch-over-fetch comments.
- `schema/migrations/080_prediction_log_prob_frame.sql` (+ `down/`) — the label + retro-NULL.
- `deploy/wb-release-cut.sh` — auto-migrations 079+080, `exit 0` terminator.
- `docs/WEATHER_STATUS.md` — canonical current status. `docs/WB_HANDOFF_MANIFEST.json` — resume
  manifest (S226 fingerprints). `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` — the time-gated next gate.
- `tests/unit/test_weather_bot.py` — S226 test classes (`TestS226*`);
  `tests/unit/test_forecast_client_synthetic.py` — V34.
