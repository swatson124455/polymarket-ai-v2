"""
Chronos-2 price trajectory forecaster (Tier 3C).

Feeds market price history into Amazon's Chronos-2 foundation model for
time-series forecasting. Returns quantile predictions (10th, 50th, 90th)
for future price trajectory, used as an additional signal multiplier in
WeatherBot's prediction pipeline.

Chronos-2 dominates every major time-series benchmark, handles covariates
natively, and excels at short time series (<100 data points — typical for
prediction markets).

Dependencies: chronos-forecasting, torch (optional — graceful fallback).
"""
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from structlog import get_logger

logger = get_logger()

# Lazy-loaded to avoid import cost when torch not installed
_chronos_pipeline = None
_chronos_available: Optional[bool] = None


def _check_chronos() -> bool:
    """Check if chronos-forecasting + torch are available."""
    global _chronos_available
    if _chronos_available is not None:
        return _chronos_available
    try:
        import torch  # noqa: F401
        import chronos  # noqa: F401
        _chronos_available = True
    except ImportError:
        _chronos_available = False
    return _chronos_available


def _get_pipeline():
    """Lazy-init the Chronos pipeline (loads model on first call)."""
    global _chronos_pipeline
    if _chronos_pipeline is not None:
        return _chronos_pipeline
    if not _check_chronos():
        return None
    try:
        import torch
        from chronos import ChronosPipeline

        model_name = os.getenv("CHRONOS_MODEL", "amazon/chronos-t5-small")
        device = "cuda" if torch.cuda.is_available() else "cpu"
        _chronos_pipeline = ChronosPipeline.from_pretrained(
            model_name,
            device_map=device,
            torch_dtype=torch.float32,
        )
        logger.info("Chronos pipeline loaded", model=model_name, device=device)
        return _chronos_pipeline
    except Exception as e:
        logger.debug("Chronos pipeline init failed (non-fatal): %s", e)
        _chronos_available = False
        return None


class ChronosForecaster:
    """Price trajectory forecaster using Chronos-2 foundation model.

    Integration: Used as a signal multiplier in WeatherBot's prediction
    pipeline, similar to _signals_mult and _flow_mult. NOT a replacement
    for the ensemble — adds one more signal source.
    """

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._cache: Dict[str, Dict] = {}
        self._cache_ttl = int(os.getenv("CHRONOS_CACHE_TTL", "1800"))  # 30min default
        self._forecast_horizon = int(os.getenv("CHRONOS_HORIZON", "7"))  # days ahead
        self._min_history = int(os.getenv("CHRONOS_MIN_HISTORY", "10"))  # min data points

    @property
    def is_available(self) -> bool:
        return _check_chronos()

    async def get_price_history(self, market_id: str, days: int = 30) -> Optional[List[float]]:
        """Fetch daily price snapshots from price_history or paper_trades."""
        if not self.db or not self.db.session_factory:
            return None
        try:
            from sqlalchemy import text
            async with self.db.get_session() as sess:
                # Try price_history table first (hourly snapshots)
                result = await sess.execute(
                    text("""
                        SELECT price FROM price_history
                        WHERE market_id = :mid
                        AND recorded_at >= NOW() - MAKE_INTERVAL(days => :days)
                        ORDER BY recorded_at ASC
                    """),
                    {"mid": market_id, "days": int(days)},
                )
                rows = result.fetchall()
                if rows and len(rows) >= self._min_history:
                    return [float(r[0]) for r in rows]

                # Fallback: reconstruct from paper_trades entry prices
                result = await sess.execute(
                    text("""
                        SELECT entry_price, created_at FROM paper_trades
                        WHERE market_id = :mid
                        ORDER BY created_at ASC
                    """),
                    {"mid": market_id},
                )
                rows = result.fetchall()
                if rows and len(rows) >= self._min_history:
                    return [float(r[0]) for r in rows]

        except Exception as e:
            logger.debug("Chronos price history fetch failed: %s", e)
        return None

    async def forecast(
        self,
        market_id: str,
        current_price: float,
        price_history: Optional[List[float]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Generate quantile forecasts for market price trajectory.

        Returns:
            dict with keys:
                - median_forecast: predicted price at horizon
                - p10_forecast: 10th percentile (pessimistic)
                - p90_forecast: 90th percentile (optimistic)
                - trend_signal: +1 (rising), 0 (flat), -1 (falling)
                - confidence_width: p90 - p10 (narrower = more confident)
        """
        # Check cache
        cache_key = f"chronos:{market_id}"
        cached = self._cache.get(cache_key)
        if cached:
            age = (datetime.now(timezone.utc) - cached["ts"]).total_seconds()
            if age < self._cache_ttl:
                return cached["result"]

        pipeline = _get_pipeline()
        if pipeline is None:
            return None

        # Get price history if not provided
        if price_history is None:
            price_history = await self.get_price_history(market_id)
        if not price_history or len(price_history) < self._min_history:
            return None

        try:
            import torch
            import numpy as np

            # Prepare tensor
            context = torch.tensor(price_history, dtype=torch.float32).unsqueeze(0)

            # Generate forecasts
            forecast = pipeline.predict(
                context,
                prediction_length=self._forecast_horizon,
                num_samples=20,  # 20 sample paths (lightweight)
            )

            # Extract quantiles
            forecast_np = forecast.numpy()[0]  # (num_samples, horizon)
            p10 = float(np.percentile(forecast_np[:, -1], 10))
            p50 = float(np.median(forecast_np[:, -1]))
            p90 = float(np.percentile(forecast_np[:, -1], 90))

            # Clip to [0, 1] range (prediction market prices)
            p10 = max(0.01, min(0.99, p10))
            p50 = max(0.01, min(0.99, p50))
            p90 = max(0.01, min(0.99, p90))

            # Trend signal
            if p50 > current_price + 0.03:
                trend = 1
            elif p50 < current_price - 0.03:
                trend = -1
            else:
                trend = 0

            result = {
                "median_forecast": round(p50, 4),
                "p10_forecast": round(p10, 4),
                "p90_forecast": round(p90, 4),
                "trend_signal": trend,
                "confidence_width": round(p90 - p10, 4),
                "history_length": len(price_history),
                "horizon_days": self._forecast_horizon,
            }

            self._cache[cache_key] = {"result": result, "ts": datetime.now(timezone.utc)}
            return result

        except Exception as e:
            logger.debug("Chronos forecast failed (non-fatal): %s", e)
            return None

    def get_signal_multiplier(self, forecast: Dict[str, Any], current_price: float) -> float:
        """Convert Chronos forecast into a signal multiplier for prediction pipeline.

        Returns:
            float in [0.8, 1.2]:
                >1.0 = Chronos agrees with (or amplifies) the ensemble prediction
                <1.0 = Chronos disagrees (dampens the prediction)
                1.0 = neutral (no effect)
        """
        if not forecast:
            return 1.0

        trend = forecast.get("trend_signal", 0)
        width = forecast.get("confidence_width", 1.0)

        # Narrow confidence interval + strong trend = strong signal
        # Wide interval = uncertain = dampen toward 1.0
        confidence = max(0.0, 1.0 - width)  # 0 = max uncertainty, 1 = min

        if trend == 0:
            return 1.0

        # Scale: +/-0.2 max adjustment, weighted by confidence
        adjustment = trend * 0.2 * confidence
        return max(0.8, min(1.2, 1.0 + adjustment))
