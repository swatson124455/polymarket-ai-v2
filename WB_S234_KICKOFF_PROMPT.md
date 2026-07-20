# WB S234 KICKOFF PROMPT (written at S233 close, 2026-07-20 ~00:4xZ)

Paste this into the next WB session. WB-scoped; standing rules bind (NEVER
quote P&L; no cross-bot vendor/secret/nag bleed; one fix per commit; calibrator
HANDS OFF until ~08-07; WB-ALWAYS-GLOBAL is a hard operator directive — no
US-only filters, ever).

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
`20260719_195417`** (unchanged by S233 — that session made ZERO deploys and
ZERO bot-code changes). Do NOT deploy without operator sign-off.

## §0 — VERIFY THE S233 HANDOFF (before ANY other work)

1. `bash scripts/wb_resume_check.sh` — expected: ALL PASS except (a) the known
   "agent WORKTREE" location FAIL, (b) a deploy-parity WARN (HEAD is ahead of
   `20260719_195417` by DOC-ONLY commits — S233 shipped no code). Any OTHER
   FAIL → STOP and report.
2. VPS spot-checks (read-only, key `~/.ssh/wb_deploy2`):
   - `readlink /opt/polymarket-ai-v2-weather` → `20260719_195417`;
     `systemctl is-active polymarket-weather` → active.
   - 3 flags in the running process env (`sudo cat /proc/$(systemctl show -p
     MainPID --value polymarket-weather)/environ | tr '\0' '\n' | grep WEATHER_`):
     `WEATHER_NOWCAST_ENTRY_ENABLED=true`, `WEATHER_PRIORITY_WAKE_ENABLED=true`,
     `WEATHER_VARIANCE_INFLATION_FACTOR=1.8`.
   - `crontab -l | grep -cE "wb_research|mesh_debias"` → **5** (NOTE: the S233
     kickoff said "4 + mesh_debias"; the mesh_debias line lives under the
     `~/wb_research/` path so it matches both greps. 5 total is CORRECT.)
   - `tail -3 ~/wb_research/pws_mesh_err.log` → 5-min ticks, `wu_fails` low,
     `cities=49`. `wc -l /opt/pa2-weather-feeds/pws_mesh_$(date -u +%Y%m%d).jsonl`
     growing (it is a 5-MIN cron — a flat recount inside 20s is NOT a stall).
   - `mesh_debias.json` in `/opt/pa2-weather-feeds/` fresh (cron 09:15Z daily).
     ⚠ At S233 close it was written 07-19 13:25Z with the next fire due
     07-20 09:15Z — **confirm it actually rotated on 07-20**; if the mtime is
     still 07-19, the daily cron did NOT fire and that IS a real finding.
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

## WHAT S233 DID (verification/watch session — no code, no deploy)

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
IS alive and predicting (82 `prediction_log` rows in the first ~20 min after
the 23:54:45Z restart, all `weather_temperature`), so this is the nowcast
signal specifically being rare — NOT a broken pipeline. Do not "fix" it.

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

## QUEUE (all need operator go where noted — NOT started in S233 per operator)

1. **Registry ADDITIONS (Tier-3, operator sign-off):** evidence complete in
   spec §"S232 REGISTRY-ADDITIONS EVIDENCE" — add RKPK/FACT/ZGGG/OEJN/RPLL/
   MPMG/ZSQD. **Karachi = OPMR has NO METARs** (WU-only class, like HK) — do
   NOT add OPKC. Build station rows + defect tests → present for sign-off →
   release cut.
2. **HKO integration** for Hong Kong (truth = HK Observatory open data) — pairs
   with the Karachi non-METAR class as one work package.
3. Wire the 6 verified national feeds (DWD/JMA/SG/HKO/BOM/SMN-AR) as debias
   anchors.
4. Phase-2 second signal `weather_nowcast_peakpass` (design in spec; separate
   build).
5. Cleanups: `has_asos_1min` dead flag; stale `data/city_icao_mapping.yaml`.

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
