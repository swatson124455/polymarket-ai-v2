# MB STEWARD — NEXT SESSION PROMPT (chain deep-dive results readout)

You are the local MB steward session, continuing from 2026-07-14 PM. The
chain-native roster-admission gate (`scripts/chain_deep_dive.py`) is built,
adversarially reviewed (16 findings fixed), smoke-validated, and the
~47-address batch was **LAUNCHED 2026-07-14 19:31 UTC (detached)**. Your job:
collect + review the results, then PROPOSE (never auto-add) admissions for the
operator's word.

**STEP ZERO — before ANY project doc:** MB state docs are BRANCH-VERSIONED.
Discover the newest copy yourself:
```
git ls-remote origin 'refs/heads/claude/*'
git fetch origin <branch> && git show FETCH_HEAD:docs/MB_STATE.md | head -15
```
Expected newest on `claude/repo-setup-docs-fq9bhn` (`5b76608`+) — confirm. THEN
read in order: `CLAUDE.md` → newest `docs/MB_STATE.md` §0 (the PM block) + §5
TO-DO + §7 (new landmines) → `docs/MB_HANDOFF_PROTOCOL.md`.

**LOCAL LOGISTICS (binding):** all local work from the worktree
`C:/lockes-picks/mb-steward` (branch locked; TWO writers → `git pull --ff-only`
before EVERY commit; push after each completed unit).

**VPS:** you run read-only commands yourself via
`ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "..."`;
the operator approves each. State WHAT + WHY before anything that changes VPS
state (writes outside `/tmp`, restarts, kills).

## FIRST — collect the batch (RUN-2 reality, updated 2026-07-15 PM)
Run-1 (rps 4) was killed at 12/47 (results preserved: **8 ADMIT / 3
INSUFFICIENT / 1 REJECT**, and the 8 ADMITs are ALREADY admitted → cohort-2,
see below). **Run-2** covers the remaining 35 (`/tmp/deep_dive_remaining.txt`)
at rps 8 with the receipt short-circuit (`d6276f7`).
1. Finished? `pgrep -fc 'chain_deep_dive[.]py'` (0 = done; **pgrep NEEDS `-f`**,
   and NEVER put a pkill in the same ssh command as anything naming the script
   — §7 variant-2 landmine). `tail -30 /tmp/deep_dive_batch.log`;
   `ls /opt/pa2-shared/mb_copyable_data/deep_dive/0x*.json | wc -l` (expect 47).
2. Read `deep_dive/_summary_run2.json` (run-2's 35 ONLY). **No artifact
   aggregates all 47** — tally the per-trader JSONs:
   `python3 -c "import json,glob,collections; print(collections.Counter(json.load(open(p))['verdict'] for p in glob.glob('/opt/pa2-shared/mb_copyable_data/deep_dive/0x*.json')))"`
3. **Died mid-run?** (fewer than 47 result files + process gone) The per-trader
   JSONs already written are durable. RESUME on the missing addresses only:
   rebuild the remaining list (roster = 9 in `readjudicate.json` + 38 in
   `deep_dive_extra_38.txt`, minus done JSONs), **refresh `/tmp/mbre` to branch
   head + verify blob**, edit `/tmp/run_deep_dive_batch.sh` to point
   `--extra-traders` at the new remaining list, relaunch detached.
4. New ADMITs → verify their tiers like the first 8 (100% backing, 0 mismatch,
   P≥bar, tailable rate, no flags), then batch-admit with ONE watcher restart
   (same mechanism as 2026-07-15: roster `clean` += addrs + extend the
   `cohort2` ledger key, rerun `deploy/mirror3_shadow_deploy.sh`, record the
   new restart epoch — cohort-2B gets ITS OWN start epoch in the ledger).

## ROSTER — you MAY add/subtract, but NOTIFY + CLARIFY at handoff (operator directive 2026-07-14)
You have standing authority to add/subtract candidate traders from the deep-dive
roster (fold in wave-2 candidates, drop invalid/dup/out-of-scope addresses)
WITHOUT asking first — but EVERY add/subtract MUST be logged with the delta +
reason in `docs/MB_STATE.md` §0 ROSTER LEDGER AND in this prompt, and the
durable address files kept in sync. Admissions still need the operator's WORD
(this authority is over the CANDIDATE roster, not who joins a live cohort).
- **Wave-1 (this batch) = 47** — 9 cohort-2 (`readjudicate.json`) + 4 grey
  (`readjudicate_grey2.json`) + 34 HFT-borderline (`deep_dive_extra_38.txt`).
- **Candidate-add menu for wave-2** (deferred to avoid a concurrent 2nd batch
  double-loading the shared tenderly RPC): `0x6bab41a0dc40d6dd4c1a915b8c01969479fd1292`,
  `0x4dfd481c16d9995b809780fd8a9808e8689f6e4a` (both run-1 strong candidates,
  cached, NOT in wave-1) + the ~38 ALL-universe scope-outs. Run wave-2 after
  wave-1 completes; **log the exact addresses you add in §0 when you do.**

## REVIEW DISCIPLINE (binding)
- **ADMIT = a PROPOSAL** to the operator for a cohort (own start date, separate
  readout, NEVER pooled with cohort-1). Nobody joins any roster without the
  operator's explicit word.
- **INSUFFICIENT = deepen, NEVER accuse.** Read the reason: receipt-cap → raise
  `--max-receipts`; thin backing / un-gradeable skill → widen the window /
  `--refresh` the API cache / second RPC. Re-run those specific addresses.
- **REJECT is only** an affirmative contradiction (mismatch / fabrication /
  adequately-powered NEGATIVE chain edge) or a measured infeasibility (true rate
  > cap). A rate-REJECT means "un-tailable", not "fraud" — say it that way.
- Cross-check any surprising verdict against the per-trader JSON tiers before
  trusting it. If a result looks impossible, the run is wrong — stop and say so
  (instrument-error hypothesis FIRST; session score so far: our bugs many,
  trader lies 0).

## MONITOR (while it runs)
The batch shares the tenderly RPC with the **LIVE `polymarket-mirror3` watcher**
(MB's primary instrument). Verify
`journalctl -u polymarket-mirror3 --since '1 hour ago' | grep -cE 'CANARY ALARM|QUOTE SANITY'`
stays **0**. If the watcher goes blind, the batch load is a suspect — lower
`--rps` or point the batch at a second endpoint (`--rpc-env`).

## SHADOW READOUT — recurring, check EVERY session (2026-07-15)
A **daily VPS cron (12:30 UTC)** runs `deploy/shadow_readout_cron.sh` →
`scripts/shadow_readout.py`, which rebuilds token→outcome labels **FRESH from
the `markets` table** (the `analyze_shadow --gamma-cache` default is STALE and
silently reports "0 resolved" — §7 landmine, operator-caught 2026-07-15) and
reads **cohort-1 and cohort-2 SEPARATELY** (never pooled).
- **On session start: `cat /opt/pa2-shared/mb_copyable_data/deep_dive/shadow_readout_ALERT.txt`**
  (exists only when triggered) **and `tail /opt/pa2-shared/mb_copyable_data/deep_dive/shadow_readout_log.txt`**;
  RELAY to the operator if an ALERT fired. Triggers: a cohort hits **≥30
  resolved markets** (→ run the pre-registered verdict) OR its **edge is
  convincingly negative** (P(>0) ≤ 0.10 on ≥10 mkts).
- **Current signal (2026-07-15, DESCRIPTIVE, UNDERPOWERED):** cohort-1 edge is
  **NEGATIVE** (≈ −0.048, P(>0) ≈ 0.37) on ~10/30 resolved — leans the WRONG
  way; watch it. Cohort-2 is at 0 (just admitted). NEVER quote this as $ P&L
  (banned) — it's calibration/edge.
- To read on demand: `sudo -u polymarket bash /opt/pa2-shared/mb_readout/deploy/shadow_readout_cron.sh`.

## STANDING STATE (verify, don't trust)
Shadow watcher live since 2026-07-13 23:29:27 UTC on `/opt/mirror3`=`25b54d4`;
S222-style shadow readout clock ~2-4wk (`analyze_shadow.py --trust-quotes-after
1783985376`); roster=16, cohort-2 pool=13 awaiting deep-dive + operator word.

## HARD FENCES (unchanged)
Never touch `polymarket-mirror3` except via `deploy/mirror3_shadow_deploy.sh`
with operator go; no shared-module edits (`base_engine/**`, `database.py`,
`position_manager.py`, `deploy.sh`, `BotBankrollManager`, `risk_manager`)
without authorization; all CLAUDE.md bans (no `neg_risk` filters, no
order_gateway keying repair, no mirror_scoring validate, `bot_pnl.py` for P&L).

## SESSION END
Update `docs/MB_STATE.md`; refresh the docs-sync PR to master — branch
`claude/mb-docs-sync-0714pm` is already pushed (open the PR at
`https://github.com/swatson124455/polymarket-ai-v2/pull/new/claude/mb-docs-sync-0714pm`
or fold your new docs into a fresh sync branch); write the next handoff prompt.

**Start by reporting:** newest MB_STATE confirmed, batch status (done / running /
died + counts), the ADMIT / REJECT / INSUFFICIENT split with per-trader reasons,
and your proposed next actions. Then wait for the operator's go on any admission.
