#!/bin/bash
# SessionStart hook — pin cloud sessions to the branch recorded in .claude/session-branch.
#
# WHY: fresh Claude Code on-the-web sessions spin their own claude/* branch instead of
# resuming the session thread's branch (observed 2026-07-08: session started on
# claude/weatherbot-s223-integrity-* instead of the pinned WB S222/S223 branch).
# This hook makes the pin travel WITH the repo, so every cloud session lands on the
# right branch before the agent's first turn.
#
# SAFETY RULES (all deliberate — do not "simplify" away):
#   - Remote-only ($CLAUDE_CODE_REMOTE): never yanks the operator's local working tree.
#   - No-op when .claude/session-branch is absent on the checked-out branch — so this
#     hook does nothing for MB/EB sessions or master until they opt in with a pin file.
#   - Never switches a dirty tree; warns instead.
#   - Never force-checkouts; a failed fetch/checkout warns and stays put.
#   - No-op inside .claude/worktrees/* (worktree isolation is intentional).
set -uo pipefail

# Remote (Claude Code on the web) sessions only.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
[ -n "$ROOT" ] || exit 0

# Don't touch agent worktrees — their branch isolation is intentional.
case "$ROOT" in
  */.claude/worktrees/*) exit 0 ;;
esac

PIN_FILE="$ROOT/.claude/session-branch"
[ -f "$PIN_FILE" ] || exit 0
PINNED="$(head -n1 "$PIN_FILE" | tr -d '[:space:]')"
[ -n "$PINNED" ] || exit 0

CURRENT="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null)" || exit 0

if [ "$CURRENT" = "$PINNED" ]; then
  echo "[session-branch] already on pinned branch '$PINNED' ($(git -C "$ROOT" log --oneline -1))"
  exit 0
fi

# Never move a dirty tree — surface the conflict for the agent/operator to resolve.
if ! git -C "$ROOT" diff --quiet 2>/dev/null || ! git -C "$ROOT" diff --cached --quiet 2>/dev/null; then
  echo "[session-branch] WARNING: on '$CURRENT' but pinned branch is '$PINNED' — working tree is DIRTY, not switching. Resolve manually (stash/commit, then: git checkout $PINNED)."
  exit 0
fi

if ! git -C "$ROOT" fetch origin "$PINNED" --quiet 2>/dev/null; then
  echo "[session-branch] WARNING: could not fetch origin/$PINNED — staying on '$CURRENT'. Check the pin file or network, then: git fetch origin && git checkout $PINNED"
  exit 0
fi

if git -C "$ROOT" checkout "$PINNED" --quiet 2>/dev/null; then
  echo "[session-branch] switched '$CURRENT' -> pinned branch '$PINNED' ($(git -C "$ROOT" log --oneline -1))"
else
  echo "[session-branch] WARNING: checkout of '$PINNED' failed — staying on '$CURRENT'. Run manually: git checkout $PINNED"
fi
exit 0
