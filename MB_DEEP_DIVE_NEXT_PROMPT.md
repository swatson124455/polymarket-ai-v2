# MB STEWARD — NEXT SESSION KICKOFF (copy-trader / shadow lane)

**Written 2026-07-19 ~23:30 UTC. Fresh, self-contained — supersedes prior
accreted versions.** You are the local MB steward continuing the copy-trader
lane: a chain-native admission gate (run-4) is finishing, 25 traders are
shadow-tested at $0, and a wave of newly-cleared ADMITs is queued for one
batched promotion. Everything below is verify-don't-trust.

---

## STEP ZERO — orient before touching anything (MB docs are branch-versioned)
```
git ls-remote origin 'refs/heads/claude/*'
git fetch origin <branch> && git show FETCH_HEAD:docs/MB_STATE.md | head -20
```
Newest is on **`claude/repo-setup-docs-fq9bhn`** (head `329444e`+, Last-updated
2026-07-19). Take the newest `Last updated`. THEN read, in order:
`CLAUDE.md` → newest `docs/MB_STATE.md` **§0 (esp. blocks 9–14)** + **§7
landmines** → `docs/MB_HANDOFF_PROTOCOL.md`. Master's MB_STATE is behind (last
docs-sync `ca97b4d` = S-0717/18); the BRANCH is authoritative — a master
docs-sync is an optional operator-gated proposal, not a blocker.

## LOGISTICS (binding)
- Work from the worktree **`C:/lockes-picks/mb-steward`** (operator exception
  to the parent-dir fence; branch locked to this worktree). TWO writers may
  share it → **`git pull --ff-only` before EVERY commit**, push after each unit.
- **VPS: you run commands yourself** —
  `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "..."`.
  Read-only freely. State WHAT + WHY before any state change (writes outside
  /tmp, restarts, kills, deploys); operator approves each.
- **PowerShell one-liners: never include `$` or `"`** inside the SSH string.
  Compose SSH payloads SINGLE-QUOTED from Git Bash (double-quoted shatters on
  Windows client-side expansion — bit us 07-17).

## THE ONE BINDING SELF-RULE — NO FAST LANE ([[feedback_ship_discipline_no_fast_lane]])
Nothing you build/run touches VPS/live state without ALL of: (1) self-test +
pytest green (local AND VPS venv), (2) an adversarial review pass for any code
that produces a verdict/number a decision reads, (3) the tool's FIRST output
cross-checked vs an INDEPENDENT source before you act on it, (4) a timeout on
every network await, (5) kill/deploy one-liners composed against §7 first — a
`pkill` command names the target and NOTHING else. This session's two workflow
reviews + one root-cause audit EACH caught a real defect a single pass missed;
the money-gate surface earns the multi-lens adversarial pass.

---

## CURRENT STATE (verify each; don't trust)

**Run-4 (chain_deep_dive fair-params re-adjudication).** At handoff: **19/28
done, ALIVE (pid 3269649, log `/tmp/deep_dive_run4.log`).** ~9 traders left
(some heavy — 27k-receipt fetches ~1h each). Per-trader JSONs in
`/opt/pa2-shared/mb_copyable_data/deep_dive/`; summary rebuilds from disk.
Overall tally ≈ 10 REJECT / 14 ADMIT / 15 INSUFFICIENT.
**PROMOTION QUEUE = 6 fresh ADMITs** (all chain-verified: 100% backing, 0
mismatch, P≥0.9975): `0xf705fa04` (probe, graduates), `0x7c3db723`,
`0xe542afd3`, `0x216509be`, `0x2ee04b8b`, `0xa6a856a8`. **May grow** — check
for new ADMITs before promoting.

**Shadow watcher (live, paper, $0).** `polymarket-mirror3`, roster=25 (16
cohort-1 + 8 cohort-2 + 1 probe). `/opt/mirror3` hang-guarded. Sink
`/opt/pa2-shared/mirror3_shadow.jsonl`. Cohort epochs in `chain_audit.json`.
At handoff: active, 0 canary/quote alarms.

**Daily readout cron (12:30 UTC).** `scripts/shadow_readout.py` (fresh DB
labels; per-cohort split; refuses on ledger drift; auto leave-one-out at ≥50%
conc). **NEW: supports any `cohort<N>` group** (generalized `load_cohorts`) —
so the cohort3 promotion works; verified differential-identical on the live
16+8+1probe roster + a VPS dry-run. Cron auto-adopts branch head at 12:30Z.
Latest line (12:30Z): cohort1 27/30 edge +0.031 P=0.66; cohort2 13/30 edge
+0.009 P=0.54; probe 0 resolved. UNDERPOWERED, no alert.

**Local watchers die with the prior session.** There is NO live batch-end
watcher now — YOU must check run-4 status on start and, if still running,
re-arm a poller or check periodically (pattern: PID-pinned `ps -p 3269649`,
timeout every ssh, confirm-twice before declaring exit).

## ON SESSION START — CHECK + RELAY (present every number with its concentration)
1. `cat /opt/pa2-shared/mb_copyable_data/deep_dive/shadow_readout_ALERT.txt`
   (exists only when triggered) + `tail shadow_readout_log.txt`. Relay if triggered.
2. Run-4: `pgrep -fc 'chain_deep_div[e].py'`; `grep -E '\[[0-9]+/28\]'
   /tmp/deep_dive_run4.log | tail -1`; JSON count + tally + fresh-ADMIT list.
   If DONE (pid 0) → the promotion is ready (Priority 1).
3. Watcher: `systemctl is-active polymarket-mirror3`;
   `journalctl -u polymarket-mirror3 --since '1 hour ago' | grep -cE 'CANARY ALARM|QUOTE SANITY'` (must be 0).

## PRIORITIES (in order; all gated at the run-4 batch boundary)
1. **[when run-4 exits, operator word] COHORT-3 PROMOTION — one fenced restart.**
   Roster = ALL run-4 ADMITs (6+). Procedure:
   - Backup: `cp chain_audit.json chain_audit.json.pre-cohort3-<date>`.
   - Edit `chain_audit.json` (polymarket-owned): `clean` += the 5 NEW addrs
     (0xf705fa already in clean) → 30+; ADD `"cohort3": {"addresses":[the 6+],
     "admitted_utc":"<restart ISO8601>"}`; EMPTY `"probe":{"addresses":[],...}`
     (0xf705fa graduates). INVARIANT: clean == cohort1_original ∪ cohort2 ∪
     cohort3 = 16+8+6 = 30. **Validate OFFLINE before deploy**: run
     `load_cohorts` on the edited file — must not raise, must show 16+8+6.
   - Deploy: `deploy/mirror3_shadow_deploy.sh` (operator go) → verify roster=30
     in journal, first canary >0, 0 alarms. Record the cohort3 epoch in §0.
   - Next 12:30Z readout must show `16+8+6` + a fresh cohort3 line, or it
     RAISES (ledger drift) — fix before trusting any line.
2. **[after run-4, before deepen wave] FILL-CACHE PROOF GATE (pre-registered).**
   On the idle RPC: (i) `chain_fill_cache.populate_multi` vs per-addr
   `sweep_lifetime` over the same block range for 2-3 addrs → fill sets IDENTICAL;
   (ii) a full re-dive of ONE completed trader via `--fill-cache-dir` → verdict
   + tier-1/2 counts match its existing JSON EXACTLY. PASS → deepen wave uses
   the flag (~10-25× cheaper sweeps). FAIL → deepen wave runs FLAG-OFF (old
   path, no function lost). `chain_deep_dive.py --fill-cache-dir <cache>/chain_fills`.
3. **[after proof gate] DEEPEN WAVE** — 9 confounded INSUFFICIENTs re-dived
   under fresh caches/labels + new gate code (rename their non-ok caches to
   `.hft-bak` first; bare-address roster file; detached launch). Then
   **`0x70d94a` solo deepen** at `--max-receipts 120000` (~4h receipts), last.
4. **[watch, ~1-2 wk] Readout verdict.** Cohort-1 ~27/30 resolved, edge
   drifting mildly POSITIVE (+0.031, P=0.66) — UNDERPOWERED, no verdict until
   ≥30 resolved AND P≥0.95 AND ≥+0.02 floor. HONEST: at ~5 resolutions/day and
   this noise, a MODEST true edge needs hundreds of markets to CONFIRM; the
   next few resolutions can only FAIL or "keep collecting", not PASS. Nothing
   is P&L; communicate via edge.
5. **[self-triggers] Forward stack-vs-first-buy test** — `scripts/
   stack_vs_firstbuy_forward.py` runs itself at 30 DISTINCT resolved multi-buy
   markets (clusters). Answers whether re-buying beats one-bet-per-market.
6. **[optional, operator word] Wave-2 candidates** (`0x6bab41`, `0x4dfd48` +
   38 ALL-universe scope-outs) — now cheap with the multi-sweep cache.

## LANDMINES ADDED THIS SESSION (also see §7)
- **run-4 code is `27ee79b` (new params) — NOT the old cap-200.** The old cap
  wrongly rejected copyable HFT-borderline traders; the fair re-test keeps
  recovering them (6 ADMITs so far). Band = 200–1000 fills/day judged on
  decisions/day ≤25 + flat-share <0.60.
- **hft-status API cache (~500 rows) makes ADMIT UNREACHABLE** (starved
  token→cond map). Rename to `.hft-bak` to force a full re-fetch before diving.
- **gamma cache goes stale → suppresses recent labels.** Refresh via
  `scripts/backfill_resolutions_gamma.py` before a dive that needs fresh labels.
- **data-api trade record is a SUBSET of chain truth** (probe: 28,926 API vs
  60,576 chain BUYs). NEVER grade a high-rate trader's entry pattern from
  /activity alone — use the shadow's executable-price records.
- **`ssh 'cmd &'` with a stale /tmp logfile owned by another user = silent
  no-launch.** Use fresh log names + a LAUNCHED/ABORTED echo marker + an
  alive-check after ~10s.
- **VPS network blips happen (07-19 ~30min).** A ping timeout is NOT a reboot
  (check `uptime -s`). Services run independent of your SSH; do NOT relaunch
  run-4 on an unconfirmed outage (a reboot-assumption would two-writer-conflict
  a still-running batch). Boot time months-old = it never rebooted.
- **cohort_readout empty-members = ZERO records** (root fix) — the empty-cohort
  and dup guards in load_cohorts are ledger-integrity guards on top; both
  layers must stay.

## HARD FENCES (unchanged)
Touch `polymarket-mirror3` ONLY via `deploy/mirror3_shadow_deploy.sh` with
operator go; no shared-module edits (`base_engine/**`, `database.py`,
`position_manager.py`, `deploy.sh`, `BotBankrollManager`, `risk_manager`)
without authorization; all CLAUDE.md bans (no `neg_risk` filters, no
order_gateway keying repair, no `mirror_scoring` validate for decisions,
`bot_pnl.py` for P&L, **NEVER quote $ P&L** — communicate via calibration/edge).
ADMIT = a PROPOSAL to the operator; nobody joins with real capital — the
shadow is the pre-capital filter. Cohorts NEVER pooled. INSUFFICIENT = deepen,
never accuse. Pre-register thresholds BEFORE a re-run.

## SESSION END
Update `docs/MB_STATE.md` §0 (consolidate — the accreted blocks are long) + any
new §7 landmine; refresh this prompt; push every unit. Optionally propose a
master MB_STATE docs-sync (operator-gated).

**START by reporting:** newest MB_STATE confirmed; run-4 status (done/N,
tally, fresh-ADMIT list); any ALERT + the latest readout line WITH its
concentration; watcher health; your top-3 next actions. Then wait for
operator go on anything that admits a trader or changes the roster.
