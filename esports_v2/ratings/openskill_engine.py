"""
OpenSkill Rating Engine for EsportsBot v2 Trinity.

Player-level Plackett-Luce ratings that compose into team predictions.
The key differentiator from Elo/Glicko-2: handles roster changes naturally
because ratings live on players, not teams.

Key properties:
  - Player-level ratings (mu, sigma) via Plackett-Luce model
  - Team prediction = composition of player ratings
  - Roster changes automatically reflected (new player brings their own rating)
  - sigma tracks uncertainty per player

Usage::
    engine = OpenSkillEngine()
    engine.process_match(
        team_a="NAVI", team_b="FaZe", winner="a",
        roster_a=["s1mple", "b1t", "electroNic", "Perfecto", "sdy"],
        roster_b=["rain", "karrigan", "broky", "ropz", "Twistzz"],
    )
    p = engine.predict("NAVI", "FaZe")  # P(NAVI wins)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from openskill.models import PlackettLuce


MU_DEFAULT = 25.0
SIGMA_DEFAULT = 25.0 / 3.0  # ~8.333


@dataclass
class PlayerRating:
    """A player's OpenSkill rating."""
    mu: float = MU_DEFAULT
    sigma: float = SIGMA_DEFAULT
    matches_played: int = 0

    def to_dict(self) -> dict:
        return {
            "rating": self.mu,
            "deviation": self.sigma,
            "matches_played": self.matches_played,
        }


class OpenSkillEngine:
    """
    Manages player-level OpenSkill ratings and composes them into team predictions.

    Standard Trinity interface: get_rating (team-level), predict, process_match,
    get_all_ratings. Plus player-level methods for roster tracking.
    """

    def __init__(self) -> None:
        self._model = PlackettLuce()
        self._player_ratings: Dict[str, PlayerRating] = {}  # player_id -> rating
        self._team_rosters: Dict[str, List[str]] = {}  # team_id -> [player_ids]
        self._match_count = 0

    @property
    def match_count(self) -> int:
        return self._match_count

    def get_player_rating(self, player_id: str) -> PlayerRating:
        """Get a player's current rating, or default if unseen."""
        return self._player_ratings.get(player_id, PlayerRating())

    def set_player_rating(self, player_id: str, rating: PlayerRating) -> None:
        """Pre-populate a player's rating (e.g., from DB restore)."""
        self._player_ratings[player_id] = rating

    def get_roster(self, team_id: str) -> List[str]:
        """Get a team's current roster."""
        return self._team_rosters.get(team_id, [])

    def predict(self, team_a: str, team_b: str) -> float:
        """
        P(team_a beats team_b) based on player ratings.

        Composes player ratings into team-level win probability via
        PlackettLuce.predict_win.
        """
        roster_a = self._team_rosters.get(team_a, [])
        roster_b = self._team_rosters.get(team_b, [])

        if not roster_a or not roster_b:
            return 0.5  # no roster info -> uninformative

        os_a = self._make_os_team(roster_a)
        os_b = self._make_os_team(roster_b)
        probs = self._model.predict_win([os_a, os_b])
        return probs[0]

    def process_match(
        self,
        team_a: str,
        team_b: str,
        winner: str = "a",
        roster_a: Optional[List[str]] = None,
        roster_b: Optional[List[str]] = None,
        is_lan: bool = False,
    ) -> Tuple[float, float]:
        """
        Process a single match result and update player ratings.

        Args:
            team_a: Team A identifier.
            team_b: Team B identifier.
            winner: "a" if team A won, "b" if team B won.
            roster_a: Player IDs for team A. Updates stored roster.
            roster_b: Player IDs for team B. Updates stored roster.
            is_lan: Whether this was a LAN event (logged, not used in math).

        Returns:
            Tuple of average (mu_a, mu_b) after update.
        """
        # Update rosters if provided
        if roster_a is not None:
            self._team_rosters[team_a] = roster_a
        if roster_b is not None:
            self._team_rosters[team_b] = roster_b

        r_a = self._team_rosters.get(team_a, [])
        r_b = self._team_rosters.get(team_b, [])

        if not r_a or not r_b:
            # Can't update without rosters — skip
            self._match_count += 1
            return (MU_DEFAULT, MU_DEFAULT)

        # Build openskill rating objects
        os_a = self._make_os_team(r_a)
        os_b = self._make_os_team(r_b)

        # Rank order: winner first
        if winner == "a":
            teams = [os_a, os_b]
            rosters = [r_a, r_b]
        elif winner == "b":
            teams = [os_b, os_a]
            rosters = [r_b, r_a]
        else:
            # Draw: both rank 1
            teams = [os_a, os_b]
            rosters = [r_a, r_b]
            # openskill handles draws via ranks parameter
            result = self._model.rate(teams, ranks=[1, 1])
            self._apply_result(result, rosters)
            self._match_count += 1
            avg_a = self._team_avg_mu(r_a)
            avg_b = self._team_avg_mu(r_b)
            return (avg_a, avg_b)

        result = self._model.rate(teams)
        self._apply_result(result, rosters)
        self._match_count += 1

        avg_a = self._team_avg_mu(r_a)
        avg_b = self._team_avg_mu(r_b)
        return (avg_a, avg_b)

    def get_all_ratings(self) -> Dict[str, PlayerRating]:
        """Get all tracked player ratings."""
        return dict(self._player_ratings)

    def get_team_mu(self, team_id: str) -> float:
        """Get team's composite mu (average of player mus)."""
        return self._team_avg_mu(self._team_rosters.get(team_id, []))

    # -- internal helpers --

    def _make_os_team(self, roster: List[str]) -> list:
        """Convert a roster into a list of openskill rating objects."""
        team = []
        for pid in roster:
            pr = self._player_ratings.get(pid, PlayerRating())
            team.append(self._model.rating(name=pid, mu=pr.mu, sigma=pr.sigma))
        return team

    def _apply_result(self, result: list, rosters: List[List[str]]) -> None:
        """Apply openskill.rate() result back to stored player ratings."""
        for team_idx, roster in enumerate(rosters):
            for player_idx, pid in enumerate(roster):
                updated = result[team_idx][player_idx]
                existing = self._player_ratings.get(pid, PlayerRating())
                self._player_ratings[pid] = PlayerRating(
                    mu=updated.mu,
                    sigma=updated.sigma,
                    matches_played=existing.matches_played + 1,
                )

    def _team_avg_mu(self, roster: List[str]) -> float:
        """Average mu across a roster."""
        if not roster:
            return MU_DEFAULT
        total = sum(self._player_ratings.get(pid, PlayerRating()).mu for pid in roster)
        return total / len(roster)
