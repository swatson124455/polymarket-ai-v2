"""
Tests for MirrorCalibrationStack (bots/mirror_calibration.py).

Covers:
  - Unfitted returns identity / shadow returns None
  - S168 NO-side bypass (highest-impact fix)
  - MIRROR_USE_CALIBRATION gate
  - Shadow calibrate ignores gate, computes both sides
  - fit() wires sub-calibrators, handles exceptions
  - Output clipping [0.01, 0.99]
"""
import numpy as np
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from bots.mirror_calibration import MirrorCalibrationStack


# =========================================================================
# Helpers
# =========================================================================

def _make_fitted_stack():
    """Build a MirrorCalibrationStack with mock sub-calibrators."""
    stack = MirrorCalibrationStack(db=None)
    # FTS mock: shifts confidence by a known amount
    fts = MagicMock()
    fts.is_fitted = True
    fts.calibrate = MagicMock(side_effect=lambda p: p * 0.9)  # softens
    stack._fts = fts

    # HorizonBias mock: shifts by a known amount
    horizon = MagicMock()
    horizon.is_fitted = True
    horizon.calibrate = MagicMock(side_effect=lambda p, category="", ttr_days=None: p * 1.05)
    stack._horizon = horizon

    stack._fitted = True
    return stack


# =========================================================================
# Unfitted behavior
# =========================================================================

class TestMirrorCalibrationUnfitted:
    def test_calibrate_confidence_returns_identity(self):
        """Unfitted stack returns raw confidence unchanged."""
        stack = MirrorCalibrationStack(db=None)
        assert not stack._fitted
        for p in [0.10, 0.50, 0.75, 0.95]:
            assert stack.calibrate_confidence(p, category="politics", ttr_days=7.0, side="YES") == p

    def test_shadow_calibrate_returns_none(self):
        """Unfitted stack returns None from shadow_calibrate."""
        stack = MirrorCalibrationStack(db=None)
        result = stack.shadow_calibrate(0.70, category="sports", ttr_days=5.0, side="YES")
        assert result is None


# =========================================================================
# S168: NO-side bypass (critical)
# =========================================================================

class TestMirrorNoSideBypass:
    """S168: NO-side trades bypass calibration entirely."""

    def test_no_side_returns_raw_confidence(self):
        """NO-side returns raw confidence even when calibrators are fitted."""
        stack = _make_fitted_stack()
        with patch("bots.mirror_calibration.settings") as mock_settings:
            mock_settings.MIRROR_USE_CALIBRATION = True
            result = stack.calibrate_confidence(0.70, side="NO")
        assert result == 0.70

    def test_no_side_case_insensitive(self):
        """NO bypass works regardless of case."""
        stack = _make_fitted_stack()
        with patch("bots.mirror_calibration.settings") as mock_settings:
            mock_settings.MIRROR_USE_CALIBRATION = True
            for side in ["NO", "no", "No", "nO"]:
                assert stack.calibrate_confidence(0.65, side=side) == 0.65

    def test_yes_side_applies_calibration(self):
        """YES-side applies the full FTS + HorizonBias stack."""
        stack = _make_fitted_stack()
        with patch("bots.mirror_calibration.settings") as mock_settings:
            mock_settings.MIRROR_USE_CALIBRATION = True
            result = stack.calibrate_confidence(0.70, side="YES")
        # FTS: 0.70 * 0.9 = 0.63, HorizonBias: 0.63 * 1.05 = 0.6615
        assert result != 0.70
        assert abs(result - 0.6615) < 0.001

    def test_empty_side_applies_calibration(self):
        """Empty side string applies calibration (not treated as NO)."""
        stack = _make_fitted_stack()
        with patch("bots.mirror_calibration.settings") as mock_settings:
            mock_settings.MIRROR_USE_CALIBRATION = True
            result = stack.calibrate_confidence(0.70, side="")
        assert result != 0.70


# =========================================================================
# MIRROR_USE_CALIBRATION gate
# =========================================================================

class TestMirrorCalibrationGate:
    def test_gate_off_returns_identity(self):
        """When MIRROR_USE_CALIBRATION=False, returns raw confidence."""
        stack = _make_fitted_stack()
        with patch("bots.mirror_calibration.settings") as mock_settings:
            mock_settings.MIRROR_USE_CALIBRATION = False
            result = stack.calibrate_confidence(0.80, side="YES")
        assert result == 0.80

    def test_gate_on_applies_calibration(self):
        """When MIRROR_USE_CALIBRATION=True, calibration is applied."""
        stack = _make_fitted_stack()
        with patch("bots.mirror_calibration.settings") as mock_settings:
            mock_settings.MIRROR_USE_CALIBRATION = True
            result = stack.calibrate_confidence(0.80, side="YES")
        assert result != 0.80

    def test_gate_missing_returns_identity(self):
        """When MIRROR_USE_CALIBRATION attr missing, returns raw confidence."""
        stack = _make_fitted_stack()
        with patch("bots.mirror_calibration.settings") as mock_settings:
            # Remove the attribute so getattr returns False
            del mock_settings.MIRROR_USE_CALIBRATION
            mock_settings.configure_mock(**{})
            result = stack.calibrate_confidence(0.80, side="YES")
        assert result == 0.80


# =========================================================================
# Shadow calibrate (S121 dual-ledger)
# =========================================================================

class TestMirrorShadowCalibrate:
    def test_shadow_ignores_gate(self):
        """Shadow calibrate computes even when MIRROR_USE_CALIBRATION=False."""
        stack = _make_fitted_stack()
        with patch("bots.mirror_calibration.settings") as mock_settings:
            mock_settings.MIRROR_USE_CALIBRATION = False
            result = stack.shadow_calibrate(0.70, side="YES")
        assert result is not None
        assert result != 0.70

    def test_shadow_computes_no_side(self):
        """Shadow calibrate computes for NO side (unlike live which bypasses)."""
        stack = _make_fitted_stack()
        result = stack.shadow_calibrate(0.70, side="NO")
        assert result is not None
        assert result != 0.70  # Shadow always applies full stack

    def test_shadow_yes_and_no_both_computed(self):
        """Both sides produce calibrated values in shadow mode."""
        stack = _make_fitted_stack()
        yes_result = stack.shadow_calibrate(0.70, side="YES")
        no_result = stack.shadow_calibrate(0.70, side="NO")
        assert yes_result is not None
        assert no_result is not None
        # Both go through same pipeline in shadow mode
        assert abs(yes_result - no_result) < 0.001

    def test_shadow_returns_none_when_unfitted(self):
        """Shadow returns None when calibrators not fitted."""
        stack = MirrorCalibrationStack(db=None)
        assert stack.shadow_calibrate(0.70, side="YES") is None


# =========================================================================
# Output clipping
# =========================================================================

class TestMirrorCalibrationClipping:
    def test_output_clipped_low(self):
        """Output should not go below 0.01."""
        stack = _make_fitted_stack()
        # FTS mock returns near-zero: p * 0.9 for very low input
        with patch("bots.mirror_calibration.settings") as mock_settings:
            mock_settings.MIRROR_USE_CALIBRATION = True
            result = stack.calibrate_confidence(0.005, side="YES")
        assert result >= 0.01

    def test_output_clipped_high(self):
        """Output should not exceed 0.99."""
        stack = MirrorCalibrationStack(db=None)
        # Wire mocks that amplify confidence
        fts = MagicMock()
        fts.is_fitted = True
        fts.calibrate = MagicMock(return_value=0.999)
        horizon = MagicMock()
        horizon.is_fitted = True
        horizon.calibrate = MagicMock(return_value=1.05)  # above 1.0
        stack._fts = fts
        stack._horizon = horizon
        stack._fitted = True

        with patch("bots.mirror_calibration.settings") as mock_settings:
            mock_settings.MIRROR_USE_CALIBRATION = True
            result = stack.calibrate_confidence(0.99, side="YES")
        assert result <= 0.99


# =========================================================================
# fit() method
# =========================================================================

class TestMirrorCalibrationFit:
    @pytest.mark.asyncio
    async def test_fit_calls_both_sub_calibrators(self):
        """fit() creates and fits both FTS and HorizonBias."""
        mock_db = MagicMock()
        mock_db.session_factory = True
        stack = MirrorCalibrationStack(db=mock_db)

        mock_fts_inst = MagicMock()
        mock_fts_inst.fit_from_prediction_log = AsyncMock(return_value=True)
        mock_fts_inst.temperature = 1.5
        mock_fts_inst.gamma = 2.0
        mock_horizon_inst = MagicMock()
        mock_horizon_inst.fit_from_paper_trades = AsyncMock(return_value=True)

        with patch("base_engine.features.calibration.FocalTemperatureCalibrator",
                    return_value=mock_fts_inst) as MockFTS, \
             patch("base_engine.features.calibration.HorizonBiasCalibrator",
                    return_value=mock_horizon_inst) as MockHorizon:
            results = await stack.fit()

        assert results["fts"] is True
        assert results["horizon"] is True
        assert stack._fitted is True

    @pytest.mark.asyncio
    async def test_fit_fitted_when_either_succeeds(self):
        """Stack is fitted if either FTS or Horizon succeeds."""
        mock_db = MagicMock()
        mock_db.session_factory = True
        stack = MirrorCalibrationStack(db=mock_db)

        mock_fts_inst = MagicMock()
        mock_fts_inst.fit_from_prediction_log = AsyncMock(return_value=False)
        mock_horizon_inst = MagicMock()
        mock_horizon_inst.fit_from_paper_trades = AsyncMock(return_value=True)

        with patch("base_engine.features.calibration.FocalTemperatureCalibrator",
                    return_value=mock_fts_inst), \
             patch("base_engine.features.calibration.HorizonBiasCalibrator",
                    return_value=mock_horizon_inst):
            results = await stack.fit()

        assert results["fts"] is False
        assert results["horizon"] is True
        assert stack._fitted is True  # horizon succeeded

    @pytest.mark.asyncio
    async def test_fit_handles_exception_gracefully(self):
        """Exception during fit() doesn't crash — returns empty results."""
        stack = MirrorCalibrationStack(db=None)

        with patch("base_engine.features.calibration.FocalTemperatureCalibrator",
                    side_effect=ImportError("test")):
            results = await stack.fit()

        assert stack._fitted is False
        assert results == {}


# =========================================================================
# Category and TTR passthrough
# =========================================================================

class TestMirrorCalibrationPassthrough:
    def test_category_passed_to_horizon(self):
        """category parameter is forwarded to HorizonBias."""
        stack = _make_fitted_stack()
        with patch("bots.mirror_calibration.settings") as mock_settings:
            mock_settings.MIRROR_USE_CALIBRATION = True
            stack.calibrate_confidence(0.70, category="politics", ttr_days=15.0, side="YES")
        # Verify horizon.calibrate was called with category and ttr_days
        stack._horizon.calibrate.assert_called_once()
        call_kwargs = stack._horizon.calibrate.call_args
        assert call_kwargs[1]["category"] == "politics"
        assert call_kwargs[1]["ttr_days"] == 15.0
