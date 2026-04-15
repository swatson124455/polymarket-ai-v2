"""
Elo Rating Engine for EsportsBot v2 Trinity.

Simple, stable team-level rating system. Acts as the baseline anchor in the
Trinity (Elo + Glicko-2 + OpenSkill). Converges quickly, minimal parameters.

Key properties:
  - K-factor configurable per-game (default 32)
  - LAN bonus: optional rating boost for LAN events
  - Win probability via logistic function
  - Deterministic: same input always produces same output

Usage::
    engine = EloEngine(k_factor=32)
    engine.process_match("team_a", "team_b", winner="a")
    p = engine.predict("team_a", "team_b")  # P(team_a wins)
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple


DEFAULT_RATING = 1500.0
DEFAULT_K = 32.0


@dataclass
class EloRating:
    """A team's Elo rating."""
    rating: float = DEFAULT_RATING
    matches_played: int = 0

    def to_dict(self) -> dict:
        return {"rating": self.rating, "matches_played": self.matches_played}

    @staticmethod
    def from_dict(d: dict) -> EloRating:
        return EloRating(rating=d.get("rating", DEFAULT_RATING), matches_played=d.get("matches_played", 0))


class EloEngine:
    """
    Manages Elo ratings for all teams in a game.

    Processes match results chronologically and maintains per-team ratings.
    """

    def __init__(self, k_factor: float = DEFAULT_K, lan_bonus: float = 0.0) -> None:
        """
        Args:
            k_factor: Rating adjustment speed. Higher = faster convergence, more noise.
            lan_bonus: Flat rating bonus applied to LAN event matches (both teams).
                       Models the observation that LAN results are more predictive.
        """
        self._ratings: Dict[str, EloRating] = {}
        self._k_factor = k_factor
        self._lan_bonus = lan_bonus
        self._match_count = 0

    @property
    def match_count(self) -> int:
        return self._match_count

    def get_rating(self, team_id: str) -> EloRating:
        """Get a team's current rating, or default if unseen."""
        return self._ratings.get(team_id, EloRating())

    def set_rating(self, team_id: str, rating: EloRating) -> None:
        """Pre-populate a team's rating (e.g., from DB restore)."""
        self._ratings[team_id] = rating

    def predict(self, team_a: str, team_b: str) -> float:
        """
        P(team_a beats team_b) based on current Elo ratings.

        Uses the standard logistic function with scale factor 400.
        """
        ra = self.get_rating(team_a).rating
        rb = self.get_rating(team_b).rating
        return _expected_score(ra, rb)

    def process_match(
        self,
        team_a: str,
        team_b: str,
        winner: str = "a",
        is_lan: bool = False,
    ) -> Tuple[EloRating, EloRating]:
        """
        Process a single match result and update both teams.

        Args:
            team_a: Team A identifier.
            team_b: Team B identifier.
            winner: "a" if team A won, "b" if team B won, "draw" for draw.
            is_lan: Whether this was a LAN event.

        Returns:
            Tuple of (new_rating_a, new_rating_b).
        """
        r_a = self._ratings.setdefault(team_a, EloRating())
        r_b = self._ratings.setdefault(team_b, EloRating())

        ra = r_a.rating
        rb = r_b.rating

        # LAN bonus: temporarily inflate both ratings for K-factor purposes
        # (doesn't change stored ratings, just makes outcomes count more)
        if is_lan and self._lan_bonus > 0:
            ra += self._lan_bonus
            rb += self._lan_bonus

        expected_a = _expected_score(ra, rb)
        expected_b = 1.0 - expected_a

        if winner == "a":
            score_a, score_b = 1.0, 0.0
        elif winner == "b":
            score_a, score_b = 0.0, 1.0
        else:
            score_a, score_b = 0.5, 0.5

        r_a.rating += self._k_factor * (score_a - expected_a)
        r_b.rating += self._k_factor * (score_b - expected_b)
        r_a.matches_played += 1
        r_b.matches_played += 1
        self._match_count += 1

        return EloRating(r_a.rating, r_a.matches_played), EloRating(r_b.rating, r_b.matches_played)

    def get_all_ratings(self) -> Dict[str, EloRating]:
        """Get all tracked team ratings."""
        return dict(self._ratings)


def _expected_score(ra: float, rb: float) -> float:
    """Standard Elo expected score: 1 / (1 + 10^((rb - ra) / 400))."""
    return 1.0 / (1.0 + math.pow(10.0, (rb - ra) / 400.0))
