#!/bin/bash
# Polymarket AI V2 — VPS Dublin (AWS Lightsail) Setup Script
# Run as root on a fresh Ubuntu 22.04/24.04 Lightsail instance
# Region: eu-west-1 (Dublin) — closest allowed location to London CLOB (~2-5ms)
#
# FULL DEPLOYMENT (3 steps):
#   1. sudo bash setup-vps.sh           # This script: OS + DB + Redis + firewall
#   2. Upload code (scp/deploy script)   # Get the app onto the VPS
#   3. sudo -u polymarket bash deploy/first-run.sh  # Migrations + backfill + services
#
# AWS Application Tag:
#   awsApplication = arn:aws:resource-groups:eu-west-1:372314668300:group/LockesPicks/066pgcny8czf2rkj5a26obodya

set -euo pipefail

INSTANCE_NAME="${LIGHTSAIL_INSTANCE_NAME:-LockePicks}"
AWS_APP_TAG="arn:aws:resource-groups:eu-west-1:372314668300:group/LockesPicks/066pgcny8czf2rkj5a26obodya"

echo "=== Polymarket AI V2 — VPS Setup ==="
echo "Region: Dublin (eu-west-1)"
echo "Instance: $INSTANCE_NAME"
echo ""

# ── Auto-generate passwords if not provided ──
PG_PASSWORD="${PG_PASSWORD:-$(openssl rand -base64 18 | tr -d '/+=')}"
REDIS_PASS="${REDIS_PASS:-$(openssl rand -base64 18 | tr -d '/+=')}"

# ── 1. System packages ──
echo "[1/8] Installing system packages..."
apt-get update && apt-get upgrade -y

# Add deadsnakes PPA for Python 3.13 (not in Ubuntu's default repos)
apt-get install -y software-properties-common
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update

apt-get install -y \
    python3.13 python3.13-venv python3.13-dev python3-pip \
    redis-server \
    git curl wget htop \
    ufw fail2ban \
    awscli

# Add PostgreSQL official repo (for pg 16)
sh -c 'echo "deb http://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list'
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc | gpg --dearmor -o /etc/apt/trusted.gpg.d/postgresql.gpg
apt-get update
apt-get install -y postgresql-16 postgresql-client-16

# Make python3.13 the default python3
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.13 1 || true

echo "  Python: $(python3.13 --version)"
echo "  PostgreSQL: $(psql --version)"
echo "  Redis: $(redis-server --version | head -c 40)"

# ── 2. Create service user ──
echo "[2/8] Creating service user..."
useradd -m -s /bin/bash polymarket 2>/dev/null || echo "  User 'polymarket' already exists."

# ── 3. PostgreSQL setup ──
echo "[3/8] Configuring PostgreSQL..."
sudo -u postgres psql -c "CREATE USER polymarket WITH PASSWORD '$PG_PASSWORD';" 2>/dev/null || \
    sudo -u postgres psql -c "ALTER USER polymarket WITH PASSWORD '$PG_PASSWORD';"
sudo -u postgres psql -c "CREATE DATABASE polymarket OWNER polymarket;" 2>/dev/null || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE polymarket TO polymarket;" 2>/dev/null || true

# Tune PostgreSQL for 16GB/4vCPU Lightsail instance
PG_CONF_DIR=$(find /etc/postgresql -name "conf.d" -type d 2>/dev/null | head -1)
if [ -z "$PG_CONF_DIR" ]; then
    PG_CONF_DIR="/etc/postgresql/16/main/conf.d"
    mkdir -p "$PG_CONF_DIR"
fi
cat > "$PG_CONF_DIR/polymarket.conf" << 'PGCONF'
# Polymarket AI tuning (16GB/4vCPU Lightsail)
shared_buffers = 2GB
effective_cache_size = 12GB
work_mem = 4MB
maintenance_work_mem = 256MB
max_connections = 50
random_page_cost = 1.1
effective_io_concurrency = 200
max_worker_processes = 4
max_parallel_workers_per_gather = 2
PGCONF

systemctl restart postgresql

# ── 4. Redis setup ──
echo "[4/8] Configuring Redis..."
sed -i "s/^# requirepass .*/requirepass $REDIS_PASS/" /etc/redis/redis.conf
sed -i "s/^requirepass .*/requirepass $REDIS_PASS/" /etc/redis/redis.conf
sed -i 's/^maxmemory .*/maxmemory 512mb/' /etc/redis/redis.conf
grep -q "^maxmemory-policy" /etc/redis/redis.conf && \
    sed -i 's/^maxmemory-policy .*/maxmemory-policy allkeys-lru/' /etc/redis/redis.conf || \
    echo "maxmemory-policy allkeys-lru" >> /etc/redis/redis.conf
systemctl restart redis

# ── 5. Application directories ──
echo "[5/8] Setting up directories..."
mkdir -p /opt/polymarket-ai-v2/data
mkdir -p /var/log/polymarket
chown -R polymarket:polymarket /opt/polymarket-ai-v2
chown polymarket:polymarket /var/log/polymarket

# ── 6. Firewall ──
echo "[6/8] Configuring firewall..."
ufw allow 22/tcp
ufw allow 8501/tcp comment "Polymarket Dashboard"
ufw --force enable

# ── 7. Static IP ──
echo "[7/8] Allocating Lightsail static IP..."
STATIC_IP_NAME="${INSTANCE_NAME}-ip"
if aws lightsail get-static-ip --static-ip-name "$STATIC_IP_NAME" --region eu-west-1 >/dev/null 2>&1; then
    echo "  Static IP '$STATIC_IP_NAME' already exists."
else
    aws lightsail allocate-static-ip \
        --static-ip-name "$STATIC_IP_NAME" \
        --region eu-west-1 || echo "  Warning: static IP allocation failed (do it in console)"
fi
aws lightsail attach-static-ip \
    --static-ip-name "$STATIC_IP_NAME" \
    --instance-name "$INSTANCE_NAME" \
    --region eu-west-1 2>/dev/null || echo "  Warning: static IP attach failed (may already be attached)"

# ── 8. AWS tagging ──
echo "[8/8] Tagging AWS resources..."
aws lightsail tag-resource \
    --resource-name "$INSTANCE_NAME" \
    --region eu-west-1 \
    --tags "key=awsApplication,value=$AWS_APP_TAG" \
           "key=Name,value=LockePicks-Dublin" \
           "key=Environment,value=production" \
    2>/dev/null || echo "  Warning: instance tagging failed (tag manually in console)"
aws lightsail tag-resource \
    --resource-name "$STATIC_IP_NAME" \
    --region eu-west-1 \
    --tags "key=awsApplication,value=$AWS_APP_TAG" \
    2>/dev/null || echo "  Warning: static IP tagging failed (non-fatal)"

echo ""
echo "============================================"
echo "  VPS SETUP COMPLETE"
echo "============================================"
echo ""
echo "  SAVE THESE PASSWORDS:"
echo "  ─────────────────────"
echo "  PostgreSQL: $PG_PASSWORD"
echo "  Redis:      $REDIS_PASS"
echo ""
echo "  DATABASE_URL=postgresql://polymarket:${PG_PASSWORD}@localhost:5432/polymarket"
echo ""
echo "  NEXT: Upload code, configure .env, run first-run.sh"
echo ""
STATIC_IP=$(aws lightsail get-static-ip --static-ip-name "$STATIC_IP_NAME" --region eu-west-1 --query 'staticIp.ipAddress' --output text 2>/dev/null || echo "check console")
echo "  STATIC IP: $STATIC_IP"
echo ""
