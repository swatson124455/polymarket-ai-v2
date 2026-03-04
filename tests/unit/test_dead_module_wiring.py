"""
Unit tests for dead-module wiring: FeatureStore, ErrorTracker,
ModelVersionManager, SentimentAnalyzer wired into base_engine.
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ---------------------------------------------------------------------------
# FeatureStore
# ---------------------------------------------------------------------------
class TestFeatureStoreWiring:
    def test_instantiation(self):
        from base_engine.learning.feature_store import FeatureStore
        mock_db = MagicMock()
        store = FeatureStore(db=mock_db)
        assert store.db is mock_db

    def test_has_required_methods(self):
        from base_engine.learning.feature_store import FeatureStore
        store = FeatureStore(db=MagicMock())
        assert callable(getattr(store, "compute_market_features", None))
        assert callable(getattr(store, "bulk_compute_features", None))

    @pytest.mark.asyncio
    async def test_compute_returns_none_without_session_factory(self):
        from base_engine.learning.feature_store import FeatureStore
        mock_db = MagicMock()
        mock_db.session_factory = None
        store = FeatureStore(db=mock_db)
        result = await store.compute_market_features("test_market")
        assert result is None

    @pytest.mark.asyncio
    async def test_bulk_compute_returns_zero_without_session_factory(self):
        from base_engine.learning.feature_store import FeatureStore
        mock_db = MagicMock()
        mock_db.session_factory = None
        store = FeatureStore(db=mock_db)
        result = await store.bulk_compute_features()
        assert result == 0

    def test_none_db_accepted(self):
        from base_engine.learning.feature_store import FeatureStore
        store = FeatureStore(db=None)
        assert store.db is None


# ---------------------------------------------------------------------------
# ErrorTracker
# ---------------------------------------------------------------------------
class TestErrorTrackerWiring:
    def test_instantiation_no_sentry(self):
        from base_engine.monitoring.error_tracking import ErrorTracker
        tracker = ErrorTracker()
        assert tracker.sentry_initialized is False

    def test_capture_exception_no_crash(self):
        from base_engine.monitoring.error_tracking import ErrorTracker
        tracker = ErrorTracker()
        # Should not raise
        tracker.capture_exception(ValueError("test error"), context={"phase": "test"})

    def test_capture_message_no_crash(self):
        from base_engine.monitoring.error_tracking import ErrorTracker
        tracker = ErrorTracker()
        tracker.capture_message("test message", level="warning")

    def test_init_error_tracking_returns_tracker(self):
        from base_engine.monitoring.error_tracking import init_error_tracking, get_error_tracker
        tracker = init_error_tracking()
        assert tracker is not None
        assert get_error_tracker() is tracker

    def test_global_singleton_consistent(self):
        from base_engine.monitoring.error_tracking import get_error_tracker
        t1 = get_error_tracker()
        t2 = get_error_tracker()
        assert t1 is t2

    def test_capture_exception_with_none_context(self):
        from base_engine.monitoring.error_tracking import ErrorTracker
        tracker = ErrorTracker()
        tracker.capture_exception(RuntimeError("oops"), context=None)

    def test_capture_message_debug_level(self):
        from base_engine.monitoring.error_tracking import ErrorTracker
        tracker = ErrorTracker()
        tracker.capture_message("debug msg", level="debug")


# ---------------------------------------------------------------------------
# ModelVersionManager
# ---------------------------------------------------------------------------
class TestModelVersionManagerWiring:
    def test_instantiation(self):
        from base_engine.learning.model_versioning import ModelVersionManager
        mgr = ModelVersionManager()
        assert mgr.versions == {}
        assert mgr.active_version is None
        assert mgr.production_version is None

    def test_create_version(self):
        from base_engine.learning.model_versioning import ModelVersionManager
        mgr = ModelVersionManager()
        v = mgr.create_version("ensemble")
        assert v.model_type == "ensemble"
        assert v.version_id in mgr.versions

    def test_set_active_version(self):
        from base_engine.learning.model_versioning import ModelVersionManager
        mgr = ModelVersionManager()
        v = mgr.create_version("ensemble")
        assert mgr.set_active_version(v.version_id) is True
        assert mgr.active_version == v.version_id

    def test_set_active_version_unknown(self):
        from base_engine.learning.model_versioning import ModelVersionManager
        mgr = ModelVersionManager()
        assert mgr.set_active_version("nonexistent") is False

    def test_update_performance(self):
        from base_engine.learning.model_versioning import ModelVersionManager
        mgr = ModelVersionManager()
        v = mgr.create_version("rf")
        mgr.update_performance(v.version_id, {"accuracy": 0.75, "brier": 0.20})
        assert mgr.versions[v.version_id].performance_metrics["accuracy"] == 0.75

    def test_list_versions(self):
        from base_engine.learning.model_versioning import ModelVersionManager
        mgr = ModelVersionManager()
        mgr.create_version("rf")
        mgr.create_version("xgb")
        versions = mgr.list_versions()
        assert len(versions) == 2

    def test_get_best_version_empty(self):
        from base_engine.learning.model_versioning import ModelVersionManager
        mgr = ModelVersionManager()
        assert mgr.get_best_version() is None

    def test_get_best_version_with_metrics(self):
        from base_engine.learning.model_versioning import ModelVersionManager
        mgr = ModelVersionManager()
        v1 = mgr.create_version("rf")
        v2 = mgr.create_version("xgb")
        mgr.update_performance(v1.version_id, {"accuracy": 0.6, "sharpe_ratio": 0.5, "win_rate": 0.5})
        mgr.update_performance(v2.version_id, {"accuracy": 0.8, "sharpe_ratio": 0.7, "win_rate": 0.7})
        best = mgr.get_best_version()
        assert best == v2.version_id


# ---------------------------------------------------------------------------
# SentimentAnalyzer
# ---------------------------------------------------------------------------
class TestSentimentAnalyzerWiring:
    def test_instantiation(self):
        from base_engine.sentiment.sentiment_analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        assert analyzer.sentiment_cache == {}
        assert analyzer.cache_ttl == 300

    @pytest.mark.asyncio
    async def test_analyze_basic_neutral(self):
        from base_engine.sentiment.sentiment_analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        result = await analyzer.analyze_market_sentiment(
            market_id="test_market",
            price_data={"current_price": 0.50, "price_change_24h": 0.0},
            volume_data={"current_volume": 100, "avg_volume_24h": 100},
        )
        assert "overall_sentiment" in result
        assert "confidence" in result
        assert result["market_id"] == "test_market"

    @pytest.mark.asyncio
    async def test_analyze_bullish(self):
        from base_engine.sentiment.sentiment_analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        result = await analyzer.analyze_market_sentiment(
            market_id="m1",
            price_data={"current_price": 0.70, "price_change_24h": 0.10},
            volume_data={"current_volume": 2000, "avg_volume_24h": 1000},
        )
        assert result["overall_sentiment"] in ("bullish", "strong_bullish")

    @pytest.mark.asyncio
    async def test_sentiment_caching(self):
        from base_engine.sentiment.sentiment_analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        r1 = await analyzer.analyze_market_sentiment(
            "m1",
            {"current_price": 0.5, "price_change_24h": 0.0},
            {"current_volume": 100, "avg_volume_24h": 100},
        )
        r2 = await analyzer.analyze_market_sentiment(
            "m1",
            {"current_price": 0.5, "price_change_24h": 0.0},
            {"current_volume": 100, "avg_volume_24h": 100},
        )
        assert r1 == r2  # cached

    @pytest.mark.asyncio
    async def test_analyze_with_orderbook(self):
        from base_engine.sentiment.sentiment_analyzer import SentimentAnalyzer
        analyzer = SentimentAnalyzer()
        result = await analyzer.analyze_market_sentiment(
            market_id="m2",
            price_data={"current_price": 0.5, "price_change_24h": 0.0},
            volume_data={"current_volume": 100, "avg_volume_24h": 100},
            orderbook_data={
                "bids": [{"size": 1000}, {"size": 500}],
                "asks": [{"size": 100}],
            },
        )
        assert result["signals"]["orderbook"] is not None


# ---------------------------------------------------------------------------
# BaseBot.get_sentiment()
# ---------------------------------------------------------------------------
class TestBaseBotSentimentHelper:
    def test_base_bot_has_get_sentiment(self):
        from bots.base_bot import BaseBot
        assert hasattr(BaseBot, "get_sentiment")

    @pytest.mark.asyncio
    async def test_get_sentiment_returns_none_without_analyzer(self):
        from bots.base_bot import BaseBot

        mock_engine = MagicMock()
        mock_engine.sentiment_analyzer = None

        class DummyBot(BaseBot):
            async def scan_and_trade(self):
                pass
            async def analyze_opportunity(self, market_data):
                return None

        bot = DummyBot("TestBot", mock_engine)
        result = await bot.get_sentiment("m1", {}, {})
        assert result is None

    @pytest.mark.asyncio
    async def test_get_sentiment_delegates_to_analyzer(self):
        from bots.base_bot import BaseBot
        from base_engine.sentiment.sentiment_analyzer import SentimentAnalyzer

        analyzer = SentimentAnalyzer()
        mock_engine = MagicMock()
        mock_engine.sentiment_analyzer = analyzer

        class DummyBot(BaseBot):
            async def scan_and_trade(self):
                pass
            async def analyze_opportunity(self, market_data):
                return None

        bot = DummyBot("TestBot", mock_engine)
        result = await bot.get_sentiment(
            "m1",
            {"current_price": 0.5, "price_change_24h": 0.0},
            {"current_volume": 100, "avg_volume_24h": 100},
        )
        assert result is not None
        assert result["market_id"] == "m1"


# ---------------------------------------------------------------------------
# Scheduler: model_version_manager param
# ---------------------------------------------------------------------------
class TestSchedulerModelVersioning:
    def test_scheduler_accepts_model_version_manager(self):
        from base_engine.learning.scheduler import LearningScheduler
        from base_engine.learning.model_versioning import ModelVersionManager
        mock_db = MagicMock()
        mock_db.session_factory = MagicMock()
        mgr = ModelVersionManager()
        sched = LearningScheduler(
            db=mock_db,
            learning_engine=MagicMock(),
            prediction_engine=MagicMock(),
            model_version_manager=mgr,
        )
        assert sched.model_version_manager is mgr

    def test_scheduler_backward_compat_without_model_version_manager(self):
        from base_engine.learning.scheduler import LearningScheduler
        mock_db = MagicMock()
        mock_db.session_factory = MagicMock()
        sched = LearningScheduler(
            db=mock_db,
            learning_engine=MagicMock(),
            prediction_engine=MagicMock(),
        )
        assert sched.model_version_manager is None

    def test_scheduler_model_version_manager_defaults_none(self):
        """Ensure no KeyError / TypeError when model_version_manager not passed."""
        from base_engine.learning.scheduler import LearningScheduler
        mock_db = MagicMock()
        mock_db.session_factory = MagicMock()
        sched = LearningScheduler(
            db=mock_db,
            learning_engine=MagicMock(),
            prediction_engine=MagicMock(),
            meta_learner=MagicMock(),
            causal_engine=MagicMock(),
            feature_engineer=MagicMock(),
        )
        assert sched.model_version_manager is None


# ---------------------------------------------------------------------------
# MetricsCollector (Prometheus)
# ---------------------------------------------------------------------------
class TestMetricsCollectorWiring:
    def test_instantiation(self):
        from base_engine.monitoring.metrics_collector import MetricsCollector
        mc = MetricsCollector()
        assert mc.enabled is True

    def test_record_trade_no_crash(self):
        from base_engine.monitoring.metrics_collector import MetricsCollector
        mc = MetricsCollector()
        mc.record_trade("TestBot", "BUY", True, 0.5)

    def test_record_prediction_no_crash(self):
        from base_engine.monitoring.metrics_collector import MetricsCollector
        mc = MetricsCollector()
        mc.record_prediction(0.1)

    def test_record_cache_hit_no_crash(self):
        from base_engine.monitoring.metrics_collector import MetricsCollector
        mc = MetricsCollector()
        mc.record_cache_hit("redis")

    def test_global_singleton(self):
        from base_engine.monitoring.metrics_collector import metrics_collector
        assert metrics_collector is not None
        assert metrics_collector.enabled is True


# ---------------------------------------------------------------------------
# DistributedTracer
# ---------------------------------------------------------------------------
class TestDistributedTracerWiring:
    def test_instantiation(self):
        from base_engine.monitoring.distributed_tracing import DistributedTracer
        tracer = DistributedTracer()
        assert tracer.traces == {}
        assert tracer.active_spans == {}

    def test_start_trace(self):
        from base_engine.monitoring.distributed_tracing import DistributedTracer
        tracer = DistributedTracer()
        span = tracer.start_trace("test", "svc", "op")
        assert span.name == "test"
        assert span.trace_id in tracer.traces

    def test_finish_span(self):
        from base_engine.monitoring.distributed_tracing import DistributedTracer
        tracer = DistributedTracer()
        span = tracer.start_trace("test", "svc", "op")
        tracer.finish_span(span.span_id)
        assert span.span_id not in tracer.active_spans
        assert span.duration_ms is not None

    @pytest.mark.asyncio
    async def test_trace_context_manager(self):
        from base_engine.monitoring.distributed_tracing import DistributedTracer
        tracer = DistributedTracer()
        async with tracer.trace("test", "svc", "op") as span:
            assert span.span_id in tracer.active_spans
        assert span.span_id not in tracer.active_spans

    def test_global_singleton(self):
        from base_engine.monitoring.distributed_tracing import get_tracer
        t1 = get_tracer()
        t2 = get_tracer()
        assert t1 is t2

    def test_find_bottlenecks_empty(self):
        from base_engine.monitoring.distributed_tracing import DistributedTracer
        tracer = DistributedTracer()
        assert tracer.find_bottlenecks("nonexistent") == []


# ---------------------------------------------------------------------------
# QualityMetrics
# ---------------------------------------------------------------------------
class TestQualityMetricsWiring:
    def test_instantiation(self):
        from base_engine.monitoring.quality_metrics import QualityMetrics
        qm = QualityMetrics(db=MagicMock())
        assert qm.db is not None

    def test_none_db_accepted(self):
        from base_engine.monitoring.quality_metrics import QualityMetrics
        qm = QualityMetrics(db=None)
        assert qm.db is None


# ---------------------------------------------------------------------------
# SnapshotManager
# ---------------------------------------------------------------------------
class TestSnapshotManagerWiring:
    def test_instantiation(self):
        from base_engine.monitoring.snapshot import SnapshotManager
        sm = SnapshotManager(db=MagicMock())
        assert sm.db is not None

    def test_none_db_accepted(self):
        from base_engine.monitoring.snapshot import SnapshotManager
        sm = SnapshotManager(db=None)
        assert sm.db is None


# ---------------------------------------------------------------------------
# PipelineGate
# ---------------------------------------------------------------------------
class TestPipelineGateWiring:
    def test_instantiation(self):
        from base_engine.monitoring.pipeline_gate import PipelineGate
        pg = PipelineGate(db=MagicMock())
        assert pg.db is not None

    def test_instantiation_with_alerting(self):
        from base_engine.monitoring.pipeline_gate import PipelineGate
        pg = PipelineGate(db=MagicMock(), alerting=MagicMock())
        assert pg.alerting is not None
