#!/usr/bin/env bash
# Polymarket AI V2 — Deploy to AWS Lightsail (Git Bash / Linux / macOS)
# Usage: bash deploy/deploy.sh
#
# Requirements: ssh, scp, tar in PATH (all present in Git for Windows)
# Prerequisite: deploy/migrate-to-releases.sh must have been run once on the VPS.
#
# What this does:
#   1. Local syntax check (abort on error before touching VPS)
#   2. Build tar archive excluding venv/.env/data
#   3. Upload archive to VPS, extract to timestamped release dir
#   4. Create symlinks: .env → shared, data → shared, venv → shared
#   5. Run migrations (abort + cleanup on failure)
#   6. Atomic symlink swap: /opt/polymarket-ai-v2 → new release
#   7. Restart service + 90s health check
#   8. Auto-rollback if health check fails
#   9. Prune old releases (keep last 5)

set -euo pipefail

KEY="${SSH_KEY:-$HOME/.ssh/LightsailDefaultKey-eu-west-1.pem}"
VPS="${VPS_HOST:-ubuntu@34.251.224.21}"
RELEASES="/opt/pa2-releases"
SHARED="/opt/pa2-shared"
CURRENT="/opt/polymarket-ai-v2"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
NEW_RELEASE="$RELEASES/$TIMESTAMP"
TMPTAR="/tmp/pa2-$TIMESTAMP.tar.gz"

echo ""
echo "=== Polymarket AI V2 — Deploy $TIMESTAMP ==="
echo "Source : $LOCAL_DIR"
echo "Target : $VPS:$CURRENT → $NEW_RELEASE"
echo ""

# ── 1. Local preflight ────────────────────────────────────────────────────────
echo "[1/7] Preflight checks..."
cd "$LOCAL_DIR"
python -m compileall bots/ base_engine/ scripts/ -q 2>&1 || {
    echo "ABORT: Python syntax error found — deploy cancelled"
    exit 1
}
# Verify SSH key exists
[ -f "$KEY" ] || { echo "ABORT: SSH key not found at $KEY"; exit 1; }
echo "  OK — syntax clean, SSH key present"

# ── 2. Build archive ──────────────────────────────────────────────────────────
echo ""
echo "[2/7] Building archive..."
tar czf "$TMPTAR" \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='.env' \
    --exclude='./data' \
    --exclude='./saved_models' \
    --exclude='./venv' \
    --exclude='./.venv' \
    --exclude='pa2-releases' \
    --exclude='pa2-shared' \
    --exclude='*.egg-info' \
    --exclude='.pytest_cache' \
    --exclude='htmlcov' \
    --exclude='.mypy_cache' \
    -C "$LOCAL_DIR" .
ARCHIVE_SIZE=$(du -sh "$TMPTAR" 2>/dev/null | cut -f1)
echo "  Archive: $TMPTAR ($ARCHIVE_SIZE)"

# ── 3. Upload ─────────────────────────────────────────────────────────────────
echo ""
echo "[3/7] Uploading to VPS..."
scp -i "$KEY" -o StrictHostKeyChecking=no "$TMPTAR" "$VPS:/tmp/"
rm -f "$TMPTAR"
echo "  Upload done"

# ── 4. Extract, symlink shared dirs, run migrations ──────────────────────────
echo ""
echo "[4/7] Extracting + running migrations..."
ssh -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail

TARFILE="/tmp/pa2-$TIMESTAMP.tar.gz"

# Extract to new release dir
sudo mkdir -p "$NEW_RELEASE"
sudo tar xzf "\$TARFILE" -C "$NEW_RELEASE" 2>/dev/null
sudo chown -R polymarket:polymarket "$NEW_RELEASE"
rm -f "\$TARFILE"
echo "  Extracted to $NEW_RELEASE"

# Symlink shared resources into release (code dir stays read-only)
sudo ln -sfn $SHARED/.env          $NEW_RELEASE/.env
sudo ln -sfn $SHARED/data          $NEW_RELEASE/data
sudo ln -sfn $SHARED/saved_models  $NEW_RELEASE/saved_models
sudo ln -sfn $SHARED/venv          $NEW_RELEASE/venv
sudo chown -h polymarket:polymarket \
    $NEW_RELEASE/.env \
    $NEW_RELEASE/data \
    $NEW_RELEASE/saved_models \
    $NEW_RELEASE/venv

# Run migrations — abort + clean up on failure
sudo -u polymarket $SHARED/venv/bin/python $NEW_RELEASE/scripts/run_migrations.py || {
    echo "MIGRATION FAILED — removing release $NEW_RELEASE"
    sudo rm -rf "$NEW_RELEASE"
    exit 1
}
echo "  Migrations OK"
REMOTE

# ── 5. Atomic symlink swap ────────────────────────────────────────────────────
echo ""
echo "[5/7] Atomic symlink swap..."
ssh -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail
SWAP_TMP="${CURRENT}_swap_$TIMESTAMP"
sudo ln -s "$NEW_RELEASE" "\$SWAP_TMP"
sudo mv -T "\$SWAP_TMP" "$CURRENT"
echo "  $CURRENT -> $NEW_RELEASE"
REMOTE

# ── 6. Restart service ────────────────────────────────────────────────────────
echo ""
echo "[6/7] Restarting service..."
ssh -i "$KEY" "$VPS" "sudo systemctl restart polymarket-ai"
echo "  Restarting..."

# ── 7. Health check ───────────────────────────────────────────────────────────
echo ""
echo "[7/7] Health check (90s timeout)..."
HEALTH_OK=false
for i in $(seq 1 18); do
    sleep 5
    ELAPSED=$((i * 5))
    if ssh -i "$KEY" "$VPS" \
        "journalctl -u polymarket-ai --since '-${ELAPSED}s' --no-pager 2>/dev/null | grep -q 'scan_ms'" 2>/dev/null; then
        HEALTH_OK=true
        echo "  Health OK at ${ELAPSED}s — bots scanning"
        break
    fi
    echo "  Waiting... ${ELAPSED}s"
done

if [ "$HEALTH_OK" = false ]; then
    echo ""
    echo "ERROR: Health check failed after 90s — triggering rollback"
    bash "$(dirname "$0")/rollback.sh" || true
    exit 1
fi

# ── Prune old releases (keep last 5) ─────────────────────────────────────────
ssh -i "$KEY" "$VPS" \
    "ls -1dt $RELEASES/*/ 2>/dev/null | tail -n +6 | xargs -r sudo rm -rf" || true

echo ""
echo "=== Deploy $TIMESTAMP SUCCESSFUL ==="
echo ""
