"""
APScheduler-based Health Monitoring Scheduler.

Replaces asyncio.sleep(N) infinite loops with deterministic IntervalTrigger jobs.
Prevents interval drift on long uptime (asyncio sleep accumulates drift from
exception handling and task scheduling overhead).

Jobs:
  health_check        : 60s  — full service health sweep (DB, Redis, API, CPU/RAM)
  streaming_anomaly   : 10s  — feed SLI metrics to ADWIN drift detectors
  log_miner           : 30s  — tail paper_trading.log for critical patterns
  degradation_check   : 30s  — log fleet degradation tier if non-zero
  drawdown_check      : 30s  — update portfolio drawdown breaker equity
  sli_report          : 120s — log SLI snapshot to observability record
  exposure_reconcile  : 300s — rebuild in-memory exposure tracking from DB ground truth

All jobs are fire-and-forget: each runs in the asyncio event loop and any
exception is caught+logged without stopping the scheduler or other jobs.
"""
import asyncio
from typing import Optional, Any, Dict, Callable
from datetime import datetime, timezone
from structlog import get_logger

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False

logger = get_logger()


class HealthScheduler:
    """
    Centralized APScheduler for all health monitoring periodic tasks.

    Usage::

        scheduler = HealthScheduler(
            health_monitor=health_monitor,
            streaming_anomaly=streaming_anomaly,
            log_miner=log_miner,
            degradation_manager=degradation_manager,
            drawdown_breaker=drawdown_breaker,
            base_engine=base_engine,
        )
        scheduler.start()    # non-blocking, runs jobs in background

        # Later, at shutdown:
        scheduler.stop()
    """

    def __init__(
        self,
        health_monitor=None,
        streaming_anomaly=None,
        log_miner=None,
        degradation_manager=None,
        drawdown_breaker=None,
        base_engine=None,
        equity_fn: Optional[Callable[[], float]] = None,
        sports_db=None,
    ):
        self.health_monitor = health_monitor
        self.streaming_anomaly = streaming_anomaly
        self.log_miner = log_miner
        self.degradation_manager = degradation_manager
        self.drawdown_breaker = drawdown_breaker
        self.base_engine = base_engine
        self._equity_fn = equity_fn  # Optional fn() -> float for drawdown equity
        self.sports_db = sports_db   # Optional DB for sports calibration job

        self._scheduler: Optional[Any] = None
        self._running = False
        self._job_run_counts: Dict[str, int] = {}
        self._job_error_counts: Dict[str, int] = {}

    # ── Job implementations ───────────────────────────────────────────────────

    async def _run_health_check(self) -> None:
        """Full health check sweep across all services."""
        if not self.health_monitor:
            return
        self._record_run("health_check")
        try:
            result = await self.health_monitor.check_all_services()
            overall = result.get("overall", "unknown")
            if overall != "healthy":
                logger.warning(
                    "Health sweep: overall=%s",
                    overall,
                    components={k: v.get("status") for k, v in result.get("components", {}).items()},
                )
        except Exception as e:
            self._record_error("health_check")
            logger.debug("Health check job error (non-fatal): %s", e)

    async def _run_streaming_anomaly(self) -> None:
        """Feed current SLI metrics into River ADWIN drift detectors."""
        if not self.streaming_anomaly or not self.base_engine:
            return
        self._record_run("streaming_anomaly")
        try:
            slis = self.base_engine.get_observability_slis()

            # DB semaphore free slots (primary capacity metric)
            free = slis.get("db_semaphore_free")
            if free is not None:
                self.streaming_anomaly.update("db_semaphore_free", float(free))

            # Data freshness (seconds since last price update)
            # NOTE: tracked as a separate metric, NOT as "api_response_ms" — mixing these
            # caused false ADWIN drift alarms whenever ingestion stalled (normal), which
            # triggered system=degraded and reduced fleet sizing to 50%.
            freshness = slis.get("data_freshness_seconds")
            if freshness is not None:
                self.streaming_anomaly.update("data_freshness_ms", float(freshness * 1000))

            # Multivariate score on available metrics
            features = {}
            if free is not None:
                features["db_semaphore_free"] = float(free)
            if features:
                self.streaming_anomaly.score(features)

        except Exception as e:
            self._record_error("streaming_anomaly")
            logger.debug("Streaming anomaly job error: %s", e)

    async def _run_log_miner(self) -> None:
        """Tail paper_trading.log and emit warnings for critical patterns."""
        if not self.log_miner:
            return
        self._record_run("log_miner")
        try:
            alerts = self.log_miner.tail_log()
            for alert in alerts:
                count = alert.get("occurrence_count", 1)
                logger.warning(
                    "LogMiner: critical pattern '%s' (occurrence #%d)",
                    alert["pattern"], count,
                    template=alert.get("template", "")[:80],
                )
            # Also check for frequency spikes
            spikes = self.log_miner.get_spike_alerts()
            for spike in spikes:
                logger.warning(
                    "LogMiner: template frequency spike (id=%s, count=%d vs baseline=%.1f, ratio=%.1fx)",
                    spike["template_id"], spike["current_count"],
                    spike["baseline"], spike["ratio"],
                )
        except Exception as e:
            self._record_error("log_miner")
            logger.debug("Log miner job error: %s", e)

    async def _run_degradation_check(self) -> None:
        """Log fleet degradation tier when system is not at full health."""
        if not self.degradation_manager:
            return
        self._record_run("degradation_check")
        try:
            status = self.degradation_manager.get_fleet_status()
            tier = status.get("degradation_tier", 0)
            if tier > 0:
                logger.warning(
                    "Fleet health: tier=%d, healthy=%d/%d, sizing=%.0f%%, close_only=%s",
                    tier,
                    status["healthy_count"],
                    status["registered_bots"],
                    status["sizing_multiplier"] * 100,
                    status["close_only_mode"],
                )
        except Exception as e:
            self._record_error("degradation_check")
            logger.debug("Degradation check job error: %s", e)

    async def _run_drawdown_check(self) -> None:
        """Update portfolio drawdown breaker with current equity estimate."""
        if not self.drawdown_breaker:
            return
        self._record_run("drawdown_check")
        try:
            equity = 0.0
            if self._equity_fn:
                equity = float(self._equity_fn())
            elif self.base_engine and hasattr(self.base_engine, "order_gateway"):
                gw = self.base_engine.order_gateway
                if gw:
                    # Use paper_trading_engine cash + position cost basis as equity.
                    # cash decreases as positions open; cost basis increases to match.
                    # equity ≈ initial_capital - fees - realized_losses (never trips on mere deployment).
                    # Old formula (1000 - exposure * 0.05) was treating deployed capital as loss.
                    pe = getattr(gw, "paper_trading_engine", None)
                    if pe and getattr(pe, "enabled", False):
                        pos_cost = sum(
                            p.get("size", 0) * p.get("avg_price", 0)
                            for p in getattr(pe, "positions", {}).values()
                        )
                        equity = pe.cash + pos_cost
                    else:
                        # No paper engine — use TOTAL_CAPITAL setting as safe baseline
                        from config.settings import settings as _settings
                        equity = float(getattr(_settings, "TOTAL_CAPITAL", 1000.0))
            if equity > 0:
                tripped = self.drawdown_breaker.update_equity(equity)
                # Phase 7.1: propagate drawdown % to risk manager for Kelly compression
                _status = self.drawdown_breaker.get_status()
                _dd_pct = abs(float(_status.get("drawdown_from_peak_pct", 0.0))) / 100.0
                if self.base_engine and hasattr(self.base_engine, "risk_manager"):
                    _rm = self.base_engine.risk_manager
                    if _rm is not None:
                        _rm._cached_drawdown_pct = _dd_pct
                if tripped:
                    logger.error(
                        "Portfolio drawdown circuit tripped — blocking new positions",
                        status=_status,
                    )
                    # Escalate to degradation manager
                    if self.degradation_manager:
                        self.degradation_manager.force_safe_mode("portfolio_drawdown_trip")
        except Exception as e:
            self._record_error("drawdown_check")
            logger.debug("Drawdown check job error: %s", e)

    async def _run_sli_report(self) -> None:
        """Log a full SLI snapshot every 2 minutes."""
        if not self.base_engine:
            return
        self._record_run("sli_report")
        try:
            slis = self.base_engine.get_observability_slis()
            anomaly_summary = {}
            if self.streaming_anomaly:
                anomaly_summary = self.streaming_anomaly.get_drift_summary()
            logger.info(
                "SLI report",
                db_free=slis.get("db_semaphore_free"),
                data_freshness_s=slis.get("data_freshness_seconds"),
                drift_events=anomaly_summary.get("total_drift_events", 0),
                anomaly_score=anomaly_summary.get("recent_anomaly_score", 0),
            )
        except Exception as e:
            self._record_error("sli_report")
            logger.debug("SLI report job error: %s", e)

    async def _run_exposure_reconcile(self) -> None:
        """Rebuild OrderGateway in-memory exposure from DB ground truth every 5 minutes.

        Corrects drift caused by SELL write failures, manual DB edits, or any
        other divergence between in-memory _total_exposure_usd and actual open positions.
        """
        if not self.base_engine:
            return
        gw = getattr(self.base_engine, "order_gateway", None)
        db = getattr(self.base_engine, "db", None)
        if gw is None or db is None:
            return
        self._record_run("exposure_reconcile")
        try:
            await gw.reconcile_exposure_from_db(db)
        except Exception as e:
            self._record_error("exposure_reconcile")
            logger.debug("Exposure reconcile job error: %s", e)

    async def _run_sports_calibration(self) -> None:
        """
        Update sports_calibration table with current bet outcomes and recompute Kelly fractions.

        Reads paper_trades WHERE bot_name LIKE 'Sports%' AND status = 'closed'.
        Computes per-(sport, market_type) Brier score from outcome data.
        Writes results via adaptive_kelly.update_calibration().

        Phase 6: runs every SPORTS_CALIBRATION_UPDATE_INTERVAL seconds (default 3600s).
        """
        db = self.sports_db
        if db is None and self.base_engine:
            db = getattr(self.base_engine, "db", None)
        if db is None:
            return

        self._record_run("sports_calibration")
        try:
            from sqlalchemy import text

            # Aggregate resolved sports bets by sport + market_type
            async with db.get_session() as session:
                result = await session.execute(
                    text(
                        "SELECT "
                        "  COALESCE(metadata->>'sport', 'unknown') as sport, "
                        "  COALESCE(metadata->>'market_type', 'moneyline') as market_type, "
                        "  COUNT(*) as bet_count, "
                        "  SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as correct_count, "
                        "  AVG(POWER(confidence - CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END, 2)) as brier_score "
                        "FROM paper_trades "
                        "WHERE bot_name IN ('SportsInjuryBot', 'SportsLiveBot', 'SportsArbBot') "
                        "  AND status = 'closed' "
                        "  AND confidence IS NOT NULL "
                        "GROUP BY sport, market_type "
                        "HAVING COUNT(*) >= 10"  # minimum 10 bets for statistical validity
                    )
                )
                rows = result.fetchall()

            if not rows:
                logger.debug("HealthScheduler: sports_calibration — no resolved bets yet (need >= 10)")
                return

            from sports.kelly.adaptive_kelly import update_calibration
            for row in rows:
                sport = str(row[0])
                market_type = str(row[1])
                bet_count = int(row[2])
                correct_count = int(row[3]) if row[3] is not None else 0
                brier_score = float(row[4]) if row[4] is not None else 0.25

                await update_calibration(
                    sport=sport,
                    market_type=market_type,
                    bet_count=bet_count,
                    correct_count=correct_count,
                    brier_score=brier_score,
                    db=db,
                )

            logger.info(
                "HealthScheduler: sports_calibration updated",
                sport_market_pairs=len(rows),
            )
        except Exception as e:
            self._record_error("sports_calibration")
            logger.debug("Sports calibration job error: %s", e)

    # ── Counters ──────────────────────────────────────────────────────────────

    def _record_run(self, job_id: str) -> None:
        self._job_run_counts[job_id] = self._job_run_counts.get(job_id, 0) + 1

    def _record_error(self, job_id: str) -> None:
        self._job_error_counts[job_id] = self._job_error_counts.get(job_id, 0) + 1

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the APScheduler (non-blocking, runs jobs on asyncio event loop)."""
        if not APSCHEDULER_AVAILABLE:
            logger.warning("apscheduler not available — HealthScheduler running in no-op mode")
            return
        if self._running:
            return

        self._scheduler = AsyncIOScheduler(timezone="UTC")

        from config.settings import settings as _cfg
        _sports_cal_interval = int(getattr(_cfg, "SPORTS_CALIBRATION_UPDATE_INTERVAL", 3600))

        jobs = [
            ("health_check",        self._run_health_check,        60,  30),
            ("streaming_anomaly",   self._run_streaming_anomaly,    10,   5),
            ("log_miner",           self._run_log_miner,            30,  15),
            ("degradation_check",   self._run_degradation_check,    30,  15),
            ("drawdown_check",      self._run_drawdown_check,        30,  15),
            ("sli_report",          self._run_sli_report,           120, 60),
            ("exposure_reconcile",  self._run_exposure_reconcile,   30, 15),   # I16: was 300s — phantom exposure now detected within 30s
            ("sports_calibration",  self._run_sports_calibration,   _sports_cal_interval, 300),
        ]

        for job_id, fn, interval_s, grace_s in jobs:
            self._scheduler.add_job(
                fn,
                IntervalTrigger(seconds=interval_s),
                id=job_id,
                replace_existing=True,
                misfire_grace_time=grace_s,
                max_instances=1,
            )

        self._scheduler.start()
        self._running = True
        logger.info(
            "HealthScheduler started: %d jobs (%s)",
            len(jobs),
            ", ".join(f"{j[0]}@{j[2]}s" for j in jobs),
        )

    def stop(self) -> None:
        """Gracefully stop the scheduler."""
        if self._scheduler and self._running:
            self._scheduler.shutdown(wait=False)
            self._running = False
            logger.info("HealthScheduler stopped (jobs run: %s)", dict(self._job_run_counts))

    def get_stats(self) -> Dict[str, Any]:
        """Job run/error counts and scheduler state."""
        return {
            "running": self._running,
            "apscheduler_available": APSCHEDULER_AVAILABLE,
            "job_run_counts": dict(self._job_run_counts),
            "job_error_counts": dict(self._job_error_counts),
            "job_count": len(self._scheduler.get_jobs()) if self._scheduler else 0,
        }
