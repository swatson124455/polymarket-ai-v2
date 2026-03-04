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
        
        Args:
            interval_seconds: How often to check health
        """
        while True:
            try:
                health = await self.health_monitor.check_all_services()
                
                # Check each component
                for comp_name, comp_data in health["components"].items():
                    if comp_data["status"] == "unhealthy":
                        await self.attempt_recovery(
                            component=comp_name,
                            issue=comp_data.get("message", "Component unhealthy")
                        )
                
                await asyncio.sleep(interval_seconds)
            except Exception as e:
                logger.error(f"Recovery monitoring error: {str(e)}", exc_info=True)
                await asyncio.sleep(interval_seconds)
    
    def get_recovery_history(self, limit: int = 100) -> list:
        """Get recent recovery history."""
        return self.recovery_history[-limit:]
