#!/usr/bin/env bash
# Polymarket AI V2 — WB SPLINTER Rollback to previous release
# Branch: wb/main (long-lived splinter, see WB-SPLINTER.md)
# Usage: bash deploy/rollback.sh

set -euo pipefail

KEY="${SSH_KEY:-$HOME/.ssh/LightsailDefaultKey-eu-west-1.pem}"
VPS="${VPS_HOST:-ubuntu@18.201.216.0}"
SSH_OPTS="-o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=no"
RELEASES="/opt/pa2-weather-releases"
CURRENT="/opt/polymarket-ai-v2-weather"

echo ""
echo "=== Polymarket AI V2 — WB SPLINTER ROLLBACK ==="

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
ssh $SSH_OPTS -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail
SWAP_TMP="${CURRENT}_rollback_$TIMESTAMP"
sudo ln -s "$PREV_DIR" "\$SWAP_TMP"
sudo mv -T "\$SWAP_TMP" "$CURRENT"
echo "Symlink: $CURRENT -> $PREV_DIR"
sudo systemctl restart polymarket-weather
REMOTE

echo ""
echo "=== WB SPLINTER ROLLBACK to $PREV_NAME COMPLETE ==="
echo "Monitor: ssh -i \$KEY \$VPS 'journalctl -u polymarket-weather -f --no-pager'"
echo ""
