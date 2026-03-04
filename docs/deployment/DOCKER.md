# Docker Deployment Guide

## Quick Start

### Build and Run

```bash
docker-compose up -d
```

### View Logs

```bash
docker-compose logs -f
```

### Stop

```bash
docker-compose down
```

## Dockerfile

The `Dockerfile` creates an optimized production image:

- Multi-stage build for smaller image size
- Python 3.10 slim base
- Only production dependencies
- Non-root user for security

## Docker Compose

The `docker-compose.yml` includes:

- **app**: Main application container
- **redis**: Redis cache (optional)
- **volumes**: Persistent data storage

## Environment Variables

Set environment variables in `.env` or `docker-compose.yml`:

```yaml
environment:
  - DATABASE_URL=postgresql://postgres:password@db:5432/postgres
  - REDIS_HOST=redis
  - REDIS_PORT=6379
```

## Data Persistence

Data is stored in Docker volumes:

- `polymarket_data`: Database and logs
- `redis_data`: Redis persistence

To backup:
```bash
docker run --rm -v polymarket-ai-v2_polymarket_data:/data -v $(pwd):/backup alpine tar czf /backup/backup.tar.gz /data
```

## Production Deployment

For production:

1. Use Docker secrets for sensitive data
2. Enable health checks
3. Set resource limits
4. Use orchestration (Kubernetes, Docker Swarm)

See `PRODUCTION.md` for details.
