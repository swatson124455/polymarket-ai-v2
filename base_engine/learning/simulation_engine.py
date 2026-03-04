import numpy as np
import math
from typing import Dict, List, Optional
from datetime import datetime, timedelta, timezone
from structlog import get_logger
from base_engine.data.database import Database
from base_engine.learning.learning_engine import LearningEngine
from base_engine.utils.validation import validate_price, validate_numeric
from config.settings import settings

logger = get_logger()


class SimulationEngine:
    def __init__(self, db: Database, learning_engine: LearningEngine):
        self.db = db
        self.learning_engine = learning_engine
    
    async def run_monte_carlo_simulation(
        self,
        market_id: str,
        token_id: str,
        price: float,
        iterations: int = None
    ) -> Dict:
        if iterations is None:
            iterations = settings.SIMULATION_ITERATIONS
        
        price = validate_price(price, "price")
        iterations = int(validate_numeric(iterations, "iterations", min_val=1, max_val=10000000))
        
        logger.info(f"Running Monte Carlo simulation: {market_id} ({iterations} iterations)")
        
        outcomes = []
        for _ in range(iterations):
            simulated_price = self._simulate_price_movement(price)
            outcome = 1.0 if simulated_price > price else 0.0
            outcomes.append(outcome)
        
        outcomes_array = np.array(outcomes)
        
        win_probability = float(np.mean(outcomes_array))
        if math.isnan(win_probability) or math.isinf(win_probability):
            logger.warning("Win probability is NaN/Infinity, using 0.5")
            win_probability = 0.5
        
        confidence_intervals = {
            "5th_percentile": float(np.percentile(outcomes_array, 5)),
            "25th_percentile": float(np.percentile(outcomes_array, 25)),
            "50th_percentile": float(np.percentile(outcomes_array, 50)),
            "75th_percentile": float(np.percentile(outcomes_array, 75)),
            "95th_percentile": float(np.percentile(outcomes_array, 95))
        }
        
        for key, value in confidence_intervals.items():
            if math.isnan(value) or math.isinf(value):
                confidence_intervals[key] = 0.0
        
        return {
            "market_id": market_id,
            "token_id": token_id,
            "current_price": price,
            "win_probability": win_probability,
            "confidence_intervals": confidence_intervals,
            "iterations": iterations,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
    
    def _simulate_price_movement(self, current_price: float) -> float:
        current_price = validate_price(current_price, "current_price")
        
        volatility = 0.1
        drift = 0.0
        
        random_shock = np.random.normal(drift, volatility)
        new_price = current_price * (1 + random_shock)
        
        result = max(0.0, min(1.0, new_price))
        if math.isnan(result) or math.isinf(result):
            logger.warning(f"Simulated price is NaN/Infinity, using current_price {current_price}")
            return current_price
        
        return result
    
    async def simulate_portfolio_strategy(
        self,
        strategy_config: Dict,
        time_horizon_days: int = 30,
        iterations: int = None
    ) -> Dict:
        if iterations is None:
            iterations = settings.SIMULATION_ITERATIONS
        
        time_horizon_days = validate_numeric(time_horizon_days, "time_horizon_days", min_val=1, max_val=3650)
        iterations = validate_numeric(iterations, "iterations", min_val=1, max_val=10000000)
        
        logger.info(f"Simulating portfolio strategy ({iterations} iterations, {time_horizon_days} days)")
        
        portfolio_values = []
        
        for _ in range(iterations):
            portfolio_value = 10000.0
            
            for day in range(time_horizon_days):
                daily_return = np.random.normal(0.001, 0.02)
                portfolio_value *= (1 + daily_return)
                
                if math.isnan(portfolio_value) or math.isinf(portfolio_value) or portfolio_value < 0:
                    logger.warning(f"Invalid portfolio value {portfolio_value} at day {day}, resetting to 10000")
                    portfolio_value = 10000.0
            
            portfolio_values.append(portfolio_value)
        
        portfolio_array = np.array(portfolio_values)
        
        mean_val = float(np.mean(portfolio_array))
        median_val = float(np.median(portfolio_array))
        std_val = float(np.std(portfolio_array))
        min_val = float(np.min(portfolio_array))
        max_val = float(np.max(portfolio_array))
        
        if any(math.isnan(v) or math.isinf(v) for v in [mean_val, median_val, std_val, min_val, max_val]):
            logger.warning("Portfolio simulation produced NaN/Infinity values, using defaults")
            mean_val = 10000.0
            median_val = 10000.0
            std_val = 0.0
            min_val = 10000.0
            max_val = 10000.0
        
        percentiles = {
            "5th": float(np.percentile(portfolio_array, 5)),
            "25th": float(np.percentile(portfolio_array, 25)),
            "50th": float(np.percentile(portfolio_array, 50)),
            "75th": float(np.percentile(portfolio_array, 75)),
            "95th": float(np.percentile(portfolio_array, 95))
        }
        
        for key, value in percentiles.items():
            if math.isnan(value) or math.isinf(value):
                percentiles[key] = 10000.0
        
        return {
            "initial_value": 10000.0,
            "mean_final_value": mean_val,
            "median_final_value": median_val,
            "std_final_value": std_val,
            "min_final_value": min_val,
            "max_final_value": max_val,
            "percentiles": percentiles,
            "iterations": iterations,
            "time_horizon_days": time_horizon_days
        }
    
    async def learn_from_simulation(
        self,
        simulation_result: Dict,
        actual_outcome: Optional[float] = None
    ):
        if actual_outcome is None:
            return
        
        predicted_probability = simulation_result["win_probability"]
        error = abs(predicted_probability - actual_outcome)
        
        logger.info(f"Learning from simulation: error={error:.4f}")
        
        await self.learning_engine.update_simulation_confidence(
            market_id=simulation_result["market_id"],
            predicted_prob=predicted_probability,
            actual_outcome=actual_outcome,
            error=error
        )
