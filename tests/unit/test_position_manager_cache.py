"""S182 Commit 3: PositionManager cache-cleanup helper tests.

_cleanup_closed_position_state(position) must remove the closed position's
entries from all in-memory bookkeeping dicts:
  * _exit_cooldowns  (keyed on position.id)
  * _unpriced_fail_count  (keyed on str(token_id))
  * _unpriced_backoff_until  (keyed on str(token_id))

Pre-S182 only _exit_cooldowns was popped at each close site. The token-backoff
dicts accumulated stale entries indefinitely, contributing to the
`unpriced_positions: ... tokens=[...]` log spam observed 2026-04-18 on MB
(226 events/6h on a token whose position was no longer in the DB).

These tests pin the cleanup contract so any future close-path addition must
route through this helper rather than introducing a new stale-state path.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from base_engine.execution.position_manager import AutomatedPositionManager as PositionManager


def _make_pm() -> PositionManager:
    """Build a minimally-constructed PositionManager. Most deps are mocked since
    the helper only touches three in-memory dicts."""
    db = MagicMock()
    pm = PositionManager.__new__(PositionManager)
    # Initialize only the fields the helper reads/writes
    pm._exit_cooldowns = {}
    pm._unpriced_fail_count = {}
    pm._unpriced_backoff_until = {}
    return pm


def _position(pid: int, token_id: str):
    return SimpleNamespace(id=pid, token_id=token_id)


def test_cleanup_removes_all_three_dicts():
    """Single-call cleanup must pop the position from all three dicts."""
    pm = _make_pm()
    pos = _position(pid=101, token_id="tok-abc")

    # Seed all three dicts with state for this position
    pm._exit_cooldowns[101] = time.monotonic() + 60
    pm._unpriced_fail_count["tok-abc"] = 7
    pm._unpriced_backoff_until["tok-abc"] = time.monotonic() + 300

    pm._cleanup_closed_position_state(pos)

    assert 101 not in pm._exit_cooldowns
    assert "tok-abc" not in pm._unpriced_fail_count
    assert "tok-abc" not in pm._unpriced_backoff_until


def test_cleanup_idempotent_on_absent_entries():
    """Calling cleanup on a position with no seeded state must not raise."""
    pm = _make_pm()
    pos = _position(pid=202, token_id="tok-absent")

    # None of the dicts have an entry for this position — cleanup should be a no-op.
    pm._cleanup_closed_position_state(pos)  # must not raise

    assert len(pm._exit_cooldowns) == 0
    assert len(pm._unpriced_fail_count) == 0
    assert len(pm._unpriced_backoff_until) == 0


def test_cleanup_only_touches_own_position():
    """Cleanup of position A must not affect position B's state."""
    pm = _make_pm()
    pos_a = _position(pid=301, token_id="tok-A")
    pos_b = _position(pid=302, token_id="tok-B")

    pm._exit_cooldowns[301] = 1.0
    pm._exit_cooldowns[302] = 2.0
    pm._unpriced_fail_count["tok-A"] = 5
    pm._unpriced_fail_count["tok-B"] = 9
    pm._unpriced_backoff_until["tok-A"] = 10.0
    pm._unpriced_backoff_until["tok-B"] = 20.0

    pm._cleanup_closed_position_state(pos_a)

    # A is gone
    assert 301 not in pm._exit_cooldowns
    assert "tok-A" not in pm._unpriced_fail_count
    assert "tok-A" not in pm._unpriced_backoff_until
    # B untouched
    assert pm._exit_cooldowns[302] == 2.0
    assert pm._unpriced_fail_count["tok-B"] == 9
    assert pm._unpriced_backoff_until["tok-B"] == 20.0


def test_cleanup_handles_none_token_id():
    """Position with token_id=None (or empty) must still clean _exit_cooldowns
    without raising on the None→str conversion."""
    pm = _make_pm()
    pos = SimpleNamespace(id=401, token_id=None)

    pm._exit_cooldowns[401] = 5.0
    # token-keyed dicts have no entry to remove — helper must not raise.
    pm._cleanup_closed_position_state(pos)

    assert 401 not in pm._exit_cooldowns
    assert len(pm._unpriced_fail_count) == 0
    assert len(pm._unpriced_backoff_until) == 0


def test_cleanup_handles_missing_position_attrs():
    """Partial Position-like objects (e.g. stub without .token_id attr) must
    not raise — uses getattr with defaults."""
    pm = _make_pm()
    pos = SimpleNamespace(id=501)  # no token_id attr

    pm._exit_cooldowns[501] = 3.0
    pm._cleanup_closed_position_state(pos)  # must not raise

    assert 501 not in pm._exit_cooldowns
