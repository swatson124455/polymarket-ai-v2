# WeatherBot — CANONICAL STATUS (always current)

> **This file is the single always-current WeatherBot status pointer. Its filename never
> changes.** Session-stamped docs (e.g. `WEATHER_S222_STATUS.md`) are archival deep-dives —
> read them for detail, but THIS file is the source of truth for "what is live and what's open."
> Update the three sections below at the end of every WB session (same commit as the work).

**Last updated:** 2026-07-13 (S229 — **S222 verification ran: FAIL, retire nothing** — and the
failure's ROOT CAUSE was found, fixed, and DEPLOYED the same day: the global SAMOS→raw
conversion pooled climatology across mixed °C/°F stations (avg_clim_mean=43.6 unit soup),
displacing every non-EMOS-ready station's forecast (KORD 97.2°F→86.4°F "certainty"; phantom
NO edges at 0.95 conf realizing ~17%). Release **`20260713_160143`** @ `24b2847`, restart
16:02:29Z — carries the per-station EMOS fix + the end_date_iso persistence fix + the S228
latency package (still inert/flag-OFF). S222 verification clock RESTARTS at 16:02:29Z.)
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

2. **S222 post-fix verification — RAN 2026-07-13 (S229) on 77 resolved markets: FAIL on every
   criterion; NOTHING retired. Clock RESTARTS at the S229 EMOS-fix deploy (2026-07-13
   16:02:29Z, release `20260713_160143`); re-run the prompt when the NEW window reaches ≥50
   (~3-4 days at ~19/day).** Verdicts on the 07-11→07-13 window: A1/A3 FAIL (PIT KS 0.285
   p≈0, WORSE than baseline 0.155; right-spiked, mean 0.628); dampener retirement FAIL
   (traded-subset BSS deeply negative, tiny N=4-16 — caps kept trade count low); price-cap
   retirement INCONCLUSIVE (caps active → no 80-100¢ cells); C0 Kelly FAIL (0.90+ conf bin
   realized ~17% vs stated, canonical bot_pnl.py, 24 trades). BUT the failure is now
   EXPLAINED and FIXED — see 2b (S229 root cause): the window traded on a poisoned global
   EMOS corrector, so these verdicts measure the defect, not the S222 fixes. The NEXT
   window is the first clean read of A1/A3.
   ORIGINAL (superseded) window framing: The entire 07-08→07-11 window is DISCARDED
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
   **S228 ADDENDUM — the gate count was starving on a resolution-discovery bug, now fixed
   on-branch (see 2a below): only ~29% of WB's logged markets were in `traded_markets`, and
   the resolution backfill never checked untraded markets — daily markets sat unresolved for
   3-4 days (07-09: 32% resolved, 07-10: 12%, 07-11: 0% at 14h). The ~19/day arrival rate was
   the lazy bulk-ingestion path, not real resolution latency. After the ingestion-service
   deploy, expect the backlog to drain within hours and the gate to open ~1 day after (markets
   must still actually resolve on Polymarket). This is a MEASUREMENT fix (labels arrive), not
   a behavior change — window comparability is unaffected.**

2a. **S228 root fix — prediction-log-driven resolution discovery (shared module, deploys with
   the INGESTION service, NOT the WB release cut).** `run_resolution_backfill` Phase 2
   discovered markets only from `traded_markets`/on-chain `trades`/open live positions;
   prediction-only markets were never resolution-checked, so `markets.resolution` stayed NULL
   and `prediction_log` rows were never labeled (proven 2026-07-11: 132/185 window markets
   untracked; ended-days-ago dailies still `resolved=f`; sync_log showed the backfill running
   110×/day — checking the wrong set). Fix: Phase 2c with a dedicated budget
   (`prediction_log_limit=150` default), ended-markets only, 14-day prediction window,
   most-recently-ended first, deduped after trade-driven ids (S125 starvation lesson: trade
   discovery keeps its full batch). Applied to BOTH copies (top-level = the one the ingestion
   service executes; vendored copy is a stale snapshot otherwise — synced for this fix only).
   **DEPLOYED 2026-07-11 ~15:3xZ** via surgical single-file patch (the main tree at
   `/opt/polymarket-ai-v2` is NOT a git repo — it's a June-3 Windows-deployed snapshot with
   CRLF endings): scp + `tr -d '\r'` + py_compile gate + install; verified
   `git hash-object` = `385af285c4…` (the dc7763e blob), `polymarket-ingestion` active.
   Rollback: `sudo cp -a .../resolution_backfill.py.bak-s228 .../resolution_backfill.py &&
   sudo systemctl restart polymarket-ingestion`.
   Verify via SQL (per-day resolved-rate query), NOT the journal: the mini scheduler passes
   `log_progress=False`, so the `prediction-log-sourced` log line only prints on full
   ingestion passes. VERIFIED WORKING 2026-07-11 evening: +43 markets resolved in the first
   cycles (07-09: 92→112, 07-10: 22→36, 07-11: 0→6), draining newest-ended-first.
   ⚠ OPS DEBT (S228 finding): the main tree is an unversioned CRLF snapshot from 2026-06-03 —
   it has NO deploy mechanism and predates a month of repo fixes. Document/rebuild after S222.

2b. **S229 ROOT CAUSE — global SAMOS/EMOS mixed-unit pooling (FIXED + DEPLOYED 2026-07-13
   16:02:29Z, `24b2847`, release `20260713_160143`).** The global SAMOS→raw conversion
   de-normalized anomaly-space params with climatology AVERAGED ACROSS ALL stations — °C
   and °F pooled (journal: `avg_clim_mean=43.6`) — installing ONE corrector
   (a=9.28, b=0.79, σ=2.10) for every station without local EMOS (all but 4 post-cutoff).
   Numerically verified: KORD 07-14 forecast 97.2°F → corrected 86.4°F → P(≤93.5°F)=0.9996
   (logged 0.999; market said ~5%); EGLC 30.4°C → 33.4°C. The RAW ensemble is GOOD
   (independent refetch of the bot's own Open-Meteo call matches the market); ~90% of
   bucket families carried <40% total model mass → phantom NO edges at 0.95 confidence.
   Fix: SAMOS stays in anomaly space, de-normalized PER STATION with its own climatology
   (`_samos_global_by_station`), engine `load_global_emos_by_station()` consulted before
   the legacy pooled tuple; no-climatology fallback fits per temperature unit. Watch after
   each ~6h reload: `grep 'weather_global_emos_by_station_loaded'` (stations≈40) and
   `weatherbot_global_samos_fitted ... method=per_station`. NOTE: entries made 07-11→07-13
   under the poisoned corrector sit in the confidence-calibrator's 30-day fit window
   (like the 53 leak-era entries, self-clears; do NOT hand-filter). ALSO NOTE: the
   07-08→07-11 crash window traded with NO corrections at all (raw+VIF) — different regime
   again; never pool it with either neighbor.

2c. **S229 EV research scoreboard (read-only market-structure studies, 2026-07-13;
   harnesses committed at `scripts/wb_research/`, README has run instructions +
   full numbers).** Terminology: "ensemble" = the bot's raw INPUT (public GFS/ECMWF
   members), not the bot; chain = ensemble → corrections → bot. Verdicts, all on
   primary sources (CLOB price-history/books, IEM METAR, CLOB-verified outcomes,
   matched timestamps):
   - **DEAD — day-ahead directional:** market Brier 0.195 vs raw-ensemble 0.243
     (n=186, 24h lead); still dead morning-of (0.191 vs 0.250, n=230, 8h lead).
     The market prices MORE than the public ensemble. Never trade pre-afternoon
     direction on raw signal.
   - **DEAD — family Dutch-book (taker):** best-ask family sums 0.997–1.062 with
     3–8 shares best-ask depth on tail legs; the ~2% mid-price "underround" is a
     spread artifact. (Fresh-Gamma family scan: 118 families, avg YES-sum 1.020
     at mid = the juice a MAKER collects.)
   - **ALIVE — resolution-day leader-following (the one confirmed edge):**
     buy the bucket containing the METAR running max at local hour H, hold to
     resolution, losers included: monotone +EV, H=15 +0.037/68% (n=31) →
     H=17 +0.085/89% (n=18) per $1 at mid pre-costs; winners jump 0.54→0.72
     within 15 min of the deciding ob, then drift ~0.75→1.00 over hours (the
     harvestable leg). Caveat: one summer week, mid prices — DO NOT trade yet.
   - **OPEN (time-gated) — station wedge:** does the FIXED bot (per-station EMOS)
     beat the market anywhere? Re-run brier_duel with prediction_log probs on the
     clean window ≥50 (~07-16/17), per (station×lead×side) cell; whitelist only
     cells beating the market by >2×(half-spread+slippage).
   - **OPEN (needs live shadow) — maker economics + executable capture:** no
     historical book snapshots exist; proposal = a read-only SHADOW-BOOK LOGGER
     (on each obs event, snapshot the books it would have hit) built AFTER the
     S222 re-run. It prices both the leader-following capture and maker fills.
   Timezone audit for all of the above: PASS (Open-Meteo `timezone=auto` local-day
   seam verified at Seoul; stored targets per-station local; date-string matching
   not index). Quirk: Gamma endDate=12:00Z precedes eastern local day-end —
   resolution arrives on retry, harmless.

2d. **S229b night pass (2026-07-13 late) — full health sweep + queue table.**
   Sweep verdict: green (2 reloads, per-station EMOS ×106, failure counters 0, no
   post-startup tracebacks, 1,271 prediction rows / 89 markets since deploy). Items found
   and their dispositions:
   - **FIXED on branch (`9dc6d59`, needs next release cut):** CancelledError escaped
     `isinstance(result, Exception)` on gather results → tuple-unpack TypeError aborted the
     ENTIRE scan ("Bot scan error: cannot unpack non-iterable CancelledError", 69× 07-11→13
     — pre-existing, not an S229 regression). Both gather sites now check BaseException.
   - **⚠ DORMANT LANDMINE — DO NOT FIX NAIVELY:** `_maybe_bootstrap_cold_station` binds
     `target_date_str` (str) into a date column → asyncpg DataError → cold-start ERA5
     bootstrap inserts have ALWAYS silently failed (why weather_calibration has zero
     `era5_bootstrap` rows). This failure is accidentally PROTECTIVE post-S224: the EMOS
     fit has NO `actual_source` filter, so "fixing" the bind alone would inject
     ERA5-ground-truth pairs into the clean training window (the exact contamination WS-3
     cut out). Only acceptable fix: date-bind + WU-only/`actual_source` training filter
     in the SAME commit, post-S222 (pairs with OPEN DECISION 3's filter item).
   - **NOT bugs:** `losing_streak consecutive_losses=176` = bulk resolution of
     poisoned-window trades arriving (compressor correctly throttling sizing);
     London/Madrid day-ahead "edges" (~0.2-0.3 raw) = small-n local-EMOS claims —
     adjudicated by the clean-window duel, contained by flat sizing + caps meanwhile.
   - **NULL-end backfill EXECUTED:** WB-predicted subset now **0** NULL (171/171 filled
     from CLOB, fill-NULL-only UPDATEs); historical pool (~17.8k weather markets, all
     sources/years) drains via the nightly cron (2e).
   - **S222 prompt RAN (per operator):** self-aborted at Precondition 0.4a as designed —
     0/50 resolved in the post-16:02:29Z window (91 predicted); ETA ~07-16.
     Station-wedge duel: same gate, structurally blocked until first resolutions ~07-15.

2e. **NEXT QUEUE (post-verdict roadmap, S229 close):**
   | # | Item | Trigger / order |
   |---|------|-----------------|
   | 1 | S222 re-run + station-wedge duel (per-cell, `scripts/wb_research/brier_duel.py` fed prediction_log probs) | clean window ≥50 (~07-16/17) |
   | 2 | Gate retirement IF PASS (A1/A3 → dampeners → caps; C0 Kelly last, calibrator-gated) | after 1 |
   | 3 | Shadow-book logger (read-only: snapshot books on obs events) — prices leader-following capture + maker fills | after 1; prerequisite for trading the confirmed edge |
   | 4 | Latency package activation (3a) — decide WITH 3 (priority-wake is the nowcast weapon) | after 3 |
   | 5 | Bootstrap landmine proper fix (date-bind + actual_source training filter, same commit) | post-S222 |
   | 6 | Release cut carrying `9dc6d59` (CancelledError fix) | next cut, any time |
   | 7 | Ops debt: main-tree deploy mechanism; prune old WB releases | after verdict |
   | 8 | Operator: wallet.txt out of repo; `$env:TEMP\wb-*.tar.gz`; per-bot worktrees | operator |
   Automation live (2026-07-13/14, ubuntu crontab on VPS, rollback = remove crontab lines):
   nightly 09:17 UTC `/home/ubuntu/wb_research/nightly.sh` — (a) drains the NULL-end pool
   2k/night (CLOB, fill-NULL-only), (b) re-runs the race study to accrue leader-following
   samples; PLUS `*/10 * * * * shadow_book.sh` — **SHADOW-BOOK LOGGER (queue item 3, built
   early per operator 07-14)**: for every active US highest-temp family in its local
   10:00–20:00 window, records METAR running max + leader + 3-deep CLOB books both sides
   to `shadow_books_YYYYMMDD.jsonl`. Read-only. REVIEW AT HANDOFF with canonical data
   (books+outcomes): executable capture vs the mid-price race-study numbers, and maker
   fill feasibility. Logs in `/home/ubuntu/wb_research/`.
   OPERATOR DECISIONS RECORDED (2026-07-14): per-bot worktree DONE — WB local sessions now
   work in `.claude/worktrees/wb-whiteboard` (pins the branch; git refuses checkouts
   elsewhere — flip hazard structurally closed); shadow logger BUILT (above); hygiene
   items (wallet.txt/tarballs/release-prune) HELD by operator; leader-following edge =
   keep VERIFYING with canonical data over time (nightly accrual + logger), no trading
   before the shadow-capture review + S222 verdict.

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

- **Deployed:** WeatherBot on its splinter, release **`20260713_160143`** (cut from `24b2847`;
  rollback target `20260711_002634`). Paper mode, treated as production. Carries everything
  through S227 PLUS: **S229 per-station global EMOS fix** (`24b2847` — the mixed-unit pooled
  corrector killed; see OPEN DECISION 2b), **S229 end_date_iso persistence** (`4fa67a3` —
  WB-discovered markets now store their end date; NULL-end rows healed on rediscovery; also
  activates the designed S172-D10 dynamic exit-cooldown TTL that had been silently inert
  because the key was never populated), and the **S228 latency package** (still flag-OFF /
  default-cadence — activation remains OPEN DECISION 3a). Restart 16:02:29Z (old process
  needed SIGKILL after stop-timeout — pre-existing shutdown slowness, watch it next cut).
  Migrations 079+080 idempotent-reapplied clean.
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

- **2026-07-13 (S229 ROOT FIX, DEPLOYED `20260713_160143`):** `24b2847` — global SAMOS→raw
  conversion pooled climatology across mixed °C/°F stations (avg_clim_mean=43.6) → one
  corrector (9.28, 0.79, σ2.10) for every non-EMOS-ready station → KORD 97.2°F read as
  86.4°F "certainty", phantom NO edges everywhere (0.90+ bin realized ~17%). Fix:
  per-station de-normalization (`_samos_global_by_station` + engine
  `load_global_emos_by_station`), unit-partitioned legacy fallback, shrinkage blends toward
  own-station tuple. 6 defect tests red→green; 428-test weather sweep green. S222
  verification (ran same day on 77 mkts) FAIL verdicts recorded; clock restarts at this
  deploy.
- **2026-07-13 (S229, same release):** `4fa67a3` — WB discovery dropped Gamma `endDate` and
  `_ensure_markets_in_db` never wrote `end_date_iso` (349/447 WB-predicted markets NULL →
  resolved ~2 days late via the S228 48h rule). Tag-fetch dicts carry the date;
  `_parse_end_date` normalizes all spellings; conflict clause heals NULL-end rows on
  rediscovery. 5+1 defect tests red→green. (Kickoff's suspected missing database.py
  backport was DISPROVEN — box database.py = repo blob `08c0b06` 06-18, already has
  `abf5a34`.) Also `9cc067e` manifest repair (S228 follow-ups had stale fingerprint counts).
- **2026-07-11 (S228 ROOT FIX, needs INGESTION deploy):** `56716e8` — resolution backfill
  never checked untraded markets: Phase 2 discovery covered traded_markets/on-chain
  trades/live positions only, so prediction-only markets (132/185 of WB's 07-11 window;
  71% of logged markets) never got `markets.resolution` and prediction_log rows stayed
  unlabeled forever — the S222 gate count and per-day resolution rates (87%→12% decay)
  were starving on this, and the CLAUDE.md #9 \"impossible 8% resolution rate on daily
  markets\" example was this bug. Fix: Phase 2c prediction-log-driven discovery
  (dedicated 150 budget, ended-only, 14-day window, newest-ended first, deduped last);
  BOTH resolution_backfill.py copies; 3 defect tests red→green. Measurement-only fix.
  Deploys with the MAIN tree (`polymarket-ingestion` restart) — NOT the WB release cut.
  Diagnosis trail in OPEN DECISION 2/2a.
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
  zero first; never git-tracked), stale /tmp tarball removed. STILL OPEN (operator): check the local `wallet.txt` original
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
