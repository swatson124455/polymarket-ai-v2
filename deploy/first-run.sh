#!/bin/bash
# Polymarket AI V2 — First Run (after setup-vps.sh and .env configured)
# Run as: sudo -u polymarket bash deploy/first-run.sh
# Prerequisites: setup-vps.sh completed, .env configured with DATABASE_URL + REDIS_PASSWORD

set -euo pipefail
cd /opt/polymarket-ai-v2

echo "=== Polymarket AI V2 — First Run ==="
echo ""

# 0. Verify .env exists
if [ ! -f .env ]; then
    echo "ERROR: .env file not found. Copy .env.example to .env and configure it first."
    echo "  cp .env.example .env"
    echo "  nano .env  # set DATABASE_URL, REDIS_PASSWORD"
    exit 1
fi

# 1. Install Python dependencies
echo "[1/7] Installing Python dependencies..."
pip install --user -r requirements.txt
echo "  Done."

# 2. Run database migrations (creates all 23 tables)
echo "[2/7] Running database migrations..."
python scripts/run_migrations.py
echo "  Done."

# 3. Validate system
echo "[3/7] Validating system..."
python validate.py --no-migrate --skip-startup-checks || true
echo "  Done."

# 4. Initial data backfill (markets + prices, ~10-30 min)
echo "[4/7] Running initial data backfill (markets + prices for last 365 days)..."
echo "  This will take 10-30 minutes depending on API rate limits."
python scripts/run_ingestion_standalone.py --backfill --backfill-days 365
echo "  Done."

# 5. Backfill market resolutions (populates resolved_at for training temporal guard)
echo "[5/7] Backfilling market resolutions..."
python scripts/backfill_market_resolution.py || echo "  Warning: backfill script had issues (non-fatal)"
echo "  Done."

# 6. Clear model cache to force fresh training with correct data
echo "[6/7] Clearing model cache (forces retrain with backfilled data)..."
rm -f data/model_cache.pkl
echo "  Done."

# 7. Install systemd services
echo "[7/7] Installing systemd services..."
if [ "$(id -u)" -eq 0 ]; then
    for SVC in polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion; do
        cp "deploy/${SVC}.service" /etc/systemd/system/ 2>/dev/null || true
    done
    cp deploy/polymarket-dashboard.service /etc/systemd/system/
    systemctl daemon-reload
    systemctl enable polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion polymarket-dashboard
    echo "  Services installed. Start with:"
    echo "    systemctl start polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion"
    echo "    systemctl start polymarket-dashboard"
else
    echo "  Not root — run these commands as root:"
    echo "    sudo cp deploy/polymarket-weather.service deploy/polymarket-mirror.service deploy/polymarket-esports.service deploy/polymarket-ingestion.service /etc/systemd/system/"
    echo "    sudo cp deploy/polymarket-dashboard.service /etc/systemd/system/"
    echo "    sudo systemctl daemon-reload"
    echo "    sudo systemctl enable --now polymarket-weather polymarket-mirror polymarket-esports polymarket-ingestion polymarket-dashboard"
fi

# 8. Verify AWS application tagging
echo "[8/8] Verifying AWS application tagging..."
INSTANCE_NAME="${LIGHTSAIL_INSTANCE_NAME:-LockePicks}"
if command -v aws >/dev/null 2>&1; then
    TAGS=$(aws lightsail get-instance --instance-name "$INSTANCE_NAME" --region eu-west-1 \
        --query 'instance.tags[?key==`awsApplication`].value' --output text 2>/dev/null || echo "")
    if [ -n "$TAGS" ]; then
        echo "  AWS tag verified: awsApplication = $TAGS"
    else
        echo "  Warning: awsApplication tag not found on instance '$INSTANCE_NAME'."
        echo "  Run setup-vps.sh again or tag manually in AWS console."
    fi
else
    echo "  AWS CLI not installed — skipping tag verification."
fi

echo ""
echo "=== First run complete ==="
echo ""
echo "System will:"
echo "  - Train ML models on first bot cycle (~5 min)"
echo "  - Begin paper trading automatically (SIMULATION_MODE=true)"
echo "  - Retrain models every 6 hours"
echo "  - Ingest new market data every 5 minutes"
echo ""
echo "Monitor:"
echo "  journalctl -u polymarket-ai -f       # Trading engine logs"
echo "  journalctl -u polymarket-dashboard -f # Dashboard logs"
echo "  http://YOUR_IP:8501                   # Dashboard UI"
echo ""
echo "Optional: Install cron jobs for data refresh:"
echo "  sudo crontab -l -u polymarket | cat deploy/crontab >> /tmp/cron.tmp"
echo "  sudo crontab -u polymarket /tmp/cron.tmp"
