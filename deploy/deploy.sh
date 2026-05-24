#!/usr/bin/env bash
# Polymarket AI V2 — EB SPLINTER Deploy to AWS Lightsail
# Branch: eb/main (long-lived splinter, see EB-SPLINTER.md)
# Usage: bash deploy/deploy.sh
#
# SPLINTER SEMANTICS (differs from master deploy.sh):
#   - Release path:  /opt/pa2-esports-releases/<stamp>  (separate from MB/WB)
#   - Symlink:       /opt/polymarket-ai-v2-esports      (separate from MB/WB)
#   - Restarts:      ONLY polymarket-esports.service    (does NOT touch
#                    polymarket-mirror, polymarket-weather, polymarket-ingestion)
#   - Migrations:    SKIPPED (EB never proposes migrations; surface to MB session)
#   - Shared timers/crontab: SKIPPED (MB owns shared maintenance jobs)
#   - Health probe:  EB-only via deploy/healthcheck_probe.sh (splinter version
#                    on eb/main scopes BOT_SERVICES/SCAN_SERVICES to esports only)
#
# Requirements: ssh, scp, tar in PATH (all present in Git for Windows)
# Prerequisite: deploy/migrate-to-releases.sh must have been run once on the VPS.
#
# What this does:
#   1. Local syntax check (abort on error before touching VPS)
#   2. Build tar archive excluding venv/.env/data
#   3. Upload archive to VPS, extract to timestamped release dir
#   4. Create symlinks: .env → shared, data → shared, venv → shared
#      (Migrations SKIPPED per splinter charter — MB owns DB schema)
#   5. Atomic symlink swap: /opt/polymarket-ai-v2-esports → new release
#   6. Install polymarket-esports.service ONLY + restart polymarket-esports ONLY
#   7. EB-scoped health check + auto-rollback on failure
#   8. Prune old EB-splinter releases (keep last 5)

set -euo pipefail

KEY="${SSH_KEY:-$HOME/.ssh/LightsailDefaultKey-eu-west-1.pem}"
VPS="${VPS_HOST:-ubuntu@18.201.216.0}"
SSH_OPTS="-o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3"
# SPLINTER: EB has its own release path + symlink (isolated from master's
# /opt/pa2-releases + /opt/polymarket-ai-v2). MB/WB/ingestion stay on master.
RELEASES="/opt/pa2-esports-releases"
SHARED="/opt/pa2-shared"
CURRENT="/opt/polymarket-ai-v2-esports"
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
echo "  OK — syntax clean, tests passed, SSH key present, bug-class patterns clean"

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
    --exclude='pa2-esports-releases' \
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

# SPLINTER: Migrations SKIPPED per EB-SPLINTER.md charter.
# EB never proposes migrations; DB schema is MB-canonical. If EB needs a schema
# change, surface to MB session for a master-side migration. The splinter's
# alembic/ directory is frozen-at-clone-time reference, not for application.
echo "  Migrations skipped (splinter charter: MB owns schema)"
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

# ── 5b. SPLINTER: postgres crontab + daily_backup SKIPPED ────────────────────
# These are shared maintenance jobs owned by MB session. EB splinter does NOT
# install/update them; they remain on master's cadence. If MB drifts on backup
# config, surface to MB session — do NOT touch from here.
echo ""
echo "[5b/7] Postgres crontab + daily_backup skipped (splinter charter: MB owns shared maintenance)"

# ── 6. SPLINTER: Install polymarket-esports.service ONLY + restart it ONLY ───
# Does NOT touch polymarket-weather, polymarket-mirror, polymarket-ingestion.
# Those are master-owned and stay pointed at /opt/polymarket-ai-v2.
echo ""
echo "[6/7] Installing polymarket-esports.service + restarting (splinter-scoped)..."
ssh $SSH_OPTS -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail
# Install ONLY the EB service file (splinter version points at /opt/polymarket-ai-v2-esports)
sudo cp "$NEW_RELEASE/deploy/polymarket-esports.service" /etc/systemd/system/
# Ensure .env.esports exists (EB's per-bot env override). Do NOT touch
# .env.weather/.env.mirror/.env.ingestion — those belong to MB/WB sessions.
[ -f $SHARED/.env.esports ] || sudo cp $SHARED/.env $SHARED/.env.esports
sudo chown polymarket:polymarket $SHARED/.env.esports
sudo systemctl daemon-reload
# S145 lineage: stop-before-start to free PgBouncer slots before new code loads.
# Splinter scope: only polymarket-esports is stopped/started. MB/WB/ingestion
# are untouched — they continue running on their /opt/polymarket-ai-v2 release.
sudo systemctl enable polymarket-esports
sudo systemctl stop polymarket-esports 2>/dev/null || true
sleep 2  # Let PgBouncer reclaim slots
sudo systemctl start polymarket-esports
echo "  polymarket-esports started (splinter, clean)"
# Defensive cross-check: confirm other services did NOT restart as a side effect.
for SVC in polymarket-weather polymarket-mirror polymarket-ingestion; do
    if systemctl is-active --quiet "\$SVC"; then
        echo "  \$SVC: active (untouched, as expected)"
    else
        echo "  WARNING: \$SVC is not active — investigate (splinter deploy should NOT have stopped it)"
    fi
done
REMOTE
echo "  Restarting..."

# ── 6b. SPLINTER: shared systemd timers + logrotate SKIPPED ──────────────────
# polymarket-prune-prices, polymarket-audit, polymarket-prune-data, and
# /etc/logrotate.d/polymarket are shared maintenance owned by MB. EB splinter
# does NOT install/refresh them. Surface to MB session if drift suspected.
echo ""
echo "[6b/7] Shared timers + logrotate skipped (splinter charter: MB owns shared maintenance)"

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
    # Post-success bookkeeping that the probe does not do: prune old EB splinter
    # releases (does NOT touch /opt/pa2-releases — that's MB's release path).
    ssh $SSH_OPTS -i "$KEY" "$VPS" \
        'ls -1dt /opt/pa2-esports-releases/*/ 2>/dev/null | tail -n +6 | xargs -r sudo rm -rf'

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
