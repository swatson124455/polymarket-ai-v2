"""
Model Versioning & A/B Testing - Version models and A/B test improvements.

Features:
- Model versioning system
- A/B testing framework
- Model performance tracking
- Automatic model selection
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from enum import Enum
from structlog import get_logger

logger = get_logger()


class ModelVersion:
    """Represents a model version."""
    
    def __init__(
        self,
        version_id: str,
        model_type: str,
        created_at: Optional[datetime] = None
    ):
        self.version_id = version_id
        self.model_type = model_type
        self.created_at = created_at or datetime.now(timezone.utc)
        self.performance_metrics: Dict[str, Any] = {}
        self.is_active = False
        self.is_production = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "version_id": self.version_id,
            "model_type": self.model_type,
            "created_at": self.created_at.isoformat(),
            "performance_metrics": self.performance_metrics,
            "is_active": self.is_active,
            "is_production": self.is_production
        }


class ModelVersionManager:
    """
    Model versioning and A/B testing system.
    
    Manages:
    - Model versions
    - A/B testing
    - Performance tracking
    - Model selection
    """
    
    def __init__(self):
        self.versions: Dict[str, ModelVersion] = {}
        self.active_version: Optional[str] = None
        self.production_version: Optional[str] = None
        self.ab_tests: Dict[str, Dict[str, Any]] = {}
    
    def create_version(
        self,
        model_type: str,
        version_id: Optional[str] = None
    ) -> ModelVersion:
        """
        Create a new model version.
        
        Args:
            model_type: Type of model (e.g., "random_forest", "xgboost")
            version_id: Optional version ID (generated if not provided)
        
        Returns:
            Model version
        """
        if version_id is None:
            version_id = f"{model_type}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        
        version = ModelVersion(version_id, model_type)
        self.versions[version_id] = version
        
        logger.info(f"Created model version: {version_id}", model_type=model_type)
        
        return version
    
    def set_active_version(self, version_id: str) -> bool:
        """Set active version for A/B testing."""
        if version_id not in self.versions:
            logger.warning(f"Version {version_id} not found")
            return False
        
        # Deactivate previous active version
        if self.active_version:
            self.versions[self.active_version].is_active = False
        
        # Activate new version
        self.versions[version_id].is_active = True
        self.active_version = version_id
        
        logger.info(f"Set active version: {version_id}")
        return True
    
    def set_production_version(self, version_id: str) -> bool:
        """Set production version."""
        if version_id not in self.versions:
            logger.warning(f"Version {version_id} not found")
            return False
        
        # Deactivate previous production version
        if self.production_version:
            self.versions[self.production_version].is_production = False
        
        # Set new production version
        self.versions[version_id].is_production = True
        self.production_version = version_id
        
        logger.info(f"Set production version: {version_id}")
        return True
    
    def update_performance(
        self,
        version_id: str,
        metrics: Dict[str, Any]
    ):
        """Update performance metrics for a version."""
        if version_id in self.versions:
            self.versions[version_id].performance_metrics.update(metrics)
            logger.debug(f"Updated performance for version {version_id}", metrics=metrics)
    
    def start_ab_test(
        self,
        test_name: str,
        version_a: str,
        version_b: str,
        traffic_split: float = 0.5
    ) -> Dict[str, Any]:
        """
        Start an A/B test.
        
        Args:
            test_name: Test name
            version_a: Version A ID
            version_b: Version B ID
            traffic_split: Traffic split (0.0 to 1.0 for version A)
        
        Returns:
            Test configuration
        """
        if version_a not in self.versions or version_b not in self.versions:
            return {
                "success": False,
                "error": "One or both versions not found"
            }
        
        test_config = {
            "test_name": test_name,
            "version_a": version_a,
            "version_b": version_b,
            "traffic_split": traffic_split,
            "start_time": datetime.now(timezone.utc).isoformat(),
            "status": "active",
            "results": {
                "version_a": {"predictions": 0, "correct": 0, "accuracy": 0.0},
                "version_b": {"predictions": 0, "correct": 0, "accuracy": 0.0}
            }
        }
        
        self.ab_tests[test_name] = test_config
        
        logger.info(f"Started A/B test: {test_name}", version_a=version_a, version_b=version_b)
        
        return test_config
    
    def record_ab_test_result(
        self,
        test_name: str,
        version_id: str,
        prediction: float,
        actual: float,
        correct: bool
    ):
        """Record A/B test result."""
        if test_name not in self.ab_tests:
            return
        
        test = self.ab_tests[test_name]
        
        if version_id == test["version_a"]:
            results = test["results"]["version_a"]
        elif version_id == test["version_b"]:
            results = test["results"]["version_b"]
        else:
            return
        
        results["predictions"] += 1
        if correct:
            results["correct"] += 1
        
        results["accuracy"] = results["correct"] / results["predictions"] if results["predictions"] > 0 else 0.0
    
    def get_ab_test_winner(self, test_name: str) -> Optional[str]:
        """Get winner of A/B test."""
        if test_name not in self.ab_tests:
            return None
        
        test = self.ab_tests[test_name]
        results_a = test["results"]["version_a"]
        results_b = test["results"]["version_b"]
        
        # Need minimum samples
        min_samples = 100
        if results_a["predictions"] < min_samples or results_b["predictions"] < min_samples:
            return None
        
        # Compare accuracy
        if results_a["accuracy"] > results_b["accuracy"] + 0.05:  # 5% improvement
            return test["version_a"]
        elif results_b["accuracy"] > results_a["accuracy"] + 0.05:
            return test["version_b"]
        
        return None  # No clear winner
    
    def get_best_version(self) -> Optional[str]:
        """Get best performing version based on metrics."""
        if not self.versions:
            return None
        
        best_version = None
        best_score = -1.0
        
        for version_id, version in self.versions.items():
            metrics = version.performance_metrics
            
            # Calculate composite score
            accuracy = metrics.get("accuracy", 0.0)
            sharpe = metrics.get("sharpe_ratio", 0.0)
            win_rate = metrics.get("win_rate", 0.0)
            
            score = (accuracy * 0.4 + sharpe * 0.3 + win_rate * 0.3)
            
            if score > best_score:
                best_score = score
                best_version = version_id
        
        return best_version
    
    def list_versions(self) -> List[Dict[str, Any]]:
        """List all model versions."""
        return [v.to_dict() for v in self.versions.values()]
