"""
Tests for Session 43 latency instrumentation.

Covers:
- _LatencyTracker mark/report
- WebSocket signal latency propagation
- Order latency unified logging (paper = live)
- Prometheus histogram creation
- Paper trade latency_ms persistence
"""
import time
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


# ── _LatencyTracker tests ──


def test_latency_tracker_mark_and_report():
    """mark() 3 stages, verify report dict has correct keys and positive values."""
    from bots.base_bot import _LatencyTracker
    tracker = _LatencyTracker()
    tracker.mark("start")
    # Small sleep to ensure measurable delta
    time.sleep(0.002)
    tracker.mark("middle")
    time.sleep(0.002)
    tracker.mark("end")
    report = tracker.report()
    assert "start>middle" in report
    assert "middle>end" in report
    assert report["start>middle"] > 0
    assert report["middle>end"] > 0


def test_latency_tracker_single_mark_returns_empty():
    """Single mark produces empty report (need at least 2 marks for deltas)."""
    from bots.base_bot import _LatencyTracker
    tracker = _LatencyTracker()
    tracker.mark("only")
    assert tracker.report() == {}


def test_latency_tracker_no_marks_returns_empty():
    """No marks at all produces empty report."""
    from bots.base_bot import _LatencyTracker
    tracker = _LatencyTracker()
    assert tracker.report() == {}


# ── WebSocket signal latency tests ──


@pytest.mark.asyncio
async def test_ws_recv_timestamp_in_dispatch():
    """Verify _ws_recv_t propagates through _dispatch_message to _handle_price_change."""
    from base_engine.data.websocket_manager import WebSocketManager

    ws_mgr = WebSocketManager.__new__(WebSocketManager)
    ws_mgr.handlers = {}
    ws_mgr.event_bus = None
    ws_mgr.cache = None
    ws_mgr._condition_to_market = {}
    ws_mgr._reverse_map = {}

    # Track calls to _handle_price_change
    calls = []

    async def mock_handle(data, _ws_recv_t=0.0):
        calls.append(_ws_recv_t)

    ws_mgr._handle_price_change = mock_handle

    test_t = time.monotonic()
    await ws_mgr._dispatch_message(
        {"event_type": "price_change", "token_id": "abc", "price": 0.5},
        _ws_recv_t=test_t,
    )
    assert len(calls) == 1
    assert calls[0] == test_t


@pytest.mark.asyncio
async def test_on_price_update_computes_signal_latency():
    """on_price_update() with _ws_recv_t logs signal latency."""
    from bots.base_bot import BaseBot

    # Create a minimal BaseBot subclass
    class TestBot(BaseBot):
        async def scan_and_trade(self):
            pass
        async def analyze_opportunity(self, market):
            pass

    bot = TestBot.__new__(TestBot)
    bot.bot_name = "TestBot"
    bot.running = True
    bot._ws_price_cache = {}

    # Set _ws_recv_t to a recent time
    _t = time.monotonic() - 0.001  # 1ms ago

    with patch("bots.base_bot.logger") as mock_logger:
        with patch("bots.base_bot.settings") as mock_settings:
            mock_settings.WS_SIGNAL_LATENCY_ALERT_MS = 0  # Alert on any latency
            await bot.on_price_update({
                "market_id": "m1",
                "token_id": "t1",
                "price": 0.65,
                "_ws_recv_t": _t,
            })

    # Price should be cached
    assert bot._ws_price_cache.get("m1") == 0.65
    # Warning should have been logged (signal_ms > 0 > threshold of 0)
    mock_logger.warning.assert_called()
    call_args = mock_logger.warning.call_args
    assert "WS signal latency" in str(call_args)


# ── Order latency unified logging tests ──


def test_order_gateway_paper_log_message_is_unified():
    """Verify the paper branch uses 'Order latency' (NOT 'Order latency (paper)')."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent.parent / "base_engine" / "execution" / "order_gateway.py"
    text = src.read_text()
    # The old "(paper)" suffix should NOT exist
    assert '"Order latency (paper)"' not in text, \
        "Paper branch should use unified 'Order latency' log event, not 'Order latency (paper)'"
    # Both branches should have the same log event
    assert text.count('"Order latency"') >= 2, \
        "Both paper and live branches should log 'Order latency'"
    # Both branches should log breakdown
    assert text.count('"Order latency breakdown"') >= 2, \
        "Both paper and live branches should log 'Order latency breakdown'"


def test_order_gateway_paper_checks_alert_threshold():
    """Verify the paper branch checks ORDER_LATENCY_ALERT_MS threshold."""
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent.parent / "base_engine" / "execution" / "order_gateway.py"
    text = src.read_text()
    # Count occurrences of threshold check — must appear in both branches
    assert text.count('"Order latency exceeded threshold"') >= 2, \
        "Alert threshold check must exist in both paper and live branches"


# ── Prometheus histogram tests ──


def test_prometheus_histograms_exist():
    """WS_SIGNAL_LATENCY and ORDER_PIPELINE_LATENCY should be importable."""
    from base_engine.monitoring.metrics_collector import WS_SIGNAL_LATENCY, ORDER_PIPELINE_LATENCY
    # They should have an observe() method (real Histogram or stub)
    assert hasattr(WS_SIGNAL_LATENCY, "observe") or hasattr(WS_SIGNAL_LATENCY, "labels")
    assert hasattr(ORDER_PIPELINE_LATENCY, "observe") or hasattr(ORDER_PIPELINE_LATENCY, "labels")


def test_record_trade_exists():
    """MetricsCollector.record_trade() should accept bot_name, side, success, latency."""
    from base_engine.monitoring.metrics_collector import MetricsCollector
    mc = MetricsCollector()
    # Should not raise
    mc.record_trade("TestBot", "YES", True, 0.042)


# ── Paper trade latency_ms persistence test ──


def test_paper_trade_record_has_latency_ms():
    """PaperTradeRecord ORM model should have latency_ms column."""
    from base_engine.data.database import PaperTradeRecord
    assert hasattr(PaperTradeRecord, "latency_ms"), \
        "PaperTradeRecord must have latency_ms column (S43)"


# ── BaseBot.mark_latency tests ──


def test_mark_latency_with_tracker():
    """mark_latency() calls tracker when available."""
    from bots.base_bot import BaseBot, _LatencyTracker

    class TestBot(BaseBot):
        async def scan_and_trade(self):
            pass
        async def analyze_opportunity(self, market):
            pass

    bot = TestBot.__new__(TestBot)
    bot._latency_tracker = _LatencyTracker()
    bot.mark_latency("test_stage")
    assert len(bot._latency_tracker._marks) == 1
    assert bot._latency_tracker._marks[0][0] == "test_stage"


def test_mark_latency_without_tracker():
    """mark_latency() is safe when tracker is None."""
    from bots.base_bot import BaseBot

    class TestBot(BaseBot):
        async def scan_and_trade(self):
            pass
        async def analyze_opportunity(self, market):
            pass

    bot = TestBot.__new__(TestBot)
    bot._latency_tracker = None
    # Should not raise
    bot.mark_latency("test_stage")


def test_mark_latency_no_attr():
    """mark_latency() is safe when _latency_tracker attr doesn't exist."""
    from bots.base_bot import BaseBot

    class TestBot(BaseBot):
        async def scan_and_trade(self):
            pass
        async def analyze_opportunity(self, market):
            pass

    bot = TestBot.__new__(TestBot)
    # Don't set _latency_tracker at all
    bot.mark_latency("test_stage")


# ── Settings test ──


def test_ws_signal_latency_alert_ms_setting():
    """WS_SIGNAL_LATENCY_ALERT_MS should be defined in settings."""
    from config.settings import settings
    assert hasattr(settings, "WS_SIGNAL_LATENCY_ALERT_MS")
    assert isinstance(settings.WS_SIGNAL_LATENCY_ALERT_MS, int)
    assert settings.WS_SIGNAL_LATENCY_ALERT_MS == 500  # Session 46: raised from 50 (spams on shared VPS)
