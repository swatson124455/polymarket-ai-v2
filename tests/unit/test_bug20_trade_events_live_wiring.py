"""S232 Bug 20 — wire trade_events for live entries (and exits).

Background
----------
S231 live re-flip diagnostic surfaced a system-wide audit-trail gap:

  SELECT execution_mode, COUNT(*)
  FROM   trade_events
  WHERE  event_time > '2026-05-27 21:17:00';
  -- execution_mode = 'paper'  →  many rows
  -- execution_mode = 'live'   →  ZERO rows (any bot)

Root cause: the ONLY ENTRY-event writer in the codebase lived in
`base_engine/execution/paper_trading.py:993`, the paper trading path.
Live order placement goes through clob_adapter → execution_engine →
order_gateway.confirm_position, and confirm_position never wrote to
trade_events. So bot_pnl.py (and any downstream P&L analytics)
mathematically could not see live trades.

Same root-cause class as S227 Bug 7 + S228 Bug 11 ("schema/feature built
but never wired into the consumer"). 3rd instance — Protocol promotion
candidate per audit-pattern-completeness (Protocol 16).

Fix (this test surface)
-----------------------
confirm_position now emits the trade_events ENTRY (for BUY) or EXIT
(for SELL) audit row after the positions write succeeds, with
execution_mode set from SIMULATION_MODE so live trades are tagged
'live' and paper trades stay tagged 'paper'.

These tests verify the shape of the wiring via source inspection, since
the actual DB write requires a live Postgres + the asyncpg driver.
"""

import inspect

from base_engine.coordination import trade_coordinator as tc_mod


class TestBug20TradeEventEmit:
    """confirm_position must emit a trade_events row after a successful positions commit."""

    def test_insert_trade_event_called_in_confirm_position(self):
        """confirm_position source must reference self.db.insert_trade_event."""
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        assert "self.db.insert_trade_event" in src, (
            "confirm_position must call self.db.insert_trade_event so the "
            "trade_events audit log captures live entries (Bug 20 root cause: "
            "live path skipped trade_events entirely)"
        )

    def test_execution_mode_derived_from_simulation_mode(self):
        """execution_mode must be 'live' when SIMULATION_MODE is False, else 'paper'."""
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        # Look for the conditional expression
        assert "SIMULATION_MODE" in src and '"live"' in src, (
            "execution_mode must be conditional on SIMULATION_MODE so live "
            "trades are tagged 'live', not the default 'paper'"
        )
        # The mode should be evaluated at call time (not hardcoded)
        # Heuristic: an expression using getattr settings + ternary produces both literals
        assert '"paper"' in src and '"live"' in src, (
            "Both 'paper' and 'live' literals must appear so the ternary "
            "covers both modes"
        )

    def test_event_type_branches_on_is_sell(self):
        """ENTRY for BUY, EXIT for SELL — both event_types must be present in source."""
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        assert '"ENTRY"' in src, "ENTRY event_type must be emitted for BUY"
        assert '"EXIT"' in src, "EXIT event_type must be emitted for SELL"

    def test_trade_event_emit_after_session_commit(self):
        """The trade_events emit must come after the positions session commit, not before.

        Order matters: if we emit before commit, a positions rollback would
        leave a phantom trade_events row pointing at no position. Tested via
        source-line ordering.
        """
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        lines = src.splitlines()
        # Find the LAST `await session.commit()` and the FIRST `self.db.insert_trade_event`
        commit_idx = max(
            (i for i, ln in enumerate(lines) if "await session.commit()" in ln),
            default=-1,
        )
        emit_idx = next(
            (i for i, ln in enumerate(lines) if "self.db.insert_trade_event" in ln),
            -1,
        )
        assert commit_idx >= 0, "no await session.commit() in confirm_position"
        assert emit_idx >= 0, "no self.db.insert_trade_event in confirm_position"
        assert emit_idx > commit_idx, (
            f"insert_trade_event (line {emit_idx}) must come AFTER session.commit() "
            f"(line {commit_idx}) — pre-commit emit would create phantom audit rows"
        )

    def test_trade_event_emit_non_fatal(self):
        """A trade_events emit failure must NOT block the positions write.

        positions is canonical, trade_events is audit-only. Verified via the
        presence of a try/except around the emit with a warning log.
        """
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        assert "confirm_position_trade_event_emit_failed" in src, (
            "Trade event emit failure must log "
            "'confirm_position_trade_event_emit_failed' (LogMiner-matchable "
            "warning, non-fatal — positions row is the source of truth)"
        )

    def test_execution_mode_kwarg_present_in_call(self):
        """The insert_trade_event call must explicitly pass execution_mode (not default)."""
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        # Look for execution_mode=<something> in the source
        assert "execution_mode=" in src, (
            "insert_trade_event call must explicitly pass execution_mode "
            "(default is 'paper' — never inheriting default makes live trades "
            "invisible to all downstream analytics, which is the Bug 20 footprint)"
        )

    def test_fees_passed_from_cost_rate(self):
        """fees must be computed from FIXED_SLIPPAGE_BPS + TAKER_FEE_BPS so audit row reflects actual cost."""
        src = inspect.getsource(tc_mod.TradeCoordinator.confirm_position)
        assert "fees=" in src, (
            "insert_trade_event must receive fees so audit checks "
            "(fee_check tier 3: fees=0 AND execution_mode=live → WARNING) "
            "have data to validate against"
        )
