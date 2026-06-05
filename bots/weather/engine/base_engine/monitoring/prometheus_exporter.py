"""
Prometheus metrics exporter for the Polymarket trading system.

Exposes per-bot and system-wide metrics via an HTTP endpoint for
Prometheus scraping.  Supports multiprocess mode (one process per bot)
via PROMETHEUS_MULTIPROC_DIR.

Usage:
    exporter = PrometheusExporter(bot_name="MirrorBot")
    await exporter.start_server()
    exporter.set_positions(count=50)
    exporter.set_exposure(usd=1500.0)
    exporter.record_trade(side="YES")
    with exporter.record_scan():
        ...  # scan logic
"""

import contextlib
import os
import threading
import time
from typing import Optional

from structlog import get_logger

logger = get_logger()

# --------------------------------------------------------------------------- #
#  Graceful degradation when prometheus_client is not installed.
# --------------------------------------------------------------------------- #
try:
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        make_wsgi_app,
        multiprocess,
    )
    from prometheus_client.exposition import make_server

    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False
    logger.warning("prometheus_client not installed — Prometheus metrics disabled")


# --------------------------------------------------------------------------- #
#  Default histogram buckets tuned for trading-system latencies.
# --------------------------------------------------------------------------- #
_SCAN_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)
_API_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class PrometheusExporter:
    """Expose Prometheus metrics for one bot (or the shared system process).

    Each bot process creates its own ``PrometheusExporter`` instance.
    When ``PROMETHEUS_MULTIPROC_DIR`` is set the prometheus_client library
    aggregates metrics across all worker processes automatically.

    Only one process should call ``start_server()`` — typically the
    orchestrator / main process.  The remaining bot processes simply
    record metrics; Prometheus reads the multiproc directory at scrape
    time.
    """

    def __init__(self, bot_name: str = "system") -> None:
        self.bot_name = bot_name
        self._server: Optional[object] = None
        self._server_thread: Optional[threading.Thread] = None

        if not PROMETHEUS_AVAILABLE:
            return

        # ---- registry ---------------------------------------------------- #
        multiproc_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
        if multiproc_dir:
            self._registry = CollectorRegistry()
            multiprocess.MultiProcessCollector(self._registry)
        else:
            self._registry = CollectorRegistry()

        # ---- per-bot gauges / counters ----------------------------------- #
        self.positions_open = Gauge(
            "polymarket_positions_open",
            "Number of open positions",
            ["bot_name"],
            registry=self._registry,
        )
        self.exposure_usd = Gauge(
            "polymarket_exposure_usd",
            "Current USD exposure",
            ["bot_name"],
            registry=self._registry,
        )
        self.daily_pnl_usd = Gauge(
            "polymarket_daily_pnl_usd",
            "Daily realised P&L in USD",
            ["bot_name"],
            registry=self._registry,
        )
        self.trades_total = Counter(
            "polymarket_trades_total",
            "Cumulative trade count",
            ["bot_name", "side"],
            registry=self._registry,
        )
        self.scan_duration_seconds = Histogram(
            "polymarket_scan_duration_seconds",
            "Time spent in a single scan loop iteration",
            ["bot_name"],
            buckets=_SCAN_BUCKETS,
            registry=self._registry,
        )
        self.api_requests_total = Counter(
            "polymarket_api_requests_total",
            "Cumulative API request count",
            ["bot_name", "endpoint", "status"],
            registry=self._registry,
        )
        self.api_latency_seconds = Histogram(
            "polymarket_api_latency_seconds",
            "API request latency",
            ["bot_name", "endpoint"],
            buckets=_API_BUCKETS,
            registry=self._registry,
        )

        # ---- system metrics ---------------------------------------------- #
        self.rss_bytes = Gauge(
            "polymarket_rss_bytes",
            "Resident set size of the bot process",
            ["bot_name"],
            registry=self._registry,
        )
        self.task_heartbeat_age_seconds = Gauge(
            "polymarket_task_heartbeat_age_seconds",
            "Seconds since last heartbeat for an async task",
            ["bot_name", "task_name"],
            registry=self._registry,
        )
        self.kill_switch_active = Gauge(
            "polymarket_kill_switch_active",
            "1 if the global kill switch is engaged",
            registry=self._registry,
        )
        self.db_pool_active = Gauge(
            "polymarket_db_pool_active",
            "Active connections in the DB pool",
            registry=self._registry,
        )
        self.db_pool_overflow = Gauge(
            "polymarket_db_pool_overflow",
            "Overflow connections in the DB pool",
            registry=self._registry,
        )

        # ---- health metrics ---------------------------------------------- #
        self.health_status = Gauge(
            "polymarket_health_status",
            "Component health: 1=healthy, 0.5=degraded, 0=unhealthy",
            ["component"],
            registry=self._registry,
        )

    # ------------------------------------------------------------------ #
    #  HTTP server
    # ------------------------------------------------------------------ #
    async def start_server(self, port: Optional[int] = None) -> None:
        """Start the WSGI metrics server in a daemon thread.

        Args:
            port: TCP port to listen on.  Falls back to
                  ``PROMETHEUS_PORT`` env var, then ``9100``.
        """
        if not PROMETHEUS_AVAILABLE:
            logger.info("prometheus_client not available — skipping metrics server")
            return

        if self._server is not None:
            logger.warning("Prometheus server already running")
            return

        if port is None:
            port = int(os.environ.get("PROMETHEUS_PORT", "9100"))

        app = make_wsgi_app(self._registry)
        try:
            self._server = make_server("127.0.0.1", port, app)
        except OSError as exc:
            logger.error("failed_to_start_prometheus_server", port=port, error=str(exc))
            return

        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            name="prometheus-exporter",
            daemon=True,
        )
        self._server_thread.start()
        logger.info("prometheus_server_started", port=port, bot=self.bot_name)

    def stop_server(self) -> None:
        """Shut down the metrics server gracefully."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
            self._server_thread = None
            logger.info("prometheus_server_stopped", bot=self.bot_name)

    # ------------------------------------------------------------------ #
    #  Convenience setters — per-bot gauges
    # ------------------------------------------------------------------ #
    def set_positions(self, count: int) -> None:
        """Set the current number of open positions."""
        if not PROMETHEUS_AVAILABLE:
            return
        self.positions_open.labels(bot_name=self.bot_name).set(count)

    def set_exposure(self, usd: float) -> None:
        """Set the current USD exposure."""
        if not PROMETHEUS_AVAILABLE:
            return
        self.exposure_usd.labels(bot_name=self.bot_name).set(usd)

    def set_daily_pnl(self, usd: float) -> None:
        """Set the daily realised P&L."""
        if not PROMETHEUS_AVAILABLE:
            return
        self.daily_pnl_usd.labels(bot_name=self.bot_name).set(usd)

    def record_trade(self, side: str) -> None:
        """Increment the trade counter.

        Args:
            side: ``"YES"`` or ``"NO"``.
        """
        if not PROMETHEUS_AVAILABLE:
            return
        self.trades_total.labels(bot_name=self.bot_name, side=side).inc()

    def record_api_request(
        self, endpoint: str, status: str, latency: float
    ) -> None:
        """Record an API call.

        Args:
            endpoint: Short label, e.g. ``"clob_price"`` or ``"gamma_market"``.
            status:   ``"ok"`` / ``"error"`` / HTTP status code as string.
            latency:  Wall-clock seconds the request took.
        """
        if not PROMETHEUS_AVAILABLE:
            return
        self.api_requests_total.labels(
            bot_name=self.bot_name, endpoint=endpoint, status=status
        ).inc()
        self.api_latency_seconds.labels(
            bot_name=self.bot_name, endpoint=endpoint
        ).observe(latency)

    # ------------------------------------------------------------------ #
    #  System metrics
    # ------------------------------------------------------------------ #
    def set_rss(self, rss_bytes: int) -> None:
        """Set resident-set-size for this bot process."""
        if not PROMETHEUS_AVAILABLE:
            return
        self.rss_bytes.labels(bot_name=self.bot_name).set(rss_bytes)

    def set_heartbeat_age(self, task_name: str, age_seconds: float) -> None:
        """Record seconds since the last heartbeat of an async task."""
        if not PROMETHEUS_AVAILABLE:
            return
        self.task_heartbeat_age_seconds.labels(
            bot_name=self.bot_name, task_name=task_name
        ).set(age_seconds)

    def set_kill_switch(self, active: bool) -> None:
        """Set the global kill-switch gauge (1 = active, 0 = inactive)."""
        if not PROMETHEUS_AVAILABLE:
            return
        self.kill_switch_active.set(1 if active else 0)

    def set_db_pool(self, active: int, overflow: int = 0) -> None:
        """Set DB connection-pool gauges."""
        if not PROMETHEUS_AVAILABLE:
            return
        self.db_pool_active.set(active)
        self.db_pool_overflow.set(overflow)

    # ------------------------------------------------------------------ #
    #  Health metrics
    # ------------------------------------------------------------------ #
    def set_health(self, component: str, status: str) -> None:
        """Update a component health gauge.

        Args:
            component: e.g. ``"database"``, ``"redis"``, ``"clob_api"``.
            status:    ``"healthy"`` | ``"degraded"`` | ``"unhealthy"``.
        """
        if not PROMETHEUS_AVAILABLE:
            return
        mapping = {"healthy": 1.0, "degraded": 0.5, "unhealthy": 0.0}
        self.health_status.labels(component=component).set(
            mapping.get(status, 0.0)
        )

    # ------------------------------------------------------------------ #
    #  Scan-duration context manager
    # ------------------------------------------------------------------ #
    @contextlib.contextmanager
    def record_scan(self):
        """Context manager that times a scan iteration.

        Usage::

            with exporter.record_scan():
                await bot.scan()
        """
        if not PROMETHEUS_AVAILABLE:
            yield
            return
        start = time.monotonic()
        try:
            yield
        finally:
            elapsed = time.monotonic() - start
            self.scan_duration_seconds.labels(bot_name=self.bot_name).observe(elapsed)
