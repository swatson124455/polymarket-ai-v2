"""
Error Tracking System
====================
Provides error tracking infrastructure ready for Sentry integration.
Works standalone with structured logging until Sentry DSN is configured.
"""
from typing import Optional, Dict, Any
from structlog import get_logger
import os

logger = get_logger()


class ErrorTracker:
    """
    Error tracking system.
    Ready for Sentry integration but works with structured logging.
    """
    
    def __init__(self, sentry_dsn: Optional[str] = None):
        self.sentry_dsn = sentry_dsn or os.getenv("SENTRY_DSN")
        self.sentry_initialized = False
        
        # Initialize Sentry if DSN is provided
        if self.sentry_dsn:
            self._init_sentry()
    
    def _init_sentry(self) -> None:
        """Initialize Sentry SDK if available."""
        try:
            import sentry_sdk
            from sentry_sdk.integrations.logging import LoggingIntegration
            
            sentry_sdk.init(
                dsn=self.sentry_dsn,
                integrations=[
                    LoggingIntegration(level=None, event_level=None)
                ],
                traces_sample_rate=0.1,  # 10% of transactions
                environment=os.getenv("ENVIRONMENT", "development")
            )
            self.sentry_initialized = True
            logger.info("Sentry error tracking initialized")
        except ImportError:
            logger.warning("Sentry SDK not installed. Install with: pip install sentry-sdk")
        except Exception as e:
            logger.warning(f"Failed to initialize Sentry: {e}")
    
    def capture_exception(
        self,
        exception: Exception,
        context: Optional[Dict[str, Any]] = None,
        level: str = "error"
    ) -> None:
        """
        Capture an exception for tracking.
        
        Args:
            exception: The exception to capture
            context: Additional context information
            level: Log level (error, warning, info)
        """
        # Log with structured logging (use named method instead of log() for structlog compatibility)
        # Note: exc_info is intentionally omitted — ConsoleRenderer does not pair with format_exc_info,
        # and the exception type + message are already captured as structured fields below.
        _log_fn = getattr(logger, level.lower(), logger.error)
        _log_fn(
            f"Exception captured: {type(exception).__name__}",
            exception=str(exception),
            exception_type=type(exception).__name__,
            context=context or {},
        )
        
        # Send to Sentry if initialized
        if self.sentry_initialized:
            try:
                import sentry_sdk
                with sentry_sdk.push_scope() as scope:
                    if context:
                        for key, value in context.items():
                            scope.set_context(key, {"value": str(value)})
                    sentry_sdk.capture_exception(exception)
            except Exception as e:
                logger.warning(f"Failed to send exception to Sentry: {e}")
    
    def capture_message(
        self,
        message: str,
        level: str = "info",
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Capture a message for tracking.
        
        Args:
            message: Message to capture
            level: Log level (error, warning, info, debug)
            context: Additional context information
        """
        # Log with structured logging (use named method instead of log() for structlog compatibility)
        _log_fn = getattr(logger, level.lower(), logger.info)
        _log_fn(
            message,
            context=context or {}
        )
        
        # Send to Sentry if initialized
        if self.sentry_initialized:
            try:
                import sentry_sdk
                with sentry_sdk.push_scope() as scope:
                    if context:
                        for key, value in context.items():
                            scope.set_context(key, {"value": str(value)})
                    sentry_sdk.capture_message(message, level=level)
            except Exception as e:
                logger.warning(f"Failed to send message to Sentry: {e}")


# Global error tracker instance
_error_tracker: Optional[ErrorTracker] = None


def get_error_tracker() -> ErrorTracker:
    """Get or create the global error tracker instance."""
    global _error_tracker
    if _error_tracker is None:
        _error_tracker = ErrorTracker()
    return _error_tracker


def init_error_tracking(sentry_dsn: Optional[str] = None) -> ErrorTracker:
    """Initialize error tracking with optional Sentry DSN."""
    global _error_tracker
    _error_tracker = ErrorTracker(sentry_dsn=sentry_dsn)
    return _error_tracker
