"""Tests for EsportsBot v2 OpenSkill rating engine."""
import pytest

from esports_v2.ratings.openskill_engine import (
    OpenSkillEngine,
    PlayerRating,
    MU_DEFAULT,
    SIGMA_DEFAULT,
)


class TestPlayerRating:
    """Test the PlayerRating dataclass."""

    def test_defaults(self):
        r = PlayerRating()
        assert r.mu == MU_DEFAULT
        assert r.sigma == SIGMA_DEFAULT
        assert r.matches_played == 0

    def test_to_dict(self):
        r = PlayerRating(mu=30.0, sigma=5.0, matches_played=10)
        d = r.to_dict()
        assert d["rating"] == 30.0
        assert d["deviation"] == 5.0
        assert d["matches_played"] == 10


class TestOpenSkillEngine:
    """Test the OpenSkill engine match processing."""

    def test_no_roster_returns_50_50(self):
        engine = OpenSkillEngine()
        p = engine.predict("a", "b")
        assert p == 0.5

    def test_process_match_updates_players(self):
        engine = OpenSkillEngine()
        engine.process_match(
            "team_a", "team_b", winner="a",
            roster_a=["p1", "p2"],
            roster_b=["p3", "p4"],
        )
        # Winners should gain, losers should lose
        r_p1 = engine.get_player_rating("p1")
        r_p3 = engine.get_player_rating("p3")
        assert r_p1.mu > MU_DEFAULT
        assert r_p3.mu < MU_DEFAULT
        assert r_p1.matches_played == 1
        assert r_p3.matches_played == 1

    def test_predict_favors_higher_rated_team(self):
        engine = OpenSkillEngine()
        # Give team_a's players high ratings
        for pid in ["p1", "p2", "p3"]:
            engine.set_player_rating(pid, PlayerRating(mu=35.0, sigma=3.0))
        # Give team_b's players low ratings
        for pid in ["p4", "p5", "p6"]:
            engine.set_player_rating(pid, PlayerRating(mu=15.0, sigma=3.0))
        # Set rosters
        engine._team_rosters["strong"] = ["p1", "p2", "p3"]
        engine._team_rosters["weak"] = ["p4", "p5", "p6"]
        p = engine.predict("strong", "weak")
        assert p > 0.7

    def test_roster_update_on_match(self):
        engine = OpenSkillEngine()
        engine.process_match(
            "team_a", "team_b", winner="a",
            roster_a=["p1", "p2"],
            roster_b=["p3", "p4"],
        )
        assert engine.get_roster("team_a") == ["p1", "p2"]
        assert engine.get_roster("team_b") == ["p3", "p4"]

    def test_roster_change_reflected_in_prediction(self):
        engine = OpenSkillEngine()
        # Play 5 matches with original roster
        for _ in range(5):
            engine.process_match(
                "team_a", "team_b", winner="a",
                roster_a=["p1", "p2"],
                roster_b=["p3", "p4"],
            )
        # team_a should be favored
        p_before = engine.predict("team_a", "team_b")
        assert p_before > 0.5
        # Now swap team_a's roster to completely new players
        engine._team_rosters["team_a"] = ["new1", "new2"]
        p_after = engine.predict("team_a", "team_b")
        # New players are unrated, so team_a's advantage should shrink
        assert p_after < p_before

    def test_match_count(self):
        engine = OpenSkillEngine()
        assert engine.match_count == 0
        engine.process_match("a", "b", winner="a", roster_a=["p1"], roster_b=["p2"])
        assert engine.match_count == 1

    def test_get_all_ratings_returns_all_players(self):
        engine = OpenSkillEngine()
        engine.process_match(
            "a", "b", winner="a",
            roster_a=["p1", "p2"],
            roster_b=["p3", "p4"],
        )
        all_r = engine.get_all_ratings()
        assert set(all_r.keys()) == {"p1", "p2", "p3", "p4"}

    def test_team_mu_average(self):
        engine = OpenSkillEngine()
        engine.set_player_rating("p1", PlayerRating(mu=30.0))
        engine.set_player_rating("p2", PlayerRating(mu=20.0))
        engine._team_rosters["team"] = ["p1", "p2"]
        assert engine.get_team_mu("team") == pytest.approx(25.0)

    def test_no_roster_match_still_counts(self):
        """Match without rosters should still increment counter."""
        engine = OpenSkillEngine()
        engine.process_match("a", "b", winner="a")
        assert engine.match_count == 1

    def test_draw_handling(self):
        engine = OpenSkillEngine()
        engine.process_match(
            "a", "b", winner="draw",
            roster_a=["p1"], roster_b=["p2"],
        )
        r_p1 = engine.get_player_rating("p1")
        r_p2 = engine.get_player_rating("p2")
        # Draw between equal teams should produce similar ratings
        assert abs(r_p1.mu - r_p2.mu) < 1.0
