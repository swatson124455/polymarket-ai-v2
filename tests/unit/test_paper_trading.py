"""Unit tests for paper trading flow."""
import pytest
from unittest.mock import patch
from base_engine.execution.paper_trading import PaperTradingEngine


@pytest.fixture(autouse=True)
def _disable_slippage():
    """Disable slippage and fees in paper trading tests for deterministic assertions."""
    with patch("base_engine.execution.paper_trading.settings") as mock_settings, \
         patch("base_engine.execution.paper_trading._size_dependent_slippage_bps", return_value=0), \
         patch("base_engine.execution.paper_trading._sqrt_market_impact_bps", return_value=0):
        mock_settings.FIXED_SLIPPAGE_BPS = 0
        mock_settings.TAKER_FEE_BPS = 0
        mock_settings.MAKER_FEE_BPS = 0
        mock_settings.PAPER_TAKER_FEE_BPS = 0
        mock_settings.PAPER_REALISTIC_FILLS = False
        mock_settings.PAPER_LATENCY_DRIFT_BPS_PER_SEC = 10
        mock_settings.PAPER_DEFAULT_SPREAD = 0.04
        yield


@pytest.mark.asyncio
async def test_paper_trading_place_order_buy():
    """PaperTradingEngine: BUY reduces cash and creates position."""
    engine = PaperTradingEngine(initial_capital=10000.0)
    engine.enable()

    result = await engine.place_order(
        market_id="m1",
        token_id="t1",
        side="BUY",
        size=10.0,
        price=0.55,
        bot_name="test",
    )
    assert result["success"] is True
    assert "order_id" in result
    assert engine.cash == 10000.0 - (10.0 * 0.55)
    assert len(engine.get_trades()) == 1
    assert len(engine.get_positions()) == 1
    assert engine.get_positions()["m1"]["size"] == 10.0
    assert engine.get_positions()["m1"]["avg_price"] == 0.55


@pytest.mark.asyncio
async def test_paper_trading_place_order_disabled():
    """PaperTradingEngine: place_order fails when disabled."""
    engine = PaperTradingEngine(initial_capital=10000.0)
    result = await engine.place_order(
        market_id="m1", token_id="t1", side="BUY", size=10.0, price=0.5, bot_name="test"
    )
    assert result["success"] is False
    assert "not enabled" in result.get("error", "").lower()


@pytest.mark.asyncio
async def test_paper_trading_insufficient_cash():
    """PaperTradingEngine: BUY fails when cost exceeds cash."""
    engine = PaperTradingEngine(initial_capital=100.0)
    engine.enable()

    result = await engine.place_order(
        market_id="m1",
        token_id="t1",
        side="BUY",
        size=1000.0,
        price=0.5,
        bot_name="test",
    )
    assert result["success"] is False
    assert "insufficient" in result.get("error", "").lower()


@pytest.mark.asyncio
async def test_paper_trading_buy_then_sell():
    """PaperTradingEngine: BUY then SELL closes position."""
    engine = PaperTradingEngine(initial_capital=10000.0)
    engine.enable()

    await engine.place_order("m1", "t1", "BUY", 10.0, 0.5, "test")
    cash_after_buy = engine.cash

    result = await engine.place_order("m1", "t1", "SELL", 10.0, 0.6, "test")
    assert result["success"] is True
    assert len(engine.get_positions()) == 0
    assert engine.cash == cash_after_buy + (10.0 * 0.6)


@pytest.mark.asyncio
async def test_paper_trading_slippage_applied():
    """PaperTradingEngine: Slippage adjusts fill price when enabled."""
    with patch("base_engine.execution.paper_trading.settings") as mock_settings:
        mock_settings.FIXED_SLIPPAGE_BPS = 100  # 1% slippage
        mock_settings.TAKER_FEE_BPS = 0
        mock_settings.MAKER_FEE_BPS = 0
        mock_settings.PAPER_TAKER_FEE_BPS = 0
        mock_settings.PAPER_REALISTIC_FILLS = False
        mock_settings.PAPER_LATENCY_DRIFT_BPS_PER_SEC = 10
        mock_settings.PAPER_DEFAULT_SPREAD = 0.04
        engine = PaperTradingEngine(initial_capital=10000.0)
        engine.enable()

        result = await engine.place_order("m1", "t1", "BUY", 10.0, 0.50, "test")
        assert result["success"] is True
        # Fill price should be higher than requested (BUY = worse price)
        assert result["price"] > 0.50
        assert result["slippage_bps"] > 0
        assert "requested_price" in result
        assert result["requested_price"] == 0.50
