# Deployment Guide

## Prerequisites

- Python 3.10+
- PostgreSQL (Supabase or local) — set `DATABASE_URL`
- Redis (optional, for caching)
- Docker (optional, for containerized deployment)

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure Environment

Copy `.env.example` to `.env` and configure:

```bash
cp .env.example .env
```

Edit `.env` with your settings:
- `DATABASE_URL`: PostgreSQL connection string (Supabase or local); e.g. `postgresql://postgres:password@db.xxx.supabase.co:5432/postgres`
- `REDIS_HOST`: Redis host (default: localhost)
- `REDIS_PORT`: Redis port (default: 6379)
- `PRIVATE_KEY`: Your wallet private key (optional, for trading)
- `WALLET_ADDRESS`: Your wallet address (optional)

### 3. Initialize Database

Schema is created automatically on first run (Supabase/Postgres). To verify connectivity:

```bash
python test_database.py
```

Or: `python -c "from base_engine.data.database import Database; import asyncio; asyncio.run(Database().init())"`

**Data migration:** Cutover is "start fresh on Supabase." Local SQLite data is not migrated automatically. For optional export of existing data (markets/trades/prices to CSV), see docs or add a one-off export script if needed.

### 4. Run the System

**Option A: Streamlit UI**
```bash
python run_ui.py
```

**Option B: Python Script**
```python
from base_engine.base_engine import BaseEngine
import asyncio

async def main():
    engine = BaseEngine()
    await engine.init()
    # Your code here

asyncio.run(main())
```

## Docker Deployment

See `DOCKER.md` for containerized deployment.

## Production Deployment

See `PRODUCTION.md` for production-specific guidance.

## Monitoring

See `MONITORING.md` for monitoring setup.

## Troubleshooting

### Database Connection Issues

- Verify `DATABASE_URL` is set and reachable (Supabase or Postgres)
- Ensure connection string uses correct host, port, user, password
- For Supabase: use the connection string from Project Settings → Database
- Schema is created automatically on first run

### Redis Connection Issues

- Verify Redis is running: `redis-cli ping`
- Check host/port configuration
- System will work without Redis (caching disabled)

### API Connection Issues

- Check network connectivity
- Verify API endpoints in settings
- Check for rate limiting
- Use proxy if needed (set `POLYMARKET_PROXY`)

## Health Checks

The system includes health check endpoints (if using FastAPI):

```bash
curl http://localhost:8000/health
```

## Backup Procedures

See `BACKUP.md` for backup and recovery procedures.
