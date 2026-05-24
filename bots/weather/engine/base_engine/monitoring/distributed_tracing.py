"""
Distributed Tracing - Trace requests across services.

Features:
- Request tracing across services
- Performance bottleneck identification
- Dependency mapping
- Error propagation tracking
"""
import time
import uuid
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from structlog import get_logger

logger = get_logger()


class TraceSpan:
    """Represents a single span in a trace."""
    
    def __init__(
        self,
        span_id: str,
        trace_id: str,
        name: str,
        service: str,
        operation: str,
        start_time: Optional[datetime] = None
    ):
        self.span_id = span_id
        self.trace_id = trace_id
        self.name = name
        self.service = service
        self.operation = operation
        self.start_time = start_time or datetime.now(timezone.utc)
        self.end_time: Optional[datetime] = None
        self.duration_ms: Optional[float] = None
        self.tags: Dict[str, Any] = {}
        self.logs: List[Dict[str, Any]] = []
        self.child_spans: List[TraceSpan] = []
        self.error: Optional[str] = None
    
    def finish(self, error: Optional[str] = None):
        """Finish the span."""
        self.end_time = datetime.now(timezone.utc)
        self.duration_ms = (self.end_time - self.start_time).total_seconds() * 1000
        if error:
            self.error = error
            self.tags["error"] = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "span_id": self.span_id,
            "trace_id": self.trace_id,
            "name": self.name,
            "service": self.service,
            "operation": self.operation,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "duration_ms": self.duration_ms,
            "tags": self.tags,
            "logs": self.logs,
            "error": self.error,
            "child_spans": [child.to_dict() for child in self.child_spans]
        }


class DistributedTracer:
    """
    Distributed tracing system.
    
    Tracks requests across services to identify bottlenecks and dependencies.
    """
    
    def __init__(self):
        self.traces: Dict[str, List[TraceSpan]] = {}
        self.max_traces = 10000
        self.active_spans: Dict[str, TraceSpan] = {}
    
    def start_trace(
        self,
        name: str,
        service: str,
        operation: str,
        trace_id: Optional[str] = None
    ) -> TraceSpan:
        """
        Start a new trace.
        
        Args:
            name: Trace name
            service: Service name
            operation: Operation name
            trace_id: Optional trace ID (generated if not provided)
        
        Returns:
            Root span
        """
        if trace_id is None:
            trace_id = str(uuid.uuid4())
        
        span = TraceSpan(
            span_id=str(uuid.uuid4()),
            trace_id=trace_id,
            name=name,
            service=service,
            operation=operation
        )
        
        self.active_spans[span.span_id] = span
        
        if trace_id not in self.traces:
            self.traces[trace_id] = []
        self.traces[trace_id].append(span)
        
        # Trim traces if needed
        if len(self.traces) > self.max_traces:
            oldest_trace = min(self.traces.keys())
            del self.traces[oldest_trace]
        
        return span
    
    def start_span(
        self,
        name: str,
        service: str,
        operation: str,
        parent_span_id: Optional[str] = None,
        trace_id: Optional[str] = None
    ) -> TraceSpan:
        """
        Start a child span.
        
        Args:
            name: Span name
            service: Service name
            operation: Operation name
            parent_span_id: Parent span ID
            trace_id: Trace ID (required if no parent)
        
        Returns:
            Child span
        """
        if parent_span_id and parent_span_id in self.active_spans:
            parent = self.active_spans[parent_span_id]
            trace_id = parent.trace_id
        elif not trace_id:
            raise ValueError("trace_id required if no parent span")
        
        span = TraceSpan(
            span_id=str(uuid.uuid4()),
            trace_id=trace_id,
            name=name,
            service=service,
            operation=operation
        )
        
        if parent_span_id and parent_span_id in self.active_spans:
            self.active_spans[parent_span_id].child_spans.append(span)
        
        self.active_spans[span.span_id] = span
        
        if trace_id not in self.traces:
            self.traces[trace_id] = []
        self.traces[trace_id].append(span)
        
        return span
    
    def finish_span(self, span_id: str, error: Optional[str] = None):
        """Finish a span."""
        if span_id in self.active_spans:
            span = self.active_spans[span_id]
            span.finish(error=error)
            del self.active_spans[span_id]
    
    @asynccontextmanager
    async def trace(
        self,
        name: str,
        service: str,
        operation: str,
        trace_id: Optional[str] = None,
        parent_span_id: Optional[str] = None
    ):
        """
        Context manager for tracing operations.
        
        Usage:
            async with tracer.trace("get_markets", "api", "fetch"):
                # Your code here
                pass
        """
        if parent_span_id:
            span = self.start_span(name, service, operation, parent_span_id, trace_id)
        else:
            span = self.start_trace(name, service, operation, trace_id)
        
        error = None
        try:
            yield span
        except Exception as e:
            error = str(e)
            raise
        finally:
            self.finish_span(span.span_id, error)
    
    def get_trace(self, trace_id: str) -> Optional[List[TraceSpan]]:
        """Get all spans for a trace."""
        return self.traces.get(trace_id)
    
    def get_trace_summary(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """Get summary of a trace."""
        spans = self.get_trace(trace_id)
        if not spans:
            return None
        
        root_spans = [s for s in spans if not any(s.span_id in p.child_spans for p in spans)]
        
        total_duration = sum(s.duration_ms or 0 for s in spans if s.duration_ms)
        has_errors = any(s.error for s in spans)
        
        return {
            "trace_id": trace_id,
            "span_count": len(spans),
            "service_count": len(set(s.service for s in spans)),
            "total_duration_ms": total_duration,
            "has_errors": has_errors,
            "root_spans": [s.to_dict() for s in root_spans],
            "all_spans": [s.to_dict() for s in spans]
        }
    
    def find_bottlenecks(self, trace_id: str) -> List[Dict[str, Any]]:
        """Find performance bottlenecks in a trace."""
        spans = self.get_trace(trace_id)
        if not spans:
            return []
        
        # Find spans with longest duration
        bottlenecks = []
        for span in spans:
            if span.duration_ms and span.duration_ms > 1000:  # > 1 second
                bottlenecks.append({
                    "span_id": span.span_id,
                    "name": span.name,
                    "service": span.service,
                    "operation": span.operation,
                    "duration_ms": span.duration_ms,
                    "severity": "high" if span.duration_ms > 5000 else "medium"
                })
        
        # Sort by duration
        bottlenecks.sort(key=lambda x: x["duration_ms"], reverse=True)
        
        return bottlenecks
    
    def get_service_dependencies(self, trace_id: str) -> Dict[str, List[str]]:
        """Get service dependency graph from a trace."""
        spans = self.get_trace(trace_id)
        if not spans:
            return {}
        
        dependencies = {}
        for span in spans:
            if span.service not in dependencies:
                dependencies[span.service] = []
            
            for child in span.child_spans:
                if child.service != span.service and child.service not in dependencies[span.service]:
                    dependencies[span.service].append(child.service)
        
        return dependencies


# Global tracer instance
_global_tracer: Optional[DistributedTracer] = None


def get_tracer() -> DistributedTracer:
    """Get global tracer instance."""
    global _global_tracer
    if _global_tracer is None:
        _global_tracer = DistributedTracer()
    return _global_tracer
