"""
Tests for dashboard async worker and run_async_safe.

Happy path, edge cases, error scenarios.
"""
import asyncio
import pytest
import threading
from unittest.mock import patch, MagicMock


class TestAsyncWorker:
    """Test the async worker thread."""

    def test_ensure_worker_starts_thread(self):
        """Worker thread starts and provides a loop."""
        import ui.async_worker as aw
        loop = aw.ensure_worker()
        assert loop is not None
        assert aw._worker_thread is not None
        assert aw._worker_thread.is_alive()

    def test_run_coro_in_worker_happy_path(self):
        """Simple coro runs and returns result."""
        from ui.async_worker import run_coro_in_worker

        async def add(a, b):
            return a + b

        result = run_coro_in_worker(add(1, 2))
        assert result == 3

    def test_run_coro_in_worker_raises(self):
        """Exception in coro propagates."""
        from ui.async_worker import run_coro_in_worker

        async def fail():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            run_coro_in_worker(fail())

    def test_run_coro_in_worker_timeout(self):
        """Timeout raises TimeoutError."""
        from ui.async_worker import run_coro_in_worker

        async def slow():
            await asyncio.sleep(10)

        with pytest.raises(TimeoutError):
            run_coro_in_worker(slow(), timeout=0.1)

    def test_run_coro_in_worker_concurrent_calls(self):
        """Multiple coros can run sequentially."""
        from ui.async_worker import run_coro_in_worker

        async def identity(x):
            return x

        results = [run_coro_in_worker(identity(i)) for i in range(5)]
        assert results == [0, 1, 2, 3, 4]


class TestRunAsyncSafe:
    """Test run_async_safe wrapper."""

    def test_run_async_safe_happy_path(self):
        """Coro runs and returns result."""
        from ui.dashboard import run_async_safe

        async def get_value():
            return 42

        result = run_async_safe(get_value())
        assert result == 42

    def test_run_async_safe_skipped_on_context_error(self):
        """Context errors return _SKIPPED."""
        from ui.dashboard import run_async_safe, _SKIPPED

        async def dummy():
            return 1

        with patch("ui.async_worker.run_coro_in_worker", side_effect=RuntimeError("cannot enter context")):
            result = run_async_safe(dummy())
            assert result is _SKIPPED

    def test_run_async_safe_propagates_other_errors(self):
        """Non-context errors propagate."""
        from ui.dashboard import run_async_safe

        async def raise_value_error():
            raise ValueError("other error")

        with pytest.raises(ValueError, match="other error"):
            run_async_safe(raise_value_error())
