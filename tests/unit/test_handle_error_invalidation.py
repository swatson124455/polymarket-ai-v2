"""WI-21 (2026-06-04) — proactive eviction of corrupted asyncpg connections.

Tests the pure predicate behind the SQLAlchemy `handle_error` listener
(`_handle_error_should_invalidate`). The new WI-21 matches ("cannot switch to state",
"another operation") must evict; benign query/constraint/timeout errors must NOT
(false-positive eviction would churn healthy connections, worsening pool pressure).
"""
from base_engine.data.database import _handle_error_should_invalidate as should_invalidate


class _Exc(Exception):
    pass


class ConnectionDoesNotExistError(Exception):  # mimic asyncpg type name
    pass


class InterfaceError(Exception):  # mimic DBAPI type name
    pass


class TestEvictsCorruptedConnections:
    # --- physical-connection failures → MUST invalidate ---
    def test_connection_was_closed(self):
        assert should_invalidate(_Exc("connection was closed in the middle of operation")) is True

    def test_cannot_switch_to_state(self):  # NEW (WI-21)
        assert should_invalidate(
            _Exc("cannot switch to state 5; another operation (2) is in progress")
        ) is True

    def test_another_operation_in_progress(self):  # NEW (WI-21)
        assert should_invalidate(
            _Exc("cannot perform operation: another operation is in progress")
        ) is True

    def test_connection_does_not_exist_by_type_name(self):
        assert should_invalidate(ConnectionDoesNotExistError("backend gone")) is True

    def test_interface_error_by_type_name(self):
        assert should_invalidate(InterfaceError("bad connection")) is True


class TestDoesNotEvictHealthyConnections:
    # --- benign errors → MUST NOT invalidate (false-positive guard) ---
    def test_unique_violation(self):
        assert should_invalidate(
            _Exc('duplicate key value violates unique constraint "positions_pkey"')
        ) is False

    def test_undefined_table(self):
        assert should_invalidate(_Exc('relation "nope" does not exist')) is False

    def test_statement_timeout(self):
        assert should_invalidate(_Exc("canceling statement due to statement timeout")) is False

    def test_check_constraint(self):
        assert should_invalidate(_Exc('new row violates check constraint "chk_positions_side"')) is False

    def test_serialization_failure(self):
        assert should_invalidate(_Exc("could not serialize access due to concurrent update")) is False

    def test_none(self):
        assert should_invalidate(None) is False
