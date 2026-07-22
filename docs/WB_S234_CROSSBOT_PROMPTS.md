# WB S234 → CROSS-BOT NOTIFICATION PROMPTS (written 2026-07-22 by the WB session)

WB deployed **master release `20260721_232241`** on operator direction. The
previous master release was `20260622_225148` (2026-06-22), so this shipped
**~a month of every session's accumulated master work — 41 commits** — in one
release, plus three shared fixes WB landed the same night.
(Range `a6efa68..4b50ce7`. `a6efa68` is INFERRED as the old release's commit —
the release dir has no SHA marker; it is the last master commit before that
release's stamp. Widen if something looks missing.)

**Which services actually changed** (verified via systemd drop-ins, not assumed):

| service | drop-in? | runs | effect of this deploy |
|---|---|---|---|
| `polymarket-mirror` | **no** | `/opt/polymarket-ai-v2` (master) | **got the full month + 3 shared fixes** |
| `polymarket-ingestion` | **no** | `/opt/polymarket-ai-v2` (master) | **got the full month + 3 shared fixes** |
| `polymarket-esports` | yes (`00-splinter.conf`) | `/opt/polymarket-ai-v2-esports` | **code UNCHANGED** (still `20260618_104949`); restarted only |
| `polymarket-weather` | yes (`00-splinter.conf`) | `/opt/polymarket-ai-v2-weather` | code unchanged (WB splinter `20260721_230638`) |

WB's post-deploy check: 4/4 services active, **0 error-level lines on all four**,
mirror/esports/ingestion all doing real work. That is a ~12-minute observation
window from a WB session — it is NOT a substitute for each bot's own verification.

Rollback for everything below: `deploy/rollback.sh`, or flip
`/opt/polymarket-ai-v2` back to `/opt/pa2-releases/20260622_225148` and restart
the four services.

---

## PROMPT 1 — MirrorBot session (HIGHEST PRIORITY)

```
MB session. A WB session deployed master release 20260721_232241 on 2026-07-22
~03:32Z (operator-directed). MirrorBot has NO systemd splinter drop-in, so
polymarket-mirror runs /opt/polymarket-ai-v2 = the master tree, and it therefore
received BOTH:

  (a) ~a month of accumulated master work — the previous master release was
      20260622_225148 from 2026-06-22, so everything every session landed on
      master since then went live in one shot; and
  (b) three shared fixes WB landed the same night:
        0e26f70  RedisCache.get/set opt-in raise_on_error (capability-only,
                 default False = byte-identical legacy behaviour)
        1950501  _publish_signal skips a market-agnostic signal instead of
                 KeyError-ing on signal["market_id"]
        3ca2270  c12 — shared prediction_log calibrators now EXCLUDE WB nowcast
                 rows

c12 IS THE ONE THAT CHANGES MIRRORBOT'S BEHAVIOUR. It adds
`AND COALESCE(model_name,'') NOT LIKE '%nowcast%'` to 8 pooled prediction_log
readers across both module copies:
  base_engine/features/calibration.py — FavoriteLongshotCalibrator
    .fit_from_prediction_log, DomainCalibrator._fit_category,
    FocalTemperatureCalibrator.fit_from_prediction_log
  base_engine/data/database.py — get_recent_performance_from_prediction_log,
    get_recent_brier_from_prediction_log, get_recent_resolved_predictions,
    get_model_live_performance, get_recent_resolved_for_blend
MirrorBot consumes these via bots/mirror_calibration.py (FocalTemperature) and
base_engine/prediction/prediction_engine.py.

Sizing of the change, measured read-only 2026-07-21:
  - resolved 90d pool: mirror_split_rtds 795,642 vs weather_nowcast_peak 40, and
    the calibrator queries take the most recent 5,000 — so the effect on the
    fitted curves today is negligible.
  - the real exposure is the recent-N readers (n=20/50/100), where a nowcast
    resolve burst could dominate a window. That is what the fix protects.
  - model_name is NULL on 0 of 3,606,154 rows, so the predicate drops nothing
    today; it is COALESCE-wrapped anyway so a future NULL is KEPT.
Defect tests fail->pass (14 fail on pre-fix master sources, 19 pass after); full
suite at the time 3991 passed / 1 pre-existing unrelated failure (since fixed).

WHAT WB COULD NOT DO (RULE ONE-A — a WB session never reads MB telemetry):
verify MirrorBot's own live behaviour. WB only established that the service is
active, scanning, and logging zero error-level lines in a ~12-minute window.

PLEASE DO:
1. Verify MirrorBot post-deploy on your own terms — scan output, entries/exits,
   position reconciliation, and anything the month of master changes touches.
   "running=True" is not evidence; check actual scan output.
2. Sanity-check the calibrator after c12 — confirm the fits still populate and
   nothing regressed now that nowcast rows are excluded.
3. Review what else landed in that month-long release for MB-relevant changes —
   it is 41 commits:
     git log --oneline a6efa68..4b50ce7
   NOTE on a6efa68: the release dir carries no SHA marker, so this is INFERRED,
   not proven — a6efa68 ("fix(mirror,watchdog): reap live phantoms ... S249") is
   the last master commit before the 20260622_225148 release stamp (22:17 EDT vs
   a 22:51 stamp; stamps are operator LOCAL time). Treat the range as
   approximately-right, and widen it by a commit or two if something looks
   missing. The first commit in that range is itself a MirrorBot fix, so the
   window is very likely MB-relevant from the start.
4. If anything looks wrong: deploy/rollback.sh, or flip /opt/polymarket-ai-v2
   back to /opt/pa2-releases/20260622_225148 and restart the 4 services.

Detail + evidence: WB spec docs/WB_NOWCAST_CAPTURE_SPEC.md §"S234 CROSS-BOT
RELAY EXECUTION" and §"S234 ARC 4", on branch
claude/new-whiteboard-session-9b23tq.
```

---

## PROMPT 2 — Maker session (STOP — do not act on the old c13 relay)

```
Maker session. Two things from the WB session of 2026-07-21/22.

1. c13 IS A VERIFIED NO-OP. DO NOT PURGE THE FEED.
   The standing relay said /opt/pa2-maker-feeds/wb_forecasts.jsonl holds pre-c5
   mislabeled ~0.44 nowcast lines that Maker should audit/purge before the
   tilt-vs-control readout. WB audited the actual file and the premise does not
   hold. Two independent lines of evidence:
     - Feed audit (17,220 lines spanning 2026-07-17T18:57:36Z ->
       2026-07-22T02:06:23Z): across 07-17, 07-18 and 07-19 — the ENTIRE pre-c5
       window — there are ZERO lines with prob in [0.42, 0.46]. Exactly one line
       in the whole file matches the c13 signature (model=weather_temperature,
       0.43<=p<=0.45): Jeddah, logged 2026-07-20T05:00:48Z, prob 0.4438.
     - The nowcast signal's FIRST prediction_log row is 2026-07-20 19:20:31
       (model_name weather_nowcast_peak). c5 (ebad791) shipped 2026-07-19 in
       release 20260719_150142. The signal fired for the first time more than a
       DAY AFTER c5 was fixed, so the pre-c5 exposure window contained no
       nowcast rows to leak.
   The single 0.4438 Jeddah line predates the first nowcast row by ~14h, so it
   cannot be a nowcast row — it is an ordinary main-model forecast that happens
   to sit near 0.44 (nothing is special about that value for the main model).
   PURGING IT WOULD DELETE GENUINE DATA AND BIAS YOUR OWN TILT STUDY.
   => Treat c13 as closed-empty. The tilt readout is NOT blocked on it.

2. The shared RedisCache raise_on_error fix is now ON MASTER (0e26f70) and
   DEPLOYED as part of master release 20260721_232241. It is backward-compatible
   (default raise_on_error=False = byte-identical legacy behaviour) and
   capability-only until a caller opts in. If you applied it to your own branch
   copy, no action; if not, master now has it.

ALSO STILL OWED TO WB (unchanged, no deadline): the tilt-vs-control readout, and
answers to WB's 3 questions in
AGENT_HANDOFF_2026-07-17_MAKER_WB_FORECAST_TILT_PROPOSAL.md.

FYI — the WB->Maker feed nearly broke in this deploy and was saved by a fix:
master's committed deploy/polymarket-weather.service was MISSING
/opt/pa2-maker-feeds from ReadWritePaths, and deploy.sh overwrites the live unit
on every master deploy. ProtectSystem=strict would have made your drop read-only
and WeatherBot's export swallows all errors by design, so the feed would have
stopped SILENTLY. Fixed on master as 4b50ce7 before deploying; verified after —
wb_forecasts.jsonl grew 17,734 -> 17,754 lines across the deploy. If the feed
ever goes quiet, check that ReadWritePaths line first.
```

---

## PROMPT 3 — EsportsBot session (low impact, verify only)

```
EB session. A WB session deployed master release 20260721_232241 on 2026-07-22
~03:32Z (operator-directed), the first master release since 20260622_225148
(2026-06-22).

GOOD NEWS — EB's CODE WAS NOT CHANGED. polymarket-esports has a systemd splinter
drop-in (/etc/systemd/system/polymarket-esports.service.d/00-splinter.conf)
overriding WorkingDirectory + ExecStart to /opt/polymarket-ai-v2-esports, and it
held through the deploy. EB is still on splinter release 20260618_104949.
WB also verified EB's live unit ReadWritePaths matches master's committed copy
exactly, so the unit reinstall stripped nothing from EB. (WeatherBot was NOT so
lucky — master's committed weather unit was missing a path WB needed; that was
found and fixed before deploying. Worth knowing the failure mode exists: if EB
ever adds a custom ReadWritePaths/EnvironmentFile to its LIVE unit, it MUST also
go into deploy/polymarket-esports.service on master or the next master deploy
silently reverts it.)

WHAT DID HAPPEN: deploy.sh stops and starts all four services, so
polymarket-esports was RESTARTED. WB's post-deploy check saw it active with 6
scan/discovery events and 0 error-level lines, and deploy.sh's Gate 3 soft-warn
("scan_ms not seen from all bots in 420s") is attributed by the script itself to
EB v2 cold-start pipeline fit (~5.5 min).

PLEASE DO: confirm EB came back cleanly on your own terms — in particular
pipeline_ready after the cold start, and that the restart did not disturb
whatever state your current halt/rebuild work depends on.
  journalctl -u polymarket-esports --since "2026-07-22 03:25" | grep pipeline_ready
```

---

## PROMPT 4 — SportsBot / other branch sessions (FYI)

```
FYI from the WB session. Master moved on 2026-07-22: HEAD is now 4b50ce7 and
master release 20260721_232241 is deployed (previous release was from
2026-06-22, so ~a month of accumulated work went live at once).

New on master since you last rebased, likely relevant:
  0e26f70  shared RedisCache.get/set opt-in raise_on_error (SB already carries
           this on claude/sports-bot-owls-backdata as 9198e52 — no action)
  1950501  _publish_signal skips market-agnostic signals instead of KeyError
  3ca2270  c12 shared prediction_log calibrators exclude WB nowcast rows
  00372e8  tests: TestDateParsing was calendar-fragile and blocked deploy.sh's
           pytest preflight — if you have your own copy of that test, it has the
           same latent bug (it asserted date(now.year,...) but _parse_date rolls
           a >180-day-past date to next year)
  4b50ce7  deploy/polymarket-weather.service keeps /opt/pa2-maker-feeds writable

Rebase against master when convenient. Nothing here is urgent for SB.

⚠ Reminder: SB holds the main checkout C:\lockes-picks\polymarket-ai-v2 on
branch claude/sports-bot-owls-backdata. WB works only in its worktree and has
not touched the main checkout.
```
