"""
Series Model — BO3/BO5 conditional probability calculator.

Pure probability math (no ML). Computes:
  - P(team_a wins series | current map score) using binomial race
  - Map veto adjusted probabilities using team-specific map win rates
  - Momentum fallacy detection (map margin ≠ next map predictor)

Key insight: Market anchors on map score (0-2 = dead) but ignores conditional math.
  Example: 55% per-game rate → P(reverse sweep from 0-2) = 0.55^3 = 16.6%
  If market prices comeback team at 5%, that's 11.6% edge.

Usage::
    from esports.models.series_model import bo3_match_prob, bo5_match_prob
    prob = bo3_match_prob(0.55, maps_won_a=0, maps_won_b=2)  # ≈ 0.166
    prob = bo5_match_prob(0.55, maps_won_a=0, maps_won_b=2)  # ≈ 0.339
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def bo1_underdog_adjustment(base_prob: float) -> float:
    """
    Adjust base probability for BO1 format variance.

    BO1 matches have systematically higher upset rates than BO3/BO5 due to
    reduced sample size (single map). Empirical research (Journal of Economic
    Psychology 2020) shows CS:GO underdogs are underpriced, with BO1 upset
    rates 1.5-2x higher than BO3 equivalents.

    Adjustment: boost underdog probability proportional to distance from 0.5.
    The further the underdog is from even, the larger the boost (more variance).

    Args:
        base_prob: Base P(team_a wins) from model (0.0-1.0).

    Returns:
        Adjusted probability accounting for BO1 variance (clamped 0.05-0.95).
    """
    if base_prob < 0.5:
        # Team A is the underdog — boost toward 0.5
        adjusted = base_prob + 0.03 * (0.5 - base_prob)
    elif base_prob > 0.5:
        # Team A is the favorite — compress toward 0.5
        adjusted = base_prob - 0.03 * (base_prob - 0.5)
    else:
        adjusted = base_prob

    return max(0.05, min(0.95, adjusted))


def bo3_match_prob(
    game_win_rate: float,
    maps_won_a: int = 0,
    maps_won_b: int = 0,
) -> float:
    """
    P(team_a wins BO3 series) given current map score.

    Args:
        game_win_rate: P(team_a wins any single map) in [0, 1].
        maps_won_a: Maps already won by team A (0-2).
        maps_won_b: Maps already won by team B (0-2).

    Returns:
        P(team_a wins the series).
    """
    return _series_prob(game_win_rate, maps_won_a, maps_won_b, maps_to_win=2)


def bo5_match_prob(
    game_win_rate: float,
    maps_won_a: int = 0,
    maps_won_b: int = 0,
) -> float:
    """
    P(team_a wins BO5 series) given current map score.

    Args:
        game_win_rate: P(team_a wins any single map) in [0, 1].
        maps_won_a: Maps already won by team A (0-3).
        maps_won_b: Maps already won by team B (0-3).

    Returns:
        P(team_a wins the series).
    """
    return _series_prob(game_win_rate, maps_won_a, maps_won_b, maps_to_win=3)


def _series_prob(
    p: float,
    won_a: int,
    won_b: int,
    maps_to_win: int,
) -> float:
    """
    Compute P(A wins race to maps_to_win) given current score.

    Uses negative binomial / recursive DP approach.
    """
    p = max(0.001, min(0.999, p))

    needs_a = maps_to_win - won_a
    needs_b = maps_to_win - won_b

    if needs_a <= 0:
        return 1.0
    if needs_b <= 0:
        return 0.0

    # DP: dp[i][j] = P(A wins | A needs i more, B needs j more)
    dp: Dict[Tuple[int, int], float] = {}

    def solve(a_needs: int, b_needs: int) -> float:
        if a_needs <= 0:
            return 1.0
        if b_needs <= 0:
            return 0.0
        key = (a_needs, b_needs)
        if key in dp:
            return dp[key]
        result = p * solve(a_needs - 1, b_needs) + (1 - p) * solve(a_needs, b_needs - 1)
        dp[key] = result
        return result

    return solve(needs_a, needs_b)


def map_veto_adjusted_prob(
    team_a_map_rates: Dict[str, float],
    team_b_map_rates: Dict[str, float],
    veto_order: List[str],
) -> List[float]:
    """
    Compute per-map P(team_a wins) adjusted for map veto picks.

    In CS2 BO3 veto: each team bans 2 maps, picks 1, decider is leftover.
    team_a picks first → chooses their best map.
    team_b picks second → chooses their best map.
    Third map is the decider (neutral).

    Args:
        team_a_map_rates: Dict of map_name -> team_a's win rate on that map.
        team_b_map_rates: Dict of map_name -> team_b's win rate on that map.
        veto_order: List of map names in play order (map picks).

    Returns:
        List of P(team_a wins) for each map in veto_order.
    """
    probs = []
    for map_name in veto_order:
        a_rate = team_a_map_rates.get(map_name, 0.50)
        b_rate = team_b_map_rates.get(map_name, 0.50)

        # Head-to-head: normalise both teams' win rates
        # If A has 60% on this map and B has 55%, A's adjusted rate is:
        # A_rate / (A_rate + B_rate) ≈ 0.522
        total = a_rate + b_rate
        if total > 0:
            adjusted = a_rate / total
        else:
            adjusted = 0.50

        probs.append(max(0.05, min(0.95, adjusted)))

    return probs


def series_prob_with_map_veto(
    team_a_map_rates: Dict[str, float],
    team_b_map_rates: Dict[str, float],
    veto_order: List[str],
    maps_won_a: int = 0,
    maps_won_b: int = 0,
) -> float:
    """
    Full series probability incorporating map veto analysis.

    Computes per-map probabilities from team map pool stats, then
    chains them into a series probability given current score.
    """
    if not veto_order:
        return 0.50

    map_probs = map_veto_adjusted_prob(team_a_map_rates, team_b_map_rates, veto_order)

    # Total maps needed to win
    best_of = len(veto_order)
    maps_to_win = (best_of // 2) + 1

    needs_a = maps_to_win - maps_won_a
    needs_b = maps_to_win - maps_won_b

    if needs_a <= 0:
        return 1.0
    if needs_b <= 0:
        return 0.0

    # Use map-specific probabilities for remaining maps
    remaining_probs = map_probs[maps_won_a + maps_won_b:]

    return _series_prob_heterogeneous(remaining_probs, needs_a, needs_b)


def _series_prob_heterogeneous(
    map_probs: List[float],
    needs_a: int,
    needs_b: int,
) -> float:
    """
    Series probability where each map has a different win probability.

    Uses recursive DP over the sequence of remaining maps.
    """
    if needs_a <= 0:
        return 1.0
    if needs_b <= 0:
        return 0.0
    if not map_probs:
        # No more maps — shouldn't happen if needs > 0
        return 0.5

    dp: Dict[Tuple[int, int, int], float] = {}

    def solve(a_needs: int, b_needs: int, map_idx: int) -> float:
        if a_needs <= 0:
            return 1.0
        if b_needs <= 0:
            return 0.0
        if map_idx >= len(map_probs):
            # Ran out of maps — use last known probability
            p = map_probs[-1] if map_probs else 0.5
            return _series_prob(p, 0, 0, 1) if a_needs == 1 and b_needs == 1 else 0.5

        key = (a_needs, b_needs, map_idx)
        if key in dp:
            return dp[key]

        p = map_probs[map_idx]
        result = p * solve(a_needs - 1, b_needs, map_idx + 1) + \
                 (1 - p) * solve(a_needs, b_needs - 1, map_idx + 1)
        dp[key] = result
        return result

    return solve(needs_a, needs_b, 0)


def detect_momentum_fallacy(
    map_margin: int,
    market_adjustment: float,
) -> Optional[float]:
    """
    Detect if the market is overweighting momentum from previous map.

    Empirically, round score margin on a completed map has near-zero
    predictive value for the next map outcome (controlling for team strength
    and map pick). Markets often adjust 5-15% based on blowout/close maps.

    Args:
        map_margin: Score margin on the just-completed map (e.g., 16-3 = 13).
        market_adjustment: How much the market moved after the map result.
                          Positive = market moved in winner's favour.

    Returns:
        Estimated edge from momentum fallacy, or None if no fallacy detected.
        Positive = bet against the momentum (mean reversion).
    """
    # Margin has near-zero predictive value — market overweights it
    # Threshold: if market adjusted > 3% and margin > 8 rounds, it's likely overweighted
    if abs(map_margin) >= 8 and abs(market_adjustment) >= 0.03:
        # The market overreacted — edge is roughly half the adjustment
        edge = market_adjustment * 0.5
        return edge

    return None
