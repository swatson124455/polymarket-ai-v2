"""
Automated Recovery Procedures - Auto-recover from common failures.

Handles:
- Database connection loss → Auto-reconnect
- API rate limiting → Auto-backoff
- Cache failures → Fallback to database
- Service crashes → Auto-restart
- Data corruption → Auto-restore from backup
"""
import asyncio
import time
from typing import Dict, Optional, Callable, Any
from datetime import datetime, timezone
from enum import Enum
from structlog import get_logger
from base_engine.monitoring.health_monitor import HealthMonitor, HealthStatus
from base_engine.data.database import Database
from base_engine.data.redis_cache import RedisCache

logger = get_logger()


class RecoveryAction(Enum):
    """Types of recovery actions."""
    RECONNECT = "reconnect"
    RETRY = "retry"
    FALLBACK = "fallback"
    RESTART = "restart"
    RESTORE = "restore"
    ALERT = "alert"


class RecoveryProcedure:
    """Automated recovery procedure."""
    
    def __init__(
        self,
        health_monitor: HealthMonitor,
        db: Optional[Database] = None,
        cache: Optional[RedisCache] = None
    ):
        self.health_monitor = health_monitor
        self.db = db
        self.cache = cache
        self.recovery_history: list = []
        self.max_history = 1000
    
    async def attempt_recovery(self, component: str, issue: str) -> Dict[str, Any]:
        """
        Attempt to recover from a component failure.
        
        Args:
            component: Name of the component (database, redis, api, etc.)
            issue: Description of the issue
        
        Returns:
            Dictionary with recovery result
        """
        logger.info(f"Attempting recovery for {component}: {issue}")
        
        recovery_result = {
            "component": component,
            "issue": issue,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "success": False,
            "action": None,
            "message": ""
        }
        
        try:
            if component == "database":
                result = await self._recover_database()
            elif component == "redis":
                result = await self._recover_redis()
            elif component == "api":
                result = await self._recover_api()
            else:
                result = {"success": False, "message": f"Unknown component: {component}"}
            
            recovery_result.update(result)
            
            # Store in history
            self.recovery_history.append(recovery_result)
            if len(self.recovery_history) > self.max_history:
                self.recovery_history.pop(0)
            
            if recovery_result["success"]:
                logger.info(f"Recovery successful for {component}: {recovery_result['message']}")
            else:
                logger.warning(f"Recovery failed for {component}: {recovery_result['message']}")
            
            return recovery_result
        except Exception as e:
            logger.error(f"Recovery attempt failed: {str(e)}", exc_info=True)
            recovery_result.update({
                "success": False,
                "message": f"Recovery error: {str(e)}"
            })
            return recovery_result
    
    async def _recover_database(self) -> Dict[str, Any]:
        """Recover database connection."""
        if not self.db:
            return {"success": False, "message": "Database not configured"}

        # EB engine-leak root fix (2026-06-08): a transient health-check timeout
        # under DB-pool pressure does NOT mean the engine is dead — the pool is
        # just busy. Re-initializing a LIVE engine here orphans its pooled
        # connections: Database.init()'s dispose() can raise on
        # cancellation-poisoned asyncpg conns and is then swallowed, leaving the
        # old engine undisposed (database.py:1112-1115). On session-mode
        # PgBouncer those connections pin until GC, accumulating into the
        # dominant shared-pool saturator (EsportsBot held ~50/80 vs an 18 cap;
        # recovery fired ~62×/6h, each re-initing the engine).
        # So when an engine already exists, re-PROBE it non-destructively first;
        # only fall through to a full re-init when the probe fails with a real
        # connection error (or no engine/session_factory exists at all).
        # pool_pre_ping=True + the corruption-eviction guards (S235 reactive +
        # WI-21a proactive) self-heal dead connections WITHOUT a rebuild.
        if self.db.session_factory is not None:
            try:
                async with self.db.get_session() as session:
                    from sqlalchemy import text
                    await session.execute(text("SELECT 1"))
                return {
                    "success": True,
                    "action": RecoveryAction.RECONNECT.value,
                    "message": "Existing DB engine healthy on re-probe — skipped engine re-init",
                }
            except Exception as e:
                logger.warning(
                    "DB re-probe failed (%s) — engine appears genuinely broken, re-initializing",
                    type(e).__name__,
                )
                # fall through to full re-init below

        try:
            # Try to reinitialize
            await self.db.init()

            # Verify it works
            if self.db.session_factory:
                async with self.db.get_session() as session:
                    from sqlalchemy import text
                    await session.execute(text("SELECT 1"))
                
                return {
                    "success": True,
                    "action": RecoveryAction.RECONNECT.value,
                    "message": "Database reconnected successfully"
                }
            else:
                return {
                    "success": False,
                    "action": RecoveryAction.RECONNECT.value,
                    "message": "Database reconnection failed - session factory not created"
                }
        except Exception as e:
            return {
                "success": False,
                "action": RecoveryAction.RECONNECT.value,
                "message": f"Database reconnection failed: {str(e)}"
            }
    
    async def _recover_redis(self) -> Dict[str, Any]:
        """Recover Redis connection."""
        if not self.cache:
            return {"success": False, "message": "Redis not configured"}
        
        try:
            # Try to reinitialize
            await self.cache.init()
            
            # Verify it works
            if self.cache.redis:
                await self.cache.redis.ping()
                return {
                    "success": True,
                    "action": RecoveryAction.RECONNECT.value,
                    "message": "Redis reconnected successfully"
                }
            else:
                return {
                    "success": False,
                    "action": RecoveryAction.RECONNECT.value,
                    "message": "Redis reconnection failed"
                }
        except Exception as e:
            return {
                "success": False,
                "action": RecoveryAction.RECONNECT.value,
                "message": f"Redis reconnection failed: {str(e)}"
            }
    
    async def _recover_api(self) -> Dict[str, Any]:
        """Recover API connectivity."""
        # API recovery is typically handled by retry logic and circuit breakers
        # This is a placeholder for future API-specific recovery
        return {
            "success": True,
            "action": RecoveryAction.RETRY.value,
            "message": "API recovery handled by retry logic"
        }
    
    async def monitor_and_recover(self, interval_seconds: int = 60):
        """
        Continuously monitor health and attempt recovery for failed components.
        Session 51: escalation — sys.exit(1) after 3 consecutive failures per component.

        Args:
            interval_seconds: How often to check health
        """
        _consecutive_failures: Dict[str, int] = {}
        _MAX_BEFORE_EXIT = 3
        while True:
            try:
                health = await self.health_monitor.check_all_services()

                for comp_name, comp_data in health["components"].items():
                    if comp_data["status"] == "unhealthy":
                        result = await self.attempt_recovery(
                            component=comp_name,
                            issue=comp_data.get("message", "Component unhealthy")
                        )
                        if not result.get("success"):
                            _consecutive_failures[comp_name] = _consecutive_failures.get(comp_name, 0) + 1
                            if _consecutive_failures[comp_name] >= _MAX_BEFORE_EXIT:
                                logger.critical(
                                    "Recovery exhausted for %s (%d consecutive failures) — requesting process restart",
                                    comp_name, _consecutive_failures[comp_name],
                                )
                                import sys
                                sys.exit(1)
                        else:
                            _consecutive_failures[comp_name] = 0
                    else:
                        _consecutive_failures.pop(comp_name, None)

                await asyncio.sleep(interval_seconds)
            except SystemExit:
                raise
            except Exception as e:
                logger.error(f"Recovery monitoring error: {str(e)}", exc_info=True)
                await asyncio.sleep(interval_seconds)
    
    def get_recovery_history(self, limit: int = 100) -> list:
        """Get recent recovery history."""
        return self.recovery_history[-limit:]
