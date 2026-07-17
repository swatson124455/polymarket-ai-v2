# MB STEWARD — NEXT SESSION PROMPT (chain deep-dive results readout)

You are the local MB steward session, continuing from 2026-07-16/17. The
chain-native roster-admission gate (`scripts/chain_deep_dive.py`) is BUILT,
twice adversarially reviewed (16 + 30 findings fixed + a copyability param
rework, no confirmed defects), and running its 3rd relaunch (run-3, 24/47 done
at handoff). 8 cohort-2 ADMITs + 1 tail-feasibility PROBE (0xf705fa) are LIVE
in the shadow watcher; the daily fresh-label readout cron is armed. Your job:
finish collecting the batch, run the QUEUED band re-run, review results, and
PROPOSE (never auto-add) admissions for the operator's word.

**STEP ZERO — before ANY project doc:** MB state docs are BRANCH-VERSIONED.
Discover the newest copy yourself:
```
git ls-remote origin 'refs/heads/claude/*'
git fetch origin <branch> && git show FETCH_HEAD:docs/MB_STATE.md | head -15
```
Expected newest on `claude/repo-setup-docs-fq9bhn` (`4b3b60e`+, Last-updated
2026-07-15/16) — confirm. THEN read in order: `CLAUDE.md` (Forbidden-Pattern
sourcing rules) → newest `docs/MB_STATE.md` §0 (ROSTER LEDGER incl. the probe +
band-re-run queue, the corrected cohort accounting) + §7 (landmines: RPC-hang,
stale-cache, pkill-variant-2, ship-discipline) → `docs/MB_HANDOFF_PROTOCOL.md`.
Binding self-rule this session: **NO FAST LANE** — nothing runs on VPS/live
state without self-test+pytest, a review pass for verdict-producing code, a
first-output cross-check vs an independent source, timeouts on every network
await, and kill/deploy one-liners composed against the §7 pkill landmine (the
pkill and NOTHING else naming the script). Every 07-15/16 incident came from
skipping this.

**LOCAL LOGISTICS (binding):** all local work from the worktree
`C:/lockes-picks/mb-steward` (branch locked; TWO writers → `git pull --ff-only`
before EVERY commit; push after each completed unit).

**VPS:** you run read-only commands yourself via
`ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "..."`;
the operator approves each. State WHAT + WHY before anything that changes VPS
state (writes outside `/tmp`, restarts, kills).

## FIRST — collect the batch (RUN-3 reality, updated 2026-07-17 ~01:00Z)
Timeline: run-1 (rps 4) killed at 12/47; run-2 (rps 8) HUNG 13h on ONE RPC
await (no read-timeout) → root-fixed (`rpc_call` 90s guard, `07e7296`) and
relaunched as **run-3** on the 27 remaining (`/tmp/deep_dive_remaining.txt`).
Now at ~24/47 (5/27 into run-3), the code carries all 46 review fixes + the
2026-07-16 copyability param rework (`27ee79b`).
1. Finished? `pgrep -fc 'chain_deep_dive[.]py'` (0 = done; **pgrep NEEDS `-f`**;
   NEVER put a pkill in the same ssh command as anything naming the script —
   §7 pkill-variant-2 landmine). Progress: `grep -oE '\[[0-9]+/27\]'
   /tmp/deep_dive_batch.log | tail -1`; heartbeats `sweep done…`/`receipts N/`
   prove it's working, not hung (the 13h hang had NONE). `ls
   .../deep_dive/0x*.json | wc -l` (expect 47).
2. Tally (the batch summary now rebuilds from on-disk JSONs after every trader
   → `_summary_run2.json` is 47-wide for run-3+; also the one-liner):
   `python3 -c "import json,glob,collections; print(collections.Counter(json.load(open(p))['verdict'] for p in glob.glob('/opt/pa2-shared/mb_copyable_data/deep_dive/0x*.json')))"`
3. **Died/hung mid-run?** (log size static ~25 min while alive = stalled; the
   monitor watches log growth now.) Per-trader JSONs are durable. RESUME the
   missing addresses: rebuild remaining (roster = 9 `readjudicate.json` + 38
   `deep_dive_extra_38.txt`, minus done JSONs), **refresh `/tmp/mbre` + verify
   blob**, edit `/tmp/run_deep_dive_batch.sh`'s `--extra-traders`, relaunch.
4. **QUEUED — BAND RE-RUN (do this after run-3 finishes):** the 200-1000
   fills/day rejects were adjudicated under the OLD cap; re-run them under the
   reworked params (`27ee79b`+: `--receipt-free-rate 1000 --max-decisions-per-day
   25 --max-flat-share 0.60`). Candidates: 938/749/395/371/288 + 0xf705fa (=the
   probe, gets a formal verdict) + any run-3 additions with true_rate in
   (200,1000]. Whoever now ADMITs → PROPOSE as a probe (operator word each).
5. New ADMITs → verify tiers like the first 8 (100% backing, 0 mismatch, P≥bar,
   decisions/day + flat-share within caps, no wash/copier), then batch-admit
   with ONE watcher restart: roster `clean` += addrs, extend the `cohort2` (or
   `probe`) ledger key with its OWN `admitted_utc`, rerun
   `deploy/mirror3_shadow_deploy.sh`, record the restart epoch. The readout
   `load_cohorts` REFUSES to run if clean ≠ cohort1 ∪ cohort2 ∪ probe (and on
   overlap / bad epoch) — so a roster edit without the ledger update fails loud.

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

## STANDING STATE (verify, don't trust — updated 2026-07-15 PM)
Shadow watcher on `/opt/mirror3`=`d6276f7` (mirror_v3 byte-identical to the
prior `25b54d4`), restarted 2026-07-15 19:20:45 UTC with **roster=24** (16
cohort-1 + 8 cohort-2 ADMITTED; cohort-2 start epoch 1784143245). Cohort-1
readout clock from 2026-07-13 23:29 UTC (epoch 1783985376); cohort-2 from its
own epoch. Cohort-2-pool accounting: 8 admitted / 3 INSUFFICIENT (deepen when
their markets resolve) / 1 REJECT-uncopyable / 1 pending in run-2. The daily
`shadow_readout` cron reads BOTH cohorts separately from the roster ledger.
**Run-2 note:** its results were produced under `d6276f7` — before the
receipts-failed fix (`5eae137`); AUDIT each run-2 JSON: `v2_side_unknown > 0`
(with verdict ≠ rate-REJECT) ⇒ re-run that trader under `5eae137`+ before
trusting mismatch/fabrication/skill. (Run-1's 12 audited: all 0 — clean.)

## HARD FENCES (unchanged)
Never touch `polymarket-mirror3` except via `deploy/mirror3_shadow_deploy.sh`
with operator go; no shared-module edits (`base_engine/**`, `database.py`,
`position_manager.py`, `deploy.sh`, `BotBankrollManager`, `risk_manager`)
without authorization; all CLAUDE.md bans (no `neg_risk` filters, no
order_gateway keying repair, no mirror_scoring validate, `bot_pnl.py` for P&L).

## SESSION END
Update `docs/MB_STATE.md`; **REFRESH the docs-sync branch before any PR** —
`claude/mb-docs-sync-0714pm` must carry the CURRENT MB_STATE (the 2026-07-15
close refreshed it; a stale sync branch is exactly the failure the branch-
versioned-docs protocol exists to prevent — never PR it as-is without checking
its MB_STATE matches the session branch's). PR link:
`https://github.com/swatson124455/polymarket-ai-v2/pull/new/claude/mb-docs-sync-0714pm`.
Write the next handoff prompt.

**Start by reporting:** newest MB_STATE confirmed, batch status (done / running /
died + counts), the ADMIT / REJECT / INSUFFICIENT split with per-trader reasons,
and your proposed next actions. Then wait for the operator's go on any admission.
