# Kickoff prompt for a fresh session

Paste the block below into a new session on `swatson124455/polymarket-ai-v2`.

---
You are continuing the **esports_silo** rebuild. Work on branch
`claude/blissful-davinci-twt397-n94qo6` (current tip — see `git log --oneline`; don't rely on a
pinned SHA).

**BEFORE DOING ANYTHING**, read these three files in full — they override default behavior:
`esports_silo/COMMANDMENTS.md`, `esports_silo/HANDOFF.md`, `esports_silo/PLAN.md`.

Operating discipline (non-negotiable):
- **Verify every claim on data. No assumptions. No fabrication.** Tag each fact
  `VERIFIED` / `DOC-SOURCED` / `UNVERIFIED`. The prior effort logged 186+ errors from
  confident-before-verified — do not repeat it.
- **Commandments:** (1) P&L is not evidence, (2) de-vig does not exist, (3) surgical cut
  (minimal self-contained extraction; label adaptations as DEVIATION, never "port"),
  (4) quarantine by default — anything unverified on real data is excluded until proven clean.
- **You are in a silo:** no VPS / DB / API / network access (`curl` → 403). The only in-repo
  data is `data/esports_matches_bulk.jsonl` and `data/cs2/pandascore_cs2.json` (commit `369606b`),
  locally readable — everything else lives on the operator's box. Anything that needs the box,
  you *write and hand to the operator to run* — never claim you ran it.

**STATE (2026-07-04): the build is COMPLETE.** All pipeline items are built and tested
(`python -m esports_silo.run.selftest` → 14/14 green — run it first to confirm your tree).
The OddsPapi v4 contract is LIVE-VERIFIED end-to-end (see HANDOFF §D3: real slugs, camelCase
params, market 171 = match winner with outcome 171=participant1/172=participant2). ⚠ D3 was
RATIFIED 2026-07-05: **pinnacle is B2B-plan-only on OddsPapi and OUT** — the book set is
singbet+sbobet, and the ≥6-month archive-depth finding (measured on pinnacle) does NOT
transfer; singbet/sbobet depth is UNVERIFIED (softs age out ~2 weeks — probe before the full
backfill, see GO_LIVE_CHECKLIST step 9). The full paid-plan chain exists:
`scripts/backfill_historical_odds.py` → `scripts/fit_calibrator.py` (backfit only — it
fits, it does not prove) → `run/runner.py --predict` (forward ledger, all `no_bet`) →
`scripts/skill_report.py` (THE forward gate). **Do not rebuild any of it.**

**The ball is in the OPERATOR's court** — `esports_silo/GO_LIVE_CHECKLIST.md` is the runbook:
steps 1–8 (DB, import, data-quality gate, keys, dry-runs, timer), then steps 9–12 (paid-plan
backfill → fit → `--predict` timer → weekly skill gate). The operator has committed to a paid
OddsPapi subscription. Your job in this session is to **support that runbook**: interpret
outputs the operator pastes, fix what breaks at the root (surgical, one fix per commit), and
never claim you ran anything that needs the box.

Hard lines: `SILO_ENTRY_HALT=true` until the forward skill gate passes AND the operator
deliberately flips it — a gate PASS alone does not lift the halt. Real keys never go into
git/PR/chat (two old ones leaked and must be rotated — checklist Lane 1). Artifacts in
`esports_silo/artifacts/` are box-local and gitignored.

Do not open a PR unless asked. Commit to the branch with clear messages.
---
