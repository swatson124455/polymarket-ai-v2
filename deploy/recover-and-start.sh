#!/bin/bash
# Polymarket AI V2 — VPS Recovery & Start Script
# Run after gaining SSH access: sudo bash deploy/recover-and-start.sh
#
# This script:
#   1. Diagnoses the current state of the VPS
#   2. Fixes any service issues (sshd, ufw, PostgreSQL, Redis)
#   3. Deploys the bot if not already deployed
#   4. Starts all services
#
# Usage:
#   sudo bash /opt/polymarket-ai-v2/deploy/recover-and-start.sh
#   OR if code not yet deployed:
#   sudo bash /tmp/recover-and-start.sh

set -euo pipefail

echo ""
echo "=== Polymarket AI V2 — VPS Recovery ==="
echo "$(date)"
echo ""

APP_DIR="/opt/polymarket-ai-v2"

# ── 1. DIAGNOSE ──────────────────────────────────────────────
echo "[DIAG] System resources:"
free -h | head -2
df -h / | tail -1
echo ""

echo "[DIAG] Service states:"
for svc in ssh sshd ufw postgresql redis; do
    state=$(systemctl is-active "$svc" 2>/dev/null || echo "not-found")
    echo "  $svc: $state"
done
echo ""

echo "[DIAG] ufw firewall rules:"
ufw status numbered 2>/dev/null || echo "  ufw not active"
echo ""

echo "[DIAG] Python:"
python3 --version 2>/dev/null || echo "  python3 not found"
python3.13 --version 2>/dev/null || echo "  python3.13 not found"
echo ""

echo "[DIAG] App directory:"
ls -la "$APP_DIR/" 2>/dev/null || echo "  $APP_DIR not found — needs deployment"
echo ""

# ── 2. FIX SSH ───────────────────────────────────────────────
echo "[FIX] Ensuring SSH daemon is running..."
systemctl enable ssh 2>/dev/null || systemctl enable sshd 2>/dev/null || true
systemctl start ssh 2>/dev/null || systemctl start sshd 2>/dev/null || true
echo "  sshd: $(systemctl is-active ssh 2>/dev/null || systemctl is-active sshd 2>/dev/null)"

# ── 3. FIX FIREWALL ─────────────────────────────────────────
echo "[FIX] Ensuring ufw allows SSH and dashboard..."
ufw allow 22/tcp    2>/dev/null || true
ufw allow 8501/tcp  2>/dev/null || true
ufw --force enable  2>/dev/null || true
echo "  ufw: $(ufw status | head -1)"

# ── 4. ENSURE SERVICES RUNNING ──────────────────────────────
echo "[FIX] Starting PostgreSQL..."
systemctl enable postgresql 2>/dev/null || true
systemctl start postgresql  2>/dev/null || true
echo "  postgresql: $(systemctl is-active postgresql)"

echo "[FIX] Starting Redis..."
systemctl enable redis 2>/dev/null || true
systemctl start redis  2>/dev/null || true
echo "  redis: $(systemctl is-active redis)"

# ── 5. CHECK / DEPLOY APP ───────────────────────────────────
if [ ! -f "$APP_DIR/main.py" ]; then
    echo ""
    echo "  [WARN] App code not found at $APP_DIR/main.py"
    echo "  Deploy from Windows first:"
    echo "    .\\deploy\\deploy-from-windows.ps1 -VpsIp \$(curl -s ifconfig.me)"
    echo ""
    echo "  Then re-run this script."
    exit 0
fi

# ── 6. CHECK .env ────────────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
    echo ""
    echo "  [WARN] .env not found — copying VPS template..."
    if [ -f "$APP_DIR/deploy/env.vps" ]; then
        cp "$APP_DIR/deploy/env.vps" "$APP_DIR/.env"
        echo "  Copied deploy/env.vps → .env"
        echo "  [ACTION REQUIRED] Edit .env and set:"
        echo "    - DATABASE_URL (replace VPS_PG_PASSWORD)"
        echo "    - REDIS_PASSWORD (replace VPS_REDIS_PASSWORD)"
        echo "  Then re-run: sudo bash deploy/recover-and-start.sh"
        exit 0
    else
        echo "  [ERROR] deploy/env.vps not found. Deploy code first."
        exit 1
    fi
fi

# Check if .env still has placeholder passwords
if grep -q "VPS_PG_PASSWORD\|VPS_REDIS_PASSWORD" "$APP_DIR/.env"; then
    echo ""
    echo "  [ACTION REQUIRED] .env has placeholder values. Edit it:"
    echo "    nano $APP_DIR/.env"
    echo "  Set VPS_PG_PASSWORD and VPS_REDIS_PASSWORD to the passwords"
    echo "  that were printed when setup-vps.sh ran."
    echo "  If you lost them, reset with:"
    echo "    sudo -u postgres psql -c \"ALTER USER polymarket WITH PASSWORD 'newpass';\""
    echo "    sudo redis-cli CONFIG SET requirepass 'newpass'"
    exit 1
fi

# ── 7. INSTALL DEPENDENCIES ─────────────────────────────────
echo ""
echo "[APP] Checking Python dependencies..."
cd "$APP_DIR"
if ! python3 -c "import structlog" 2>/dev/null; then
    echo "  Installing requirements..."
    sudo -u polymarket pip install --user -r requirements.txt --quiet
    echo "  Done."
else
    echo "  Dependencies already installed."
fi

# ── 8. RUN MIGRATIONS ────────────────────────────────────────
echo ""
echo "[APP] Running database migrations..."
sudo -u polymarket python3 scripts/run_migrations.py
echo "  Migrations complete."

# ── 9. INSTALL + START SERVICES ─────────────────────────────
echo ""
echo "[SVC] Installing systemd services..."
cp deploy/polymarket-ai.service /etc/systemd/system/
cp deploy/polymarket-dashboard.service /etc/systemd/system/ 2>/dev/null || true
systemctl daemon-reload
systemctl enable polymarket-ai 2>/dev/null || true
systemctl enable polymarket-dashboard 2>/dev/null || true

echo "[SVC] Starting bot..."
systemctl restart polymarket-ai
sleep 3
echo "  polymarket-ai: $(systemctl is-active polymarket-ai)"

echo "[SVC] Starting dashboard..."
systemctl restart polymarket-dashboard 2>/dev/null || echo "  polymarket-dashboard: not configured (non-fatal)"

# ── 10. FINAL STATUS ─────────────────────────────────────────
echo ""
echo "============================================"
echo "  RECOVERY COMPLETE"
echo "============================================"
echo ""
echo "Service status:"
for svc in polymarket-ai polymarket-dashboard postgresql redis; do
    echo "  $svc: $(systemctl is-active $svc 2>/dev/null || echo 'not-found')"
done
echo ""
echo "Follow logs:"
echo "  journalctl -u polymarket-ai -f --no-pager"
echo ""
echo "Dashboard:"
echo "  http://$(curl -s ifconfig.me 2>/dev/null || echo 'YOUR_IP'):8501"
echo ""
echo "Run health check:"
echo "  bash $APP_DIR/deploy/vps-check.sh"
echo ""
