# Monitoring Setup Guide

## Overview

The system includes built-in metrics collection ready for Prometheus integration.

## Built-in Metrics

The `base_engine.monitoring.metrics` module provides:

- Operation duration tracking
- Success/failure counters
- Custom metrics

## Usage

### Track Operation Metrics

```python
from base_engine.monitoring.metrics import track_metrics

@track_metrics("ingest_markets")
async def ingest_all_markets(self):
    # Your code here
    pass
```

### Get Metrics

```python
from base_engine.monitoring.metrics import get_metrics_collector

collector = get_metrics_collector()
stats = collector.get_stats("ingest_markets")
print(f"Average duration: {stats['avg_duration']}s")
print(f"Success rate: {stats['success_rate']}")
```

## Prometheus Integration (Optional)

### Install Prometheus Client

```bash
pip install prometheus-client
```

### Export Metrics

```python
from prometheus_client import start_http_server, Counter, Histogram
from base_engine.monitoring.metrics import get_metrics_collector

# Create Prometheus metrics
operation_duration = Histogram('operation_duration_seconds', 'Operation duration')
operation_total = Counter('operation_total', 'Total operations')

# Export metrics
def export_metrics():
    collector = get_metrics_collector()
    stats = collector.get_all_stats()
    # Export to Prometheus...

# Start metrics server
start_http_server(8000)
```

## Grafana Dashboards

Create Grafana dashboards to visualize:

- Operation durations
- Success rates
- Error rates
- Throughput

## Error Tracking

### Sentry Setup

1. Install Sentry SDK:
```bash
pip install sentry-sdk
```

2. Set environment variable:
```bash
SENTRY_DSN=<your-sentry-dsn>
```

3. Initialize in code:
```python
from base_engine.monitoring.error_tracking import init_error_tracking

init_error_tracking(sentry_dsn=os.getenv("SENTRY_DSN"))
```

## Logging

Structured logging is already configured with `structlog`:

```python
from structlog import get_logger

logger = get_logger()
logger.info("Operation started", operation="ingest_markets", count=100)
```

## Alerts

Set up alerts for:

- API error rate > 5%
- Database connection failures
- Order execution failures
- System resource usage > 80%

## Health Checks

Monitor health endpoints:

```bash
curl http://localhost:8000/health
```

Expected response:
```json
{
  "status": "healthy",
  "database": "connected",
  "redis": "connected",
  "timestamp": "2025-01-26T12:00:00Z"
}
```
