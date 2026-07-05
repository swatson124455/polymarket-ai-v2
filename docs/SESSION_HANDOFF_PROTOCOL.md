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

Run these and confirm — a handoff that references uncommitted state is broken:

1. `git status --short` → **clean** (handoff doc + all work committed).
2. `git log --oneline origin/master..HEAD` → every claimed commit is present.
3. `git ls-remote origin <branch>` → HEAD matches; branch is pushed.
4. Every file path / prompt / command referenced in the handoff **exists on the branch**
   (`git show <branch>:<path>` for the important ones).
5. If work is deployed: the handoff's "what is LIVE" matches a real health check,
   and the rollback command is present and correct.
6. Numbers in the handoff obey the project's rules (e.g. **no P&L** — calibration
   metrics only, per Forbidden Pattern #11).

## Losing nothing — the checklist

- [ ] Handoff doc created, all 8 sections filled, committed + pushed.
- [ ] All code/scripts/prompts committed (nothing in scratchpad that matters).
- [ ] Exact next command/prompt/file named, not described vaguely.
- [ ] Time-gates and decision trees explicit ("if PIT still U-shaped → raise VIF").
- [ ] Deploy state + rollback captured with real release ids/paths.
- [ ] Gotchas/dead-ends recorded so the next session doesn't re-discover them.
- [ ] Scope/ownership + which steps need the VPS spelled out.
- [ ] Working tree clean; branch pushed; `git status` verified.

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
```
