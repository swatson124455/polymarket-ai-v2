# S229 KICKOFF — WeatherBot session, LOCAL machine (first local session)

Paste this whole file as your first message to a Claude Code session started in
`C:\lockes-picks\polymarket-ai-v2`. You are S229, the direct continuation of S228
(cloud). Unlike cloud sessions, YOU have the VPS deploy key — run ssh/scp/psql
yourself via the shell (the operator approves commands); never ask the operator
to copy-paste command blocks.

## Land on the branch FIRST
```
git fetch origin
git checkout claude/new-whiteboard-session-9b23tq
git pull origin claude/new-whiteboard-session-9b23tq
bash scripts/wb_resume_check.sh   # must be ALL PASS (deploy-parity WARN expected)
```
Then read `docs/WEATHER_STATUS.md` (OPEN DECISIONS) and `docs/WEATHER_S227_STATUS.md`.

## VPS access (proven patterns — you run these directly now)
- Host: `ubuntu@18.201.216.0`, key `~/.ssh/wb_deploy2`
- Multi-line bash: here-string | `ssh -i ~/.ssh/wb_deploy2 ubuntu@18.201.216.0 "tr -d '\r' | sudo bash -s"`
- SQL: here-string | `ssh ... "sudo -u postgres psql polymarket -f -"`
- WB splinter deploys: release-cut recipe in S227 handoff §6 (+ data/ skeleton now folded
  into `deploy/wb-release-cut.sh`).
- MAIN tree (`/opt/polymarket-ai-v2`, runs `polymarket-ingestion`): NOT a git repo — a
  June-3 Windows-deployed CRLF snapshot. Patches go on as surgical single files:
  scp → `tr -d '\r'` → py_compile with the tree's venv python → mv → chown
  `--reference=` a backup → restart service → `git hash-object` verify. Backups:
  `resolution_backfill.py.bak-s228` exists.

## What S228 did (all live / pushed at `863fae8`+)
1. **Resolution-discovery root fix (LIVE on box):** `run_resolution_backfill` Phase 2c —
   prediction-log-driven discovery (untraded markets were NEVER resolution-checked; daily
   resolution rates had decayed to 12–32%). Three patches deployed to
   `/opt/polymarket-ai-v2/base_engine/data/resolution_backfill.py`, final installed hash
   `04951b1f15c212905c0db4e06c447e6e9c04dd9e` (= repo blob at `863fae8`): base fix +
   NULL-end-date qualification (latest_pred >48h) + split budget (2/3 newest-first,
   1/3 oldest-first NULLS FIRST anti-starvation tail lane). VERIFIED: dated markets
   resolve 17/17; per-day rates recovering strongly.
2. **S228 latency package (on branch, INERT until env-flipped, AFTER S222):** priority-wake
   (`WEATHER_PRIORITY_WAKE_ENABLED`, default OFF), `WEATHER_MODEL_RUN_POLL_INTERVAL_S`
   (default 300), release-cut data/-skeleton fix. Full activation block in
   `WEATHER_STATUS.md` OPEN DECISION 3a.
3. Docs/manifest in sync; S228 fingerprints in the resume check.

## YOUR FIRST TASK — finish the database.py end-date backport (S228 unfinished)
**Bug:** the box ingestion (June-3 code) stores `end_date_iso = NULL` for essentially all
newly ingested weather markets (295 of 354 window markets NULL). The repo FIXED this
2026-06-10 (coalesce all end-date spellings in `bulk_insert_markets`, comment says
"stored NULL for ~89% of markets" — see `base_engine/data/database.py` ~1472-1490) but
the fix never reached the box. NULL-dated markets resolve ~2 days late via the 48h rule
instead of ~12h.
**Plan (surgical, NOT a full database.py swap — the box tree is a month behind repo):**
1. `git hash-object /opt/polymarket-ai-v2/base_engine/data/database.py` (on box).
2. Match that blob against repo history to identify the box's exact version
   (remember the CRLF trick: the box file likely = some repo blob + CRLF; compare via
   `git hash-object --no-filters` on a `sed 's/$/\r/'` variant, as S228 did).
3. Backport ONLY the end-date coalesce hunk onto that exact version; py_compile; deploy
   surgical-style with a `.bak-s229`; hash-verify; restart `polymarket-ingestion`.
4. Verify: new markets ingested after restart have `end_date_iso NOT NULL`:
   `SELECT count(*) FILTER (WHERE end_date_iso IS NULL) , count(*) FROM markets
    WHERE created_at > NOW() - INTERVAL '2 hours';` (column name may differ — check).
**Defect-test-first where testable; one fix per commit; record in WEATHER_STATUS.md.**

## Then, in order
1. **S222 gate check** (was 28/50 late 07-12; opens ≥50):
   `SELECT count(DISTINCT market_id) FILTER (WHERE resolution IS NOT NULL) FROM
    prediction_log WHERE bot_name='WeatherBot' AND prediction_time > '2026-07-11 00:47:00';`
   When ≥50 → run `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` (self-aborting) from the VPS.
   PASS → retire containment gates per `WEATHER_S222_STATUS.md` §4-B (A1/A3 → dampeners →
   caps; C0 Kelly LAST, calibrator-gated).
2. **Calibration health** (must stay clean; hands OFF the calibrator until ~08-07):
   `journalctl -u polymarket-weather --since "2026-07-11 00:47:00" | grep -cE
    "calibration_reload_failed|cal_fit_failed"` → MUST be 0; `calibration_reloaded`
   recurs ~6h (3/3 on schedule so far).
3. **Stranded-market residue:** the 07-06/07-07 buckets (`end_date NULL` was 22 and
   falling, `ENDED LONG AGO` 9 queued behind the cross-bot NULL pool). If any ids survive
   past a day, CLOB-check them (`https://clob.polymarket.com/markets/<condition_id>`).
4. **Operator nags (still open):** Pinnacle API key rotation (URGENT — echoed in journal +
   chat) then delete `/opt/pa2-shared/.env` junk lines ~360/367; local `wallet.txt`
   original (move out of repo; consider wallet rotation); `Remove-Item $env:TEMP\wb-*.tar.gz`;
   prune old WB releases only after the S222 verdict.
5. **Post-S222:** latency-package activation env block (OPEN DECISION 3a); deferred
   switches (OPEN DECISION 3); ops-debt: give the main tree a real deploy mechanism.

## Housekeeping
- A reminder Routine (`trig_01SLfyuiSzqqVNyUyAeZfZpH`) fires into the OLD S228 CLOUD
  session 2026-07-13 12:00Z with the gate/calibration/nag drill. It fires harmlessly
  there; this local session owns the work now. (Cloud tools can delete it if desired.)
- Cloud sessions pushed via env branch `claude/weatherbot-s228-h6wq0y` (now merged);
  locally, work directly on the pinned whiteboard branch.

## Rules (unchanged, binding)
- WeatherBot scope + the sanctioned shared-layer exceptions exercised in S228 (resolution
  backfill / ingestion single-file patches — always both repo copies where mirrored,
  top-level AND `bots/weather/engine/base_engine/...`).
- NEVER quote P&L (CLAUDE.md Forbidden Pattern #11) — calibration metrics only.
- One fix per commit; defect-test-first (red→green proof); keep
  `docs/WB_HANDOFF_MANIFEST.json` in sync (resume check FAILs on drift; tag new work S229).
- Watch for asyncpg str-vs-timestamptz binds (grep `AS timestamptz`).
- Do NOT touch the calibrator; do NOT start new calibration changes before S222 + re-learn.
- Test-suite env note: full pytest needs the optional-deps caveat — 34 known
  environment failures (libcst/catboost/lightgbm/skops/h2/dashboard) existed at baseline
  `c04f70e` in the S228 cloud sandbox; compare failure SETS against a stashed baseline,
  never absolute counts.

Confirm the resume check passed and summarize OPEN DECISIONS before doing anything.
