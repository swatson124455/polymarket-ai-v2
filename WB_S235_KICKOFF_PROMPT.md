# WB S235 KICKOFF PROMPT (S234 work 2026-07-21, ~20:1x–21:0xZ)

Paste this into the next WB session. WB-scoped; standing rules bind (NEVER
quote P&L; no cross-bot vendor/secret/nag bleed; one fix per commit; calibrator
HANDS OFF until ~08-07 — the only exception taken remains the S233 HKO change;
WB-ALWAYS-GLOBAL is a hard operator directive — no US-only filters, ever).

**S234 in one line:** read-only verification/watch session, ZERO code changes,
ZERO deploys — §0 all clean; day-4 mesh-lead **PASS (4th)** and the S233
declining-lead caveat RESOLVES BENIGN; nat_mesh validation **DRY-RUN PASS**
(go-live ready, operator-gated); first-ever nowcast shadow lines + 40
`weather_nowcast_peak` rows; HK's first HKO-grounded override chain fired clean;
one NEW defect found and reported (ERA5 bootstrap str-date, report-only under
calibrator hands-off).

## Tree / branch (READ FIRST — landmine still applies)

Main checkout `C:\lockes-picks\polymarket-ai-v2` is on ANOTHER bot's branch
(SB). Work ONLY in the permanent worktree
`C:\lockes-picks\polymarket-ai-v2\.claude\worktrees\wb-whiteboard`
(pinned `claude/new-whiteboard-session-9b23tq`).
- `git -C <worktree>` for EVERY git op; ABSOLUTE worktree paths for
  Read/Edit/Write/python/pytest; verify `git branch --show-current` before any
  repo write.
- `M base_engine/data/ingestion_error_capture.txt` in the MAIN checkout is a
  runtime artifact — never stage it.

VPS: `ubuntu@18.201.216.0`, key `~/.ssh/wb_deploy2`. **Deployed release:
`20260720_150112`** (unchanged all of S234; rollback chain 150112 → 115735 →
113011 → `20260719_195417`). Do NOT deploy without operator sign-off.

**DB credential gotcha:** no usable `DB_PASSWORD` in the shared env — extract:
`PW=$(grep -oP "postgresql[^ ]*://polymarket:\K[^@]+" /opt/pa2-shared/.env | head -1)`
then `export PGPASSWORD="$PW"; psql -h 127.0.0.1 -U polymarket -d polymarket`.

## §0 — VERIFY THE S234 HANDOFF (before ANY other work)

1. `bash scripts/wb_resume_check.sh` — expected: ALL PASS except (a) the known
   "agent WORKTREE" location FAIL, (b) at most a deploy-parity WARN (HEAD ahead
   of `20260720_150112` by S234's doc-only commits — no undeployed CODE). Any
   OTHER FAIL → STOP and report.
2. VPS spot-checks (read-only): `readlink /opt/polymarket-ai-v2-weather` →
   `20260720_150112`; service active; the 3 WEATHER_ flags in the running
   process env (`WEATHER_NOWCAST_ENTRY_ENABLED=true`,
   `WEATHER_PRIORITY_WAKE_ENABLED=true`, `WEATHER_VARIANCE_INFLATION_FACTOR=1.8`);
   `crontab -l | grep -cE "wb_research|mesh_debias|nat_mesh"` → 6; pws_mesh
   5-min ticks `cities=49 wu_fails` low; nat_mesh ticks `feeds=6 feed_fails=0`
   (live=0 unless the operator flipped go-live — see QUEUE 1); mesh_debias.json
   mtime after the last 09:15Z; calibration_check `grep -c ValueError` → 0.
3. Health greps: leak SQL (`predicted_prob >= 0.9995 OR <= 0.0005` since 07-11)
   → 0. `cal_fit_failed|calibration_reload_failed` — the historical 07-11
   00:00-00:46Z cluster is EXPECTED/cleared (NB: `--since "2026-07-11 00:46"`
   catches the cluster's last line at 00:46:26 — that ONE line is NOT a
   finding); only lines after 00:47 are findings.
4. NB S234 verified all of the above clean at ~20:1xZ 07-21; §0 is
   re-verification, not re-investigation.

## WHAT S234 DID (read-only; all detail in spec §S234 sections)

- **Day-4 mesh-lead grade (--lead 20260719): PASS, 4th consecutive.** 10
  stations (coverage probe: 87-206% of each station's own 07-16 baseline;
  KSFO + KLAX back in), 99 events, 77% led / 63.0 min pooled median / 15.2%
  FCs. **Caveat-1 (declining lead) RESOLVED BENIGN** — 49.0→63.0 bounce.
  FC 15.2% vs day-3 5.7% is the predicted composition effect (hotspot KSFO
  back in set) and is inside the <20% gate. Spec §"S234 DAY-4 LEAD VERDICT".
- **nat_mesh validation (QUEUE-2): DRY-RUN PASS.** Isolated merged-file run of
  the EXACT production mesh_debias (paths repointed to /tmp/natval; live table
  mtime verified untouched). All 6 nat sources sane (max |scalar| 0.9F,
  JMA/BOM ~0.0F); nat-only cities EDDB/YSSY/YMML residual_sd 0.48-0.54F —
  Berlin/Sydney/Melbourne become NEW debias cities on go-live; merged cities
  (EDDM/RJTT/WSSS) all improve but stay dropped (>1.5F, PWS noise) — go-live
  cannot degrade any published city. Spec §"S234 NAT_MESH VALIDATION".
- **WATCH landings:** first 91 `weatherbot_nowcast_shadow` lines (KLAX 70,
  KDAL 20, KORD 1; ALL reason=repriced; 0 entry crossings) + first 40
  `weather_nowcast_peak` prediction_log rows. HK's first HKO-grounded
  resolution-day override chain fired (07-21 07:30-09:40Z, running max 28→29 C,
  0 fail-closed lines). 7 new registry cities all in the 49-city universe and
  accruing ICAO-keyed calibration pairs (2-5 rows each as of 07-21).
- **NEW DEFECT — REPORTED, NOT FIXED (calibrator hands-off):** ERA5 bootstrap
  INSERT binds target_date as str → asyncpg DataError on every row
  (weather_bot.py:1500, `"td": target_date_str`) — same S227 class as 92740f3,
  missed call site. bootstrap_gfs rows frozen at 314 (2026-05-31..06-12, zero
  since); first observed failure 07-14. Impact bounded: cold-starts lose the
  instant ERA5 seed, learn from live pairs + pooled fallback only. Fix is a
  1-line date-parse mirroring 92740f3 — NEEDS OPERATOR GO (calibration infra).
- **KBKF watch:** all 74 station_unhealthy lines in 24h are KBKF (~3x mixed
  baseline) — Denver METAR grounding degraded. Data-source issue, no action.

## SCHEDULED / WATCH (consume as they land)

1. **Day-5 mesh-lead grade** — `mesh_validation.py --lead 20260720` once IEM
   1-min covers 0720 (probe per-station vs own baseline first; S234 measured
   0-9% = not ready; expect ~07-22/23). Also day-6+ if the operator wants the
   series continued.
2. `wb-vif-tune-remeasure` fires **07-24 10:00 ET** — MAIN-model post-VIF grade
   + VIF→2.0 recommendation + the nowcast shadow scorecard (now HAS data: 40
   rows / 91 shadows). Consume its notification.
3. **Shadow accrual**: `weatherbot_nowcast_shadow` / first
   `weatherbot_nowcast_crossing` ENTRY lines; window-cap + overshoot behavior
   on any entry. All shadows so far are reason=repriced — the S230 "hole open
   at the print" question accruing live evidence.
4. **KBKF unhealthy streak** — did it recover? If still 100% unhealthy after
   days, it's a station-registry/data-source question (NOT a blacklist — fix
   grounding, never remove the city).
5. Maker tilt-vs-control readout (still pending Maker's c13 purge) — consume +
   relay when it lands on the coordination list.
6. 7-city + HK EMOS cold-start continues (ICAO-keyed pairs accruing; HK VHHH
   rows mixing old-airport/new-HKO until aged out — self-correcting).
7. HK resolution-day overrides now routine — spot-check one per session while
   fresh (station=VHHH lines + zero hko_runmax_failed_closed).

## QUEUE (operator-gated actions, in rough priority)

1. **nat_mesh GO-LIVE — VALIDATED, awaiting operator go.** Flip: `crontab -e`
   → prepend `NAT_MESH_LIVE=1 ` to the nat_mesh line. That injects national
   anchors into the FLAG-ON nowcast data plane (adds Berlin/Sydney/Melbourne
   debias rows; improves Munich/Tokyo/Singapore). Rollback: unset the var.
   After go-live: verify `live=1` in nat_mesh_err ticks + `grep -c "nat:"
   /opt/pa2-weather-feeds/pws_mesh_$(date -u +%Y%m%d).jsonl` > 0 + next 09:15Z
   mesh_debias rotation carries `nat:` sources.
2. **ERA5 bootstrap str-date fix** — 1-line + defect test, blocked on operator
   go (calibrator hands-off). When authorized: parse `target_date_str` to
   `datetime.date` at weather_bot.py:1500 (mirror 92740f3), grep for OTHER
   str-date binds in the same file (adjacent-shape completeness, P16), defect
   test fail→pass, full suite, deploy with sign-off.
3. **Peakpass (Phase-2 signal 2) — STILL DO NOT BUILD** (viability fail:
   supply vanishes after certainty; ~9% false-lock RETRACTED, do not re-cite).
   Next action if revisited = OFFLINE supply research, not bot code.
4. Per-source (vs per-city) debias drop rule — design note from the nat_mesh
   validation (spec §"S234 NAT_MESH VALIDATION"); needs its own review; only
   worth it if nat proves itself live.

## CROSS-BOT FLAGS TO RELAY (WB does NOT act — peer rules + RULE ONE-A)

- **Shared RedisCache raise_on_error root fix** (memory
  `project_shared_redis_get_root_fix.md`): top-level copy still needs a peer
  master merge + deploy; EB/Maker/SB apply per branch. Backward-compatible.
- **Top-level `_publish_signal` market_id guard** (memory
  `project_shared_signal_market_id_fix.md`): WB vendored copy DEPLOYED
  (`754555a`); top-level `base_engine/signals/signal_ingestion.py` PENDING a
  peer master merge + deploy for mirror/esports/ingestion.
- **c13 (Maker):** pre-c5 mislabeled ~0.44 lines in `wb_forecasts.jsonl` —
  Maker to audit/purge BEFORE the tilt readout is trustworthy.
- **c12 (MB):** shared prediction_log calibrators unfiltered by
  bot_name/model_name — MB to filter.

## STANDING OPERATOR REMINDERS (echo EVERY handoff until confirmed)

1. ROTATE trading wallet `0xd6a5…627F` (operator-only).
2. VPS release pruning (many WB releases now).
3. NWWS-OI application (free).
4. API signups: **KMA (Seoul minutely — best single upgrade)**, WU key (retires
   the pws_mesh web-key dependency + Phase-2 acceptance gate), Synoptic, MADIS.

## CONTEXT POINTERS

- `docs/WB_NOWCAST_CAPTURE_SPEC.md` — THE anchor; S234 sections at the tail
  (day-4 verdict, nat_mesh validation, watch landings + the bootstrap defect).
- `docs/WEATHER_STATUS.md` — OPEN DECISIONS 0 (day-4 verdict + first live
  nowcast evidence) and 2 (S222/VIF).
- Memory `project_wb_next_pointer.md`.
