"""
S152: Shared structlog configuration for all entry points (main.py, ingestion_main.py).

Extracted from main.py so both trading and ingestion services get identical logging:
structured JSON, tee to file, consistent format in journalctl.
"""
import logging
import os
import sys

import structlog

from config.settings import settings


# ---------------------------------------------------------------------------
# Tee logger: writes to both stdout (journald) AND a log file
# ---------------------------------------------------------------------------

_LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "paper_trading.log")
_log_fh = None
try:
    _log_fh = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)  # line-buffered
except Exception:
    pass


class _TeeLogger:
    """Logger that writes to both stdout (flush=True) and the log file."""
    def msg(self, message: str, **kw) -> None:
        print(message, flush=True)
        if _log_fh:
            try:
                _log_fh.write(message + "\n")
            except Exception:
                pass
    log = debug = info = warning = warn = error = critical = fatal = exception = msg


class _TeeLoggerFactory:
    def __call__(self, *args, **kwargs):
        return _TeeLogger()


# ---------------------------------------------------------------------------
# Configure structlog (call once at process startup, before any get_logger())
# ---------------------------------------------------------------------------

def configure_logging() -> None:
    """Configure structlog with tee logger. Must be called before any get_logger()."""
    # Ensure stdout is line-buffered when redirected to a file
    if not sys.stdout.isatty():
        try:
            sys.stdout.reconfigure(line_buffering=True)
        except Exception:
            pass

    _log_level = getattr(settings, "LOG_LEVEL", "INFO").upper()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, _log_level, logging.INFO)),
        logger_factory=_TeeLoggerFactory(),
        cache_logger_on_first_use=True,
    )
