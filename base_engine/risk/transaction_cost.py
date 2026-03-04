"""
Transaction cost model for Polymarket.
Estimates fees, slippage, gas. Use to filter fee-negative trades and dynamic edge thresholds.
"""
from typing import Optional


def _get_settings():
    try:
        from config.settings import settings
        return settings
    except ImportError:
        return None


class TransactionCostModel:
    """
    Estimates total transaction cost for a trade.
    Polymarket: maker ~0%, taker ~1-2%, gas on Polygon, slippage by size vs depth.
    """

    def __init__(
        self,
        taker_fee_bps: Optional[int] = None,
        maker_fee_bps: Optional[int] = None,
        gas_cost_usd: Optional[float] = None,
        fixed_slippage_bps: Optional[int] = None,
    ):
        s = _get_settings()
        self.taker_fee_bps = taker_fee_bps if taker_fee_bps is not None else (getattr(s, "TAKER_FEE_BPS", 150) if s else 150)
        self.maker_fee_bps = maker_fee_bps if maker_fee_bps is not None else (getattr(s, "MAKER_FEE_BPS", 0) if s else 0)
        self.gas_cost_usd = gas_cost_usd if gas_cost_usd is not None else (getattr(s, "GAS_COST_USD", 0.01) if s else 0.01)
        self.fixed_slippage_bps = fixed_slippage_bps if fixed_slippage_bps is not None else (getattr(s, "FIXED_SLIPPAGE_BPS", 50) if s else 50)

    def estimate_cost(
        self,
        order_size: float,
        market_volume_24h: float = 0,
        order_type: str = "market",
    ) -> float:
        """
        Estimate total transaction cost for a trade.
        Returns cost in USD (fee + slippage + gas).
        """
        if order_size <= 0:
            return 0.0
        fee_bps = self.taker_fee_bps if order_type == "market" else self.maker_fee_bps
        fee = order_size * fee_bps / 10000
        slippage = order_size * self.fixed_slippage_bps / 10000
        if market_volume_24h > 0:
            impact = (order_size / market_volume_24h) * 0.1
            slippage = order_size * min(impact, 0.05)
        return fee + slippage + self.gas_cost_usd

    def min_edge_for_profitability(
        self,
        order_size: float,
        market_volume_24h: float = 0,
    ) -> float:
        """
        Minimum edge (as fraction) required for a trade to be profitable after costs.
        Polymarket resolution is FREE (no exit fee) — only pay entry cost, not round-trip.
        Previous code used cost * 2 (round-trip), but exit via resolution costs nothing.
        """
        if order_size <= 0:
            return 0.0
        cost = self.estimate_cost(order_size, market_volume_24h)
        # One-way cost: resolution is free on Polymarket, only pay entry fees
        return cost / order_size
