#!/usr/bin/env bash
# Polymarket AI V2 — Rollback to previous release
# Usage: bash deploy/rollback.sh
#
# Finds the second-most-recent release in /opt/pa2-releases/, atomically
# swaps the /opt/polymarket-ai-v2 symlink back to it, and restarts the service.

set -euo pipefail

KEY="${SSH_KEY:-$HOME/.ssh/LightsailDefaultKey-eu-west-1.pem}"
VPS="${VPS_HOST:-ubuntu@18.201.216.0}"
SSH_OPTS="-o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3 -o StrictHostKeyChecking=no"
RELEASES="/opt/pa2-releases"
CURRENT="/opt/polymarket-ai-v2"

echo ""
echo "=== Polymarket AI V2 — ROLLBACK ==="

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
# Service list MUST match deploy.sh's restart set (currently weather +
# esports + ingestion). Drift here means rollback leaves the missing
# service(s) running on the pre-rollback code while the symlink points
# elsewhere — the §S180 rollback-list-drift class. If deploy.sh adds a
# service, mirror it here.
# 2026-09-01 operator ruling ("Deploy, keep mirror down"): polymarket-mirror
# (legacy live MirrorBot, deliberately stopped+disabled 2026-08-25) is
# EXCLUDED here exactly as in deploy.sh step 6 and healthcheck_probe.sh —
# the 13:19Z rollback proved this list resurrects it otherwise. Re-add in
# ALL THREE places only on explicit operator instruction.
ssh $SSH_OPTS -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail
SWAP_TMP="${CURRENT}_rollback_$TIMESTAMP"
sudo ln -s "$PREV_DIR" "\$SWAP_TMP"
sudo mv -T "\$SWAP_TMP" "$CURRENT"
echo "Symlink: $CURRENT -> $PREV_DIR"
sudo systemctl restart polymarket-weather polymarket-esports polymarket-ingestion
REMOTE

echo ""
echo "=== ROLLBACK to $PREV_NAME COMPLETE ==="
echo "Monitor: ssh -i \$KEY \$VPS 'journalctl -u polymarket-weather -u polymarket-mirror -u polymarket-esports -u polymarket-ingestion -f --no-pager'"
echo ""
