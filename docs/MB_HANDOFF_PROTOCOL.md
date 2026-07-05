# MB Handoff Protocol

How any MirrorBot session (human or agent) hands off to the next. Keep it short,
verified, and honest. The goal: the next session is productive in 5 minutes and
cannot trip a known landmine.

## On every session START

1. Read in order: `CLAUDE.md` → `MB_REBUILD_PLAN.md` → `docs/MB_STATE.md`.
2. `git fetch` and check branch `claude/mirror-bot-salvage-rebuild-d08v6x` vs `master`.
3. Confirm live state before touching anything: is the bot paper or live?
   `deploy/mb_vps_oneshot.sh` §1 (or the runbook) answers it. **Never assume.**
4. Re-derive any number you're about to rely on. Prior figures are stale until
   re-measured (`scripts/verify_salvage_data.py`, `scripts/bot_pnl.py`).

## On every session END — update `docs/MB_STATE.md`

Edit these sections so they reflect reality (not intentions):
- **§1 One-paragraph state** — rewrite if the headline changed.
- **§2 Current system state** — paper/live, config, any measured number, with source.
- **§4 What's built** — add rows for new modules (path + test count + state).
- **§5 Open threads** — tag each `[operator]` or `[build]`; delete done ones.
- **§7 Landmines** — add any new one you discovered.
Bump the "Last updated" date + the commit SHA line. Commit the handoff with your work.

## Rules that make handoffs trustworthy

1. **Verified > asserted.** Every claim carries a source (file:line, test name, or
   script output). If you couldn't verify it, label it UNVERIFIED.
2. **One fix per commit**, with the CLAUDE.md change-log block on code changes.
3. **Don't cross lanes.** MB session touches MB code. Statistical scoring lane is
   `mb-formula-review`'s. Odds-vendor (esports) is EB's. Hand findings across as
   recommendations; don't apply them in someone else's lane.
4. **Secrets never land in the repo or in chat.** Report keys as set/unset only.
   Env keys are read from the environment, never hardcoded.
5. **Operator actions are the operator's.** Sessions don't SSH, deploy, push to
   `master`, or move real money. Hand the operator a single copy-paste and the
   expected output.
6. **Shadow until gated.** No strategy reaches capital without passing the
   acceptance gate (`bots/mirror_backtest`). Label pre-gate output UNVERIFIED.
7. **Surface uncertainty.** If a number looks impossible, say so and fix the query
   before reporting. Silence about doubt is a defect.

## Cross-silo handoff (when another bot's data/capability is needed)

- Send a **read-only, reply-only** request naming exactly what you need and the
  no-secrets rule (see the EB odds request in session history as the template).
- Prefer a machine-readable capability entry at repo root (like
  `SALVAGE_PACKAGE.json`) so any silo can discover it.
- MB has priority on shared resources; still, ask before assuming another lane
  will do work for you.

## Operator-facing handoff (plain-language)

When the reader can't code: give the *why* in one line, the *exact paste* in a
block, and the *expected result* to look for. One action at a time. A refusal-to-
run with a clear message (e.g. env_guard) is the system working, not breaking —
say so.
