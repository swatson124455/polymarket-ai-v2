import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from bots.arbitrage_bot import ArbitrageBot
from bots.ensemble_bot import EnsembleBot
from bots.mirror_bot import MirrorBot
from bots.cross_platform_arb_bot import CrossPlatformArbBot
from bots.oracle_bot import OracleBot
from bots.sports_bot import SportsBot
from bots.llm_forecaster_bot import LLMForecasterBot
from base_engine.base_engine import BaseEngine


@pytest.fixture
async def base_engine():
    engine = BaseEngine()
    await engine.init()
    yield engine
    await engine.stop()


@pytest.mark.asyncio
async def test_arbitrage_bot_init(base_engine):
    bot = ArbitrageBot(base_engine)
    assert bot.bot_name == "ArbitrageBot"
    assert bot.base_engine == base_engine


@pytest.mark.asyncio
async def test_ensemble_bot_init(base_engine):
    bot = EnsembleBot(base_engine)
    assert bot.bot_name == "EnsembleBot"
    assert bot.base_engine == base_engine
    assert len(bot.model_weights) >= 3


@pytest.mark.asyncio
async def test_mirror_bot_init(base_engine):
    bot = MirrorBot(base_engine)
    assert bot.bot_name == "MirrorBot"
    assert bot.base_engine == base_engine


@pytest.mark.asyncio
async def test_cross_platform_arb_bot_init(base_engine):
    bot = CrossPlatformArbBot(base_engine)
    assert bot.bot_name == "CrossPlatformArbBot"
    assert bot.base_engine == base_engine


@pytest.mark.asyncio
async def test_oracle_bot_init(base_engine):
    bot = OracleBot(base_engine)
    assert bot.bot_name == "OracleBot"
    assert bot.base_engine == base_engine


@pytest.mark.asyncio
async def test_sports_bot_init(base_engine):
    bot = SportsBot(base_engine)
    assert bot.bot_name == "SportsBot"
    assert bot.base_engine == base_engine


@pytest.mark.asyncio
async def test_llm_forecaster_bot_init(base_engine):
    bot = LLMForecasterBot(base_engine)
    assert bot.bot_name == "LLMForecasterBot"
    assert bot.base_engine == base_engine


@pytest.mark.asyncio
async def test_bot_start_stop(base_engine):
    bot = ArbitrageBot(base_engine)
    await bot.start()
    assert bot.running is True
    await bot.stop()
    assert bot.running is False


@pytest.mark.asyncio
async def test_arbitrage_bot_uses_price_history_when_configured(base_engine):
    """When ARBITRAGE_REQUIRE_PRICE_MOVEMENT=True, analyze_opportunity filters by price movement."""
    with patch("bots.arbitrage_bot.settings") as mock_settings:
        mock_settings.ARBITRAGE_REQUIRE_PRICE_MOVEMENT = True
        # ArbitrageBot __init__ reads these settings — must be real numbers
        mock_settings.ARB_MIN_PROFIT_THRESHOLD = 0.01
        mock_settings.ARB_MAX_PROFIT_THRESHOLD = 0.05
        mock_settings.ARB_DEFAULT_ORDER_SIZE = 100.0
        mock_settings.ARB_MAX_MARKETS_PER_SCAN = 500
        mock_settings.ARB_MIN_NET_EDGE = 0.005
        mock_settings.ARB_NEGRISK_CONFIDENCE_BOOST = 0.1
        mock_settings.ARB_DEFAULT_CONFIDENCE = 0.5
        mock_settings.ARB_PRICE_MOVEMENT_LIMIT = 20
        mock_settings.ARB_PRICE_MOVEMENT_MIN_STD = 0.01
        base_engine.db = AsyncMock()
        base_engine.db.get_recent_prices_for_market = AsyncMock(return_value=[])
        bot = ArbitrageBot(base_engine)
        market_data = {
            "id": "m1",
            "tokens": [
                {"outcome": "YES", "tokenId": "t1", "outcomePrice": "0.4"},
                {"outcome": "NO", "tokenId": "t2", "outcomePrice": "0.5"},
            ],
        }
        opp = await bot.analyze_opportunity(market_data)
        assert opp is None
        base_engine.db.get_recent_prices_for_market.assert_called()




@pytest.mark.asyncio
async def test_ensemble_bot_weight_optimization(base_engine):
    bot = EnsembleBot(base_engine)
    
    backtest_results = [
        {"sharpe_ratio": 1.5},
        {"sharpe_ratio": 1.8},
        {"sharpe_ratio": 1.2}
    ]
    
    await bot.optimize_weights(backtest_results)
    assert sum(bot.model_weights.values()) == pytest.approx(1.0, abs=0.01)


@pytest.mark.asyncio
async def test_ensemble_bot_respects_trade_coordinator():
    """When trade_coordinator says can_take_position False, bot does not return opportunity."""
    engine = MagicMock()
    engine.trade_coordinator = AsyncMock()
    engine.trade_coordinator.can_take_position = AsyncMock(return_value=False)
    engine.get_predictions = AsyncMock(
        return_value={"confidence": 0.7, "prediction": 0.6}
    )
    engine.db = MagicMock()
    engine.db.session_factory = True
    result = MagicMock()
    result.fetchone.return_value = (0.8, 20)
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    engine.db.get_session = MagicMock(return_value=session)
    engine.signal_ingestion = None
    engine.risk_manager = None
    engine.cache = None

    bot = EnsembleBot(engine)
    market_data = {
        "id": "m1",
        "category": "politics",
        "tokens": [
            {"outcome": "Yes", "tokenId": "t1", "outcomePrice": "0.55"},
        ],
        "end_date_iso": "2030-01-01T00:00:00Z",
    }
    opp = await bot.analyze_opportunity(market_data)
    assert opp is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
