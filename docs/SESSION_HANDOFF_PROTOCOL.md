# Session Handoff Protocol

How to hand off a session in this repo so **nothing is lost** and the next session
can resume in minutes. Sessions are ephemeral (cloud clones are reclaimed; local
sessions end) — anything not committed is gone. Treat the handoff doc as the
durable memory.

---

## When to write a handoff

- Before ending any session that leaves work in-flight, deployed, or pending verification.
- When context is about to be summarized/compacted and state is complex.
- Whenever the operator says "handoff", "wrap up", or switches sessions/silos.

## The one rule

**If it isn't committed and pushed, it doesn't survive.** The handoff doc, the code,
the scripts, the verification prompts — all get committed to the working branch.
Scratchpad files, chat-only explanations, and uncommitted edits are lost.

---

## How the next session resumes (automated — WeatherBot)

The hand-typed resume checklist has been replaced by a self-deriving harness. A resuming
WB session should do exactly this, in order:

1. **`bash scripts/wb_resume_check.sh`** — verifies repo/branch, that HEAD == the pinned
   remote branch, that the headline-fix SHAs are in history, that the required docs exist,
   that code fingerprints match, and reports deploy parity. Everything "expected" is derived
   from `docs/WB_HANDOFF_MANIFEST.json` + git — there are **no hardcoded SHAs or counts** to
   go stale. ALL PASS → resume. Any FAIL → stop, the script prints the exact remediation.
2. **Read `docs/WEATHER_STATUS.md`** — the canonical, always-current status (stable filename).
   Its top `## OPEN DECISIONS` section is the first thing to act on; `## WHAT IS LIVE` is the
   current deploy state. Session-stamped docs (`WEATHER_S222_STATUS.md`, the fallacy audit)
   are archival deep-dives it links to.
3. Live-VPS **health** still needs the deploy key (keyless/cloud sessions can't reach it);
   the ssh one-liner is in the header of `scripts/wb_resume_check.sh`.

**Branch pinning:** the pinned branch lives in `.claude/session-branch`; a committed
SessionStart hook (`.claude/hooks/session-start-pin-branch.sh`) auto-checks-it-out in cloud
sessions so a fresh session can't drift onto its own branch. If you intentionally move the
session to a new branch, update `.claude/session-branch` **and** the manifest's `branch` field.

### What a WB handoff must keep in sync (same commit as the work)

- **`docs/WB_HANDOFF_MANIFEST.json`** — if you add a headline fix, change a fingerprinted
  marker's count, or move the branch, update the manifest in the SAME commit. The resume
  check FAILs loudly if you forget, which is the point (drift is caught, not silent).
- **`docs/WEATHER_STATUS.md`** — update `OPEN DECISIONS`, `WHAT IS LIVE`, and prepend a
  one-line `CHANGELOG` entry at session end. This file is the current truth; keep it current.
- **`deploy/LAST_DEPLOY.json`** — after any deploy, run
  `bash deploy/wb-record-deploy.sh <STAMP>` (locally, where git lives — `wb-release-cut.sh`
  runs on the VPS from a `.git`-excluded tarball and can't know the SHA) and commit the
  result, so keyless sessions can verify what's actually live.

---

## Handoff document template

Create `docs/HANDOFF_<TOPIC>.md`, committed to the working branch. Fill every section;
write "N/A" rather than deleting a heading (a missing heading reads as "forgot").

```
# HANDOFF — <topic>

**Session:** <silo> · **Branch:** <branch> · **HEAD:** <sha> · **Status:** <one line>
**Date:** <YYYY-MM-DD> · **Mode:** PAPER/LIVE

## 0. TL;DR for the next session
   3-5 sentences: what changed, what's live, the single most important "do not do X yet".

## 1. What is LIVE right now
   Deployed? which release/sha? health-verified how? rollback command. If nothing
   deployed, say so explicitly.

## 2. The diagnosis / why this work exists
   The problem in the operator's terms + the evidence. Link the source docs/commits.
   Note any binding directives (e.g. Forbidden Pattern #11 — never quote P&L).

## 3. What was done
   Table of commits: sha | change | why. Note tests (pass count + that each fix has a
   defect-reproducing test). Note which files/copies were touched and which were left.

## 4. PENDING WORK — exact next steps
   Numbered, executable. Include the EXACT command/prompt/file to run next, any
   time-gate ("wait ~1 week for ≥N samples"), and the decision tree for the result.

## 5. Gotchas / traps discovered
   Dead config, double-defined values, environment quirks, false leads. Each with
   file:line and "flag to operator, don't silently change" where relevant.

## 6. Deploy / ops mechanics
   How it deploys (the ACTUAL working path, not the aspirational one), rollback,
   health check, and any half-built automation + its known issues.

## 7. Scope & constraints
   Which silo, which files are owned vs MB-priority, binding CLAUDE.md rules,
   what needs the VPS/DB and therefore can't run in a cloud sandbox.

## 8. Key file map
   The 8-12 files the next session will actually open, one line each.
```

---

## Verification of the handoff (do this before ending)

For WeatherBot, **`bash scripts/wb_resume_check.sh` is the fast gate** — it must exit ALL
PASS after your final push (it covers items 1-4 below mechanically). Then confirm the rest —
a handoff that references uncommitted state is broken:

1. `git status --short` → **clean** (handoff doc + all work committed).
2. `git log --oneline origin/master..HEAD` → every claimed commit is present.
3. `git ls-remote origin <branch>` → HEAD matches; branch is pushed.
4. Every file path / prompt / command referenced in the handoff **exists on the branch**
   (`git show <branch>:<path>` for the important ones).
5. If work is deployed: the handoff's "what is LIVE" matches a real health check,
   and the rollback command is present and correct. Record it with
   `deploy/wb-record-deploy.sh` so the next session can verify parity without the key.
6. Numbers in the handoff obey the project's rules (e.g. **no P&L** — calibration
   metrics only, per Forbidden Pattern #11).
7. WB only: `docs/WEATHER_STATUS.md` and `docs/WB_HANDOFF_MANIFEST.json` reflect this
   session's end state (open decisions, changelog, any new fix SHA / fingerprint).

## Losing nothing — the checklist

- [ ] Handoff doc created, all 8 sections filled, committed + pushed.
- [ ] All code/scripts/prompts committed (nothing in scratchpad that matters).
- [ ] Exact next command/prompt/file named, not described vaguely.
- [ ] Time-gates and decision trees explicit ("if PIT still U-shaped → raise VIF").
- [ ] Deploy state + rollback captured with real release ids/paths.
- [ ] Gotchas/dead-ends recorded so the next session doesn't re-discover them.
- [ ] Scope/ownership + which steps need the VPS spelled out.
- [ ] Working tree clean; branch pushed; `git status` verified.
- [ ] **WB:** `scripts/wb_resume_check.sh` exits ALL PASS after the final push.
- [ ] **WB:** `docs/WEATHER_STATUS.md` open-decisions + changelog updated this session.
- [ ] **WB:** manifest (`docs/WB_HANDOFF_MANIFEST.json`) updated if a fix SHA, fingerprint,
      or the pinned branch changed.
- [ ] **WB:** if deployed, `deploy/wb-record-deploy.sh <STAMP>` run and committed.

---

## Continuity rules specific to this repo

- **Silo scope binds across the handoff.** A WB handoff hands off WB work; do not
  smuggle in EB/MB/shared changes. Shared-resource items get flagged for the owning
  silo, not done.
- **The verification/measurement layer needs the VPS DB** (`localhost:5432` on the
  box). Cloud-sandbox sessions cannot run it — the handoff must route those steps to
  a VPS-access session, with the exact prompt file to paste.
- **Deploys are tarball splinter releases**, not git-on-VPS. The handoff must carry the
  actual working deploy command + rollback, because the "obvious" `git pull` path does
  not exist on the box.
- **P&L is unreliable and must never be quoted** (Forbidden Pattern #11). Handoffs
  communicate bot quality via calibration (Brier/PIT/reliability) and hit-rate only.
- **One-fix-per-commit / test-before-after / snapshot-first** (CLAUDE.md) applies to
  the handoff work itself, including the handoff commit.
- **Handoff state lives in git, never only in local memory.** Cloud sessions clone fresh —
  a memory-store key does not travel. Anything the next session must read goes in a
  committed file (the manifest, `WEATHER_STATUS.md`, the handoff doc), not memory alone.
- **The session branch is pinned in `.claude/session-branch`.** Don't rely on a fresh
  session landing on the right branch by luck; if you move branches, move the pin (and the
  manifest `branch` field) with it.
