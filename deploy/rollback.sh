#!/usr/bin/env bash
# Polymarket AI V2 — EB SPLINTER Rollback to previous release
# Branch: eb/main (long-lived splinter, see EB-SPLINTER.md)
# Usage: bash deploy/rollback.sh
#
# SPLINTER SEMANTICS:
#   - Finds 2nd-most-recent release in /opt/pa2-esports-releases/
#   - Atomically swaps /opt/polymarket-ai-v2-esports symlink back
#   - Restarts ONLY polymarket-esports.service (does NOT touch MB/WB/ingestion)

set -euo pipefail

KEY="${SSH_KEY:-$HOME/.ssh/LightsailDefaultKey-eu-west-1.pem}"
VPS="${VPS_HOST:-ubuntu@18.201.216.0}"
SSH_OPTS="-o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=no"
RELEASES="/opt/pa2-esports-releases"
CURRENT="/opt/polymarket-ai-v2-esports"

echo ""
echo "=== Polymarket AI V2 — EB SPLINTER ROLLBACK ==="

# Find previous release (second-most-recent dir, sorted by modification time)
PREV_DIR=$(ssh $SSH_OPTS -i "$KEY" "$VPS" \
    "ls -1dt $RELEASES/*/ 2>/dev/null | sed -n '2p' | sed 's|/$||'" || true)

if [ -z "$PREV_DIR" ]; then
    echo "ERROR: No previous release found in $RELEASES/"
    echo "       At least 2 releases are needed to roll back."
    exit 1
fi

PREV_NAME=$(basename "$PREV_DIR")
echo "Current  : $(ssh $SSH_OPTS -i "$KEY" "$VPS" "readlink $CURRENT 2>/dev/null || echo '(unknown)'")"
echo "Rollback : $PREV_DIR"
echo ""

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
# SPLINTER rollback service list MUST match splinter deploy.sh's restart set
# (currently polymarket-esports only). §S180 rollback-list-drift lesson:
# if splinter deploy.sh ever adds another EB service, mirror it here.
# DO NOT add polymarket-weather/mirror/ingestion — those are MB/WB owned.
ssh $SSH_OPTS -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail
SWAP_TMP="${CURRENT}_rollback_$TIMESTAMP"
sudo ln -s "$PREV_DIR" "\$SWAP_TMP"
sudo mv -T "\$SWAP_TMP" "$CURRENT"
echo "Symlink: $CURRENT -> $PREV_DIR"
sudo systemctl restart polymarket-esports
REMOTE

echo ""
echo "=== EB SPLINTER ROLLBACK to $PREV_NAME COMPLETE ==="
echo "Monitor: ssh -i \$KEY \$VPS 'journalctl -u polymarket-esports -f --no-pager'"
echo ""
