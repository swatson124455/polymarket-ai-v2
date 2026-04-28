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

        The inline SQL fully-exited guard in INSERT...SELECT causes 0 rows
        inserted when EXIT size >= ENTRY size. fetchone() returns None →
        insert_trade_event() returns None.

        S196: RESOLUTION now has 3 execute calls:
          [0] SET LOCAL synchronous_commit
          [1] RESOLUTION over-size pre-check (S196 guard) — pass non-blocking
              values (entry=200, disposal=50) so the SQL guard is the path
              under test, not the Python pre-check.
          [2] INSERT...SELECT — fetchone returns None (SQL guard blocks)
        """
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        mock_result_sync_commit = MagicMock()  # SET LOCAL
        mock_result_size_check = MagicMock()
        mock_result_size_check.fetchone.return_value = (200.0, 50.0)  # entry=200, disposal=50
        mock_result_insert = MagicMock()
        mock_result_insert.fetchone.return_value = None  # blocked by SQL fully-exited guard

        mock_session.execute = AsyncMock(side_effect=[
            mock_result_sync_commit,    # SET LOCAL synchronous_commit
            mock_result_size_check,     # RESOLUTION over-size pre-check (S196)
            mock_result_insert,         # INSERT...SELECT returns 0 rows
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
        # Verify the INSERT SQL still contains the inline fully-exited guard.
        # The INSERT is the 3rd execute call (index 2) post-S196.
        call_args = mock_session.execute.call_args_list[2]
        sql_text = str(call_args[0][0].text)
        assert "te_exit" in sql_text, "SQL must contain the fully-exited guard (te_exit alias)"
        assert "HAVING SUM" in sql_text, "SQL must contain HAVING SUM for exit size check"

    @pytest.mark.asyncio
    async def test_resolution_allowed_when_not_exited(self):
        """ENTRY(100) -> no exit -> RESOLUTION should succeed.

        S196: RESOLUTION now has 3 execute calls:
          [0] SET LOCAL
          [1] RESOLUTION over-size pre-check (entry=100, disposal=0 → passes)
          [2] INSERT — returns sequence_num=42
        """
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        mock_result_sync = MagicMock()
        mock_result_size_check = MagicMock()
        mock_result_size_check.fetchone.return_value = (100.0, 0.0)  # entry=100, disposal=0
        mock_result_insert = MagicMock()
        mock_result_insert.fetchone.return_value = (42,)  # inserted, returns sequence_num

        mock_session.execute = AsyncMock(side_effect=[
            mock_result_sync,
            mock_result_size_check,
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
        """EXIT events use INSERT...SELECT WHERE NOT EXISTS for partition-safe dedup."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        # S167: EXIT now has 4 execute calls:
        #   [0] SET LOCAL synchronous_commit
        #   [1] FK check (SELECT 1 FROM markets) — return row to pass
        #   [2] EXIT size guard (SUM entry/exit) — entry=100, exit=0 to allow
        #   [3] INSERT...SELECT (the actual insert)
        mock_fk_result = MagicMock()
        mock_fk_result.fetchone.return_value = (1,)  # market exists

        mock_size_result = MagicMock()
        mock_size_result.fetchone.return_value = (100.0, 0.0)  # entry=100, exit=0

        mock_insert_result = MagicMock()
        mock_insert_result.fetchone.return_value = (1,)  # inserted, returns seq

        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(),        # SET LOCAL
            mock_fk_result,     # FK check
            mock_size_result,   # EXIT size guard
            mock_insert_result, # INSERT
        ])
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

        # INSERT is the 4th execute call (index 3)
        insert_call = mock_session.execute.call_args_list[3]
        sql = str(insert_call[0][0].text)
        # S159: Changed from INSERT...VALUES to INSERT...SELECT WHERE NOT EXISTS
        # for partition-safe idempotency (same pattern as RESOLUTION path since S87).
        assert "SELECT" in sql, "EXIT must use INSERT...SELECT WHERE NOT EXISTS"
        assert "WHERE NOT EXISTS" in sql, "EXIT must have partition-safe dedup guard"


class TestResolutionBackfillGuard:
    """Phase 4b must contain the fully-exited guard. S195 lifted Phase 4b
    out of resolution_backfill.run_resolution_backfill into
    Database.backfill_trade_events_resolution — these regression guards now
    grep the new home."""

    def test_phase4b_sql_contains_exit_guard(self):
        """Source code regression test: the NOT EXISTS exit guard must be present."""
        from base_engine.data.database import Database
        source = inspect.getsource(Database.backfill_trade_events_resolution)
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
        from base_engine.data.database import Database
        source = inspect.getsource(Database.backfill_trade_events_resolution)
        assert "pt.side IN ('YES', 'NO')" in source, (
            "Phase 4b must restrict to YES/NO sides (exclude SELL)"
        )

    def test_phase4b_sql_subtracts_exit_pnl(self):
        """Phase 4b must subtract EXIT P&L to avoid double-counting partial exits."""
        from base_engine.data.database import Database
        source = inspect.getsource(Database.backfill_trade_events_resolution)
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


class TestS167ResolutionDedupNoSide:
    """S167: RESOLUTION dedup should NOT use side — one per (bot, market)."""

    @pytest.mark.asyncio
    async def test_resolution_sql_omits_side_from_dedup(self):
        """Verify the RESOLUTION NOT EXISTS guard does not include te.side."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(fetchone=MagicMock(return_value=None))
        )
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

        # S196: RESOLUTION now has 3 execute calls — SET LOCAL [0],
        # over-size pre-check [1], INSERT [2]. The mock's return_value applies
        # uniformly so the pre-check's fetchone returns None and the guard
        # skips (allowing the INSERT to run).
        insert_call = mock_session.execute.call_args_list[2]
        sql = str(insert_call[0][0].text)
        # The first NOT EXISTS block (RESOLUTION dedup) must NOT contain "te.side"
        # Split at the second NOT EXISTS to isolate the first guard
        first_guard = sql.split("NOT EXISTS")[1].split("NOT EXISTS")[0]
        assert "te.side" not in first_guard, (
            "S167: RESOLUTION dedup must NOT include side — one per (bot, market)"
        )


class TestS167ExitOversizeGuard:
    """S167: EXIT events must be rejected when total exit size exceeds entry size."""

    @pytest.mark.asyncio
    async def test_exit_rejected_when_oversize(self):
        """EXIT of 50 should be rejected when existing exits=80, entries=100."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        mock_fk = MagicMock()
        mock_fk.fetchone.return_value = (1,)  # market exists

        mock_size = MagicMock()
        mock_size.fetchone.return_value = (100.0, 80.0)  # entry=100, exit=80

        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(),   # SET LOCAL
            mock_fk,       # FK check
            mock_size,     # size guard: 80+50=130 > 100 → reject
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="EXIT",
            bot_name="TestBot",
            market_id="0xtest",
            side="YES",
            size=50.0,
            price=0.5,
        )

        assert seq is None, "EXIT should be rejected when total exits exceed entries"

    @pytest.mark.asyncio
    async def test_exit_allowed_cross_side_transition(self):
        """EXIT side=SELL on ENTRY side=YES should pass (side-agnostic guard)."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        mock_fk = MagicMock()
        mock_fk.fetchone.return_value = (1,)

        mock_size = MagicMock()
        mock_size.fetchone.return_value = (100.0, 0.0)  # entry=100, exit=0

        mock_insert = MagicMock()
        mock_insert.fetchone.return_value = (42,)

        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(),   # SET LOCAL
            mock_fk,       # FK check
            mock_size,     # size guard: 0+10 < 100 → allow
            mock_insert,   # INSERT
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="EXIT",
            bot_name="TestBot",
            market_id="0xtest",
            side="SELL",  # pre-S163 side
            size=10.0,
            price=0.5,
        )

        assert seq == 42, "EXIT with SELL side should pass (side-agnostic guard)"


class TestS196ResolutionOversizeGuard:
    """S196: RESOLUTION events must be rejected when total disposal
    (existing EXITs + RESOLUTIONs) plus the new emit's size would exceed
    total ENTRY size. Mirrors the S167 EXIT over-size guard but for the
    RESOLUTION emission path — closes the partial-EXIT + RESOLUTION
    over-emit case that the inline SQL fully-exited guard misses (the
    SQL guard only blocks when SUM(EXIT) >= SUM(ENTRY), not partial)."""

    @pytest.mark.asyncio
    async def test_resolution_rejected_when_oversize_partial_exit(self):
        """ENTRY=100, EXIT=20 (partial), RESOLUTION emit size=85.
        Total disposal = 20 + 85 = 105 > 100 → reject.

        This is the exact RC-2 shape from prod data. The inline SQL
        fully-exited guard would NOT block this (SUM(EXIT)=20 < SUM(ENTRY)=100),
        but the new Python pre-check does.
        """
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        mock_size = MagicMock()
        mock_size.fetchone.return_value = (100.0, 20.0)  # entry=100, disposal=20

        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(),  # SET LOCAL
            mock_size,    # RESOLUTION over-size pre-check (S196): 20+85=105 > 100 → reject
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="RESOLUTION",
            bot_name="TestBot",
            market_id="0xtest",
            side="YES",
            size=85.0,
            price=1.0,
            realized_pnl=72.0,
        )

        assert seq is None, (
            "RESOLUTION should be rejected when total disposal would exceed ENTRY"
        )
        # Only 2 execute calls: SET LOCAL + size check. INSERT never reached.
        assert mock_session.execute.await_count == 2, (
            f"expected 2 execute calls (SET LOCAL + size check), got "
            f"{mock_session.execute.await_count} — guard may have allowed INSERT through"
        )

    @pytest.mark.asyncio
    async def test_resolution_allowed_when_disposal_plus_size_fits_entry(self):
        """ENTRY=100, EXIT=20, RESOLUTION size=80. Total disposal = 20 + 80 = 100 ≤ 100 → allow."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        mock_size = MagicMock()
        mock_size.fetchone.return_value = (100.0, 20.0)  # entry=100, disposal=20

        mock_insert = MagicMock()
        mock_insert.fetchone.return_value = (99,)

        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(),  # SET LOCAL
            mock_size,    # size check passes
            mock_insert,  # INSERT
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="RESOLUTION",
            bot_name="TestBot",
            market_id="0xtest",
            side="YES",
            size=80.0,
            price=1.0,
        )

        assert seq == 99, "RESOLUTION should succeed when disposal+size fits entry"

    @pytest.mark.asyncio
    async def test_resolution_size_check_query_sums_exit_and_resolution(self):
        """The S196 size check query MUST aggregate disposal as
        EXIT + RESOLUTION (not just EXIT). Defensive against future runs
        where a stale RESOLUTION already exists (the dedup check would block
        the new emit, but the size check should also account for it)."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        mock_size = MagicMock()
        mock_size.fetchone.return_value = (100.0, 0.0)
        mock_insert = MagicMock()
        mock_insert.fetchone.return_value = (1,)

        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(), mock_size, mock_insert,
        ])
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
            price=1.0,
        )

        # The size check is the 2nd execute (after SET LOCAL).
        size_check_sql = str(mock_session.execute.call_args_list[1][0][0].text)
        assert "EXIT" in size_check_sql and "RESOLUTION" in size_check_sql, (
            "S196 size check must aggregate disposal as EXIT + RESOLUTION"
        )
        assert "total_disposal" in size_check_sql, (
            "S196 size check must compute total_disposal column"
        )

    def test_resolution_guard_pinned_in_source(self):
        """Source-string regression guard: the S196 RESOLUTION over-size
        guard must remain in insert_trade_event. If anyone removes it,
        this test fails immediately with a clear message."""
        import inspect
        from base_engine.data.database import Database

        source = inspect.getsource(Database.insert_trade_event)
        assert "RESOLUTION over-size rejected" in source, (
            "S196 RESOLUTION over-size guard removed from insert_trade_event. "
            "This guard is the structural fix for RC-2 partial-EXIT + RESOLUTION "
            "over-emit (e.g., Phase 4b-alt with stale positions.size). "
            "Restore the pre-INSERT _res_size_check block."
        )


class TestS167FKValidation:
    """S167 + S193: FK policy for trade events.

    S167 behavior (unchanged for EXIT/RESOLUTION):
      - EXIT on a market not in DB → return None (fail-closed)
      - RESOLUTION bypasses the FK check entirely

    S193 behavior (new for ENTRY):
      - ENTRY on a market not in DB → insert minimal market stub, re-verify,
        then insert the trade_event. Prevents phantom positions where the
        paper_trade committed but the trade_event was silently dropped
        (asyncio.gather None-return was not detected in paper_trading).
    """

    @pytest.mark.asyncio
    async def test_entry_auto_heals_when_market_missing(self):
        """S193: ENTRY on a market not in DB should auto-heal via stub insert."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        _fk_miss = MagicMock()
        _fk_miss.fetchone.return_value = None        # initial FK check — miss
        _stub_ok = MagicMock()                       # stub INSERT — no fetchone required
        _fk_hit = MagicMock()
        _fk_hit.fetchone.return_value = (1,)         # FK recheck — now present
        _final_insert = MagicMock()
        _final_insert.fetchone.return_value = (101,) # main INSERT returns seq_num

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(),     # SET LOCAL
            _fk_miss,        # FK check 1 (miss)
            _stub_ok,        # stub INSERT (ON CONFLICT DO NOTHING)
            _fk_hit,         # FK recheck (hit)
            _final_insert,   # main INSERT...SELECT
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="ENTRY",
            bot_name="TestBot",
            market_id="1706785",
            side="NO",
            size=10.0,
            price=0.88,
        )

        assert seq == 101, "ENTRY should succeed after auto-heal"
        assert mock_session.execute.call_count == 5, (
            "Auto-heal path expects 5 execute calls: SET LOCAL + FK + stub + recheck + INSERT"
        )

    @pytest.mark.asyncio
    async def test_entry_auto_heal_hex_market_sets_condition_id(self):
        """S193: hex market_id stub must set condition_id to the hex value."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        _fk_miss = MagicMock(); _fk_miss.fetchone.return_value = None
        _fk_hit = MagicMock(); _fk_hit.fetchone.return_value = (1,)
        _final = MagicMock(); _final.fetchone.return_value = (7,)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(), _fk_miss, MagicMock(), _fk_hit, _final,
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        hex_id = "0x15a515b02f64fa86ee17e1657c61ce098c960374b73064e86f3b092d4cf9d2f8"
        seq = await db.insert_trade_event(
            event_type="ENTRY", bot_name="TestBot", market_id=hex_id,
            side="NO", size=10.0, price=0.68,
        )

        assert seq == 7
        stub_call = mock_session.execute.call_args_list[2]
        stub_params = stub_call[0][1]
        assert stub_params["mid"] == hex_id
        assert stub_params["cid"] == hex_id, "hex market_id → condition_id set to same value"

    @pytest.mark.asyncio
    async def test_entry_auto_heal_numeric_market_nulls_condition_id(self):
        """S193: numeric market_id stub must leave condition_id NULL (ingestion fills later)."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        _fk_miss = MagicMock(); _fk_miss.fetchone.return_value = None
        _fk_hit = MagicMock(); _fk_hit.fetchone.return_value = (1,)
        _final = MagicMock(); _final.fetchone.return_value = (8,)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(), _fk_miss, MagicMock(), _fk_hit, _final,
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="ENTRY", bot_name="TestBot", market_id="1706785",
            side="NO", size=10.0, price=0.88,
        )

        assert seq == 8
        stub_params = mock_session.execute.call_args_list[2][0][1]
        assert stub_params["mid"] == "1706785"
        assert stub_params["cid"] is None, "numeric market_id → condition_id NULL"

    @pytest.mark.asyncio
    async def test_entry_returns_none_when_auto_heal_fails(self):
        """S193: if stub INSERT does not satisfy FK recheck, return None."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        _fk_miss_1 = MagicMock(); _fk_miss_1.fetchone.return_value = None
        _fk_miss_2 = MagicMock(); _fk_miss_2.fetchone.return_value = None  # still missing

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(),   # SET LOCAL
            _fk_miss_1,    # FK check 1
            MagicMock(),   # stub INSERT (no-op)
            _fk_miss_2,    # FK recheck — still miss
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="ENTRY", bot_name="TestBot", market_id="bogus_mid",
            side="YES", size=1.0, price=0.5,
        )

        assert seq is None, "Auto-heal failure must return None"
        assert mock_session.execute.call_count == 4, (
            "Failed auto-heal stops after recheck — 4 execute calls, no main INSERT"
        )

    @pytest.mark.asyncio
    async def test_exit_rejected_when_market_missing(self):
        """S167 preserved: EXIT on a market not in DB returns None (no auto-heal)."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        _fk_miss = MagicMock(); _fk_miss.fetchone.return_value = None

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(),   # SET LOCAL
            _fk_miss,      # FK check — miss
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="EXIT", bot_name="TestBot", market_id="unknown_market",
            side="YES", size=10.0, price=0.5,
        )

        assert seq is None, "EXIT on unknown market must still be rejected"
        assert mock_session.execute.call_count == 2, (
            "EXIT fail-closed — no stub insert, no recheck"
        )

    @pytest.mark.asyncio
    async def test_resolution_skips_fk_check(self):
        """RESOLUTION events should NOT be FK-checked (backfill on deleted markets)."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(
            return_value=MagicMock(fetchone=MagicMock(return_value=(99,)))
        )
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="RESOLUTION",
            bot_name="TestBot",
            market_id="deleted_market",
            side="YES",
            size=10.0,
            price=0.0,
        )

        # RESOLUTION path has 2 execute calls: SET LOCAL + INSERT
        # No FK check in between
        assert mock_session.execute.call_count == 2, (
            "RESOLUTION should have exactly 2 execute calls (no FK check)"
        )


class TestS199ShadowEntryFKAutoHeal:
    """S199: SHADOW_ENTRY joins the FK auto-heal path.

    Pre-S199 the auto-heal guard at database.py:5490 was ENTRY/EXIT only;
    SHADOW_ENTRY emissions on unknown markets fell through to INSERT, and
    since `trade_events` has no DB-level FK to `markets` (verified
    2026-04-28), those rows were silently inserted as orphans — the
    post-S193 orphan footprint diagnosed in S198 §2.3.

    SHADOW_ENTRY is "would-have-been entry" (zero-Kelly / negative-EV /
    capped). It has no preceding state to corrupt by stub-healing, so
    ENTRY semantics (heal + insert) is the correct treatment.
    """

    @pytest.mark.asyncio
    async def test_shadow_entry_auto_heals_when_market_missing(self):
        """SHADOW_ENTRY on a market not in DB should auto-heal via stub insert."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        _fk_miss = MagicMock(); _fk_miss.fetchone.return_value = None
        _stub_ok = MagicMock()
        _fk_hit = MagicMock(); _fk_hit.fetchone.return_value = (1,)
        _final_insert = MagicMock(); _final_insert.fetchone.return_value = (202,)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(),     # SET LOCAL
            _fk_miss,        # FK check 1 (miss)
            _stub_ok,        # stub INSERT (ON CONFLICT DO NOTHING)
            _fk_hit,         # FK recheck (hit)
            _final_insert,   # main INSERT...SELECT
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="SHADOW_ENTRY",
            bot_name="WeatherBot",
            market_id="0xunknownshadow",
            side="YES",
            size=10.0,
            price=0.5,
        )

        assert seq == 202, "SHADOW_ENTRY should succeed after auto-heal"
        assert mock_session.execute.call_count == 5, (
            "Auto-heal path expects 5 execute calls: "
            "SET LOCAL + FK + stub + recheck + INSERT"
        )

    @pytest.mark.asyncio
    async def test_shadow_entry_returns_none_when_auto_heal_fails(self):
        """SHADOW_ENTRY: if stub INSERT does not satisfy FK recheck, return None."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        _fk_miss_1 = MagicMock(); _fk_miss_1.fetchone.return_value = None
        _fk_miss_2 = MagicMock(); _fk_miss_2.fetchone.return_value = None

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(),   # SET LOCAL
            _fk_miss_1,    # FK check 1
            MagicMock(),   # stub INSERT (no-op)
            _fk_miss_2,    # FK recheck — still miss
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="SHADOW_ENTRY",
            bot_name="WeatherBot",
            market_id="bogus_shadow_mid",
            side="YES",
            size=1.0,
            price=0.5,
        )

        assert seq is None, "Auto-heal failure must return None for SHADOW_ENTRY"
        assert mock_session.execute.call_count == 4, (
            "Failed auto-heal stops after recheck — 4 execute calls, no main INSERT"
        )

    @pytest.mark.asyncio
    async def test_shadow_entry_no_heal_when_market_present(self):
        """SHADOW_ENTRY on a market already in DB should skip stub-insert path."""
        from base_engine.data.database import Database

        db = Database.__new__(Database)
        db.session_factory = MagicMock()

        _fk_hit = MagicMock(); _fk_hit.fetchone.return_value = (1,)
        _final_insert = MagicMock(); _final_insert.fetchone.return_value = (303,)

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(side_effect=[
            MagicMock(),     # SET LOCAL
            _fk_hit,         # FK check 1 — hit, no heal
            _final_insert,   # main INSERT
        ])
        mock_session.commit = AsyncMock()

        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        db.get_session = MagicMock(return_value=mock_cm)

        seq = await db.insert_trade_event(
            event_type="SHADOW_ENTRY",
            bot_name="WeatherBot",
            market_id="0xknownmkt",
            side="NO",
            size=5.0,
            price=0.4,
        )

        assert seq == 303
        assert mock_session.execute.call_count == 3, (
            "FK-present path: SET LOCAL + FK check + INSERT (no stub, no recheck)"
        )

    def test_shadow_entry_in_heal_guard_pinned_in_source(self):
        """Source-string regression guard: SHADOW_ENTRY must be in the
        auto-heal type list at database.py:5490. If anyone reverts the
        guard to ENTRY/EXIT only, this test fires immediately.
        """
        from base_engine.data.database import Database
        source = inspect.getsource(Database.insert_trade_event)
        # The outer guard tuple must include SHADOW_ENTRY alongside ENTRY/EXIT.
        assert '"ENTRY", "EXIT", "SHADOW_ENTRY"' in source, (
            "S199: SHADOW_ENTRY removed from FK auto-heal guard at "
            "database.py:5490. Restore the tuple `(\"ENTRY\", \"EXIT\", "
            "\"SHADOW_ENTRY\")`. Without SHADOW_ENTRY in the guard, "
            "shadow events on unknown markets become orphan trade_events "
            "rows (no DB-level FK enforces it)."
        )
        # The inner heal-vs-fail-closed branch must include SHADOW_ENTRY in heal.
        assert '"ENTRY", "SHADOW_ENTRY"' in source, (
            "S199: SHADOW_ENTRY removed from heal branch. SHADOW_ENTRY must "
            "be treated like ENTRY (auto-heal stub), not like EXIT (fail-closed)."
        )
