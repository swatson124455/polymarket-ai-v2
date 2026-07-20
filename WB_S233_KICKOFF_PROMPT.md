# WB S233 KICKOFF PROMPT (written at S232 close, 2026-07-19 ~20:0xZ)

Paste this into the next WB session. WB-scoped; standing rules bind (NEVER
quote P&L; no cross-bot vendor/secret/nag bleed; one fix per commit; calibrator
HANDS OFF until ~08-07; WB-ALWAYS-GLOBAL is a hard operator directive — no
US-only filters, ever).

## Tree / branch (READ FIRST — a real landmine hit this session)

The main checkout `C:\lockes-picks\polymarket-ai-v2` is on **another bot's
branch** (SB: `claude/sports-bot-owls-backdata`). Work ONLY in the permanent
worktree `C:\lockes-picks\polymarket-ai-v2\.claude\worktrees\wb-whiteboard`
(pinned to `claude/new-whiteboard-session-9b23tq`).

**LANDMINE (S232): the Bash tool's cwd can drift to the main SB checkout, so a
relative-path `git`/`grep` silently reads/writes the WRONG file.** Guardrails:
- `git -C <worktree>` for EVERY git op, or `cd <worktree> &&` at the start of
  each Bash call. Verify `git branch --show-current` = the WB branch before any
  repo write.
- Use ABSOLUTE worktree paths for Read/Edit/Write and for `python`/`pytest`.
- `git status` in the worktree may show `M base_engine/data/ingestion_error_capture.txt`
  — that is a runtime artifact, NOT yours; never stage it.

VPS: `ubuntu@18.201.216.0`, key `~/.ssh/wb_deploy2`. **Deployed release:
`20260719_195417`** (rollback code = the prior release listed in
`deploy/LAST_DEPLOY.json`). WB splinter deploy = `git archive HEAD` →
`deploy/wb-release-cut.sh` (restarts polymarket-weather; ~5 clean restarts this
session). Do NOT deploy without operator sign-off.

## §0 — VERIFY THE S232 HANDOFF (before ANY other work)

1. `bash scripts/wb_resume_check.sh` — expected: ALL PASS except (a) the known
   "agent WORKTREE" location FAIL, (b) at most a deploy-parity WARN from a
   trailing doc commit. Any OTHER FAIL → STOP and report.
2. VPS spot-checks (read-only, key `~/.ssh/wb_deploy2`):
   - `readlink /opt/polymarket-ai-v2-weather` → `20260719_195417`; `systemctl
     is-active polymarket-weather` → active.
   - 3 flags in the running process env (`sudo cat /proc/$(systemctl show -p
     MainPID --value polymarket-weather)/environ | tr '\0' '\n' | grep WEATHER_`):
     `WEATHER_NOWCAST_ENTRY_ENABLED=true`, `WEATHER_PRIORITY_WAKE_ENABLED=true`,
     `WEATHER_VARIANCE_INFLATION_FACTOR=1.8`.
   - `crontab -l | grep -c wb_research` → 4 (+ `mesh_debias` = 5 total wb crons);
     `tail -3 ~/wb_research/pws_mesh_err.log` → 5-min ticks, `wu_fails` low.
   - `wc -l /opt/pa2-weather-feeds/pws_mesh_$(date -u +%Y%m%d).jsonl` growing;
     `mesh_debias.json` fresh (cron 09:15Z).
   - calibration_check does NOT crash (S232 audit fixed a ValueError):
     `cd /opt/polymarket-ai-v2-weather && set -a && . /opt/pa2-shared/.env; set +a;
     PYTHONPATH=$PWD venv/bin/python scripts/calibration_check.py WeatherBot
     --since 20260713_160229 --dedup-markets 2>&1 | grep -c ValueError` → 0.
3. Health greps (~1 min): `calibration_reload_failed|cal_fit_failed` → 0;
   leak SQL (`predicted_prob >= 0.9995` since 07-11) → 0.

## WHAT S232 SHIPPED (do NOT re-derive; read the spec §S232 blocks)

Phase-2 mesh-nowcast signal is **BUILT + FLAG ON (paper)** after a day-1 and
day-2 mesh-lead PASS (60%/74.8min/10.8% and 62%/61.0min/11.2%). Flag flipped on
the operator's standing order; kill = both flags false + restart. Self-limiting
to non-dropped debias-table cities, `$50`/(station,date) window cap, model_name
`weather_nowcast_peak` (graded separately). VIF raised 1.4→1.8 (S222 re-cut
N=627 = RETIRE NOTHING; the tune targets non-EMOS overconfidence).

**Hardened by THREE adversarial review passes + a shared root fix** (all
deployed, full suite 4011 green): c1 (HIGH: Redis window-cap failed OPEN →
raw-handle fail-closed), c5 (Maker-feed leak), c2 (prediction_log dedup by
model_name), c9 (city-Brier sizing-dampener contamination), c11 (calibration_check
read-side dedup + nowcast exclusion — the audit also caught+fixed a ValueError
crash it had introduced), F5b (overshoot-skip visibility), F7 (mesh_debias
fail-loud), and the **shared `RedisCache.get/set raise_on_error` root fix**
(backward-compatible, both copies; WB deployed its vendored copy).

**⚠ STILL 0 nowcast rows logged/resolved** as of close — the signal is genuinely
rare (strict E_rem≤1F peak rule; US cities eligible only from ~16Z). Do NOT
conclude it's broken; accumulation is the job of the scheduled re-measure.

## SCHEDULED / WATCH (consume as they land)

1. `wb-vif-tune-remeasure` fires **07-24 10:00 ET** — grades the MAIN model
   post-VIF on a fresh clean window since the VIF restart (calibration_check now
   EXCLUDES nowcast via c11), recommends VIF→2.0 if still overconfident (operator
   applies), and reports the nowcast shadow-set scorecard via a direct
   model_name query. Consume its notification.
2. **Day-3 mesh-lead grade (07-18):** still IEM-1-min-backfill-gated at close;
   re-run `~/wb_research/mesh_validation.py --lead 20260718` when it covers.
3. **Maker tilt-vs-control readout ~07-20/22** on the coordination list — consume + relay.
4. First `weatherbot_nowcast_crossing` / `weatherbot_nowcast_shadow` journal
   lines + grader rows under `weather_nowcast_peak`; window-cap + overshoot behavior.
5. Cold-start midpoint (~07-24): 7 corrected stations re-learning EMOS.

## CROSS-BOT FLAGS TO RELAY (WB did NOT touch other bots — RULE ONE/ONE-A)

- **Shared RedisCache root fix** — memory `project_shared_redis_get_root_fix.md`:
  MB cherry-picks the top-level copy to master + deploy; EB/Maker/SB apply the
  3-line change to their branch's copy. Backward-compatible.
- **c13 (Maker):** the pre-c5 Maker feed holds mislabeled ~0.44 lines (07-19);
  Maker to audit/purge `wb_forecasts.jsonl` before the tilt readout.
- **c12 (MB):** shared `prediction_log` calibrators (base_engine/features/
  calibration.py + database.py) are unfiltered by bot_name/model_name — MB to filter.

## QUEUE (after §0, roughly in order — all need operator go where noted)

1. **Registry ADDITIONS (Tier-3, operator sign-off):** evidence complete in spec
   §"S232 REGISTRY-ADDITIONS EVIDENCE" — add RKPK/FACT/ZGGG/OEJN/RPLL/MPMG/ZSQD.
   **Karachi = OPMR has NO METARs** (WU-only class, like HK) — do NOT add OPKC.
   Build station rows + defect tests → present for sign-off → release cut.
2. **HKO integration** for Hong Kong (truth = HK Observatory open data) — pairs
   with the Karachi non-METAR class as one work package.
3. Wire the 6 verified national feeds (DWD/JMA/SG/HKO/BOM/SMN-AR) as debias anchors.
4. Phase-2 second signal `weather_nowcast_peakpass` (design in spec; separate build).
5. Cleanups: `has_asos_1min` dead flag; stale `data/city_icao_mapping.yaml`.

## STANDING OPERATOR REMINDERS (echo EVERY handoff until confirmed)

1. ROTATE trading wallet `0xd6a5…627F` (operator-only).
2. VPS release pruning (many WB releases now).
3. NWWS-OI application (free).
4. API signups: **KMA (Seoul minutely — best single upgrade; RKSI/Seoul mesh
   still weak)**, WU key (retires the pws_mesh web-key dependency AND is a
   Phase-2 acceptance gate), Synoptic, MADIS, Météo-France/Met Office/etc.

## CONTEXT POINTERS

- `docs/WB_NOWCAST_CAPTURE_SPEC.md` — THE anchor: every S231/S232 verdict, the
  Phase-2 design/build, all 3 review passes, the shared-redis fix, in order.
- `docs/WEATHER_STATUS.md` — OPEN DECISIONS 0 (nowcast flag ON) + 2 (S222/VIF).
- Memory `project_wb_next_pointer.md` (S232 close state) +
  `project_shared_redis_get_root_fix.md` (cross-bot).
