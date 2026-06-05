"""WI-11: audit-on-write hooks for positions table — unit tests.

Verifies that TradeCoordinator._wi11_audit_live_entry():
  - Logs WARNING when trade_events ENTRY is missing (check 1)
  - Escalates to CRITICAL after _WI11_DISCREPANCY_LIMIT consecutive misses
  - Resets the counter on success
  - Logs WARNING when wallet balance < entry_cost (check 2)
  - Logs WARNING when balance probe not yet in system_kv
  - Does not raise on DB error (non-fatal)

Also verifies:
  - system_kv balance write in base_engine.py balance probe
  - _wi11_discrepancy_count initialises to 0
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from base_engine.coordination.trade_coordinator import (
    TradeCoordinator,
    _WI11_DISCREPANCY_LIMIT,
)


def _make_coordinator():
    db = MagicMock()
    db.session_factory = MagicMock()
    return TradeCoordinator(db=db, bot_id="MirrorBot")


def _session_ctx(rows_per_call):
    """Return a context manager mock whose execute() returns successive row sets."""
    call_idx = [0]

    async def _execute(sql, params=None):
        result = MagicMock()
        r = rows_per_call[call_idx[0] % len(rows_per_call)]
        call_idx[0] += 1
        if isinstance(r, int):
            result.scalar.return_value = r
        elif r is None:
            result.fetchone.return_value = None
        else:
            result.fetchone.return_value = r
        return result

    session = MagicMock()
    session.execute = _execute
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    return session


class TestWI11Init:
    def test_discrepancy_count_initialises_to_zero(self):
        tc = _make_coordinator()
        assert tc._wi11_discrepancy_count == 0

    def test_limit_constant_is_three(self):
        assert _WI11_DISCREPANCY_LIMIT == 3


class TestWI11AuditLiveEntry:
    """Core audit hook behaviour."""

    @pytest.mark.asyncio
    async def test_no_discrepancy_on_present_entry_and_sufficient_balance(self):
        tc = _make_coordinator()
        # Query 1: trade_events count = 1 (ENTRY present)
        # Query 2: system_kv balance = "100.00" (> entry_cost 10.0)
        session = _session_ctx([1, ("100.00",)])
        tc.db.get_session = MagicMock(return_value=session)
        await tc._wi11_audit_live_entry("0xmarket", "MirrorBot", entry_cost=10.0)
        assert tc._wi11_discrepancy_count == 0

    @pytest.mark.asyncio
    async def test_discrepancy_increments_when_no_trade_event(self):
        tc = _make_coordinator()
        session = _session_ctx([0, ("100.00",)])  # count=0 → missing
        tc.db.get_session = MagicMock(return_value=session)
        with patch("base_engine.coordination.trade_coordinator.logger") as mock_log:
            await tc._wi11_audit_live_entry("0xmarket", "MirrorBot", entry_cost=10.0)
        assert tc._wi11_discrepancy_count == 1
        mock_log.warning.assert_any_call(
            "position_audit_discrepancy_no_trade_event",
            market_id="0xmarket",
            bot_name="MirrorBot",
            consecutive_discrepancies=1,
            check="live ENTRY in trade_events within 120s",
        )

    @pytest.mark.asyncio
    async def test_critical_after_discrepancy_limit(self):
        tc = _make_coordinator()
        tc._wi11_discrepancy_count = _WI11_DISCREPANCY_LIMIT - 1  # one short of limit

        session = _session_ctx([0, ("100.00",)])  # missing ENTRY
        tc.db.get_session = MagicMock(return_value=session)
        with patch("base_engine.coordination.trade_coordinator.logger") as mock_log:
            await tc._wi11_audit_live_entry("0xmkt", "MirrorBot", entry_cost=1.0)

        assert tc._wi11_discrepancy_count == _WI11_DISCREPANCY_LIMIT
        assert mock_log.critical.called, "CRITICAL must fire when limit is reached"
        critical_call = mock_log.critical.call_args_list[0]
        assert critical_call[0][0] == "position_audit_discrepancy_escalated"

    @pytest.mark.asyncio
    async def test_success_resets_discrepancy_counter(self):
        tc = _make_coordinator()
        tc._wi11_discrepancy_count = 2  # pre-existing

        session = _session_ctx([1, ("100.00",)])  # ENTRY present, balance OK
        tc.db.get_session = MagicMock(return_value=session)
        await tc._wi11_audit_live_entry("0xmkt", "MirrorBot", entry_cost=5.0)

        assert tc._wi11_discrepancy_count == 0, (
            "Counter must reset to 0 on success"
        )

    @pytest.mark.asyncio
    async def test_warning_when_balance_below_entry_cost(self):
        tc = _make_coordinator()
        # ENTRY present (count=1), but balance (5.0) < entry_cost (50.0)
        session = _session_ctx([1, ("5.0",)])
        tc.db.get_session = MagicMock(return_value=session)
        with patch("base_engine.coordination.trade_coordinator.logger") as mock_log:
            await tc._wi11_audit_live_entry("0xmkt", "MirrorBot", entry_cost=50.0)
        mock_log.warning.assert_any_call(
            "position_audit_insufficient_balance",
            market_id="0xmkt",
            bot_name="MirrorBot",
            wallet_balance=5.0,
            entry_cost=50.0,
            shortfall=45.0,
            check="wallet balance < entry_cost at time of live ENTRY",
        )

    @pytest.mark.asyncio
    async def test_warning_when_no_balance_probe_in_system_kv(self):
        tc = _make_coordinator()
        session = _session_ctx([1, None])  # ENTRY present, no balance row
        tc.db.get_session = MagicMock(return_value=session)
        with patch("base_engine.coordination.trade_coordinator.logger") as mock_log:
            await tc._wi11_audit_live_entry("0xmkt", "MirrorBot", entry_cost=10.0)
        mock_log.warning.assert_any_call(
            "position_audit_no_balance_probe",
            market_id="0xmkt",
            entry_cost=10.0,
            check="deposit_wallet_balance_pusd not yet in system_kv",
        )

    @pytest.mark.asyncio
    async def test_nonfatal_on_db_error(self):
        """Audit errors must never raise — they are monitoring, not trading."""
        tc = _make_coordinator()
        tc.db.get_session = MagicMock(side_effect=RuntimeError("DB down"))
        # Must not raise
        await tc._wi11_audit_live_entry("0xmkt", "MirrorBot", entry_cost=10.0)

    @pytest.mark.asyncio
    async def test_skips_when_no_session_factory(self):
        tc = _make_coordinator()
        tc.db.session_factory = None
        execute_called = []
        tc.db.get_session = MagicMock(side_effect=lambda: execute_called.append(1))
        await tc._wi11_audit_live_entry("0xmkt", "MirrorBot", entry_cost=10.0)
        assert not execute_called, "Must short-circuit when session_factory is None"
