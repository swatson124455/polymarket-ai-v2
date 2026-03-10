#!/usr/bin/env bash
# Polymarket AI V2 — Rollback to previous release
# Usage: bash deploy/rollback.sh
#
# Finds the second-most-recent release in /opt/pa2-releases/, atomically
# swaps the /opt/polymarket-ai-v2 symlink back to it, and restarts the service.

set -euo pipefail

KEY="${SSH_KEY:-$HOME/.ssh/LightsailDefaultKey-eu-west-1.pem}"
VPS="${VPS_HOST:-ubuntu@34.251.224.21}"
RELEASES="/opt/pa2-releases"
CURRENT="/opt/polymarket-ai-v2"

echo ""
echo "=== Polymarket AI V2 — ROLLBACK ==="

# Find previous release (second-most-recent dir, sorted by modification time)
PREV_DIR=$(ssh -i "$KEY" "$VPS" \
    "ls -1dt $RELEASES/*/ 2>/dev/null | sed -n '2p' | sed 's|/$||'" || true)

if [ -z "$PREV_DIR" ]; then
    echo "ERROR: No previous release found in $RELEASES/"
    echo "       At least 2 releases are needed to roll back."
    exit 1
fi

PREV_NAME=$(basename "$PREV_DIR")
echo "Current  : $(ssh -i "$KEY" "$VPS" "readlink $CURRENT 2>/dev/null || echo '(unknown)'")"
echo "Rollback : $PREV_DIR"
echo ""

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
ssh -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail
SWAP_TMP="${CURRENT}_rollback_$TIMESTAMP"
sudo ln -s "$PREV_DIR" "\$SWAP_TMP"
sudo mv -T "\$SWAP_TMP" "$CURRENT"
echo "Symlink: $CURRENT -> $PREV_DIR"
sudo systemctl restart polymarket-ai
REMOTE

echo ""
echo "=== ROLLBACK to $PREV_NAME COMPLETE ==="
echo "Monitor: ssh -i \$KEY \$VPS 'journalctl -u polymarket-ai -f --no-pager'"
echo ""
