"""
Trinity Runner — Orchestrates Elo + Glicko-2 + OpenSkill rating systems.

Processes matches chronologically through all 3 systems and computes
consensus signals (spread, mean, agreement) used as features for the
XGBoost meta-model.

Key signals:
  - trinity_spread: max(P) - min(P). Low spread = agreement = confidence.
  - trinity_mean: Average of 3 predicted probabilities.
  - agreement_flag: spread < 0.05 (high confidence) vs > 0.15 (abstain).

Usage::
    trinity = Trinity()
    trinity.process_match(match)
    features = trinity.predict("team_a", "team_b")
    # features = {"p_elo": 0.62, "p_glicko": 0.58, "p_openskill": 0.61,
    #             "trinity_spread": 0.04, "trinity_mean": 0.603, ...}
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from esports_v2.ratings.elo import EloEngine, EloRating
from esports_v2.ratings.glicko2 import Glicko2Engine, Glicko2Rating
from esports_v2.ratings.openskill_engine import OpenSkillEngine, PlayerRating


# Consensus thresholds
SPREAD_HIGH_AGREEMENT = 0.05
SPREAD_ABSTAIN = 0.15


@dataclass
class TrinityPrediction:
    """Output of a Trinity prediction for a match."""
    team_a: str
    team_b: str
    p_elo: float           # P(team_a wins) from Elo
    p_glicko: float        # P(team_a wins) from Glicko-2
    p_openskill: float     # P(team_a wins) from OpenSkill
    trinity_spread: float  # max(p) - min(p)
    trinity_mean: float    # average of 3 probs

    @property
    def high_agreement(self) -> bool:
        """All 3 systems agree closely -> high confidence."""
        return self.trinity_spread < SPREAD_HIGH_AGREEMENT

    @property
    def should_abstain(self) -> bool:
        """Systems diverge too much -> skip this match."""
        return self.trinity_spread > SPREAD_ABSTAIN

    def to_feature_dict(self) -> Dict[str, float]:
        """Convert to feature dict for XGBoost meta-model."""
        return {
            "p_elo": self.p_elo,
            "p_glicko": self.p_glicko,
            "p_openskill": self.p_openskill,
            "trinity_spread": self.trinity_spread,
            "trinity_mean": self.trinity_mean,
        }


@dataclass
class MatchResult:
    """Normalized match result for Trinity processing."""
    match_id: str
    game: str              # 'cs2' or 'lol'
    team_a: str
    team_b: str
    winner: str            # 'a' or 'b' or 'draw'
    is_lan: bool = False
    roster_a: Optional[List[str]] = None  # player IDs (for OpenSkill)
    roster_b: Optional[List[str]] = None
    patch: Optional[str] = None
    match_date: Optional[str] = None


class Trinity:
    """
    Orchestrates Elo + Glicko-2 + OpenSkill rating systems.

    Maintains separate engine instances per game (CS2, LoL). Each engine
    is independent — processing a CS2 match doesn't affect LoL ratings.
    """

    def __init__(
        self,
        elo_k: float = 32.0,
        glicko_tau: float = 0.5,
        elo_lan_bonus: float = 0.0,
    ) -> None:
        """
        Args:
            elo_k: Elo K-factor (default 32).
            glicko_tau: Glicko-2 tau system constant (default 0.5).
            elo_lan_bonus: Elo LAN bonus (default 0, no bonus).
        """
        self._elo_k = elo_k
        self._glicko_tau = glicko_tau
        self._elo_lan_bonus = elo_lan_bonus

        # Per-game engines
        self._elo: Dict[str, EloEngine] = {}
        self._glicko: Dict[str, Glicko2Engine] = {}
        self._openskill: Dict[str, OpenSkillEngine] = {}

        self._match_count = 0

    @property
    def match_count(self) -> int:
        return self._match_count

    def _get_elo(self, game: str) -> EloEngine:
        if game not in self._elo:
            self._elo[game] = EloEngine(k_factor=self._elo_k, lan_bonus=self._elo_lan_bonus)
        return self._elo[game]

    def _get_glicko(self, game: str) -> Glicko2Engine:
        if game not in self._glicko:
            self._glicko[game] = Glicko2Engine(tau=self._glicko_tau)
        return self._glicko[game]

    def _get_openskill(self, game: str) -> OpenSkillEngine:
        if game not in self._openskill:
            self._openskill[game] = OpenSkillEngine()
        return self._openskill[game]

    def predict(self, team_a: str, team_b: str, game: str) -> TrinityPrediction:
        """
        Get Trinity consensus prediction for a match.

        Args:
            team_a: Team A identifier.
            team_b: Team B identifier.
            game: Game identifier ('cs2', 'lol').

        Returns:
            TrinityPrediction with all 3 probabilities + consensus signals.
        """
        p_elo = self._get_elo(game).predict(team_a, team_b)
        p_glicko = self._get_glicko(game).predict(team_a, team_b)
        p_openskill = self._get_openskill(game).predict(team_a, team_b)

        # OpenSkill returns exactly 0.5 when roster data is missing.
        # Exclude uninformative signal from spread/mean to prevent:
        # (a) artificially widening spread → false abstain
        # (b) artificially narrowing spread → false confidence
        # XGBoost still sees p_openskill=0.5 as a feature (learns it's noise).
        informative = [p_elo, p_glicko]
        if abs(p_openskill - 0.5) > 1e-9:
            informative.append(p_openskill)

        spread = max(informative) - min(informative)
        mean = sum(informative) / len(informative)

        return TrinityPrediction(
            team_a=team_a,
            team_b=team_b,
            p_elo=p_elo,
            p_glicko=p_glicko,
            p_openskill=p_openskill,
            trinity_spread=spread,
            trinity_mean=mean,
        )

    def process_match(self, match: MatchResult) -> TrinityPrediction:
        """
        Process a match through all 3 rating systems, then return the
        pre-match prediction (features for training).

        IMPORTANT: Captures prediction BEFORE updating ratings, so the
        prediction represents what the model would have predicted at the
        time of the match (no lookahead).

        Args:
            match: Normalized match result.

        Returns:
            TrinityPrediction captured BEFORE this match's ratings update.
        """
        game = match.game

        # 1. Capture pre-match prediction (before update)
        prediction = self.predict(match.team_a, match.team_b, game)

        # 2. Update all 3 systems
        self._get_elo(game).process_match(
            match.team_a, match.team_b,
            winner=match.winner,
            is_lan=match.is_lan,
        )

        self._get_glicko(game).process_match(
            match.team_a, match.team_b,
            winner=match.winner,
            is_lan=match.is_lan,
        )

        self._get_openskill(game).process_match(
            match.team_a, match.team_b,
            winner=match.winner,
            roster_a=match.roster_a,
            roster_b=match.roster_b,
            is_lan=match.is_lan,
        )

        self._match_count += 1
        return prediction

    def process_matches(self, matches: List[MatchResult]) -> List[TrinityPrediction]:
        """
        Process multiple matches chronologically (must be pre-sorted by date).

        Returns list of pre-match predictions (one per match).
        """
        return [self.process_match(m) for m in matches]

    def get_elo_ratings(self, game: str) -> Dict[str, EloRating]:
        """Get all Elo ratings for a game."""
        return self._get_elo(game).get_all_ratings()

    def get_glicko_ratings(self, game: str) -> Dict[str, Glicko2Rating]:
        """Get all Glicko-2 ratings for a game."""
        return self._get_glicko(game).get_all_ratings()

    def get_openskill_ratings(self, game: str) -> Dict[str, PlayerRating]:
        """Get all OpenSkill player ratings for a game."""
        return self._get_openskill(game).get_all_ratings()

    def get_games(self) -> List[str]:
        """Get list of games with initialized engines."""
        return list(set(list(self._elo.keys()) + list(self._glicko.keys()) + list(self._openskill.keys())))
