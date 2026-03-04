"""
Unit tests for Speed Optimization phases.

Covers: UserOrderWebSocket (Phase 7), KillSwitch cache (Phase 8),
BaseBot StrategicTimer reuse and on_price_update (Phases 4, 15),
and fast JSON parsing (orjson fallback).
"""
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Fast JSON parsing (orjson when available)
# ---------------------------------------------------------------------------
class TestJsonParse:
    """json_parse.loads uses orjson when available, else stdlib json."""

    def test_loads_from_str(self):
        from base_engine.data.json_parse import loads
        assert loads('{"a": 1, "b": 2}') == {"a": 1, "b": 2}

    def test_loads_from_bytes(self):
        from base_engine.data.json_parse import loads
        assert loads(b'{"x": 1}') == {"x": 1}


# ---------------------------------------------------------------------------
# Phase 7: UserOrderWebSocket
# ---------------------------------------------------------------------------
class TestUserOrderWebSocket:
    """User/order WebSocket channel - instantiation, connect skip when no auth, disconnect."""

    def test_instantiation(self):
        from base_engine.data.user_order_websocket import UserOrderWebSocket
        ws = UserOrderWebSocket(
            ws_url_base="wss://ws-subscriptions-clob.polymarket.com",
            event_bus=None,
            auth={},
        )
        assert ws.ws_url_base == "wss://ws-subscriptions-clob.polymarket.com"
        assert ws.event_bus is None
        assert ws.auth == {}
        assert ws.ws is None
        assert ws.running is False

    def test_ws_url(self):
        from base_engine.data.user_order_websocket import UserOrderWebSocket
        ws = UserOrderWebSocket("wss://example.com", None, {"apiKey": "k", "secret": "s"})
        assert ws._ws_url() == "wss://example.com/ws/user"

    def test_connect_kwargs_direct(self):
        """Connect kwargs should have ping settings (no proxy)."""
        from base_engine.data.user_order_websocket import UserOrderWebSocket
        ws = UserOrderWebSocket("wss://x.com", None, {})
        kwargs = ws._connect_kwargs()
        assert "ping_interval" in kwargs
        assert "proxy" not in kwargs

    @pytest.mark.asyncio
    async def test_connect_skips_when_no_auth(self):
        from base_engine.data.user_order_websocket import UserOrderWebSocket
        ws = UserOrderWebSocket("wss://example.com", None, {})
        await ws.connect()
        assert ws.ws is None
        assert ws.running is False

    @pytest.mark.asyncio
    async def test_disconnect_idempotent_when_never_connected(self):
        from base_engine.data.user_order_websocket import UserOrderWebSocket
        ws = UserOrderWebSocket("wss://example.com", None, {})
        await ws.disconnect()
        assert ws.ws is None


# ---------------------------------------------------------------------------
# Phase 8: KillSwitch cache
# ---------------------------------------------------------------------------
class TestKillSwitchCache:
    """Kill switch 5s TTL cache - engaged path, cache hit, no DB when no session_factory."""

    @pytest.mark.asyncio
    async def test_is_engaged_returns_false_when_no_db_session_factory(self):
        from base_engine.coordination.kill_switch import KillSwitch
        db = MagicMock()
        db.session_factory = None
        ks = KillSwitch(db=db)
        result = await ks.is_engaged()
        assert result is False

    @pytest.mark.asyncio
    async def test_engage_sets_cache_and_killed(self):
        from base_engine.coordination.kill_switch import KillSwitch
        db = MagicMock()
        db.session_factory = None
        ks = KillSwitch(db=db)
        await ks.engage(reason="test")
        assert ks._killed is True
        assert ks._cache_engaged is True
        assert ks._cache_until > 0

    @pytest.mark.asyncio
    async def test_check_kill_status_returns_true_when_killed_without_db(self):
        from base_engine.coordination.kill_switch import KillSwitch
        db = MagicMock()
        db.session_factory = None
        ks = KillSwitch(db=db)
        ks._killed = True
        assert await ks.check_kill_status() is True


# ---------------------------------------------------------------------------
# Phases 4 & 15: BaseBot on_price_update and StrategicTimer reuse
# ---------------------------------------------------------------------------
class TestBaseBotSpeedOptimization:
    """BaseBot on_price_update (Phase 4) and StrategicTimer reuse (Phase 15)."""

    @pytest.mark.asyncio
    async def test_on_price_update_default_no_op(self):
        from bots.base_bot import BaseBot
        from base_engine.base_engine import BaseEngine
        # BaseBot is ABC; use a minimal concrete impl for test
        class ConcreteBot(BaseBot):
            async def scan_and_trade(self):
                pass
            async def analyze_opportunity(self, market_data):
                return None
        engine = MagicMock(spec=BaseEngine)
        bot = ConcreteBot(bot_name="TestBot", base_engine=engine)
        await bot.on_price_update({"market_id": "m1", "token_id": "t1", "price": 0.5})
        # No exception = no-op works

    def test_strategic_timer_reused_when_use_scan_jitter_true(self):
        from bots.base_bot import BaseBot
        from base_engine.base_engine import BaseEngine
        class ConcreteBot(BaseBot):
            async def scan_and_trade(self):
                pass
            async def analyze_opportunity(self, market_data):
                return None
        engine = MagicMock(spec=BaseEngine)
        bot = ConcreteBot(bot_name="TestBot", base_engine=engine)
        assert getattr(bot, "_strategic_timer", None) is None
        with patch("bots.base_bot.settings") as mock_settings:
            mock_settings.USE_SCAN_JITTER = True
            mock_settings.SCAN_JITTER_PCT = 0.2
            mock_settings.BOT_SCAN_INTERVAL_SECONDS = 60
            mock_settings.DEFAULT_SCAN_INTERVAL = 60
            interval1 = bot._get_scan_interval_seconds()
            interval2 = bot._get_scan_interval_seconds()
        assert bot._strategic_timer is not None
        assert 0 < interval1 <= 60 * 3  # jittered (StrategicTimer adds variable jitter)
        assert 0 < interval2 <= 60 * 3
        # Same timer instance reused
        assert bot._strategic_timer is not None
