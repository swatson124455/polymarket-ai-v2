"""
B5: Closing Line Value (CLV) tracking for EsportsBot v2.

CLV = model_prob - implied_closing_prob (Pinnacle).
Positive CLV means the model had an edge over the sharpest market.

Uses Shin's method to strip overround from Pinnacle closing odds,
producing fair implied probabilities for comparison.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def odds_to_implied(odds_a: float, odds_b: float) -> Tuple[float, float]:
    """
    Convert decimal odds to implied probabilities using Shin's method.

    Falls back to simple normalization if shin package not available.

    Args:
        odds_a: Decimal odds for team A (e.g., 1.85).
        odds_b: Decimal odds for team B (e.g., 2.05).

    Returns:
        (prob_a, prob_b) — fair implied probabilities summing to ~1.0.
    """
    if odds_a <= 1.0 or odds_b <= 1.0:
        return 0.5, 0.5

    try:
        import shin
        probs = shin.calculate_implied_probabilities([odds_a, odds_b])
        return probs[0], probs[1]
    except ImportError:
        # Simple normalization fallback
        raw_a = 1.0 / odds_a
        raw_b = 1.0 / odds_b
        total = raw_a + raw_b
        return raw_a / total, raw_b / total


def compute_clv_single(
    model_prob: float,
    pinnacle_odds_a: Optional[float],
    pinnacle_odds_b: Optional[float],
) -> Optional[float]:
    """
    Compute CLV for a single prediction.

    Args:
        model_prob: Model's P(team_a wins).
        pinnacle_odds_a: Pinnacle closing decimal odds for team A.
        pinnacle_odds_b: Pinnacle closing decimal odds for team B.

    Returns:
        CLV (model_prob - pinnacle_implied_prob) or None if no odds.
    """
    if pinnacle_odds_a is None or pinnacle_odds_b is None:
        return None
    if pinnacle_odds_a <= 1.0 or pinnacle_odds_b <= 1.0:
        return None

    pin_prob_a, _ = odds_to_implied(pinnacle_odds_a, pinnacle_odds_b)
    return model_prob - pin_prob_a


def enrich_with_clv(
    predictions: List[dict],
    odds_lookup: Optional[Dict[str, Tuple[float, float]]] = None,
) -> List[dict]:
    """
    Add CLV fields to prediction records.

    Args:
        predictions: List of prediction dicts (must have 'match_id', 'p_model').
        odds_lookup: Dict mapping match_id -> (pinnacle_odds_a, pinnacle_odds_b).
                     If None, uses 'pinnacle_odds_a'/'pinnacle_odds_b' from records.

    Returns:
        Same predictions with added 'pinnacle_prob', 'clv' fields.
    """
    for pred in predictions:
        match_id = pred.get("match_id")
        if odds_lookup and match_id in odds_lookup:
            odds_a, odds_b = odds_lookup[match_id]
        else:
            odds_a = pred.get("pinnacle_odds_a")
            odds_b = pred.get("pinnacle_odds_b")

        if odds_a and odds_b and odds_a > 1.0 and odds_b > 1.0:
            pin_a, pin_b = odds_to_implied(odds_a, odds_b)
            pred["pinnacle_prob"] = pin_a
            pred["clv"] = pred["p_model"] - pin_a
        else:
            pred["pinnacle_prob"] = None
            pred["clv"] = None

    return predictions
