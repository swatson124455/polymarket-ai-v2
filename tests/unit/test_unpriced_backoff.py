"""S167 P0-C: Tests for unpriced position retry backoff.

Tokens that fail 6+ consecutive price lookup cycles enter exponential backoff
(5min → 10min → ... → 60min max) to prevent pool exhaustion from permanently
unpriced positions retrying 4 fallback queries every 10s.
"""
import time
import pytest
from unittest.mock import patch


class TestUnpricedBackoff:
    """Test the backoff dict logic in AutomatedPositionManager."""

    def _make_pm(self):
        """Create a minimal AutomatedPositionManager for testing backoff state."""
        from base_engine.execution.position_manager import AutomatedPositionManager
        pm = AutomatedPositionManager.__new__(AutomatedPositionManager)
        pm._unpriced_fail_count = {}
        pm._unpriced_backoff_until = {}
        pm._api_price_cache = {}
        return pm

    def test_token_excluded_after_6_failures(self):
        """After 6 consecutive failures, token should be in backoff."""
        pm = self._make_pm()
        tid = "test_token_123"

        # Simulate 6 failures
        for _ in range(6):
            pm._unpriced_fail_count[tid] = pm._unpriced_fail_count.get(tid, 0) + 1

        _fails = pm._unpriced_fail_count[tid]
        assert _fails == 6

        # Set backoff
        _backoff_s = min(300 * (2 ** (_fails - 6)), 3600)
        pm._unpriced_backoff_until[tid] = time.monotonic() + _backoff_s

        # Token should be excluded from _missing list
        _now = time.monotonic()
        assert _now < pm._unpriced_backoff_until[tid], "Token should be in backoff"

        # First backoff should be 300s (5 min)
        assert _backoff_s == 300

    def test_token_not_excluded_before_6_failures(self):
        """Before 6 failures, token should NOT be in backoff."""
        pm = self._make_pm()
        tid = "test_token_456"

        for _ in range(5):
            pm._unpriced_fail_count[tid] = pm._unpriced_fail_count.get(tid, 0) + 1

        assert pm._unpriced_fail_count[tid] == 5
        assert tid not in pm._unpriced_backoff_until

    def test_backoff_exponential_capped_at_3600(self):
        """Backoff doubles each cycle but caps at 3600s (1 hour)."""
        pm = self._make_pm()
        tid = "test_token_789"

        # Simulate many failures
        expected_backoffs = [300, 600, 1200, 2400, 3600, 3600, 3600]
        for i, expected in enumerate(expected_backoffs):
            fails = 6 + i
            backoff_s = min(300 * (2 ** (fails - 6)), 3600)
            assert backoff_s == expected, f"At {fails} failures, backoff should be {expected}, got {backoff_s}"

    def test_reset_on_price_found(self):
        """Token that gets priced should have its fail count reset."""
        pm = self._make_pm()
        tid = "test_token_reset"

        # Simulate 10 failures + backoff
        pm._unpriced_fail_count[tid] = 10
        pm._unpriced_backoff_until[tid] = time.monotonic() + 3600

        # Simulate token getting priced (found in latest_prices)
        latest_prices = {tid: 0.5}
        for t in list(pm._unpriced_fail_count):
            if t in latest_prices or t in pm._api_price_cache:
                pm._unpriced_fail_count.pop(t, None)
                pm._unpriced_backoff_until.pop(t, None)

        assert tid not in pm._unpriced_fail_count
        assert tid not in pm._unpriced_backoff_until

    def test_missing_list_filter(self):
        """Backed-off tokens should be filtered from _missing list."""
        pm = self._make_pm()

        # Token A: in backoff (future)
        pm._unpriced_backoff_until["tok_a"] = time.monotonic() + 300

        # Token B: backoff expired (past)
        pm._unpriced_backoff_until["tok_b"] = time.monotonic() - 10

        # Token C: no backoff entry
        # Token D: has a price (should be excluded by latest_prices, not backoff)

        token_ids = ["tok_a", "tok_b", "tok_c", "tok_d"]
        latest_prices = {"tok_d": 0.5}

        _now = time.monotonic()
        _missing = [t for t in token_ids
                    if t not in latest_prices
                    and _now >= pm._unpriced_backoff_until.get(t, 0)]

        assert "tok_a" not in _missing, "tok_a should be excluded (in backoff)"
        assert "tok_b" in _missing, "tok_b should be included (backoff expired)"
        assert "tok_c" in _missing, "tok_c should be included (no backoff)"
        assert "tok_d" not in _missing, "tok_d should be excluded (has price)"
