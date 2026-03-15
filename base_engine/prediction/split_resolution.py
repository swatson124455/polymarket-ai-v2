"""
Split (50/50) Resolution Handler for Polymarket Kelly Sizing

Polymarket markets can resolve at 50/50 when UMA voters determine the outcome
is ambiguous or the resolution criteria are unclear. In a 50/50 resolution,
both YES and NO shares settle at $0.50 (50 cents).

This creates a third outcome that standard binary Kelly criterion ignores:

  Standard Kelly (binary):
    EV = p_win * profit_if_win + p_lose * loss_if_lose
    f* = (p * (odds + 1) - 1) / odds * kelly_fraction

  Three-outcome Kelly (with split):
    p_lose = 1 - p_win - p_split
    EV = p_win * (1 - entry) + p_split * (0.50 - entry) + p_lose * (-entry)

    The split outcome returns 0.50 per share regardless of side (YES or NO).
    If entry_price > 0.50, the split outcome is a partial loss.
    If entry_price < 0.50, the split outcome is a partial win.
    If entry_price = 0.50, the split outcome breaks even.

  The adjusted Kelly fraction accounts for this by reducing the edge when
  p_split > 0 and entry_price > 0.50 (the common case, since most positions
  are entered at prices reflecting a directional view).

Frequency: ~0.1% of Polymarket markets resolve 50/50, but when it happens
it affects ALL positions in that market. The feature is OFF by default
(ENABLE_SPLIT_KELLY=false) and should only be enabled after validation.

Env vars:
  ENABLE_SPLIT_KELLY  — "true" to enable, default "false"
  SPLIT_KELLY_DEFAULT_P — base-rate P(split), default 0.001
"""

import os
import logging
import re

logger = logging.getLogger(__name__)


class SplitResolutionHandler:
    """Handles 50/50 (split) market resolution adjustments for Kelly sizing.

    When disabled (default), all methods return standard values so that
    kelly_with_split() produces identical output to the standard Kelly formula.
    """

    def __init__(self, enabled: bool = False):
        env_flag = os.environ.get("ENABLE_SPLIT_KELLY", "false").lower().strip()
        self.enabled = env_flag == "true" if not enabled else enabled

        default_p_str = os.environ.get("SPLIT_KELLY_DEFAULT_P", "0.001")
        try:
            self.default_split_p = float(default_p_str)
        except (ValueError, TypeError):
            self.default_split_p = 0.001

        if self.enabled:
            logger.info(
                "SplitResolutionHandler ENABLED — default_split_p=%.4f",
                self.default_split_p,
            )
        else:
            logger.debug("SplitResolutionHandler disabled (default)")

    def kelly_with_split(
        self,
        p_win: float,
        p_split: float,
        odds: float,
        kelly_fraction: float,
    ) -> float:
        """Compute Kelly bet fraction adjusted for the possibility of 50/50 resolution.

        Args:
            p_win: Probability of winning (the position's side resolves YES).
            p_split: Probability of 50/50 resolution. When 0, returns standard Kelly.
            odds: Decimal odds offered by the market. For a price p, odds = (1-p)/p
                  for YES positions, or p/(1-p) for NO positions.
            kelly_fraction: Fractional Kelly multiplier (e.g. 0.25 for quarter-Kelly).

        Returns:
            Adjusted Kelly fraction (0 to 1 range, pre-multiplied by kelly_fraction).
            Returns 0.0 if the bet has negative or zero expected value.

        When p_split=0 this reduces to the standard binary Kelly formula:
            f* = (p_win * (odds + 1) - 1) / odds * kelly_fraction
        """
        if odds <= 0 or kelly_fraction <= 0:
            return 0.0

        if p_split <= 0.0:
            # Standard binary Kelly — no split adjustment
            edge = p_win * (odds + 1) - 1
            if edge <= 0:
                return 0.0
            return (edge / odds) * kelly_fraction

        # Three-outcome Kelly
        #
        # Entry price implied by odds: entry = 1 / (odds + 1)
        # profit_if_win  = odds  (net gain per unit risked)
        # profit_if_split = (0.50 - entry) / entry = (0.50 * (odds + 1) - 1)
        #   i.e. the return on 1 unit risked when settlement is 0.50
        # loss_if_lose   = -1  (lose entire stake)
        #
        # EV per unit risked = p_win * odds + p_split * split_return + p_lose * (-1)
        # where split_return = 0.50 * (odds + 1) - 1

        entry_price = 1.0 / (odds + 1.0)
        p_lose = max(0.0, 1.0 - p_win - p_split)

        # Net return per unit risked for each outcome
        profit_win = odds  # win: gain odds units per 1 risked
        profit_split = (0.50 - entry_price) / entry_price  # split: partial
        loss_lose = -1.0  # lose: lose entire stake

        ev_per_unit = (
            p_win * profit_win
            + p_split * profit_split
            + p_lose * loss_lose
        )

        if ev_per_unit <= 0:
            return 0.0

        # Optimal Kelly for three outcomes uses the same edge/odds structure
        # but with adjusted edge. We use the approximation:
        #   f* = EV / variance_proxy
        # where variance_proxy uses the squared payoffs weighted by probability.
        #
        # For the standard binary case this simplifies to (p*(b+1)-1)/b.
        # For the three-outcome case we compute:
        #   variance_proxy = p_win * odds^2 + p_split * split_return^2 + p_lose * 1
        #   (since loss = -1, squared = 1)

        variance_proxy = (
            p_win * profit_win ** 2
            + p_split * profit_split ** 2
            + p_lose * loss_lose ** 2
        )

        if variance_proxy <= 0:
            return 0.0

        f_star = ev_per_unit / variance_proxy

        # Clamp to [0, 1] before applying fractional Kelly
        f_star = max(0.0, min(1.0, f_star))

        return f_star * kelly_fraction

    def estimate_split_probability(self, market_data: dict) -> float:
        """Estimate the probability of a 50/50 (split) resolution for a market.

        Args:
            market_data: Dict with market metadata. Recognized keys:
                - uma_dispute_active (bool): Whether a UMA dispute is active
                - resolution_source (str): e.g. "uma", "oracle", "admin"
                - question (str): The market question text

        Returns:
            Estimated P(split) in [0.0, 1.0].
            Returns 0.0 if the feature is disabled.
        """
        if not self.enabled:
            return 0.0

        if not market_data or not isinstance(market_data, dict):
            return self.default_split_p

        # Highest signal: active UMA dispute
        if self.is_dispute_active(market_data):
            # Active disputes have 5-15% split probability depending on signals
            question = market_data.get("question", "")
            if _has_ambiguous_language(question):
                return 0.15
            return 0.05

        # Medium signal: UMA-sourced resolution (disputes possible but not active)
        resolution_source = market_data.get("resolution_source", "")
        if isinstance(resolution_source, str) and resolution_source.lower() == "uma":
            if _has_ambiguous_language(market_data.get("question", "")):
                return 0.02
            return self.default_split_p

        # Low signal: ambiguous question language alone
        question = market_data.get("question", "")
        if _has_ambiguous_language(question):
            return 0.02

        # Default base rate
        return self.default_split_p

    def is_dispute_active(self, market_data: dict) -> bool:
        """Check whether a UMA proposal/dispute is active for this market.

        Args:
            market_data: Dict with market metadata.

        Returns:
            True if a dispute is detected, False otherwise.
            Always returns False if feature is disabled.
        """
        if not self.enabled:
            return False

        if not market_data or not isinstance(market_data, dict):
            return False

        # Direct flag
        if market_data.get("uma_dispute_active") is True:
            return True

        # String variant
        dispute_val = market_data.get("uma_dispute_active")
        if isinstance(dispute_val, str) and dispute_val.lower() in ("true", "1", "yes"):
            return True

        return False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Patterns that suggest ambiguous resolution criteria
_AMBIGUOUS_PATTERNS = re.compile(
    r"(substantially|approximately|around|roughly|significant(ly)?|"
    r"more or less|in the ballpark|could be interpreted)",
    re.IGNORECASE,
)


def _has_ambiguous_language(question: str) -> bool:
    """Return True if the question text contains language suggesting ambiguous resolution."""
    if not question or not isinstance(question, str):
        return False
    return bool(_AMBIGUOUS_PATTERNS.search(question))
