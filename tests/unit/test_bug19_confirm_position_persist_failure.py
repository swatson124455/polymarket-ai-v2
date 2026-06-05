"""S232 Bug 19 — confirm_position persist failure escalation.

Background
----------
S231 live re-flip surfaced an in-memory-vs-DB divergence: 22 Mirror trade
executions per counter, only ~21 corresponding rows in the positions table,
and a SELL row at 03:01:18 May 28 for market 0x481e603aa927 with NO paired
entry row in the live window. Root cause: confirm_position swallowed any
DB exception (statement timeout, transaction abort) with a single
`logger.warning("confirm_position failed: %s", e)` and returned None. The
caller in order_gateway had already updated _open_positions; the bot
continued to manage a position whose DB record never landed. On restart,
that position becomes invisible (restore reads from DB).

Two observed DB error classes during S231 live window:
  - asyncpg QueryCanceledError (statement timeout)
  - InFailedSQLTransactionError (transaction abort after prior error)

Fix (this test surface)
-----------------------
1. Source-level: confirm_position now retries ONCE on any exception with
   a 1s backoff before escalating.
2. On final failure: logger.critical with structured fields (market_id,
   bot_id, side, size, entry_price, is_sell, attempts, error, action).
   LogMiner picks up the CRITICAL level; Bug 18 alerting bridge will
   eventually fan this out.
3. We do NOT raise — the order succeeded on-chain. Raising would make
   the caller mark the trade as failed and the on-chain position would
   become fully unmanaged (worse than the orphan).

These tests verify the shape of the fix via source inspection (mirroring
the pattern from test_bug11_restore_is_paper_filter.py). Full integration
tests require a DB; that's covered indirectly by the broader test surface.
"""

import inspect

from base_engine.coordination import trade_coordinator as tc_mod


class TestBug19RetryShape:
    """confirm_position must retry once and escalate to CRITICAL on final failure."""

    def test_function_signature_unchanged(self):
        """Bug 19 fix must preserve the existing signature (per CLAUDE.md Rule 2)."""
        sig = inspect.signature(tc_mod.TradeCoordinator.confirm_position)
        params = list(sig.parameters.keys())
        assert params == [
            "self", "market_id", "side", "size", "entry_price",
            "source_bot", "bot_id", "token_id",
        ], f"signature changed: {params}"
        assert sig.return_annotation is None, "return annotation should remain None"

    def test_retry_loop_present(self):
        """A retry loop (for _attempt in range(...)) must wrap the DB ops."""
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        assert "_MAX_PERSIST_ATTEMPTS" in src, (
            "confirm_position must define _MAX_PERSIST_ATTEMPTS for the retry loop"
        )
        assert "for _attempt in range(_MAX_PERSIST_ATTEMPTS)" in src, (
            "confirm_position must contain a retry loop over attempts"
        )

    def test_backoff_present(self):
        """A backoff sleep must run between retry attempts."""
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        assert "asyncio.sleep" in src, (
            "confirm_position must sleep between retry attempts"
        )

    def test_critical_log_on_final_failure(self):
        """Final failure must log at CRITICAL level (not warning)."""
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        assert "logger.critical" in src, (
            "Final persist failure must escalate to logger.critical for "
            "operator visibility (Bug 19 root cause was logger.warning swallow)"
        )

    def test_critical_log_event_name(self):
        """The CRITICAL log must use the canonical event name so LogMiner / Bug 18 can match."""
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        assert '"confirm_position_persist_failed"' in src, (
            "CRITICAL log must use event 'confirm_position_persist_failed' for "
            "downstream pattern matching"
        )

    def test_no_silent_warning_swallow(self):
        """The pre-fix `logger.warning("confirm_position failed: %s", e)` swallow path is gone."""
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        assert '"confirm_position failed: %s"' not in src, (
            "The silent warning swallow that hid Bug 19 must be removed"
        )

    def test_structured_fields_in_critical_log(self):
        """CRITICAL log must include the fields operator needs to reconcile."""
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        # Must include at least market_id, bot_id, side, size, error, action
        for field in ("market_id=market_id", "bot_id=which_bot", "side=side",
                      "size=size", "error=", "action="):
            assert field in src, f"CRITICAL log missing structured field: {field}"

    def test_no_raise_in_failure_path(self):
        """The fix must NOT raise to caller (would make on-chain position unmanaged).

        Inspecting source: there should be no bare `raise` after the retry loop
        ends. The function falls through to logger.critical and then returns
        implicitly.
        """
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        # The function should end with the logger.critical block, not a raise.
        # A `raise` inside the retry loop's except block would short-circuit
        # the retry and propagate immediately — we want neither.
        lines = src.splitlines()
        # Find the position of the logger.critical line
        critical_line_idx = next(
            (i for i, ln in enumerate(lines) if "logger.critical" in ln), -1
        )
        assert critical_line_idx >= 0, "logger.critical not found in function"
        # Lines AFTER logger.critical block until end-of-function must not contain `raise`
        tail = "\n".join(lines[critical_line_idx:])
        # Allow raise inside docstrings/comments (none expected), but the tail
        # should not contain a top-level `raise` statement.
        for ln in tail.splitlines():
            stripped = ln.strip()
            if stripped.startswith("raise ") or stripped == "raise":
                raise AssertionError(
                    "confirm_position must not raise in the failure path — "
                    f"found: {stripped!r}"
                )

    def test_retry_count_at_least_one(self):
        """_MAX_PERSIST_ATTEMPTS must be at least 2 (1 initial + 1 retry)."""
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        # Source line should be `_MAX_PERSIST_ATTEMPTS = N` with N >= 2
        for ln in src.splitlines():
            if "_MAX_PERSIST_ATTEMPTS = " in ln and "#" not in ln.split("=")[0]:
                # Extract integer value
                val_str = ln.split("=", 1)[1].strip().split()[0].split("#")[0].strip()
                try:
                    n = int(val_str)
                except ValueError:
                    continue
                assert n >= 2, (
                    f"_MAX_PERSIST_ATTEMPTS={n} — must be at least 2 (1 initial + 1 retry)"
                )
                return
        raise AssertionError("Could not find _MAX_PERSIST_ATTEMPTS assignment with integer value")
