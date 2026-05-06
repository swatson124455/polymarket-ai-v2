#!/usr/bin/env bash
# Install project git hooks (P0.0).
# Uses core.hooksPath (git >= 2.9) so hooks are version-controlled.
# Falls back to .git/hooks/ copy for older git.
#
# Usage: bash scripts/install_hooks.sh
# Run once per clone.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOOKS_DIR="$SCRIPT_DIR/hooks"
GIT_DIR="$(git rev-parse --git-dir 2>/dev/null)" || {
    echo "ERROR: not inside a git repository"
    exit 1
}

# Verify hooks exist
[ -f "$HOOKS_DIR/pre-commit" ] || {
    echo "ERROR: $HOOKS_DIR/pre-commit not found"
    exit 1
}

# Parse git version for core.hooksPath support check (requires >= 2.9)
GIT_VERSION=$(git --version | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -1)
GIT_MAJOR=$(echo "$GIT_VERSION" | cut -d. -f1)
GIT_MINOR=$(echo "$GIT_VERSION" | cut -d. -f2)

USE_HOOKSPATH=true
if [ "$GIT_MAJOR" -lt 2 ] || { [ "$GIT_MAJOR" -eq 2 ] && [ "$GIT_MINOR" -lt 9 ]; }; then
    USE_HOOKSPATH=false
fi

if $USE_HOOKSPATH; then
    git config core.hooksPath "$HOOKS_DIR"
    # Ensure the hook is executable (matters on Linux/macOS)
    chmod +x "$HOOKS_DIR/pre-commit" 2>/dev/null || true
    echo "OK — core.hooksPath set to $HOOKS_DIR (git $GIT_VERSION)"
    echo "Verify: git config core.hooksPath"
else
    echo "WARNING: git $GIT_VERSION < 2.9 — core.hooksPath not supported, copying to .git/hooks/"
    cp "$HOOKS_DIR/pre-commit" "$GIT_DIR/hooks/pre-commit"
    chmod +x "$GIT_DIR/hooks/pre-commit"
    echo "OK — pre-commit hook installed to $GIT_DIR/hooks/ (fallback)"
    echo "NOTE: hook is NOT version-controlled in this mode; re-run after re-clone."
fi
