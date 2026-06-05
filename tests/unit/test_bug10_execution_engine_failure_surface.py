"""S228 Bug 10: ExecutionEngine.place_order must surface CLOB-side failures.

Bug history:
  - Pre-fix execution_engine.py retry loop (S150 pattern) only continued on
    {success: False, retryable: True}. Any other non-success result fell
    through to `circuit_breaker.record_success(); break`, then the post-loop
    path extracted `order_id = result.get("id") or result.get("order_id")`,
    logged "Order placed but no order_id returned" (warning) + "Order placed"
    (info), and returned {success: True, order_id: None}.
  - Two failure modes hidden:
      a) Caller could not distinguish phantom orders from real ones —
         OrderGateway / position_manager / copy_trading_engine all check
         `result.get("success")` and would proceed as if the order succeeded.
      b) Circuit breaker recorded success on actual failures, clearing
         prior failure counts and defeating its purpose.
  - Surfaced S228 live flip #3 (2026-05-24): 4 distinct CLOB failures from
    Bug 9's AsyncClobClient ({success: False, error: 'CLOB client or request
    build failed'}) all logged as "Order placed" with order_id=None.

Fix:
  - In-loop: replaced unconditional `record_success(); break` with
    conditional that calls `record_success()` only when
    `order_result.get("success")` is True, else `record_failure()`.
  - Post-loop: added explicit failure surface that returns {success: False,
    error: <CLOB error>} when order_result.get("success") is False.

Cross-bot blast radius: ExecutionEngine is shared by 14 bots. The
behavior change converts the broken {success: True, order_id: None}
return value into {success: False, error: <CLOB error>}. Verified
OrderGateway (the primary caller) already handles failure paths
correctly (_execute_with_retry at order_gateway.py:1479+ checks
result.get("success") and routes failures through _PERMANENT_PATTERNS
classification + retry). Direct callers (position_manager,
copy_trading_engine) all prefer order_gateway via
`(self.order_gateway or self.execution_engine).place_order`.

These tests detect future regressions via behavioral + source-grep,
mirroring the S217/S218/S227 Bug 7/Bug 8/Bug 9 pattern.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from base_engine.execution.execution_engine import ExecutionEngine


# Well-formed test private key (same convention as test_contract_manager_approve.py)
_TEST_PRIVATE_KEY = "0x" + ("1" * 64)


def _build_engine(clob_response):
    """Construct ExecutionEngine with mocked deps + a clob_adapter that
    returns `clob_response` on every place_order call."""
    mock_client = MagicMock()
    mock_risk_manager = MagicMock()
    mock_risk_manager.check_risk_limits = AsyncMock(
        return_value={"allowed": True, "reasons": []}
    )
    mock_risk_manager.update_position = AsyncMock(return_value=None)
    mock_db = MagicMock()

    engine = ExecutionEngine(
        client=mock_client,
        risk_manager=mock_risk_manager,
        db=mock_db,
        private_key=_TEST_PRIVATE_KEY,
        kill_switch=None,
    )
    # Disable contract_manager so approval blocks are skipped
    engine.contract_manager = None

    # Install clob_adapter that returns the canned response
    mock_adapter = MagicMock()
    mock_adapter.available = True
    mock_adapter.place_order = AsyncMock(return_value=clob_response)
    engine.clob_adapter = mock_adapter

    return engine, mock_adapter


_VALID_MARKET_ID = "0x" + "a" * 64
_VALID_TOKEN_ID = "12345678901234567890"


class TestSuccessPath:
    """Sanity: success responses still pass through correctly post-fix."""

    @pytest.mark.asyncio
    async def test_success_response_returns_success(self):
        engine, _ = _build_engine(
            {"success": True, "order_id": "ord_abc123", "market_id": _VALID_MARKET_ID}
        )
        result = await engine.place_order(
            bot_name="TestBot",
            market_id=_VALID_MARKET_ID,
            token_id=_VALID_TOKEN_ID,
            side="YES",
            size=1.0,
            price=0.5,
            confidence=0.7,
            skip_position_update=True,
        )
        assert result["success"] is True
        assert result["order_id"] == "ord_abc123"

    @pytest.mark.asyncio
    async def test_success_records_circuit_breaker_success(self):
        engine, _ = _build_engine(
            {"success": True, "order_id": "ord_xyz", "market_id": _VALID_MARKET_ID}
        )
        cb_before = engine.circuit_breaker.failure_count
        await engine.place_order(
            bot_name="TestBot", market_id=_VALID_MARKET_ID, token_id=_VALID_TOKEN_ID,
            side="YES", size=1.0, price=0.5, confidence=0.7, skip_position_update=True,
        )
        # record_success clears failure_count to 0
        assert engine.circuit_breaker.failure_count == 0
        assert engine.circuit_breaker.state == engine.circuit_breaker.CLOSED


class TestFailureSurface:
    """Bug 10 primary: non-retryable failures must return as failures."""

    @pytest.mark.asyncio
    async def test_non_retryable_failure_returns_failure(self):
        """The exact failure shape from Bug 9 (CLOB client or request build
        failed) must now surface as {success: False, error: ...}, not the
        pre-fix {success: True, order_id: None}."""
        bug9_error = "CLOB client or request build failed"
        engine, _ = _build_engine({"success": False, "error": bug9_error})
        result = await engine.place_order(
            bot_name="MirrorBot", market_id=_VALID_MARKET_ID, token_id=_VALID_TOKEN_ID,
            side="YES", size=1.0, price=0.5, confidence=0.7, skip_position_update=True,
        )
        assert result["success"] is False, (
            "Pre-fix bug regressed: failure misclassified as success"
        )
        assert result["error"] == bug9_error
        assert result.get("order_id") is None or "order_id" not in result

    @pytest.mark.asyncio
    async def test_non_retryable_failure_records_circuit_breaker_failure(self):
        """The fix changes record_success() → record_failure() for
        non-retryable failures inside the retry loop."""
        engine, _ = _build_engine({"success": False, "error": "stub error"})
        cb_failures_before = engine.circuit_breaker.failure_count
        await engine.place_order(
            bot_name="TestBot", market_id=_VALID_MARKET_ID, token_id=_VALID_TOKEN_ID,
            side="YES", size=1.0, price=0.5, confidence=0.7, skip_position_update=True,
        )
        # record_failure should have incremented at least once. Pre-fix,
        # this counter was 0 because record_success was called instead.
        assert engine.circuit_breaker.failure_count > cb_failures_before, (
            "Pre-fix bug regressed: circuit_breaker recorded success on "
            "actual failure. The breaker would never trip when CLOB is "
            "consistently rejecting orders."
        )

    @pytest.mark.asyncio
    async def test_failure_missing_error_field_uses_default_message(self):
        """Defensive: clob might return {success: False} with no 'error' key.
        Should not crash; should still surface as failure with a default msg."""
        engine, _ = _build_engine({"success": False})
        result = await engine.place_order(
            bot_name="TestBot", market_id=_VALID_MARKET_ID, token_id=_VALID_TOKEN_ID,
            side="YES", size=1.0, price=0.5, confidence=0.7, skip_position_update=True,
        )
        assert result["success"] is False
        assert "error" in result and result["error"]  # some default message


class TestS228Bug10SourceRegression:
    """Source-grep regression tests mirroring S227 Bug 7 pattern."""

    def test_s228_bug10_marker_present(self):
        """Production source must contain the S228 Bug 10 marker.
        The fix adds the marker in BOTH the in-loop circuit-breaker fix
        AND the post-loop failure surface — expect at least 2 occurrences."""
        import inspect
        from base_engine.execution import execution_engine as ee_mod
        src = inspect.getsource(ee_mod)
        count = src.count("S228 Bug 10")
        assert count >= 2, (
            f"S228 Bug 10 marker missing from one or both fix sites "
            f"(found {count}, expected >=2). Was the patch reverted?"
        )

    def test_order_placement_failed_log_event_present(self):
        """The fix emits an order_placement_failed warning when CLOB returns
        failure. Operator visibility per CLAUDE.md 'Can't Fully Verify' rule."""
        import inspect
        from base_engine.execution import execution_engine as ee_mod
        src = inspect.getsource(ee_mod)
        assert "order_placement_failed" in src, (
            "order_placement_failed log event missing — silent regression "
            "risk for CLOB failure visibility."
        )

    def test_failure_surface_precedes_order_id_extraction(self):
        """The post-loop failure surface must appear BEFORE the
        `order_id = result.get(...)` line. Pre-fix that line was reached
        even on failures, producing fake 'Order placed' logs."""
        import inspect
        from base_engine.execution import execution_engine as ee_mod
        src = inspect.getsource(ee_mod)
        failure_marker = "order_placement_failed"
        order_id_marker = 'order_id = order_result.get("id")'
        f_idx = src.find(failure_marker)
        o_idx = src.find(order_id_marker)
        assert f_idx != -1 and o_idx != -1, "expected markers not found"
        assert f_idx < o_idx, (
            "Bug 10 failure surface must precede order_id extraction. "
            "If they got reordered, failures could still fall through as "
            "fake 'Order placed' events."
        )
