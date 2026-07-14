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

## FIRST — collect the batch
1. Finished? `pgrep -fc 'chain_deep_dive[.]py'` (0 = done; **pgrep NEEDS `-f`** —
   the comm is `python`, landmine). `tail -30 /tmp/deep_dive_batch.log`;
   `ls /opt/pa2-shared/mb_copyable_data/deep_dive/0x*.json | wc -l` (expect 47).
2. Read `deep_dive/_summary.json` — `counts` {ADMIT, REJECT,
   INSUFFICIENT-EVIDENCE}, the `admitted` list, `sybil` funder clusters.
3. **Died mid-run?** (fewer than 47 result files + process gone) The per-trader
   JSONs already written are durable. RESUME on the missing addresses only
   (roster = 9 in `readjudicate.json` + 38 in `deep_dive_extra_38.txt`).
   Launcher: `/tmp/run_deep_dive_batch.sh` — but **refresh `/tmp/mbre` to branch
   head + verify blob first**, and only feed it the not-yet-done addresses
   (e.g. build a remaining-list via `--traders`).

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
