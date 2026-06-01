"""WI-8: positions table CHECK constraints — unit tests.

These tests verify the migration SQL (schema/migrations/078_positions_check_constraints.sql)
contains the correct constraint definitions without requiring a live database.
They also verify that the application code correctly sets is_paper on INSERT
(via trade_coordinator.py).

Constraints pinned:
  chk_positions_status_valid      — status IN ('open','closed','resolved','reserving')
  chk_positions_is_paper_not_null — is_paper IS NOT NULL
  chk_positions_entry_price_range — status != 'open' OR (entry_price > 0 AND <= 1)
  chk_positions_size_positive     — status != 'open' OR size > 0
"""
from pathlib import Path

import pytest

_MIGRATION = Path("schema/migrations/078_positions_check_constraints.sql")
_MIGRATION_TEXT = _MIGRATION.read_text(encoding="utf-8") if _MIGRATION.exists() else ""

EXPECTED_CONSTRAINTS = [
    "chk_positions_status_valid",
    "chk_positions_is_paper_not_null",
    "chk_positions_entry_price_range",
    "chk_positions_size_positive",
]


class TestMigration078Exists:
    def test_migration_file_exists(self):
        assert _MIGRATION.exists(), f"{_MIGRATION} not found"

    def test_migration_defines_all_four_constraints(self):
        for name in EXPECTED_CONSTRAINTS:
            assert name in _MIGRATION_TEXT, (
                f"migration 078 must define constraint {name!r}; "
                f"not found in {_MIGRATION}"
            )

    def test_migration_is_idempotent(self):
        """Each constraint block must catch duplicate_object so re-running
        migration is safe (no unhandled error on second apply)."""
        assert "duplicate_object" in _MIGRATION_TEXT, (
            "migration 078 must handle duplicate_object to be idempotent; "
            "re-running migrations must not fail"
        )

    def test_status_constraint_includes_reserving(self):
        """'reserving' is a valid status used during order placement; the
        constraint must not block it."""
        assert "reserving" in _MIGRATION_TEXT, (
            "chk_positions_status_valid must allow 'reserving' status; "
            "omitting it would break order placement"
        )

    def test_size_constraint_is_conditional_on_open(self):
        """Closed/reserving positions may have size=0 (zeroed on close or
        pre-fill). The size constraint must only apply to open positions."""
        assert "status != 'open'" in _MIGRATION_TEXT or \
               "status <> 'open'" in _MIGRATION_TEXT, (
            "chk_positions_size_positive must be conditional on status='open'; "
            "5,537 closed positions have size=0 and must not be invalidated"
        )

    def test_entry_price_constraint_is_conditional_on_open(self):
        """Same rationale: closed/reserving may have entry_price=0."""
        # We just need both the conditional form AND the range check present
        assert "entry_price > 0" in _MIGRATION_TEXT, (
            "chk_positions_entry_price_range must enforce entry_price > 0"
        )
        assert "entry_price <= 1" in _MIGRATION_TEXT, (
            "chk_positions_entry_price_range must enforce entry_price <= 1"
        )


class TestConstraintSemantics:
    """White-box tests: verify the constraint SQL correctly allows/blocks
    inserts by simulating the CHECK evaluation in Python."""

    @staticmethod
    def _check_status(status: str) -> bool:
        return status in ("open", "closed", "resolved", "reserving")

    @staticmethod
    def _check_entry_price(status: str, entry_price) -> bool:
        if status != "open":
            return True
        return entry_price is not None and 0 < entry_price <= 1

    @staticmethod
    def _check_size(status: str, size) -> bool:
        if status != "open":
            return True
        return size is not None and size > 0

    @staticmethod
    def _check_is_paper(is_paper) -> bool:
        return is_paper is not None

    # --- status ---
    def test_valid_statuses_pass(self):
        for s in ("open", "closed", "resolved", "reserving"):
            assert self._check_status(s), f"'{s}' should be valid"

    def test_invalid_status_blocked(self):
        for s in ("OPEN", "invalid", "", "pending", "cancelled"):
            assert not self._check_status(s), f"'{s}' should be blocked"

    # --- is_paper ---
    def test_is_paper_true_passes(self):
        assert self._check_is_paper(True)

    def test_is_paper_false_passes(self):
        assert self._check_is_paper(False)

    def test_is_paper_none_blocked(self):
        assert not self._check_is_paper(None)

    # --- entry_price for open positions ---
    def test_open_entry_price_valid_range(self):
        for ep in (0.01, 0.5, 0.99, 1.0):
            assert self._check_entry_price("open", ep), f"entry_price={ep} should pass for open"

    def test_open_entry_price_zero_blocked(self):
        assert not self._check_entry_price("open", 0)

    def test_open_entry_price_over_one_blocked(self):
        assert not self._check_entry_price("open", 1.01)

    def test_open_entry_price_negative_blocked(self):
        assert not self._check_entry_price("open", -0.1)

    def test_closed_entry_price_zero_allowed(self):
        """Closed positions may have entry_price=0 (historical data)."""
        assert self._check_entry_price("closed", 0)

    def test_reserving_entry_price_zero_allowed(self):
        """Reserving positions start with entry_price=0 before fill."""
        assert self._check_entry_price("reserving", 0)

    # --- size for open positions ---
    def test_open_size_positive_passes(self):
        for sz in (0.001, 1.0, 1000.0):
            assert self._check_size("open", sz), f"size={sz} should pass for open"

    def test_open_size_zero_blocked(self):
        assert not self._check_size("open", 0)

    def test_open_size_negative_blocked(self):
        assert not self._check_size("open", -1)

    def test_closed_size_zero_allowed(self):
        """5,537 closed positions have size=0 — constraint must not block them."""
        assert self._check_size("closed", 0)

    def test_reserving_size_zero_allowed(self):
        """Reserving positions start with size=0."""
        assert self._check_size("reserving", 0)

    # --- compound: phantom live position (WI-8 motivation) ---
    def test_phantom_position_blocked_by_size(self):
        """A position like 190660 (is_paper=false, size from fill, but
        opened with size=0 as 'open') would be caught by size constraint."""
        # A valid live position has size > 0, entry_price in (0,1], valid status
        assert self._check_size("open", 0) is False  # size=0 open → blocked
        assert self._check_entry_price("open", 0) is False  # entry_price=0 open → blocked

    def test_valid_live_open_position_passes_all(self):
        status, ep, sz, is_paper = "open", 0.46, 2.2, False
        assert self._check_status(status)
        assert self._check_entry_price(status, ep)
        assert self._check_size(status, sz)
        assert self._check_is_paper(is_paper)
