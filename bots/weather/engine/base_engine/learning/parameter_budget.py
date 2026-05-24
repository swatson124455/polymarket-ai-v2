"""
ParameterBudgetGuard — Prevents multi-loop feedback instability.

After each adaptation cycle, limits the total absolute change across ALL adaptive
parameters to a configurable budget (default 15%). If exceeded, all proposed changes
are scaled proportionally so the total stays within budget.

This prevents the instability that arises when 8+ feedback loops modify behavior
from the same outcome data pool — individual EMA smoothing prevents single-loop
oscillation but not multi-loop interaction effects.
"""
import math
from typing import Dict, Tuple, Optional
from structlog import get_logger

logger = get_logger()

DEFAULT_MAX_TOTAL_CHANGE_PCT = 0.15  # 15% total budget per adaptation cycle


class ParameterBudgetGuard:
    """
    Collects proposed parameter changes, computes total absolute change,
    and scales all changes proportionally if the budget is exceeded.

    Usage:
        guard = ParameterBudgetGuard()
        guard.propose("signal_mult_news", old=1.0, new=1.15)
        guard.propose("flow_mult", old=1.1, new=0.85)
        guard.propose("z_threshold", old=2.0, new=1.8)
        approved = guard.apply()
        # approved = {"signal_mult_news": 1.15, "flow_mult": 0.85, "z_threshold": 1.8}
        # OR if budget exceeded, all changes scaled proportionally
    """

    def __init__(self, max_total_change_pct: float = DEFAULT_MAX_TOTAL_CHANGE_PCT):
        self._max_total_change_pct = max_total_change_pct
        self._proposals: Dict[str, Tuple[float, float]] = {}  # name -> (old, new)

    def propose(self, name: str, old: float, new: float) -> "ParameterBudgetGuard":
        """Register a proposed parameter change."""
        self._proposals[name] = (float(old), float(new))
        return self

    def apply(self) -> Dict[str, float]:
        """
        Apply the budget constraint. Returns dict of {name: approved_new_value}.

        If total absolute change exceeds the budget, all changes are scaled
        proportionally so the total stays within budget.
        """
        if not self._proposals:
            return {}

        # Compute total absolute change as fraction of old values
        total_change = 0.0
        per_param_change: Dict[str, float] = {}  # name -> |delta/old|

        for name, (old, new) in self._proposals.items():
            if old == 0:
                # For zero-valued old params, use absolute change
                pct_change = abs(new) if new != 0 else 0.0
            else:
                pct_change = abs(new - old) / abs(old)
            per_param_change[name] = pct_change
            total_change += pct_change

        # If within budget, approve all changes as-is
        if total_change <= self._max_total_change_pct:
            result = {name: new for name, (old, new) in self._proposals.items()}
            if total_change > 0:
                logger.debug(
                    "ParameterBudgetGuard: %.1f%% total change (within %.0f%% budget, %d params)",
                    total_change * 100, self._max_total_change_pct * 100, len(self._proposals),
                )
            return result

        # Budget exceeded: scale all changes proportionally
        scale_factor = self._max_total_change_pct / max(total_change, 1e-10)
        result: Dict[str, float] = {}
        for name, (old, new) in self._proposals.items():
            delta = new - old
            scaled_delta = delta * scale_factor
            result[name] = old + scaled_delta

        logger.info(
            "ParameterBudgetGuard: %.1f%% requested → scaled to %.0f%% budget (%d params, scale=%.2f)",
            total_change * 100, self._max_total_change_pct * 100,
            len(self._proposals), scale_factor,
        )

        # Log individual parameter changes for debugging
        for name in self._proposals:
            old, proposed = self._proposals[name]
            approved = result[name]
            if abs(proposed - approved) > 0.001:
                logger.debug(
                    "  %s: %.4f → proposed %.4f → approved %.4f",
                    name, old, proposed, approved,
                )

        return result

    def get_total_change_pct(self) -> float:
        """Return the total absolute change as a fraction (for monitoring)."""
        total = 0.0
        for name, (old, new) in self._proposals.items():
            if old == 0:
                total += abs(new) if new != 0 else 0.0
            else:
                total += abs(new - old) / abs(old)
        return total

    def reset(self) -> None:
        """Clear all proposals for reuse."""
        self._proposals.clear()

    def __repr__(self) -> str:
        return (
            f"ParameterBudgetGuard(proposals={len(self._proposals)}, "
            f"total_change={self.get_total_change_pct():.1%}, "
            f"budget={self._max_total_change_pct:.0%})"
        )
