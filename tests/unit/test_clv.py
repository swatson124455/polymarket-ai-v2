"""Tests for B5: CLV tracking with Shin's method."""
import pytest

from esports_v2.model.clv import (
    compute_clv_single,
    enrich_with_clv,
    odds_to_implied,
)


class TestOddsToImplied:
    def test_even_odds(self):
        """2.00 vs 2.00 = 50/50 (no overround)."""
        p_a, p_b = odds_to_implied(2.0, 2.0)
        assert abs(p_a - 0.5) < 0.01
        assert abs(p_b - 0.5) < 0.01
        assert abs(p_a + p_b - 1.0) < 0.01

    def test_heavy_favorite(self):
        """1.20 vs 5.00 — heavy favorite."""
        p_a, p_b = odds_to_implied(1.20, 5.00)
        assert p_a > 0.75
        assert p_b < 0.25
        assert abs(p_a + p_b - 1.0) < 0.02

    def test_real_pinnacle_cs2_match(self):
        """
        Issue 4: Real Pinnacle odds from a CS2 match.

        Pinnacle line: Natus Vincere 1.45 vs Vitality 2.75
        Raw implied: 1/1.45 = 0.6897, 1/2.75 = 0.3636, sum = 1.0533 (5.3% overround)
        After Shin devigging, fair probabilities should sum to ~1.0
        and Na'Vi should be ~65-67%.
        """
        p_navi, p_vitality = odds_to_implied(1.45, 2.75)
        # Must sum to ~1.0 (within rounding)
        assert abs(p_navi + p_vitality - 1.0) < 0.02
        # Na'Vi should be favored ~65-67%
        assert 0.62 < p_navi < 0.72
        # Vitality should be ~28-35%
        assert 0.28 < p_vitality < 0.38

    def test_real_pinnacle_lol_match(self):
        """
        Issue 4: Real Pinnacle odds from a LoL match.

        Pinnacle line: T1 1.30 vs Gen.G 3.50
        Raw implied: 1/1.30 = 0.7692, 1/3.50 = 0.2857, sum = 1.0549 (5.5% overround)
        """
        p_t1, p_geng = odds_to_implied(1.30, 3.50)
        assert abs(p_t1 + p_geng - 1.0) < 0.02
        assert 0.70 < p_t1 < 0.78
        assert 0.22 < p_geng < 0.30

    def test_close_match_overround_removal(self):
        """
        Issue 4: Close match with typical Pinnacle overround.

        Pinnacle line: 1.87 vs 1.95
        Raw sum: 1/1.87 + 1/1.95 = 0.5348 + 0.5128 = 1.0476 (4.8% overround)
        After devigging, should be close to 51/49 split.
        """
        p_a, p_b = odds_to_implied(1.87, 1.95)
        assert abs(p_a + p_b - 1.0) < 0.02
        assert 0.49 < p_a < 0.54
        assert 0.46 < p_b < 0.51
        # A is slightly favored (lower odds = higher implied)
        assert p_a > p_b

    def test_invalid_odds(self):
        """Odds <= 1.0 should return 0.5/0.5."""
        p_a, p_b = odds_to_implied(0.5, 2.0)
        assert p_a == 0.5
        assert p_b == 0.5


class TestComputeCLVSingle:
    def test_positive_clv(self):
        # Model says 0.65, Pinnacle implied ~0.60
        clv = compute_clv_single(0.65, 1.65, 2.40)
        assert clv is not None
        assert clv > 0

    def test_negative_clv(self):
        # Model says 0.50, Pinnacle implied ~0.69
        clv = compute_clv_single(0.50, 1.45, 2.75)
        assert clv is not None
        assert clv < 0

    def test_no_odds(self):
        assert compute_clv_single(0.5, None, None) is None

    def test_invalid_odds(self):
        assert compute_clv_single(0.5, 0.5, 2.0) is None


class TestEnrichWithCLV:
    def test_adds_fields(self):
        preds = [
            {"match_id": "m1", "p_model": 0.65, "pinnacle_odds_a": 1.65, "pinnacle_odds_b": 2.40},
            {"match_id": "m2", "p_model": 0.50},  # No odds
        ]
        enriched = enrich_with_clv(preds)
        assert enriched[0]["pinnacle_prob"] is not None
        assert enriched[0]["clv"] is not None
        assert enriched[1]["pinnacle_prob"] is None
        assert enriched[1]["clv"] is None

    def test_external_lookup(self):
        preds = [{"match_id": "m1", "p_model": 0.65}]
        lookup = {"m1": (1.65, 2.40)}
        enriched = enrich_with_clv(preds, odds_lookup=lookup)
        assert enriched[0]["clv"] is not None
        assert enriched[0]["clv"] > 0
