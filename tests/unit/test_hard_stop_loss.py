"""S172 D7: Tests for shared inviolable hard stop-loss in risk_manager.py.

Verifies:
  - Each bot's default threshold fires correctly
  - Absolute -50% floor cannot be loosened by env vars
  - Positions above threshold are NOT exited
  - Boundary conditions at exact threshold
"""
import pytest
from unittest.mock import MagicMock, patch
from base_engine.risk.risk_manager import RiskManager


class _CleanSettings:
    """Minimal settings object with NO hard stop env vars — forces code defaults."""
    pass


@pytest.fixture
def risk_manager():
    db = MagicMock()
    return RiskManager(db=db)


class TestHardStopDefaults:
    """Per-bot default thresholds: WB -25%, MB -30%, EB -50%.

    NOTE: Tests patch settings to use the plan-specified defaults, not whatever
    is currently in settings.py (which may differ, e.g. EB has 0.40 in settings).
    The code-level defaults in _HARD_STOP_DEFAULTS are the plan values.
    """

    def test_wb_fires_at_minus_25(self, risk_manager):
        with patch("base_engine.risk.risk_manager.settings", _CleanSettings()):
            result = risk_manager.check_hard_stop_loss("WeatherBot", pnl_pct=-0.26)
        assert result["should_exit"] is True
        assert result["reason"] == "hard_stop_loss"

    def test_wb_holds_at_minus_24(self, risk_manager):
        with patch("base_engine.risk.risk_manager.settings", _CleanSettings()):
            result = risk_manager.check_hard_stop_loss("WeatherBot", pnl_pct=-0.24)
        assert result["should_exit"] is False

    def test_wb_boundary_exact_minus_25(self, risk_manager):
        with patch("base_engine.risk.risk_manager.settings", _CleanSettings()):
            result = risk_manager.check_hard_stop_loss("WeatherBot", pnl_pct=-0.25)
        assert result["should_exit"] is True

    def test_mb_fires_at_minus_31(self, risk_manager):
        with patch("base_engine.risk.risk_manager.settings", _CleanSettings()):
            result = risk_manager.check_hard_stop_loss("MirrorBot", pnl_pct=-0.31)
        assert result["should_exit"] is True

    def test_mb_holds_at_minus_29(self, risk_manager):
        with patch("base_engine.risk.risk_manager.settings", _CleanSettings()):
            result = risk_manager.check_hard_stop_loss("MirrorBot", pnl_pct=-0.29)
        assert result["should_exit"] is False

    def test_eb_fires_at_minus_51(self, risk_manager):
        with patch("base_engine.risk.risk_manager.settings", _CleanSettings()):
            result = risk_manager.check_hard_stop_loss("EsportsBot", pnl_pct=-0.51)
        assert result["should_exit"] is True

    def test_eb_holds_at_minus_49(self, risk_manager):
        with patch("base_engine.risk.risk_manager.settings", _CleanSettings()):
            result = risk_manager.check_hard_stop_loss("EsportsBot", pnl_pct=-0.49)
        assert result["should_exit"] is False


class TestAbsoluteFloor:
    """No env var can loosen the hard stop beyond -50%."""

    def test_env_var_cannot_exceed_50pct(self, risk_manager):
        """Even if WEATHER_HARD_STOP_LOSS_PCT=0.90, stop fires at -50%."""
        with patch("base_engine.risk.risk_manager.settings") as ms:
            ms.WEATHER_HARD_STOP_LOSS_PCT = 0.90  # Try to set 90%
            result = risk_manager.check_hard_stop_loss("WeatherBot", pnl_pct=-0.51)
            assert result["should_exit"] is True
            assert result["details"]["hard_stop"] == 0.50  # Clamped to floor

    def test_env_var_can_tighten(self, risk_manager):
        """WEATHER_HARD_STOP_LOSS_PCT=0.15 tightens WB stop to -15%."""
        with patch("base_engine.risk.risk_manager.settings") as ms:
            ms.WEATHER_HARD_STOP_LOSS_PCT = 0.15
            result = risk_manager.check_hard_stop_loss("WeatherBot", pnl_pct=-0.16)
            assert result["should_exit"] is True
            assert result["details"]["hard_stop"] == 0.15


class TestUnknownBot:
    """Unknown bots get the 0.30 default."""

    def test_unknown_bot_default_30pct(self, risk_manager):
        result = risk_manager.check_hard_stop_loss("SomeNewBot", pnl_pct=-0.31)
        assert result["should_exit"] is True

    def test_unknown_bot_holds_at_29(self, risk_manager):
        result = risk_manager.check_hard_stop_loss("SomeNewBot", pnl_pct=-0.29)
        assert result["should_exit"] is False


class TestReturnStructure:
    """Verify the return dict has all expected keys."""

    def test_exit_result_has_reason(self, risk_manager):
        result = risk_manager.check_hard_stop_loss("MirrorBot", pnl_pct=-0.31)
        assert result["reason"] == "hard_stop_loss"
        assert "details" in result
        assert "bot_name" in result["details"]
        assert "pnl_pct" in result["details"]
        assert "hard_stop" in result["details"]

    def test_hold_result_has_empty_reason(self, risk_manager):
        result = risk_manager.check_hard_stop_loss("MirrorBot", pnl_pct=-0.10)
        assert result["reason"] == ""
        assert result["should_exit"] is False


class TestNoEdgeParameter:
    """Verify the method does NOT accept remaining_edge (removed per audit fix #1)."""

    def test_no_remaining_edge_param(self, risk_manager):
        """check_hard_stop_loss should NOT accept remaining_edge."""
        import inspect
        sig = inspect.signature(risk_manager.check_hard_stop_loss)
        param_names = list(sig.parameters.keys())
        assert "remaining_edge" not in param_names
        assert param_names == ["bot_name", "pnl_pct"]
