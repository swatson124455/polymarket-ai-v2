"""
Glicko-2 Rating System for esports teams.

Implements the Glicko-2 algorithm (Glickman 2013) for tracking team
strength with uncertainty. Superior to raw win-rate for:
  - Handling opponent strength (beating #1 team vs #50 matters)
  - Uncertainty decay (inactive teams get wider sigma)
  - Recency weighting (naturally downweights old results)

Per EsportsBench (2024): Glicko-2 achieves 63.1% accuracy on CS:GO,
outperforming Elo, TrueSkill, and raw win-rate approaches.

Usage::
    tracker = Glicko2Tracker()
    tracker.process_match("team_a_id", "team_b_id", winner="a")
    expected = tracker.expected_score("team_a_id", "team_b_id")
    diff = tracker.strength_diff("team_a_id", "team_b_id")
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# Glicko-2 constants
_MU_DEFAULT = 1500.0       # Default rating
_PHI_DEFAULT = 350.0       # Default rating deviation (high uncertainty)
_SIGMA_DEFAULT = 0.06      # Default volatility
_TAU = 0.5                 # Default system constant (controls volatility change rate)
                           # S136: Now per-game configurable via Glicko2Tracker(tau=...)
_EPSILON = 0.000001        # Convergence tolerance for volatility iteration
_SCALE = 173.7178          # Glicko-2 scaling factor (400 / ln(10))


@dataclass
class Glicko2Rating:
    """A team's Glicko-2 rating with uncertainty."""
    mu: float = _MU_DEFAULT
    phi: float = _PHI_DEFAULT
    sigma: float = _SIGMA_DEFAULT

    @property
    def interval_95(self) -> Tuple[float, float]:
        """95% confidence interval for true rating."""
        return (self.mu - 2 * self.phi, self.mu + 2 * self.phi)

    def to_glicko2_scale(self) -> Tuple[float, float]:
        """Convert to Glicko-2 internal scale (mu', phi')."""
        return ((self.mu - _MU_DEFAULT) / _SCALE, self.phi / _SCALE)

    @staticmethod
    def from_glicko2_scale(mu2: float, phi2: float, sigma: float) -> Glicko2Rating:
        """Convert from Glicko-2 internal scale back to Glicko-1 scale."""
        return Glicko2Rating(
            mu=mu2 * _SCALE + _MU_DEFAULT,
            phi=phi2 * _SCALE,
            sigma=sigma,
        )


def expected_score(rating_a: Glicko2Rating, rating_b: Glicko2Rating) -> float:
    """
    P(a beats b) based on Glicko-2 ratings.

    Accounts for both rating difference AND uncertainty of both teams.
    """
    mu_a, phi_a = rating_a.to_glicko2_scale()
    mu_b, phi_b = rating_b.to_glicko2_scale()
    g_phi_b = _g(phi_b)
    return _E(mu_a, mu_b, g_phi_b)


def update_rating(
    player: Glicko2Rating,
    opponents: List[Glicko2Rating],
    outcomes: List[float],
    tau: float = _TAU,
) -> Glicko2Rating:
    """
    Update a player's rating after a rating period.

    Args:
        player: Current player rating.
        opponents: List of opponent ratings faced.
        outcomes: List of outcomes (1.0 = win, 0.5 = draw, 0.0 = loss).

    Returns:
        Updated Glicko2Rating.
    """
    if not opponents:
        # No games: increase uncertainty (rating period decay)
        phi_star = math.sqrt(player.phi ** 2 + (player.sigma * _SCALE) ** 2)
        return Glicko2Rating(mu=player.mu, phi=min(phi_star, _PHI_DEFAULT), sigma=player.sigma)

    # Step 1: Convert to Glicko-2 scale
    mu, phi = player.to_glicko2_scale()

    opp_data = []
    for opp in opponents:
        mu_j, phi_j = opp.to_glicko2_scale()
        g_j = _g(phi_j)
        E_j = _E(mu, mu_j, g_j)
        opp_data.append((mu_j, phi_j, g_j, E_j))

    # Step 2: Compute variance (v)
    v_inv = 0.0
    for _, _, g_j, E_j in opp_data:
        v_inv += g_j ** 2 * E_j * (1 - E_j)
    v = 1.0 / v_inv if v_inv > 0 else 1e6

    # Step 3: Compute delta
    delta = 0.0
    for i, (_, _, g_j, E_j) in enumerate(opp_data):
        delta += g_j * (outcomes[i] - E_j)
    delta *= v

    # Step 4: Determine new volatility (sigma')
    sigma_new = _compute_new_sigma(player.sigma, phi, v, delta, tau=tau)

    # Step 5: Update phi and mu
    phi_star = math.sqrt(phi ** 2 + sigma_new ** 2)
    phi_new = 1.0 / math.sqrt(1.0 / phi_star ** 2 + 1.0 / v)

    mu_new = mu
    for i, (_, _, g_j, E_j) in enumerate(opp_data):
        mu_new += phi_new ** 2 * g_j * (outcomes[i] - E_j)

    return Glicko2Rating.from_glicko2_scale(mu_new, phi_new, sigma_new)


class Glicko2Tracker:
    """
    Manages Glicko-2 ratings for all teams in a game.

    Processes match results chronologically and maintains per-team ratings.
    """

    def __init__(self, tau: float = _TAU) -> None:
        self._ratings: Dict[str, Glicko2Rating] = {}
        self._match_count = 0
        self._tau = tau  # S136: Per-game configurable
        # S136 Phase 10A: Player-level ratings
        self._player_ratings: Dict[str, Glicko2Rating] = {}  # player_id → rating
        self._team_rosters: Dict[str, List[str]] = {}  # team_id → [player_ids]

    @property
    def match_count(self) -> int:
        return self._match_count

    def get_rating(self, team_id: str) -> Glicko2Rating:
        """Get team's current rating, or default if unseen."""
        return self._ratings.get(team_id, Glicko2Rating())

    def process_match(
        self, team_a_id: str, team_b_id: str, winner: str = "a"
    ) -> Tuple[Glicko2Rating, Glicko2Rating]:
        """
        Process a single match result and update both teams' ratings.

        Args:
            team_a_id: Team A identifier.
            team_b_id: Team B identifier.
            winner: "a" if team A won, "b" if team B won, "draw" for draw.

        Returns:
            Tuple of (new_rating_a, new_rating_b).
        """
        rating_a = self.get_rating(team_a_id)
        rating_b = self.get_rating(team_b_id)

        if winner == "a":
            outcome_a, outcome_b = 1.0, 0.0
        elif winner == "b":
            outcome_a, outcome_b = 0.0, 1.0
        else:
            outcome_a, outcome_b = 0.5, 0.5

        new_a = update_rating(rating_a, [rating_b], [outcome_a], tau=self._tau)
        new_b = update_rating(rating_b, [rating_a], [outcome_b], tau=self._tau)

        self._ratings[team_a_id] = new_a
        self._ratings[team_b_id] = new_b
        self._match_count += 1

        return new_a, new_b

    def process_matches_bulk(
        self, matches: List[Dict]
    ) -> None:
        """
        Process multiple matches chronologically.

        Each match dict should have: team_a_id, team_b_id, winner ("a"/"b"/"draw").
        """
        for m in matches:
            self.process_match(
                str(m["team_a_id"]),
                str(m["team_b_id"]),
                m.get("winner", "a"),
            )

    def expected_score(self, team_a_id: str, team_b_id: str) -> float:
        """P(team_a beats team_b) based on current ratings."""
        return expected_score(self.get_rating(team_a_id), self.get_rating(team_b_id))

    def strength_diff(self, team_a_id: str, team_b_id: str) -> float:
        """
        Compute team_strength_diff compatible with model features.

        Returns expected_score(a, b) - 0.5, so:
          positive = team A is stronger
          negative = team B is stronger
          0.0 = equal strength
        """
        return self.expected_score(team_a_id, team_b_id) - 0.5

    def set_rating(self, team_id: str, rating: Glicko2Rating) -> None:
        """Pre-populate a team's rating (e.g., from DB persistence)."""
        self._ratings[team_id] = rating

    def set_match_count(self, count: int) -> None:
        """Restore match count from DB (for cold-start detection)."""
        self._match_count = count

    def get_all_ratings(self) -> Dict[str, Glicko2Rating]:
        """Get all tracked team ratings."""
        return dict(self._ratings)

    # ── S136 Phase 10A: Player-level rating methods ──────────────────

    def set_player_rating(self, player_id: str, rating: Glicko2Rating) -> None:
        """Pre-populate a player's rating (e.g., from DB persistence)."""
        self._player_ratings[player_id] = rating

    def get_player_rating(self, player_id: str) -> Glicko2Rating:
        """Get player's current rating, or default if unseen."""
        return self._player_ratings.get(player_id, Glicko2Rating())

    def update_roster(self, team_id: str, new_roster: List[str]) -> float:
        """Update team roster, return roster change ratio.

        S136: Composite rating adjustment on roster change.
        Returns the proportion of roster that changed (0.0 = no change, 1.0 = full rebuild).
        """
        old_roster = self._team_rosters.get(team_id, [])
        self._team_rosters[team_id] = new_roster

        if not old_roster:
            return 0.0  # First time seeing roster, no change

        old_set = set(old_roster)
        new_set = set(new_roster)
        n_total = max(len(new_set), 1)
        n_changes = len(new_set - old_set)
        change_ratio = n_changes / n_total

        if change_ratio > 0 and team_id in self._ratings:
            team_rating = self._ratings[team_id]
            # Composite: blend team history with new player average
            player_ratings = [self._player_ratings.get(p, Glicko2Rating()) for p in new_roster]
            avg_player_mu = sum(r.mu for r in player_ratings) / max(len(player_ratings), 1)

            # Alpha: how much to trust team history vs player average
            alpha = max(0.50, 1.0 - change_ratio)  # Always >= 50% team history

            # Cap adjustment if player avg deviates too much
            if abs(avg_player_mu - team_rating.mu) > 200:
                avg_player_mu = team_rating.mu + max(-200, min(200, avg_player_mu - team_rating.mu))

            new_mu = alpha * team_rating.mu + (1 - alpha) * avg_player_mu

            # RD inflation proportional to change
            new_phi = math.sqrt(team_rating.phi ** 2 + 0.4 * change_ratio * 350.0 ** 2)
            new_phi = min(new_phi, _PHI_DEFAULT)

            self._ratings[team_id] = Glicko2Rating(
                mu=new_mu, phi=new_phi, sigma=team_rating.sigma
            )

        return change_ratio

    def get_all_player_ratings(self) -> Dict[str, tuple]:
        """Export all player ratings for DB persistence."""
        return {
            pid: (r.mu, r.phi, r.sigma)
            for pid, r in self._player_ratings.items()
        }


# ── Glicko-2 math helpers ────────────────────────────────────────────

def _g(phi: float) -> float:
    """Glicko-2 g() function: reduces weight of opponent based on their uncertainty."""
    return 1.0 / math.sqrt(1.0 + 3.0 * phi ** 2 / math.pi ** 2)


def _E(mu: float, mu_j: float, g_j: float) -> float:
    """Expected score of player with mu against opponent with mu_j."""
    return 1.0 / (1.0 + math.exp(-g_j * (mu - mu_j)))


def _compute_new_sigma(sigma: float, phi: float, v: float, delta: float, tau: float = _TAU) -> float:
    """
    Iterative algorithm to determine new volatility (Glickman Step 5.4).

    Uses Illinois algorithm variant for root finding.
    """
    a = math.log(sigma ** 2)
    delta_sq = delta ** 2
    phi_sq = phi ** 2

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta_sq - phi_sq - v - ex)
        denom = 2.0 * (phi_sq + v + ex) ** 2
        return num / denom - (x - a) / (tau ** 2)

    # Initial bounds
    A = a
    if delta_sq > phi_sq + v:
        B = math.log(delta_sq - phi_sq - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        B = a - k * tau

    # Iterative convergence
    f_A = f(A)
    f_B = f(B)

    for _ in range(100):  # Safety limit
        if abs(B - A) < _EPSILON:
            break
        C = A + (A - B) * f_A / (f_B - f_A)
        f_C = f(C)
        if f_C * f_B <= 0:
            A = B
            f_A = f_B
        else:
            f_A /= 2.0
        B = C
        f_B = f_C

    return math.exp(A / 2.0)
