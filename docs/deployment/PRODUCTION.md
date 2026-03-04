# Production Deployment Guide

## Pre-Deployment Checklist

- [ ] All tests passing
- [ ] Environment variables configured
- [ ] Database backups configured
- [ ] Monitoring set up
- [ ] Error tracking configured (Sentry)
- [ ] Logging configured
- [ ] Health checks enabled
- [ ] Resource limits set
- [ ] Security review completed

## Production Configuration

### Environment Variables

Required for production:

```bash
# Database (Supabase/Postgres)
DATABASE_URL=postgresql://postgres:password@db.xxx.supabase.co:5432/postgres

# Redis
REDIS_HOST=redis
REDIS_PORT=6379

# Security
PRIVATE_KEY=<your-private-key>  # Store securely!
WALLET_ADDRESS=<your-address>

# Monitoring (Optional)
SENTRY_DSN=<your-sentry-dsn>
ENVIRONMENT=production

# API Configuration
POLYMARKET_GAMMA_API=https://gamma-api.polymarket.com
POLYMARKET_CLOB_API=https://clob.polymarket.com
```

### Resource Limits

Set appropriate resource limits:

```yaml
# docker-compose.yml
deploy:
  resources:
    limits:
      cpus: '2'
      memory: 4G
    reservations:
      cpus: '1'
      memory: 2G
```

### Health Checks

Health check endpoint (if using FastAPI):

```python
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "database": check_database(),
        "redis": check_redis(),
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
```

## Scaling Strategies

### Horizontal Scaling

- Run multiple instances behind load balancer
- Use shared Redis for cache
- Use shared database (PostgreSQL for production)

### Vertical Scaling

- Increase CPU/memory limits
- Optimize database queries
- Enable connection pooling

## Monitoring

See `MONITORING.md` for detailed monitoring setup.

## Backup & Recovery

See `BACKUP.md` for backup procedures.

## Security

1. **Secrets Management**: Use Docker secrets or environment variable management
2. **Network Security**: Use private networks, VPNs
3. **Access Control**: Limit who can access the system
4. **Audit Logging**: Log all critical operations
5. **Regular Updates**: Keep dependencies updated

## Rollback Procedures

1. Stop current deployment
2. Restore previous version
3. Restore database backup if needed
4. Verify system health
5. Monitor for issues

## Performance Tuning

1. **Database**: Enable WAL mode, optimize queries
2. **Redis**: Configure memory limits, eviction policies
3. **API**: Use connection pooling, rate limiting
4. **Caching**: Cache frequently accessed data

## Troubleshooting

### High Memory Usage

- Check for memory leaks
- Reduce cache sizes
- Limit concurrent operations

### Slow Performance

- Check database query performance
- Verify Redis connectivity
- Check API response times
- Review logs for errors

### Connection Issues

- Verify network connectivity
- Check firewall rules
- Verify DNS resolution
- Check SSL/TLS certificates
