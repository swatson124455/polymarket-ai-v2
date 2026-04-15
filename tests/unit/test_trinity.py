"""Tests for EsportsBot v2 Trinity runner."""
import pytest

from esports_v2.ratings.trinity import (
    Trinity,
    TrinityPrediction,
    MatchResult,
    SPREAD_HIGH_AGREEMENT,
    SPREAD_ABSTAIN,
)


class TestTrinityPrediction:
    """Test the TrinityPrediction dataclass."""

    def test_high_agreement(self):
        pred = TrinityPrediction(
            team_a="a", team_b="b",
            p_elo=0.60, p_glicko=0.61, p_openskill=0.59,
            trinity_spread=0.02, trinity_mean=0.60,
        )
        assert pred.high_agreement is True
        assert pred.should_abstain is False

    def test_should_abstain(self):
        pred = TrinityPrediction(
            team_a="a", team_b="b",
            p_elo=0.70, p_glicko=0.50, p_openskill=0.55,
            trinity_spread=0.20, trinity_mean=0.583,
        )
        assert pred.high_agreement is False
        assert pred.should_abstain is True

    def test_to_feature_dict(self):
        pred = TrinityPrediction(
            team_a="a", team_b="b",
            p_elo=0.55, p_glicko=0.60, p_openskill=0.58,
            trinity_spread=0.05, trinity_mean=0.577,
        )
        d = pred.to_feature_dict()
        assert d["p_elo"] == 0.55
        assert d["p_glicko"] == 0.60
        assert d["p_openskill"] == 0.58
        assert d["trinity_spread"] == 0.05
        assert d["trinity_mean"] == 0.577


class TestTrinity:
    """Test the Trinity orchestrator."""

    def test_initial_predictions_near_50_50(self):
        trinity = Trinity()
        pred = trinity.predict("team_a", "team_b", game="cs2")
        assert pred.p_elo == pytest.approx(0.5, abs=0.01)
        assert pred.p_glicko == pytest.approx(0.5, abs=0.01)
        # OpenSkill returns 0.5 for teams with no roster
        assert pred.p_openskill == pytest.approx(0.5, abs=0.01)
        assert pred.trinity_spread == pytest.approx(0.0, abs=0.01)

    def test_process_match_returns_pre_match_prediction(self):
        """process_match should return prediction BEFORE ratings update."""
        trinity = Trinity()
        match = MatchResult(
            match_id="m1", game="cs2",
            team_a="NAVI", team_b="FaZe",
            winner="a",
        )
        # First match: both unseen, should predict ~0.5
        pred = trinity.process_match(match)
        assert pred.p_elo == pytest.approx(0.5, abs=0.01)
        assert pred.trinity_mean == pytest.approx(0.5, abs=0.01)

    def test_ratings_update_after_match(self):
        trinity = Trinity()
        match = MatchResult(
            match_id="m1", game="cs2",
            team_a="NAVI", team_b="FaZe",
            winner="a",
        )
        trinity.process_match(match)
        # After NAVI won, prediction should favor NAVI
        pred = trinity.predict("NAVI", "FaZe", game="cs2")
        assert pred.p_elo > 0.5
        assert pred.p_glicko > 0.5

    def test_per_game_isolation(self):
        """CS2 matches should not affect LoL ratings."""
        trinity = Trinity()
        cs2_match = MatchResult(
            match_id="m1", game="cs2",
            team_a="team_a", team_b="team_b",
            winner="a",
        )
        trinity.process_match(cs2_match)
        # LoL ratings should be unaffected
        lol_pred = trinity.predict("team_a", "team_b", game="lol")
        assert lol_pred.p_elo == pytest.approx(0.5, abs=0.01)

    def test_match_count(self):
        trinity = Trinity()
        assert trinity.match_count == 0
        match = MatchResult(match_id="m1", game="cs2", team_a="a", team_b="b", winner="a")
        trinity.process_match(match)
        assert trinity.match_count == 1

    def test_process_matches_bulk(self):
        trinity = Trinity()
        matches = [
            MatchResult(match_id=f"m{i}", game="cs2", team_a="a", team_b="b", winner="a")
            for i in range(5)
        ]
        predictions = trinity.process_matches(matches)
        assert len(predictions) == 5
        assert trinity.match_count == 5
        # After 5 wins for "a", should be strongly favored
        pred = trinity.predict("a", "b", game="cs2")
        assert pred.p_elo > 0.6
        assert pred.p_glicko > 0.6

    def test_consistent_winner_builds_edge(self):
        """Team that wins 10 straight should be heavily favored."""
        trinity = Trinity()
        for i in range(10):
            match = MatchResult(
                match_id=f"m{i}", game="lol",
                team_a="dominant", team_b=f"opp_{i}",
                winner="a",
            )
            trinity.process_match(match)
        pred = trinity.predict("dominant", "new_opp", game="lol")
        # All 3 systems should favor dominant
        assert pred.p_elo > 0.6
        assert pred.p_glicko > 0.6

    def test_get_games(self):
        trinity = Trinity()
        trinity.process_match(MatchResult(match_id="m1", game="cs2", team_a="a", team_b="b", winner="a"))
        trinity.process_match(MatchResult(match_id="m2", game="lol", team_a="c", team_b="d", winner="a"))
        games = trinity.get_games()
        assert "cs2" in games
        assert "lol" in games

    def test_get_elo_ratings(self):
        trinity = Trinity()
        trinity.process_match(MatchResult(match_id="m1", game="cs2", team_a="a", team_b="b", winner="a"))
        ratings = trinity.get_elo_ratings("cs2")
        assert "a" in ratings
        assert "b" in ratings
        assert ratings["a"].rating > 1500.0

    def test_get_glicko_ratings(self):
        trinity = Trinity()
        trinity.process_match(MatchResult(match_id="m1", game="cs2", team_a="a", team_b="b", winner="a"))
        ratings = trinity.get_glicko_ratings("cs2")
        assert "a" in ratings
        assert ratings["a"].mu > 1500.0

    def test_openskill_with_rosters(self):
        """OpenSkill should work when rosters are provided."""
        trinity = Trinity()
        for i in range(5):
            match = MatchResult(
                match_id=f"m{i}", game="cs2",
                team_a="NAVI", team_b="FaZe",
                winner="a",
                roster_a=["s1mple", "b1t", "electronic"],
                roster_b=["rain", "karrigan", "ropz"],
            )
            trinity.process_match(match)
        ratings = trinity.get_openskill_ratings("cs2")
        assert "s1mple" in ratings
        assert ratings["s1mple"].mu > MU_DEFAULT
        assert ratings["rain"].mu < MU_DEFAULT
        # Prediction should favor NAVI
        pred = trinity.predict("NAVI", "FaZe", game="cs2")
        assert pred.p_openskill > 0.5


# Import the default for comparison
from esports_v2.ratings.openskill_engine import MU_DEFAULT


class TestOpenSkillMissingRosterGuard:
    """Trinity spread/mean exclude uninformative OpenSkill (0.5) when no rosters."""

    def test_spread_excludes_openskill_when_no_roster(self):
        """Without rosters, spread is Elo vs Glicko-2 only (not inflated by 0.5)."""
        trinity = Trinity()
        # Process several matches to build Elo/Glicko-2 ratings (no rosters)
        for i in range(20):
            trinity.process_match(MatchResult(
                match_id=f"m{i}", game="cs2",
                team_a="Strong", team_b="Weak",
                winner="a",
            ))

        pred = trinity.predict("Strong", "Weak", "cs2")
        # Without fix: OpenSkill=0.5 pulls spread wide (Elo~0.7, Glicko~0.7, OS=0.5)
        # With fix: spread = |Elo - Glicko| which is small since both agree
        assert pred.p_openskill == pytest.approx(0.5, abs=0.01)
        assert pred.p_elo > 0.6  # Strong team has high Elo after 20 wins
        assert pred.p_glicko > 0.6  # Same for Glicko-2
        # Spread should be small (Elo ≈ Glicko, OpenSkill excluded)
        assert pred.trinity_spread < SPREAD_ABSTAIN, \
            f"Spread {pred.trinity_spread} >= {SPREAD_ABSTAIN}: OpenSkill=0.5 is inflating spread"

    def test_spread_includes_openskill_when_roster_present(self):
        """With rosters, all 3 systems contribute to spread/mean."""
        trinity = Trinity()
        for i in range(20):
            trinity.process_match(MatchResult(
                match_id=f"m{i}", game="lol",
                team_a="Alpha", team_b="Beta",
                winner="a",
                roster_a=["p1", "p2", "p3", "p4", "p5"],
                roster_b=["p6", "p7", "p8", "p9", "p10"],
            ))

        pred = trinity.predict("Alpha", "Beta", "lol")
        # OpenSkill should NOT be 0.5 since rosters were provided
        assert pred.p_openskill != pytest.approx(0.5, abs=0.01)

    def test_mean_uses_only_informative_systems(self):
        """Mean computed from Elo+Glicko-2 when OpenSkill is uninformative."""
        trinity = Trinity()
        for i in range(10):
            trinity.process_match(MatchResult(
                match_id=f"m{i}", game="cs2",
                team_a="X", team_b="Y",
                winner="a",
            ))

        pred = trinity.predict("X", "Y", "cs2")
        # Mean should be average of Elo and Glicko (not dragged toward 0.5 by OpenSkill)
        expected_mean = (pred.p_elo + pred.p_glicko) / 2
        assert pred.trinity_mean == pytest.approx(expected_mean, abs=0.001)
