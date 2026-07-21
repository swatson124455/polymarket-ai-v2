# WB S234 KICKOFF PROMPT (S233 work 2026-07-20; handoff finalized 07-21 ~20Z)

> TIMING NOTE: S233 spanned ~a day of wall-clock. The 3 deploys landed 07-20
> (15:31/15:58/19:01Z); this handoff was finalized 07-21 ~20:04Z. Consequences a
> new session should exploit: **nat_mesh has ~1 day of STAGING accrual already**
> (the validation-before-go-live is READY to run, QUEUE 2), and the **day-4
> mesh-lead grade (`--lead 20260719`) is likely un-gated now** (IEM 1-min backfill
> ~2-day lag → 07-19 covered by ~07-21; probe per-station coverage first, WATCH 2).

Paste this into the next WB session. WB-scoped; standing rules bind (NEVER
quote P&L; no cross-bot vendor/secret/nag bleed; one fix per commit; calibrator
HANDS OFF until ~08-07 — EXCEPT the S233 HKO calibration change, operator-
authorized as a defect fix; WB-ALWAYS-GLOBAL is a hard operator directive — no
US-only filters, ever).

**S233 in one line:** a big "permission on all go" arc — §0 verified clean; day-3
mesh-lead PASS (3rd); THREE deploys (registry additions + busan jma_seamless;
shared `_publish_signal` guard; HKO grounding for Hong Kong); nat_mesh
national-feed collector BUILT + STAGING (go-live gated); peakpass DEFERRED
(viability); yaml cleanup. Current release `20260720_150112`. Nothing is
half-finished — every built thing is either deployed+verified or staged behind
an operator flag. Two open operator decisions: nat_mesh `NAT_MESH_LIVE=1`
go-live (QUEUE 2) and the top-level `_publish_signal` peer master deploy
(SHARED SIGNAL FIX section).

## Tree / branch (READ FIRST — a real landmine hit S232 and still applies)

The main checkout `C:\lockes-picks\polymarket-ai-v2` is on **another bot's
branch** (SB: `claude/sports-bot-owls-backdata`). Work ONLY in the permanent
worktree `C:\lockes-picks\polymarket-ai-v2\.claude\worktrees\wb-whiteboard`
(pinned to `claude/new-whiteboard-session-9b23tq`).

**LANDMINE: the Bash tool's cwd can drift to the main SB checkout, so a
relative-path `git`/`grep` silently reads/writes the WRONG file.** Guardrails:
- `git -C <worktree>` for EVERY git op, or `cd <worktree> &&` at the start of
  each Bash call. Verify `git branch --show-current` = the WB branch before any
  repo write.
- Use ABSOLUTE worktree paths for Read/Edit/Write and for `python`/`pytest`.
- `git status` may show `M base_engine/data/ingestion_error_capture.txt` — a
  runtime artifact, NOT yours; never stage it.

VPS: `ubuntu@18.201.216.0`, key `~/.ssh/wb_deploy2`. **Deployed release:
`20260720_150112`** (S233 made THREE operator-authorized deploys: `20260720_113011`
= 7 registry additions + busan jma_seamless (restart 15:31:08Z); `20260720_115735`
= shared signal_ingestion market_id guard (15:58:06Z); `20260720_150112` = HKO
grounding for Hong Kong (restart 19:01:55Z). Rollback chain: 150112 → 115735 →
113011 → `20260719_195417`). Do NOT deploy without operator sign-off.

## §0 — VERIFY THE S233 HANDOFF (before ANY other work)

1. `bash scripts/wb_resume_check.sh` — expected: ALL PASS except (a) the known
   "agent WORKTREE" location FAIL, (b) at most a deploy-parity WARN (HEAD ahead
   of `20260720_150112` by the trailing LAST_DEPLOY record commit + any S234 doc
   commits — no undeployed CODE). Any OTHER FAIL → STOP and report.
2. VPS spot-checks (read-only, key `~/.ssh/wb_deploy2`):
   - `readlink /opt/polymarket-ai-v2-weather` → `20260720_150112`;
     `systemctl is-active polymarket-weather` → active.
   - 3 flags in the running process env (`sudo cat /proc/$(systemctl show -p
     MainPID --value polymarket-weather)/environ | tr '\0' '\n' | grep WEATHER_`):
     `WEATHER_NOWCAST_ENTRY_ENABLED=true`, `WEATHER_PRIORITY_WAKE_ENABLED=true`,
     `WEATHER_VARIANCE_INFLATION_FACTOR=1.8`.
   - `crontab -l | grep -cE "wb_research|mesh_debias|nat_mesh"` → **6** (S233
     ADDED the `nat_mesh.py` 10-min STAGING cron `4-54/10`. The 5 prior: nightly,
     shadow_book, trade_prints, pws_mesh, mesh_debias. All 6 live under
     `~/wb_research/`. 6 total is CORRECT for S234.)
   - `tail -3 ~/wb_research/pws_mesh_err.log` → 5-min ticks, `wu_fails` low,
     `cities=49`. `wc -l /opt/pa2-weather-feeds/pws_mesh_$(date -u +%Y%m%d).jsonl`
     growing (it is a 5-MIN cron — a flat recount inside 20s is NOT a stall).
   - `tail -3 ~/wb_research/nat_mesh_err.log` → `feeds=6 new_obs=N feed_fails=0
     live=0` ticks (STAGING — see WATCH item 7). `nat_mesh_$(date -u +%Y%m%d).jsonl`
     grows across cities' local days.
   - `mesh_debias.json` in `/opt/pa2-weather-feeds/` fresh (cron 09:15Z daily).
     S233 CONFIRMED it rotated 07-20 09:18Z (`cities=31`); next fire 07-21 09:15Z.
     If the mtime is older than the last 09:15Z, the daily cron did NOT fire = a
     real finding.
   - calibration_check does NOT crash: `cd /opt/polymarket-ai-v2-weather &&
     set -a && . /opt/pa2-shared/.env; set +a; PYTHONPATH=$PWD
     venv/bin/python scripts/calibration_check.py WeatherBot --since
     20260713_160229 --dedup-markets 2>&1 | grep -c ValueError` → 0.
3. Health greps: leak SQL (`predicted_prob >= 0.9995 OR <= 0.0005` since 07-11)
   → 0. `calibration_reload_failed|cal_fit_failed` → **7 is EXPECTED and
   ALREADY CLEARED** — all 7 are a historical 07-11 00:00-00:46Z cluster from
   the pre-`92740f3` asyncpg DataError, ZERO since. Only NEW lines (post
   2026-07-11T00:46Z) are a finding. Do not re-litigate this.

**DB credential gotcha (cost S233 five wasted round-trips):** the shared env has
NO usable `DB_PASSWORD` for psql. Extract from the URL instead:
`PW=$(grep -oP "postgresql[^ ]*://polymarket:\K[^@]+" /opt/pa2-shared/.env | head -1)`
then `export PGPASSWORD="$PW"` and `psql -h 127.0.0.1 -U polymarket -d polymarket`.
Peer auth (no `-h`) FAILS. Also: sourcing `/opt/pa2-shared/.env` in a shell
prints `line 360: true}: command not found` — a JSON-ish value bash tries to
eval. Harmless (systemd's EnvironmentFile parser does not shell-eval, so the
service is unaffected) and it is MB-owned shared infra — DO NOT touch it.

## WHAT S233 DID (verification + Tier-3 registry build + DEPLOY)

- **DEPLOY 1 — release `20260720_113011`** (operator-authorized, live): the 7
  registry additions + busan jma_seamless. Post-deploy verified in the RUNNING
  release venv — registry 114, all 7 resolve to their ICAOs, busan
  local_model=jma_seamless, 3 nowcast flags survived the restart, scan healthy
  (`weatherbot_scan_done active_cities=49 weather_markets=341 groups=109`), no
  station/import errors. Commits: `e49aa01` (rows+tests), `71ba226` (busan
  model), `5dcdb26` (LAST_DEPLOY record).
- **DEPLOY 2 — release `20260720_115735`** (operator-authorized, live):
  the shared `_publish_signal` market_id guard (`754555a`) + record `e06cd66`.
  Post-deploy verified: guard in running code, 0 KeyError since, registry
  additions still intact. See the SHARED SIGNAL FIX section below.
- **DEPLOY 3 — release `20260720_150112`** (operator-authorized, live, CURRENT):
  HKO grounding for Hong Kong (`e2dd243`) + record `5cd91c6` (restart 19:01:55Z).
  Post-deploy verified in the RUNNING venv: HK truth_provider="hko" at HKO HQ
  coords (22.3019,114.1742); HKOClient imports; HK is the ONLY truth_provider
  station; registry 114; scan healthy (`weatherbot_scan_done active_cities=49
  groups=100 groups_with_edge=12`, first scan 71s cold-start); 0 errors/import
  issues; 3 nowcast flags survived. One `trade_event_entry_returned_none` warning
  post-deploy = PRE-EXISTING (21× over prior 3 days) + non-HK, NOT from the HKO
  change. Rollback chain: 150112 → 115735 → 113011 → `20260719_195417`.
- Full §0 verification of the S232 handoff: **all clean.** Details above.
- **Consumed the day-3 mesh-lead grade** (`--lead 20260718`), which S232 left
  IEM-backfill-gated. Confirmed backfill landed via a per-station coverage
  probe vs each station's OWN 07-16 baseline (cross-station row counts are NOT
  comparable — cadence varies per station). **PASS: 8 stations, 87 events,
  72% mesh-led / 49.0 min pooled median lead / 5.7% false-crossings — gates
  hold a 3rd time.** Full table + per-station breakdown in spec
  §"S233 DAY-3 LEAD VERDICT".
- **Two caveats recorded — carry them forward, do not let them get lost:**
  1. Median lead is **monotonically declining** (74.8 → 61.0 → 49.0 min across
     day-1/2/3). Still 3.3x the 15-min gate. Three points cannot separate
     regime from drift. **If day-4/day-5 continue down, investigate before
     treating ~50 min as a stable property.**
  2. The false-crossing improvement is **partly composition, not quality** —
     KSFO was the day-2 hotspot (5 of 10 false crossings) and is ABSENT from
     the day-3 station set (IEM 1-min coverage only 66% of its baseline), as
     are KLAX and KBKF (Buckley has no 1-min ASOS at all — structural). Do NOT
     quote 5.7% as a clean halving vs day-2.

**⚠ STILL 0 nowcast rows** — `weather_nowcast_peak` count in `prediction_log`
= 0 all-time; 0 `weatherbot_nowcast_crossing`/`_shadow` journal lines. The bot
IS alive and predicting normally (each restart resumes scanning ~49 cities and
writing `weather_temperature` prediction_log rows within ~1-2 min), so this is
the nowcast signal specifically being rare — NOT a broken pipeline. Do not
"fix" it. NB: S233 restarted the service 3× (deploys at 15:31, 15:58, 19:01Z);
current release `20260720_150112`.

## SCHEDULED / WATCH (consume as they land)

1. `wb-vif-tune-remeasure` fires **07-24 10:00 ET** — grades the MAIN model
   post-VIF on a fresh clean window since the VIF restart (calibration_check
   EXCLUDES nowcast via c11), recommends VIF→2.0 if still overconfident
   (operator applies), and reports the nowcast shadow-set scorecard via a
   direct model_name query. Consume its notification.
2. **Day-4 / day-5 mesh-lead grades** — `~/wb_research/mesh_validation.py
   --lead 20260719` (and 0720) once IEM 1-min backfill covers (~2 days lag).
   **These are now load-bearing** for caveat 1 above (is the declining median
   lead a trend?). Probe coverage per-station vs its own baseline first.
3. **Maker tilt-vs-control readout ~07-20/22** on the coordination list —
   consume + relay. (Maker owes a c13 audit/purge of the pre-c5 mislabeled
   0.44 lines in `wb_forecasts.jsonl` BEFORE that readout is trustworthy.)
4. First `weatherbot_nowcast_crossing` / `weatherbot_nowcast_shadow` journal
   lines + grader rows under `weather_nowcast_peak`; window-cap + overshoot
   behavior.
5. Cold-start midpoint (~07-24): 7 corrected stations re-learning EMOS.
6. **NEW — the 7 S233 registry cities calibration cold-start:** busan/cape_town/
   guangzhou/jeddah/manila/panama_city/qingdao switched from lowercase dynamic
   keys to their ICAO keys on the 07-20 deploy, so their bias/EMOS history reset
   (the old history was against wrong CENTROID coords — this is corrective, not
   a loss). They fall back to the pooled global path until fresh (forecast,
   actual) pairs accrue under the ICAO. WATCH them re-learn; expect a few days.
   Verify the forecast now queries the airport: the running-venv import/lookup
   proved the stations resolve, but they only surface in journal `city=` lines
   when they generate a trade signal — absence of a per-city line ≠ not processed.
7. **NEW — nat_mesh STAGING accrual:** the national-feed collector runs 10-min on
   the VPS in STAGING (`~/wb_research/nat_mesh_YYYYMMDD.jsonl`, nothing consumes
   it). Check `tail ~/wb_research/nat_mesh_err.log` (expect `feeds=6 new_obs=N
   feed_fails=0 live=0` ticks) and that the staged file grows across cities' local
   days (Europe midday = Asia/AU night and vice versa, so a single tick only
   writes the in-window cities). After ~1 day, run the validation before the
   operator-gated `NAT_MESH_LIVE=1` go-live (see QUEUE item 2).
8. **NEW — HK's first HKO-grounded resolution-day override:** fires only when an
   HK market is <6h to resolution (`weatherbot_metar_resolution_override
   station=VHHH` with the HKO running max, or `hko_runmax_failed_closed` if Redis
   hiccups). Also watch HK's calibration transition (VHHH-keyed rows mixing
   old-airport + new-HKO grounding until aged out — self-correcting).

## SHARED SIGNAL FIX — `_publish_signal` market_id guard (FIXED + WB-DEPLOYED)

The `KeyError: 'market_id'` seen on the 07-20 restart (in `_publish_signal`
handling a market-agnostic `federal_register` signal — carries
`categories_matched`, no `market_id`) was **fixed on operator direction**
(commit `754555a`): a guard skips a signal with no `market_id` cleanly instead
of KeyError-ing on the per-market subscript. Byte-identical for signals that
HAVE a market_id. Defect test proven fail→pass; full suite 4020 green.
**DEPLOYED to polymarket-weather** (release `20260720_115735`, restart
15:58:06Z) — the WB vendored copy. Post-deploy: guard present in running code,
0 KeyError since, registry additions intact.
**⚠ CROSS-BOT PENDING:** the TOP-LEVEL copy (`base_engine/signals/
signal_ingestion.py`, same fix, in the same commit) serves
mirror/esports/ingestion via master — that reach is a PEER-COORDINATED master
merge + deploy, NOT done from WB. Tracked in memory
`project_shared_signal_market_id_fix.md`. Also NOT done (design decision, not
this bug): whether federal_register macro signals should be market-MATCHED by
category (like intl_elections) rather than dropped.

## QUEUE (S233 executed several under operator "permission on all go")

1. **Registry ADDITIONS — DONE S233** (built + DEPLOYED, release `20260720_113011`).
2. **National-feed debias anchors (Item 2) — BUILT + STAGING S233** (commit
   `39435b7`, `scripts/wb_research/nat_mesh.py`). 4 feeds / 6 cities pinned +
   validated live (DWD Berlin/Munich, JMA Tokyo, SG Singapore, BOM Sydney/
   Melbourne; SMN Argentina deferred — stale timestamp). Running as a 10-min
   STAGING cron on the VPS (`4-54/10`, `NAT_MESH_LIVE` unset → writes only
   `~/wb_research/nat_mesh_*.jsonl`, nothing consumes it). Spec §"S233
   NATIONAL-FEED MESH COLLECTOR". **← NEXT ACTION (operator-gated):** after ~1
   day of staging accrual, VALIDATE (dry-run mesh_debias over merged pws+nat,
   confirm each nat source gets a sane offset vs its METAR print + residual_sd
   <1.5F), then flip **GO-LIVE**: `crontab -e` → add `NAT_MESH_LIVE=1` to the
   nat_mesh line's env (or prepend `NAT_MESH_LIVE=1 ` to the command). That
   injects national anchors into the FLAG-ON nowcast data plane. Rollback: unset
   the var / remove the cron line.
3. **HKO integration (Item 1) — DONE + DEPLOYED S233** (release `20260720_150112`,
   commit `e2dd243`; foundation `b75b9a9`). HK grounding redirected VHHH airport
   → HK Observatory HQ, all 3 legs behind `truth_provider="hko"` (byte-identical
   for every other city). Adversarial review found + FIXED a real fail-OPEN-on-
   Redis defect (now fails closed). 8 dispatch + 15 client tests; suite 4061;
   post-deploy verified live. **WATCH:** HK's first resolution-day override via
   HKO (fires when an HK market is <6h to resolution) + the calibration transition
   (HK rows keyed VHHH mix old-airport/new-HKO grounding until aged out). Karachi
   deferred (no open OPMR/PMD source; OPKC = the S231 trap).
4. **Phase-2 second signal `weather_nowcast_peakpass` — DO NOT BUILD YET** (Item
   3). Deferred on two independent grounds:
   (a) signal 1 (`weather_nowcast_peak`) has fired 0 times → the shared data
   plane peakpass reuses has zero live-graded validation.
   (b) **VIABILITY, not signal quality** (corrected Study B, spec :615-620): at
   the RIGHT stations the peak-passed LOCK is real — false-locks L1 0/579
   (0.00%), L2 1/579 (0.17%), both PASS the <1% gate (the old ~9% false-lock
   number is RETRACTED — it was the 3-miswired-US-station artifact, spec
   :564-571; do NOT re-cite it). Peakpass fails because **supply vanishes after
   certainty**: fills ≤0.97 post-lock exist on only ~1% of locked days, so the
   0.68→1.00 drift-capture buy has nothing to fill against; the 5 windows that
   filled were Study-C-class rare events (accrual-watch). The drift leg also
   wants resting-maker machinery the bot lacks (weather_bot.py:4605-4607). Next
   action if revisited = OFFLINE research (does denser mesh / any regime move the
   supply picture), NOT bot code.
5. Cleanups: stale `data/city_icao_mapping.yaml` DELETED S233 (orphaned generated
   artifact, 0 code refs). `has_asos_1min` dead flag DELIBERATELY LEFT — it is
   dead (0 `.has_asos_1min` reads) but is SET on ~90 station rows, so removing it
   is a ~180-line churn across both shared registry copies for zero functional
   gain (surgical discipline: not worth the blast radius). Leave it, or bundle
   its removal into a future registry change that already touches every row.

## CROSS-BOT FLAGS TO RELAY (WB did NOT touch other bots — SCOPE + RULE ONE-A)

⚠ **RULE ONE (MB right-of-way) was RESCINDED 2026-07-20 — all bots are PEERS on
shared resources** (deploys, master merges, shared modules, `/opt/pa2-shared/.env`,
VPS capacity, operator bandwidth). Coordinate on contention; there is NO default
winner. Any older doc/commit text saying "MB decides" or "MB/operator action —
RULE ONE" is STALE FRAMING, including the body of commit `e37d666` and spec
lines ~1160/1164 — do not re-derive priority from it. What still binds: Layer-1
scope (a bot-scoped session works only on its own bot's code) and **RULE ONE-A**
(WB/EB sessions never touch MB). Those are separate rules and are unchanged.

- **Shared RedisCache root fix** — memory `project_shared_redis_get_root_fix.md`:
  the top-level copy still needs to land on master + deploy, but that is now a
  PEER-COORDINATED action, not MB's call — whichever session lands it coordinates
  the deploy (deploy.sh restarts mirror/esports/ingestion, so it affects several
  bots' runtimes). EB/Maker/SB apply the 3-line change to their branch's copy.
  Backward-compatible (default False = byte-identical legacy behavior), and
  capability-only until a caller opts in — so no bot is forced to deploy for it.
- **c13 (Maker):** the pre-c5 Maker feed holds mislabeled ~0.44 lines (07-19);
  Maker to audit/purge `wb_forecasts.jsonl` before the tilt readout.
- **c12 (MB):** shared `prediction_log` calibrators (`base_engine/features/
  calibration.py` + `database.py`) are unfiltered by bot_name/model_name — MB
  to filter.

## STANDING OPERATOR REMINDERS (echo EVERY handoff until confirmed)

1. ROTATE trading wallet `0xd6a5…627F` (operator-only).
2. VPS release pruning (many WB releases now).
3. NWWS-OI application (free).
4. API signups: **KMA (Seoul minutely — best single upgrade; RKSI/Seoul mesh
   still weak)**, WU key (retires the pws_mesh web-key dependency AND is a
   Phase-2 acceptance gate), Synoptic, MADIS, Météo-France/Met Office/etc.

## CONTEXT POINTERS

- `docs/WB_NOWCAST_CAPTURE_SPEC.md` — THE anchor: every S231/S232/S233 verdict,
  the Phase-2 design/build, all 3 review passes, the shared-redis fix, in order.
- `docs/WEATHER_STATUS.md` — OPEN DECISIONS 0 (nowcast flag ON + day-3 verdict)
  and 2 (S222/VIF).
- Memory `project_wb_next_pointer.md` + `project_shared_redis_get_root_fix.md`.
