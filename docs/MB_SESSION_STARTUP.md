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

# Session Startup Prompt Template (copy, fill the TASK line, paste as message 1)

Why this file exists: on 2026-07-11 a new session was started with a
from-memory prompt that pointed at `docs/MB_STATE.md` without pinning a
branch. The session read master's 6-day-stale copy — which still listed the
BANNED circular validate rerun as a recommended operator action — and built
its entire plan on it. Prompts are now generated from THIS maintained file,
and step zero is always branch discovery.

---

```
TASK: <one paragraph — what this session works on>

Work in repo swatson124455/polymarket-ai-v2. Create and push to your OWN
branch (claude/<task-name>-<id>).

STEP ZERO — before reading ANY project doc: MB state docs are
BRANCH-VERSIONED and master's copies may be stale or actively wrong.
Discover the authoritative copy:
  git ls-remote origin 'refs/heads/claude/*'
  # for the recently-updated heads:
  git fetch origin <branch> && git show FETCH_HEAD:docs/MB_STATE.md | head -5
Take the newest "Last updated". Read THAT MB_STATE.md (and the companion
docs it names) via git show or by checking the branch out. Do not act on
the working-tree copy until you have confirmed it IS the newest.

THEN read: CLAUDE.md (binding directives — surgical fixes, bot-session
scope, MB priority on shared resources, forbidden patterns,
paper=production) → the newest docs/MB_STATE.md §0.

HARD FENCE — respect everything MB_STATE.md §0 lists as deployed/running.
Never stop, restart, redeploy, or push to anything another session
operates. If your task needs something inside the fence or any shared
module (base_engine/**, database.py, position_manager.py, deploy.sh,
BotBankrollManager, risk_manager), STOP and ask the operator first.

OPERATOR PROTOCOL (the operator runs every VPS command): single-line SSH
one-liners only — ssh -t -i ~/.ssh/LightsailDefaultKey-eu-west-1.pem
ubuntu@18.201.216.0 "..." — no pasting after connecting. NEVER put $ or
escaped double-quotes inside the quoted command (PowerShell mangles both).
pkill only with bracket patterns ('foo[b]ar'). Long jobs: nohup detached,
logs to /tmp, durable copies to /opt/pa2-shared. Never run analysis from
the deployed tree /opt/polymarket-ai-v2 — clone to /tmp/<dir> and use
PYTHONPATH.

DISCIPLINE (binding): pre-register verdict criteria before running
experiments; widen data, never loosen thresholds; numbers only with
coverage/sample qualifiers; canonical scripts over ad-hoc SQL (bot_pnl.py
for P&L); if a result looks impossible the query is wrong — stop and say
so; verified > asserted, label UNVERIFIED honestly.

SESSION END: update the newest MB_STATE.md per docs/MB_HANDOFF_PROTOCOL.md
AND open the mandatory docs-only sync PR to master.

Start by reporting: the branch whose MB_STATE.md you determined to be
newest (name + Last updated line), your working branch, and your plan in
3 bullets. Then wait for my go.
```
