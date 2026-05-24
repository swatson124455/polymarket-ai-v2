"""
Performance Metrics Dashboard - Centralized metrics collection and visualization.

Aggregates metrics from:
- API response times
- Database query performance
- Cache hit rates
- Trade execution latency
- Strategy performance
- System resource usage
"""
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from collections import defaultdict
from structlog import get_logger
from bots.weather.engine.base_engine.utils.performance import PerformanceMetrics, get_performance_stats
from bots.weather.engine.base_engine.monitoring.health_monitor import HealthMonitor

logger = get_logger()


class MetricsDashboard:
    """
    Comprehensive metrics dashboard.
    
    Collects and aggregates metrics from all system components.
    """
    
    def __init__(
        self,
        health_monitor: Optional[HealthMonitor] = None,
        performance_metrics: Optional[PerformanceMetrics] = None
    ):
        self.health_monitor = health_monitor
        self.performance_metrics = performance_metrics
        
        # Metric storage
        self.metric_history: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self.max_history_per_metric = 10000
        
        # Aggregated metrics
        self.aggregated_metrics: Dict[str, Any] = {}
    
    def record_metric(
        self,
        metric_name: str,
        value: float,
        tags: Optional[Dict[str, str]] = None,
        timestamp: Optional[datetime] = None
    ):
        """
        Record a custom metric.
        
        Args:
            metric_name: Name of the metric
            value: Metric value
            tags: Optional tags for filtering
            timestamp: Optional timestamp (defaults to now)
        """
        if timestamp is None:
            timestamp = datetime.now(timezone.utc)
        
        metric_entry = {
            "value": value,
            "timestamp": timestamp.isoformat(),
            "tags": tags or {}
        }
        
        self.metric_history[metric_name].append(metric_entry)
        
        # Trim history
        if len(self.metric_history[metric_name]) > self.max_history_per_metric:
            self.metric_history[metric_name].pop(0)
    
    async def get_dashboard_data(self) -> Dict[str, Any]:
        """
        Get comprehensive dashboard data.
        
        Returns:
            Dictionary with all metrics for dashboard display
        """
        dashboard = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "system_health": await self._get_system_health(),
            "performance_metrics": self._get_performance_metrics(),
            "api_metrics": self._get_api_metrics(),
            "database_metrics": self._get_database_metrics(),
            "cache_metrics": self._get_cache_metrics(),
            "trade_metrics": self._get_trade_metrics(),
            "strategy_metrics": self._get_strategy_metrics(),
            "system_resources": self._get_system_resources()
        }
        
        return dashboard
    
    async def _get_system_health(self) -> Dict[str, Any]:
        """Get system health status."""
        if not self.health_monitor:
            return {"status": "unknown", "message": "Health monitor not configured"}
        
        latest = self.health_monitor.get_latest_health()
        if not latest:
            return {"status": "unknown", "message": "No health data available"}
        
        return {
            "overall": latest.get("overall", "unknown"),
            "components": latest.get("components", {}),
            "last_check": latest.get("timestamp")
        }
    
    def _get_performance_metrics(self) -> Dict[str, Any]:
        """Get performance metrics from PerformanceMetrics."""
        if not self.performance_metrics:
            stats = get_performance_stats()
        else:
            stats = self.performance_metrics.get_all_stats()
        
        # Aggregate by category
        api_ops = {}
        db_ops = {}
        other_ops = {}
        
        for op_name, op_stats in stats.items():
            if "api" in op_name.lower() or "http" in op_name.lower():
                api_ops[op_name] = op_stats
            elif "database" in op_name.lower() or "db" in op_name.lower() or "query" in op_name.lower():
                db_ops[op_name] = op_stats
            else:
                other_ops[op_name] = op_stats
        
        return {
            "api_operations": api_ops,
            "database_operations": db_ops,
            "other_operations": other_ops,
            "total_operations": len(stats)
        }
    
    def _get_api_metrics(self) -> Dict[str, Any]:
        """Get API-specific metrics."""
        api_history = self.metric_history.get("api_response_time", [])
        
        if not api_history:
            return {"status": "no_data"}
        
        recent = api_history[-100:]  # Last 100 requests
        values = [m["value"] for m in recent]
        
        return {
            "avg_response_time_ms": sum(values) / len(values) if values else 0,
            "min_response_time_ms": min(values) if values else 0,
            "max_response_time_ms": max(values) if values else 0,
            "total_requests": len(api_history),
            "recent_requests": len(recent)
        }
    
    def _get_database_metrics(self) -> Dict[str, Any]:
        """Get database-specific metrics."""
        db_history = self.metric_history.get("database_query_time", [])
        
        if not db_history:
            return {"status": "no_data"}
        
        recent = db_history[-100:]
        values = [m["value"] for m in recent]
        
        return {
            "avg_query_time_ms": sum(values) / len(values) if values else 0,
            "min_query_time_ms": min(values) if values else 0,
            "max_query_time_ms": max(values) if values else 0,
            "total_queries": len(db_history),
            "recent_queries": len(recent)
        }
    
    def _get_cache_metrics(self) -> Dict[str, Any]:
        """Get cache-specific metrics."""
        hit_history = self.metric_history.get("cache_hit", [])
        miss_history = self.metric_history.get("cache_miss", [])
        
        total_requests = len(hit_history) + len(miss_history)
        hits = len(hit_history)
        
        hit_rate = (hits / total_requests * 100) if total_requests > 0 else 0
        
        return {
            "hit_rate_percent": round(hit_rate, 2),
            "total_hits": hits,
            "total_misses": len(miss_history),
            "total_requests": total_requests
        }
    
    def _get_trade_metrics(self) -> Dict[str, Any]:
        """Get trade execution metrics."""
        execution_history = self.metric_history.get("trade_execution_time", [])
        
        if not execution_history:
            return {"status": "no_data"}
        
        recent = execution_history[-100:]
        values = [m["value"] for m in recent]
        
        return {
            "avg_execution_time_ms": sum(values) / len(values) if values else 0,
            "min_execution_time_ms": min(values) if values else 0,
            "max_execution_time_ms": max(values) if values else 0,
            "total_trades": len(execution_history),
            "recent_trades": len(recent)
        }
    
    def _get_strategy_metrics(self) -> Dict[str, Any]:
        """Get strategy performance metrics."""
        # Get strategy metrics from history
        strategy_metrics = {}
        
        for metric_name, history in self.metric_history.items():
            if "strategy" in metric_name.lower():
                strategy_name = metric_name.replace("strategy_", "").replace("_", " ").title()
                recent = history[-100:] if history else []
                values = [m["value"] for m in recent]
                
                strategy_metrics[strategy_name] = {
                    "avg_value": sum(values) / len(values) if values else 0,
                    "count": len(history),
                    "recent_count": len(recent)
                }
        
        return strategy_metrics
    
    def _get_system_resources(self) -> Dict[str, Any]:
        """Get system resource usage."""
        try:
            import psutil
        except ImportError:
            return {
                "status": "unavailable",
                "message": "psutil not installed - install with: pip install psutil"
            }
        
        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            return {
                "cpu_percent": round(cpu_percent, 2),
                "memory_percent": round(memory.percent, 2),
                "memory_available_gb": round(memory.available / (1024**3), 2),
                "disk_percent": round(disk.percent, 2),
                "disk_free_gb": round(disk.free / (1024**3), 2)
            }
        except Exception as e:
            logger.warning(f"Failed to get system resources: {str(e)}")
            return {"status": "error", "message": str(e)}
    
    def get_metric_history(
        self,
        metric_name: str,
        since: Optional[datetime] = None,
        limit: int = 1000
    ) -> List[Dict[str, Any]]:
        """
        Get metric history with optional filtering.
        
        Args:
            metric_name: Name of the metric
            since: Only return metrics since this time
            limit: Maximum number of metrics to return
        """
        history = self.metric_history.get(metric_name, [])
        
        if since:
            since_iso = since.isoformat()
            history = [m for m in history if m["timestamp"] >= since_iso]
        
        return history[-limit:]
    
    def get_metric_summary(self, metric_name: str) -> Optional[Dict[str, Any]]:
        """Get summary statistics for a metric."""
        history = self.metric_history.get(metric_name, [])
        
        if not history:
            return None
        
        values = [m["value"] for m in history]
        
        return {
            "count": len(history),
            "min": min(values) if values else 0,
            "max": max(values) if values else 0,
            "avg": sum(values) / len(values) if values else 0,
            "latest": values[-1] if values else None,
            "first_timestamp": history[0]["timestamp"] if history else None,
            "last_timestamp": history[-1]["timestamp"] if history else None
        }
