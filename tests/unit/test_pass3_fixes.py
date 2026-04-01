"""
S133 Pass 3 audit fixes — tests for ClobClient config, WS subscription cap, HTTP 425.

NOTE: These tests were written for Pass 3 audit fixes that have NOT yet been
applied to the production code. All tests are marked xfail until the
corresponding code changes are implemented.
"""
import asyncio
import pytest

# Most tests are xfail — Pass 3 fixes not yet applied. Individual tests
# that validate CURRENT behavior (not pending fixes) are unmarked.
from unittest.mock import patch, MagicMock, AsyncMock


# ── Fix 1: ClobClient signature_type + funder ──

@pytest.mark.xfail(reason="Pass 3 Fix 1: signature_type/funder not yet wired into clob_adapter")
class TestClobClientConfig:
    """Verify _get_clob_client passes signature_type and funder to ClobClient."""

    def test_signature_type_and_funder_passed(self):
        """ClobClient must receive signature_type and funder from settings."""
        # Reset module-level singleton
        import base_engine.execution.clob_adapter as mod
        mod._CLOB_CLIENT = None

        mock_settings = MagicMock()
        mock_settings.POLYMARKET_CLOB_API = "https://clob.polymarket.com"
        mock_settings.PRIVATE_KEY = "0xdeadbeef"
        mock_settings.CLOB_API_KEY = "key"
        mock_settings.CLOB_SECRET = "secret"
        mock_settings.CLOB_PASSPHRASE = "pass"
        mock_settings.POLYGON_CHAIN_ID = 137
        mock_settings.CLOB_SIGNATURE_TYPE = 1
        mock_settings.CLOB_FUNDER_ADDRESS = "0xProxyAddress123"

        mock_clob_client_cls = MagicMock()
        mock_api_creds_cls = MagicMock()

        with patch.object(mod, "settings", mock_settings), \
             patch.dict("sys.modules", {
                 "py_clob_client": MagicMock(),
                 "py_clob_client.client": MagicMock(ClobClient=mock_clob_client_cls),
                 "py_clob_client.clob_types": MagicMock(ApiCreds=mock_api_creds_cls),
             }):
            result = mod._get_clob_client()

        # Verify ClobClient was called with signature_type and funder
        mock_clob_client_cls.assert_called_once()
        call_kwargs = mock_clob_client_cls.call_args
        assert call_kwargs.kwargs.get("signature_type") == 1 or call_kwargs[1].get("signature_type") == 1
        funder_val = call_kwargs.kwargs.get("funder") or call_kwargs[1].get("funder")
        assert funder_val == "0xProxyAddress123"

        # Cleanup singleton
        mod._CLOB_CLIENT = None

    def test_default_signature_type_zero(self):
        """Without env vars, signature_type defaults to 0 and funder to None."""
        import base_engine.execution.clob_adapter as mod
        mod._CLOB_CLIENT = None

        mock_settings = MagicMock()
        mock_settings.POLYMARKET_CLOB_API = "https://clob.polymarket.com"
        mock_settings.PRIVATE_KEY = "0xdeadbeef"
        mock_settings.CLOB_API_KEY = "key"
        mock_settings.CLOB_SECRET = "secret"
        mock_settings.CLOB_PASSPHRASE = "pass"
        mock_settings.POLYGON_CHAIN_ID = 137
        mock_settings.CLOB_SIGNATURE_TYPE = 0
        mock_settings.CLOB_FUNDER_ADDRESS = ""

        mock_clob_client_cls = MagicMock()
        mock_api_creds_cls = MagicMock()

        with patch.object(mod, "settings", mock_settings), \
             patch.dict("sys.modules", {
                 "py_clob_client": MagicMock(),
                 "py_clob_client.client": MagicMock(ClobClient=mock_clob_client_cls),
                 "py_clob_client.clob_types": MagicMock(ApiCreds=mock_api_creds_cls),
             }):
            mod._get_clob_client()

        call_kwargs = mock_clob_client_cls.call_args
        assert call_kwargs.kwargs.get("signature_type") == 0 or call_kwargs[1].get("signature_type") == 0
        funder_val = call_kwargs.kwargs.get("funder") or call_kwargs[1].get("funder")
        assert funder_val is None  # Empty string → None

        mod._CLOB_CLIENT = None


# ── Fix 2: WebSocket subscription limit ──

@pytest.mark.xfail(reason="Pass 3 Fix 2: WS subscription cap not yet implemented in websocket_manager")
class TestWSSubscriptionLimit:
    """Verify WebSocket subscription cap at MAX_WS_SUBSCRIPTIONS."""

    @pytest.mark.asyncio
    async def test_subscribe_market_stops_at_limit(self):
        """subscribe_market should reject new subscriptions at the cap."""
        from base_engine.data.websocket_manager import WebSocketManager, MAX_WS_SUBSCRIPTIONS

        mock_cache = MagicMock()
        mgr = WebSocketManager(cache=mock_cache)
        mgr.ws = AsyncMock()

        # Pre-fill subscriptions to just below limit
        for i in range(MAX_WS_SUBSCRIPTIONS):
            mgr.subscriptions.add(f"orderbook:token_{i}")

        assert len(mgr.subscriptions) == MAX_WS_SUBSCRIPTIONS

        # Next subscribe should be rejected
        await mgr.subscribe_market("new_market", "new_token")
        assert f"orderbook:new_token" not in mgr.subscriptions
        assert len(mgr.subscriptions) == MAX_WS_SUBSCRIPTIONS

    @pytest.mark.asyncio
    async def test_subscribe_market_allows_below_limit(self):
        """subscribe_market should work when under the cap."""
        from base_engine.data.websocket_manager import WebSocketManager, MAX_WS_SUBSCRIPTIONS

        mock_cache = MagicMock()
        mgr = WebSocketManager(cache=mock_cache)
        mgr.ws = AsyncMock()

        # Pre-fill to one below limit
        for i in range(MAX_WS_SUBSCRIPTIONS - 1):
            mgr.subscriptions.add(f"orderbook:token_{i}")

        await mgr.subscribe_market("new_market", "new_token")
        assert f"orderbook:new_token" in mgr.subscriptions

    @pytest.mark.asyncio
    async def test_subscribe_price_stream_stops_at_limit(self):
        """subscribe_price_stream should stop adding at the cap."""
        from base_engine.data.websocket_manager import WebSocketManager, MAX_WS_SUBSCRIPTIONS

        mock_cache = MagicMock()
        mgr = WebSocketManager(cache=mock_cache)
        mgr.ws = AsyncMock()

        # Pre-fill to limit
        for i in range(MAX_WS_SUBSCRIPTIONS):
            mgr.subscriptions.add(f"price:token_{i}")

        # Try to add 10 more via price stream
        new_tokens = [f"new_token_{i}" for i in range(10)]
        await mgr.subscribe_price_stream(new_tokens)

        # None of the new tokens should be added
        for t in new_tokens:
            assert f"price:{t}" not in mgr.subscriptions

    @pytest.mark.asyncio
    async def test_max_ws_subscriptions_is_450(self):
        """Verify the constant is 450 (safe margin below Polymarket's ~500 limit)."""
        from base_engine.data.websocket_manager import MAX_WS_SUBSCRIPTIONS
        assert MAX_WS_SUBSCRIPTIONS == 450


# ── Fix 3: HTTP 425 handling ──

class TestHTTP425Handling:
    """Verify HTTP 425 returns retryable flag. Note: test_non_425_error_no_retryable
    validates CURRENT behavior and is NOT xfail."""

    @pytest.mark.asyncio
    async def test_425_returns_retryable(self):
        """HTTP 425 should return success=False with retryable=True."""
        import httpx
        from base_engine.execution.async_clob_client import AsyncClobClient

        client = AsyncClobClient()

        # Mock the sync client builder
        mock_request = {
            "url": "https://clob.polymarket.com/order",
            "headers": {"Authorization": "test"},
            "body": '{"test": true}',
        }

        # Create a mock 425 response
        mock_response = httpx.Response(
            status_code=425,
            text="Matching engine maintenance",
            request=httpx.Request("POST", "https://clob.polymarket.com/order"),
        )

        mock_httpx_client = AsyncMock()
        mock_httpx_client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "425", request=mock_response.request, response=mock_response,
        ))
        client._client = mock_httpx_client

        with patch("base_engine.execution.async_clob_client._build_post_order_request", return_value=mock_request):
            result = await client.place_order("market1", "token1", "BUY", 10.0, 0.55)

        assert result["success"] is False
        assert result.get("retryable") is True
        assert "425" in result["error"]

    @pytest.mark.asyncio
    async def test_non_425_error_no_retryable(self):
        """Non-425 HTTP errors should NOT have retryable flag."""
        import httpx
        from base_engine.execution.async_clob_client import AsyncClobClient

        client = AsyncClobClient()

        mock_request = {
            "url": "https://clob.polymarket.com/order",
            "headers": {"Authorization": "test"},
            "body": '{"test": true}',
        }

        mock_response = httpx.Response(
            status_code=400,
            text="Bad request",
            request=httpx.Request("POST", "https://clob.polymarket.com/order"),
        )

        mock_httpx_client = AsyncMock()
        mock_httpx_client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "400", request=mock_response.request, response=mock_response,
        ))
        client._client = mock_httpx_client

        with patch("base_engine.execution.async_clob_client._build_post_order_request", return_value=mock_request):
            result = await client.place_order("market1", "token1", "BUY", 10.0, 0.55)

        assert result["success"] is False
        assert "retryable" not in result
