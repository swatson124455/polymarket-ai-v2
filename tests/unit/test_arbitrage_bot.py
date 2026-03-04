"""Unit tests for ArbitrageBot. Do not touch other bots or full engine init."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from bots.arbitrage_bot import ArbitrageBot, _arb_setting
from base_engine.coordination.arbitrage_coordinator import ArbitrageTransactionCoordinator


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.trade_coordinator = None
    engine.cache = None
    engine.db = None
    engine.risk_manager = None
    return engine


@pytest.fixture
def arb_bot(mock_engine):
    return ArbitrageBot(mock_engine)


def test_arb_bot_uses_settings(arb_bot):
    assert arb_bot.min_profit_threshold >= 0
    assert arb_bot.max_profit_threshold >= arb_bot.min_profit_threshold
    assert arb_bot.default_order_size > 0
    assert arb_bot.max_markets_per_scan > 0


def test_arb_setting_fallback():
    v = _arb_setting("NONEXISTENT_SETTING", 0.42)
    assert v == 0.42


@pytest.mark.asyncio
async def test_analyze_opportunity_empty_returns_none(arb_bot):
    out = await arb_bot.analyze_opportunity({})
    assert out is None


@pytest.mark.asyncio
async def test_analyze_opportunity_no_id_returns_none(arb_bot):
    out = await arb_bot.analyze_opportunity({"tokens": []})
    assert out is None


@pytest.mark.asyncio
async def test_analyze_opportunity_no_tokens_returns_none(arb_bot):
    out = await arb_bot.analyze_opportunity({"id": "m1"})
    assert out is None


@pytest.mark.asyncio
async def test_analyze_opportunity_single_token_goes_to_bundle(arb_bot):
    out = await arb_bot.analyze_opportunity({
        "id": "m1",
        "tokens": [{"outcome": "A", "tokenId": "t1", "outcomePrice": "0.5"}],
    })
    assert out is None


def test_get_arb_coordinator_returns_coordinator(arb_bot):
    c = arb_bot._get_arb_coordinator()
    assert isinstance(c, ArbitrageTransactionCoordinator)
    assert callable(c.place_order_fn)
    assert c.reserving_bot_id == "ArbitrageBot"
    assert c.trade_coordinator is None


def test_dedup_key_stable(arb_bot):
    opp = {"type": "long_arbitrage", "market_id": "m1", "yes_price": 0.4, "no_price": 0.5, "total_price": 0.9}
    k1 = arb_bot._dedup_key(opp)
    k2 = arb_bot._dedup_key(opp)
    assert k1 == k2
    assert len(k1) == 16
