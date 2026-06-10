"""
S152: Shared structlog configuration for all entry points (main.py, ingestion_main.py).
S178 2D: WatchedFileHandler replaces raw file handle for logrotate compatibility.

Extracted from main.py so both trading and ingestion services get identical logging:
structured JSON, tee to file, consistent format in journalctl.
"""
import logging
import logging.handlers
import os
import sys
import time
from collections import OrderedDict

import structlog

from config.settings import settings


# ---------------------------------------------------------------------------
# Tee logger: writes to both stdout (journald) AND a log file
# S178 2D: Use WatchedFileHandler so logrotate can rotate the file and the
# handler will detect the inode change and reopen automatically.
# Current logrotate config uses copytruncate (same inode), so the handler
# never needs to reopen — but if we switch to `create` mode later,
# WatchedFileHandler becomes load-bearing.  Both are defensive choices.
# ---------------------------------------------------------------------------

_LOG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "paper_trading.log")
_watched_handler: logging.handlers.WatchedFileHandler | None = None
try:
    _watched_handler = logging.handlers.WatchedFileHandler(_LOG_FILE, encoding="utf-8")
    _watched_handler.setFormatter(logging.Formatter("%(message)s"))
except Exception:
    pass


class _TeeLogger:
    """Logger that writes to both stdout (flush=True) and the log file."""
    def msg(self, message: str, **kw) -> None:
        print(message, flush=True)
        if _watched_handler:
            try:
                _watched_handler.emit(
                    logging.LogRecord("polymarket", logging.INFO, "", 0, message, (), None)
                )
            except Exception:
                pass
    log = debug = info = warning = warn = error = critical = fatal = exception = msg


class _TeeLoggerFactory:
    def __call__(self, *args, **kwargs):
        return _TeeLogger()


# ---------------------------------------------------------------------------
# S177: Dedup processor — suppress identical log lines within a time window
# ---------------------------------------------------------------------------

_DEDUP_WINDOW = 60  # seconds
_DEDUP_MAX_KEYS = 500  # bound memory


class _DedupProcessor:
    """
    Suppress duplicate (event, level) pairs within a sliding window.

    After the window expires, emits a single 'suppressed N duplicates' line.
    Keyed on (event_text, log_level) to avoid cross-level suppression.
    """

    def __init__(self) -> None:
        # key -> [first_seen_time, suppress_count]
        self._seen: OrderedDict = OrderedDict()

    def __call__(self, logger, method_name, event_dict):
        event = event_dict.get("event", "")
        level = event_dict.get("level", "info")
        key = (event, level)
        now = time.monotonic()

        if key in self._seen:
            first_seen, count = self._seen[key]
            if now - first_seen < _DEDUP_WINDOW:
                self._seen[key] = [first_seen, count + 1]
                raise structlog.DropEvent
            else:
                # Window expired — emit suppression notice, start new window
                suppressed = count
                self._seen.pop(key)
                self._seen[key] = [now, 0]
                if suppressed > 0:
                    event_dict["event"] = f"{event} (suppressed {suppressed} duplicates)"
                return event_dict
        else:
            # New key
            self._seen[key] = [now, 0]
            # Evict oldest if over capacity
            while len(self._seen) > _DEDUP_MAX_KEYS:
                self._seen.popitem(last=False)
            return event_dict


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
            _DedupProcessor(),  # S177: suppress repeated identical log lines (60s window)
            # 2026-06-10 (EB scan-wedge root fix): force PLAIN tracebacks. Without an
            # explicit exception_formatter, ConsoleRenderer auto-upgrades to rich's
            # traceback renderer (Panel+Syntax+pygments) when rich is importable.
            # That rendering runs SYNCHRONOUSLY on the event-loop thread at every
            # exc_info log site; on a fat SQLAlchemy/asyncpg exception, pygments
            # tokenization blocked the esports event loop for minutes (py-spy-proven:
            # MainThread pinned in pygments get_tokens_unprocessed across 60s dumps),
            # freezing the scan loop after cycle 1 -> scan_age>900s -> stall-watchdog
            # restart churn. plain_traceback is O(n) stdlib formatting; every
            # non-exception log line is byte-identical to before.
            structlog.dev.ConsoleRenderer(exception_formatter=structlog.dev.plain_traceback),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, _log_level, logging.INFO)),
        logger_factory=_TeeLoggerFactory(),
        cache_logger_on_first_use=True,
    )
