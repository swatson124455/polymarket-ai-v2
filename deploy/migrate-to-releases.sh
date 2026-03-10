#!/usr/bin/env bash
# Polymarket AI V2 — ONE-TIME migration to release-based deployment structure
#
# Run this ONCE to transform /opt/polymarket-ai-v2 from a flat directory into
# a symlink pointing at a timestamped release directory.
#
# Before: /opt/polymarket-ai-v2/  (flat directory with .env, venv, data, code)
# After:  /opt/polymarket-ai-v2   (symlink → /opt/pa2-releases/initial/)
#         /opt/pa2-shared/        (.env, venv/, data/ — persists across deploys)
#         /opt/pa2-releases/      (timestamped code snapshots)
#
# After this script succeeds, use deploy/deploy.sh for all future deployments.
# The old directory is preserved at /opt/polymarket-ai-v2_pre_migration/ as a
# safety backup — delete it once you've verified the new structure works.
#
# Usage: bash deploy/migrate-to-releases.sh

set -euo pipefail

KEY="${SSH_KEY:-$HOME/.ssh/LightsailDefaultKey-eu-west-1.pem}"
VPS="${VPS_HOST:-ubuntu@34.251.224.21}"
CURRENT="/opt/polymarket-ai-v2"
RELEASES="/opt/pa2-releases"
SHARED="/opt/pa2-shared"

echo ""
echo "=== Polymarket AI V2 — One-Time Release Structure Migration ==="
echo ""
echo "This script transforms $CURRENT from a directory into a symlink."
echo ""
echo "What will happen on the VPS:"
echo "  1. Create $RELEASES/ and $SHARED/"
echo "  2. Move .env, venv/, data/ from $CURRENT to $SHARED/"
echo "  3. Create $RELEASES/initial/ with the current code (minus shared items)"
echo "  4. Stop service"
echo "  5. Backup $CURRENT to ${CURRENT}_pre_migration/"
echo "  6. Create symlink: $CURRENT → $RELEASES/initial/"
echo "  7. Update systemd service ReadWritePaths + EnvironmentFile"
echo "  8. Restart service"
echo ""
echo "Rollback: Delete the symlink, rename backup back, systemctl restart"
echo ""
read -rp "Proceed? (yes/no) " CONFIRM
if [ "$CONFIRM" != "yes" ]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "Running migration on VPS..."

ssh -i "$KEY" "$VPS" bash <<'REMOTE'
set -euo pipefail

CURRENT="/opt/polymarket-ai-v2"
RELEASES="/opt/pa2-releases"
SHARED="/opt/pa2-shared"
INITIAL="$RELEASES/initial"
BACKUP="${CURRENT}_pre_migration"
SERVICE_FILE="/etc/systemd/system/polymarket-ai.service"

# Safety check: bail if already migrated
if [ -L "$CURRENT" ]; then
    echo "ERROR: $CURRENT is already a symlink — migration already done."
    exit 1
fi
if [ -d "$RELEASES" ]; then
    echo "ERROR: $RELEASES already exists — migration may be partially done."
    echo "       Inspect manually before re-running."
    exit 1
fi

echo "[1/8] Creating directories..."
sudo mkdir -p "$RELEASES" "$SHARED"
sudo chown polymarket:polymarket "$RELEASES" "$SHARED"

echo "[2/8] Moving shared resources to $SHARED..."
[ -f "$CURRENT/.env"         ] && sudo mv "$CURRENT/.env"         "$SHARED/.env"         || echo "  .env not found, skipping"
[ -d "$CURRENT/venv"         ] && sudo mv "$CURRENT/venv"         "$SHARED/venv"         || echo "  venv not found, skipping"
[ -d "$CURRENT/data"         ] && sudo mv "$CURRENT/data"         "$SHARED/data"         || echo "  data not found, skipping"
[ -d "$CURRENT/saved_models" ] && sudo mv "$CURRENT/saved_models" "$SHARED/saved_models" || echo "  saved_models not found, skipping"
sudo chown -R polymarket:polymarket "$SHARED"

echo "[3/8] Copying code to $INITIAL..."
sudo cp -rp "$CURRENT" "$INITIAL"
sudo chown -R polymarket:polymarket "$INITIAL"

echo "[4/8] Removing shared items from initial release copy..."
sudo rm -f  "$INITIAL/.env"
sudo rm -rf "$INITIAL/venv"
sudo rm -rf "$INITIAL/data"
sudo rm -rf "$INITIAL/saved_models"

echo "[5/8] Creating symlinks in initial release..."
sudo ln -sfn "$SHARED/.env"         "$INITIAL/.env"
sudo ln -sfn "$SHARED/venv"         "$INITIAL/venv"
sudo ln -sfn "$SHARED/data"         "$INITIAL/data"
sudo ln -sfn "$SHARED/saved_models" "$INITIAL/saved_models"
sudo chown -h polymarket:polymarket "$INITIAL/.env" "$INITIAL/venv" "$INITIAL/data" "$INITIAL/saved_models"

echo "[6/8] Stopping service..."
sudo systemctl stop polymarket-ai

echo "[7/8] Swapping $CURRENT to symlink..."
sudo mv "$CURRENT" "$BACKUP"
sudo ln -s "$INITIAL" "$CURRENT"
echo "  $CURRENT -> $INITIAL"
echo "  Backup at: $BACKUP"

echo "[8/8] Updating systemd service file..."
if [ -f "$SERVICE_FILE" ]; then
    # Update ReadWritePaths to use absolute shared path (includes saved_models)
    sudo sed -i "s|ReadWritePaths=.*|ReadWritePaths=$SHARED/data $SHARED/saved_models /var/log/polymarket|" "$SERVICE_FILE"
    # Update EnvironmentFile to use absolute shared path
    sudo sed -i "s|EnvironmentFile=.*|EnvironmentFile=$SHARED/.env|" "$SERVICE_FILE"
    sudo systemctl daemon-reload
    echo "  Service file updated"
else
    echo "  WARNING: $SERVICE_FILE not found — update ReadWritePaths manually"
fi

echo ""
echo "Restarting service..."
sudo systemctl start polymarket-ai
REMOTE

echo ""
echo "Migration done. Waiting 20s for health check..."
sleep 20

echo ""
SCAN_FOUND=$(ssh -i "$KEY" "$VPS" \
    "journalctl -u polymarket-ai --since '-60s' --no-pager 2>/dev/null | grep -c 'scan_ms' || true")

if [ "$SCAN_FOUND" -gt 0 ]; then
    echo "SUCCESS: $SCAN_FOUND scan_ms log lines found — bots are scanning."
    echo ""
    echo "Migration complete. Next steps:"
    echo "  1. Use deploy/deploy.sh for all future deployments"
    echo "  2. Once confident, delete the backup:"
    echo "     ssh -i \$KEY \$VPS 'sudo rm -rf ${CURRENT}_pre_migration'"
else
    echo "WARNING: No scan_ms log lines found yet. Bots may still be starting."
    echo "Check: ssh -i \$KEY \$VPS 'journalctl -u polymarket-ai -n 50 --no-pager'"
    echo ""
    echo "If bots fail to start, rollback:"
    echo "  ssh -i \$KEY \$VPS 'sudo systemctl stop polymarket-ai && sudo rm /opt/polymarket-ai-v2 && sudo mv ${CURRENT}_pre_migration /opt/polymarket-ai-v2 && sudo systemctl start polymarket-ai'"
fi
echo ""
