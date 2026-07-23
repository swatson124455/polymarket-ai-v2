# MB STEWARD — NEXT SESSION KICKOFF (copy-trader / shadow lane)

**Written 2026-07-22. Supersedes the 07-19 version.** You are the local MB
steward. The lane's headline changed this session: **the edge numbers were
flattered by incomplete resolution labels**, that is now fixed for our markets,
and a re-review of every ADMIT is in flight. Everything below is
verify-don't-trust.

---

## STEP ZERO
```
git ls-remote origin 'refs/heads/claude/*'
git fetch origin <branch> && git show FETCH_HEAD:docs/MB_STATE.md | head -20
```
Newest is on **`claude/repo-setup-docs-fq9bhn`**. Read in order: `CLAUDE.md` →
newest `docs/MB_STATE.md` **§0 (the 2026-07-22 block)** + **§7 landmines (esp.
the 07-22 additions)** → `docs/MB_HANDOFF_PROTOCOL.md`.

## LOGISTICS (binding)
- Worktree **`C:/lockes-picks/mb-steward`**; `git pull --ff-only` before EVERY
  commit; push after each unit.
- VPS: you run commands yourself —
  `ssh -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem ubuntu@18.201.216.0 "..."`.
  Read-only freely; state WHAT + WHY before any state change.
- **Compose SSH payloads single-quoted from Git Bash. Sanitize uploaded scripts
  to PURE ASCII** — em-dashes/`§` mangle in transit and break bash parsing.
- **PEER RULE (07-20):** no bot has right of way on shared resources.
  Coordinate; serialize on the shared RPC. MB-owned resources unaffected.

## THE ONE BINDING SELF-RULE — NO FAST LANE
Nothing touches VPS/live state without: self-test + pytest green, an adversarial
review for any code producing a verdict/number, **first output cross-checked vs
an INDEPENDENT source**, a timeout on every network await, and kill/deploy
one-liners composed against §7. **And the 07-22 addition: any comparison must
assert its inputs are NON-EMPTY.** An empty-set "IDENTICAL"/"FLIPPED: 0" is the
single most dangerous failure mode in this lane — it bit twice in one session.

---

## CURRENT STATE (verify each)

**Live roster = 30** (`chain_audit.json`): cohort1(15, REDUCED) + cohort2(8) +
cohort3(6) + benched(1). `polymarket-mirror3` shadow, paper, $0, 0 alarms.
Daily readout 12:30Z renders `15+8+6+1benched`; cohort1 prints
`REDUCED :: NO VERDICT` by design (post-hoc cut, can never "survive").

**The bum `0x44886115` is BENCHED** (time-out, from_cohort=cohort1, stays
watched). Three independent lines vindicate it: shadow drag −0.1051, forward
line, and a chain deep-dive verdict of INSUFFICIENT (edge +0.0031, P=0.678) on
his cleanest sample. **Re-admission bar (pre-registered): forward-since-bench
edge ≥ +0.02 AND P ≥ 0.90 on ≥ 20 resolved → propose (operator go).**

**Labels are FIXED for our markets** — CLOB supplement added 14,791 labels
(gap 32% → 1.9%). Do NOT trust `markets.resolved` for shadow markets; the
shared backfill structurally cannot see them.

**⏳ IN FLIGHT — ADMIT RE-REVIEW (the key deliverable).**
`/tmp/admit_rereview3.sh`, 20 ADMITs, out-dir
`/opt/pa2-shared/mb_copyable_data/deep_dive_rereview/` (originals preserved).
Check: `tail /tmp/rereview3_main.log`; JSON count in the out-dir; the final
before/after table with `<== FLIPPED`. **If it shows FATAL / 0 compared, it did
NOT run — do not read that as "all survived".**

## PRIORITIES

1. **[first] Read the re-review diff.** Any FLIPPED ADMIT is a PROPOSAL only —
   no roster change without operator go. Pay special attention to the three
   cohort-3 members graded on ~50% evidence (`0x216509be`, `0x7c3db723`,
   `0xe542afd3`); cohort-3's live edge is currently NEGATIVE.
2. **[then] Re-grade the 5 label-starved INSUFFICIENTs** — they may have been
   wrongly shelved by the same gap.
3. **[watch] Readout.** Nothing has cleared the bar (≥30 resolved AND P≥0.95 AND
   edge≥+0.02). cohort2 18/30. cohort3 accruing from a fresh epoch.
4. **[operator-gated, shared infra] (a)** backfill poison-batch ordering;
   **(b)** `end_date_iso` NULL on 56% of markets; **(c)** the 123h stuck
   force-exit loop on `polymarket-mirror`; **(d)** master docs-sync PR.
5. **[optional]** deepen wave (13 run-4 INSUFFICIENTs), `0x70d94a` solo deepen.

## WHAT THE DATA IS AND ISN'T (say this plainly to the operator)
- **CANON:** the whale trades. 691,201 chain-verified BUYs, 0 mismatch. Every
  shadow record carries an on-chain `tx`.
- **MODEL, never executed:** our fills. ~89% have order-book depth behind them;
  **zero** were executed. `bot_pnl.py` is empty for this lane by construction.
- **EV of tailing = the `edge` figure** (per share, net of fee, at OUR entry).
  Currently ~+0.03/share cohort1-2, NEGATIVE cohort3, none significant.
  The tailing tax is ~1c/share + 2% fee against a +0.02–0.05 trader edge —
  **that tax is the central risk to this strategy's EV.**
- **NEVER quote $ P&L** (CLAUDE.md #11). Communicate via edge/calibration.

## SESSION END
Update `docs/MB_STATE.md` §0 + §7, refresh this prompt, push every unit,
propose the master docs-sync PR.

**START by reporting:** newest MB_STATE confirmed; re-review result (or its
gate state); any ALERT + the latest readout line WITH concentration; watcher
health; top-3 next actions. Then wait for operator go on anything that admits a
trader or changes the roster.
