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
from unittest.mock import AsyncMock

import pytest

from base_engine.coordination import trade_coordinator as tc_mod
from base_engine.coordination.trade_coordinator import TradeCoordinator


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


# --- S245: _is_paper must be bound on every path (SELL/exit included) ---------
#
# confirm_position reads `_is_paper` at `if not _is_paper and not _is_sell:`
# (the WI-11 audit gate). Python evaluates `not _is_paper` FIRST, so _is_paper
# must be bound even when the branch is ultimately a SELL. Pre-fix the only
# assignment lived inside the reserve-skipped BUY branch, so a SELL/exit (or a
# BUY that took the reserving-row path) raised UnboundLocalError — failing the
# DB persist with the order already filled on-chain (the DB-vs-chain drift that
# surfaced live the moment trading resumed after the S245 pUSD funding fix).


class _Result:
    def __init__(self, obj):
        self._obj = obj

    def scalar_one_or_none(self):
        return self._obj

    def fetchone(self):
        return None


class _Session:
    """Minimal async-context-manager session: hands back queued results."""

    def __init__(self, results):
        self._results = list(results)
        self.commits = 0

    async def execute(self, *a, **k):
        return self._results.pop(0) if self._results else _Result(None)

    async def commit(self):
        self.commits += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _Pos:
    def __init__(self, side, status):
        self.side = side
        self.status = status
        self.size = 0.0
        self.entry_price = 0.0
        self.current_price = 0.0
        self.entry_cost = 0.0
        self.breakeven_price = 0.0
        self.source_bot = None


class _DB:
    def __init__(self, session):
        self.session_factory = object()  # truthy → confirm_position proceeds
        self._session = session
        self.insert_trade_event = AsyncMock()

    def get_session(self):
        return self._session


class TestS245IsPaperAlwaysBound:
    """Behavioral + source regression for the SELL-path UnboundLocalError."""

    @pytest.mark.asyncio
    async def test_confirm_position_sell_does_not_raise_unbound(self):
        reserving_sell = _Pos("SELL", "reserving")
        orig_yes = _Pos("YES", "open")
        session = _Session([_Result(reserving_sell), _Result(orig_yes)])
        coord = TradeCoordinator(db=_DB(session), bot_id="MirrorBot")
        # Pre-fix this raised UnboundLocalError on _is_paper. Must complete.
        await coord.confirm_position(
            market_id="0x" + "8" * 64, side="SELL", size=2.6,
            entry_price=0.44, bot_id="MirrorBot", token_id="123",
        )
        assert reserving_sell.status == "closed"   # SELL audit row closed
        assert orig_yes.status == "closed"          # original YES position closed
        assert session.commits >= 1

    def _src(self):
        return inspect.getsource(tc_mod.TradeCoordinator.confirm_position)

    def test_is_paper_bound_before_retry_loop(self):
        lines = self._src().splitlines()
        assign_idx = next((i for i, ln in enumerate(lines)
                           if ln.strip().startswith("_is_paper =")), -1)
        loop_idx = next((i for i, ln in enumerate(lines)
                         if "for _attempt in range(_MAX_PERSIST_ATTEMPTS)" in ln), -1)
        assert assign_idx >= 0, "_is_paper must be assigned in confirm_position"
        assert loop_idx >= 0, "retry-loop marker not found"
        assert assign_idx < loop_idx, (
            "_is_paper must be bound BEFORE the retry loop so it is defined on "
            "every path (incl. SELL/exit) at the WI-11 audit gate (S245 regression)."
        )

    def test_single_top_level_is_paper_assignment(self):
        """The hoisted binding must be the ONLY _is_paper assignment — guards
        against re-introducing a branch-local one as a shadow."""
        assigns = [ln.strip() for ln in self._src().splitlines()
                   if ln.strip().startswith("_is_paper =")]
        assert len(assigns) == 1, (
            f"expected exactly one top-level _is_paper assignment, found "
            f"{len(assigns)}: {assigns}"
        )
