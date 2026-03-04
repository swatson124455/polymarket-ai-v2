"""
Kyle's Lambda Market Impact Estimation (P3-02).

Estimates market impact coefficient lambda from fill_analysis data.
Optimal trade size = edge / (2 * lambda). In thin markets, lambda is high
so you should trade smaller to avoid moving price against yourself.

Dependencies: statsmodels (for OLS regression).
"""
from typing import Dict, Any, Optional
from structlog import get_logger

logger = get_logger()

# Conservative default: assume high impact when data is insufficient
DEFAULT_LAMBDA = 0.5
MIN_FILLS_FOR_REGRESSION = 20


class MarketImpactEstimator:
    """Estimate Kyle's lambda (price impact per unit traded) from fill_analysis data."""

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._cache: Dict[str, float] = {}

    async def estimate_kyle_lambda(self, market_id: str) -> float:
        """
        Regress price_change on order_size from fill_analysis table.
        Returns lambda coefficient (higher = more impact = trade smaller).
        Falls back to DEFAULT_LAMBDA when insufficient data.
        """
        if market_id in self._cache:
            return self._cache[market_id]

        if not self.db or not getattr(self.db, "session_factory", None):
            return DEFAULT_LAMBDA

        try:
            from sqlalchemy import text
            async with self.db.get_session() as session:
                r = await session.execute(text("""
                    SELECT fill_price, adverse_move_30s
                    FROM fill_analysis
                    WHERE market_id = :mid
                      AND adverse_move_30s IS NOT NULL
                    ORDER BY fill_time DESC
                    LIMIT 200
                """), {"mid": market_id})
                rows = r.fetchall()

            if len(rows) < MIN_FILLS_FOR_REGRESSION:
                return DEFAULT_LAMBDA

            prices = [float(r[0]) for r in rows]
            impacts = [float(r[1]) for r in rows]

            try:
                import statsmodels.api as sm
                import numpy as np
                X = sm.add_constant(np.array(prices))
                y = np.array(impacts)
                model = sm.OLS(y, X).fit()
                lam = abs(model.params[1]) if len(model.params) > 1 else DEFAULT_LAMBDA
            except ImportError:
                # statsmodels not installed: simple ratio fallback
                import numpy as np
                avg_price = np.mean(prices)
                avg_impact = np.mean(np.abs(impacts))
                lam = avg_impact / max(avg_price, 0.01) if avg_price > 0 else DEFAULT_LAMBDA

            lam = max(0.001, min(lam, 5.0))  # clamp to sane range
            self._cache[market_id] = lam
            logger.info("Kyle lambda estimated", market_id=market_id, lambda_val=round(lam, 4), n_fills=len(rows))
            return lam

        except Exception as e:
            logger.debug("Kyle lambda estimation failed: %s", e)
            return DEFAULT_LAMBDA

    async def estimate_sqrt_impact(self, market_id: str) -> float:
        """
        Square-root market impact model: impact ~ sigma * sqrt(price).
        More realistic for large orders — impact grows sub-linearly with size.
        Returns sigma coefficient. Falls back to DEFAULT_LAMBDA if insufficient data.
        """
        cache_key = f"sqrt_{market_id}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not self.db or not getattr(self.db, "session_factory", None):
            return DEFAULT_LAMBDA

        try:
            from sqlalchemy import text
            import numpy as np
            async with self.db.get_session() as session:
                r = await session.execute(text("""
                    SELECT fill_price, adverse_move_30s
                    FROM fill_analysis
                    WHERE market_id = :mid
                      AND adverse_move_30s IS NOT NULL
                    ORDER BY fill_time DESC
                    LIMIT 200
                """), {"mid": market_id})
                rows = r.fetchall()

            if len(rows) < MIN_FILLS_FOR_REGRESSION:
                return DEFAULT_LAMBDA

            prices = np.array([float(r[0]) for r in rows])
            impacts = np.abs(np.array([float(r[1]) for r in rows]))

            sqrt_prices = np.sqrt(prices)
            sigma = np.sum(impacts * sqrt_prices) / max(np.sum(sqrt_prices ** 2), 1e-10)
            sigma = max(0.001, min(sigma, 5.0))

            self._cache[cache_key] = sigma
            logger.info("Sqrt impact estimated", market_id=market_id, sigma=round(sigma, 4), n_fills=len(rows))
            return sigma

        except Exception as e:
            logger.debug("Sqrt impact estimation failed: %s", e)
            return DEFAULT_LAMBDA

    def kyle_optimal_size(
        self,
        edge: float,
        lambda_estimate: float,
        max_position: float,
    ) -> float:
        """
        Optimal trade size = edge / (2 * lambda), capped by max_position.
        Edge is the predicted probability minus market price (signed).
        """
        if lambda_estimate <= 0:
            return max_position
        optimal = abs(edge) / (2.0 * lambda_estimate)
        return min(optimal, max_position)

    def sqrt_optimal_size(
        self,
        edge: float,
        sigma: float,
        max_position: float,
    ) -> float:
        """
        Optimal size using square-root impact: size = (edge / (3 * sigma))^2.
        Sub-linear impact means larger optimal sizes vs linear Kyle model.
        """
        if sigma <= 0:
            return max_position
        optimal = (abs(edge) / (3.0 * sigma)) ** 2
        return min(optimal, max_position)
