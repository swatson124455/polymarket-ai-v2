"""
Correlation-Aware Risk Management with CVaR (P4-01).

Standard VaR assumes continuous distributions and independent positions.
Prediction markets have binary payoffs and correlated positions.
CVaR (Conditional Value at Risk) measures average tail loss.

Dependencies: scipy, numpy.
"""
import numpy as np
from typing import Dict, Any, Optional, List, Tuple
from structlog import get_logger

logger = get_logger()


class CorrelationRiskManager:
    """CVaR-based risk management with cross-market correlation awareness."""

    def __init__(self, db: Optional[Any] = None):
        self.db = db
        self._correlation_matrix: Optional[np.ndarray] = None
        self._market_ids: List[str] = []

    async def compute_correlation_matrix(self, market_ids: List[str], lookback_days: int = 30) -> np.ndarray:
        """
        Compute correlation matrix from price history for given markets.
        Returns N x N correlation matrix.
        """
        if not self.db or not getattr(self.db, "session_factory", None) or not market_ids:
            n = len(market_ids) if market_ids else 1
            return np.eye(n)

        try:
            from sqlalchemy import text
            price_series: Dict[str, List[float]] = {mid: [] for mid in market_ids}

            async with self.db.get_session() as session:
                for mid in market_ids:
                    r = await session.execute(text("""
                        SELECT price FROM market_prices
                        WHERE market_id = :mid
                          AND timestamp > NOW() - INTERVAL ':days days'
                        ORDER BY timestamp
                        LIMIT 500
                    """.replace(":days", str(lookback_days))), {"mid": mid})
                    rows = r.fetchall()
                    price_series[mid] = [float(row[0]) for row in rows]

            # Align series to same length (truncate to shortest)
            min_len = min(len(v) for v in price_series.values()) if price_series else 0
            if min_len < 10:
                return np.eye(len(market_ids))

            matrix = np.array([price_series[mid][:min_len] for mid in market_ids])
            # Compute returns correlation
            returns = np.diff(matrix, axis=1)
            if returns.shape[1] < 5:
                return np.eye(len(market_ids))

            corr = np.corrcoef(returns)
            corr = np.nan_to_num(corr, nan=0.0)
            np.fill_diagonal(corr, 1.0)

            self._correlation_matrix = corr
            self._market_ids = list(market_ids)
            return corr

        except Exception as e:
            logger.debug("Correlation matrix computation failed: %s", e)
            return np.eye(len(market_ids))

    def compute_cvar(
        self,
        positions: List[Dict[str, Any]],
        confidence_level: float = 0.95,
        n_simulations: int = 2000,
    ) -> Dict[str, Any]:
        """
        Compute CVaR (Expected Shortfall) for the portfolio.

        Args:
            positions: list of {market_id, side, size, price, predicted_prob}
            confidence_level: VaR confidence level (0.95 = 95%)
            n_simulations: Monte Carlo simulations

        Returns:
            var: Value at Risk at given confidence
            cvar: Conditional VaR (avg loss beyond VaR)
            max_loss: worst-case scenario loss
        """
        if not positions:
            return {"var": 0.0, "cvar": 0.0, "max_loss": 0.0}

        n = len(positions)
        # Binary outcome simulation using correlation structure
        corr = self._correlation_matrix if self._correlation_matrix is not None and self._correlation_matrix.shape[0] == n else np.eye(n)

        try:
            # Generate correlated uniform samples via Gaussian copula
            from scipy.stats import norm
            mean = np.zeros(n)
            L = np.linalg.cholesky(np.clip(corr, -1, 1) + np.eye(n) * 0.001)
            z = np.random.standard_normal((n_simulations, n)) @ L.T
            u = norm.cdf(z)  # correlated uniforms

            pnl_scenarios = np.zeros(n_simulations)
            for i, pos in enumerate(positions):
                prob = pos.get("predicted_prob", 0.5)
                side = pos.get("side", "YES").upper()
                size = pos.get("size", 0)
                price = pos.get("price", 0.5)

                # Binary outcome: resolve YES with probability = prob
                resolves_yes = u[:, i] < prob

                if side == "YES":
                    # Win (1 - price) * size if YES, lose price * size if NO
                    pnl = np.where(resolves_yes, (1 - price) * size, -price * size)
                else:
                    pnl = np.where(resolves_yes, -price * size, (1 - price) * size)

                pnl_scenarios += pnl

            # Sort losses (negative PnL)
            sorted_pnl = np.sort(pnl_scenarios)
            var_idx = int((1 - confidence_level) * n_simulations)
            var_value = -sorted_pnl[max(var_idx, 0)]
            cvar_value = -np.mean(sorted_pnl[:max(var_idx, 1)])
            max_loss = -sorted_pnl[0]

            return {
                "var": round(float(var_value), 2),
                "cvar": round(float(cvar_value), 2),
                "max_loss": round(float(max_loss), 2),
                "n_positions": n,
                "n_simulations": n_simulations,
            }

        except ImportError:
            # scipy not available: simple worst-case
            total_exposure = sum(p.get("size", 0) * p.get("price", 0.5) for p in positions)
            return {"var": total_exposure * 0.3, "cvar": total_exposure * 0.5, "max_loss": total_exposure}
        except Exception as e:
            logger.debug("CVaR computation failed: %s", e)
            total_exposure = sum(p.get("size", 0) * p.get("price", 0.5) for p in positions)
            return {"var": total_exposure * 0.3, "cvar": total_exposure * 0.5, "max_loss": total_exposure}

    def compute_marginal_cvar(
        self,
        existing_positions: List[Dict[str, Any]],
        new_position: Dict[str, Any],
        confidence_level: float = 0.95,
    ) -> float:
        """
        Compute the marginal CVaR impact of adding a new position.
        Returns the increase in CVaR from adding the position.
        """
        cvar_before = self.compute_cvar(existing_positions, confidence_level)
        cvar_after = self.compute_cvar(existing_positions + [new_position], confidence_level)
        return cvar_after["cvar"] - cvar_before["cvar"]

    def compute_stress_scenarios(self, positions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Predefined stress scenarios for portfolio.
        Returns list of {scenario, loss, description}.
        """
        scenarios = []

        # Scenario 1: All political markets resolve against us
        political_loss = sum(
            p.get("size", 0) * p.get("price", 0.5)
            for p in positions
            if "politic" in str(p.get("category", "")).lower()
        )
        scenarios.append({
            "scenario": "all_political_against",
            "loss": political_loss,
            "description": "All political markets resolve against our positions",
        })

        # Scenario 2: All crypto markets drop
        crypto_loss = sum(
            p.get("size", 0) * p.get("price", 0.5)
            for p in positions
            if "crypto" in str(p.get("category", "")).lower()
        )
        scenarios.append({
            "scenario": "crypto_crash",
            "loss": crypto_loss,
            "description": "All crypto-related markets resolve against us",
        })

        # Scenario 3: Total loss (worst case)
        total_loss = sum(p.get("size", 0) * p.get("price", 0.5) for p in positions)
        scenarios.append({
            "scenario": "total_loss",
            "loss": total_loss,
            "description": "Every position resolves against us (maximum loss)",
        })

        return scenarios
