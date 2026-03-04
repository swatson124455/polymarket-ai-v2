"""
Cross-platform capital tracker.

Tracks available balance across all connected venues, alerts when
capital allocation is imbalanced, and suggests rebalancing amounts.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from structlog import get_logger

from base_engine.exchanges.base_adapter import ExchangeAdapter

logger = get_logger()


@dataclass
class VenueBalance:
    """Balance snapshot for a single venue."""
    platform: str
    available: float
    allocated: float = 0.0  # Capital in open positions

    @property
    def total(self) -> float:
        return self.available + self.allocated

    @property
    def utilization(self) -> float:
        if self.total <= 0:
            return 0.0
        return self.allocated / self.total


@dataclass
class RebalanceSuggestion:
    """Suggested capital transfer between venues."""
    from_platform: str
    to_platform: str
    amount: float
    reason: str


class CapitalTracker:
    """
    Monitors capital allocation across prediction market venues.

    Alerts when one venue is capital-starved while another has excess,
    and suggests rebalancing amounts.
    """

    def __init__(self, adapters: List[ExchangeAdapter], target_allocation: Optional[Dict[str, float]] = None):
        self._adapters = {a.platform_name(): a for a in adapters if a.is_enabled()}
        # Target allocation by platform (weights summing to 1.0)
        # Default: equal allocation across all platforms
        if target_allocation:
            self._target = target_allocation
        else:
            n = max(len(self._adapters), 1)
            self._target = {name: 1.0 / n for name in self._adapters}
        self._last_balances: Dict[str, VenueBalance] = {}

    async def refresh(self) -> Dict[str, VenueBalance]:
        """Fetch balances from all venues in parallel."""
        tasks = {name: adapter.get_balance() for name, adapter in self._adapters.items()}
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        balances: Dict[str, VenueBalance] = {}
        for name, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                logger.debug("CapitalTracker: %s balance fetch failed: %s", name, result)
                balances[name] = VenueBalance(platform=name, available=0.0)
            else:
                balances[name] = VenueBalance(platform=name, available=float(result))

        self._last_balances = balances
        return balances

    @property
    def total_capital(self) -> float:
        return sum(b.total for b in self._last_balances.values())

    def get_rebalance_suggestions(self, threshold_pct: float = 20.0) -> List[RebalanceSuggestion]:
        """
        Suggest rebalancing when actual allocation deviates from target by > threshold_pct.

        Args:
            threshold_pct: Minimum deviation (as % of total) to trigger suggestion.

        Returns:
            List of suggested transfers.
        """
        total = self.total_capital
        if total <= 0:
            return []

        suggestions: List[RebalanceSuggestion] = []
        actual_pct = {name: (b.available / total) * 100 for name, b in self._last_balances.items()}
        target_pct = {name: w * 100 for name, w in self._target.items()}

        over_funded = []
        under_funded = []

        for name in self._last_balances:
            actual = actual_pct.get(name, 0)
            target = target_pct.get(name, 0)
            diff = actual - target
            if diff > threshold_pct:
                over_funded.append((name, diff, diff * total / 100))
            elif diff < -threshold_pct:
                under_funded.append((name, abs(diff), abs(diff) * total / 100))

        over_funded.sort(key=lambda x: x[1], reverse=True)
        under_funded.sort(key=lambda x: x[1], reverse=True)

        for (over_name, over_diff, over_amt), (under_name, under_diff, under_amt) in zip(over_funded, under_funded):
            transfer = min(over_amt, under_amt)
            if transfer > 1.0:  # Don't suggest trivial transfers
                suggestions.append(RebalanceSuggestion(
                    from_platform=over_name,
                    to_platform=under_name,
                    amount=round(transfer, 2),
                    reason=f"{over_name} over-funded by {over_diff:.1f}%, {under_name} under-funded by {under_diff:.1f}%",
                ))

        return suggestions
