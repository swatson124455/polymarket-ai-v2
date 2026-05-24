"""
Meta-Learning (Learning to Learn) - System learns which learning approaches work best.

Features:
- Learn optimal hyperparameters
- Learn best feature combinations
- Learn optimal model ensembles
- Adaptive learning strategies
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from structlog import get_logger
from bots.weather.engine.config.settings import settings

logger = get_logger()


class MetaLearner:
    """
    Meta-learning system.
    
    Learns:
    - Which learning approaches work best
    - Optimal hyperparameters
    - Best feature combinations
    - Optimal model ensembles
    """
    
    def __init__(self):
        self.learning_strategies: Dict[str, Dict[str, Any]] = {}
        self.hyperparameter_history: List[Dict[str, Any]] = []
        self.best_configurations: Dict[str, Dict[str, Any]] = {}
    
    async def learn_optimal_hyperparameters(
        self,
        model_type: str,
        performance_history: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Learn optimal hyperparameters from performance history.
        
        Args:
            model_type: Type of model
            performance_history: Historical performance with different hyperparameters
        
        Returns:
            Optimal hyperparameters
        """
        logger.info(f"Learning optimal hyperparameters for {model_type}")
        
        if not performance_history:
            return {
                "model_type": model_type,
                "optimal_params": {},
                "message": "No performance history available"
            }
        
        # Find best performing configuration
        best_config = max(
            performance_history,
            key=lambda x: x.get("performance_score", 0.0)
        )
        
        optimal_params = best_config.get("hyperparameters", {})
        
        self.best_configurations[model_type] = {
            "hyperparameters": optimal_params,
            "performance_score": best_config.get("performance_score", 0.0),
            "learned_at": datetime.now(timezone.utc).isoformat()
        }
        
        logger.info(
            f"Learned optimal hyperparameters for {model_type}",
            params=optimal_params
        )
        
        return {
            "model_type": model_type,
            "optimal_params": optimal_params,
            "performance_score": best_config.get("performance_score", 0.0)
        }
    
    async def learn_best_features(
        self,
        feature_performance: Dict[str, float]
    ) -> List[str]:
        """
        Learn which features work best.

        Args:
            feature_performance: Dictionary of feature names to performance scores

        Returns:
            List of best feature names
        """
        min_importance = getattr(settings, "FEATURE_IMPORTANCE_MIN_THRESHOLD", 0.01)

        # Sort features by performance
        sorted_features = sorted(
            feature_performance.items(),
            key=lambda x: x[1],
            reverse=True
        )

        # Select features above configurable threshold
        above_threshold = [
            name for name, score in sorted_features
            if score > min_importance
        ]

        # Guarantee at least 5 features to prevent degenerate models
        if len(above_threshold) < 5 and len(sorted_features) >= 5:
            above_threshold = [name for name, _ in sorted_features[:5]]

        best_features = above_threshold if above_threshold else [name for name, _ in sorted_features]

        logger.info(
            "Learned best features: %d selected (threshold=%.4f)",
            len(best_features), min_importance,
            features=best_features[:10],
        )

        return best_features
    
    MIN_RESOLVED_FOR_BLEND = 30
    BLEND_EMA_CURRENT = 0.7
    BLEND_EMA_LEARNED = 0.3
    BLEND_MIN = 0.3
    BLEND_MAX = 0.9

    async def learn_ensemble_blend(
        self,
        db: Any,
        current_blend: float,
        n: int = 50,
    ) -> float:
        """
        Learn ensemble vs learning blend from recent prediction_log. When ensemble_pred/learning_conf are stored,
        grid-search over [0.4, 0.5, 0.6, 0.7] and pick blend with lowest Brier; then EMA smoothing. Otherwise fallback to current.
        """
        if db is None:
            return current_blend
        try:
            if not hasattr(db, "get_recent_resolved_for_blend"):
                return current_blend
            rows = await db.get_recent_resolved_for_blend(n)
            with_components = [r for r in rows if r.get("ensemble_pred") is not None and r.get("learning_conf") is not None]
            if len(with_components) < self.MIN_RESOLVED_FOR_BLEND:
                return current_blend
            grid = [0.4, 0.5, 0.6, 0.7]
            best_blend = current_blend
            best_brier = float("inf")
            for b in grid:
                brier = 0.0
                for r in with_components:
                    pred = b * r["ensemble_pred"] + (1.0 - b) * r["learning_conf"]
                    brier += (pred - r["outcome"]) ** 2
                brier /= len(with_components)
                if brier < best_brier:
                    best_brier = brier
                    best_blend = b
            learned = max(self.BLEND_MIN, min(self.BLEND_MAX, best_blend))
            new_blend = self.BLEND_EMA_CURRENT * current_blend + self.BLEND_EMA_LEARNED * learned
            return max(self.BLEND_MIN, min(self.BLEND_MAX, new_blend))
        except Exception as e:
            logger.debug("learn_ensemble_blend failed: %s", e)
            return current_blend

    async def learn_optimal_ensemble(
        self,
        model_performances: Dict[str, Dict[str, float]]
    ) -> Dict[str, Any]:
        """
        Learn optimal model ensemble weights.
        
        Args:
            model_performances: Dictionary of model names to performance metrics
        
        Returns:
            Optimal ensemble configuration
        """
        logger.info("Learning optimal ensemble configuration")
        
        # Calculate weights based on performance
        total_performance = sum(
            perf.get("accuracy", 0.0) + perf.get("sharpe_ratio", 0.0)
            for perf in model_performances.values()
        )
        
        ensemble_weights = {}
        for model_name, perf in model_performances.items():
            model_score = perf.get("accuracy", 0.0) + perf.get("sharpe_ratio", 0.0)
            weight = model_score / total_performance if total_performance > 0 else 0.0
            ensemble_weights[model_name] = weight
        
        # Normalize weights
        total_weight = sum(ensemble_weights.values())
        if total_weight > 0:
            ensemble_weights = {
                name: weight / total_weight
                for name, weight in ensemble_weights.items()
            }
        
        logger.info(
            "Learned optimal ensemble weights",
            weights=ensemble_weights
        )
        
        return {
            "ensemble_weights": ensemble_weights,
            "models": list(model_performances.keys()),
            "learned_at": datetime.now(timezone.utc).isoformat()
        }
    
    def get_best_configuration(self, model_type: str) -> Optional[Dict[str, Any]]:
        """Get best learned configuration for a model type."""
        return self.best_configurations.get(model_type)
    
    def update_learning_strategy(
        self,
        strategy_name: str,
        performance: float
    ):
        """Update learning strategy performance."""
        if strategy_name not in self.learning_strategies:
            self.learning_strategies[strategy_name] = {
                "performance_history": [],
                "avg_performance": 0.0
            }
        
        strategy = self.learning_strategies[strategy_name]
        strategy["performance_history"].append(performance)
        
        # Keep only recent history
        if len(strategy["performance_history"]) > 100:
            strategy["performance_history"].pop(0)
        
        # Update average
        strategy["avg_performance"] = (
            sum(strategy["performance_history"]) / len(strategy["performance_history"])
        )
    
    def get_best_learning_strategy(self) -> Optional[str]:
        """Get best performing learning strategy."""
        if not self.learning_strategies:
            return None
        
        best_strategy = max(
            self.learning_strategies.items(),
            key=lambda x: x[1]["avg_performance"]
        )
        
        return best_strategy[0]
