"""Tests for Chronos-2 price trajectory forecaster (Tier 3C)."""

import pytest

from base_engine.prediction.chronos_forecaster import ChronosForecaster


class TestSignalMultiplier:
    """Test signal multiplier logic (no torch/chronos dependency)."""

    def test_neutral_on_none(self):
        cf = ChronosForecaster()
        assert cf.get_signal_multiplier(None, 0.5) == 1.0

    def test_neutral_on_flat_trend(self):
        cf = ChronosForecaster()
        forecast = {"trend_signal": 0, "confidence_width": 0.1}
        assert cf.get_signal_multiplier(forecast, 0.5) == 1.0

    def test_positive_trend_narrow_confidence(self):
        cf = ChronosForecaster()
        forecast = {"trend_signal": 1, "confidence_width": 0.1}
        mult = cf.get_signal_multiplier(forecast, 0.5)
        assert mult > 1.0
        assert mult <= 1.2

    def test_negative_trend_narrow_confidence(self):
        cf = ChronosForecaster()
        forecast = {"trend_signal": -1, "confidence_width": 0.1}
        mult = cf.get_signal_multiplier(forecast, 0.5)
        assert mult < 1.0
        assert mult >= 0.8

    def test_wide_confidence_dampens_signal(self):
        cf = ChronosForecaster()
        # Wide interval = low confidence = closer to 1.0
        forecast_wide = {"trend_signal": 1, "confidence_width": 0.8}
        forecast_narrow = {"trend_signal": 1, "confidence_width": 0.1}
        mult_wide = cf.get_signal_multiplier(forecast_wide, 0.5)
        mult_narrow = cf.get_signal_multiplier(forecast_narrow, 0.5)
        assert mult_narrow > mult_wide

    def test_multiplier_bounds(self):
        cf = ChronosForecaster()
        # Even extreme inputs stay in [0.8, 1.2]
        forecast = {"trend_signal": 1, "confidence_width": 0.0}
        assert cf.get_signal_multiplier(forecast, 0.5) <= 1.2
        forecast = {"trend_signal": -1, "confidence_width": 0.0}
        assert cf.get_signal_multiplier(forecast, 0.5) >= 0.8


class TestAvailability:
    def test_not_available_without_torch(self):
        """is_available should return False when torch/chronos not installed."""
        cf = ChronosForecaster()
        # On dev machines without torch, this should be False
        # On VPS with torch installed, this would be True
        # Either way, the property should not raise
        result = cf.is_available
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_forecast_returns_none_without_torch(self):
        """forecast() gracefully returns None when chronos not available."""
        cf = ChronosForecaster()
        if not cf.is_available:
            result = await cf.forecast("test_market", 0.5, price_history=[0.5] * 20)
            assert result is None
