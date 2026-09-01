# !! OVERHAUL PROGRAM IN FORCE (2026-08-25) - READ BEFORE THE BRIEF BELOW !!
Every new MB session starts by reading, in order:
1. docs/MB_OVERHAUL_REVIEW_AGENDA.md  (the tiered review agenda, 87 verified
   findings; evidence pack docs/mb_overhaul_review_findings.json)
2. docs/MEASUREMENT_CANON.md          (the normative measurement rulebook -
   bare "edge" means the canonical per-market mean, nothing else)
3. the latest [canon] line in deep_dive/label_fee_refresh.log - ALARMS must
   be 0; any alarm means the recorded data disagrees with the chain and NO
   number is quotable until it is explained.
The C1 group is graded ANYTIME-VALID (docs/COHORT1_UNTESTED_AMENDMENT.md);
the original cohort5 twenty keep their 07-30 charter. Evidence is backed up
nightly (VPS 03:30Z bundle + Windows 05:00 pull - check pull.log is fresh).

# MB STEWARD — NEXT SESSION KICKOFF (copy-trader / shadow lane)

**Written 2026-08-20. Supersedes the 07-22 version.** You are the local MB
steward. The lane's posture changed this session: measurement machinery is
DONE and trusted; four pre-registered FORWARD experiments are running; the
job now is to READ them, not to build.

---

## STEP ZERO
```
git ls-remote origin 'refs/heads/claude/*'
git fetch origin claude/repo-setup-docs-fq9bhn
```
Newest is on **`claude/repo-setup-docs-fq9bhn`**. Read in order: `CLAUDE.md` →
newest `docs/MB_STATE.md` **§0 (the 2026-08-20 SESSION CLOSE block)** + **§7
landmines** → `docs/MB_HANDOFF_PROTOCOL.md` → `docs/BAND_PREREGISTRATION.md` +
`docs/BIDSIM_DESIGN.md` (the two live experiment charters).
Worktree **`C:/lockes-picks/mb-steward`**; `git pull --ff-only` before every
commit; push after each unit.

## BINDING RULES (all operator-ratified, all bit us before)
1. **FORWARD DATA ONLY (2026-08-20):** in-sample/backtest numbers carry ZERO
   decision weight — hypothesis-generation only, never evidence. Decision
   thresholds are recomputed from forward data at decision time. Venue
   calibrations (fee formula validated vs live charged fees, CLOB labels)
   remain usable as infrastructure.
2. **NO FAST LANE:** self-test + pytest green, adversarial review for any
   verdict/number code, first output cross-checked vs an INDEPENDENT source,
   timeout on every network await, targeted pkill patterns.
3. **NON-EMPTY ASSERTS EVERYWHERE:** a diff/count over zero rows printing
   success is this lane's worst failure mode (bit repeatedly).
4. **Verdicts/locks are immutable; roster changes are operator-gated;
   pre-registered epochs never move.** PRICE_NO_UPSIDE cap ruled KEEP AS IS
   (0.98). Verify-don't-trust everything below.
5. SSH: `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0`
   — single-quoted payloads, PURE ASCII uploads. `/tmp` is wiped on reboot:
   orchestration scripts have repo copies in `scripts/vps_jobs/`.

## THE FOUR LIVE FORWARD INSTRUMENTS (verify each, then mostly leave alone)
1. **Band 0.65–0.85 e-process test** — epoch 2026-08-19T18:00Z; reject at
   e>=20; futility at 600 resolved band markets; econ floor (+0.02) applied
   only after rejection; lock `deep_dive/band_lock.json`. Daily `[band]` line
   in `deep_dive/label_fee_refresh.log` (11:40Z cron). Last read 08-20:
   n=11, e=0.717 — meaningless n, let it accrue.
2. **Shadow-bid simulator** (chase-vs-post decider) — sink
   `/opt/pa2-shared/mirror3_bidsim.jsonl`; bid at whale price in-band; fill =
   any print <= bid (queue-OPTIMISTIC; the 07-19 snapshot study is the
   conservative bracket); expire 24h. First reads: 13/13 resolved bids
   filled. At **~100 resolved bids**: produce the chase-vs-post proposal from
   FORWARD data on both arms (forward taker edge vs forward fill-rate ×
   maker edge) — NOT any in-sample threshold.
3. **cohort5 qualification** (daily cron; single-look locks in
   `deep_dive/cohort5_qual_locks.json`; 3 consumed, all DOES NOT QUALIFY) and
   **cohort4** (verdict already LOCKED NOT DEMONSTRATED — diagnostic only).
4. **probe(1) = 0xfbfd14dd** watch (observation-only; maker_frac 0.886
   objection on record). Roster = 31; header `15+8+6+1+1probe`.

**Also running:** scout sweep #1 remainder — 6 dives relaunched
2026-08-20T16:41Z (`/tmp/scout_queue3_main.log`; 3 of 9 already REJECT).
Verdicts are PROPOSALS; admission = operator gate.

## THE FINAL PLAN (operator-ratified, over-correction-reviewed)
- Let the four instruments vote. Do NOT add instruments until one reports.
- **ONE build allowed:** a unified daily scoreboard block in the cron log
  (band e-value + bidsim counts + cohort5 accrual + scout status in one
  glance). Nothing else.
- PARKED as overcompensation (operator-reviewed; re-raise only with cause):
  weekly scout cadence (decide after sweep #1 reports), auto-promote funnel,
  dual RTDS sockets.
- OPEN OPERATOR DECISIONS: conditional funding number; scout cadence.
- TRIPWIRES: band e>=20 (PASS → live-bot proposal + funding) or n=600
  (futility close); bidsim ~100 resolved → chase-vs-post proposal; scout
  completions → admission proposals.

## START by reporting
1. Date check (sessions have jumped days — `date -u` first, re-derive
   everything at speaking time).
2. The latest `[band]` line + bidsim post/fill/expire counts + scout sweep
   status + cohort5 accrual + RTDS coverage spot-check (join both sinks,
   expect ~3% chain-only) + watcher health (active, 0 DIED).
3. Any tripwire crossed → the corresponding proposal, from forward data only.
4. Then WAIT for operator direction. No roster changes, no new experiments,
   no epoch changes without explicit go.

## KNOWN STATE OF THE WORLD (calibrations — usable infrastructure)
- Venue fee = C·rate·p·(1−p); rates crypto .07 / sports+other .05 /
  politics-class .04 / geopolitics 0 — validated vs 3,070 live charged fees.
- Wallet (last on-chain read 07-25): ~2.24 pUSD (deposit proxy) + ~9.34
  MATIC (EOA). Funding is an open operator decision.
- RTDS venue cycles connections ~6/h regardless of pings (PING theory
  refuted); 15s silent-window keeps the loss at ~3%.
- `/opt/mirror3` deploy == repo `mirror_v3/` (verify byte-identity before
  editing). Backups `copy_watcher.py.pre-*-2026081*` on the VPS.
