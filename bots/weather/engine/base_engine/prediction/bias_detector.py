"""
Post-training bias detection. Run after every retrain cycle.
Flags potential issues before the model goes live.
"""
from typing import Dict, List, Optional
import numpy as np
from structlog import get_logger

logger = get_logger()


class BiasDetector:
    """
    Post-training bias detection. Run after every retrain cycle.
    """

    @staticmethod
    def check_base_rate_exploitation(
        predictions: np.ndarray,
        y_test: np.ndarray,
    ) -> Optional[str]:
        """
        Does the model just predict the majority class?
        """
        if len(y_test) < 10:
            return None
        base_rate = float(np.mean(y_test))
        prediction_rate = float(np.mean(predictions))
        if abs(prediction_rate - base_rate) < 0.05:
            return (
                "Model prediction rate matches base rate. "
                "May be predicting majority class without learning signal."
            )
        return None

    @staticmethod
    def check_price_parroting(
        predictions: np.ndarray,
        current_prices: np.ndarray,
    ) -> Optional[str]:
        """
        Does the model just output the current price?
        """
        if len(predictions) < 10 or len(current_prices) < 10:
            return None
        corr = np.corrcoef(predictions, current_prices)[0, 1]
        if np.isnan(corr):
            return None
        if corr > 0.95:
            return (
                f"Model predictions correlate {corr:.2f} with current price. "
                "The model may be parroting price instead of finding edge."
            )
        return None

    @classmethod
    def run_checks(
        cls,
        model,
        X_test: np.ndarray,
        y_test: np.ndarray,
        current_prices: Optional[np.ndarray] = None,
        categories: Optional[np.ndarray] = None,
    ) -> List[str]:
        """
        Run all bias checks. Returns list of warning messages.
        """
        warnings: List[str] = []
        try:
            preds = model.predict(X_test)
            pred_proba = model.predict_proba(X_test)[:, 1] if hasattr(model, "predict_proba") else preds
        except Exception as e:
            logger.warning("BiasDetector: predict failed", error=str(e))
            return warnings

        if len(preds) < 10:
            return warnings

        msg = cls.check_base_rate_exploitation(preds, y_test)
        if msg:
            warnings.append(msg)
            logger.warning("BiasDetector: %s", msg)

        if current_prices is not None and len(current_prices) == len(pred_proba):
            msg = cls.check_price_parroting(pred_proba, current_prices)
            if msg:
                warnings.append(msg)
                logger.warning("BiasDetector: %s", msg)

        return warnings
