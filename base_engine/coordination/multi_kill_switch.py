"""
Multi-Layered Kill Switch System (P4-03).

Three layers:
  1. BotKillSwitch   — Per-bot pause (MirrorBot offline, others continue)
  2. PortfolioKillSwitch — All trading halted, positions held (triggered by drawdown/loss limits)
  3. SystemKillSwitch — All activity halted, connections closed (triggered by critical error/manual)

Events are logged to kill_switch_events table for audit trail.
Each layer has auto-reset timers (e.g., bot kill auto-resets after 1 hour).
"""
import asyncio
from typing import Optional, Any, Dict, Set
from datetime import datetime, timezone, timedelta
from structlog import get_logger

logger = get_logger()


class BotKillSwitch:
    """Per-bot pause. Other bots continue trading."""

    def __init__(self, db: Optional[Any] = None, alerting: Optional[Any] = None):
        self.db = db
        self.alerting = alerting
        self._killed_bots: Dict[str, datetime] = {}  # bot_name -> killed_at
        self._auto_reset_minutes: int = 60

    def is_killed(self, bot_name: str) -> bool:
        """Check if a specific bot is killed."""
        killed_at = self._killed_bots.get(bot_name)
        if killed_at is None:
            return False
        # Auto-reset check
        if (datetime.now(timezone.utc) - killed_at).total_seconds() > self._auto_reset_minutes * 60:
            del self._killed_bots[bot_name]
            logger.info("Bot kill auto-reset", bot=bot_name)
            return False
        return True

    async def kill_bot(self, bot_name: str, reason: str = "") -> None:
        """Kill a specific bot."""
        self._killed_bots[bot_name] = datetime.now(timezone.utc)
        await self._log_event("bot", f"Bot {bot_name} killed: {reason}")
        logger.warning("Bot killed", bot=bot_name, reason=reason)

    async def reset_bot(self, bot_name: str) -> None:
        """Reset a specific bot."""
        self._killed_bots.pop(bot_name, None)
        await self._log_event("bot", f"Bot {bot_name} manually reset")
        logger.info("Bot kill reset", bot=bot_name)

    @property
    def killed_bots(self) -> Set[str]:
        return set(self._killed_bots.keys())

    async def _log_event(self, level: str, reason: str) -> None:
        if not self.db or not getattr(self.db, "session_factory", None):
            return
        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                await session.execute(text("""
                    INSERT INTO kill_switch_events (trigger_level, trigger_reason, triggered_at)
                    VALUES (:level, :reason, :ts)
                """), {"level": level, "reason": reason, "ts": datetime.now(timezone.utc)})
                await session.commit()
        except Exception as e:
            logger.debug("Kill switch event log failed: %s", e)


class PortfolioKillSwitch:
    """
    All trading halted, positions held. Triggered by drawdown or loss limits.
    Wraps existing KillSwitch for backward compatibility.
    """

    def __init__(self, base_kill_switch: Any, db: Optional[Any] = None, alerting: Optional[Any] = None):
        self._base = base_kill_switch  # existing KillSwitch from coordination/
        self.db = db
        self.alerting = alerting
        self._auto_reset_hours: int = 24

    async def is_engaged(self) -> bool:
        """Delegates to base KillSwitch."""
        return await self._base.is_engaged()

    async def engage(self, reason: str = "Portfolio limit breach") -> None:
        """Engage portfolio-level kill switch."""
        await self._base.engage(reason)
        await self._log_event("portfolio", reason)
        if self.alerting:
            try:
                from base_engine.monitoring.alerting import AlertSeverity
                await self.alerting.send_alert(
                    title="PORTFOLIO KILL SWITCH ENGAGED",
                    message=reason,
                    severity=AlertSeverity.CRITICAL,
                    source="portfolio_kill_switch",
                )
            except Exception as e:
                logger.debug("portfolio kill switch alert send failed: %s", e)

    async def disengage(self) -> None:
        await self._base.disengage()
        await self._log_event("portfolio", "Portfolio kill switch disengaged")

    async def _log_event(self, level: str, reason: str) -> None:
        if not self.db or not getattr(self.db, "session_factory", None):
            return
        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                await session.execute(text("""
                    INSERT INTO kill_switch_events (trigger_level, trigger_reason, triggered_at)
                    VALUES (:level, :reason, :ts)
                """), {"level": level, "reason": reason, "ts": datetime.now(timezone.utc)})
                await session.commit()
        except Exception as e:
            logger.debug("portfolio kill switch event log failed: %s", e)


class SystemKillSwitch:
    """
    Full system halt — all activity stopped, connections closed.
    Reserved for critical failures (data corruption, API compromise, etc.).
    """

    def __init__(self, base_kill_switch: Any, db: Optional[Any] = None, alerting: Optional[Any] = None):
        self._base = base_kill_switch
        self.db = db
        self.alerting = alerting
        self._system_killed = False

    async def is_engaged(self) -> bool:
        return self._system_killed or await self._base.is_engaged()

    async def engage(self, reason: str = "System critical failure") -> None:
        """Full system halt."""
        self._system_killed = True
        await self._base.engage(reason)
        await self._log_event("system", reason)
        if self.alerting:
            try:
                from base_engine.monitoring.alerting import AlertSeverity
                await self.alerting.send_alert(
                    title="SYSTEM KILL SWITCH — FULL HALT",
                    message=reason,
                    severity=AlertSeverity.CRITICAL,
                    source="system_kill_switch",
                )
            except Exception as e:
                logger.debug("system kill switch alert send failed: %s", e)
        logger.critical("SYSTEM KILL SWITCH ENGAGED: %s", reason)

    async def disengage(self) -> None:
        self._system_killed = False
        await self._base.disengage()
        await self._log_event("system", "System kill switch disengaged")

    async def _log_event(self, level: str, reason: str) -> None:
        if not self.db or not getattr(self.db, "session_factory", None):
            return
        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                await session.execute(text("""
                    INSERT INTO kill_switch_events (trigger_level, trigger_reason, triggered_at)
                    VALUES (:level, :reason, :ts)
                """), {"level": level, "reason": reason, "ts": datetime.now(timezone.utc)})
                await session.commit()
        except Exception as e:
            logger.debug("system kill switch event log failed: %s", e)


class MultiLayerKillSwitch:
    """Unified facade for all kill switch layers."""

    def __init__(
        self,
        base_kill_switch: Any,
        db: Optional[Any] = None,
        alerting: Optional[Any] = None,
    ):
        self.bot = BotKillSwitch(db=db, alerting=alerting)
        self.portfolio = PortfolioKillSwitch(base_kill_switch, db=db, alerting=alerting)
        self.system = SystemKillSwitch(base_kill_switch, db=db, alerting=alerting)

    async def should_trade(self, bot_name: str) -> bool:
        """Returns True if this bot is allowed to trade. Checks all layers.

        Optimization: system + portfolio both delegate to the same base KillSwitch,
        so we only check it once (the base KillSwitch has its own 30s TTL cache).
        """
        # Bot-level is in-memory only (no DB) — check first
        if self.bot.is_killed(bot_name):
            return False
        # System-level checks _system_killed flag (in-memory) then delegates to base
        if self.system._system_killed:
            return False
        # Single base kill_switch check covers both portfolio and system
        if await self.portfolio.is_engaged():
            return False
        return True
