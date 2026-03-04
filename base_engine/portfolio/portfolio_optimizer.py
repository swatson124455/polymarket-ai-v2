"""
Portfolio Optimization (#42) - suggest optimal market positions.

Mean-variance optimization and Kelly criterion for position sizing.
"""
from typing import Any, Dict, List, Optional
from structlog import get_logger

logger = get_logger()


class PortfolioOptimizer:
    """
    Suggest optimal weights across markets (mean-variance or Kelly).
    """

    def __init__(self, risk_free_rate: float = 0.0):
        self.risk_free_rate = risk_free_rate

    def kelly_fraction(
        self,
        win_prob: float,
        win_loss_ratio: float,
        fraction: float = 1.0,
    ) -> float:
        """
        Kelly criterion: f = (p * b - q) / b where b = win/loss ratio, q = 1-p.
        fraction caps the result (e.g. 0.25 for quarter-Kelly).
        """
        if win_prob <= 0 or win_prob >= 1 or win_loss_ratio <= 0:
            return 0.0
        q = 1.0 - win_prob
        f = (win_prob * win_loss_ratio - q) / win_loss_ratio
        f = max(0.0, min(fraction, f))
        return round(f, 4)

    def suggest_weights_kelly(
        self,
        markets: List[Dict[str, Any]],
        win_probs: Optional[Dict[str, float]] = None,
        win_loss_ratios: Optional[Dict[str, float]] = None,
        max_fraction: float = 0.25,
    ) -> Dict[str, float]:
        """
        Suggest position size (fraction of capital) per market_id using Kelly.
        markets: list of { market_id, ... }; win_probs/win_loss_ratios keyed by market_id.
        """
        weights: Dict[str, float] = {}
        for m in markets:
            mid = m.get("market_id") or m.get("id")
            if not mid:
                continue
            p = (win_probs or {}).get(mid, 0.5)
            b = (win_loss_ratios or {}).get(mid, 1.0)
            f = self.kelly_fraction(p, b, fraction=max_fraction)
            if f > 0:
                weights[mid] = f
        total = sum(weights.values())
        if total > 1.0 and total > 0:
            for k in weights:
                weights[k] = round(weights[k] / total, 4)
        return weights

    def mean_variance_weights(
        self,
        expected_returns: Dict[str, float],
        cov_matrix: Optional[Dict[str, Dict[str, float]]] = None,
        target_return: Optional[float] = None,
    ) -> Dict[str, float]:
        """
        Simple mean-variance: equal weight if no cov; otherwise proportional to Sharpe.
        Full Markowitz would need a solver; this is a lightweight heuristic.
        """
        if not expected_returns:
            return {}
        if not cov_matrix and target_return is None:
            n = len(expected_returns)
            return {k: round(1.0 / n, 4) for k in expected_returns}
        excess = {k: r - self.risk_free_rate for k, r in expected_returns.items()}
        total = sum(max(0, e) for e in excess.values())
        if total <= 0:
            n = len(expected_returns)
            return {k: round(1.0 / n, 4) for k in expected_returns}
        return {k: round(max(0, excess[k]) / total, 4) for k in excess}
