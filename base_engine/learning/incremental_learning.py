"""
Online/Incremental Learning - Models learn from new data without full retraining.

Features:
- Incremental model updates
- Online learning algorithms
- Adaptive learning rates
- Continuous model improvement
- process_resolved_prediction: feed prediction_log outcomes; when batch full, trigger full retrain (C).
"""
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from structlog import get_logger
from base_engine.data.database import Database
from base_engine.learning.learning_engine import LearningEngine
from base_engine.prediction.prediction_engine import PredictionEngine

logger = get_logger()


def _get_incremental_batch_size() -> int:
    try:
        from config.settings import settings
        return int(getattr(settings, "INCREMENTAL_LEARNER_BATCH_SIZE", 100))
    except Exception:
        return 100


class IncrementalLearner:
    """
    Online/incremental learning system.
    
    Updates models incrementally from new data without full retraining.
    When batch is full, _update_prediction_models triggers a full retrain (learn from new outcomes).
    """

    def __init__(
        self,
        learning_engine: LearningEngine,
        prediction_engine: PredictionEngine,
        db: Database,
        update_batch_size: Optional[int] = None,
    ):
        self.learning_engine = learning_engine
        self.prediction_engine = prediction_engine
        self.db = db
        self.update_batch_size = update_batch_size if update_batch_size is not None else _get_incremental_batch_size()
        self.pending_updates: List[Dict[str, Any]] = []

    async def process_resolved_prediction(
        self,
        market_id: str,
        predicted_prob: float,
        resolution: str,
        resolved_at: Optional[datetime] = None,
    ) -> None:
        """Feed one resolved prediction_log row into the learner (C). Outcome 1=YES, 0=NO."""
        outcome = 1.0 if (resolution or "").strip().upper() == "YES" else 0.0
        trade_data = {
            "market_id": market_id,
            "price": predicted_prob,
            "entry_price": predicted_prob,
            "timestamp": resolved_at or datetime.now(timezone.utc),
            "entry_time": resolved_at or datetime.now(timezone.utc),
        }
        await self.process_new_trade(trade_data, actual_outcome=outcome)
    
    async def process_new_trade(
        self,
        trade_data: Dict[str, Any],
        actual_outcome: Optional[float] = None
    ):
        """
        Process a new trade and update models incrementally.
        
        Args:
            trade_data: Trade data dictionary
            actual_outcome: Actual outcome (0.0 or 1.0) if available
        """
        # Add to pending updates
        self.pending_updates.append({
            "trade_data": trade_data,
            "actual_outcome": actual_outcome,
            "timestamp": datetime.now(timezone.utc)
        })
        
        # Update if batch size reached
        if len(self.pending_updates) >= self.update_batch_size:
            await self._incremental_update()
    
    async def _incremental_update(self):
        """Perform incremental model update from pending updates."""
        if not self.pending_updates:
            return
        
        logger.info(f"Performing incremental update with {len(self.pending_updates)} samples")
        
        try:
            # Batch-normalize and update learning engine patterns via learn_from_trades
            to_learn = []
            for update in self.pending_updates:
                trade_data = update["trade_data"]
                actual_outcome = update.get("actual_outcome")
                if actual_outcome is not None and trade_data.get("market_id"):
                    to_learn.append({
                        "market_id": trade_data["market_id"],
                        "entry_price": trade_data.get("entry_price") or trade_data.get("price", 0.5),
                        "pnl": 1.0 if actual_outcome > 0.5 else -1.0,
                        "entry_time": trade_data.get("entry_time") or trade_data.get("timestamp"),
                    })
            if to_learn:
                await self.learning_engine.learn_from_trades(to_learn)
            
            # Update prediction models incrementally
            # This is a placeholder - actual implementation would use
            # online learning algorithms (SGD, incremental XGBoost, etc.)
            await self._update_prediction_models()
            
            # Clear pending updates
            self.pending_updates = []
            
            logger.info("Incremental update completed")
            
        except Exception as e:
            logger.error(f"Incremental update failed: {str(e)}", exc_info=True)
    
    async def _update_prediction_models(self) -> None:
        """
        Update prediction models. When batch is full we trigger a full retrain so new outcomes
        are learned (C: grow from new data in batches without real online learning).
        """
        try:
            await self.prediction_engine.retrain()
            logger.info("Incremental batch full: triggered full retrain")
        except Exception as e:
            logger.warning("Incremental retrain trigger failed: %s", e)
    
    async def force_update(self):
        """Force immediate incremental update even if batch not full."""
        if self.pending_updates:
            await self._incremental_update()
    
    def get_update_stats(self) -> Dict[str, Any]:
        """Get statistics about incremental updates."""
        return {
            "pending_updates": len(self.pending_updates),
            "batch_size": self.update_batch_size,
            "ready_for_update": len(self.pending_updates) >= self.update_batch_size
        }
