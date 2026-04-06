"""Tests for trade_event P&L integrity guards.

S120: Phantom RESOLUTION events were created for fully-exited positions,
double-counting P&L. These tests verify the guards that prevent it.
"""
import inspect
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestInsertTradeEventResolutionGuard:
    """insert_trade_event() must reject RESOLUTION for fully-exited positions."""

    @pytest.mark.asyncio
    async def test_resolution_blocked_when_fully_exited(self):
        """ENTRY(100) -> EXIT(100) -> RESOLUTION should return None.

        The WHERE NOT EXISTS (fully-exited) clause in the INSERT...SELECT
        causes 0 rows inserted when EXIT size >= ENTRY size.
        fetchone() returns None -> insert_trade_event() returns None.
        """
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        mock_result_sync_commit = MagicMock()  # SET LOCAL
        mock_result_insert = MagicMock()
        mock_result_insert.fetchone.return_value = None  # blocked by WHERE NOT EXISTS

        mock_session.execute = AsyncMock(side_effect=[
            mock_result_sync_commit,  # SET LOCAL synchronous_commit
            mock_result_insert,       # INSERT...SELECT returns 0 rows
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="RESOLUTION",
            bot_name="TestBot",
            market_id="0xtest123",
            side="YES",
            size=100.0,
            price=0.0,
            realized_pnl=-50.0,
            correlation_id="resolution:0xtest123",
        )

        assert seq is None, "RESOLUTION should be blocked for fully-exited position"
        # Verify the SQL contains the fully-exited guard
        call_args = mock_session.execute.call_args_list[1]
        sql_text = str(call_args[0][0].text)
        assert "te_exit" in sql_text, "SQL must contain the fully-exited guard (te_exit alias)"
        assert "HAVING SUM" in sql_text, "SQL must contain HAVING SUM for exit size check"

    @pytest.mark.asyncio
    async def test_resolution_allowed_when_not_exited(self):
        """ENTRY(100) -> no exit -> RESOLUTION should succeed."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        mock_result_sync = MagicMock()
        mock_result_insert = MagicMock()
        mock_result_insert.fetchone.return_value = (42,)  # inserted, returns sequence_num

        mock_session.execute = AsyncMock(side_effect=[
            mock_result_sync,
            mock_result_insert,
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="RESOLUTION",
            bot_name="TestBot",
            market_id="0xtest456",
            side="NO",
            size=50.0,
            price=0.0,
            realized_pnl=25.0,
            correlation_id="resolution:0xtest456",
        )

        assert seq == 42, "RESOLUTION should succeed when position is not exited"

    @pytest.mark.asyncio
    async def test_resolution_sql_uses_insert_select_not_values(self):
        """RESOLUTION events must use INSERT...SELECT (not INSERT...VALUES)
        to enable the WHERE NOT EXISTS guards."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=None)))
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        await db.insert_trade_event(
            event_type="RESOLUTION",
            bot_name="TestBot",
            market_id="0xtest",
            side="YES",
            size=10.0,
            price=0.0,
        )

        # The INSERT call is the second execute (after SET LOCAL)
        insert_call = mock_session.execute.call_args_list[1]
        sql = str(insert_call[0][0].text)
        assert "SELECT" in sql, "RESOLUTION must use INSERT...SELECT, not INSERT...VALUES"
        assert "VALUES" not in sql, "RESOLUTION must NOT use INSERT...VALUES"

    @pytest.mark.asyncio
    async def test_exit_event_uses_insert_values(self):
        """EXIT events use INSERT...VALUES (not INSERT...SELECT) — no guard needed."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=MagicMock(fetchone=MagicMock(return_value=(1,))))
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="EXIT",
            bot_name="TestBot",
            market_id="0xtest",
            side="SELL",
            size=10.0,
            price=0.5,
            realized_pnl=1.0,
        )

        insert_call = mock_session.execute.call_args_list[1]
        sql = str(insert_call[0][0].text)
        # S159: Changed from INSERT...VALUES to INSERT...SELECT WHERE NOT EXISTS
        # for partition-safe idempotency (same pattern as RESOLUTION path since S87).
        assert "SELECT" in sql, "EXIT must use INSERT...SELECT WHERE NOT EXISTS"
        assert "WHERE NOT EXISTS" in sql, "EXIT must have partition-safe dedup guard"


class TestResolutionBackfillGuard:
    """resolution_backfill.py Phase 4b must contain the fully-exited guard."""

    def test_phase4b_sql_contains_exit_guard(self):
        """Source code regression test: the NOT EXISTS exit guard must be present."""
        from base_engine.data import resolution_backfill
        source = inspect.getsource(resolution_backfill.run_resolution_backfill)
        assert "total_exit_size" in source, (
            "Phase 4b query must reference total_exit_size for fully-exited guard"
        )
        assert "event_type = 'EXIT'" in source or "event_type='EXIT'" in source, (
            "Phase 4b query must filter for EXIT events"
        )
        assert "total_entry_size" in source and "total_exit_size" in source, (
            "Phase 4b query must compare entry size to exit size for fully-exited guard"
        )

    def test_phase4b_sql_excludes_sell_side(self):
        """Phase 4b must filter paper_trades to YES/NO only (no SELL)."""
        from base_engine.data import resolution_backfill
        source = inspect.getsource(resolution_backfill.run_resolution_backfill)
        assert "pt.side IN ('YES', 'NO')" in source, (
            "Phase 4b must restrict to YES/NO sides (exclude SELL)"
        )

    def test_phase4b_sql_subtracts_exit_pnl(self):
        """Phase 4b must subtract EXIT P&L to avoid double-counting partial exits."""
        from base_engine.data import resolution_backfill
        source = inspect.getsource(resolution_backfill.run_resolution_backfill)
        assert "exit_pnl_already" in source, (
            "Phase 4b query must compute exit_pnl_already subquery"
        )


class TestTradeEventAudit:
    """trade_event_audit.py must detect impossible states."""

    @pytest.mark.asyncio
    async def test_audit_returns_clean_on_no_violations(self):
        """Audit with no violations returns zero counts."""
        from base_engine.data.trade_event_audit import audit_trade_events

        mock_db = MagicMock()
        mock_db.session_factory = MagicMock()

        mock_session = AsyncMock()
        empty_result = MagicMock()
        empty_result.fetchall.return_value = []
        mock_session.execute = AsyncMock(return_value=empty_result)

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_db.get_session = MagicMock(return_value=mock_cm)

        result = await audit_trade_events(mock_db)

        assert result["size_violations"] == 0
        assert result["orphan_resolutions"] == 0
        assert result["negative_sizes"] == 0

    @pytest.mark.asyncio
    async def test_audit_skips_when_no_db(self):
        """Audit gracefully skips when session_factory is None."""
        from base_engine.data.trade_event_audit import audit_trade_events

        mock_db = MagicMock()
        mock_db.session_factory = None

        result = await audit_trade_events(mock_db)
        assert result == {}
