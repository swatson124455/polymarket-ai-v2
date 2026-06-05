"""
Metrics Collection System
========================
Provides decorators and utilities for tracking performance metrics.
Ready for Prometheus integration but works standalone with logging.
"""
import time
from typing import Dict, Any, Optional, Callable
from functools import wraps
from datetime import datetime, timezone
from structlog import get_logger

logger = get_logger()


class MetricsCollector:
    """
    Collects metrics for operations.
    Can be extended to push to Prometheus, but works standalone.
    """
    
    def __init__(self):
        self.metrics: Dict[str, list] = {}
        self.counters: Dict[str, int] = {}
    
    def record_duration(self, operation: str, duration: float, success: bool = True) -> None:
        """Record operation duration."""
        if operation not in self.metrics:
            self.metrics[operation] = []
        
        self.metrics[operation].append({
            "duration": duration,
            "success": success,
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        
        # Keep only last 1000 entries per operation
        if len(self.metrics[operation]) > 1000:
            self.metrics[operation] = self.metrics[operation][-1000:]
    
    def increment_counter(self, counter_name: str, value: int = 1) -> None:
        """Increment a counter."""
        self.counters[counter_name] = self.counters.get(counter_name, 0) + value
    
    def get_stats(self, operation: str) -> Optional[Dict[str, Any]]:
        """Get statistics for an operation."""
        if operation not in self.metrics or not self.metrics[operation]:
            return None
        
        durations = [m["duration"] for m in self.metrics[operation]]
        successes = [m["success"] for m in self.metrics[operation]]
        
        return {
            "count": len(durations),
            "avg_duration": sum(durations) / len(durations),
            "min_duration": min(durations),
            "max_duration": max(durations),
            "success_rate": sum(successes) / len(successes) if successes else 0.0,
            "total_calls": len(durations)
        }
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all operations."""
        return {
            op: self.get_stats(op)
            for op in self.metrics.keys()
        }
    
    def reset(self) -> None:
        """Reset all metrics."""
        self.metrics.clear()
        self.counters.clear()


# Global metrics collector instance
_metrics_collector = MetricsCollector()


def get_metrics_collector() -> MetricsCollector:
    """Get the global metrics collector instance."""
    return _metrics_collector


def track_metrics(operation_name: Optional[str] = None):
    """
    Decorator to track operation metrics (duration, success/failure).
    
    Usage:
        @track_metrics("ingest_markets")
        async def ingest_all_markets(self):
            ...
    """
    def decorator(func: Callable) -> Callable:
        op_name = operation_name or f"{func.__module__}.{func.__name__}"
        
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            success = False
            try:
                result = await func(*args, **kwargs)
                success = True
                return result
            except Exception as e:
                logger.error(
                    f"Operation {op_name} failed",
                    operation=op_name,
                    error=str(e),
                    exc_info=True
                )
                raise
            finally:
                duration = time.time() - start_time
                _metrics_collector.record_duration(op_name, duration, success)
                _metrics_collector.increment_counter(f"{op_name}.total")
                if success:
                    _metrics_collector.increment_counter(f"{op_name}.success")
                else:
                    _metrics_collector.increment_counter(f"{op_name}.failure")
        
        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            success = False
            try:
                result = func(*args, **kwargs)
                success = True
                return result
            except Exception as e:
                logger.error(
                    f"Operation {op_name} failed",
                    operation=op_name,
                    error=str(e),
                    exc_info=True
                )
                raise
            finally:
                duration = time.time() - start_time
                _metrics_collector.record_duration(op_name, duration, success)
                _metrics_collector.increment_counter(f"{op_name}.total")
                if success:
                    _metrics_collector.increment_counter(f"{op_name}.success")
                else:
                    _metrics_collector.increment_counter(f"{op_name}.failure")
        
        # Return appropriate wrapper based on function type
        import inspect
        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper
    
    return decorator
