"""
RLVR probability estimator — same interface as LLMProbabilityEstimator.

Loads a quantized RLVR model for batch inference across markets.
Falls back gracefully when model is not available.
"""
from __future__ import annotations
from typing import Optional
from structlog import get_logger

logger = get_logger()


class RLVRProbabilityEstimator:
    """
    Probability estimator using a locally-running RLVR-trained model.

    Same interface as LLMProbabilityEstimator so it plugs into the
    prediction ensemble. The cost advantage is massive: a 14B model
    on consumer GPU at ~1/30th the cost of API calls.
    """

    def __init__(self, model_path: str = "", ensemble_runs: int = 7):
        self._model_path = model_path
        self._ensemble_runs = ensemble_runs
        self._inference = None
        self._loaded = False

        if model_path:
            self._try_load()

    def _try_load(self) -> None:
        """Attempt to load the RLVR model."""
        try:
            from bots.weather.engine.base_engine.ml.rlvr.inference import RLVRInference
            from bots.weather.engine.base_engine.ml.rlvr.training_config import RLVRTrainingConfig
            config = RLVRTrainingConfig(ensemble_runs=self._ensemble_runs)
            self._inference = RLVRInference(config=config)
            self._loaded = self._inference.load_model(self._model_path)
        except ImportError:
            logger.debug("RLVR dependencies not available (transformers/torch)")
        except Exception as e:
            logger.debug("RLVR model load failed: %s", e)

    @property
    def is_available(self) -> bool:
        return self._loaded and self._inference is not None

    async def estimate_probability(
        self,
        market_id: str = "",
        question: str = "",
        current_price: float = 0.5,
        category: str = "",
        **kwargs,
    ) -> Optional[float]:
        """
        Estimate probability using the RLVR model.

        Args:
            market_id: Market identifier.
            question: The prediction question text.
            current_price: Current market price (for context).
            category: Market category (for context).

        Returns:
            Probability [0, 1] or None if estimation fails.
        """
        if not self.is_available:
            return None

        context = f"Current market price: {current_price:.3f}"
        if category:
            context += f"\nCategory: {category}"

        try:
            prob = await self._inference.predict(question, context=context)
            if prob is not None:
                logger.debug(
                    "RLVR estimate: market=%s prob=%.3f price=%.3f",
                    market_id, prob, current_price,
                )
            return prob
        except Exception as e:
            logger.debug("RLVR estimation failed: %s", e)
            return None
