"""Tests for EsportsBot v2 Elo rating engine."""
import pytest

from esports_v2.ratings.elo import EloEngine, EloRating, _expected_score


class TestExpectedScore:
    """Test the Elo expected score formula."""

    def test_equal_ratings_give_50_50(self):
        assert _expected_score(1500.0, 1500.0) == pytest.approx(0.5)

    def test_higher_rated_favored(self):
        p = _expected_score(1600.0, 1400.0)
        assert p > 0.5
        assert p == pytest.approx(0.7597, abs=0.001)

    def test_symmetry(self):
        """P(a beats b) + P(b beats a) = 1.0."""
        p_ab = _expected_score(1600.0, 1400.0)
        p_ba = _expected_score(1400.0, 1600.0)
        assert p_ab + p_ba == pytest.approx(1.0)

    def test_large_difference(self):
        """400-point gap should give ~91% win probability."""
        p = _expected_score(1900.0, 1500.0)
        assert p == pytest.approx(0.9091, abs=0.001)


class TestEloEngine:
    """Test the Elo engine match processing."""

    def test_new_team_gets_default(self):
        engine = EloEngine()
        r = engine.get_rating("new_team")
        assert r.rating == 1500.0
        assert r.matches_played == 0

    def test_winner_gains_loser_loses(self):
        engine = EloEngine(k_factor=32)
        engine.process_match("team_a", "team_b", winner="a")
        ra = engine.get_rating("team_a")
        rb = engine.get_rating("team_b")
        assert ra.rating > 1500.0
        assert rb.rating < 1500.0
        assert ra.matches_played == 1
        assert rb.matches_played == 1

    def test_rating_sum_conserved(self):
        """Total Elo in the system is conserved."""
        engine = EloEngine(k_factor=32)
        engine.process_match("a", "b", winner="a")
        engine.process_match("b", "c", winner="b")
        engine.process_match("c", "a", winner="c")
        total = sum(r.rating for r in engine.get_all_ratings().values())
        expected = 1500.0 * 3  # 3 teams, each started at 1500
        assert total == pytest.approx(expected, abs=0.01)

    def test_draw_minimal_change(self):
        """Draw between equal teams should produce no change."""
        engine = EloEngine(k_factor=32)
        engine.process_match("a", "b", winner="draw")
        ra = engine.get_rating("a")
        rb = engine.get_rating("b")
        assert ra.rating == pytest.approx(1500.0, abs=0.01)
        assert rb.rating == pytest.approx(1500.0, abs=0.01)

    def test_upset_bigger_swing(self):
        """Low-rated team beating high-rated team should cause bigger change."""
        engine = EloEngine(k_factor=32)
        engine.set_rating("strong", EloRating(rating=1700.0, matches_played=50))
        engine.set_rating("weak", EloRating(rating=1300.0, matches_played=50))
        engine.process_match("weak", "strong", winner="a")  # upset
        # Weak team should gain a lot
        assert engine.get_rating("weak").rating > 1300.0 + 20  # big gain
        assert engine.get_rating("strong").rating < 1700.0 - 20  # big loss

    def test_predict_uses_current_ratings(self):
        engine = EloEngine(k_factor=32)
        engine.set_rating("good", EloRating(rating=1700.0))
        engine.set_rating("bad", EloRating(rating=1300.0))
        p = engine.predict("good", "bad")
        assert p > 0.8

    def test_match_count_increments(self):
        engine = EloEngine()
        assert engine.match_count == 0
        engine.process_match("a", "b", winner="a")
        assert engine.match_count == 1
        engine.process_match("a", "b", winner="b")
        assert engine.match_count == 2

    def test_consistent_winner_rating_increases_monotonically(self):
        """A team that always wins should have steadily increasing rating."""
        engine = EloEngine(k_factor=32)
        ratings = []
        for i in range(10):
            engine.process_match("winner", f"opponent_{i}", winner="a")
            ratings.append(engine.get_rating("winner").rating)
        # Each rating should be >= the previous
        for i in range(1, len(ratings)):
            assert ratings[i] >= ratings[i - 1]

    def test_k_factor_affects_magnitude(self):
        """Higher K-factor should produce larger rating changes."""
        engine_low = EloEngine(k_factor=16)
        engine_high = EloEngine(k_factor=64)
        engine_low.process_match("a", "b", winner="a")
        engine_high.process_match("a", "b", winner="a")
        change_low = abs(engine_low.get_rating("a").rating - 1500.0)
        change_high = abs(engine_high.get_rating("a").rating - 1500.0)
        assert change_high > change_low
