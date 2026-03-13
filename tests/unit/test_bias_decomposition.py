"""Tests for esports bias decomposition (Le 2026)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import numpy as np
import pytest

from esports.calibration.bias_decomposition import (
    EsportsBiasDecomposition,
    _recalibrate_prob,
)


# ---------------------------------------------------------------------------
# Recalibration unit tests
# ---------------------------------------------------------------------------


class TestRecalibrate:
    def test_recalibrate_identity(self):
        """b=1.0 returns input unchanged (within clipping bounds)."""
        bd = EsportsBiasDecomposition()
        bd._game_params["lol"] = {"b": 1.0}
        for p in [0.1, 0.3, 0.5, 0.6, 0.8, 0.9]:
            result = bd.recalibrate(p, "lol")
            assert abs(result - p) < 1e-6, f"p={p} result={result}"

    def test_recalibrate_underconfident(self):
        """b=1.5 pushes 0.6 toward extremes (>0.6)."""
        bd = EsportsBiasDecomposition()
        bd._game_params["cs2"] = {"b": 1.5}
        result = bd.recalibrate(0.6, "cs2")
        assert result > 0.6, f"Expected >0.6, got {result}"

    def test_recalibrate_overconfident(self):
        """b=0.7 pushes 0.6 toward 0.5 (<0.6)."""
        bd = EsportsBiasDecomposition()
        bd._game_params["dota2"] = {"b": 0.7}
        result = bd.recalibrate(0.6, "dota2")
        assert result < 0.6, f"Expected <0.6, got {result}"

    def test_recalibrate_unfitted_game(self):
        """Unknown game returns raw_prob."""
        bd = EsportsBiasDecomposition()
        assert bd.recalibrate(0.73, "unknown_game") == 0.73

    def test_recalibrate_symmetry_at_half(self):
        """p=0.5 should stay at 0.5 for any b."""
        for b in [0.5, 0.7, 1.0, 1.5, 2.0]:
            result = _recalibrate_prob(0.5, b)
            assert abs(result - 0.5) < 1e-6, f"b={b} result={result}"

    def test_recalibrate_clipping(self):
        """Extreme inputs are clipped to safe ranges."""
        bd = EsportsBiasDecomposition()
        bd._game_params["lol"] = {"b": 1.0}
        # Very low input clipped to 0.05 output minimum
        assert bd.recalibrate(0.001, "lol") >= 0.05
        # Very high input clipped to 0.95 output maximum
        assert bd.recalibrate(0.999, "lol") <= 0.95


# ---------------------------------------------------------------------------
# fit_from_db tests
# ---------------------------------------------------------------------------


def _make_mock_db(rows):
    """Build a mock db object matching the get_session() pattern."""
    mock_result = MagicMock()
    mock_result.__iter__ = lambda self: iter(rows)

    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    class _SessionCtx:
        async def __aenter__(self):
            return mock_session

        async def __aexit__(self, *args):
            pass

    mock_db = MagicMock()
    mock_db.get_session = MagicMock(return_value=_SessionCtx())
    return mock_db


def _make_rows(game, n, bias=0.0, noise=0.05):
    """Generate synthetic prediction rows with controlled bias.

    predicted_prob ~ Uniform(0.2, 0.8) + bias
    actual_outcome = 1 if predicted_prob > 0.5 else 0 (with noise)
    """
    rng = np.random.RandomState(42)
    rows = []
    for _ in range(n):
        p = rng.uniform(0.2, 0.8) + bias
        p = np.clip(p, 0.05, 0.95)
        # actual outcome correlated with true probability
        outcome = 1.0 if rng.random() < (p - bias) else 0.0
        hours = rng.uniform(1.0, 48.0)
        rows.append(
            SimpleNamespace(
                game=game,
                predicted_prob=p,
                actual_outcome=outcome,
                hours_to_resolve=hours,
            )
        )
    return rows


class TestFitFromDb:
    @pytest.mark.asyncio
    async def test_fit_basic(self):
        """Mock DB rows with known bias, verify b is in reasonable range."""
        rows = _make_rows("lol", 100, bias=0.05)
        mock_db = _make_mock_db(rows)

        bd = EsportsBiasDecomposition()
        result = await bd.fit_from_db(mock_db, games=["lol"], days=90)

        assert "lol" in result
        params = result["lol"]
        assert 0.5 <= params["b"] <= 2.0
        assert params["n_samples"] == 100
        assert "base_bias" in params
        assert "ece" in params
        assert "horizon_corr" in params
        # Base bias should be positive (predicted > actual on average)
        assert params["base_bias"] > 0

    @pytest.mark.asyncio
    async def test_fit_insufficient_data(self):
        """<30 rows returns no params for that game."""
        rows = _make_rows("cs2", 15)
        mock_db = _make_mock_db(rows)

        bd = EsportsBiasDecomposition()
        result = await bd.fit_from_db(mock_db, games=["cs2"], days=90)

        assert "cs2" not in result
        assert bd.game_params == {}

    @pytest.mark.asyncio
    async def test_fit_no_db(self):
        """None db returns empty dict."""
        bd = EsportsBiasDecomposition()
        result = await bd.fit_from_db(None, games=["lol"], days=90)
        assert result == {}

    def test_game_params_property(self):
        """game_params returns a copy of the internal dict."""
        bd = EsportsBiasDecomposition()
        bd._game_params["lol"] = {"b": 1.2, "base_bias": 0.03, "ece": 0.05, "horizon_corr": 0.1, "n_samples": 50}
        params = bd.game_params
        assert params == bd._game_params
        # Mutating copy should not affect internal state
        params["lol"]["b"] = 999.0
        assert bd._game_params["lol"]["b"] == 1.2
