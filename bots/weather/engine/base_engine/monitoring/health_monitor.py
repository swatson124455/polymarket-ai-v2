"""
Health Monitoring System - Foundation for all monitoring and alerting.

Provides comprehensive health checks for all system components.
"""
import asyncio
import time
from typing import Dict, Optional, List, Any
from datetime import datetime, timezone
from enum import Enum
from structlog import get_logger
from bots.weather.engine.base_engine.data.database import Database
from bots.weather.engine.base_engine.data.redis_cache import RedisCache
from bots.weather.engine.base_engine.data.polymarket_client import PolymarketClient

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger = get_logger()
    logger.warning("psutil not available - system resource monitoring disabled")

logger = get_logger()


class HealthStatus(Enum):
    """Health status levels."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


class ComponentHealth:
    """Health status for a single component."""
    
    def __init__(
        self,
        name: str,
        status: HealthStatus,
        message: str = "",
        response_time_ms: Optional[float] = None,
        details: Optional[Dict[str, Any]] = None,
        last_check: Optional[datetime] = None
    ):
        self.name = name
        self.status = status
        self.message = message
        self.response_time_ms = response_time_ms
        self.details = details or {}
        self.last_check = last_check or datetime.now(timezone.utc)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "response_time_ms": self.response_time_ms,
            "details": self.details,
            "last_check": self.last_check.isoformat() if self.last_check else None
        }


class HealthMonitor:
    """
    Comprehensive health monitoring system.
    
    Monitors:
    - Database connectivity and performance
    - Redis connectivity and performance
    - API connectivity
    - WebSocket connectivity
    - System resources (CPU, memory, disk)
    """
    
    def __init__(
        self,
        db: Optional[Database] = None,
        cache: Optional[RedisCache] = None,
        client: Optional[PolymarketClient] = None
    ):
        self.db = db
        self.cache = cache
        self.client = client
        self.health_history: List[Dict[str, Any]] = []
        self.max_history_size = 1000
        self.check_timeout_seconds = 5.0
    
    async def check_all_services(self) -> Dict[str, Any]:
        """
        Check health of all services.
        
        Returns:
            Dictionary with overall status and component health
        """
        start_time = time.time()
        components = {}
        
        # Check database
        components["database"] = await self._check_database()
        
        # Check Redis
        components["redis"] = await self._check_redis()
        
        # Check API
        components["api"] = await self._check_api()
        
        # Check system resources
        components["system"] = await self._check_system_resources()
        
        # Determine overall status
        statuses = [comp.status for comp in components.values()]
        if all(s == HealthStatus.HEALTHY for s in statuses):
            overall_status = HealthStatus.HEALTHY
        elif any(s == HealthStatus.UNHEALTHY for s in statuses):
            overall_status = HealthStatus.UNHEALTHY
        else:
            overall_status = HealthStatus.DEGRADED
        
        total_time_ms = (time.time() - start_time) * 1000
        
        result = {
            "overall": overall_status.value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "check_duration_ms": round(total_time_ms, 2),
            "components": {name: comp.to_dict() for name, comp in components.items()}
        }
        
        # Store in history
        self.health_history.append(result)
        if len(self.health_history) > self.max_history_size:
            self.health_history.pop(0)
        
        return result
    
    async def _check_database(self) -> ComponentHealth:
        """Check database health."""
        if not self.db:
            return ComponentHealth(
                name="database",
                status=HealthStatus.UNKNOWN,
                message="Database not initialized"
            )
        
        start_time = time.time()
        
        try:
            # Check if session factory exists
            if self.db.session_factory is None:
                return ComponentHealth(
                    name="database",
                    status=HealthStatus.UNHEALTHY,
                    message="Database session factory not initialized",
                    response_time_ms=(time.time() - start_time) * 1000
                )
            
            # Try to execute a simple query
            async with asyncio.timeout(self.check_timeout_seconds):
                async with self.db.get_session() as session:
                    from sqlalchemy import text
                    result = await session.execute(text("SELECT 1"))
                    result.scalar()
            
            response_time_ms = (time.time() - start_time) * 1000
            
            return ComponentHealth(
                name="database",
                status=HealthStatus.HEALTHY,
                message="Database connection successful",
                response_time_ms=round(response_time_ms, 2)
            )
        except asyncio.TimeoutError:
            return ComponentHealth(
                name="database",
                status=HealthStatus.UNHEALTHY,
                message="Database query timeout",
                response_time_ms=(time.time() - start_time) * 1000
            )
        except Exception as e:
            return ComponentHealth(
                name="database",
                status=HealthStatus.UNHEALTHY,
                message=f"Database check failed: {str(e)}",
                response_time_ms=(time.time() - start_time) * 1000
            )
    
    async def _check_redis(self) -> ComponentHealth:
        """Check Redis health."""
        if not self.cache or not self.cache.redis:
            return ComponentHealth(
                name="redis",
                status=HealthStatus.UNKNOWN,
                message="Redis not initialized"
            )
        
        start_time = time.time()
        
        try:
            async with asyncio.timeout(self.check_timeout_seconds):
                await self.cache.redis.ping()
            
            response_time_ms = (time.time() - start_time) * 1000
            
            return ComponentHealth(
                name="redis",
                status=HealthStatus.HEALTHY,
                message="Redis connection successful",
                response_time_ms=round(response_time_ms, 2)
            )
        except asyncio.TimeoutError:
            return ComponentHealth(
                name="redis",
                status=HealthStatus.UNHEALTHY,
                message="Redis ping timeout",
                response_time_ms=(time.time() - start_time) * 1000
            )
        except Exception as e:
            return ComponentHealth(
                name="redis",
                status=HealthStatus.UNHEALTHY,
                message=f"Redis check failed: {str(e)}",
                response_time_ms=(time.time() - start_time) * 1000
            )
    
    async def _check_api(self) -> ComponentHealth:
        """Check API connectivity."""
        if not self.client:
            return ComponentHealth(
                name="api",
                status=HealthStatus.UNKNOWN,
                message="API client not initialized"
            )
        
        # Check circuit breaker state first — if OPEN, api is effectively unavailable
        if hasattr(self.client, 'circuit_breaker') and self.client.circuit_breaker.state == "OPEN":
            return ComponentHealth(
                name="api",
                status=HealthStatus.DEGRADED,
                message="CLOB circuit breaker OPEN — API unavailable"
            )

        start_time = time.time()

        try:
            # Try to get health status from client if available
            if hasattr(self.client, 'get_polymarket_health'):
                async with asyncio.timeout(self.check_timeout_seconds):
                    health = await self.client.get_polymarket_health()
                    response_time_ms = (time.time() - start_time) * 1000
                    
                    if health.get("status") == "healthy":
                        return ComponentHealth(
                            name="api",
                            status=HealthStatus.HEALTHY,
                            message="API connectivity successful",
                            response_time_ms=round(response_time_ms, 2),
                            details=health
                        )
                    else:
                        return ComponentHealth(
                            name="api",
                            status=HealthStatus.DEGRADED,
                            message=f"API status: {health.get('status', 'unknown')}",
                            response_time_ms=round(response_time_ms, 2),
                            details=health
                        )
            else:
                # Fallback: just check if client exists
                response_time_ms = (time.time() - start_time) * 1000
                return ComponentHealth(
                    name="api",
                    status=HealthStatus.HEALTHY,
                    message="API client initialized",
                    response_time_ms=round(response_time_ms, 2)
                )
        except asyncio.TimeoutError:
            return ComponentHealth(
                name="api",
                status=HealthStatus.UNHEALTHY,
                message="API check timeout",
                response_time_ms=(time.time() - start_time) * 1000
            )
        except Exception as e:
            return ComponentHealth(
                name="api",
                status=HealthStatus.DEGRADED,
                message=f"API check failed: {str(e)}",
                response_time_ms=(time.time() - start_time) * 1000
            )
    
    async def _check_system_resources(self) -> ComponentHealth:
        """Check system resource usage."""
        if not PSUTIL_AVAILABLE:
            return ComponentHealth(
                name="system",
                status=HealthStatus.UNKNOWN,
                message="psutil not available - system monitoring disabled"
            )
        
        try:
            # Use user+system CPU only — excludes hypervisor steal time which is common on
            # shared VPS instances (Lightsail) and would otherwise report 100% CPU constantly.
            try:
                cpu_times = psutil.cpu_times_percent(interval=0.1)
                cpu_percent = cpu_times.user + cpu_times.system
            except Exception:
                cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')

            details = {
                "cpu_percent": round(cpu_percent, 2),
                "memory_percent": round(memory.percent, 2),
                "memory_available_gb": round(memory.available / (1024**3), 2),
                "disk_percent": round(disk.percent, 2),
                "disk_free_gb": round(disk.free / (1024**3), 2)
            }

            # Determine status based on thresholds
            if cpu_percent > 90 or memory.percent > 90 or disk.percent > 90:
                status = HealthStatus.UNHEALTHY
                message = "System resources critically high"
            elif cpu_percent > 75 or memory.percent > 75 or disk.percent > 75:
                status = HealthStatus.DEGRADED
                message = "System resources elevated"
            else:
                status = HealthStatus.HEALTHY
                message = "System resources normal"
            
            return ComponentHealth(
                name="system",
                status=status,
                message=message,
                details=details
            )
        except Exception as e:
            return ComponentHealth(
                name="system",
                status=HealthStatus.UNKNOWN,
                message=f"System check failed: {str(e)}"
            )
    
    def get_health_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent health check history."""
        return self.health_history[-limit:]
    
    def get_latest_health(self) -> Optional[Dict[str, Any]]:
        """Get the most recent health check."""
        if not self.health_history:
            return None
        return self.health_history[-1]
