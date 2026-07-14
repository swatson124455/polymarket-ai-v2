# S230 KICKOFF — WeatherBot session (continuation of S229, LOCAL machine)

Paste this whole file as your first message to a Claude Code session started in
`C:\lockes-picks\polymarket-ai-v2`. You are S230, the direct continuation of S229
(local). You have the VPS deploy key — run ssh/scp/psql yourself.

## ⚠ SHARED CHECKOUT — verify branch before EVERY repo write
The main tree at `C:\lockes-picks\polymarket-ai-v2` is shared with EB sessions and may be
checked out to an EB branch right now. WB work lives on `claude/new-whiteboard-session-9b23tq`,
and there is a permanent WB worktree at `.claude/worktrees/wb-whiteboard` pinned to it.
- Run `git branch --show-current` FIRST. If it is not the whiteboard branch, do NOT check it
  out over the EB branch — `cd` into `.claude/worktrees/wb-whiteboard` for all writes (it is
  already on the WB branch; `git pull` there first).
- Read-only checks (ssh/psql/git log) can run from anywhere.

## Land + resume check
```
cd .claude/worktrees/wb-whiteboard    # (create with: git worktree add .claude/worktrees/wb-whiteboard claude/new-whiteboard-session-9b23tq  — if missing)
git pull origin claude/new-whiteboard-session-9b23tq
bash scripts/wb_resume_check.sh        # must be ALL PASS (run from the MAIN tree if it flags "agent WORKTREE"; the check asserts main-repo toplevel)
```
Then read `docs/WEATHER_STATUS.md` (OPEN DECISIONS 1–4 + WHAT IS LIVE) and `docs/WEATHER_S229_STATUS.md` (S229 deep-dive).

## VPS access
- Host `ubuntu@18.201.216.0`, key `~/.ssh/wb_deploy2`.
- Multi-line: here-string | `ssh -i ~/.ssh/wb_deploy2 ubuntu@18.201.216.0 "tr -d '\r' | sudo bash -s"`.
- SQL: here-string | `ssh … "sudo -u postgres psql polymarket -f -"`.
- WB splinter deploy: `deploy/wb-release-cut.sh` (git-archive tarball → scp → `bash -s $STAMP`).
- Deployed now: release `20260714_003205` (rollback `20260713_160143`).

## PRIMARY TASK — S222 re-run (the whole session gates on this)
S229 deployed the EMOS unit-soup root fix; the S222 clock RESTARTED **2026-07-13 16:02:29Z**.
The 07-11→13 verdict was FAIL but measured the bug, not the fixes — that window is void.
1. Gate count (was 19/50 at 2026-07-14 16:40Z, on track for the ≥50 gate ~07-16/17):
   `SELECT count(DISTINCT market_id) FILTER (WHERE resolution IS NOT NULL) FROM prediction_log WHERE bot_name='WeatherBot' AND prediction_time > '2026-07-13 16:02:29';`
2. If <50 → report count + ETA (~19/day), watch health (below), STOP.
3. If ≥50 → run `WB_S222_POSTFIX_VERIFICATION_PROMPT.md` using its **S229 re-point block** (cutoffs `2026-07-13 16:02:29` / `--since 20260713_160229`; deployed release `20260714_003205`+; S229 markers ≥9 in the box's `weather_bot.py`; journal must show `weather_global_emos_by_station_loaded` each ~6h — if `avg_clim_mean` reappears in `weatherbot_global_samos_fitted`, the unit-soup defect regressed → STOP).
4. Station-wedge duel (WEATHER_STATUS 2c): rerun `scripts/wb_research/brier_duel.py` fed with `prediction_log.predicted_prob` (last per market) instead of raw members, vs CLOB price at matched time, per (station×lead×side) cell. First read of the FIXED bot vs the market.
5. Report PASS/PARTIAL/FAIL per gate-retirement criterion with numbers+sources. NEVER quote P&L. Retire nothing yourself — report; operator decides.

NOTE: a local scheduled task `wb-s222-gate-check` may fire 09:00 daily 07-16→19 and do steps 1–4 automatically. After a completed verification run, tell the operator to delete that task.

## THEN, in order (NEXT QUEUE — WEATHER_STATUS 2e is canonical)
1. Gate retirement IF S222 PASSES (A1/A3 → dampeners → caps; C0 Kelly LAST, calibrator-gated).
2. **Shadow-book review:** `~/wb_research/shadow_books_*.jsonl` on the VPS holds 10-min METAR+CLOB-book snapshots. Analyze whether the leader-following +EV (H=17 ≈ +8¢/$1 at MID) survives real ask depth. This decides if the one confirmed edge is tradeable.
3. Latency package activation (OPEN DECISION 3a) — decide WITH the shadow review.
4. Bootstrap landmine PROPER fix (§5 of S229 doc): date-bind + `actual_source` training filter in ONE commit. POST-verdict only (half changes predictions → restarts clock). DO NOT fix the bind alone.

## HEALTH WATCH (every session, ~1 min)
- `journalctl -u polymarket-weather --since "24 hours ago" | grep -cE "calibration_reload_failed|cal_fit_failed"` → 0.
- `… | grep -c weather_global_emos_by_station_loaded` → ≥1 per ~6h (~106 stations); `… | grep -c avg_clim_mean` → 0 (regression tripwire).
- CancelledError check — use the DEPLOY cutoff, NOT "24h ago": `journalctl -u polymarket-weather --since '2026-07-14 00:32:41' | grep -c "cannot unpack non-iterable CancelledError"` → 0 (S229 fix holds). ⚠ A "24h ago" window shows ~6 PRE-deploy hits until 07-14 ~23:40Z (last pre-fix occurrence 07-13 23:40) — those are expected residue, not a regression.
- Nightly automation: tail `~/wb_research/nightly_*.log` (NULL-end drain + race accrual) and confirm shadow-book file grows.

## STANDING OPERATOR REMINDERS — echo these in EVERY handoff until the operator confirms done
1. **ROTATE the trading wallet `0xd6a5…627F`** — its private key sat in-repo since May 15 + weeks of world-readable VPS copies; wallet is active. File relocated out of repo 07-14. Rotation is operator-only (shared `/opt/pa2-shared/.env` + restart all 4 services). Detail: WEATHER_STATUS 2e row 8 / S229 doc §8.
2. **Review VPS release pruning** — 19 releases / 43G. After the S222 verdict: keep live + rollback, delete the rest (~40G). Gated as forensic insurance until then.

## Rules (unchanged, binding)
- WB scope + sanctioned shared-layer exceptions only; shared checkout → worktree; verify branch before writes.
- NEVER quote P&L (#11); NEVER track/echo other bots' vendors/secrets/nags.
- One fix per commit; defect-test-first; keep `docs/WB_HANDOFF_MANIFEST.json` in sync (resume check FAILs on drift; tag S230).
- Do NOT touch the calibrator before S222 + re-learn (~08-07). Do NOT fix the bootstrap bind alone.
- Watch for asyncpg str-vs-timestamptz binds (grep `AS timestamptz`).

Confirm the resume check passed and summarize OPEN DECISIONS before doing anything.
