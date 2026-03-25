"""
Precipitation Engine — converts ensemble precipitation forecasts into bucket probabilities.

Core math:
  1. Estimate P(rain) from fraction of ensemble members with precip > 0
  2. Fit a Gamma distribution to non-zero ensemble precipitation amounts
  3. For each precipitation bucket, integrate:
     P(bucket) = P(rain) × P(amount in bucket | rain)
     Special case: P(0 inches) bucket = P(no rain)
  4. Compare model probabilities against market prices → compute edges

Data sources:
  - Open-Meteo ensemble precipitation_sum (GEFS 31 + ECMWF IFS 51 + AIFS 51)
  - NWS NDFD probability of precipitation (12h PoP, US only)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from structlog import get_logger

logger = get_logger()

try:
    from scipy.stats import gamma as gamma_dist
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False


@dataclass
class PrecipBucket:
    """A precipitation market bucket (e.g., 'between 3-4 inches')."""

    market_id: str
    token_id: str
    no_token_id: str = ""
    yes_price: float = 0.0
    bucket_type: str = "range"      # range, at_or_below, at_or_higher
    low_bound: Optional[float] = None  # inches or mm
    high_bound: Optional[float] = None
    precip_unit: str = "in"         # "in" (inches) or "mm"


class PrecipitationProbabilityEngine:
    """Convert ensemble precipitation forecasts into bucket probabilities."""

    def compute_bucket_probabilities(
        self,
        ensemble_members: List[float],
        buckets: List[PrecipBucket],
        ndfd_pop: Optional[float] = None,
    ) -> Dict[str, float]:
        """Compute probability for each precipitation bucket.

        Args:
            ensemble_members: Precipitation totals per member (inches or mm).
            buckets: List of PrecipBucket defining market boundaries.
            ndfd_pop: Optional NWS probability of precipitation (0-100 scale)
                      to blend with ensemble-derived rain probability.

        Returns:
            Dict of market_id → model probability.
        """
        if not SCIPY_AVAILABLE or not ensemble_members or not buckets:
            return {}

        n = len(ensemble_members)
        if n < 5:
            return {}

        # Step 1: P(rain) from ensemble
        n_wet = sum(1 for x in ensemble_members if x > 0.01)  # trace threshold
        p_rain_ensemble = n_wet / n

        # Blend with NDFD PoP if available (weighted average)
        if ndfd_pop is not None:
            p_rain_ndfd = ndfd_pop / 100.0
            # NDFD gets 40% weight — it's a model consensus, not raw ensemble
            p_rain = 0.6 * p_rain_ensemble + 0.4 * p_rain_ndfd
        else:
            p_rain = p_rain_ensemble

        p_rain = max(0.001, min(0.999, p_rain))

        # Step 2: Fit Gamma distribution to non-zero members
        wet_members = [x for x in ensemble_members if x > 0.01]

        if len(wet_members) < 3:
            # Too few wet members — use simple empirical probabilities
            return self._empirical_probabilities(ensemble_members, buckets)

        # Gamma MLE via method of moments (fast, robust)
        mean_wet = sum(wet_members) / len(wet_members)
        var_wet = sum((x - mean_wet) ** 2 for x in wet_members) / max(len(wet_members) - 1, 1)

        if var_wet < 1e-6 or mean_wet < 1e-6:
            # Near-zero variance — all members agree
            return self._empirical_probabilities(ensemble_members, buckets)

        # Gamma shape (alpha) and scale (beta)
        alpha = (mean_wet ** 2) / var_wet
        beta = var_wet / mean_wet  # scale parameter

        # Clamp alpha to avoid degenerate distributions
        raw_alpha, raw_beta = alpha, beta
        alpha = max(0.1, min(alpha, 50.0))
        beta = max(0.01, beta)
        if alpha != raw_alpha or beta != raw_beta:
            logger.warning("precip_gamma_clamped", raw_alpha=round(raw_alpha, 4), raw_beta=round(raw_beta, 4), clamped_alpha=round(alpha, 4), clamped_beta=round(beta, 4))

        # Step 3: Integrate over each bucket
        probs: Dict[str, float] = {}
        rv = gamma_dist(a=alpha, scale=beta)

        for bucket in buckets:
            if bucket.bucket_type == "at_or_below":
                if bucket.high_bound is not None:
                    if bucket.high_bound < 0.01:
                        # "0 inches" bucket = P(no rain)
                        prob = 1.0 - p_rain
                    else:
                        # P(precip <= X) = P(dry) + P(rain) × P(amount <= X | rain)
                        prob = (1.0 - p_rain) + p_rain * rv.cdf(bucket.high_bound)
                else:
                    prob = 0.5

            elif bucket.bucket_type == "at_or_higher":
                if bucket.low_bound is not None:
                    # P(precip >= X) = P(rain) × P(amount >= X | rain)
                    prob = p_rain * (1.0 - rv.cdf(bucket.low_bound))
                else:
                    prob = 0.5

            elif bucket.bucket_type == "range":
                if bucket.low_bound is not None and bucket.high_bound is not None:
                    cdf_low = rv.cdf(bucket.low_bound)
                    cdf_high = rv.cdf(bucket.high_bound)
                    prob = p_rain * (cdf_high - cdf_low)
                    # If low_bound is 0, include P(dry) if range starts at 0
                    if bucket.low_bound < 0.01:
                        prob += (1.0 - p_rain)
                else:
                    prob = 0.5
            else:
                prob = 0.5

            # Clamp to valid probability range
            prob = max(0.001, min(0.999, prob))
            probs[bucket.market_id] = round(prob, 6)

        return probs

    def _empirical_probabilities(
        self,
        members: List[float],
        buckets: List[PrecipBucket],
    ) -> Dict[str, float]:
        """Fallback: compute probabilities from raw ensemble member counts."""
        n = len(members)
        if n == 0:
            return {}

        probs: Dict[str, float] = {}
        for bucket in buckets:
            count = 0
            for val in members:
                if bucket.bucket_type == "at_or_below":
                    if bucket.high_bound is not None and val <= bucket.high_bound:
                        count += 1
                elif bucket.bucket_type == "at_or_higher":
                    if bucket.low_bound is not None and val >= bucket.low_bound:
                        count += 1
                elif bucket.bucket_type == "range":
                    if (bucket.low_bound is not None and bucket.high_bound is not None
                            and bucket.low_bound <= val <= bucket.high_bound):
                        count += 1
            prob = max(0.001, min(0.999, count / n))
            probs[bucket.market_id] = round(prob, 6)

        return probs

    def compute_edges(
        self,
        model_probs: Dict[str, float],
        buckets: List[PrecipBucket],
        min_edge: float = 0.08,
    ) -> List[Dict]:
        """Compare model probabilities against market prices to find edges.

        Returns list of trading opportunities with positive edge.
        """
        opportunities = []
        for bucket in buckets:
            model_prob = model_probs.get(bucket.market_id)
            if model_prob is None:
                continue

            market_price = bucket.yes_price
            if market_price <= 0.0 or market_price >= 1.0:
                continue

            # YES edge: model thinks YES is more likely than market
            edge = model_prob - market_price
            if edge > min_edge:
                opportunities.append({
                    "market_id": bucket.market_id,
                    "token_id": bucket.token_id,
                    "side": "YES",
                    "model_prob": model_prob,
                    "price": market_price,
                    "edge": edge,
                    "abs_edge": abs(edge),
                    "confidence": model_prob,
                })

            # NO edge: model thinks NO is more likely
            no_edge = (1.0 - model_prob) - (1.0 - market_price)
            if no_edge > min_edge and bucket.no_token_id:
                opportunities.append({
                    "market_id": bucket.market_id,
                    "token_id": bucket.no_token_id,
                    "side": "NO",
                    "model_prob": 1.0 - model_prob,
                    "price": 1.0 - market_price,
                    "edge": no_edge,
                    "abs_edge": abs(no_edge),
                    "confidence": 1.0 - model_prob,
                })

        return opportunities
