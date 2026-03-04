"""
Dedicated async worker thread for Streamlit dashboard.

Streamlit runs on different threads per rerun. Running asyncio + SQLAlchemy async
on Streamlit's thread causes:
- RuntimeError: no running event loop
- RuntimeError: Leaving task X does not match current task Y (nest_asyncio + Python 3.13)
- error: Cannot switch to a different thread (asyncpg greenlets)

Solution: One persistent thread, one event loop. All async ops run there.
DB, base_engine, schedulers stay in that thread - no cross-thread greenlet switching.
"""
import asyncio
import threading
from typing import Any, Coroutine, Optional

_worker_loop: Optional[asyncio.AbstractEventLoop] = None
_worker_thread: Optional[threading.Thread] = None
_worker_ready = threading.Event()


def _worker_main() -> None:
    """Run event loop forever. Called from worker thread."""
    global _worker_loop
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    _worker_loop = loop
    _worker_ready.set()
    try:
        loop.run_forever()
    finally:
        loop.close()
        _worker_loop = None


def ensure_worker() -> asyncio.AbstractEventLoop:
    """Start worker thread if needed. Return its loop."""
    global _worker_thread
    if _worker_loop is not None and _worker_thread is not None and _worker_thread.is_alive():
        return _worker_loop
    if _worker_thread is not None and not _worker_thread.is_alive():
        _worker_ready.clear()
    _worker_thread = threading.Thread(target=_worker_main, daemon=True)
    _worker_thread.start()
    if not _worker_ready.wait(timeout=10):
        raise RuntimeError("Async worker thread failed to start within 10s")
    return _worker_loop


def run_coro_in_worker(coro: Coroutine[Any, Any, Any], timeout: float = 300) -> Any:
    """
    Run coroutine in the dedicated worker thread. Blocks until done.
    Use for all dashboard async ops (init, trading, ingestion, etc).
    """
    loop = ensure_worker()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)
