# Backup & Recovery Procedures

## Backup Strategy

### Database Backups (Postgres/Supabase)

**Supabase:** Use Supabase Dashboard → Database → Backups (Pro plan includes point-in-time recovery).

**Automated pg_dump (self-hosted Postgres):**

```bash
#!/bin/bash
# backup_database.sh
BACKUP_DIR="/backups/polymarket"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p $BACKUP_DIR
pg_dump "$DATABASE_URL" -Fc -f "$BACKUP_DIR/polymarket_$DATE.dump"
find $BACKUP_DIR -name "polymarket_*.dump" -mtime +30 -delete
```

**Cron Job:**
```bash
0 2 * * * /path/to/backup_database.sh
```

### Configuration Backups

Backup `.env` file and configuration:

```bash
cp .env /backups/config/.env.$(date +%Y%m%d)
```

### Redis Backups

If using Redis persistence:

```bash
redis-cli --rdb /backups/redis/dump_$(date +%Y%m%d).rdb
```

## Recovery Procedures

### Database Recovery

1. **Stop the application:**
```bash
docker-compose down
```

2. **Restore database (pg_restore):**
```bash
pg_restore -d "$DATABASE_URL" -c /backups/polymarket/polymarket_YYYYMMDD_HHMMSS.dump
```

3. **Verify database:**
```bash
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM markets;"
```

4. **Restart application:**
```bash
docker-compose up -d
```

### Configuration Recovery

1. **Restore .env:**
```bash
cp /backups/config/.env.YYYYMMDD .env
```

2. **Restart application**

### Full System Recovery

1. **Restore database**
2. **Restore configuration**
3. **Restore Redis data (if applicable)**
4. **Verify all services**
5. **Run health checks**
6. **Monitor for issues**

## Disaster Recovery

### Recovery Time Objective (RTO): < 1 hour

- Database restore: 15 minutes
- Configuration restore: 5 minutes
- Service restart: 10 minutes
- Verification: 30 minutes

### Recovery Point Objective (RPO): < 24 hours

- Daily backups ensure maximum 24-hour data loss
- For critical systems, consider hourly backups

## Backup Verification

### Automated Verification

Use `psql` or asyncpg to connect and run `SELECT COUNT(*) FROM markets;` against the restored database.

### Manual Verification

```bash
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM markets;"
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM market_prices;"
psql "$DATABASE_URL" -c "SELECT COUNT(*) FROM trades;"
```

## Backup Storage

### Local Storage

- Store backups on separate disk/volume
- Use compression to save space
- Implement retention policy

### Remote Storage

- Upload to cloud storage (S3, GCS, Azure)
- Use encrypted backups
- Test restore from remote storage

## Testing Recovery

### Monthly Recovery Test

1. Create test environment
2. Restore from backup
3. Verify data integrity
4. Test system functionality
5. Document any issues

## Backup Monitoring

Monitor backup success:

- Check backup file creation
- Verify backup file size
- Test backup integrity
- Alert on backup failures
