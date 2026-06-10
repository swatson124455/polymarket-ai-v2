#!/usr/bin/env bash
# Polymarket AI V2 — WB SPLINTER Deploy to AWS Lightsail
# Branch: wb/main (long-lived splinter, see WB-SPLINTER.md)
# Usage: bash deploy/deploy.sh
#
# SPLINTER SEMANTICS (differs from master deploy.sh):
#   - Release path:  /opt/pa2-weather-releases/<stamp>  (separate from MB/EB)
#   - Symlink:       /opt/polymarket-ai-v2-weather      (separate from MB/EB)
#   - Restarts:      ONLY polymarket-weather.service    (does NOT touch
#                    polymarket-mirror, polymarket-esports, polymarket-ingestion)
#   - Migrations:    SKIPPED (WB does not propose migrations; MB owns DB schema)
#   - Shared timers/crontab: SKIPPED (MB owns shared maintenance jobs)
#   - Health probe:  WB-only via deploy/healthcheck_probe.sh (splinter version
#                    on wb/main scopes BOT_SERVICES/SCAN_SERVICES to weather only)
#   - Preflight tests: WB-scoped only (test_weather*, test_paper_fill_probability_silo).
#                    Skips full tests/unit/ to avoid MB-side failures blocking WB.

set -euo pipefail

KEY="${SSH_KEY:-$HOME/.ssh/LightsailDefaultKey-eu-west-1.pem}"
VPS="${VPS_HOST:-ubuntu@18.201.216.0}"
SSH_OPTS="-o ConnectTimeout=10 -o ServerAliveInterval=5 -o ServerAliveCountMax=3"
RELEASES="/opt/pa2-weather-releases"
SHARED="/opt/pa2-shared"
CURRENT="/opt/polymarket-ai-v2-weather"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
NEW_RELEASE="$RELEASES/$TIMESTAMP"
TMPTAR="/tmp/pa2-weather-$TIMESTAMP.tar.gz"

echo ""
echo "=== Polymarket AI V2 — WB SPLINTER Deploy $TIMESTAMP ==="
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
# WB-scoped preflight: ONLY weather-owned tests + silo paper-fill test.
# Intentionally NOT running the full tests/unit/ suite — MB-side test failures
# (e.g. test_startup_hold_wiring.py) must not block WB deploys. WB owns its
# own test surface via the silo at bots/weather/engine/.
python -m pytest \
    tests/unit/test_weather_bot.py \
    tests/unit/test_weather_cold_start.py \
    tests/unit/test_paper_fill_probability_silo.py \
    --tb=short -q 2>&1 || {
    echo "ABORT: WB-scoped unit tests failed — deploy cancelled"
    exit 1
}
[ -f "$KEY" ] || { echo "ABORT: SSH key not found at $KEY"; exit 1; }
# Bug-class pattern check (P0.0) — scoped to WB-relevant paths only.
_bcp_violations=0
_bcp_hits=$(grep -rn --include="*.py" 'place_order.*event_type=' \
    "$LOCAL_DIR/bots/weather_bot.py" \
    "$LOCAL_DIR/bots/weather/" \
    "$LOCAL_DIR/scripts/onboard_weather_cities.py" \
    2>/dev/null | grep -v '^\s*#' || true)
if [ -n "$_bcp_hits" ]; then
    echo "ABORT [M1]: place_order() called with event_type= kwarg (P0.0 bug-class check):"
    echo "$_bcp_hits"
    _bcp_violations=$((_bcp_violations + 1))
fi
_bcp_hits=$(grep -rn --include="*.py" -E 'asyncio\.create_task\(.*write_through' \
    "$LOCAL_DIR/bots/weather_bot.py" \
    "$LOCAL_DIR/bots/weather/" \
    2>/dev/null | grep -v '^\s*#' || true)
if [ -n "$_bcp_hits" ]; then
    echo "ABORT [CLAUDE.md]: asyncio.create_task() wrapping write_through (P0.0 bug-class check):"
    echo "$_bcp_hits"
    _bcp_violations=$((_bcp_violations + 1))
fi
[ "$_bcp_violations" -eq 0 ] || { echo "ABORT: deploy cancelled — fix bug-class violations first"; exit 1; }
echo "  OK — syntax clean, WB tests passed, SSH key present, bug-class patterns clean"

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
    --exclude='pa2-weather-releases' \
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

# ── 4. Extract, symlink shared dirs ───────────────────────────────────────────
echo ""
echo "[4/7] Extracting + symlinking shared resources..."
ssh $SSH_OPTS -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail

TARFILE="/tmp/pa2-weather-$TIMESTAMP.tar.gz"

sudo mkdir -p "$NEW_RELEASE"
sudo tar xzf "\$TARFILE" -C "$NEW_RELEASE"
sudo chown -R polymarket:polymarket "$NEW_RELEASE"
rm -f "\$TARFILE"
echo "  Extracted to $NEW_RELEASE"

sudo ln -sfn $SHARED/.env          $NEW_RELEASE/.env
sudo ln -sfn $SHARED/data          $NEW_RELEASE/data
sudo ln -sfn $SHARED/saved_models  $NEW_RELEASE/saved_models
sudo ln -sfn $SHARED/venv          $NEW_RELEASE/venv
sudo chown -h polymarket:polymarket \
    $NEW_RELEASE/.env \
    $NEW_RELEASE/data \
    $NEW_RELEASE/saved_models \
    $NEW_RELEASE/venv

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

echo ""
echo "[5b/7] Postgres crontab + daily_backup skipped (splinter charter: MB owns shared maintenance)"

# ── 6. Install drop-in override + restart polymarket-weather ─────────────────
echo ""
echo "[6/7] Installing systemd drop-in override + restarting (splinter-scoped)..."
ssh $SSH_OPTS -i "$KEY" "$VPS" bash <<REMOTE
set -euo pipefail
sudo mkdir -p /etc/systemd/system/polymarket-weather.service.d
sudo cp "$NEW_RELEASE/deploy/polymarket-weather.service.d/00-splinter.conf" \
    /etc/systemd/system/polymarket-weather.service.d/00-splinter.conf
[ -f $SHARED/.env.weather ] || sudo cp $SHARED/.env $SHARED/.env.weather
sudo chown polymarket:polymarket $SHARED/.env.weather
sudo systemctl daemon-reload
sudo systemctl enable polymarket-weather
sudo systemctl stop polymarket-weather 2>/dev/null || true
sleep 2
sudo systemctl start polymarket-weather
echo "  polymarket-weather started (splinter, override-driven, clean)"
for SVC in polymarket-esports polymarket-mirror polymarket-ingestion; do
    if systemctl is-active --quiet "\$SVC"; then
        echo "  \$SVC: active (untouched, as expected)"
    else
        echo "  WARNING: \$SVC is not active — investigate (splinter deploy should NOT have stopped it)"
    fi
done
EFFECTIVE_CWD=\$(systemctl show polymarket-weather -p WorkingDirectory --value)
if [ "\$EFFECTIVE_CWD" = "/opt/polymarket-ai-v2-weather" ]; then
    echo "  override verified: WorkingDirectory=\$EFFECTIVE_CWD"
else
    echo "  ERROR: drop-in override not effective; got WorkingDirectory=\$EFFECTIVE_CWD"
    exit 1
fi
REMOTE
echo "  Restarting..."

echo ""
echo "[6b/7] Shared timers + logrotate skipped (splinter charter: MB owns shared maintenance)"

# ── 7. Health check ──────────────────────────────────────────────────────────
echo ""
echo "[7/7] Health check (tiered via healthcheck_probe.sh)..."
HEALTH_RESULT=$(ssh $SSH_OPTS -i "$KEY" "$VPS" \
    "bash $NEW_RELEASE/deploy/healthcheck_probe.sh" 2>&1) && PROBE_EXIT=0 || PROBE_EXIT=$?

echo "$HEALTH_RESULT" | grep -v '^$'

if [ "$PROBE_EXIT" -eq 0 ]; then
    ssh $SSH_OPTS -i "$KEY" "$VPS" \
        'ls -1dt /opt/pa2-weather-releases/*/ 2>/dev/null | tail -n +6 | xargs -r sudo rm -rf'

    _PGB_POOL=$(echo "$HEALTH_RESULT" | awk -F= '/^PGB_POOL=/{print $2; exit}')
    _PGB_POOL=${_PGB_POOL:-0}
    if [ "$_PGB_POOL" -lt 40 ] 2>/dev/null; then
        echo "  WARNING: PgBouncer default_pool_size=$_PGB_POOL (< 40)."
    else
        echo "  PgBouncer pool_size=$_PGB_POOL — OK"
    fi
    if echo "$HEALTH_RESULT" | grep -q "BACKUP_STALE"; then
        echo "  WARNING: No pg_dump backup in last 25 hours"
    fi
    if echo "$HEALTH_RESULT" | grep -q "HEALTH_WARN"; then
        echo "  WARN: scan_ms not seen from polymarket-weather within 420s. Service still active."
    fi
elif [ "$PROBE_EXIT" -eq 255 ]; then
    # S241: ssh exit 255 = the TRANSPORT failed (connection reset/timeout — e.g.
    # fail2ban throttling after the deploy's connection burst), NOT a health
    # verdict from the probe. The release is already swapped and the service
    # restarted by step 6; rolling back on a dropped probe SSH reverts a
    # healthy deploy (observed 2026-06-09: probe died at ~180s -> false
    # rollback attempt). Leave the deploy in place and verify manually.
    echo ""
    echo "WARN: health probe SSH transport failed (exit 255) — NOT a health verdict."
    echo "      No rollback. Release $TIMESTAMP is swapped and the service restarted."
    echo "      Verify manually once SSH recovers:"
    echo "        ssh -i \"$KEY\" $VPS 'systemctl is-active polymarket-weather; journalctl -u polymarket-weather -n 20 --no-pager'"
    echo ""
    echo "=== WB SPLINTER Deploy $TIMESTAMP UNVERIFIED (probe transport failure) ==="
    exit 2
else
    echo ""
    echo "ERROR: Health check failed (probe exit $PROBE_EXIT) — triggering rollback"
    bash "$(dirname "$0")/rollback.sh" || true
    exit 1
fi

echo ""
echo "=== WB SPLINTER Deploy $TIMESTAMP SUCCESSFUL ==="
echo ""
