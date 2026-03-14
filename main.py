"""
Polymarket AI Trading System V2 - Main Entry Point.

Startup flow:
  1. Pre-flight health gate (DB + Redis ping)
  2. Initialize BaseEngine (50+ components)
  3. Start enabled bots from BOT_REGISTRY
  4. Watchdog monitors bot liveness; restarts dead bots or shuts down if all fail
"""
import asyncio
import json
import logging
import os as _os
import signal
import sys
import time
from dataclasses import dataclass
import structlog

# ── Structlog configuration ─────────────────────────────────────────────
# MUST happen BEFORE any application imports that call get_logger() at module level.
# With cache_logger_on_first_use=True, loggers cached during import would use the
# default factory forever if we configured after imports.

# Ensure stdout is line-buffered when redirected to a file
if not sys.stdout.isatty():
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

# Direct file logger: writes to data/paper_trading.log immediately, bypassing stdout
# buffering issues when run via subprocess, Start-Process, or shell redirection.
_LOG_FILE = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "paper_trading.log")
_log_fh = None
try:
    _log_fh = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)  # line-buffered
except Exception:
    pass


class _TeeLogger:
    """Logger that writes to both stdout (flush=True) and the log file."""
    def msg(self, message: str, **kw) -> None:
        print(message, flush=True)
        if _log_fh:
            try:
                _log_fh.write(message + "\n")
            except Exception:
                pass
    log = debug = info = warning = warn = error = critical = fatal = exception = msg


class _TeeLoggerFactory:
    def __call__(self, *args, **kwargs):
        return _TeeLogger()


# Import settings early (before structlog.configure) — settings itself doesn't log
from config.settings import settings
_log_level = getattr(settings, "LOG_LEVEL", "INFO").upper()

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, _log_level, logging.INFO)),
    logger_factory=_TeeLoggerFactory(),
    cache_logger_on_first_use=True,
)

# Session 47: Suppress noisy sklearn warnings BEFORE application imports.
# sklearn.utils.parallel.py emits UserWarning ~600+ times per scan cycle (10 markets × 6-8
# models each). Without this, journald suppresses 12,000+ messages per scan.
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="sklearn")
warnings.filterwarnings("ignore", category=FutureWarning, module="sklearn")
try:
    from sklearn.exceptions import ConvergenceWarning
    warnings.filterwarnings("ignore", category=ConvergenceWarning)
except ImportError:
    pass

# NOW import application modules (their module-level get_logger() calls will use TeeLogger)
from structlog import get_logger
from base_engine.base_engine import BaseEngine
from bots.arbitrage_bot import ArbitrageBot
from bots.mirror_bot import MirrorBot
from bots.cross_platform_arb_bot import CrossPlatformArbBot
from bots.oracle_bot import OracleBot
from bots.sports_bot import SportsBot
from bots.llm_forecaster_bot import LLMForecasterBot
from bots.weather_bot import WeatherBot
from bots.sports_injury_bot import SportsInjuryBot
from bots.sports_live_bot import SportsLiveBot
from bots.sports_arb_bot import SportsArbBot
from bots.esports_bot import EsportsBot
from bots.esports_live_bot import EsportsLiveBot
from bots.esports_series_bot import EsportsSeriesBot
from bots.logical_arb_bot import LogicalArbBot

logger = get_logger()


@dataclass
class PreflightResult:
    api_ok: bool = False
    db_ok: bool = False
    redis_ok: bool = False

    @property
    def all_ok(self) -> bool:
        return self.api_ok and self.db_ok

    @property
    def degraded(self) -> bool:
        return not self.all_ok and (self.api_ok or self.db_ok)


# Mapping of bot names to their classes and enable flags
BOT_REGISTRY = {
    "ArbitrageBot": (ArbitrageBot, "BOT_ENABLED_ARBITRAGE"),
    "MirrorBot": (MirrorBot, "BOT_ENABLED_MIRROR"),
    "CrossPlatformArbBot": (CrossPlatformArbBot, "BOT_ENABLED_CROSS_PLATFORM_ARB"),
    "OracleBot": (OracleBot, "BOT_ENABLED_ORACLE"),
    "SportsBot": (SportsBot, "BOT_ENABLED_SPORTS"),
    "LLMForecasterBot": (LLMForecasterBot, "BOT_ENABLED_LLM_FORECASTER"),
    "WeatherBot": (WeatherBot, "BOT_ENABLED_WEATHER"),
    # Sports betting bots — Migration 022 (all disabled by default)
    "SportsInjuryBot": (SportsInjuryBot, "BOT_ENABLED_SPORTS_INJURY"),
    "SportsLiveBot":   (SportsLiveBot,   "BOT_ENABLED_SPORTS_LIVE"),
    "SportsArbBot":    (SportsArbBot,    "BOT_ENABLED_SPORTS_ARB"),
    # Esports bots — Migration 024 (all disabled by default)
    "EsportsBot":       (EsportsBot,       "BOT_ENABLED_ESPORTS"),
    "EsportsLiveBot":   (EsportsLiveBot,   "BOT_ENABLED_ESPORTS_LIVE"),
    "EsportsSeriesBot": (EsportsSeriesBot, "BOT_ENABLED_ESPORTS_SERIES"),
    # Logical arbitrage bot — cross-market constraint violations
    "LogicalArbBot":    (LogicalArbBot,    "BOT_ENABLED_LOGICAL_ARB"),
}

WATCHDOG_INTERVAL_SECONDS = 30
MAX_GLOBAL_RESTART_ATTEMPTS = 10  # I15: raised from 3 — transient DB timeout was causing permanent death


async def _preflight_check(base_engine: BaseEngine) -> bool:
    """Pre-flight health gate: verify DB, Redis, and API are reachable before starting bots.

    Returns True if core infrastructure is available. Database failure is
    now non-fatal -- the system degrades to API-only mode (no historical
    data, no trade persistence) but bots can still scan and paper-trade.
    """
    import asyncio as _aio
    db_ok = False
    api_ok = False
    redis_ok = False

    # ── API check ──────────────────────────────────────────────────────
    client = getattr(base_engine, "client", None)
    if client:
        try:
            markets = await _aio.wait_for(client.get_markets(limit=1), timeout=10)
            if markets:
                api_ok = True
                logger.info("Pre-flight: Polymarket API OK")
            else:
                logger.warning("Pre-flight: Polymarket API returned empty response")
        except Exception as e:
            err_str = str(e)
            if "403" in err_str or "Forbidden" in err_str:
                logger.error(
                    "Pre-flight: Polymarket API returned 403 Forbidden. "
                    "This typically means your IP is blocked (US/restricted region). "
                    "ACTION: Verify VPS IP is in an allowed jurisdiction."
                )
            else:
                logger.warning("Pre-flight: Polymarket API unreachable", error=err_str)
    else:
        logger.warning("Pre-flight: Polymarket client not initialized")

    # ── Database check ─────────────────────────────────────────────────
    # Use get_raw_session() (bypasses semaphore) so the pre-flight check succeeds
    # even when all 15 semaphore slots are taken by startup background services.
    # Using get_session() here causes a 30s semaphore timeout and falsely reports
    # the database as unreachable during the startup burst.
    db = getattr(base_engine, "db", None)
    if db and getattr(db, "session_factory", None):
        try:
            from sqlalchemy import text
            async with db.get_raw_session() as session:
                await _aio.wait_for(session.execute(text("SELECT 1")), timeout=8)
            logger.info("Pre-flight: database OK")
            db_ok = True

            # Clean up stale idle-in-transaction sessions
            try:
                async with db.engine.begin() as _cleanup_conn:
                    _cleanup_result = await _cleanup_conn.execute(text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE state = 'idle in transaction' "
                        "AND query_start < NOW() - INTERVAL '10 minutes' "
                        "AND pid <> pg_backend_pid()"
                    ))
                    _terminated = sum(1 for row in _cleanup_result.fetchall() if row[0])
                    if _terminated:
                        logger.warning("Pre-flight: terminated %d stale idle-in-transaction sessions", _terminated)
            except Exception as _cleanup_err:
                logger.debug("Pre-flight: stale session cleanup skipped: %s", _cleanup_err)

            # Clean up stale advisory locks from previous crashed processes.
            # Advisory locks (IDs 100001-100008) are held by database_lock.py sessions.
            # After a force-kill, the pooler may keep the server-side session alive.
            try:
                async with db.engine.begin() as _lock_conn:
                    _lock_result = await _lock_conn.execute(text(
                        "SELECT pg_terminate_backend(pid) FROM pg_locks "
                        "WHERE locktype = 'advisory' "
                        "AND objid IN (100001, 100002, 100003, 100004, 100005, 100006, 100007, 100008) "
                        "AND pid <> pg_backend_pid()"
                    ))
                    _lock_cleared = sum(1 for row in _lock_result.fetchall() if row[0])
                    if _lock_cleared:
                        logger.warning("Pre-flight: terminated %d sessions holding stale advisory locks", _lock_cleared)
            except Exception as _lock_err:
                logger.debug("Pre-flight: advisory lock cleanup skipped: %s", _lock_err)
        except Exception as e:
            err_str = str(e)
            if "ConnectionDoesNotExistError" in err_str or "SSL" in err_str:
                logger.error(
                    "Pre-flight: database connection dropped mid-query. "
                    "Database connection dropped mid-query. "
                    "ACTION: Check PostgreSQL status (sudo systemctl status postgresql) and restart if needed."
                )
            else:
                logger.warning(
                    "Pre-flight: database unreachable -- running in API-only mode",
                    error=err_str,
                )
    else:
        logger.warning(
            "Pre-flight: database not configured -- running in API-only mode "
            "(DATABASE_URL empty or session_factory missing)"
        )

    # ── Redis check ────────────────────────────────────────────────────
    cache = getattr(base_engine, "cache", None)
    if cache and getattr(settings, "REDIS_ENABLED", True):
        try:
            if hasattr(cache, "redis") and cache.redis:
                await cache.redis.ping()
                redis_ok = True
                logger.info("Pre-flight: redis OK")
            else:
                logger.warning("Pre-flight: redis client not connected (running without cache)")
        except Exception as e:
            logger.warning("Pre-flight: redis ping failed (running without cache)", error=str(e))
    else:
        logger.info("Pre-flight: redis disabled or not configured")

    # ── Summary ────────────────────────────────────────────────────────
    mode = settings.SIMULATION_MODE
    issues = []
    if not api_ok:
        issues.append("API unreachable (no market data)")
    if not db_ok:
        issues.append("Database unreachable (no persistence/training)")
    if not getattr(settings, "REDIS_ENABLED", True):
        issues.append("Redis disabled (no caching)")

    if issues:
        logger.warning(
            "Pre-flight: starting in DEGRADED MODE",
            simulation_mode=mode,
            issues=issues,
        )
    else:
        logger.info(
            "Pre-flight: all systems operational",
            simulation_mode=mode,
        )

    _result = PreflightResult(api_ok=api_ok, db_ok=db_ok, redis_ok=redis_ok)
    if not _result.all_ok:
        logger.warning("Preflight: degraded mode", api_ok=api_ok, db_ok=db_ok, redis_ok=redis_ok)
    return _result


async def _watchdog(bots: dict, base_engine: BaseEngine) -> None:
    """
    Monitor bot liveness. Restart dead bots up to MAX_GLOBAL_RESTART_ATTEMPTS.
    I15: Exponential backoff between restart attempts (30s → 60s → 120s … capped at 600s).
    Session 51: heartbeat staleness check, decision_events retention, sys.exit on all-dead.
    """
    restart_counts = {name: 0 for name in bots}
    restart_backoff: dict = {name: 30.0 for name in bots}  # I15: per-bot backoff seconds
    _last_retention_cleanup: float = 0.0  # Session 51: P2-3
    _last_snapshot: float = 0.0  # Session 83: daily equity snapshots
    _last_reconciliation: float = 0.0  # Session 83: 6h integrity check
    _last_partition_check: float = 0.0  # Session 83: monthly partition auto-creation

    while True:
        await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)

        alive_count = 0
        for bot_name, bot in list(bots.items()):
            if bot.running:
                alive_count += 1
                restart_backoff[bot_name] = 30.0  # I15: reset backoff when bot is healthy
                continue

            # Bot is dead -- try restart
            if restart_counts[bot_name] < MAX_GLOBAL_RESTART_ATTEMPTS:
                restart_counts[bot_name] += 1
                backoff = restart_backoff.get(bot_name, 30.0)
                logger.warning(
                    "Watchdog: restarting dead bot",
                    bot_name=bot_name,
                    attempt=restart_counts[bot_name],
                    max_attempts=MAX_GLOBAL_RESTART_ATTEMPTS,
                    backoff_s=backoff,
                )
                await asyncio.sleep(backoff)  # I15: exponential backoff before restart
                restart_backoff[bot_name] = min(backoff * 2, 600.0)  # double, cap at 10 min
                try:
                    await bot.start()
                    alive_count += 1
                except Exception as e:
                    logger.error("Watchdog: bot restart failed", bot_name=bot_name, error=str(e))
            else:
                logger.error(
                    "Watchdog: bot exhausted restart attempts",
                    bot_name=bot_name,
                    attempts=restart_counts[bot_name],
                )

        # Session 51 P0-2: Heartbeat staleness check — detect bots that are running but silent
        _alerting = getattr(base_engine, "alerting_system", None)
        _db = getattr(base_engine, "db", None)
        if _db and _db.session_factory and _alerting:
            try:
                from sqlalchemy import text as sa_text
                from base_engine.monitoring.alerting import AlertSeverity
                _stale_minutes = getattr(settings, "BOT_HEARTBEAT_STALE_MINUTES", 15)
                # Map bot heartbeat names → settings flag so disabled bots don't trigger false alarms.
                # A stale heartbeat for a bot explicitly disabled via BOT_ENABLED_*=false is expected.
                _bot_enabled_map = {
                    "ArbitrageBot": "BOT_ENABLED_ARBITRAGE",
                    "MirrorBot": "BOT_ENABLED_MIRROR",
                    "CrossPlatformArbBot": "BOT_ENABLED_CROSS_PLATFORM_ARB",
                    "OracleBot": "BOT_ENABLED_ORACLE",
                    "SportsBot": "BOT_ENABLED_SPORTS",
                    "LLMForecasterBot": "BOT_ENABLED_LLM_FORECASTER",
                    "SportsInjuryBot": "BOT_ENABLED_SPORTS_INJURY",
                    "SportsLiveBot": "BOT_ENABLED_SPORTS_LIVE",
                    "SportsArbBot": "BOT_ENABLED_SPORTS_ARB",
                    "LogicalArbBot": "BOT_ENABLED_LOGICAL_ARB",
                    "WeatherBot": "BOT_ENABLED_WEATHER",
                    "EsportsBot": "BOT_ENABLED_ESPORTS",
                    "EsportsLiveBot": "BOT_ENABLED_ESPORTS_LIVE",
                    "EsportsSeriesBot": "BOT_ENABLED_ESPORTS_SERIES",
                }
                async with _db.get_session() as _hb_sess:
                    _hb_result = await _hb_sess.execute(
                        sa_text("""
                            SELECT bot_name,
                                   EXTRACT(EPOCH FROM (NOW() - last_scan_at)) / 60 AS minutes_stale
                            FROM bot_heartbeats
                            WHERE last_scan_at < NOW() - INTERVAL '1 minute' * :threshold
                        """),
                        {"threshold": _stale_minutes},
                    )
                    for row in _hb_result.fetchall():
                        _setting_key = _bot_enabled_map.get(row[0])
                        if _setting_key and not getattr(settings, _setting_key, True):
                            continue  # Bot is intentionally disabled — expected to be stale
                        await _alerting.send_alert(
                            title=f"Bot {row[0]} is stale",
                            message=f"Last scan {row[1]:.1f}m ago (threshold: {_stale_minutes}m)",
                            severity=AlertSeverity.WARNING,
                            source="watchdog.heartbeat",
                            metadata={"bot_name": row[0], "minutes_stale": float(row[1])},
                        )
            except Exception as e:
                logger.debug("Heartbeat staleness check failed: %s", e)

        # Check scheduler health (SF-28)
        if hasattr(base_engine, 'ingestion_scheduler') and base_engine.ingestion_scheduler:
            _is = base_engine.ingestion_scheduler
            if hasattr(_is, 'running') and not _is.running:
                logger.warning("Watchdog: ingestion_scheduler stopped -- restarting")
                try:
                    await _is.start()
                except Exception as e:
                    logger.error("Watchdog: ingestion_scheduler restart failed: %s", e)

        if hasattr(base_engine, 'scheduler') and base_engine.scheduler:
            _ls = base_engine.scheduler
            if hasattr(_ls, 'running') and not _ls.running:
                logger.warning("Watchdog: learning_scheduler stopped -- restarting")
                try:
                    await _ls.start()
                except Exception as e:
                    logger.error("Watchdog: learning_scheduler restart failed: %s", e)

        # Session 51 P2-3: Daily decision_events retention cleanup
        _now_ts = time.time()
        if _now_ts - _last_retention_cleanup > 86400:
            _event_bus = getattr(base_engine, "event_bus", None)
            if _event_bus and hasattr(_event_bus, "_retention_cleanup"):
                try:
                    await _event_bus._retention_cleanup(retain_days=30)
                    _last_retention_cleanup = _now_ts
                    logger.info("Watchdog: decision_events retention cleanup completed")
                except Exception as e:
                    logger.debug("Retention cleanup failed: %s", e)

        # Daily equity snapshots (position_snapshots removed in migration 052)
        if _now_ts - _last_snapshot > 86400:
            _db = getattr(base_engine, "db", None)
            if _db and hasattr(_db, "take_equity_snapshot"):
                try:
                    from datetime import date as _date_cls
                    _today = _date_cls.today()
                    await _db.take_equity_snapshot(_today)
                    _last_snapshot = _now_ts
                    logger.info("Watchdog: daily equity snapshot completed for %s", _today)
                except Exception as e:
                    logger.warning("Watchdog: daily snapshot failed: %s", e)

        # Session 83: 6-hour reconciliation check
        if _now_ts - _last_reconciliation > 21600:
            _db = getattr(base_engine, "db", None)
            if _db and hasattr(_db, "run_reconciliation"):
                try:
                    _breaks = await _db.run_reconciliation()
                    if _breaks and _breaks > 0:
                        logger.warning("Watchdog: reconciliation found %d breaks", _breaks)
                except Exception as e:
                    logger.warning("Watchdog: reconciliation failed: %s", e)
                finally:
                    _last_reconciliation = _now_ts

        # Daily partition auto-creation (trade_events only; position_snapshots removed in 052)
        if _now_ts - _last_partition_check > 86400:
            _db = getattr(base_engine, "db", None)
            if _db and hasattr(_db, "ensure_future_partitions"):
                try:
                    _parts = await _db.ensure_future_partitions()
                    if _parts > 0:
                        logger.info("Watchdog: created %d future partitions", _parts)
                except Exception as e:
                    logger.warning("Watchdog: partition creation failed: %s", e)
                finally:
                    _last_partition_check = _now_ts

        if alive_count == 0 and len(bots) > 0:
            logger.critical(
                "Watchdog: ALL bots dead after restart attempts -- initiating shutdown",
                total_bots=len(bots),
            )
            # Signal shutdown via event bus if available
            event_bus = getattr(base_engine, "event_bus", None)
            if event_bus:
                await event_bus.emit("system_shutdown", {"reason": "all_bots_dead"})
            # Session 51 P2-2: Alert before exit, then sys.exit for systemd restart
            if _alerting:
                try:
                    from base_engine.monitoring.alerting import AlertSeverity
                    await _alerting.send_alert(
                        title="SYSTEM DOWN: All bots dead",
                        message="All bots exhausted restart attempts. Exiting for systemd restart.",
                        severity=AlertSeverity.CRITICAL,
                        source="watchdog",
                    )
                except Exception:
                    pass
            sys.exit(1)


_PROCESS_START = time.monotonic()


async def _health_server(bots_ref: dict, shutdown_event: asyncio.Event, port: int = 8765) -> None:
    """Lightweight asyncio health endpoint on localhost:{port}.

    Responds to any HTTP GET with JSON status — no new pip dependencies.
    Example:
        curl http://localhost:8765/health
        {"ok": true, "uptime_s": 142, "active_bots": ["MirrorBot", "WeatherBot", ...]}
    Port is localhost-only for security; never exposed to the internet.
    """
    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(reader.read(512), timeout=2.0)
        except Exception:
            pass
        payload = json.dumps({
            "ok": True,
            "uptime_s": round(time.monotonic() - _PROCESS_START, 1),
            "active_bots": list(bots_ref.keys()),
        })
        writer.write(
            f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n\r\n{payload}".encode()
        )
        try:
            await writer.drain()
        except Exception:
            pass
        writer.close()

    try:
        server = await asyncio.start_server(_handle, "127.0.0.1", port)
        logger.info("Health endpoint started", port=port)
        await shutdown_event.wait()
        server.close()
        await server.wait_closed()
        logger.info("Health endpoint stopped")
    except OSError as exc:
        # Non-fatal: port already in use or similar — bots still run
        logger.warning("Health endpoint failed to start", port=port, error=str(exc))
    except asyncio.CancelledError:
        pass


async def main():
    logger.info("Starting Polymarket AI Trading System V2")

    base_engine = None
    bots = {}
    watchdog_task = None
    health_task = None

    try:
        # ── Event loop + signal handling ───────────────────────────────
        loop = asyncio.get_running_loop()
        _shutdown_event = asyncio.Event()

        def _sigterm_handler():
            logger.info("Shutdown requested (SIGTERM)")
            _shutdown_event.set()

        try:
            loop.add_signal_handler(signal.SIGTERM, _sigterm_handler)
        except NotImplementedError:
            pass  # Windows doesn't support add_signal_handler

        # ── Initialize BaseEngine ──────────────────────────────────────
        base_engine = BaseEngine()
        await base_engine.init()
        await base_engine.start()

        # ── Pre-flight check ───────────────────────────────────────────
        await _preflight_check(base_engine)

        # ── Instantiate enabled bots ───────────────────────────────────
        for bot_name, (bot_cls, enable_flag) in BOT_REGISTRY.items():
            enabled = getattr(settings, enable_flag, True)
            if not enabled:
                logger.info("Bot disabled via config", bot_name=bot_name, flag=enable_flag)
                continue
            bots[bot_name] = bot_cls(base_engine)

        # ── Register bots with DegradationManager ──────────────────────
        # Each bot gets a BotStateMachine that feeds fleet-level tier recomputation.
        # Must happen after bots are instantiated, before start() is called.
        if hasattr(base_engine, "degradation_manager") and base_engine.degradation_manager is not None:
            for bot_name in bots:
                try:
                    machine = base_engine.degradation_manager.register_bot(bot_name)
                    # Attach machine to bot so scan loop can call record_health_ok/record_error
                    bot_obj = bots[bot_name]
                    if not hasattr(bot_obj, "state_machine") or bot_obj.state_machine is None:
                        bot_obj.state_machine = machine
                    logger.info("DegradationManager: registered bot", bot_name=bot_name)  # I48
                except Exception as e:
                    logger.warning("DegradationManager registration failed (non-fatal)", bot_name=bot_name, error=str(e))
            logger.info(
                "DegradationManager: registered %d bots for fleet health tracking",
                len(bots),
            )

        # ── Start bots with staggered delay ────────────────────────────
        # Stagger bot starts to spread cold-cache first scans.
        _BOT_START_STAGGER_SECONDS = 5
        failed_bots = []
        for bot_name, bot in list(bots.items()):
            try:
                base_engine.register_bot_for_price_events(bot)
                await bot.start()
                await asyncio.sleep(_BOT_START_STAGGER_SECONDS)
            except Exception as e:
                logger.error("Bot failed to start (skipping)", bot_name=bot_name, error=str(e))
                failed_bots.append(bot_name)

        for fb in failed_bots:
            bots.pop(fb, None)

        # ── Watchdog + health endpoint + wait for shutdown ─────────────
        watchdog_task = asyncio.create_task(_watchdog(bots, base_engine))

        # Session 51 P2-2: done_callback so watchdog crash doesn't go unnoticed
        def _watchdog_done(t):
            try:
                if not t.cancelled():
                    exc = t.exception()
                    if exc:
                        logger.critical("Watchdog crashed: %s", exc, exc_info=exc)
            except Exception:
                pass
        watchdog_task.add_done_callback(_watchdog_done)

        # Phase 5b: lightweight health endpoint on localhost:8765
        health_task = asyncio.create_task(_health_server(bots, _shutdown_event))

        logger.info("System running -- press Ctrl+C to stop", active_bots=len(bots))

        # Wait for either watchdog exit or SIGTERM/shutdown event
        shutdown_task = asyncio.create_task(_shutdown_event.wait())
        done, pending = await asyncio.wait(
            [watchdog_task, shutdown_task],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass

    except KeyboardInterrupt:
        logger.info("Shutdown requested (Ctrl+C)")
    except Exception as e:
        logger.error("Fatal error", error=str(e), exc_info=True)
        raise
    finally:
        # ── Graceful shutdown ──────────────────────────────────────────
        if watchdog_task and not watchdog_task.done():
            watchdog_task.cancel()
            try:
                await watchdog_task
            except asyncio.CancelledError:
                pass

        # Cancel health server task (shutdown_event already set, but guard against early exit paths)
        if health_task and not health_task.done():
            health_task.cancel()
            try:
                await health_task
            except asyncio.CancelledError:
                pass

        if bots:
            # Phase 2: wait for in-progress scan cycles to finish before cancelling tasks.
            # Prevents mid-trade cancellation. Timeout 25s stays inside systemd's default
            # TimeoutStopSec=90s with headroom for base_engine.stop() to flush state.
            logger.info("Graceful shutdown: waiting for active scan cycles (max 25s)")
            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        *[bot.wait_for_idle() for bot in bots.values()],
                        return_exceptions=True,
                    ),
                    timeout=25.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Graceful shutdown: scan wait timed out — proceeding with force stop")

            for bot_name, bot in bots.items():
                try:
                    await bot.stop()
                except Exception as e:
                    logger.warning("Error stopping bot", bot_name=bot_name, error=str(e))

        if base_engine:
            try:
                await base_engine.stop()
            except Exception as e:
                logger.warning("Error stopping base engine", error=str(e))

        logger.info("Shutdown complete")


if __name__ == "__main__":
    # Phase 8: uvloop — 2-4× faster async I/O (Rust event loop). Zero code changes elsewhere.
    try:
        import uvloop
        uvloop.install()
    except ImportError:
        pass  # uvloop not installed — fall back to default asyncio event loop
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        import traceback
        print("\n============================================================")
        print("FATAL ERROR -- system crashed during startup")
        print("============================================================")
        traceback.print_exc()
        print("============================================================")
        try:
            input("\nPress Enter to exit...")
        except EOFError:
            pass
        sys.exit(1)
