"""
A/B Testing Framework
=====================
Test strategy variations in production.
Compare performance of different strategy configurations.
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from collections import defaultdict
from structlog import get_logger
from base_engine.data.database import Database, PerformanceRecord

logger = get_logger()


class ABTest:
    """A/B test configuration"""
    def __init__(
        self,
        test_id: str,
        test_name: str,
        variant_a: Dict[str, Any],
        variant_b: Dict[str, Any],
        allocation: float = 0.5  # 50% to each variant
    ):
        self.test_id = test_id
        self.test_name = test_name
        self.variant_a = variant_a
        self.variant_b = variant_b
        self.allocation = allocation
        self.start_time = datetime.now(timezone.utc)
        self.active = True
        self.results: Dict[str, Dict] = {
            "A": {"trades": 0, "profit": 0.0, "wins": 0},
            "B": {"trades": 0, "profit": 0.0, "wins": 0}
        }


class ABTestingFramework:
    """
    A/B testing framework for strategy variations.
    """
    
    def __init__(self, db: Optional[Database] = None):
        self.db = db
        self.active_tests: Dict[str, ABTest] = {}
        self.test_history: List[ABTest] = []
    
    def create_test(
        self,
        test_name: str,
        variant_a: Dict[str, Any],
        variant_b: Dict[str, Any],
        allocation: float = 0.5
    ) -> str:
        """
        Create a new A/B test.
        
        Args:
            test_name: Name of the test
            variant_a: Configuration for variant A
            variant_b: Configuration for variant B
            allocation: Fraction of traffic to variant A (0.0-1.0)
        
        Returns:
            Test ID
        """
        import uuid
        test_id = str(uuid.uuid4())
        
        test = ABTest(
            test_id=test_id,
            test_name=test_name,
            variant_a=variant_a,
            variant_b=variant_b,
            allocation=allocation
        )
        
        self.active_tests[test_id] = test
        
        logger.info(
            f"A/B test created",
            test_id=test_id,
            test_name=test_name,
            allocation=allocation
        )
        
        return test_id
    
    def assign_variant(self, test_id: str, trade_id: str) -> str:
        """
        Assign a trade to a variant (A or B).
        
        Args:
            test_id: Test ID
            trade_id: Trade ID (used for consistent assignment)
        
        Returns:
            "A" or "B"
        """
        test = self.active_tests.get(test_id)
        if not test or not test.active:
            return "A"  # Default to A if test not found
        
        # Use hash of trade_id for consistent assignment
        import hashlib
        hash_val = int(hashlib.md5(trade_id.encode()).hexdigest(), 16)
        assignment = "A" if (hash_val % 100) < (test.allocation * 100) else "B"
        
        return assignment
    
    def get_variant_config(self, test_id: str, variant: str) -> Optional[Dict]:
        """Get configuration for a variant"""
        test = self.active_tests.get(test_id)
        if not test:
            return None
        
        if variant == "A":
            return test.variant_a
        elif variant == "B":
            return test.variant_b
        
        return None
    
    async def record_trade_result(
        self,
        test_id: str,
        variant: str,
        trade_id: str,
        profit: float,
        was_winner: bool
    ):
        """Record trade result for a variant"""
        test = self.active_tests.get(test_id)
        if not test:
            return
        
        if variant not in ["A", "B"]:
            return
        
        test.results[variant]["trades"] += 1
        test.results[variant]["profit"] += profit
        if was_winner:
            test.results[variant]["wins"] += 1
        
        logger.debug(
            f"A/B test result recorded",
            test_id=test_id,
            variant=variant,
            profit=profit
        )
    
    async def get_test_results(self, test_id: str) -> Dict:
        """
        Get results for a test.
        
        Returns:
            Dict with results for both variants
        """
        test = self.active_tests.get(test_id)
        if not test:
            return {"error": "Test not found"}
        
        results_a = test.results["A"]
        results_b = test.results["B"]
        
        # Calculate metrics
        def calc_metrics(r: Dict) -> Dict:
            return {
                "trades": r["trades"],
                "profit": r["profit"],
                "wins": r["wins"],
                "win_rate": r["wins"] / r["trades"] if r["trades"] > 0 else 0.0,
                "avg_profit": r["profit"] / r["trades"] if r["trades"] > 0 else 0.0
            }
        
        metrics_a = calc_metrics(results_a)
        metrics_b = calc_metrics(results_b)
        
        # Determine winner
        winner = None
        if metrics_a["trades"] > 10 and metrics_b["trades"] > 10:  # Minimum sample size
            if metrics_a["profit"] > metrics_b["profit"]:
                winner = "A"
            elif metrics_b["profit"] > metrics_a["profit"]:
                winner = "B"
        
        return {
            "test_id": test_id,
            "test_name": test.test_name,
            "start_time": test.start_time.isoformat(),
            "active": test.active,
            "variant_a": {
                "config": test.variant_a,
                "metrics": metrics_a
            },
            "variant_b": {
                "config": test.variant_b,
                "metrics": metrics_b
            },
            "winner": winner,
            "difference": {
                "profit_diff": metrics_a["profit"] - metrics_b["profit"],
                "win_rate_diff": metrics_a["win_rate"] - metrics_b["win_rate"]
            }
        }
    
    def stop_test(self, test_id: str):
        """Stop an active test"""
        test = self.active_tests.get(test_id)
        if not test:
            return
        
        test.active = False
        self.test_history.append(test)
        del self.active_tests[test_id]
        
        logger.info(f"A/B test stopped: {test_id}")
    
    def get_active_tests(self) -> List[str]:
        """Get list of active test IDs"""
        return list(self.active_tests.keys())
