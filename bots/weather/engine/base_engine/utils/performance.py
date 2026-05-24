"""
Performance metrics collection utilities.
"""
import time
import math
from typing import Dict, Optional, Callable, Any
from functools import wraps
from structlog import get_logger

logger = get_logger()


class PerformanceMetrics:
    """Collect and track performance metrics for operations."""
    
    def __init__(self):
        self.metrics: Dict[str, Dict] = {}
    
    def record(self, operation_name: str, duration: float, success: bool = True, **kwargs):
        """
        Record a performance metric.
        
        Args:
            operation_name: Name of the operation
            duration: Duration in seconds (must be >= 0)
            success: Whether operation succeeded
            **kwargs: Additional metadata
        
        Raises:
            ValueError: If duration is negative or invalid
        """
        if not isinstance(duration, (int, float)) or math.isnan(duration) or math.isinf(duration):
            logger.warning(f"Invalid duration {duration} for {operation_name}, skipping")
            return
        
        if duration < 0:
            logger.warning(f"Negative duration {duration} for {operation_name}, using 0.0")
            duration = 0.0
        if operation_name not in self.metrics:
            self.metrics[operation_name] = {
                "count": 0,
                "total_duration": 0.0,
                "success_count": 0,
                "failure_count": 0,
                "min_duration": float('inf'),
                "max_duration": 0.0
            }
        
        metric = self.metrics[operation_name]
        metric["count"] += 1
        metric["total_duration"] += duration
        metric["min_duration"] = min(metric["min_duration"], duration)
        metric["max_duration"] = max(metric["max_duration"], duration)
        
        if success:
            metric["success_count"] += 1
        else:
            metric["failure_count"] += 1
        
        metric["avg_duration"] = metric["total_duration"] / metric["count"]
        metric["success_rate"] = metric["success_count"] / metric["count"] if metric["count"] > 0 else 0.0
        
        logger.debug(
            f"Performance metric recorded: {operation_name}",
            duration=duration,
            success=success,
            **kwargs
        )
    
    def get_stats(self, operation_name: str) -> Optional[Dict]:
        """Get statistics for an operation."""
        return self.metrics.get(operation_name)
    
    def get_all_stats(self) -> Dict:
        """Get all performance statistics."""
        return self.metrics.copy()


_global_metrics = PerformanceMetrics()


def track_performance(operation_name: Optional[str] = None):
    """
    Decorator to track performance of async functions.
    
    Args:
        operation_name: Optional name for operation (defaults to function name)
    
    Usage:
        @track_performance("database_query")
        async def my_function():
            ...
    """
    def decorator(func: Callable) -> Callable:
        name = operation_name or func.__name__
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            success = True
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                raise
            finally:
                duration = time.time() - start_time
                _global_metrics.record(name, duration, success)
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            success = True
            try:
                result = func(*args, **kwargs)
                return result
            except Exception as e:
                success = False
                raise
            finally:
                duration = time.time() - start_time
                _global_metrics.record(name, duration, success)
        
        if hasattr(func, '__code__') and func.__code__.co_flags & 0x80:
            return async_wrapper
        else:
            return sync_wrapper
    
    return decorator


def get_performance_stats() -> Dict:
    """Get all performance statistics."""
    return _global_metrics.get_all_stats()
