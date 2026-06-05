"""
Metrics Collector - Prometheus metrics for performance tracking.
Tracks latency, throughput, error rates, and custom metrics.

FIX: Streamlit/IDE reruns can re-import this module, causing "Duplicated timeseries"
when metrics are re-registered. We create metrics lazily and reuse existing ones.
"""
try:
    from prometheus_client import Counter, Histogram, Gauge, REGISTRY
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False
    # Stub classes so the module doesn't crash when prometheus_client isn't installed
    class _StubMetric:
        def __init__(self, *a, **kw): pass
        def labels(self, *a, **kw): return self
        def inc(self, *a, **kw): pass
        def dec(self, *a, **kw): pass
        def set(self, *a, **kw): pass
        def observe(self, *a, **kw): pass
    Counter = Histogram = Gauge = _StubMetric
    class _StubRegistry:
        _names_to_collectors = {}
    REGISTRY = _StubRegistry()
from typing import Optional
from structlog import get_logger
import time

logger = get_logger()

if not _PROMETHEUS_AVAILABLE:
    logger.warning("prometheus_client not installed; MetricsCollector will use no-op stubs")

# Lazy metric creation - avoids "Duplicated timeseries" when module is re-imported (Streamlit reruns)
def _get_or_create_metric(metric_type, name, doc, *args, **kwargs):
    """Create metric or return existing from registry if already registered."""
    if not _PROMETHEUS_AVAILABLE:
        return metric_type(name, doc, *args, **kwargs)
    try:
        return metric_type(name, doc, *args, **kwargs)
    except ValueError as e:
        if "Duplicated" in str(e):
            # Metric already registered (e.g. from previous import). Retrieve from registry.
            names = getattr(REGISTRY, "_names_to_collectors", {})
            existing = names.get(name)
            if existing is not None:
                return existing
        raise

# Define metrics (lazy - reuse if already registered)
TRADE_COUNTER = _get_or_create_metric(Counter, "polymarket_trades_total", "Total trades executed", ["bot_name", "side", "success"])
TRADE_LATENCY = _get_or_create_metric(Histogram, "polymarket_trade_latency_seconds", "Trade execution latency", ["bot_name"])
PREDICTION_LATENCY = _get_or_create_metric(Histogram, "polymarket_prediction_latency_seconds", "Prediction latency")
LEARNING_UPDATES = _get_or_create_metric(Counter, "polymarket_learning_updates_total", "Learning pattern updates")
DB_QUERY_LATENCY = _get_or_create_metric(Histogram, "polymarket_db_query_seconds", "Database query latency", ["query_type"])
CACHE_HITS = _get_or_create_metric(Counter, "polymarket_cache_hits_total", "Cache hits", ["cache_type"])
CACHE_MISSES = _get_or_create_metric(Counter, "polymarket_cache_misses_total", "Cache misses", ["cache_type"])
ACTIVE_POSITIONS = _get_or_create_metric(Gauge, "polymarket_active_positions", "Number of active positions", ["bot_name"])
WIN_RATE = _get_or_create_metric(Gauge, "polymarket_win_rate", "Win rate", ["bot_name"])
CONFIDENCE_SCORE = _get_or_create_metric(Histogram, "polymarket_confidence_score", "Prediction confidence scores", ["bot_name"])
WS_SIGNAL_LATENCY = _get_or_create_metric(Histogram, "polymarket_ws_signal_latency_seconds", "WebSocket signal propagation latency (recv to bot handler)", ["bot_name"])
ORDER_PIPELINE_LATENCY = _get_or_create_metric(Histogram, "polymarket_order_pipeline_seconds", "Order pipeline component latency", ["bot_name", "component"])


class MetricsCollector:
    """Collects and reports performance metrics."""
    
    def __init__(self):
        self.enabled = True
        
    def record_trade(self, bot_name: str, side: str, success: bool, latency: float):
        """Record trade execution."""
        try:
            TRADE_COUNTER.labels(bot_name=bot_name, side=side, success=str(success)).inc()
            TRADE_LATENCY.labels(bot_name=bot_name).observe(latency)
        except Exception as e:
            logger.debug(f"Failed to record trade metric: {e}")
    
    def record_prediction(self, latency: float):
        """Record prediction latency."""
        try:
            PREDICTION_LATENCY.observe(latency)
        except Exception as e:
            logger.debug(f"Failed to record prediction metric: {e}")
    
    def record_learning_update(self):
        """Record learning pattern update."""
        try:
            LEARNING_UPDATES.inc()
        except Exception as e:
            logger.debug(f"Failed to record learning metric: {e}")
    
    def record_db_query(self, query_type: str, latency: float):
        """Record database query latency."""
        try:
            DB_QUERY_LATENCY.labels(query_type=query_type).observe(latency)
        except Exception as e:
            logger.debug(f"Failed to record DB metric: {e}")
    
    def record_cache_hit(self, cache_type: str):
        """Record cache hit."""
        try:
            CACHE_HITS.labels(cache_type=cache_type).inc()
        except Exception as e:
            logger.debug(f"Failed to record cache hit: {e}")
    
    def record_cache_miss(self, cache_type: str):
        """Record cache miss."""
        try:
            CACHE_MISSES.labels(cache_type=cache_type).inc()
        except Exception as e:
            logger.debug(f"Failed to record cache miss: {e}")
    
    def set_active_positions(self, bot_name: str, count: int):
        """Set current active positions."""
        try:
            ACTIVE_POSITIONS.labels(bot_name=bot_name).set(count)
        except Exception as e:
            logger.debug(f"Failed to set positions metric: {e}")
    
    def set_win_rate(self, bot_name: str, win_rate: float):
        """Set current win rate."""
        try:
            WIN_RATE.labels(bot_name=bot_name).set(win_rate)
        except Exception as e:
            logger.debug(f"Failed to set win rate: {e}")
    
    def record_confidence(self, bot_name: str, confidence: float):
        """Record confidence score distribution."""
        try:
            CONFIDENCE_SCORE.labels(bot_name=bot_name).observe(confidence)
        except Exception as e:
            logger.debug(f"Failed to record confidence: {e}")


# Global instance
metrics_collector = MetricsCollector()
