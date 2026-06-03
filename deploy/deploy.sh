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
# Bug-class pattern check (P0.0) — enforced on full codebase regardless of hook bypass
_bcp_violations=0
_bcp_hits=$(grep -rn --include="*.py" 'place_order.*event_type=' \
    "$LOCAL_DIR/bots/" "$LOCAL_DIR/base_engine/" "$LOCAL_DIR/esports/" "$LOCAL_DIR/config/" \
    2>/dev/null | grep -v '^\s*#' || true)
if [ -n "$_bcp_hits" ]; then
    echo "ABORT [M1]: place_order() called with event_type= kwarg (P0.0 bug-class check):"
    echo "$_bcp_hits"
    _bcp_violations=$((_bcp_violations + 1))
fi
_bcp_hits=$(grep -rn --include="*.py" -E 'asyncio\.create_task\(.*write_through' \
    "$LOCAL_DIR/bots/" "$LOCAL_DIR/base_engine/" "$LOCAL_DIR/esports/" \
    2>/dev/null | grep -v '^\s*#' || true)
if [ -n "$_bcp_hits" ]; then
    echo "ABORT [CLAUDE.md]: asyncio.create_task() wrapping write_through (P0.0 bug-class check):"
    echo "$_bcp_hits"
    _bcp_violations=$((_bcp_violations + 1))
fi
[ "$_bcp_violations" -eq 0 ] || { echo "ABORT: deploy cancelled — fix bug-class violations first"; exit 1; }
# WI-8: Verify positions table CHECK constraints are active on the VPS DB.
# If a constraint is missing (e.g., after a host rebuild), warn but do NOT
# block the deploy — the constraints protect against future bad inserts; an
# absent constraint during deploy is not itself dangerous, but it must be
# flagged for immediate remediation.
_wi8_missing=0
_wi8_result=$(ssh $SSH_OPTS -i "$KEY" "$VPS" \
    "sudo -u postgres psql -d polymarket -tAc \
    \"SELECT COUNT(*) FROM pg_constraint WHERE conrelid='positions'::regclass AND conname LIKE 'chk_positions_%'\"" 2>/dev/null || echo "0")
if [ "${_wi8_result:-0}" -lt 4 ] 2>/dev/null; then
    echo "  WARNING [WI-8]: positions table is missing CHECK constraints (found ${_wi8_result:-0}/4)."
    echo "           Run schema/migrations/078_positions_check_constraints.sql as postgres user."
    _wi8_missing=1
fi
echo "  OK — syntax clean, tests passed, SSH key present, bug-class patterns clean${_wi8_missing:+ (WI-8 constraint warning — see above)}"

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
# Migrations run as the postgres superuser (the table owner) so DDL on
# postgres-owned tables works. TimescaleDB manages many of these tables, so
# reassigning ownership to 'polymarket' is risky on the live cluster — instead we
# elevate ONLY this deploy-time migration step; the runtime bots stay
# least-privilege as 'polymarket'. Peer auth via the unix socket — no password.
# (Before: ran as polymarket, which cannot ALTER postgres-owned tables, so
# migration 078 blocked every deploy — 2026-06-02.)
MIGRATION_DB_URL="postgresql://postgres@/polymarket?host=/var/run/postgresql"
sudo -u postgres DATABASE_URL="\$MIGRATION_DB_URL" $SHARED/venv/bin/python scripts/run_migrations.py || {
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
# Ensure backup directory and script exist
sudo mkdir -p /opt/pa2-backups
if [ -f "$NEW_RELEASE/deploy/daily_backup.sh" ]; then
    sudo cp "$NEW_RELEASE/deploy/daily_backup.sh" /opt/pa2-backups/daily_backup.sh
    sudo chmod +x /opt/pa2-backups/daily_backup.sh
    sudo chown postgres:postgres /opt/pa2-backups/daily_backup.sh
    echo "  daily_backup.sh installed to /opt/pa2-backups/"
fi
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

# ── 6b. Install + enable systemd timers (prune, audit) ───────────────────────
echo ""
echo "[6b/7] Installing systemd timers..."
ssh $SSH_OPTS -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail
for TIMER_SVC in polymarket-prune-prices polymarket-audit polymarket-prune-data; do
    if [ -f "$NEW_RELEASE/deploy/\${TIMER_SVC}.service" ] && [ -f "$NEW_RELEASE/deploy/\${TIMER_SVC}.timer" ]; then
        sudo cp "$NEW_RELEASE/deploy/\${TIMER_SVC}.service" "$NEW_RELEASE/deploy/\${TIMER_SVC}.timer" /etc/systemd/system/
        sudo systemctl daemon-reload
        sudo systemctl enable --now "\${TIMER_SVC}.timer"
        echo "  \${TIMER_SVC}.timer enabled"
    fi
done
# S177: Install logrotate config
if [ -f "$NEW_RELEASE/deploy/logrotate.d/polymarket" ]; then
    sudo cp "$NEW_RELEASE/deploy/logrotate.d/polymarket" /etc/logrotate.d/polymarket
    echo "  logrotate config installed"
fi
REMOTE

# ── 7. Health check (tiered 3-gate, via healthcheck_probe.sh) ────────────────
# S180: Replaced single-gate 420s scan_ms loop with tiered check in
# deploy/healthcheck_probe.sh:
#   Gate 1 (T+30s): systemctl is-active --quiet for all bot services (fail-fast)
#   Gate 2 (T+60s): no ERROR-priority entries in journalctl (fail-fast)
#   Gate 3 (T+420s): soft-wait for scan_ms (timeout → warn, not fail, as long as
#                    services still active — covers EB v2 cold-start fit case
#                    that caused the S180 false-red)
# Probe exit 0 = HEALTH_OK or HEALTH_WARN; exit 1 = HEALTH_FAIL (triggers rollback).
echo ""
echo "[7/7] Health check (tiered via healthcheck_probe.sh)..."
HEALTH_RESULT=$(ssh $SSH_OPTS -i "$KEY" "$VPS" \
    "bash $NEW_RELEASE/deploy/healthcheck_probe.sh" 2>&1) && PROBE_EXIT=0 || PROBE_EXIT=$?

echo "$HEALTH_RESULT" | grep -v '^$'

if [ "$PROBE_EXIT" -eq 0 ]; then
    # Post-success bookkeeping that the probe does not do: prune old releases.
    ssh $SSH_OPTS -i "$KEY" "$VPS" \
        'ls -1dt /opt/pa2-releases/*/ 2>/dev/null | tail -n +6 | xargs -r sudo rm -rf'

    # Report PgBouncer pool size warning if below threshold.
    # grep -oP \K is PCRE-only and fails on non-UTF-8 locales with
    # "grep: -P supports only unibyte and UTF-8 locales", which made the
    # extraction silently return empty → "0" → false low-pool warning
    # fired on every deploy regardless of actual pool size. POSIX-portable
    # awk replacement extracts the value reliably under any locale.
    _PGB_POOL=$(echo "$HEALTH_RESULT" | awk -F= '/^PGB_POOL=/{print $2; exit}')
    _PGB_POOL=${_PGB_POOL:-0}
    if [ "$_PGB_POOL" -lt 40 ] 2>/dev/null; then
        echo "  WARNING: PgBouncer default_pool_size=$_PGB_POOL (< 40). Risk of pool exhaustion with 3 bots."
    else
        echo "  PgBouncer pool_size=$_PGB_POOL — OK"
    fi
    if echo "$HEALTH_RESULT" | grep -q "BACKUP_STALE"; then
        echo "  WARNING: No pg_dump backup in last 25 hours — check postgres crontab"
    fi
    if echo "$HEALTH_RESULT" | grep -q "HEALTH_WARN"; then
        echo "  WARN: scan_ms not seen from all bots within 420s. Services still active."
        echo "        Likely EB v2 cold-start — monitor pipeline_ready log signal."
    fi
else
    echo ""
    echo "ERROR: Health check failed (probe exit $PROBE_EXIT) — triggering rollback"
    bash "$(dirname "$0")/rollback.sh" || true
    exit 1
fi

echo ""
echo "=== Deploy $TIMESTAMP SUCCESSFUL ==="
echo ""
