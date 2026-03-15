"""
Unit tests for the 2026 Alpha Roadmap infrastructure.

Covers: exchange models, arb_scanner, capital_tracker, geo_restrictions,
wash_trading_detector, airdrop_tracker, regulatory_monitor, sports_client,
and all new/modified bots.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import asdict


# ─── Exchange Models ────────────────────────────────────────────────
from base_engine.exchanges.models import (
    OrderBookLevel,
    OrderBook,
    MarketSnapshot,
    FeeSchedule,
    OrderResult,
    PositionSnapshot,
)


class TestOrderBookLevel:
    def test_frozen(self):
        lvl = OrderBookLevel(price=0.5, size=100)
        with pytest.raises(AttributeError):
            lvl.price = 0.6  # type: ignore

    def test_values(self):
        lvl = OrderBookLevel(price=0.75, size=50)
        assert lvl.price == 0.75
        assert lvl.size == 50


class TestOrderBook:
    def test_empty_book(self):
        ob = OrderBook(market_id="m1", platform="poly")
        assert ob.best_bid is None
        assert ob.best_ask is None
        assert ob.mid_price is None
        assert ob.spread is None

    def test_properties(self):
        bids = [OrderBookLevel(0.50, 100), OrderBookLevel(0.49, 200)]
        asks = [OrderBookLevel(0.52, 80)]
        ob = OrderBook(market_id="m1", platform="poly", bids=bids, asks=asks)
        assert ob.best_bid == 0.50
        assert ob.best_ask == 0.52
        assert ob.mid_price == pytest.approx(0.51)
        assert ob.spread == pytest.approx(0.02)


class TestMarketSnapshot:
    def test_valid_snapshot(self):
        ms = MarketSnapshot(market_id="m1", platform="poly", question="Will X?", yes_price=0.6)
        assert ms.is_valid is True

    def test_missing_question(self):
        ms = MarketSnapshot(market_id="m1", platform="poly", question="", yes_price=0.6)
        assert ms.is_valid is False

    def test_missing_price(self):
        ms = MarketSnapshot(market_id="m1", platform="poly", question="Will X?")
        assert ms.is_valid is False


class TestFeeSchedule:
    def test_round_trip(self):
        fees = FeeSchedule(taker_fee=0.015, maker_fee=0.005, settlement_fee=0.01, platform="kalshi")
        assert fees.total_round_trip() == pytest.approx(0.04)

    def test_net_price_buy(self):
        fees = FeeSchedule(taker_fee=0.02)
        assert fees.net_price_after_fees(0.50, "BUY") == pytest.approx(0.51)

    def test_net_price_sell(self):
        fees = FeeSchedule(taker_fee=0.02)
        assert fees.net_price_after_fees(0.50, "SELL") == pytest.approx(0.49)

    def test_proportional_taker_fee_at_midpoint(self):
        fees = FeeSchedule(taker_coefficient=0.07, maker_coefficient=0.0175, platform="kalshi")
        # At P=0.50: taker = 0.07 * (1 - 0.50) = 0.035 (3.5%)
        assert fees.taker_fee_at_price(0.50) == pytest.approx(0.035)
        # At P=0.80: taker = 0.07 * 0.20 = 0.014 (1.4%)
        assert fees.taker_fee_at_price(0.80) == pytest.approx(0.014)

    def test_proportional_maker_fee(self):
        fees = FeeSchedule(taker_coefficient=0.07, maker_coefficient=0.0175, platform="kalshi")
        # At P=0.50: maker = 0.0175 * 0.50 = 0.00875 (0.875%)
        assert fees.maker_fee_at_price(0.50) == pytest.approx(0.00875)

    def test_proportional_net_price(self):
        fees = FeeSchedule(taker_coefficient=0.07, platform="kalshi")
        # Buy at P=0.55: fee_rate = 0.07 * 0.45 = 0.0315
        # net = 0.55 * (1 + 0.0315) = 0.567325
        assert fees.net_price_after_fees(0.55, "BUY") == pytest.approx(0.567325)
        # Absolute fee = 0.567325 - 0.55 = 0.017325 ≈ 0.07 * 0.55 * 0.45
        assert (fees.net_price_after_fees(0.55, "BUY") - 0.55) == pytest.approx(0.07 * 0.55 * 0.45)

    def test_flat_fee_at_price_returns_constant(self):
        fees = FeeSchedule(taker_fee=0.015, maker_fee=0.005)
        assert fees.taker_fee_at_price(0.50) == 0.015
        assert fees.taker_fee_at_price(0.80) == 0.015
        assert fees.maker_fee_at_price(0.50) == 0.005


# ─── ArbScanner ─────────────────────────────────────────────────────
from base_engine.exchanges.arb_scanner import ArbScanner, _match_score, _normalize_question


class TestArbScannerHelpers:
    def test_normalize_question(self):
        assert _normalize_question("  Will Bitcoin hit 100k? ") == "will bitcoin hit 100k?"

    def test_match_score_identical(self):
        assert _match_score("Will Bitcoin hit 100k?", "Will Bitcoin hit 100k?") == 1.0

    def test_match_score_empty(self):
        assert _match_score("", "test") == 0.0

    def test_match_score_similar(self):
        score = _match_score("Will Bitcoin reach $100k by end of 2026?",
                             "Will Bitcoin hit $100k by end of 2026?")
        assert score > 0.8


class TestArbScanner:
    def _mock_adapter(self, name: str, markets: list, fee: FeeSchedule) -> MagicMock:
        adapter = MagicMock()
        adapter.is_enabled.return_value = True
        adapter.platform_name.return_value = name
        adapter.get_markets = AsyncMock(return_value=markets)
        adapter.fee_schedule.return_value = fee
        return adapter

    @pytest.mark.asyncio
    async def test_scan_less_than_two_adapters(self):
        adapter = self._mock_adapter("poly", [], FeeSchedule())
        scanner = ArbScanner([adapter])
        assert await scanner.scan() == []

    @pytest.mark.asyncio
    async def test_scan_finds_arb(self):
        ms_a = MarketSnapshot("m1", "poly", "Will Bitcoin reach 100k?", yes_price=0.40)
        ms_b = MarketSnapshot("m2", "kalshi", "Will Bitcoin reach 100k?", yes_price=0.55)
        a1 = self._mock_adapter("poly", [ms_a], FeeSchedule(taker_fee=0.01, platform="poly"))
        a2 = self._mock_adapter("kalshi", [ms_b], FeeSchedule(taker_fee=0.01, platform="kalshi"))
        scanner = ArbScanner([a1, a2])
        opps = await scanner.scan(min_profit_pct=1.0)
        assert len(opps) >= 1
        assert opps[0].profit_pct > 0
        assert opps[0].side in ("buy_a_sell_b", "buy_b_sell_a")

    @pytest.mark.asyncio
    async def test_scan_no_arb_when_fees_eat_spread(self):
        ms_a = MarketSnapshot("m1", "poly", "Will X happen?", yes_price=0.50)
        ms_b = MarketSnapshot("m2", "kalshi", "Will X happen?", yes_price=0.51)
        a1 = self._mock_adapter("poly", [ms_a], FeeSchedule(taker_fee=0.02, platform="poly"))
        a2 = self._mock_adapter("kalshi", [ms_b], FeeSchedule(taker_fee=0.02, platform="kalshi"))
        scanner = ArbScanner([a1, a2])
        opps = await scanner.scan(min_profit_pct=2.0)
        assert opps == []


# ─── CapitalTracker ──────────────────────────────────────────────────
from base_engine.exchanges.capital_tracker import CapitalTracker, VenueBalance, RebalanceSuggestion


class TestVenueBalance:
    def test_total(self):
        vb = VenueBalance(platform="poly", available=100, allocated=50)
        assert vb.total == 150

    def test_utilization(self):
        vb = VenueBalance(platform="poly", available=50, allocated=50)
        assert vb.utilization == pytest.approx(0.5)

    def test_utilization_zero_total(self):
        vb = VenueBalance(platform="poly", available=0, allocated=0)
        assert vb.utilization == 0.0


class TestCapitalTracker:
    def _mock_adapter(self, name: str, balance: float) -> MagicMock:
        adapter = MagicMock()
        adapter.is_enabled.return_value = True
        adapter.platform_name.return_value = name
        adapter.get_balance = AsyncMock(return_value=balance)
        return adapter

    @pytest.mark.asyncio
    async def test_refresh(self):
        a1 = self._mock_adapter("poly", 500.0)
        a2 = self._mock_adapter("kalshi", 300.0)
        tracker = CapitalTracker([a1, a2])
        balances = await tracker.refresh()
        assert "poly" in balances
        assert balances["poly"].available == 500.0
        assert tracker.total_capital == 800.0

    @pytest.mark.asyncio
    async def test_rebalance_suggestions(self):
        a1 = self._mock_adapter("poly", 900.0)
        a2 = self._mock_adapter("kalshi", 100.0)
        tracker = CapitalTracker([a1, a2])
        await tracker.refresh()
        suggestions = tracker.get_rebalance_suggestions(threshold_pct=10.0)
        assert len(suggestions) >= 1
        assert suggestions[0].from_platform == "poly"
        assert suggestions[0].to_platform == "kalshi"

    @pytest.mark.asyncio
    async def test_no_rebalance_when_balanced(self):
        a1 = self._mock_adapter("poly", 500.0)
        a2 = self._mock_adapter("kalshi", 500.0)
        tracker = CapitalTracker([a1, a2])
        await tracker.refresh()
        assert tracker.get_rebalance_suggestions(threshold_pct=20.0) == []


# ─── GeoRestrictions ────────────────────────────────────────────────
from config.geo_restrictions import GeoRestrictionChecker, PLATFORM_STATE_MATRIX


class TestGeoRestrictionChecker:
    def test_disabled_allows_all(self):
        checker = GeoRestrictionChecker(user_state="NY", user_country="US", enabled=False)
        assert checker.is_platform_allowed("kalshi") is True

    def test_us_kalshi_default_allowed(self):
        checker = GeoRestrictionChecker(user_state="TX", user_country="US")
        assert checker.is_platform_allowed("kalshi") is True

    def test_ny_kalshi_blocked(self):
        checker = GeoRestrictionChecker(user_state="NY", user_country="US")
        assert checker.is_platform_allowed("kalshi") is False

    def test_us_polymarket_blocked(self):
        checker = GeoRestrictionChecker(user_state="TX", user_country="US")
        assert checker.is_platform_allowed("polymarket") is False

    def test_non_us_polymarket_allowed(self):
        checker = GeoRestrictionChecker(user_state="", user_country="IE")
        assert checker.is_platform_allowed("polymarket") is True

    def test_unknown_platform_allowed(self):
        checker = GeoRestrictionChecker(user_state="NY", user_country="US")
        assert checker.is_platform_allowed("betfair") is True

    def test_get_allowed_platforms(self):
        checker = GeoRestrictionChecker(user_state="TX", user_country="US")
        allowed = checker.get_allowed_platforms()
        assert "kalshi" in allowed
        assert "polymarket" not in allowed

    def test_check_and_log(self):
        checker = GeoRestrictionChecker(user_state="NY", user_country="US")
        assert checker.check_and_log("kalshi") is False

    def test_ny_coinbase_allowed(self):
        """Coinbase has NY BitLicense."""
        checker = GeoRestrictionChecker(user_state="NY", user_country="US")
        assert checker.is_platform_allowed("coinbase") is True


# ─── WashTradingDetector ────────────────────────────────────────────
from base_engine.analysis.wash_trading_detector import WashTradingDetector


class TestWashTradingDetector:
    @pytest.mark.asyncio
    async def test_no_db_returns_default(self):
        detector = WashTradingDetector(db=None)
        result = await detector.analyze_market("m1")
        assert result["wash_score"] == 0.0
        assert result["is_suspicious"] is False

    @pytest.mark.asyncio
    async def test_analyze_batch(self):
        detector = WashTradingDetector(db=None)
        results = await detector.analyze_batch(["m1", "m2"])
        assert len(results) == 2
        assert "m1" in results
        assert "m2" in results


# ─── AirdropTracker ──────────────────────────────────────────────────
from base_engine.monitoring.airdrop_tracker import AirdropTracker, AirdropMetrics


class TestAirdropMetrics:
    def test_to_dict(self):
        m = AirdropMetrics(daily_volume_usd=100.0, unique_markets_traded=5)
        d = m.to_dict()
        assert d["daily_volume_usd"] == 100.0
        assert d["unique_markets_traded"] == 5

    def test_defaults(self):
        m = AirdropMetrics()
        assert m.readiness_score == 0.0
        assert m.consecutive_active_days == 0


class TestAirdropTracker:
    @pytest.mark.asyncio
    async def test_no_db_returns_defaults(self):
        tracker = AirdropTracker(db=None)
        metrics = await tracker.compute_metrics()
        assert isinstance(metrics, AirdropMetrics)
        assert metrics.daily_volume_usd == 0.0

    def test_last_metrics_property(self):
        tracker = AirdropTracker(db=None)
        assert tracker.last_metrics is None


# ─── RegulatoryMonitor ───────────────────────────────────────────────
from base_engine.monitoring.regulatory_monitor import RegulatoryMonitor, ALERT_KEYWORDS, REGULATORY_FEEDS


class TestRegulatoryMonitor:
    def test_init(self):
        bus = MagicMock()
        monitor = RegulatoryMonitor(event_bus=bus)
        assert monitor._event_bus is bus

    def test_alert_keywords_not_empty(self):
        assert len(ALERT_KEYWORDS) > 0

    def test_regulatory_feeds_not_empty(self):
        assert len(REGULATORY_FEEDS) > 0

    @pytest.mark.asyncio
    async def test_check_once_graceful_on_no_feeds(self):
        """Check it doesn't crash if feedparser returns empty or is missing."""
        monitor = RegulatoryMonitor(event_bus=MagicMock())
        # feedparser is imported inside check_once(); mock it at builtins level
        mock_fp = MagicMock()
        mock_fp.parse.return_value = {"entries": []}
        with patch.dict("sys.modules", {"feedparser": mock_fp}):
            alerts = await monitor.check_once()
            assert isinstance(alerts, list)


# ─── SportsClient ───────────────────────────────────────────────────
from base_engine.data.sports_client import SportsClient


class TestSportsClient:
    def test_init_no_key(self):
        client = SportsClient()
        assert client._api_football_key is None

    def test_init_with_key(self):
        client = SportsClient(api_football_key="test_key")
        assert client._api_football_key == "test_key"


# ─── New Bot Imports ─────────────────────────────────────────────────
class TestBotImports:
    """Verify all new bots import without errors (catches syntax bugs)."""

    def test_import_cross_platform_arb_bot(self):
        from bots.cross_platform_arb_bot import CrossPlatformArbBot
        assert CrossPlatformArbBot is not None

    def test_import_oracle_bot(self):
        from bots.oracle_bot import OracleBot
        assert OracleBot is not None

    def test_import_sports_bot(self):
        from bots.sports_bot import SportsBot
        assert SportsBot is not None

    def test_import_llm_forecaster_bot(self):
        from bots.llm_forecaster_bot import LLMForecasterBot
        assert LLMForecasterBot is not None


# ─── CrossPlatformArbBot ────────────────────────────────────────────
from bots.cross_platform_arb_bot import CrossPlatformArbBot


class TestCrossPlatformArbBot:
    @pytest.fixture
    def mock_engine(self):
        engine = MagicMock()
        engine.trade_coordinator = None
        engine.cache = None
        engine.db = None
        engine.risk_manager = None
        engine._arb_scanner = None
        engine._exchange_adapters = []
        return engine

    @pytest.fixture
    def bot(self, mock_engine):
        return CrossPlatformArbBot(mock_engine)

    def test_bot_name(self, bot):
        assert bot.bot_name == "CrossPlatformArbBot"

    def test_is_crypto_market(self, bot):
        assert bot._is_crypto_market({"question": "Will Bitcoin exceed $100k?"}) is True
        assert bot._is_crypto_market({"question": "Will Biden win?"}) is False

    def test_extract_crypto_symbol(self, bot):
        sym = bot._extract_crypto_symbol({"question": "Will Bitcoin exceed $100k?"})
        assert sym is not None
        assert sym.upper() in ("BTC", "BITCOIN")

    def test_extract_threshold_price(self, bot):
        price = bot._extract_threshold_price("Will Bitcoin exceed $100,000?")
        assert price is not None
        assert price > 0

    @pytest.mark.asyncio
    async def test_analyze_opportunity_empty(self, bot):
        result = await bot.analyze_opportunity({})
        assert result is None


# ─── OracleBot ───────────────────────────────────────────────────────
from bots.oracle_bot import OracleBot


class TestOracleBot:
    @pytest.fixture
    def mock_engine(self):
        engine = MagicMock()
        engine.trade_coordinator = None
        engine.cache = None
        engine.db = None
        engine.risk_manager = None
        engine.event_bus = MagicMock()
        engine.event_bus.subscribe = AsyncMock()
        engine.event_bus.unsubscribe = AsyncMock()
        return engine

    @pytest.fixture
    def bot(self, mock_engine):
        return OracleBot(mock_engine)

    def test_bot_name(self, bot):
        assert bot.bot_name == "OracleBot"

    @pytest.mark.asyncio
    async def test_analyze_opportunity_returns_none(self, bot):
        """OracleBot is event-driven, analyze_opportunity returns None."""
        result = await bot.analyze_opportunity({})
        assert result is None

    @pytest.mark.asyncio
    async def test_on_proposed_outcome_no_event_bus(self, bot):
        """Should not crash if event_bus is missing."""
        bot.base_engine.event_bus = None
        await bot._on_proposed_outcome({})


# ─── SportsBot ───────────────────────────────────────────────────────
from bots.sports_bot import SportsBot


class TestSportsBot:
    @pytest.fixture
    def mock_engine(self):
        engine = MagicMock()
        engine.trade_coordinator = None
        engine.cache = None
        engine.db = None
        engine.risk_manager = None
        engine._sports_client = None
        return engine

    @pytest.fixture
    def bot(self, mock_engine):
        return SportsBot(mock_engine)

    def test_bot_name(self, bot):
        assert bot.bot_name == "SportsBot"

    def test_scan_interval_no_live_games(self, bot):
        """Default interval when no games are live."""
        interval = bot._get_scan_interval_seconds()
        assert interval > 0

    @pytest.mark.asyncio
    async def test_analyze_opportunity_empty(self, bot):
        result = await bot.analyze_opportunity({})
        assert result is None


# ─── LLMForecasterBot ───────────────────────────────────────────────
from bots.llm_forecaster_bot import LLMForecasterBot


class TestLLMForecasterBot:
    @pytest.fixture
    def mock_engine(self):
        engine = MagicMock()
        engine.trade_coordinator = None
        engine.cache = None
        engine.db = None
        engine.risk_manager = None
        engine.prediction_engine = None
        return engine

    @pytest.fixture
    def bot(self, mock_engine):
        return LLMForecasterBot(mock_engine)

    def test_bot_name(self, bot):
        assert bot.bot_name == "LLMForecasterBot"

    def test_scan_interval(self, bot):
        interval = bot._get_scan_interval_seconds()
        assert interval > 0

    @pytest.mark.asyncio
    async def test_analyze_opportunity_empty(self, bot):
        result = await bot.analyze_opportunity({})
        assert result is None


# ─── EnsembleBot (new methods) ──────────────────────────────────────
from bots.ensemble_bot import EnsembleBot


class TestEnsembleBotNewMethods:
    @pytest.fixture
    def mock_engine(self):
        engine = MagicMock()
        engine.trade_coordinator = None
        engine.cache = None
        engine.db = None
        engine.risk_manager = None
        engine.signal_ingestion = MagicMock()
        return engine

    @pytest.fixture
    def bot(self, mock_engine):
        return EnsembleBot(mock_engine)

    @pytest.mark.asyncio
    async def test_calculate_sentiment_no_db(self, bot):
        """Should return None gracefully when DB unavailable."""
        bot.base_engine.db = None
        result = await bot._calculate_sentiment("m1")
        assert result is None

    @pytest.mark.asyncio
    async def test_event_calendar_no_signal_ingestion(self, bot):
        """Should return 1.0 (neutral) when signal_ingestion is missing."""
        bot.base_engine.signal_ingestion = None
        mult = await bot._event_calendar_confidence_mult("m1")
        assert mult == 1.0



# ─── Main.py BOT_REGISTRY ───────────────────────────────────────────
class TestBotRegistry:
    def test_registry_has_all_new_bots(self):
        """main.py BOT_REGISTRY should contain exactly 14 bots (EnsembleBot archived Session 60)."""
        from main import BOT_REGISTRY
        expected_bots = {
            "ArbitrageBot", "MirrorBot",
            "CrossPlatformArbBot", "OracleBot", "SportsBot", "LLMForecasterBot",
            "WeatherBot",
            # Sports betting bots — Migration 022
            "SportsInjuryBot", "SportsLiveBot", "SportsArbBot",
            # Esports bots — Migration 024
            "EsportsBot", "EsportsLiveBot",
            # Logical arbitrage bot
            "LogicalArbBot",
        }
        assert set(BOT_REGISTRY.keys()) == expected_bots

    def test_registry_no_deleted_bots(self):
        """Removed bots must not be in registry."""
        from main import BOT_REGISTRY
        deleted = {"CryptoPoliticalBot", "CryptoBot", "PoliticalBot",
                   "MarketMakerBot", "StalePriceBot", "FrontRunningHFTBot",
                   "TemporalArbitrageBot", "MomentumBot", "EnsembleBot"}
        assert deleted.isdisjoint(set(BOT_REGISTRY.keys()))
