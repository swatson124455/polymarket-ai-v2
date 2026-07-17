# WB S232 KICKOFF PROMPT (REWRITTEN at S231 true close, 2026-07-18 ~00:1xZ)

Paste this into the next WB session. WB-scoped; standing rules bind (NEVER
quote P&L; no cross-bot vendor/secret/nag bleed; one fix per commit;
calibrator HANDS OFF until ~08-07; never fix the bootstrap date-bind alone;
WB-ALWAYS-GLOBAL is a hard operator directive — no US-only filters, ever).

## Tree / branch (main checkout may be held by another bot)

Run `git branch --show-current` FIRST. If the main tree is not on
`claude/new-whiteboard-session-9b23tq`, do NOT check out over it — work in
the permanent worktree `.claude/worktrees/wb-whiteboard`
(`git pull origin claude/new-whiteboard-session-9b23tq` there first).
VPS: `ubuntu@18.201.216.0`, key `~/.ssh/wb_deploy2`.

**Deployed release: `20260717_145326`** (restart 07-17 18:53:50Z; rollback
code = `20260717_105239`; the S231 arc shipped TWO live changes with operator
sign-off: the 7-station registry fix and the Maker forecast feed). INFRA
NOTE: WB's systemd unit gained `/opt/pa2-maker-feeds` in `ReadWritePaths`
(backup `polymarket-weather.service.bak_20260717_makerfeed`). Do not deploy
anything else without operator sign-off.

## §0 — VERIFY THE S231 HANDOFF (before ANY other work)

1. `bash scripts/wb_resume_check.sh` — expected: ALL PASS except (a) the
   known "agent WORKTREE" location FAIL when run from the worktree, (b) the
   deploy-parity WARN. Any OTHER FAIL → STOP, report, no new work.
2. VPS spot-checks (read-only):
   - `crontab -l | grep -c wb_research` → **4** (nightly / shadow_book /
     trade_prints / pws_mesh)
   - `tail -3 ~/wb_research/pws_mesh_err.log` → 5-min ticks, `wu_fails=0`-ish
     (it EXCLUDES dead-station 204s; a spike = WU ban/outage)
   - `wc -l /opt/pa2-maker-feeds/wb_forecasts.jsonl` → growing (586 lines at
     close 07-17 23:41Z, 36 cities, temperature model)
   - `readlink /opt/polymarket-ai-v2-weather` → `20260717_145326`
3. Re-derive any 3 rows of `docs/WEATHER_S231_STATUS.md` §0 quick facts +
   spot-check the S231-FINAL section's claims against
   `docs/WB_NOWCAST_CAPTURE_SPEC.md` (§S231 blocks carry every verdict with
   sources). Mismatch beyond rounding → STOP.
4. Health (~1 min): journal greps `calibration_reload_failed|cal_fit_failed`
   → 0; `weather_global_emos_by_station_loaded` ≥1/~6h; `avg_clim_mean` → 0;
   leak SQL (`predicted_prob >= 0.9995` since 07-11 00:47) → 0.

## CONTEXT — what S231 established (do NOT re-derive; read the docs)

- **Peak-model gate PASSED 4×** (last: corrected stations + hardened
  clustered gate, TEST +0.070 / clustered +0.087). The ONLY validated edge.
  5 other candidates died honestly (spec records all).
- **INPUT AUDIT (the big one):** Polymarket resolves on stations named in
  each market DESCRIPTION. 7 registry mismatches FIXED + DEPLOYED
  (Dallas=KDAL, Denver=KBKF, Houston=KHOU, Seoul=RKSI, Taipei=RCSS,
  Milan=LIMC, Istanbul=LTFM). The "~9% settlement-risk" claim was RETRACTED
  (100% miswiring artifact; 0/268 at correct stations). HK still KNOWINGLY
  mis-stationed (resolution = HK Observatory) — S233 item.
- **Cold-start watch until ~07-31:** the 7 renamed cities re-learn
  EMOS/calibration from scratch under new ids (correct — old history
  measured wrong airports); sizing runs baseline ~14d (capped).
- **Maker forecast feed LIVE** (operator-approved cross-bot): WB appends
  YES-frame forecasts to `/opt/pa2-maker-feeds/wb_forecasts.jsonl` via
  `_export_forecast_for_maker` (hard-isolated; kill =
  `WEATHER_MAKER_FEED_ENABLED=false`). WB's Q3 semantics answer is at the
  bottom of `AGENT_HANDOFF_2026-07-17_MAKER_WB_FORECAST_TILT_PROPOSAL.md`
  (MAIN checkout root, gitignored). Maker owes WB a tilt-vs-control readout
  ~07-20/22 — consume it when it appears on the coordination list.
- **Mesh knowledge:** offsets split into REPRODUCIBLE scalars (London/
  Amsterdam/Madrid/Singapore/Tokyo/…) vs DIURNAL cities (SFO confirmed,
  Toronto suspected) → Phase-2 debias needs an hour-of-day term for
  coastal/lake cities. Shenzhen roster is garbage (drop-rule candidate #1;
  mainland China has no PWS). First correct-station reads: KDAL/KBKF good,
  KHOU stable-warm; RKSI/RCSS arrive with their next local day.
- Ops laws (5 incidents): [b]racket patterns in EVERY pgrep/pkill; ABSOLUTE
  paths in nohup launches; VPS release files are CRLF (normalize before
  raw-hash comparisons).

## PRIMARY 1 — MESH-LEAD VERDICT (the Phase-2 gate)

The scheduled local task `wb-mesh-lead-validation` fires Sat 07-18 10:00 ET
and runs `mesh_validation.py --lead 20260716`. CAVEAT: IEM's 1-min product
was STALLED at 07-16 ~07:57Z for 40+ h at close — if the task reports
insufficient arbiter data, re-run manually later
(`/opt/polymarket-ai-v2-weather/venv/bin/python ~/wb_research/mesh_validation.py
--lead 20260716`, then 20260717). Grade against the spec's PHASE-2 DESIGN
acceptance gates: mesh leads ≥50% of gradeable events, median lead ≥15 min,
false-crossing <20%; DISCLOSE which cities were gradeable (LGA's 1-min was
absent entirely at last check). Also re-run --bias for accruing days and
extend the day-over-day offset table (scalar-vs-diurnal classification).

## PRIMARY 2 — PHASE-2 BUILD (ONLY on explicit operator go, after Primary 1)

Design is in the spec (§PHASE-2 DESIGN + peak-passed second signal). Build
requirements the S231 findings added: hour-of-day debias term for diurnal
cities; per-city drop rule (post-debias residual sd >~1.5F → exclude;
Shenzhen out); the ~9% haircut input is RETRACTED — use the corrected
Study-B numbers (locks real, supply vanishes post-certainty); sizing honesty
$50/window cap. Defect-test-first, release cut, flag stays OFF until
acceptance gates pass.

## QUEUE (after the primaries, roughly in order)

1. Registry ADDITIONS: replace the lowercase dynamic pseudo-stations
   (busan, guangzhou, jeddah, karachi, manila, qingdao, + Cape Town, Jinan,
   Panama City/Albrook, Zhengzhou) with VERIFIED ICAO entries per each
   market description (same evidence pattern as the 7 fixes; per-station
   AWC + WU-page verification; Tier-3, operator sign-off). Istanbul roster
   is thin (1 PWS/bin) — re-resolve candidates.
2. HKO integration for Hong Kong (truth = HK Observatory open data — the
   feed is already verified in the source ledger).
3. Wire the 6 verified national feeds (DWD/JMA/SG/HKO/BOM/SMN-AR) as debias
   anchors (source ledger has probe results; unhurried per-station checks).
4. rep_bias_test recompute at corrected stations (the 81% number is
   contaminated by the 3 miswired US cities).
5. Cold-start midpoint check (~07-24): EMOS pairs accruing under new ids.
6. Cleanups: has_asos_1min dead flag; data/city_icao_mapping.yaml stale;
   wu_fails 204-split S232 note is DONE — skip.
7. S222 re-cut at n≥200 clean-window resolved (check the gate count).

## STANDING OPERATOR REMINDERS (echo EVERY handoff until confirmed)

1. ROTATE trading wallet `0xd6a5…627F` (operator-only).
2. VPS release pruning (~42G legacy; now 20+ releases after S231's three cuts).
3. NWWS-OI application (free) — feeds a PASSED program's react leg.
4. API signups: **KMA (Seoul minutely — best single upgrade)**, WU key
   (needs a registered PWS; then set WU_WEBKEY in ~/wb_research/pws_mesh.sh),
   Synoptic, MADIS, Météo-France/Met Office/MET Norway/DMI/KNMI/CWA.
5. Phase-2 go/no-go after the mesh-lead verdict.

## CONTEXT POINTERS

- `docs/WEATHER_S231_STATUS.md` — session handoff (§0 quick facts + FINAL
  section listing the whole late arc with commit shas).
- `docs/WB_NOWCAST_CAPTURE_SPEC.md` — THE knowledge anchor: every S231
  verdict, retraction, review, ledger, and design block, in order.
- `docs/WEATHER_STATUS.md` — canonical WHAT IS LIVE + OPEN DECISIONS.
- `scripts/wb_research/README.md` — every harness + results.
- Memory `project_wb_next_pointer.md` — delta chain #1-#5.
