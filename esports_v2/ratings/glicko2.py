"""
Glicko-2 Rating Engine for EsportsBot v2 Trinity.

Wraps the proven Glicko-2 implementation from esports v1 with the standard
Trinity interface. Adds uncertainty tracking via rating deviation (RD) and
volatility — the key differentiator from Elo.

Key properties:
  - Uncertainty via phi (RD): inactive teams lose certainty over time
  - Volatility via sigma: tracks how erratic a team's results are
  - Win probability accounts for both teams' uncertainty
  - tau parameter configurable per-game

Usage::
    engine = Glicko2Engine(tau=0.5)
    engine.process_match("team_a", "team_b", winner="a")
    p = engine.predict("team_a", "team_b")
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# Glicko-2 constants
MU_DEFAULT = 1500.0
PHI_DEFAULT = 350.0
SIGMA_DEFAULT = 0.06
TAU_DEFAULT = 0.5
EPSILON = 0.000001
SCALE = 173.7178  # 400 / ln(10)


@dataclass
class Glicko2Rating:
    """A team's Glicko-2 rating with uncertainty."""
    mu: float = MU_DEFAULT
    phi: float = PHI_DEFAULT
    sigma: float = SIGMA_DEFAULT
    matches_played: int = 0

    @property
    def interval_95(self) -> Tuple[float, float]:
        """95% confidence interval for true rating."""
        return (self.mu - 2 * self.phi, self.mu + 2 * self.phi)

    def to_glicko2_scale(self) -> Tuple[float, float]:
        """Convert to Glicko-2 internal scale (mu', phi')."""
        return ((self.mu - MU_DEFAULT) / SCALE, self.phi / SCALE)

    @staticmethod
    def from_glicko2_scale(mu2: float, phi2: float, sigma: float, matches: int = 0) -> Glicko2Rating:
        """Convert from Glicko-2 internal scale back to Glicko-1 scale."""
        return Glicko2Rating(
            mu=mu2 * SCALE + MU_DEFAULT,
            phi=phi2 * SCALE,
            sigma=sigma,
            matches_played=matches,
        )

    def to_dict(self) -> dict:
        return {
            "rating": self.mu,
            "deviation": self.phi,
            "volatility": self.sigma,
            "matches_played": self.matches_played,
        }

    @staticmethod
    def from_dict(d: dict) -> Glicko2Rating:
        return Glicko2Rating(
            mu=d.get("rating", MU_DEFAULT),
            phi=d.get("deviation", PHI_DEFAULT),
            sigma=d.get("volatility", SIGMA_DEFAULT),
            matches_played=d.get("matches_played", 0),
        )


class Glicko2Engine:
    """
    Manages Glicko-2 ratings for all teams in a game.

    Standard Trinity interface: get_rating, predict, process_match, get_all_ratings.
    """

    def __init__(self, tau: float = TAU_DEFAULT) -> None:
        self._ratings: Dict[str, Glicko2Rating] = {}
        self._tau = tau
        self._match_count = 0

    @property
    def match_count(self) -> int:
        return self._match_count

    def get_rating(self, team_id: str) -> Glicko2Rating:
        """Get a team's current rating, or default if unseen."""
        return self._ratings.get(team_id, Glicko2Rating())

    def set_rating(self, team_id: str, rating: Glicko2Rating) -> None:
        """Pre-populate a team's rating (e.g., from DB restore)."""
        self._ratings[team_id] = rating

    def predict(self, team_a: str, team_b: str) -> float:
        """P(team_a beats team_b) based on current Glicko-2 ratings."""
        ra = self.get_rating(team_a)
        rb = self.get_rating(team_b)
        mu_a, phi_a = ra.to_glicko2_scale()
        mu_b, phi_b = rb.to_glicko2_scale()
        g_b = _g(phi_b)
        return _E(mu_a, mu_b, g_b)

    def process_match(
        self,
        team_a: str,
        team_b: str,
        winner: str = "a",
        is_lan: bool = False,
    ) -> Tuple[Glicko2Rating, Glicko2Rating]:
        """
        Process a single match result and update both teams.

        Args:
            team_a: Team A identifier.
            team_b: Team B identifier.
            winner: "a" if team A won, "b" if team B won, "draw" for draw.
            is_lan: Whether this was a LAN event (not used by Glicko-2 directly).

        Returns:
            Tuple of (new_rating_a, new_rating_b).
        """
        r_a = self._ratings.setdefault(team_a, Glicko2Rating())
        r_b = self._ratings.setdefault(team_b, Glicko2Rating())

        if winner == "a":
            outcome_a, outcome_b = 1.0, 0.0
        elif winner == "b":
            outcome_a, outcome_b = 0.0, 1.0
        else:
            outcome_a, outcome_b = 0.5, 0.5

        new_a = _update_rating(r_a, [r_b], [outcome_a], tau=self._tau)
        new_b = _update_rating(r_b, [r_a], [outcome_b], tau=self._tau)

        new_a.matches_played = r_a.matches_played + 1
        new_b.matches_played = r_b.matches_played + 1

        self._ratings[team_a] = new_a
        self._ratings[team_b] = new_b
        self._match_count += 1

        return new_a, new_b

    def get_all_ratings(self) -> Dict[str, Glicko2Rating]:
        """Get all tracked team ratings."""
        return dict(self._ratings)


# -- Glicko-2 math (from v1, proven correct) ----------------------------------

def _g(phi: float) -> float:
    """Reduces weight of opponent based on their uncertainty."""
    return 1.0 / math.sqrt(1.0 + 3.0 * phi ** 2 / math.pi ** 2)


def _E(mu: float, mu_j: float, g_j: float) -> float:
    """Expected score of player with mu against opponent with mu_j."""
    return 1.0 / (1.0 + math.exp(-g_j * (mu - mu_j)))


def _update_rating(
    player: Glicko2Rating,
    opponents: List[Glicko2Rating],
    outcomes: List[float],
    tau: float = TAU_DEFAULT,
) -> Glicko2Rating:
    """Update a player's rating after a rating period."""
    if not opponents:
        phi_star = math.sqrt(player.phi ** 2 + (player.sigma * SCALE) ** 2)
        return Glicko2Rating(mu=player.mu, phi=min(phi_star, PHI_DEFAULT), sigma=player.sigma)

    mu, phi = player.to_glicko2_scale()

    opp_data = []
    for opp in opponents:
        mu_j, phi_j = opp.to_glicko2_scale()
        g_j = _g(phi_j)
        E_j = _E(mu, mu_j, g_j)
        opp_data.append((mu_j, phi_j, g_j, E_j))

    # Variance
    v_inv = 0.0
    for _, _, g_j, E_j in opp_data:
        v_inv += g_j ** 2 * E_j * (1 - E_j)
    v = 1.0 / v_inv if v_inv > 0 else 1e6

    # Delta
    delta = 0.0
    for i, (_, _, g_j, E_j) in enumerate(opp_data):
        delta += g_j * (outcomes[i] - E_j)
    delta *= v

    # New volatility
    sigma_new = _compute_new_sigma(player.sigma, phi, v, delta, tau=tau)

    # Update phi and mu
    phi_star = math.sqrt(phi ** 2 + sigma_new ** 2)
    phi_new = 1.0 / math.sqrt(1.0 / phi_star ** 2 + 1.0 / v)

    mu_new = mu
    for i, (_, _, g_j, E_j) in enumerate(opp_data):
        mu_new += phi_new ** 2 * g_j * (outcomes[i] - E_j)

    return Glicko2Rating.from_glicko2_scale(mu_new, phi_new, sigma_new, player.matches_played)


def _compute_new_sigma(sigma: float, phi: float, v: float, delta: float, tau: float = TAU_DEFAULT) -> float:
    """Iterative algorithm to determine new volatility (Glickman Step 5.4)."""
    a = math.log(sigma ** 2)
    delta_sq = delta ** 2
    phi_sq = phi ** 2

    def f(x: float) -> float:
        ex = math.exp(x)
        num = ex * (delta_sq - phi_sq - v - ex)
        denom = 2.0 * (phi_sq + v + ex) ** 2
        return num / denom - (x - a) / (tau ** 2)

    A = a
    if delta_sq > phi_sq + v:
        B = math.log(delta_sq - phi_sq - v)
    else:
        k = 1
        while f(a - k * tau) < 0:
            k += 1
        B = a - k * tau

    f_A = f(A)
    f_B = f(B)

    for _ in range(100):
        if abs(B - A) < EPSILON:
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
