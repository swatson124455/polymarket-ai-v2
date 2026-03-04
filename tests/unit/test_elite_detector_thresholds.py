"""
Unit tests for EliteUserDetector threshold changes.
Verifies: relaxed thresholds (min_trades=5, min_profit=0) allow top traders with many trades.
"""
from unittest.mock import MagicMock

from base_engine.learning.elite_detector import EliteUserDetector, ELITE_THRESHOLDS


def test_elite_thresholds_relaxed():
    """Elite thresholds should be relaxed: min_trades low, min_profit=0."""
    assert ELITE_THRESHOLDS["min_trades"] <= 10
    assert ELITE_THRESHOLDS["min_profit"] == 0.0
    assert 0.5 <= ELITE_THRESHOLDS["min_win_rate"] <= 0.6


def test_elite_detector_accepts_custom_thresholds():
    """EliteUserDetector accepts custom thresholds (e.g. min_profit=0)."""
    detector = EliteUserDetector(MagicMock(), thresholds={"min_trades": 5, "min_win_rate": 0.52, "min_profit": 0.0})
    assert detector.thresholds["min_profit"] == 0.0
    assert detector.thresholds["min_trades"] == 5
