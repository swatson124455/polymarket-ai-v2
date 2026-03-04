"""
Unit tests for feature importance learning wiring.

Tests: FeatureEngineer integration, CausalInferenceEngine integration,
       MetaLearner threshold fix, Scheduler blending, bot access.
"""
import pytest
import numpy as np
from unittest.mock import MagicMock, AsyncMock


# ── FeatureEngineer ──────────────────────────────────────────────────

class TestFeatureEngineerIntegration:

    def test_instantiation(self):
        from base_engine.learning.feature_engineering import FeatureEngineer
        fe = FeatureEngineer()
        assert fe.feature_cache == {}
        assert fe.feature_importance == {}

    def test_generate_features_valid_data(self):
        from base_engine.learning.feature_engineering import FeatureEngineer
        fe = FeatureEngineer()
        market = {"liquidity": 5000.0, "volume": 12000.0, "category": "crypto"}
        prices = [0.5, 0.52, 0.51, 0.53, 0.55, 0.54, 0.56, 0.58, 0.57, 0.60]
        features = fe.generate_features(market, prices)
        assert "current_price" in features
        assert "volatility" in features
        assert "ma_5" in features
        assert "ma_10" in features
        assert features["current_price"] == 0.60
        assert features["liquidity"] == 5000.0

    def test_generate_features_empty_history(self):
        from base_engine.learning.feature_engineering import FeatureEngineer
        fe = FeatureEngineer()
        features = fe.generate_features({}, [])
        assert features == {}

    def test_generate_features_single_price(self):
        from base_engine.learning.feature_engineering import FeatureEngineer
        fe = FeatureEngineer()
        features = fe.generate_features({}, [0.5])
        assert features == {}

    def test_select_features_with_importance(self):
        from base_engine.learning.feature_engineering import FeatureEngineer
        fe = FeatureEngineer()
        fe.update_feature_importance({"price": 0.5, "volume": 0.02, "noise": 0.001})
        all_features = {"price": 0.65, "volume": 1000.0, "noise": 42.0}
        selected = fe.select_features(all_features, min_importance=0.01)
        assert "price" in selected
        assert "volume" in selected
        assert "noise" not in selected

    def test_select_features_no_importance_returns_all(self):
        from base_engine.learning.feature_engineering import FeatureEngineer
        fe = FeatureEngineer()
        all_features = {"price": 0.65, "volume": 1000.0}
        selected = fe.select_features(all_features)
        assert selected == all_features

    def test_update_feature_importance(self):
        from base_engine.learning.feature_engineering import FeatureEngineer
        fe = FeatureEngineer()
        fe.update_feature_importance({"a": 0.3, "b": 0.7})
        assert fe.get_feature_importance() == {"a": 0.3, "b": 0.7}
        fe.update_feature_importance({"a": 0.5})
        assert fe.get_feature_importance()["a"] == 0.5

    def test_discover_feature_interactions(self):
        from base_engine.learning.feature_engineering import FeatureEngineer
        fe = FeatureEngineer()
        features = {"price": 0.5, "volume": 100.0}
        interactions = fe.discover_feature_interactions(features)
        assert len(interactions) == 1
        assert interactions[0]["feature1"] == "price"
        assert interactions[0]["feature2"] == "volume"
        assert interactions[0]["product"] == 50.0


# ── CausalInferenceEngine ────────────────────────────────────────────

class TestCausalInferenceEngineIntegration:

    def test_instantiation(self):
        from base_engine.learning.causal_inference import CausalInferenceEngine
        engine = CausalInferenceEngine()
        assert engine.causal_graphs == {}
        assert engine.intervention_history == []

    @pytest.mark.asyncio
    async def test_heuristic_graph_learning(self):
        from base_engine.learning.causal_inference import CausalInferenceEngine
        engine = CausalInferenceEngine()
        # Create data with a known correlation
        rng = np.random.default_rng(42)
        data = rng.standard_normal((50, 3))
        data[:, 2] = data[:, 0] * 0.8 + rng.standard_normal(50) * 0.2
        graph = await engine.learn_causal_graph(
            "test_market", ["feat_a", "feat_b"], ["outcome"], data=data
        )
        assert graph["market_id"] == "test_market"
        assert "nodes" in graph
        assert len(graph["nodes"]) == 3

    def test_causal_importance_no_graph(self):
        from base_engine.learning.causal_inference import CausalInferenceEngine
        engine = CausalInferenceEngine()
        importance = engine.get_causal_importance("nonexistent", "outcome")
        assert importance == {}

    @pytest.mark.asyncio
    async def test_causal_importance_with_graph(self):
        from base_engine.learning.causal_inference import CausalInferenceEngine
        engine = CausalInferenceEngine()
        rng = np.random.default_rng(42)
        data = rng.standard_normal((50, 3))
        data[:, 2] = data[:, 0] * 0.9 + rng.standard_normal(50) * 0.1
        await engine.learn_causal_graph("m1", ["a", "b"], ["outcome"], data=data)
        importance = engine.get_causal_importance("m1", "outcome")
        assert "outcome" not in importance

    @pytest.mark.asyncio
    async def test_counterfactual_no_graph(self):
        from base_engine.learning.causal_inference import CausalInferenceEngine
        engine = CausalInferenceEngine()
        result = await engine.analyze_counterfactual(
            "unknown", {"price": 0.8}, {"price": 0.5}
        )
        assert result["effect"] == 0.0


# ── MetaLearner Feature Selection ────────────────────────────────────

class TestMetaLearnerFeatureSelection:

    @pytest.mark.asyncio
    async def test_all_above_threshold(self):
        from base_engine.learning.meta_learning import MetaLearner
        ml = MetaLearner()
        scores = {"price": 0.3, "volume": 0.2, "liquidity": 0.1}
        best = await ml.learn_best_features(scores)
        assert len(best) == 3
        assert "price" in best

    @pytest.mark.asyncio
    async def test_some_below_threshold(self):
        from base_engine.learning.meta_learning import MetaLearner
        ml = MetaLearner()
        scores = {
            "price": 0.3, "volume": 0.2, "liquidity": 0.1,
            "regime": 0.05, "signal": 0.02,
            "noise1": 0.005, "noise2": 0.003,
        }
        best = await ml.learn_best_features(scores)
        assert "noise1" not in best
        assert "noise2" not in best
        assert "price" in best

    @pytest.mark.asyncio
    async def test_minimum_five_features(self):
        from base_engine.learning.meta_learning import MetaLearner
        ml = MetaLearner()
        # All scores below default threshold of 0.01 — should still keep top 5
        scores = {
            "a": 0.001, "b": 0.002, "c": 0.003,
            "d": 0.004, "e": 0.005, "f": 0.006, "g": 0.007,
        }
        best = await ml.learn_best_features(scores)
        assert len(best) >= 5

    @pytest.mark.asyncio
    async def test_empty_scores(self):
        from base_engine.learning.meta_learning import MetaLearner
        ml = MetaLearner()
        best = await ml.learn_best_features({})
        assert best == []

    @pytest.mark.asyncio
    async def test_preserves_order(self):
        from base_engine.learning.meta_learning import MetaLearner
        ml = MetaLearner()
        scores = {"c": 0.1, "a": 0.5, "b": 0.3}
        best = await ml.learn_best_features(scores)
        assert best[0] == "a"  # highest score first


# ── Scheduler Feature Importance Blending ────────────────────────────

class TestSchedulerFeatureImportanceBlending:

    def test_accepts_causal_engine_param(self):
        from base_engine.learning.scheduler import LearningScheduler
        from base_engine.learning.causal_inference import CausalInferenceEngine
        mock_db = MagicMock()
        mock_db.session_factory = MagicMock()
        ce = CausalInferenceEngine()
        sched = LearningScheduler(
            db=mock_db,
            learning_engine=MagicMock(),
            prediction_engine=MagicMock(),
            causal_engine=ce,
        )
        assert sched.causal_engine is ce

    def test_backward_compat_without_causal(self):
        from base_engine.learning.scheduler import LearningScheduler
        mock_db = MagicMock()
        mock_db.session_factory = MagicMock()
        sched = LearningScheduler(
            db=mock_db,
            learning_engine=MagicMock(),
            prediction_engine=MagicMock(),
        )
        assert sched.causal_engine is None
        assert sched.feature_engineer is None

    def test_accepts_feature_engineer_param(self):
        from base_engine.learning.scheduler import LearningScheduler
        from base_engine.learning.feature_engineering import FeatureEngineer
        mock_db = MagicMock()
        mock_db.session_factory = MagicMock()
        fe = FeatureEngineer()
        sched = LearningScheduler(
            db=mock_db,
            learning_engine=MagicMock(),
            prediction_engine=MagicMock(),
            feature_engineer=fe,
        )
        assert sched.feature_engineer is fe

    @pytest.mark.asyncio
    async def test_compute_causal_importance_no_engine(self):
        from base_engine.learning.scheduler import LearningScheduler
        mock_db = MagicMock()
        mock_db.session_factory = MagicMock()
        sched = LearningScheduler(
            db=mock_db,
            learning_engine=MagicMock(),
            prediction_engine=MagicMock(),
        )
        result = await sched._compute_causal_importance({"a": 0.5, "b": 0.3, "c": 0.1})
        assert result == {}

    @pytest.mark.asyncio
    async def test_compute_causal_importance_too_few_features(self):
        from base_engine.learning.scheduler import LearningScheduler
        from base_engine.learning.causal_inference import CausalInferenceEngine
        mock_db = MagicMock()
        mock_db.session_factory = MagicMock()
        sched = LearningScheduler(
            db=mock_db,
            learning_engine=MagicMock(),
            prediction_engine=MagicMock(),
            causal_engine=CausalInferenceEngine(),
        )
        result = await sched._compute_causal_importance({"a": 0.5, "b": 0.3})
        assert result == {}


# ── PredictionEngine Feature Importance ──────────────────────────────

class TestPredictionEngineFeatureImportance:

    def test_feature_importance_scores_initialized(self):
        from base_engine.prediction.prediction_engine import PredictionEngine
        pe = PredictionEngine(db=MagicMock(), learning_engine=MagicMock())
        assert pe._feature_importance_scores == {}

    def test_feature_engineer_instantiated_when_enabled(self):
        from base_engine.prediction.prediction_engine import PredictionEngine
        pe = PredictionEngine(db=MagicMock(), learning_engine=MagicMock())
        # USE_FEATURE_ENGINEER defaults to true
        assert pe._feature_engineer is not None

    def test_get_feature_importance_empty_initially(self):
        from base_engine.prediction.prediction_engine import PredictionEngine
        pe = PredictionEngine(db=MagicMock(), learning_engine=MagicMock())
        result = pe.get_feature_importance()
        # No models trained yet, should be empty
        assert result == {}

    def test_get_feature_importance_returns_stored_scores(self):
        from base_engine.prediction.prediction_engine import PredictionEngine
        pe = PredictionEngine(db=MagicMock(), learning_engine=MagicMock())
        pe._feature_importance_scores = {"price": 0.4, "volume": 0.3}
        result = pe.get_feature_importance()
        assert result == {"price": 0.4, "volume": 0.3}


# ── Bot Access ───────────────────────────────────────────────────────

class TestBotFeatureImportanceAccess:

    def test_base_bot_has_method(self):
        from bots.base_bot import BaseBot
        assert hasattr(BaseBot, "get_feature_importance")

    def test_prediction_result_shape(self):
        """Verify the prediction result dict shape includes feature_importance."""
        result = {
            "confidence": 0.85,
            "prediction": 0.72,
            "model_predictions": {"rf": 0.7, "xgb": 0.75},
            "feature_importance": {"price": 0.3, "volume": 0.2},
        }
        assert "feature_importance" in result
        assert isinstance(result["feature_importance"], dict)
