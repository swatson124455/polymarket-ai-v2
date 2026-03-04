"""
Unit tests for esports/models/series_model.py — PURE MATH tests.

Tests:
  - bo3_match_prob: favoured team, equal teams, reverse sweep, already won
  - bo5_match_prob: favoured team, equal teams at 2-2
  - Symmetry: bo3_match_prob(p, a, b) + bo3_match_prob(1-p, b, a) ~ 1.0
  - Probabilities always in [0, 1] range
  - detect_momentum_fallacy returns edge when overweighted
  - map_veto_adjusted_prob returns per-map probs
"""
import pytest

from esports.models.series_model import (
    bo3_match_prob,
    bo5_match_prob,
    map_veto_adjusted_prob,
    series_prob_with_map_veto,
    detect_momentum_fallacy,
)


# =========================================================================
# BO3 Match Probability
# =========================================================================


class TestBO3MatchProb:
    def test_favoured_team_from_zero(self):
        """55% per-map rate from 0-0 -> series prob > 0.5."""
        p = bo3_match_prob(0.55, 0, 0)
        assert p > 0.5

    def test_equal_teams_from_zero(self):
        """50% per-map rate from 0-0 -> series prob exactly 0.5."""
        p = bo3_match_prob(0.50, 0, 0)
        assert p == pytest.approx(0.5, abs=1e-6)

    def test_reverse_sweep_from_0_2(self):
        """55% per-map rate from 0-2 is impossible in BO3 (need 2 to win).
        Actually 0-2 means B already won. P(A wins) = 0.0."""
        p = bo3_match_prob(0.55, 0, 2)
        assert p == pytest.approx(0.0, abs=1e-6)

    def test_already_won_2_0(self):
        """A already at 2-0 in BO3 -> P = 1.0 (already won)."""
        p = bo3_match_prob(0.55, 2, 0)
        assert p == pytest.approx(1.0, abs=1e-6)

    def test_leading_1_0(self):
        """55% per-map rate from 1-0 -> higher than from 0-0."""
        p_from_10 = bo3_match_prob(0.55, 1, 0)
        p_from_00 = bo3_match_prob(0.55, 0, 0)
        assert p_from_10 > p_from_00

    def test_trailing_0_1(self):
        """55% per-map rate from 0-1 -> lower than from 0-0."""
        p_from_01 = bo3_match_prob(0.55, 0, 1)
        p_from_00 = bo3_match_prob(0.55, 0, 0)
        assert p_from_01 < p_from_00

    def test_1_1_with_strong_player(self):
        """At 1-1 in BO3, probability equals map win rate (one map left)."""
        p = bo3_match_prob(0.60, 1, 1)
        assert p == pytest.approx(0.60, abs=0.01)

    def test_result_in_zero_one_range(self):
        """Probability must always be in [0, 1]."""
        for rate in [0.01, 0.10, 0.30, 0.50, 0.70, 0.90, 0.99]:
            for a in range(3):
                for b in range(3):
                    p = bo3_match_prob(rate, a, b)
                    assert 0.0 <= p <= 1.0, f"Out of range: bo3({rate}, {a}, {b}) = {p}"

    def test_weak_team_from_zero(self):
        """30% per-map rate -> series prob < 0.5."""
        p = bo3_match_prob(0.30, 0, 0)
        assert p < 0.5


# =========================================================================
# BO5 Match Probability
# =========================================================================


class TestBO5MatchProb:
    def test_favoured_team_from_zero(self):
        """55% per-map rate from 0-0 -> series prob > 0.5."""
        p = bo5_match_prob(0.55, 0, 0)
        assert p > 0.5

    def test_equal_teams_at_2_2(self):
        """50% per-map rate at 2-2 -> exactly 0.5 (one map left)."""
        p = bo5_match_prob(0.50, 2, 2)
        assert p == pytest.approx(0.5, abs=1e-6)

    def test_strong_team_at_2_2(self):
        """60% per-map rate at 2-2 -> exactly 0.60 (one map left)."""
        p = bo5_match_prob(0.60, 2, 2)
        assert p == pytest.approx(0.60, abs=0.01)

    def test_already_won_3_0(self):
        """A already at 3-0 -> P = 1.0."""
        p = bo5_match_prob(0.55, 3, 0)
        assert p == pytest.approx(1.0, abs=1e-6)

    def test_already_lost_0_3(self):
        """A at 0-3 -> P = 0.0."""
        p = bo5_match_prob(0.55, 0, 3)
        assert p == pytest.approx(0.0, abs=1e-6)

    def test_reverse_sweep_0_2_in_bo5(self):
        """55% per-map rate from 0-2 in BO5 -> needs to win 3 straight.
        P = 0.55^3 ~ 0.166."""
        p = bo5_match_prob(0.55, 0, 2)
        assert p == pytest.approx(0.166, abs=0.01)

    def test_leading_2_0_in_bo5(self):
        """55% per-map from 2-0: need 1 more win out of up to 3 maps."""
        p = bo5_match_prob(0.55, 2, 0)
        # Should be very high but not 1.0
        assert p > 0.85
        assert p < 1.0

    def test_bo5_amplifies_skill_gap(self):
        """BO5 should amplify skill advantage more than BO3."""
        bo3 = bo3_match_prob(0.60, 0, 0)
        bo5 = bo5_match_prob(0.60, 0, 0)
        # In longer series, better team is even more likely to win
        assert bo5 > bo3

    def test_result_in_zero_one_range(self):
        """Probability must always be in [0, 1]."""
        for rate in [0.01, 0.25, 0.50, 0.75, 0.99]:
            for a in range(4):
                for b in range(4):
                    p = bo5_match_prob(rate, a, b)
                    assert 0.0 <= p <= 1.0, f"Out of range: bo5({rate}, {a}, {b}) = {p}"


# =========================================================================
# Symmetry Property
# =========================================================================


class TestSymmetry:
    def test_bo3_symmetry_from_zero(self):
        """P(A wins) + P(B wins) = 1.0 -> bo3(p, a, b) + bo3(1-p, b, a) ~ 1."""
        for p in [0.30, 0.45, 0.55, 0.70, 0.80]:
            pa = bo3_match_prob(p, 0, 0)
            pb = bo3_match_prob(1 - p, 0, 0)
            assert pa + pb == pytest.approx(1.0, abs=1e-6), (
                f"Symmetry broken: bo3({p}, 0, 0)={pa}, bo3({1-p}, 0, 0)={pb}"
            )

    def test_bo3_symmetry_from_1_0(self):
        """Symmetry holds at 1-0 score."""
        p = 0.55
        pa = bo3_match_prob(p, 1, 0)
        pb = bo3_match_prob(1 - p, 0, 1)
        assert pa + pb == pytest.approx(1.0, abs=1e-6)

    def test_bo5_symmetry_from_zero(self):
        """Symmetry holds for BO5."""
        for p in [0.30, 0.50, 0.65, 0.80]:
            pa = bo5_match_prob(p, 0, 0)
            pb = bo5_match_prob(1 - p, 0, 0)
            assert pa + pb == pytest.approx(1.0, abs=1e-6)

    def test_bo5_symmetry_from_1_2(self):
        """Symmetry at 1-2 score in BO5."""
        p = 0.60
        pa = bo5_match_prob(p, 1, 2)
        pb = bo5_match_prob(1 - p, 2, 1)
        assert pa + pb == pytest.approx(1.0, abs=1e-6)


# =========================================================================
# Map Veto Adjusted Probability
# =========================================================================


class TestMapVetoAdjustedProb:
    def test_returns_list_of_probs(self):
        """Returns a list with one entry per map in veto order."""
        team_a = {"inferno": 0.60, "mirage": 0.55, "dust2": 0.50}
        team_b = {"inferno": 0.50, "mirage": 0.55, "dust2": 0.45}
        veto_order = ["inferno", "mirage", "dust2"]
        probs = map_veto_adjusted_prob(team_a, team_b, veto_order)
        assert len(probs) == 3

    def test_all_probs_in_valid_range(self):
        """All probs must be in [0.05, 0.95] (clamped range)."""
        team_a = {"inferno": 0.90, "mirage": 0.10, "dust2": 0.50}
        team_b = {"inferno": 0.10, "mirage": 0.90, "dust2": 0.50}
        veto_order = ["inferno", "mirage", "dust2"]
        probs = map_veto_adjusted_prob(team_a, team_b, veto_order)
        for p in probs:
            assert 0.05 <= p <= 0.95

    def test_equal_teams_equal_probs(self):
        """If both teams have same win rate, adjusted prob is 0.5."""
        team_a = {"inferno": 0.55, "mirage": 0.55}
        team_b = {"inferno": 0.55, "mirage": 0.55}
        veto_order = ["inferno", "mirage"]
        probs = map_veto_adjusted_prob(team_a, team_b, veto_order)
        for p in probs:
            assert p == pytest.approx(0.50, abs=0.01)

    def test_stronger_team_gets_higher_prob(self):
        """Team A stronger on a map -> higher adjusted prob."""
        team_a = {"inferno": 0.70}
        team_b = {"inferno": 0.40}
        veto_order = ["inferno"]
        probs = map_veto_adjusted_prob(team_a, team_b, veto_order)
        assert probs[0] > 0.50

    def test_unknown_map_defaults_to_0_5(self):
        """Maps not in either team's dict default to 0.5."""
        team_a = {}
        team_b = {}
        veto_order = ["ancient"]
        probs = map_veto_adjusted_prob(team_a, team_b, veto_order)
        assert probs[0] == pytest.approx(0.50, abs=0.01)


# =========================================================================
# Series Prob with Map Veto
# =========================================================================


class TestSeriesProbWithMapVeto:
    def test_empty_veto_order_returns_0_5(self):
        """No maps in veto order -> returns 0.5."""
        p = series_prob_with_map_veto({}, {}, [])
        assert p == pytest.approx(0.5, abs=1e-6)

    def test_already_won(self):
        """A already won the series -> 1.0."""
        p = series_prob_with_map_veto(
            {"m1": 0.55, "m2": 0.55, "m3": 0.55},
            {"m1": 0.45, "m2": 0.45, "m3": 0.45},
            ["m1", "m2", "m3"],
            maps_won_a=2, maps_won_b=0,
        )
        assert p == pytest.approx(1.0, abs=1e-6)


# =========================================================================
# Momentum Fallacy Detection
# =========================================================================


class TestMomentumFallacy:
    def test_returns_edge_when_blowout_and_market_overreacts(self):
        """Large margin + significant market adjustment -> fallacy detected."""
        edge = detect_momentum_fallacy(map_margin=13, market_adjustment=0.10)
        assert edge is not None
        assert edge == pytest.approx(0.05, abs=0.01)  # 0.10 * 0.5

    def test_returns_none_when_margin_small(self):
        """Small margin (< 8) -> no fallacy even with market adjustment."""
        edge = detect_momentum_fallacy(map_margin=5, market_adjustment=0.10)
        assert edge is None

    def test_returns_none_when_adjustment_small(self):
        """Large margin but small market adjustment (< 3%) -> no fallacy."""
        edge = detect_momentum_fallacy(map_margin=13, market_adjustment=0.02)
        assert edge is None

    def test_returns_none_when_both_small(self):
        """Both margin and adjustment small -> no fallacy."""
        edge = detect_momentum_fallacy(map_margin=3, market_adjustment=0.01)
        assert edge is None

    def test_negative_margin_still_detects(self):
        """Negative margin (other team won) is handled by abs()."""
        edge = detect_momentum_fallacy(map_margin=-10, market_adjustment=-0.08)
        assert edge is not None
        assert edge == pytest.approx(-0.04, abs=0.01)

    def test_exact_threshold_margin(self):
        """Margin = 8 exactly + adjustment = 0.03 exactly -> fallacy detected."""
        edge = detect_momentum_fallacy(map_margin=8, market_adjustment=0.03)
        assert edge is not None
        assert edge == pytest.approx(0.015, abs=0.001)

    def test_just_below_threshold_margin(self):
        """Margin = 7 (below 8) -> not detected."""
        edge = detect_momentum_fallacy(map_margin=7, market_adjustment=0.10)
        assert edge is None

    def test_just_below_threshold_adjustment(self):
        """Adjustment = 0.029 (below 0.03) -> not detected."""
        edge = detect_momentum_fallacy(map_margin=13, market_adjustment=0.029)
        assert edge is None
