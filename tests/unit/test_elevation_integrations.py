"""
Integration tests for all Elevation Plan modules.
Tests: instantiation, method signatures, logic correctness, edge cases.
"""
import asyncio
import pytest
import math
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta


# ============================================================
# P3-02: Market Impact Estimator (Kyle's Lambda)
# ============================================================
class TestMarketImpactEstimator:
    def test_instantiation_no_db(self):
        from base_engine.features.market_impact import MarketImpactEstimator, DEFAULT_LAMBDA
        est = MarketImpactEstimator(db=None)
        assert est.db is None
        assert est._cache == {}

    @pytest.mark.asyncio
    async def test_estimate_no_db_returns_default(self):
        from base_engine.features.market_impact import MarketImpactEstimator, DEFAULT_LAMBDA
        est = MarketImpactEstimator(db=None)
        result = await est.estimate_kyle_lambda("market_123")
        assert result == DEFAULT_LAMBDA

    def test_kyle_optimal_size_basic(self):
        from base_engine.features.market_impact import MarketImpactEstimator
        est = MarketImpactEstimator()
        # edge=0.1, lambda=0.5 -> optimal = 0.1/(2*0.5) = 0.1
        size = est.kyle_optimal_size(edge=0.1, lambda_estimate=0.5, max_position=1000)
        assert abs(size - 0.1) < 0.001

    def test_kyle_optimal_size_capped(self):
        from base_engine.features.market_impact import MarketImpactEstimator
        est = MarketImpactEstimator()
        # Very small lambda -> large optimal, but capped at max_position
        size = est.kyle_optimal_size(edge=0.5, lambda_estimate=0.001, max_position=50)
        assert size == 50

    def test_kyle_optimal_size_zero_lambda(self):
        from base_engine.features.market_impact import MarketImpactEstimator
        est = MarketImpactEstimator()
        size = est.kyle_optimal_size(edge=0.1, lambda_estimate=0, max_position=100)
        assert size == 100


# ============================================================
# P3-08: Spread Decomposition
# ============================================================
class TestSpreadDecomposer:
    def test_instantiation(self):
        from base_engine.features.spread_decomposition import SpreadDecomposer
        sd = SpreadDecomposer(db=None)
        assert sd.db is None

    @pytest.mark.asyncio
    async def test_no_db_returns_caution(self):
        from base_engine.features.spread_decomposition import SpreadDecomposer
        sd = SpreadDecomposer(db=None)
        result = await sd.compute_information_share("market_1")
        assert result["recommendation"] == "caution"
        assert result["information_share"] == 0.5
        assert result["n_fills"] == 0


# ============================================================
# P3-06: Calibration
# ============================================================
class TestCalibrator:
    def test_instantiation(self):
        from base_engine.features.calibration import FavoriteLongshotCalibrator
        cal = FavoriteLongshotCalibrator(db=None)
        assert not cal.is_fitted

    def test_calibrate_unfitted_returns_identity(self):
        from base_engine.features.calibration import FavoriteLongshotCalibrator
        cal = FavoriteLongshotCalibrator(db=None)
        assert cal.calibrate(0.75) == 0.75
        assert cal.calibrate(0.10) == 0.10

    @pytest.mark.asyncio
    async def test_fit_no_db_returns_false(self):
        from base_engine.features.calibration import FavoriteLongshotCalibrator
        cal = FavoriteLongshotCalibrator(db=None)
        result = await cal.fit_from_prediction_log()
        assert result is False
        assert not cal.is_fitted


# ============================================================
# P3-05: Cross-Market Features
# ============================================================
class TestCrossMarketFeatures:
    def test_instantiation(self):
        from base_engine.features.cross_market_features import CrossMarketFeatureExtractor
        ext = CrossMarketFeatureExtractor(db=None)
        assert ext._related_cache == {}

    @pytest.mark.asyncio
    async def test_no_db_returns_zeros(self):
        from base_engine.features.cross_market_features import CrossMarketFeatureExtractor
        ext = CrossMarketFeatureExtractor(db=None)
        features = await ext.get_features("market_1")
        assert features["n_related_markets"] == 0.0
        assert features["logical_consistency_violation"] == 0.0
        assert "related_price_spread" in features


# ============================================================
# P5-02: Counterparty Classifier
# ============================================================
class TestCounterpartyClassifier:
    def test_instantiation(self):
        from base_engine.features.counterparty_classifier import CounterpartyClassifier, CounterpartyType
        cc = CounterpartyClassifier(db=None)
        assert cc._cache == {}

    @pytest.mark.asyncio
    async def test_no_db_returns_unknown(self):
        from base_engine.features.counterparty_classifier import CounterpartyClassifier
        cc = CounterpartyClassifier(db=None)
        result = await cc.classify("0xabc123")
        assert result["type"] == "unknown"
        assert result["confidence"] == 0.0

    def test_classify_from_metrics_market_maker(self):
        from base_engine.features.counterparty_classifier import CounterpartyClassifier, CounterpartyType
        cc = CounterpartyClassifier(db=None)
        metrics = {
            "trade_count": 100,
            "unique_markets": 30,
            "avg_interval_seconds": 300,
            "both_sides_ratio": 0.7,
            "trades_per_market": 3.3,
        }
        result = cc._classify_from_metrics(metrics)
        assert result == CounterpartyType.MARKET_MAKER

    def test_classify_from_metrics_arbitrageur(self):
        from base_engine.features.counterparty_classifier import CounterpartyClassifier, CounterpartyType
        cc = CounterpartyClassifier(db=None)
        metrics = {
            "trade_count": 500,
            "unique_markets": 50,
            "avg_interval_seconds": 10,
            "both_sides_ratio": 0.2,
            "trades_per_market": 10,
        }
        result = cc._classify_from_metrics(metrics)
        assert result == CounterpartyType.ARBITRAGEUR

    def test_classify_from_metrics_noise(self):
        from base_engine.features.counterparty_classifier import CounterpartyClassifier, CounterpartyType
        cc = CounterpartyClassifier(db=None)
        metrics = {
            "trade_count": 50,
            "unique_markets": 40,
            "avg_interval_seconds": 3600,
            "both_sides_ratio": 0.1,
            "trades_per_market": 1.25,
        }
        result = cc._classify_from_metrics(metrics)
        assert result == CounterpartyType.NOISE


# ============================================================
# P5-03: LLM Probability Estimator
# ============================================================
class TestLLMProbability:
    def test_instantiation_no_keys(self):
        from base_engine.features.llm_probability import LLMProbabilityEstimator
        est = LLMProbabilityEstimator(db=None)
        # No API keys set -> not available
        # (may be available if env vars set, but shouldn't crash)
        assert isinstance(est.is_available, bool)

    def test_prompt_format(self):
        from base_engine.features.llm_probability import LLMProbabilityEstimator
        est = LLMProbabilityEstimator(db=None)
        prompt = est._build_prompt("Will BTC hit 100k?", 0.65, "crypto", "30 days")
        assert "Will BTC hit 100k?" in prompt
        assert "0.65" in prompt
        assert "probability" in prompt.lower()


# ============================================================
# P4-01: CVaR Risk Management
# ============================================================
class TestCorrelationRisk:
    def test_instantiation(self):
        from base_engine.risk.correlation_risk import CorrelationRiskManager
        crm = CorrelationRiskManager(db=None)
        assert crm._correlation_matrix is None

    def test_cvar_empty_positions(self):
        from base_engine.risk.correlation_risk import CorrelationRiskManager
        crm = CorrelationRiskManager(db=None)
        result = crm.compute_cvar([], confidence_level=0.95)
        assert result["var"] == 0.0
        assert result["cvar"] == 0.0
        assert result["max_loss"] == 0.0

    def test_cvar_single_position(self):
        from base_engine.risk.correlation_risk import CorrelationRiskManager
        crm = CorrelationRiskManager(db=None)
        positions = [{"market_id": "m1", "side": "YES", "size": 100, "price": 0.5, "predicted_prob": 0.6}]
        result = crm.compute_cvar(positions, confidence_level=0.95, n_simulations=5000)
        assert result["var"] > 0
        assert result["cvar"] >= result["var"]
        assert result["max_loss"] >= result["cvar"]
        assert result["n_positions"] == 1

    def test_marginal_cvar(self):
        from base_engine.risk.correlation_risk import CorrelationRiskManager
        crm = CorrelationRiskManager(db=None)
        existing = [{"market_id": "m1", "side": "YES", "size": 100, "price": 0.5, "predicted_prob": 0.6}]
        new_pos = {"market_id": "m2", "side": "YES", "size": 50, "price": 0.3, "predicted_prob": 0.5}
        marginal = crm.compute_marginal_cvar(existing, new_pos)
        assert isinstance(marginal, float)

    def test_stress_scenarios(self):
        from base_engine.risk.correlation_risk import CorrelationRiskManager
        crm = CorrelationRiskManager(db=None)
        positions = [
            {"market_id": "m1", "side": "YES", "size": 100, "price": 0.5, "category": "politics"},
            {"market_id": "m2", "side": "NO", "size": 50, "price": 0.7, "category": "crypto"},
        ]
        scenarios = crm.compute_stress_scenarios(positions)
        assert len(scenarios) == 3
        assert any(s["scenario"] == "total_loss" for s in scenarios)


# ============================================================
# P4-03: Multi-Layer Kill Switch
# ============================================================
class TestMultiKillSwitch:
    def test_bot_kill_switch(self):
        from base_engine.coordination.multi_kill_switch import BotKillSwitch
        bks = BotKillSwitch(db=None)
        assert not bks.is_killed("test_bot")

    @pytest.mark.asyncio
    async def test_bot_kill_and_check(self):
        from base_engine.coordination.multi_kill_switch import BotKillSwitch
        bks = BotKillSwitch(db=None)
        await bks.kill_bot("test_bot", "testing")
        assert bks.is_killed("test_bot")
        assert not bks.is_killed("other_bot")

    @pytest.mark.asyncio
    async def test_bot_kill_reset(self):
        from base_engine.coordination.multi_kill_switch import BotKillSwitch
        bks = BotKillSwitch(db=None)
        await bks.kill_bot("test_bot", "testing")
        assert bks.is_killed("test_bot")
        await bks.reset_bot("test_bot")
        assert not bks.is_killed("test_bot")

    @pytest.mark.asyncio
    async def test_multi_layer_facade(self):
        from base_engine.coordination.multi_kill_switch import MultiLayerKillSwitch
        mock_ks = AsyncMock()
        mock_ks.is_engaged = AsyncMock(return_value=False)
        mock_ks.check_kill_status = AsyncMock(return_value=False)
        mlks = MultiLayerKillSwitch(base_kill_switch=mock_ks, db=None)
        assert await mlks.should_trade("test_bot") is True

    @pytest.mark.asyncio
    async def test_multi_layer_bot_killed(self):
        from base_engine.coordination.multi_kill_switch import MultiLayerKillSwitch
        mock_ks = AsyncMock()
        mock_ks.is_engaged = AsyncMock(return_value=False)
        mock_ks.check_kill_status = AsyncMock(return_value=False)
        mlks = MultiLayerKillSwitch(base_kill_switch=mock_ks, db=None)
        await mlks.bot.kill_bot("test_bot", "test")
        assert await mlks.should_trade("test_bot") is False
        assert await mlks.should_trade("other_bot") is True


# ============================================================
# P5-01: Tunable Config
# ============================================================
class TestTunableConfig:
    def test_instantiation(self):
        from base_engine.config.tunable_config import TunableConfigStore
        store = TunableConfigStore(db=None)
        assert store._cache == {}
        assert not store._loaded

    def test_get_default(self):
        from base_engine.config.tunable_config import TunableConfigStore
        store = TunableConfigStore(db=None)
        assert store.get("MISSING_KEY", 42.0) == 42.0

    @pytest.mark.asyncio
    async def test_set_and_get(self):
        from base_engine.config.tunable_config import TunableConfigStore
        store = TunableConfigStore(db=None)
        await store.set("KELLY_FRACTION", 0.3, "test")
        assert store.get("KELLY_FRACTION") == 0.3

    def test_get_all(self):
        from base_engine.config.tunable_config import TunableConfigStore
        store = TunableConfigStore(db=None)
        store._cache = {"A": 1.0, "B": 2.0}
        assert store.get_all() == {"A": 1.0, "B": 2.0}


# ============================================================
# P5-01: Config Tuner
# ============================================================
class TestConfigTuner:
    @pytest.mark.asyncio
    async def test_tune_no_db(self):
        from base_engine.learning.config_tuner import ConfigTuner
        tuner = ConfigTuner(db=None, config_store=None)
        result = await tuner.tune()
        assert result == {}


# ============================================================
# P6-02: Secret Manager
# ============================================================
class TestSecretManager:
    def test_instantiation(self):
        from base_engine.config.secret_manager import SecretManager
        sm = SecretManager()
        assert not sm._initialized

    def test_get_without_init_falls_back_to_env(self):
        import os
        from base_engine.config.secret_manager import SecretManager
        sm = SecretManager()
        # Should fall back to os.getenv
        result = sm.get("NONEXISTENT_KEY_12345", "default_val")
        assert result == "default_val"

    def test_init_no_password_returns_false(self):
        from base_engine.config.secret_manager import SecretManager
        sm = SecretManager()
        result = sm.init(password="")
        assert result is False


# ============================================================
# Kalshi Client (P5-05)
# ============================================================
class TestKalshiClient:
    def test_instantiation(self):
        from base_engine.data.kalshi_client import KalshiClient
        kc = KalshiClient()
        # No env vars -> not available
        assert isinstance(kc.is_available, bool)

    @pytest.mark.asyncio
    async def test_get_markets_not_initialized(self):
        from base_engine.data.kalshi_client import KalshiClient
        kc = KalshiClient()
        result = await kc.get_markets()
        assert result == []

    @pytest.mark.asyncio
    async def test_place_order_not_initialized(self):
        from base_engine.data.kalshi_client import KalshiClient
        kc = KalshiClient()
        result = await kc.place_order("market_1", "YES", 10, 0.5)
        assert result["success"] is False


# ============================================================
# Oracle Monitor (P5-06)
# ============================================================
class TestOracleMonitor:
    def test_instantiation(self):
        from base_engine.data.oracle_monitor import OracleMonitor
        om = OracleMonitor(db=None)
        assert not om._initialized

    @pytest.mark.asyncio
    async def test_check_proposals_not_initialized(self):
        from base_engine.data.oracle_monitor import OracleMonitor
        om = OracleMonitor(db=None)
        result = await om.check_proposals()
        assert result == []


# ============================================================
# Reconciliation (P2B-06 / P6-01)
# ============================================================
class TestReconciliation:
    def test_instantiation(self):
        from base_engine.portfolio.reconciliation import PositionReconciler
        pr = PositionReconciler(db=None)
        assert not pr._initialized

    @pytest.mark.asyncio
    async def test_reconcile_no_db(self):
        from base_engine.portfolio.reconciliation import PositionReconciler
        pr = PositionReconciler(db=None)
        result = await pr.reconcile()
        assert result == []


# ============================================================
# Tax Logger (P6-03)
# ============================================================
class TestTaxLogger:
    def test_instantiation(self):
        from base_engine.data.tax_logger import TaxLogger
        tl = TaxLogger(db=None)
        assert tl.db is None

    @pytest.mark.asyncio
    async def test_log_transaction_no_db(self):
        from base_engine.data.tax_logger import TaxLogger
        tl = TaxLogger(db=None)
        # Should not raise
        await tl.log_transaction("m1", "BUY", 10, 0.5)

    @pytest.mark.asyncio
    async def test_export_csv_no_db(self):
        from base_engine.data.tax_logger import TaxLogger
        tl = TaxLogger(db=None)
        result = await tl.export_csv(2025)
        assert result == ""


# ============================================================
# AdverseSelectionTracker persist (P2A-10)
# ============================================================
class TestAdverseSelectionPersist:
    @pytest.mark.asyncio
    async def test_persist_no_db(self):
        from base_engine.analysis.game_theory import AdverseSelectionTracker
        tracker = AdverseSelectionTracker(db=None)
        tracker.record_fill("m1", "YES", 0.5, datetime.now(timezone.utc), source_bot="test")
        count = await tracker.persist_fills_to_db()
        assert count == 0

    def test_record_fill_has_source_bot(self):
        from base_engine.analysis.game_theory import AdverseSelectionTracker
        tracker = AdverseSelectionTracker(db=None)
        tracker.record_fill("m1", "YES", 0.5, datetime.now(timezone.utc), source_bot="ensemble")
        assert tracker.fill_history[-1]["source_bot"] == "ensemble"


# ============================================================
# BaseEngine wiring smoke test
# ============================================================
class TestBaseEngineWiring:
    def test_elevation_attributes_exist(self):
        """Verify all new attributes are declared in BaseEngine.__init__."""
        from base_engine.base_engine import BaseEngine
        be = BaseEngine()
        assert hasattr(be, 'multi_kill_switch')
        assert hasattr(be, 'correlation_risk')
        assert hasattr(be, 'tunable_config')
        assert hasattr(be, 'config_tuner')
        assert hasattr(be, 'market_impact')
        assert hasattr(be, 'spread_decomposer')
        assert hasattr(be, 'counterparty_classifier')
        assert hasattr(be, 'position_reconciler')
        assert hasattr(be, 'tax_logger')
        assert hasattr(be, 'oracle_monitor')
        assert hasattr(be, 'kalshi_client')
        assert hasattr(be, 'secret_manager')
        # All should be None before init()
        assert be.multi_kill_switch is None
        assert be.correlation_risk is None
