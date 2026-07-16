# WB S232 KICKOFF PROMPT (written at S231 close, 2026-07-16)

Paste this into the next WB session. WB-scoped; all standing rules bind (never
quote P&L; no cross-bot bleed; one fix per commit; calibrator hands-off ~08-07;
do NOT fix the bootstrap date-bind alone).

## Tree / branch (main checkout may be EB-held)

Run `git branch --show-current` FIRST. If the main tree is not on
`claude/new-whiteboard-session-9b23tq`, do NOT check out over it — work in the
permanent worktree `.claude/worktrees/wb-whiteboard` (git pull there first).
VPS: `ubuntu@18.201.216.0`, key `~/.ssh/wb_deploy2`. Deployed release is still
`20260714_003205` (rollback `20260713_160143`) — S230 AND S231 deployed NOTHING
(research + docs only); do not deploy unless the operator says so.

## §0 — VERIFY THE S231 HANDOFF (do this before ANY other work)

1. `bash scripts/wb_resume_check.sh` — expected: ALL PASS except (a) the known
   "agent WORKTREE" location FAIL when run from the worktree, (b) the
   deploy-parity WARN. Any OTHER FAIL → STOP, report, do no new work.
2. VPS spot-checks (read-only):
   - `crontab -l | grep -c wb_research` → **4** (nightly / shadow_book /
     trade_prints / **pws_mesh**)
   - `ls -la ~/wb_research/pws_mesh_$(date -u +%Y%m%d).jsonl` → exists, growing
     during US local daytime (09-21 local per city); check
     `tail ~/wb_research/pws_mesh_err.log` for `new_obs=` lines, no tracebacks
   - `grep -c "GATE: PASS" ~/wb_research/nowcast_peak_133d.out` → 1
   - `ls ~/wb_research/{shadow_books,trade_prints}_$(date -u +%Y%m%d).jsonl`
3. Handoff content check: re-derive any 3 rows of `docs/WEATHER_S231_STATUS.md`
   §0 QUICK FACTS from their named sources. Mismatch beyond rounding → STOP.
4. Health watch (~1 min): `journalctl -u polymarket-weather --since "24 hours ago"`
   greps — `calibration_reload_failed|cal_fit_failed` → 0;
   `weather_global_emos_by_station_loaded` ≥1/~6h; `avg_clim_mean` → 0;
   leak SQL (WB_S222_POSTFIX_VERIFICATION_PROMPT.md §4c) → 0.

## PRIMARY TASK 1 — MESH VALIDATION (the Phase-1 acceptance test)

`pws_mesh_*.jsonl` has been accruing since 2026-07-16 ~01:00Z. Once IEM 1-min
has caught up over the logged window (~42h lag — so data for 07-16 is fetchable
from ~07-18):
1. Reconstruct per-city running-max curves from the mesh (median of the ≤4 PWS,
   qc==1 only; consider per-PWS bias vs the airport METAR first).
2. Compare against IEM 1-min truth + hourly print times (`nowcast_skill.py`
   conventions): does the MESH detect bucket-boundary crossings with a usable
   lead over the public print (target: reproduce a meaningful share of the
   58-min median lead)? Report per-city lead distribution + false-crossing rate
   (mesh says crossed, print world never does — the 14% never-print risk).
3. PWS-vs-METAR bias/scatter per city — the mesh must predict the PRINT world;
   a systematic mesh-vs-ASOS offset must be learned per station, not ignored.
Verdict shape: "mesh is/isn't a viable live substitute for the dead 1-min feed",
with numbers. This gates Phase 2.

## PRIMARY TASK 2 — PHASE-2 DESIGN (operator-scoped; DESIGN ONLY unless told)

Write the paper-strategy design into the spec (no bot code without operator go):
entry = peak-model rule (E_rem<=1.0F AND h>=12) on mesh-detected crossings;
`WEATHER_NOWCAST_ENTRY_ENABLED` flag-OFF default; separate model_name in
prediction_log (independent grading); maker-first execution (rest bids at
pre-reveal levels per maker_fill_study); ALL existing risk plumbing unchanged;
sizing honesty ~$100/window upper bound (task-2 capacity). Include the S228
latency package activation (OD-3a) in the design as its react leg — activation
remains a Tier-2 operator decision.

## IF TIME — secondary

- executable_replay re-read (shadow books keep accruing; ≥50/cell bar).
- S222 clean-window re-cut when gate count reaches ~n≥200 (bin power).
- Spec/WEATHER_STATUS hygiene: fold mesh-validation numbers in.

## STANDING OPERATOR REMINDERS (echo in EVERY handoff until confirmed)

1. ROTATE trading wallet `0xd6a5…627F` (in-repo key since May 15; operator-only).
2. VPS release pruning — unblocked; ~42G in 11 fat legacy releases.
3. NWWS-OI application (free) — now feeds a PASSED program's react leg.
4. **WU key or Synoptic token** for pws_mesh (replaces the public-web-key
   dependency; swap via `WU_WEBKEY` env in `~/wb_research/pws_mesh.sh`).

## CONTEXT POINTERS

- `docs/WEATHER_S231_STATUS.md` — THIS session's handoff (quick facts, verdict
  chain, corrections, VPS state).
- `docs/WB_NOWCAST_CAPTURE_SPEC.md` — §"S231 DEEP-BACKTEST RESULTS" (gate PASS,
  maker fills, 9-12h death, Phase-1 build record) + Phase-2 sketch.
- `docs/WEATHER_STATUS.md` — canonical OPEN DECISIONS (2e row 4b = the program).
- `scripts/wb_research/README.md` — every harness + its results.
