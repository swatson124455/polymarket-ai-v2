"""
Unit tests for Poly Data pipeline fixes.
Verifies: goldsky/processed dir creation, import script logic, prediction fallback.
"""
import sys
import pytest
from pathlib import Path

# Ensure project root on path
_project_root = Path(__file__).resolve().parent.parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))


def test_goldsky_dir_creation():
    """_ensure_goldsky_dir creates goldsky folder."""
    from unittest.mock import patch

    try:
        from poly_data.update_utils.update_goldsky import _ensure_goldsky_dir
    except ImportError:
        import pytest
        pytest.skip("poly_data not on path - run from project root")
    with patch("os.makedirs") as mock_makedirs:
        _ensure_goldsky_dir()
        mock_makedirs.assert_called_once_with("goldsky", exist_ok=True)


def test_process_live_has_makedirs():
    """process_live source includes makedirs for processed/."""
    pl_path = _project_root / "poly_data" / "update_utils" / "process_live.py"
    src = pl_path.read_text()
    assert 'makedirs("processed"' in src or "makedirs('processed'" in src


def test_import_script_has_fetch_historical():
    """Import script has fetch-historical-prices logic."""
    imp_path = _project_root / "scripts" / "import_poly_data_to_db.py"
    src = imp_path.read_text()
    assert "--no-fetch-historical-prices" in src
    assert "fetch_historical_prices" in src
    assert "total_t == 0" in src and "total_m > 0" in src


def test_prediction_fallback_uses_coalesce():
    """Price fallback query uses COALESCE for liquidity/volume."""
    pe_path = _project_root / "base_engine" / "prediction" / "prediction_engine.py"
    src = pe_path.read_text()
    assert "COALESCE(m.liquidity" in src
    assert "COALESCE(m.volume" in src
