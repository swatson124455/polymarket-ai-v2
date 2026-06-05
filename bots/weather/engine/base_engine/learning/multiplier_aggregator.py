"""
MultiplierAggregator — Prevents multiplicative stacking of confidence/sizing multipliers.

Collects all named multiplier inputs, computes their product, applies a composite clamp,
and logs each component for debugging. Replaces unbounded `for m in mults: confidence *= m`
patterns throughout the codebase.

Safety: Composite clamp [0.3, 2.0] prevents extreme values from multiple suppressors
(crushing to near-zero) or multiple boosters (inflating past 1.0).
"""
from typing import Dict, Tuple
from structlog import get_logger

logger = get_logger()

# Default composite clamp range — prevents extreme multiplier stacking
DEFAULT_MIN_PRODUCT = 0.3
DEFAULT_MAX_PRODUCT = 2.0


class MultiplierAggregator:
    """
    Collects named multiplier contributions, computes their product,
    and clamps the result to prevent runaway confidence/sizing swings.

    Usage:
        agg = MultiplierAggregator()
        agg.add("signal", 1.15)
        agg.add("flow", 0.85)
        agg.add("category", 1.08)
        product = agg.compute()  # → clamped product
        final_confidence = base_confidence * product
    """

    __slots__ = ("_factors", "_min_product", "_max_product")

    def __init__(
        self,
        min_product: float = DEFAULT_MIN_PRODUCT,
        max_product: float = DEFAULT_MAX_PRODUCT,
    ):
        self._factors: Dict[str, float] = {}
        self._min_product = min_product
        self._max_product = max_product

    def add(self, name: str, multiplier: float) -> "MultiplierAggregator":
        """Register a named multiplier. Neutral (1.0) multipliers are tracked but don't change product."""
        self._factors[name] = float(multiplier)
        return self

    def compute(self, log: bool = True) -> float:
        """
        Compute the clamped product of all registered multipliers.

        Returns:
            float: The composite multiplier, clamped to [min_product, max_product].
        """
        if not self._factors:
            return 1.0

        raw_product = 1.0
        for mult in self._factors.values():
            raw_product *= mult

        clamped = max(self._min_product, min(self._max_product, raw_product))

        if log and (clamped != raw_product or len(self._factors) >= 3):
            # Build compact repr: only show non-neutral multipliers
            non_neutral = {k: round(v, 3) for k, v in self._factors.items() if abs(v - 1.0) > 0.001}
            if non_neutral:
                logger.debug(
                    "MultiplierAggregator: %s → product=%.3f%s",
                    non_neutral,
                    raw_product,
                    f" (clamped to {clamped:.3f})" if clamped != raw_product else "",
                )

        return clamped

    def compute_pair(self, log: bool = True) -> Tuple[float, float]:
        """Like compute() but also returns the raw (unclamped) product for logging.

        Returns:
            (clamped_product, raw_product)
        """
        if not self._factors:
            return 1.0, 1.0

        raw_product = 1.0
        for mult in self._factors.values():
            raw_product *= mult

        clamped = max(self._min_product, min(self._max_product, raw_product))

        if log and clamped != raw_product:
            non_neutral = {k: round(v, 3) for k, v in self._factors.items() if abs(v - 1.0) > 0.001}
            logger.info(
                "MultiplierAggregator CLAMPED: %s → raw=%.3f clamped=%.3f",
                non_neutral, raw_product, clamped,
            )

        return clamped, raw_product

    @property
    def factors(self) -> Dict[str, float]:
        """Read-only access to registered factors (for debugging/logging)."""
        return dict(self._factors)

    def reset(self) -> None:
        """Clear all registered factors for reuse."""
        self._factors.clear()

    def __repr__(self) -> str:
        return f"MultiplierAggregator(factors={len(self._factors)}, product={self.compute(log=False):.3f})"
