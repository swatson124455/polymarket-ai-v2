# MB STEWARD — NEXT SESSION KICKOFF (copy-trader / shadow lane)

**Written 2026-07-17 ~01:15Z. Fresh, self-contained — supersedes prior
accreted versions.** You are the local MB steward session continuing the
copy-trader lane: a chain-native roster-admission gate is built and running,
8 traders + 1 probe are being shadow-tested at $0 risk, and the forward edge
readout is accruing toward a verdict.

---

## STEP ZERO — orient before touching anything (MB docs are branch-versioned)
```
git ls-remote origin 'refs/heads/claude/*'                 # find the newest MB branch
git fetch origin <branch> && git show FETCH_HEAD:docs/MB_STATE.md | head -15
```
Newest expected on `claude/repo-setup-docs-fq9bhn` (`828ec91`+, Last-updated
2026-07-15/16). Take the newest `Last updated`. THEN read, in order:
`CLAUDE.md` → newest `docs/MB_STATE.md` **§0 ROSTER LEDGER** + **§7 landmines** →
`docs/MB_HANDOFF_PROTOCOL.md`. Do not act on a fact you have not re-read there.

## LOGISTICS (binding)
- All local work from the worktree **`C:/lockes-picks/mb-steward`** (operator
  exception to the parent-dir fence; branch is locked to this worktree). TWO
  writers may share the branch → **`git pull --ff-only` before EVERY commit**,
  push after each unit.
- **VPS: you run commands yourself** via
  `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "..."`.
  Read-only freely. State WHAT + WHY before anything that changes VPS state
  (writes outside `/tmp`, restarts, kills, deploys); the operator approves each.

## THE ONE BINDING SELF-RULE — NO FAST LANE ([[feedback_ship_discipline_no_fast_lane]])
Nothing you build or run touches VPS/live state without ALL of:
1. self-test + pytest green (local AND the VPS venv),
2. an adversarial review pass for any code that produces a verdict/number a
   decision reads (even a single subagent; full multi-lens for the gate),
3. the tool's FIRST output cross-checked against an INDEPENDENT source
   (API vs chain vs DB) before you present or act on it — an impossible-looking
   number means the instrument is wrong until proven otherwise,
4. a timeout on every network await,
5. kill/deploy one-liners composed against the §7 landmines first —
   **a `pkill` command contains the pkill and NOTHING else naming the script**
   (a pgrep or file path in the same command self-kills the session — bit twice).
Every 2026-07-15/16 incident came from skipping this; the disciplined path had
zero. Minutes per step beats the hours each skip cost.

---

## CURRENT STATE (verify each; don't trust)

**Batch (chain_deep_dive gate).** 47 candidates (9 cohort-2 vindicated + grey-4 +
34 HFT-borderline), adjudicated over 3 relaunches (run-1 killed→run-2 hung 13h
on an un-timed RPC await, root-fixed→run-3). At last check **24/47 done**, run-3
grinding. Per-trader JSONs in `/opt/pa2-shared/mb_copyable_data/deep_dive/`;
summary rebuilds from disk after every trader (`_summary_run2.json`, 47-wide
for run-3+). Verdict tally so far ≈ 8 ADMIT / 9 INSUFFICIENT / 11 REJECT.
Heartbeats (`sweep done…`/`receipts N/`) prove liveness — the 13h hang had none.

**Shadow watcher (live, paper, $0).** `polymarket-mirror3`, roster=25:
16 cohort-1 + 8 cohort-2 ADMITs + 1 PROBE (`0xf705fa`). Deployed
`/opt/mirror3`=`5c91261` (hang-guarded copy_watcher). Records to
`/opt/pa2-shared/mirror3_shadow.jsonl`. Cohort epochs & the probe epoch are in
`chain_audit.json` (`cohort1_original`/`cohort2`/`probe` keys).

**Daily readout cron (12:30 UTC).** `deploy/shadow_readout_cron.sh` →
`scripts/shadow_readout.py`: rebuilds resolution labels FRESH from the DB (the
`analyze_shadow` gamma cache is STALE — §7 landmine, never trust it), reads
cohort-1 / cohort-2 / probe **separately** (`load_cohorts` REFUSES on
clean≠c1∪c2∪probe, on overlap, or a bad epoch), discloses top-trader
concentration inline + auto leave-one-out at ≥50%, and writes
`deep_dive/shadow_readout_ALERT.txt` on ≥30 resolved or negative-firming.

## ON SESSION START — CHECK + RELAY
1. `cat /opt/pa2-shared/mb_copyable_data/deep_dive/shadow_readout_ALERT.txt`
   (exists only when triggered) and `tail .../shadow_readout_log.txt`. Relay to
   the operator if triggered. **Present the concentration/LOO with every number**
   ([[feedback_concentration_before_presenting]]) — never a bare pooled figure.
2. Batch: `pgrep -fc 'chain_deep_dive[.]py'`, progress
   `grep -oE '\[[0-9]+/27\]' /tmp/deep_dive_batch.log | tail -1`, JSON count.
3. Watcher healthy: `systemctl is-active polymarket-mirror3`, canary alarms
   `journalctl -u polymarket-mirror3 --since '1 hour ago' | grep -cE 'CANARY ALARM|QUOTE SANITY'` (must be 0).

## PRIORITIES (in order)
1. **[when run-3 finishes] BAND RE-RUN.** The 200–1000 fills/day traders were
   rejected under the OLD cap; the params were reworked (fix-not-bend, `27ee79b`):
   receipt-free reject only >1000 fills/day; the band judged on
   `--max-decisions-per-day 25` (chain first-buys/day) + `--max-flat-share 0.60`
   (net-flat position share = the direct market-maker test). Re-run those
   addresses (938/749/395/371/288 + `0xf705fa` for its formal verdict + any
   run-3 additions) under `27ee79b`+. Whoever now ADMITs → PROPOSE as a probe.
2. **[operator word] Admit any new ADMITs.** Verify tiers first (100% backing,
   0 mismatch, P≥bar, decisions/day & flat-share within caps, no wash/copier),
   then batch-admit with ONE watcher restart: roster `clean` += addrs, extend
   the `cohort2` (or `probe`) ledger key with its OWN `admitted_utc`, rerun
   `deploy/mirror3_shadow_deploy.sh`, record the epoch. Ledger drift fails loud.
3. **[watch, ~1-2 wk] The readout verdict.** Cohort-1 was ~21/30 resolved,
   edge +0.06, P≈0.74 — underpowered, no verdict until ≥30 mkts & P≥0.95.
   EARLY WATCH: cohort-2 OK-rate ~69% vs cohort-1 ~92% — the first hint the
   faster admits' entries are harder to catch at our 2–4s latency; the
   `0xf705fa` probe tests exactly that. Nothing is P&L; communicate via edge.
4. **[optional] Wave-2 candidates:** `0x6bab41a0dc…`, `0x4dfd481c16…` (cached,
   run-1 strong, not yet dived) + the 38 ALL-universe scope-outs. Deferred to
   avoid doubling RPC load next to the live watcher; run after run-3.

## STANDING DISCIPLINE (voids results if broken)
- ADMIT = a PROPOSAL to the operator; nobody joins with real capital — the
  shadow is the pre-capital filter. Cohort-2 & probe readouts are NEVER pooled.
- INSUFFICIENT = deepen (more resolved markets / raise --max-receipts / wider
  window), NEVER an accusation. REJECT = affirmative chain contradiction
  (mismatch/fabrication/disproven-skill) or measured infeasibility only.
- Pre-register thresholds BEFORE a re-run; widen data, never loosen thresholds
  to fit a trader you like. Instrument-error hypothesis FIRST — session score:
  our bugs many, trader lies 0.

## HARD FENCES (unchanged)
Never touch `polymarket-mirror3` except via `deploy/mirror3_shadow_deploy.sh`
with operator go; never touch `polymarket-mirror3` (the old live bot) code; no
shared-module edits (`base_engine/**`, `database.py`, `position_manager.py`,
`deploy.sh`, `BotBankrollManager`, `risk_manager`) without authorization; all
CLAUDE.md bans (no `neg_risk` filters, no order_gateway keying repair, no
`mirror_scoring` validate for decisions, `bot_pnl.py` for P&L, **never quote $
P&L**).

## SESSION END
Update `docs/MB_STATE.md` (ledger + any landmine); refresh the docs-sync branch
`claude/mb-docs-sync-0714pm` so it carries current MB_STATE (open/verify PR #4 —
merge is the operator's click); write the next kickoff prompt.

**START by reporting:** newest MB_STATE confirmed; batch status (done/N,
running/finished, tally); any ALERT + the latest readout line WITH its
concentration; watcher health; and your top-3 next actions. Then wait for go on
anything that admits a trader or changes the roster.
