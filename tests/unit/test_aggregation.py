"""Tests for extremized geometric mean of odds aggregation."""
from __future__ import annotations

import math

import pytest

from base_engine.features.aggregation import extremized_geometric_mean


class TestExtremizedGeometricMean:
    """Unit tests for extremized_geometric_mean()."""

    def test_egm_single_prob(self):
        """Single input returns itself (clipped to output range)."""
        assert extremized_geometric_mean([0.7]) == pytest.approx(0.7, abs=0.01)
        # Extreme single value gets clipped to [0.05, 0.95]
        assert extremized_geometric_mean([0.01]) == 0.05
        assert extremized_geometric_mean([0.99]) == 0.95

    def test_egm_two_equal(self):
        """Two identical probs return the same value (before extremization pushes it)."""
        # With d=1.0 (no extremization), two equal probs should return the same prob
        result = extremized_geometric_mean([0.7, 0.7], d=1.0)
        assert result == pytest.approx(0.7, abs=0.01)

    def test_egm_extremization(self):
        """d=2.0 pushes result further from 0.5 than d=1.0."""
        probs = [0.7, 0.8]
        mild = extremized_geometric_mean(probs, d=1.0)
        strong = extremized_geometric_mean(probs, d=2.0)
        # Both should be > 0.5
        assert mild > 0.5
        assert strong > 0.5
        # Stronger extremization pushes further from 0.5
        assert abs(strong - 0.5) > abs(mild - 0.5)

    def test_egm_weighted(self):
        """Weighted version gives more weight to the first probability."""
        # First prob high, second low; weighting toward first should push result up
        equal = extremized_geometric_mean([0.8, 0.4], d=1.0)
        weighted_high = extremized_geometric_mean(
            [0.8, 0.4], weights=[0.8, 0.2], d=1.0
        )
        weighted_low = extremized_geometric_mean(
            [0.8, 0.4], weights=[0.2, 0.8], d=1.0
        )
        assert weighted_high > equal
        assert weighted_low < equal

    def test_egm_empty_returns_half(self):
        """Empty list returns 0.5."""
        assert extremized_geometric_mean([]) == 0.5

    def test_egm_extreme_inputs(self):
        """Values near 0 and 1 don't produce NaN or inf."""
        result = extremized_geometric_mean([0.01, 0.99])
        assert math.isfinite(result)
        assert 0.05 <= result <= 0.95

        result2 = extremized_geometric_mean([0.001, 0.999], d=2.5)
        assert math.isfinite(result2)
        assert 0.05 <= result2 <= 0.95

    def test_egm_symmetric(self):
        """Opposing signals (0.7 and 0.3) should roughly cancel to ~0.5."""
        result = extremized_geometric_mean([0.7, 0.3], d=1.0)
        assert result == pytest.approx(0.5, abs=0.01)
