# HANDOFF — WeatherBot S227 (calibrator/EMOS crash found + fixed + DEPLOYED · S222 clock restarted)

**Session:** WeatherBot (WB) silo · **Branch:** `claude/new-whiteboard-session-9b23tq`
**HEAD at handoff:** the commit carrying this doc (parent `c1ca4bb`) · **Deployed release:** `20260711_002634` (verified live)
**Date:** 2026-07-11 · **Mode:** PAPER (`SIMULATION_MODE=true`) — treated as live per CLAUDE.md
**Scope:** WeatherBot only (`bots/weather_bot.py` + tests + docs; no shared-module edits this session).

> **Canonical always-current status is `docs/WEATHER_STATUS.md`.** This file is the archival
> deep-dive of the S227 session. Resume path: `bash scripts/wb_resume_check.sh` → read
> `WEATHER_STATUS.md` (OPEN DECISIONS) → this doc for detail.

---

## 0. TL;DR for the next session

S227 set out to run the time-gated S222 verification and instead found (via a stress-test
error triage) that **the calibrator had never been re-learning at all**: the S224 ground-truth
cutoff was bound as a Python *str* into a `timestamptz` SQL parameter — asyncpg DataError —
so since the 07-08 deploy EVERY confidence-calibrator fit crashed (1,109 warning-level
failures) and EVERY EMOS/bias/tail calibration reload crashed **silently** (debug-level
swallow, 0 successes journal-wide). Fixed at root (`92740f3`, datetime bind + swallow
elevated to warning), deployed in release `20260711_002634` (effective restart
**2026-07-11 00:47:00Z**), proof-of-life verified (calibration reloading, fit path executing,
failure counters 0). **Consequences: (a) the S222 verification clock RESTARTED at 00:47:00Z —
the whole 07-08→07-11 window is discarded (it traded without calibration); (b) the calibrator
re-learn clock also starts 07-11, not 07-08.** The single most important don'ts: do NOT
measure/judge anything against pre-07-11 data, and do NOT touch the calibrator — it is now
genuinely re-learning from zero.

## 1. What is LIVE right now

- **Deployed:** release **`20260711_002634`** cut from `6770883` via **`git archive`** (clean,
  tracked-files-only — a first; see §6). Effective service restart **2026-07-11 00:47:00Z**
  (the release STAMP is 00:26 — the first flip crash-looped 43×, was rolled back ~00:42, and
  re-flipped repaired at ~00:46; rows 00:26→00:47 are old-code output).
- **Rollback:** `sudo ln -sfn /opt/pa2-weather-releases/20260710_204822 /opt/polymarket-ai-v2-weather && sudo systemctl restart polymarket-weather`
- **Verified live (00:48Z):** `weatherbot_calibration_reloaded` — first success since 07-08
  (41 stations / 571 rows post-cutoff, EMOS-ready: EDDM + LIML, rest pending samples);
  `weatherbot_confidence_cal_insufficient_data n=0 need=200` (fit query EXECUTES; re-learn
  accumulates from zero); `calibration_reload_failed` = 0, `cal_fit_failed` = 0.
  Prediction flow healthy: 266 rows / 56 distinct markets in the first ~90 min.
- Migrations 079+080 remain applied (S227 added none). All S222 containment gates still ON.
  V28 gate still OFF. Deploy recorded (`82302b7`, `deploy/LAST_DEPLOY.json`).

## 2. The diagnosis / why this work exists

Operator asked for a stress test while waiting on the S222 gate. The box passed (2× load
spike absorbed, window 00:01:38→00:08:30Z on record), but triaging the 6 in-window journal
errors surfaced `weatherbot_confidence_cal_fit_failed` with
`asyncpg DataError: invalid input for query argument $2: '2026-07-01' (expected a
datetime.date or datetime.datetime instance)`. All three WS-3 cutoff sites
(`CAST(:gt_cutoff AS timestamptz)`) bound the setting as str. Journal proof of blast radius:
**0** `weatherbot_calibration_reloaded` and **1,109** `cal_fit_failed` since 07-08 19:18.
The "calibrator resetting toward identity" everyone was watching (OPEN DECISION #1) was the
crash producing identity behavior, not the designed re-learn — and the prescribed watch-grep
(`calibrator|actual_source|abstain|holdout_valid`) matched none of the failure event names,
which is why three days of failures went unseen.

**Binding directive:** never quote P&L (CLAUDE.md Forbidden Pattern #11). Calibration only.

## 3. What was done

| Commit | Change | One line |
|--------|--------|----------|
| `23db98a` | S222 prompt true-up #1 | verdict cutoff moved from tarball stamp (15:13) to real S224 restart (19:18:38); `--dedup-markets`; window-integrity checks. Ran on box: clean window 40/50 → correct self-abort (leak-regression 0, PSW-ambiguous 0) |
| `92740f3` | **THE FIX** | `_gt_cutoff_datetime()` tz-aware UTC datetime bound at all 3 WS-3 sites; reload swallow elevated debug→warning (S177 precedent). Defect-test-first: `TestS227GtCutoffBindsDatetime` (3 tests, bind-param capture, red→green). WB suites 361 passed |
| `6770883` | docs | OPEN DECISIONS #1/#2 rewritten (re-learn never started; S222 clock restarts at fix deploy) |
| `82302b7` | record | deploy `20260711_002634` recorded (content-verified: S227 markers=4 on box) |
| `c1ca4bb` | docs | status re-pointed to the live deploy; release-cut recipe codified; verification prompt re-pointed to `--since 20260711_004700` |

Also this session, non-commit: stress test executed + analyzed (PASS); crash-loop diagnosis +
fix-forward on the box (data/ skeleton, §6); deploy record; manifest fingerprint `S227 x4` +
headline commit added.

## 4. PENDING WORK — exact next steps

1. **(Operator, URGENT) Rotate the Pinnacle API key.** It is now exposed in THREE places:
   `/opt/pa2-shared/.env` junk lines (~360/367), the systemd journal (echoed on every service
   start), and pasted chat transcripts. Rotate at the provider, delete both junk lines.
2. **(Operator) Box security/hygiene cleanup:** old releases (`20260710_*` and earlier)
   contain a swept-in **`wallet.txt`** + ~250 untracked working-tree files (old tar flow);
   also a stale `/tmp/wb-20260710_165940.tar.gz` and `%TEMP%\wb-*.tar.gz` on the Windows box.
   List first, then delete (operator judgment — do not bulk-delete blind).
3. **S222 verification — time-gated on ≥50 distinct resolved markets after 00:47:00Z**
   (ETA ~07-13; 56 distinct markets already logged in the first 90 min, so possibly sooner).
   Gate query:
   `SELECT count(DISTINCT market_id) FILTER (WHERE resolution IS NOT NULL) FROM prediction_log
    WHERE bot_name='WeatherBot' AND prediction_time > '2026-07-11 00:47:00';`
   When ≥50 → run `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` (fully re-pointed, self-aborting)
   from a VPS session. PASS → retire gates per `WEATHER_S222_STATUS.md` §4-B in order
   (A1/A3 → dampeners → caps; C0 Kelly last and calibrator-gated). A reminder Routine fires
   into the S227 cloud session 2026-07-13 12:00Z (`trig_012fmdChMLAMu2PfhjzVtx9X`).
4. **Watch the calibrator re-learn (now real).** `journalctl -u polymarket-weather | grep -E
   "calibration_reloaded|calibration_reload_failed|cal_fit|insufficient_data|holdout_valid"` —
   `calibration_reloaded` should recur ~6h; `reload_failed`/`cal_fit_failed` must STAY 0 (both
   warning-level now — silence is meaningful). 53 leak-era entries age out ~2026-08-07.
5. **Deferred switches** (V28 gate ON, deeper V26, bootstrap_gfs purge, WU-only filter) —
   unchanged, gated on #3 + #4.

## 5. Gotchas / traps discovered (the expensive lessons)

1. **asyncpg str-vs-timestamptz:** any bind param inside `CAST(:x AS timestamptz)` (or typed
   timestamptz by inference) MUST be a datetime object. grep `AS timestamptz` before writing
   new cutoff SQL — sqlite/mock tests will NOT catch this class.
2. **Watch-greps must match the failure event names, not the happy-path names.** The
   calibrator watch-grep missed 1,109 failures because it grepped for "calibrator" while the
   events were `weatherbot_confidence_cal_fit_failed` / `weatherbot_calibration_reload_failed`.
   When prescribing a journal watch, grep for BOTH the success and the failure event names.
3. **Debug-level swallows on financial-adjacent paths are invisible** (journal at info) —
   second occurrence (S226 prediction-log outage was the first). Both calibration swallows
   are warning-level now; audit remaining `logger.debug` catches before trusting silence.
4. **`ProtectSystem=strict`:** the release tree is READ-ONLY at runtime (whitelist:
   `/opt/pa2-shared/data`, `/opt/pa2-shared/saved_models`, `/var/log/polymarket`). The engine
   cannot mkdir inside the release — required dirs must EXIST in the release. A clean tarball
   without `data/` crash-loops on `Read-only file system: 'data/backups'`.
5. **Old tar-the-working-tree deploys swept ~250 untracked files into every release** —
   handoff docs, caches, `wallet.txt` (!). That's also why old releases are ~4G and why they
   "worked": the swept `data/` dirs satisfied gotcha #4 by accident.
6. **Tarball STAMP ≠ restart time — third occurrence.** Release `_002634` stamp is 00:26;
   effective code start 00:47:00Z. Rows in between are old-code output. Always cutoff at the
   journal-confirmed restart.
7. **A resolved-count gate query is meaningless minutes after a window reset** (returns 0;
   markets need ~a day to resolve). Check `total_rows`/`distinct_markets` for flow health
   instead; judge the resolved count only near the ETA.
8. **`main.py` prints "Press Enter to exit..." on crash** — it just delays the systemd
   restart cycle; not a hang, ignore it in crash-loop journals.

## 6. Deploy / ops mechanics — NEW RECIPE (this is now the working path)

From the operator's Windows machine (key `~/.ssh/wb_deploy2`):

```powershell
cd C:\lockes-picks\polymarket-ai-v2
git fetch origin ; git checkout claude/new-whiteboard-session-9b23tq ; git pull origin claude/new-whiteboard-session-9b23tq
$STAMP = (Get-Date).ToUniversalTime().ToString("yyyyMMdd_HHmmss")
git archive --format=tar.gz -o "$env:TEMP\wb-$STAMP.tar.gz" HEAD
scp -i ~/.ssh/wb_deploy2 "$env:TEMP\wb-$STAMP.tar.gz" ubuntu@18.201.216.0:/tmp/wb-$STAMP.tar.gz
Get-Content -Raw deploy\wb-release-cut.sh | ssh -i ~/.ssh/wb_deploy2 ubuntu@18.201.216.0 "tr -d '\r' | bash -s $STAMP"
```

**PLUS the data-skeleton step `wb-release-cut.sh` does not yet do** (candidate one-commit
improvement for a future session — add to the cut script after extract):

```bash
# mirror previous release's data/ dir skeleton into the new release (dirs only)
cd "$OLD" && find data -type d -exec mkdir -p "$RELEASE/{}" \; && chown -R polymarket:polymarket "$RELEASE/data"
```

Multi-line remote work from PowerShell: here-string `@'…'@ | ssh … "tr -d '\r' | sudo bash -s"`
(the `tr` defuses CRLF). SQL: here-string piped to `sudo -u postgres psql polymarket -f -`.
Never paste raw multiline text at a bare PS prompt. Verify a deploy by RESTART time
(`journalctl … | grep -iE 'Started|Stopped'`), never by the stamp.

## 7. Scope & constraints

- WB silo; this session touched `bots/weather_bot.py`, its tests, docs, deploy record only.
- Never quote P&L (Forbidden Pattern #11) — calibration metrics only.
- One fix per commit; defect-test-first; manifest in sync (S227 fingerprint + headline added).
- VPS/DB steps need the deploy key — cloud sandboxes route them to the operator as paste-ready
  here-string commands (the pattern above; this session ran the whole deploy that way).

## 8. Key file map

- `bots/weather_bot.py` — `_gt_cutoff_datetime()` (~89), fit bind (~262), EMOS bind (~6300),
  warning-level reload swallow (~6570).
- `tests/unit/test_weather_bot.py` — `TestS227GtCutoffBindsDatetime` (end of file).
- `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` — re-pointed; cutoff `20260711_004700`; self-aborts.
- `docs/WEATHER_STATUS.md` — canonical current status (OPEN DECISIONS rewritten this session).
- `docs/WB_HANDOFF_MANIFEST.json` — S227 fingerprint (`S227` x4) + headline `92740f3`.
- `deploy/LAST_DEPLOY.json` — `20260711_002634` @ `6770883`.
- `deploy/wb-release-cut.sh` — works, but see §6 for the data-skeleton gap.
- `docs/WEATHER_S226_STATUS.md` — prior session (V23/prob_frame/leak-closure context).
