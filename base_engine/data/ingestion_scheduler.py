"""
IngestionScheduler - Runs periodic data ingestion (markets, prices) and elite status update.
When daily full ingestion is enabled, runs ingest_everything (markets + recent prices) once per 24h;
otherwise runs ingest_all_markets every interval.
Weekly full: when enabled, runs full 365-day price refresh once per week (incremental=False).
Optimal flow: resolution backfill runs after daily full ingestion to enable learnable trades.
Uses advisory locks to prevent concurrent ingestion/backfill/elite_update from multiple processes.
"""
import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional
from structlog import get_logger
from config.settings import settings

logger = get_logger()

RESOLUTION_BACKFILL_ENABLED = getattr(settings, "RESOLUTION_BACKFILL_ENABLED", True)
RESOLUTION_BACKFILL_AFTER_DAILY = getattr(settings, "RESOLUTION_BACKFILL_AFTER_DAILY", True)

DAILY_INTERVAL_SECONDS = 24 * 60 * 60
WEEKLY_INTERVAL_SECONDS = 7 * 24 * 60 * 60
PROGRESS_LOG_INTERVAL_SECONDS = 60

# I51: configurable ingest timeout — override INGESTION_TIMEOUT_SECONDS in .env
_INGESTION_TIMEOUT_SECONDS: float = float(getattr(settings, "INGESTION_TIMEOUT_SECONDS", 600.0))


class IngestionScheduler:
    """
    Periodically runs market (and optional price) ingestion and elite user update.
    When daily_full_ingestion_enabled, runs full ingest_everything once per 24h for continued learning.
    """

    def __init__(
        self,
        data_ingestion,
        elite_detector=None,
        interval_minutes: int = 5,
        top_markets_count: int = 1000,
        initial_delay_seconds: int = 30,
        daily_full_ingestion_enabled: bool = False,
        daily_days_back: int = 365,
        daily_markets_count: int = 1000,
        daily_prices_markets: int = 1000,
        alerting=None,
        auto_healer=None,
        performance_tracker=None,
    ):
        self.data_ingestion = data_ingestion
        self.performance_tracker = performance_tracker
        self.elite_detector = elite_detector
        self.alerting = alerting
        self.auto_healer = auto_healer
        self.interval_seconds = max(60, int(interval_minutes) * 60)
        self.top_markets_count = max(1, int(top_markets_count))
        self.initial_delay_seconds = max(0, int(initial_delay_seconds))
        self.daily_full_enabled = bool(daily_full_ingestion_enabled)
        self.daily_days_back = max(1, int(daily_days_back))
        self.daily_markets_count = max(1, int(daily_markets_count))
        self.daily_prices_markets = max(1, int(daily_prices_markets))
        self._last_full_run: Optional[datetime] = None
        self._last_weekly_full_run: Optional[datetime] = None
        self._last_health_check: Optional[datetime] = None
        self._last_mini_backfill: Optional[datetime] = None
        self.running = False
        self._task: Optional[asyncio.Task] = None

    def _on_loop_done(self, task: asyncio.Task) -> None:
        """Callback for loop task — auto-restart on crash."""
        if task.cancelled():
            return
        exc = task.exception()
        if exc:
            logger.critical("IngestionScheduler._loop() crashed: %s", exc, exc_info=exc)
            if self.running:
                logger.warning("IngestionScheduler: auto-restarting loop after crash")
                self._task = asyncio.create_task(self._loop())
                self._task.add_done_callback(self._on_loop_done)

    async def start(self) -> None:
        """Start the scheduled ingestion loop."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._loop())
        self._task.add_done_callback(self._on_loop_done)
        logger.info(
            "IngestionScheduler started",
            interval_seconds=self.interval_seconds,
            top_markets_count=self.top_markets_count,
        )

    async def stop(self) -> None:
        """Stop the scheduler."""
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("IngestionScheduler stopped")

    async def _loop(self) -> None:
        if self.initial_delay_seconds > 0:
            logger.info(
                "IngestionScheduler: waiting %s seconds before first run",
                self.initial_delay_seconds,
            )
            await asyncio.sleep(self.initial_delay_seconds)
        while self.running:
            try:
                await self._run_ingestion()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Scheduled ingestion failed: %s", e, exc_info=True)
            # Heartbeat: sleep in 60s chunks and log so long idle periods show activity
            remaining = self.interval_seconds
            logger.info("IngestionScheduler: next run in %s s", remaining)
            while remaining > 0 and self.running:
                chunk = min(60, remaining)
                await asyncio.sleep(chunk)
                remaining -= chunk
                if remaining > 0 and self.running:
                    logger.info("IngestionScheduler: idle, next run in %s s", remaining)

    async def _run_ingestion(self) -> None:
        """Run one ingestion cycle: daily full (markets + prices) if due, else markets only; then elite update."""
        if not self.data_ingestion:
            return
        db = getattr(self.data_ingestion, "db", None)
        if not db or not getattr(db, "session_factory", None):
            logger.warning("IngestionScheduler: no DB, skipping")
            return
        from base_engine.data.database_lock import acquire_lock, LockAcquisitionError
        logger.info("IngestionScheduler: starting run")
        now = datetime.now(timezone.utc)
        run_full = (
            self.daily_full_enabled
            and (self._last_full_run is None or (now - self._last_full_run).total_seconds() >= DAILY_INTERVAL_SECONDS)
        )
        weekly_full_enabled = getattr(settings, "WEEKLY_FULL_INGESTION_ENABLED", True)
        weekly_weekday = getattr(settings, "WEEKLY_FULL_INGESTION_WEEKDAY", 0)
        run_weekly_full = (
            weekly_full_enabled
            and run_full
            and now.weekday() == weekly_weekday
            and (
                self._last_weekly_full_run is None
                or (now - self._last_weekly_full_run).total_seconds() >= WEEKLY_INTERVAL_SECONDS
            )
        )
        use_incremental = run_full and not run_weekly_full
        try:
            async with acquire_lock(db, "ingestion", timeout_seconds=60):
                await self._do_ingestion(run_full, run_weekly_full, use_incremental, now)
                if getattr(settings, "PIPELINE_CANARY_AFTER_INGESTION", True):
                    try:
                        from base_engine.data.pipeline_canary import run_canary_after_markets, run_canary_after_prices
                        await run_canary_after_markets(db)
                        if run_full:
                            await run_canary_after_prices(db)
                    except Exception as ca:
                        logger.debug("Canary after ingestion: %s", ca)
        except LockAcquisitionError as e:
            logger.warning("IngestionScheduler: lock acquisition failed — another process may be stuck: %s", e)
            return
        except Exception as e:
            logger.warning("Scheduled ingestion failed: %s", e)
        try:
            if RESOLUTION_BACKFILL_ENABLED and RESOLUTION_BACKFILL_AFTER_DAILY and run_full:
                try:
                    async with acquire_lock(db, "resolution_backfill", timeout_seconds=30):
                        bf = await self.data_ingestion.run_resolution_backfill(
                            log_progress=True,
                            performance_tracker=self.performance_tracker,
                        )
                        if bf.get("inserted", 0) > 0 or bf.get("updated", 0) > 0:
                            logger.info("Resolution backfill: %d inserted, %d updated", bf.get("inserted", 0), bf.get("updated", 0))
                        if getattr(settings, "PIPELINE_CANARY_AFTER_INGESTION", True):
                            try:
                                from base_engine.data.pipeline_canary import run_canary_after_resolution_backfill
                                await run_canary_after_resolution_backfill(db)
                            except Exception as ca:
                                logger.debug("Canary after resolution backfill: %s", ca)
                except LockAcquisitionError:
                    logger.debug("Resolution backfill lock busy, skipping")
                except Exception as eb:
                    logger.warning("Resolution backfill failed (non-fatal): %s", eb)
        except Exception:
            pass
        if run_full and getattr(settings, "RUN_ORPHAN_CLEANUP_AFTER_INGESTION", False):
            try:
                from base_engine.data.orphan_cleanup import run_orphan_cleanup
                res = await run_orphan_cleanup(db, dry_run=False, cleanup_prices=False)
                if res.get("deleted_trades", 0) > 0:
                    logger.info("Orphan cleanup: removed %s trades", res["deleted_trades"])
            except Exception as oc:
                logger.debug("Orphan cleanup (non-fatal): %s", oc)

        # Mini backfill: run resolution backfill + prediction_log labeling + pseudo-labels
        # every 30 min (not just daily). Ensures markets are resolved and labels flow to
        # the model as soon as markets settle, without waiting for the 24h daily cycle.
        _mini_backfill_interval = int(getattr(settings, "MINI_BACKFILL_INTERVAL_MINUTES", 30)) * 60
        _now = datetime.now(timezone.utc)
        _mini_due = (
            self._last_mini_backfill is None
            or (_now - self._last_mini_backfill).total_seconds() >= _mini_backfill_interval
        )
        if _mini_due and RESOLUTION_BACKFILL_ENABLED:
            try:
                async with acquire_lock(db, "resolution_backfill", timeout_seconds=10):
                    # Phase 1: Insert missing markets + update resolutions from Gamma API
                    _rb_inserted = 0
                    try:
                        bf = await self.data_ingestion.run_resolution_backfill(
                            log_progress=False,
                            performance_tracker=self.performance_tracker,
                        )
                        _rb_inserted = bf.get("inserted", 0) + bf.get("updated", 0)
                    except Exception as exc:
                        logger.debug("mini_backfill_resolution_failed", error=str(exc))
                    # Phase 2: Propagate resolutions to prediction_log + paper_trades
                    pred_updated = await db.backfill_prediction_log_resolution()
                    pseudo_updated = 0
                    try:
                        pseudo_updated = await db.backfill_prediction_log_from_closed_trades()
                    except Exception:
                        pass
                    paper_updated = 0
                    try:
                        paper_updated = await db.backfill_paper_trades_resolution()
                    except Exception:
                        pass
                    if _rb_inserted > 0 or pred_updated > 0 or pseudo_updated > 0 or paper_updated > 0:
                        logger.info(
                            "Mini backfill: %d markets_resolved, %d prediction_log, %d pseudo-labels, %d paper_trades",
                            _rb_inserted, pred_updated, pseudo_updated, paper_updated,
                        )
                    self._last_mini_backfill = _now
            except Exception as mb_err:
                logger.debug("Mini backfill (non-fatal): %s", mb_err)

        # Periodic health check: run top-to-bottom every HEALTH_CHECK_INTERVAL_MINUTES.
        _health_interval = int(getattr(settings, "HEALTH_CHECK_INTERVAL_MINUTES", 60)) * 60
        _health_due = (
            self._last_health_check is None
            or (_now - self._last_health_check).total_seconds() >= _health_interval
        )
        if _health_due:
            try:
                from base_engine.monitoring.health_runner import HealthRunner
                runner = HealthRunner(db, settings)
                report = await runner.run()
                self._last_health_check = _now
                # Surface critical issues as alerts
                if self.alerting:
                    criticals = [i for i in report.issues if i.severity == "critical"]
                    if criticals:
                        try:
                            from base_engine.monitoring.alerting import AlertSeverity
                            await self.alerting.send_alert(
                                title=f"Health check: {len(criticals)} critical issue(s)",
                                message="\n".join(i.message for i in criticals[:5]),
                                severity=AlertSeverity.CRITICAL,
                                source="health_runner",
                            )
                        except Exception:
                            pass
            except Exception as hc_err:
                logger.debug("Health check (non-fatal): %s", hc_err)
        try:
            await self.data_ingestion.ingest_top_users()
        except Exception as eu:
            logger.warning("Top users ingest failed (non-fatal): %s", eu)
        try:
            await self.data_ingestion.ingest_elite_trader_activity()
        except Exception as ea:
            logger.warning("Elite trader activity ingest failed (non-fatal): %s", ea)
        if self.elite_detector is not None:
            try:
                async with acquire_lock(db, "elite_update", timeout_seconds=30):
                    await self.elite_detector.update_elite_status()
            except LockAcquisitionError:
                logger.debug("Elite update lock busy, skipping")
            except Exception as e:
                logger.warning("Elite status update failed (non-fatal): %s", e)

    async def _do_ingestion(self, run_full: bool, run_weekly_full: bool, use_incremental: bool, now: datetime) -> None:
        """Execute ingestion (markets + optional prices). Called inside ingestion lock."""
        if run_full:
            logger.info(
                "Running daily full ingestion (markets + historical prices)",
                incremental=use_incremental,
                weekly_full=run_weekly_full,
            )
            last_log_time = [0.0]  # mutable; first log after interval

            def progress_callback(prog: Dict[str, Any]) -> None:
                now_ts = time.monotonic()
                elapsed = now_ts - last_log_time[0]
                if last_log_time[0] == 0.0 or elapsed >= PROGRESS_LOG_INTERVAL_SECONDS:
                    last_log_time[0] = now_ts
                    logger.info(
                        "Ingestion progress",
                        phase=prog.get("phase"),
                        phase_name=prog.get("phase_name"),
                        markets_ingested=prog.get("markets_ingested"),
                        prices_ingested=prog.get("prices_ingested"),
                        current_market=prog.get("current_market"),
                        batch=prog.get("batch"),
                        total_batches=prog.get("total_batches"),
                        max_batches=prog.get("max_batches"),
                    )

            try:
                res = await asyncio.wait_for(
                    self.data_ingestion.ingest_everything(
                        top_markets_count=self.daily_markets_count,
                        days_back=self.daily_days_back,
                        max_markets_prices=self.daily_prices_markets,
                        progress_callback=progress_callback,
                        incremental=use_incremental,
                    ),
                    timeout=_INGESTION_TIMEOUT_SECONDS,  # I51: configurable via INGESTION_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                logger.error(
                    "IngestionScheduler: ingest_everything() timed out — advisory lock will be released",
                    timeout_s=_INGESTION_TIMEOUT_SECONDS,
                )
                res = {"success": False, "error": f"ingest_everything timeout after {_INGESTION_TIMEOUT_SECONDS}s", "phase1_count": 0}
            self._last_full_run = now
            if run_weekly_full:
                self._last_weekly_full_run = now
            logger.info(
                "Daily full ingestion complete",
                phase1=res.get("phase1_count", 0),
                phase2_success=res.get("success", False),
            )
            if res.get("phase1_count", 0) == 0 and res.get("error"):
                logger.warning(
                    "Full ingestion returned 0 markets - error: %s",
                    res.get("error"),
                )
            # PipelineGate: post-condition check, alert on failure, trigger AutoHealer if retriable
            db = getattr(self.data_ingestion, "db", None)
            if db and getattr(db, "session_factory", None):
                try:
                    from base_engine.monitoring.pipeline_gate import PipelineGate
                    from base_engine.monitoring.alerting import AlertSeverity

                    gate = PipelineGate(db, alerting=self.alerting)
                    gate_result = await gate.check_ingestion()
                    if not gate_result.passed:
                        logger.warning(
                            "Ingestion post-check failed: %s",
                            gate_result.summary,
                            failures=gate_result.failures,
                        )
                        if self.alerting:
                            await self.alerting.send_alert(
                                title="Ingestion post-check failed",
                                message=gate_result.summary,
                                severity=AlertSeverity.ERROR,
                                source="pipeline_gate",
                                metadata={"failures": gate_result.failures},
                            )
                        if gate_result.retriable and self.auto_healer:
                            try:
                                await self.auto_healer.auto_heal()
                            except Exception as ah:
                                logger.warning("AutoHealer after gate failure: %s", ah)
                except Exception as e:
                    logger.warning("PipelineGate check failed (non-fatal): %s", e)
        else:
            count = await self.data_ingestion.ingest_all_markets(
                top_markets_count=self.top_markets_count,
                include_closed=True,
            )
            logger.info("Scheduled ingestion complete", markets_ingested=count)
