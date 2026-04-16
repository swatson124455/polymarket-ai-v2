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
VPS="${VPS_HOST:-ubuntu@18.201.216.0}"
SSH_OPTS="-o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3"
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
python -m compileall bots/ base_engine/ scripts/ esports/ -q 2>&1 || {
    echo "ABORT: Python syntax error found — deploy cancelled"
    exit 1
}
# Run unit tests — block deploy if any fail
python -m pytest tests/unit/ --tb=short -q 2>&1 || {
    echo "ABORT: Unit tests failed — deploy cancelled"
    exit 1
}
# Verify SSH key exists
[ -f "$KEY" ] || { echo "ABORT: SSH key not found at $KEY"; exit 1; }
echo "  OK — syntax clean, tests passed, SSH key present"

# ── 2. Build archive ──────────────────────────────────────────────────────────
echo ""
echo "[2/7] Building archive..."
set +e
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
_tar_exit=$?
set -e
if [[ $_tar_exit -ne 0 && $_tar_exit -ne 1 ]]; then
    echo "ABORT: tar failed with exit code $_tar_exit"
    exit 1
fi
ARCHIVE_SIZE=$(du -sh "$TMPTAR" 2>/dev/null | cut -f1)
echo "  Archive: $TMPTAR ($ARCHIVE_SIZE)"

# ── 3. Upload ─────────────────────────────────────────────────────────────────
echo ""
echo "[3/7] Uploading to VPS..."
scp $SSH_OPTS -i "$KEY" "$TMPTAR" "$VPS:/tmp/"
rm -f "$TMPTAR"
echo "  Upload done"

# ── 4. Extract, symlink shared dirs, run migrations ──────────────────────────
echo ""
echo "[4/7] Extracting + running migrations..."
ssh $SSH_OPTS -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail

TARFILE="/tmp/pa2-$TIMESTAMP.tar.gz"

# Extract to new release dir
sudo mkdir -p "$NEW_RELEASE"
sudo tar xzf "\$TARFILE" -C "$NEW_RELEASE"
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
# cd first so pydantic-settings resolves .env relative to the release dir
# S144: Bypass PgBouncer for migrations — connect directly to postgres via unix socket.
# Bots consume most of PgBouncer's 25 connections; migration only needs 1 for a few seconds.
cd $NEW_RELEASE
# S157: Extract DB password using Python urlparse (sed breaks on passwords containing @)
_DB_PW=\$(python3 -c "from urllib.parse import urlparse; import os; print(urlparse(open('$SHARED/.env').read().split('DATABASE_URL=')[1].split('\\\\n')[0].strip()).password)" 2>/dev/null || grep '^DATABASE_URL=' $SHARED/.env | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
if [ -z "\$_DB_PW" ]; then
    echo "ABORT: Could not extract DB password from $SHARED/.env"
    sudo rm -rf "$NEW_RELEASE"
    exit 1
fi
MIGRATION_DB_URL="postgresql://polymarket:\${_DB_PW}@/polymarket?host=/var/run/postgresql"
sudo -u polymarket DATABASE_URL="\$MIGRATION_DB_URL" $SHARED/venv/bin/python scripts/run_migrations.py || {
    echo "MIGRATION FAILED — removing release $NEW_RELEASE"
    sudo rm -rf "$NEW_RELEASE"
    exit 1
}
echo "  Migrations OK"
REMOTE

# ── 5. Atomic symlink swap ────────────────────────────────────────────────────
echo ""
echo "[5/7] Atomic symlink swap..."
ssh $SSH_OPTS -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail
SWAP_TMP="${CURRENT}_swap_$TIMESTAMP"
sudo ln -s "$NEW_RELEASE" "\$SWAP_TMP"
sudo mv -T "\$SWAP_TMP" "$CURRENT"
echo "  $CURRENT -> $NEW_RELEASE"
REMOTE

# ── 5b. Install postgres crontab (backup job) ────────────────────────────────
echo ""
echo "[5b/7] Installing postgres crontab..."
ssh $SSH_OPTS -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail
if [ -f "$NEW_RELEASE/deploy/crontabs/postgres.crontab" ]; then
    sudo -u postgres crontab "$NEW_RELEASE/deploy/crontabs/postgres.crontab"
    echo "  postgres crontab installed from deploy/crontabs/postgres.crontab"
else
    echo "  WARNING: deploy/crontabs/postgres.crontab not found, skipping"
fi
REMOTE

# ── 6. Install service files + restart per-bot services ──────────────────────
echo ""
echo "[6/7] Installing service files + restarting services..."
ssh $SSH_OPTS -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail
# Install per-bot service files
for SVC in polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion; do
    sudo cp "$NEW_RELEASE/deploy/\${SVC}.service" /etc/systemd/system/
done
# Ensure per-bot override env files exist (second EnvironmentFile wins over shared .env)
[ -f $SHARED/.env.weather   ] || sudo cp $SHARED/.env $SHARED/.env.weather
[ -f $SHARED/.env.mirror    ] || sudo cp $SHARED/.env $SHARED/.env.mirror
[ -f $SHARED/.env.esports   ] || sudo cp $SHARED/.env $SHARED/.env.esports
[ -f $SHARED/.env.ingestion ] || sudo cp $SHARED/.env $SHARED/.env.ingestion
sudo chown polymarket:polymarket $SHARED/.env.weather $SHARED/.env.mirror $SHARED/.env.esports $SHARED/.env.ingestion
sudo systemctl daemon-reload
# Stop and disable the old monolithic service (if running)
sudo systemctl stop polymarket-ai 2>/dev/null || true
sudo systemctl disable polymarket-ai 2>/dev/null || true
# S145: Explicit stop before start — frees all PgBouncer slots before new code loads.
# Without this, old processes hold connections during the restart window, causing
# pool exhaustion if the new processes also try to connect simultaneously.
sudo systemctl enable polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion
sudo systemctl stop polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion 2>/dev/null || true
sleep 2  # Let PgBouncer reclaim slots
sudo systemctl start polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion
echo "  polymarket-weather, polymarket-mirror, polymarket-esports, polymarket-ingestion started (clean)"
REMOTE
echo "  Restarting..."

# ── 7. Health check ───────────────────────────────────────────────────────────
# S173: Single SSH connection for health check. Previous version opened 2 SSH
# connections per bot per 5s tick (up to 360 total), triggering fail2ban bans.
# Now runs the entire polling loop server-side over one SSH session.
echo ""
echo "[7/7] Health check (420s timeout, single SSH connection)..."
HEALTH_RESULT=$(ssh $SSH_OPTS -i "$KEY" "$VPS" bash <<'REMOTE'
set -euo pipefail
# S177: Increased from 300s to 420s to accommodate EsportsBotV2 pipeline.fit()
# which takes ~330s on cold start (5.5 min XGBoost + Venn-ABERS training).
# Revert to 300s after pipeline serialization (S176 P0) eliminates startup cost.
MAX_WAIT=420
INTERVAL=10
ELAPSED=0

while [ "$ELAPSED" -lt "$MAX_WAIT" ]; do
    sleep "$INTERVAL"
    ELAPSED=$((ELAPSED + INTERVAL))

    ALL_OK=true
    CHECKED=0
    for SVC in polymarket-weather polymarket-mirror polymarket-esports; do
        # Skip services that are deliberately disabled
        if ! systemctl is-enabled "$SVC" &>/dev/null; then
            continue
        fi
        CHECKED=$((CHECKED + 1))
        if ! journalctl -u "$SVC" --since "-${ELAPSED}s" --no-pager 2>/dev/null | grep -q 'scan_ms'; then
            ALL_OK=false
        fi
    done

    if [ "$CHECKED" -gt 0 ] && [ "$ALL_OK" = true ]; then
        echo "HEALTH_OK at ${ELAPSED}s — all $CHECKED enabled bots scanning"
        # Also grab PgBouncer pool size while we're here
        PGB=$(sudo grep -oP 'default_pool_size\s*=\s*\K[0-9]+' /etc/pgbouncer/pgbouncer.ini 2>/dev/null || echo "0")
        echo "PGB_POOL=$PGB"
        # Backup staleness check: alert if no pg_dump newer than 25 hours
        if ! find /opt/pa2-backups -name '*.dump' -mmin -1500 2>/dev/null | grep -q .; then
            echo "BACKUP_STALE"
        fi
        # Prune old releases (keep last 5)
        ls -1dt /opt/pa2-releases/*/ 2>/dev/null | tail -n +6 | xargs -r sudo rm -rf
        exit 0
    fi
    echo "  Waiting... ${ELAPSED}s" >&2
done

echo "HEALTH_FAIL after ${MAX_WAIT}s — check EsportsBotV2 pipeline startup (5.5 min cold start)"
exit 1
REMOTE
) 2>&1  # capture both stdout and stderr from the SSH session

echo "$HEALTH_RESULT" | grep -v '^$'

if echo "$HEALTH_RESULT" | grep -q "HEALTH_OK"; then
    # Extract and report PgBouncer pool size
    _PGB_POOL=$(echo "$HEALTH_RESULT" | grep -oP 'PGB_POOL=\K[0-9]+' || echo "0")
    if [ "$_PGB_POOL" -lt 40 ] 2>/dev/null; then
        echo "  WARNING: PgBouncer default_pool_size=$_PGB_POOL (< 40). Risk of pool exhaustion with 3 bots."
    else
        echo "  PgBouncer pool_size=$_PGB_POOL — OK"
    fi
    if echo "$HEALTH_RESULT" | grep -q "BACKUP_STALE"; then
        echo "  WARNING: No pg_dump backup in last 25 hours — check postgres crontab"
    fi
else
    echo ""
    echo "ERROR: Health check failed after 420s — triggering rollback"
    bash "$(dirname "$0")/rollback.sh" || true
    exit 1
fi

echo ""
echo "=== Deploy $TIMESTAMP SUCCESSFUL ==="
echo ""
