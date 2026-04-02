"""
S152: Dedicated ingestion service — runs in its own process, isolated from trading.

Handles: market ingestion, price history, elite trader activity, resolution backfill,
         elite detection, health checks.

This process separation ensures ingestion can never starve the scan/trade event loop
or exhaust the DB connection pool used by bot services. Each process gets its own
pool with independent statement_timeout settings via per-service .env files.
"""
import asyncio
import os
import signal
import sys
import time
import warnings

# ── Structlog config (must run before any get_logger() calls) ─────────
# S152: Shared with main.py via config/logging_setup.py — identical tee logger + formatting
from config.logging_setup import configure_logging
configure_logging()

from config.settings import settings

warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")
warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")

from structlog import get_logger
logger = get_logger()


# ---------------------------------------------------------------------------
# Filesystem lock — prevents dual ingestion regardless of PgBouncer mode
# ---------------------------------------------------------------------------

def _acquire_ingestion_lock() -> int:
    """Acquire exclusive filesystem lock. Returns fd (keep reference to hold lock).

    Reliable regardless of PgBouncer mode — auto-releases on process death/SIGKILL.
    pg_stat_activity is unreliable for this because PgBouncer transaction mode
    releases backend connections between transactions, making idle processes invisible.
    """
    if sys.platform == "win32":
        logger.warning("Filesystem lock not available on Windows, skipping dual-ingestion guard")
        return -1
    import fcntl
    fd = os.open('/tmp/polymarket-ingestion.lock', os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.write(fd, str(os.getpid()).encode())
        return fd
    except (BlockingIOError, OSError):
        os.close(fd)
        logger.critical("Another ingestion process is running (lock file held)")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Background monitors
# ---------------------------------------------------------------------------

async def _event_loop_lag_monitor(shutdown_event: asyncio.Event):
    """Log warning when event loop lags > 500ms."""
    while not shutdown_event.is_set():
        t0 = asyncio.get_event_loop().time()
        await asyncio.sleep(1.0)
        lag = asyncio.get_event_loop().time() - t0 - 1.0
        if lag > 0.5:
            logger.warning("event_loop_lag", lag_seconds=round(lag, 3))


async def _scheduler_watchdog(scheduler, shutdown_event: asyncio.Event):
    """Kill process if scheduler crashes or goes stale (no cycle in 30 min).

    Systemd Restart=always will bring us back. This prevents silent death where
    the process stays alive but the scheduler loop is dead.
    """
    stale_threshold = 1800  # 30 minutes
    while not shutdown_event.is_set():
        await asyncio.sleep(60)
        if not scheduler.running:
            logger.critical("ingestion_watchdog: scheduler.running=False, exiting")
            shutdown_event.set()
            break
        if scheduler._task and scheduler._task.done():
            exc = scheduler._task.exception() if not scheduler._task.cancelled() else None
            logger.critical("ingestion_watchdog: scheduler task died", error=str(exc))
            shutdown_event.set()
            break
        # Stale cycle detection
        last_run = getattr(scheduler, '_last_cycle_end', None)
        if last_run and (time.time() - last_run) > stale_threshold:
            logger.critical(
                "ingestion_watchdog: no cycle completed in %ds, exiting",
                stale_threshold,
            )
            shutdown_event.set()
            break


async def _safe_task(coro, name: str):
    """Wrapper that logs exceptions from background tasks instead of silently dying."""
    try:
        await coro
    except asyncio.CancelledError:
        pass
    except Exception:
        logger.exception("Background task %s crashed", name)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main():
    shutdown_event = asyncio.Event()
    db = None
    scheduler = None

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, shutdown_event.set)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

    # Acquire filesystem lock BEFORE any async work — prevents dual ingestion
    _lock_fd = _acquire_ingestion_lock()  # noqa: F841 — keep reference to hold lock

    try:
        from base_engine.data.database import Database
        from base_engine.data.polymarket_client import PolymarketClient
        from base_engine.data.data_ingestion import DataIngestionService
        from base_engine.data.ingestion_scheduler import IngestionScheduler
        from base_engine.learning.elite_detector import EliteUserDetector

        db = Database()
        await db.init()

        # PolymarketClient accepts private_key=None for read-only API access
        client = PolymarketClient(
            private_key=(getattr(settings, "WALLET_PRIVATE_KEY", "") or "").strip() or None,
            wallet_address=(getattr(settings, "WALLET_ADDRESS", "") or "").strip() or None,
        )

        data_ingestion = DataIngestionService(client, db)
        await data_ingestion.start()

        elite_detector = EliteUserDetector(db) if db.session_factory else None

        # Optional monitoring components — non-fatal if unavailable
        alerting = None
        auto_healer = None
        perf_tracker = None
        try:
            from base_engine.monitoring.alerting import AlertingSystem
            alerting = AlertingSystem(db)
            await alerting.init()
        except Exception:
            pass
        try:
            from base_engine.monitoring.auto_healer import AutoHealer
            auto_healer = AutoHealer(db, data_ingestion, alerting)
        except Exception:
            pass
        try:
            from base_engine.monitoring.performance_tracker import PerformanceTracker
            perf_tracker = PerformanceTracker(db)
        except Exception:
            pass

        scheduler = IngestionScheduler(
            data_ingestion,
            elite_detector=elite_detector,
            interval_minutes=int(getattr(settings, "INGESTION_SCHEDULER_INTERVAL_MINUTES", 5)),
            top_markets_count=int(getattr(settings, "INGESTION_TOP_MARKETS_COUNT", 500)),
            initial_delay_seconds=int(getattr(settings, "INGESTION_SCHEDULER_INITIAL_DELAY_SECONDS", 30)),
            daily_full_ingestion_enabled=getattr(settings, "DAILY_FULL_INGESTION_ENABLED", True),
            daily_days_back=int(getattr(settings, "DAILY_INGESTION_DAYS_BACK", 365)),
            daily_markets_count=int(getattr(settings, "DAILY_INGESTION_MARKETS_COUNT", 1000)),
            daily_prices_markets=int(getattr(settings, "DAILY_INGESTION_PRICES_MARKETS", 1000)),
            alerting=alerting,
            auto_healer=auto_healer,
            performance_tracker=perf_tracker,
        )
        await scheduler.start()
        logger.info("Ingestion service started")

        # Start watchdog and lag monitor (wrapped to prevent silent death)
        asyncio.create_task(_safe_task(_scheduler_watchdog(scheduler, shutdown_event), "watchdog"))
        asyncio.create_task(_safe_task(_event_loop_lag_monitor(shutdown_event), "lag_monitor"))

        # Run until shutdown signal
        await shutdown_event.wait()

    except Exception:
        logger.exception("Ingestion service failed during startup")
    finally:
        logger.info("Ingestion service shutting down")
        if scheduler:
            await scheduler.stop()
        if db:
            await db.close()
        logger.info("Ingestion service stopped")


if __name__ == "__main__":
    asyncio.run(main())
