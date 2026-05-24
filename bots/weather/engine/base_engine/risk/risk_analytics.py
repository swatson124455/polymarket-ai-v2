"""
Risk Analytics Engine (#46) - VaR, drawdowns, portfolio risk.

Calculate Value-at-Risk and drawdown metrics for positions/trades.
"""
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone, timedelta
from structlog import get_logger

logger = get_logger()


class RiskAnalytics:
    """
    VaR and drawdown analytics for portfolio/trade history.
    """

    def __init__(self, confidence: float = 0.95):
        self.confidence = confidence

    def max_drawdown(self, equity_curve: List[float]) -> float:
        """Peak-to-trough decline (fraction)."""
        if len(equity_curve) < 2:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for v in equity_curve:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return round(max_dd, 4)

    def var_historical(self, pnl_list: List[float], confidence: Optional[float] = None) -> float:
        """
        Historical VaR: (1 - confidence) quantile of PnL (losses positive).
        Returns positive number = max loss at confidence level.
        """
        if not pnl_list:
            return 0.0
        conf = confidence or self.confidence
        losses = [-x for x in pnl_list if x < 0]
        if not losses:
            return 0.0
        losses.sort()
        idx = max(0, int((1 - conf) * len(losses)) - 1)
        return round(losses[idx], 4)

    def var_from_trades(
        self,
        trades: List[Dict[str, Any]],
        pnl_key: str = "pnl",
        confidence: Optional[float] = None,
    ) -> float:
        """VaR from list of trade dicts with pnl_key."""
        pnls = []
        for t in trades:
            v = t.get(pnl_key)
            if v is not None:
                pnls.append(float(v))
        return self.var_historical(pnls, confidence)

    def drawdown_from_equity(self, equity_curve: List[float]) -> List[float]:
        """Per-step drawdown (fraction from running peak)."""
        if len(equity_curve) < 2:
            return [0.0] * len(equity_curve)
        out = []
        peak = equity_curve[0]
        for v in equity_curve:
            if v > peak:
                peak = v
            out.append((peak - v) / peak if peak > 0 else 0.0)
        return out

    def summary(
        self,
        trades: List[Dict[str, Any]],
        pnl_key: str = "pnl",
        initial_equity: float = 10000.0,
    ) -> Dict[str, Any]:
        """Return VaR, max drawdown, and basic stats from trade list."""
        pnls = [float(t[pnl_key]) for t in trades if t.get(pnl_key) is not None]
        if not pnls:
            return {"var_95": 0.0, "max_drawdown": 0.0, "total_pnl": 0.0, "count": 0}
        equity = initial_equity
        curve = [equity]
        for p in pnls:
            equity += p
            curve.append(equity)
        return {
            "var_95": self.var_historical(pnls, 0.95),
            "max_drawdown": self.max_drawdown(curve),
            "total_pnl": sum(pnls),
            "count": len(pnls),
        }
