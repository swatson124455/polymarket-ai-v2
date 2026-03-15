# A2: Increase PostgreSQL max_connections to 300

## Why
5 active bots × 20 connections (pool_size=15 + max_overflow=5) = 100 = PostgreSQL default.
Enabling bot #6 exceeds this. At 14 bots: 280 connections needed.

## Memory Impact
Each connection uses ~5-10MB RAM. 300 × 10MB = 3GB max.
On 16GB VPS with 14 Python processes + PostgreSQL + Redis: tight but feasible.

## Steps

### 1. Check current value
```bash
sudo -u postgres psql -c "SHOW max_connections;"
# Expected: 100
```

### 2. Edit postgresql.conf
```bash
sudo nano /etc/postgresql/*/main/postgresql.conf
# Find: max_connections = 100
# Change to: max_connections = 300
```

### 3. Also increase shared_buffers if needed
```bash
# In postgresql.conf, ensure:
# shared_buffers = 2GB    (25% of 16GB RAM, good default)
# work_mem = 16MB         (per-sort, keep conservative with 300 connections)
# effective_cache_size = 8GB
```

### 4. Update kernel shared memory limits (if needed)
```bash
# Check current:
cat /proc/sys/kernel/shmmax
# Should be >= 2GB (2147483648). If not:
sudo sysctl -w kernel.shmmax=2147483648
echo "kernel.shmmax=2147483648" | sudo tee -a /etc/sysctl.conf
```

### 5. Restart PostgreSQL
```bash
sudo systemctl restart postgresql
# Verify:
sudo -u postgres psql -c "SHOW max_connections;"
# Expected: 300
```

### 6. Verify application connects
```bash
sudo systemctl restart polymarket-ai
journalctl -u polymarket-ai -f | grep -i "database\|pool\|connection"
# Should see: "Database connection successful" for each bot
```

## Rollback
```bash
# In postgresql.conf, change back to: max_connections = 100
sudo systemctl restart postgresql
sudo systemctl restart polymarket-ai
```

## Verification
```bash
# Check active connections:
sudo -u postgres psql -c "SELECT count(*) FROM pg_stat_activity;"
# Should be well under 300

# Check per-bot connections:
sudo -u postgres psql -c "SELECT application_name, count(*) FROM pg_stat_activity GROUP BY 1 ORDER BY 2 DESC;"
```
