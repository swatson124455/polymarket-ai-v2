"""Import-level test for risk_manager to catch syntax/import errors in CI."""
import pytest


def test_risk_manager_module_imports():
    """Import base_engine.risk.risk_manager at module level; fails if syntax or import error."""
    import base_engine.risk.risk_manager as risk_manager  # noqa: F401
    assert risk_manager.RiskManager is not None
    assert hasattr(risk_manager.RiskManager, "check_risk_limits")
