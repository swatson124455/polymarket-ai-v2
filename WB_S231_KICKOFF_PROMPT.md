# WB S231 KICKOFF PROMPT (written at S230 close, 2026-07-15)

Paste this into the next WB session. WB-scoped; all standing rules bind (never
quote P&L; no cross-bot bleed; one fix per commit; calibrator hands-off ~08-07;
do NOT fix the bootstrap date-bind alone).

## Tree / branch (main checkout may be EB-held)

Run `git branch --show-current` FIRST. If the main tree is not on
`claude/new-whiteboard-session-9b23tq`, do NOT check out over it — work in the
permanent worktree `.claude/worktrees/wb-whiteboard` (git pull there first).
VPS: `ubuntu@18.201.216.0`, key `~/.ssh/wb_deploy2`. Deployed release is still
`20260714_003205` — S230 deployed NOTHING (research + docs only).

## §0 — VERIFY THE S230 HANDOFF (do this before ANY other work)

1. `bash scripts/wb_resume_check.sh` — the manifest pins all S230 commits, docs,
   and the 7 research scripts. Expected: ALL PASS except
   (a) the "agent WORKTREE" location FAIL when run from the worktree (known
   artifact; substantive checks still run — only escalate if OTHER checks fail),
   (b) a deploy-parity WARN (docs/research commits ahead of the release — correct).
   Any OTHER FAIL → STOP, report to operator, do no new work.
2. VPS spot-checks (read-only):
   - `crontab -l | grep -c wb_research` → 3 (nightly / shadow_book / trade_prints)
   - `ls -la ~/wb_research/trade_prints_$(date -u +%Y%m%d).jsonl` → exists, growing
   - `ls -la ~/wb_research/shadow_books_$(date -u +%Y%m%d).jsonl` → exists, growing
   - `grep -c GATE ~/wb_research/nowcast_peak_90d.out` → 1 (the 90d verdict file)
3. Handoff content check: open `docs/WEATHER_S230_STATUS.md` §0 QUICK FACTS and
   re-derive any 3 rows from their named sources (all read-only SQL/scripts).
   Disagreement beyond rounding → STOP and report before new work.
4. Health watch (~1 min, all should be green):
   - `journalctl -u polymarket-weather --since "24 hours ago" | grep -cE "calibration_reload_failed|cal_fit_failed"` → 0
   - `... | grep -c weather_global_emos_by_station_loaded` → ≥1 per ~6h
   - `... | grep -c avg_clim_mean` → 0 (unit-soup regression tripwire)
   - leak SQL (S230 doc §2) → 0

## PRIMARY TASK — DEEP-BACKTEST PROGRAM (operator-approved 07-15)

Read `docs/WB_NOWCAST_CAPTURE_SPEC.md` — especially "PHASE 0 RESULTS", the 90d
FINAL block, and §"NEXT SESSION PLAN" (the canonical task list). In order:
1. Wire archived Open-Meteo forecasts (historical-forecast + previous-runs +
   historical-ensemble APIs; the previous-runs variant takes `..._previous_dayN`
   variables, NOT `forecast_days`) into `nowcast_peak_model.py`; fill the
   missing-forecast entries and add March → n_test ~120+. RULE STAYS FROZEN
   (E_rem<=1.0F AND hour>=12). Report: gate PASS (≥+0.05 with 2σ) or DEAD.
2. Historical maker-fill study from paginated data-api prints (03→07): fill
   probability of hypothetical resting bids in reveal windows; report as UPPER
   bounds (queue position unknowable).
3. 9-12h cell at scale: all 03→07 resolved families, CLOB minute prices at
   matched timestamps, family-clustered SEs. Does +0.118 hold bot-independently?
4. One Gamma probe: do pre-2026 temp dailies exist at scale? (DB says ~13 in
   Oct-Dec 2025 — likely no; confirm and close the question.)
5. Fold all verdicts into the spec + WEATHER_STATUS OD-2 → Phase-1 build/kill
   recommendation (decision is the operator's).

## SECONDARY (if time)

- **EMOS correction-path verification (code read, no changes):** post-cutoff
  pairs already average −0.62F — confirm the per-station corrector's shift
  actually reaches the bucket-TAIL computation in probability_engine.py, not
  just the mean fit. This decides whether the cheap-NO tail self-heals or needs
  a code fix (S230 doc §3.2).
- First multi-day `executable_replay.py` read (shadow books accrue ~11
  family-days/day; EV rows populate as families resolve).
- AsosOneMinClient decision prep: fix-for-research vs remove (3 request bugs,
  42h upstream lag — it can NEVER be a live feed; S230 doc §4).

## STANDING OPERATOR REMINDERS (echo in EVERY handoff until confirmed)

1. ROTATE trading wallet `0xd6a5…627F` (in-repo key since May 15; operator-only).
2. VPS release pruning — unblocked; ~42G in 11 fat legacy releases; disk 62%.
3. DELETE the local `wb-s222-gate-check` daily task (verification ran twice).
4. NWWS-OI application (free) — useful regardless of the nowcast program's fate.

## CONTEXT POINTERS

- `docs/WEATHER_S230_STATUS.md` — THIS session's full handoff (quick-facts table,
  verdict chain, corrections to stale claims, VPS state).
- `docs/WEATHER_STATUS.md` — canonical OPEN DECISIONS + WHAT IS LIVE (OD-2 carries
  the n=133 verdicts + root cause; 2e queue row 4b points at the program).
- `docs/WB_NOWCAST_CAPTURE_SPEC.md` — the program spec + all Phase-0 numbers.
- `scripts/wb_research/README.md` — every harness documented with its results.
