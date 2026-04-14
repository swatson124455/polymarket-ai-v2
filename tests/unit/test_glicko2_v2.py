"""Tests for EsportsBot v2 Glicko-2 rating engine."""
import pytest

from esports_v2.ratings.glicko2 import (
    Glicko2Engine,
    Glicko2Rating,
    MU_DEFAULT,
    PHI_DEFAULT,
    SIGMA_DEFAULT,
)


class TestGlicko2Rating:
    """Test the Glicko2Rating dataclass."""

    def test_defaults(self):
        r = Glicko2Rating()
        assert r.mu == MU_DEFAULT
        assert r.phi == PHI_DEFAULT
        assert r.sigma == SIGMA_DEFAULT

    def test_interval_95(self):
        r = Glicko2Rating(mu=1500.0, phi=100.0)
        lo, hi = r.interval_95
        assert lo == pytest.approx(1300.0)
        assert hi == pytest.approx(1700.0)

    def test_roundtrip_scale(self):
        """Converting to Glicko-2 scale and back should be identity."""
        r = Glicko2Rating(mu=1600.0, phi=200.0, sigma=0.05)
        mu2, phi2 = r.to_glicko2_scale()
        r2 = Glicko2Rating.from_glicko2_scale(mu2, phi2, r.sigma)
        assert r2.mu == pytest.approx(r.mu, abs=0.001)
        assert r2.phi == pytest.approx(r.phi, abs=0.001)
        assert r2.sigma == pytest.approx(r.sigma)


class TestGlicko2Engine:
    """Test the Glicko-2 engine match processing."""

    def test_new_team_gets_default(self):
        engine = Glicko2Engine()
        r = engine.get_rating("new_team")
        assert r.mu == MU_DEFAULT
        assert r.phi == PHI_DEFAULT

    def test_winner_rating_increases(self):
        engine = Glicko2Engine()
        engine.process_match("a", "b", winner="a")
        ra = engine.get_rating("a")
        rb = engine.get_rating("b")
        assert ra.mu > MU_DEFAULT
        assert rb.mu < MU_DEFAULT

    def test_uncertainty_decreases_with_games(self):
        """Phi (RD) should decrease as a team plays more games."""
        engine = Glicko2Engine()
        initial_phi = engine.get_rating("a").phi
        engine.process_match("a", "b", winner="a")
        after_one = engine.get_rating("a").phi
        assert after_one < initial_phi

    def test_predict_equal_teams(self):
        engine = Glicko2Engine()
        p = engine.predict("a", "b")
        assert p == pytest.approx(0.5, abs=0.01)

    def test_predict_favors_higher_rated(self):
        engine = Glicko2Engine()
        engine.set_rating("strong", Glicko2Rating(mu=1700.0, phi=100.0, sigma=0.06))
        engine.set_rating("weak", Glicko2Rating(mu=1300.0, phi=100.0, sigma=0.06))
        p = engine.predict("strong", "weak")
        assert p > 0.7

    def test_match_count(self):
        engine = Glicko2Engine()
        assert engine.match_count == 0
        engine.process_match("a", "b", winner="a")
        assert engine.match_count == 1

    def test_matches_played_tracks(self):
        engine = Glicko2Engine()
        engine.process_match("a", "b", winner="a")
        engine.process_match("a", "c", winner="a")
        assert engine.get_rating("a").matches_played == 2
        assert engine.get_rating("b").matches_played == 1

    def test_consistent_winner_increases_monotonically(self):
        """A team that always wins should gain rating monotonically."""
        engine = Glicko2Engine()
        ratings = []
        for i in range(10):
            engine.process_match("winner", f"opp_{i}", winner="a")
            ratings.append(engine.get_rating("winner").mu)
        for i in range(1, len(ratings)):
            assert ratings[i] >= ratings[i - 1]

    def test_set_rating_persists(self):
        engine = Glicko2Engine()
        engine.set_rating("x", Glicko2Rating(mu=1800.0, phi=50.0, sigma=0.04, matches_played=100))
        r = engine.get_rating("x")
        assert r.mu == 1800.0
        assert r.phi == 50.0
        assert r.matches_played == 100

    def test_to_dict(self):
        r = Glicko2Rating(mu=1600.0, phi=100.0, sigma=0.05, matches_played=10)
        d = r.to_dict()
        assert d["rating"] == 1600.0
        assert d["deviation"] == 100.0
        assert d["volatility"] == 0.05
        assert d["matches_played"] == 10
