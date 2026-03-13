"""
Extremized Geometric Mean of Odds — principled probability aggregation.

Reference: Satopää et al. 2014, used in IARPA ACE Tournament.

Formula:
1. Convert each probability p_i to odds: o_i = p_i / (1 - p_i)
2. Geometric mean of odds: gm = (prod o_i)^(1/n)
3. Extremize: gm_ext = gm^d  (d > 1 pushes away from 0.5)
4. Convert back: result = gm_ext / (1 + gm_ext)

d ∈ [1.0, 2.5]: 1.0 = no extremization, 2.5 = strong push toward certainty.
Default d=1.5 is conservative (IARPA ACE range: 1.5-2.5).
"""
from __future__ import annotations

import numpy as np


def extremized_geometric_mean(
    probabilities: list[float],
    weights: list[float] | None = None,
    d: float = 1.5,
    clip_min: float = 0.02,
    clip_max: float = 0.98,
) -> float:
    """Aggregate probability estimates via extremized geometric mean of odds.

    Parameters
    ----------
    probabilities : list[float]
        Individual probability estimates in (0, 1).
    weights : list[float] | None
        Optional weights (must sum to 1). If *None*, equal weighting.
    d : float
        Extremization parameter. 1.0 = plain geometric mean of odds,
        >1 pushes result away from 0.5.
    clip_min, clip_max : float
        Input probabilities are clipped to [clip_min, clip_max] before
        conversion to odds (prevents log(0)).

    Returns
    -------
    float
        Aggregated probability clipped to [0.05, 0.95].
    """
    if not probabilities:
        return 0.5

    probs = np.clip(probabilities, clip_min, clip_max)

    if len(probs) == 1:
        return float(np.clip(probs[0], 0.05, 0.95))

    # Convert to log-odds for numerical stability
    log_odds = np.log(probs / (1.0 - probs))

    # Weighted geometric mean in log-space
    if weights is not None:
        w = np.asarray(weights, dtype=np.float64)
        w = w / w.sum()  # normalise for safety
        gm_log_odds = float(np.dot(w, log_odds))
    else:
        gm_log_odds = float(np.mean(log_odds))

    # Extremize
    gm_log_odds *= d

    # Convert back to probability via sigmoid
    result = 1.0 / (1.0 + np.exp(-gm_log_odds))

    return float(np.clip(result, 0.05, 0.95))
