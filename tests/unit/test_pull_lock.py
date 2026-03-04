"""Unit tests for pull_lock module."""
import pytest
from pathlib import Path
from unittest.mock import patch

from base_engine.utils.pull_lock import (
    create_pull_lock,
    remove_pull_lock,
    is_pull_in_progress,
    get_lock_path,
    LOCK_PATH,
)


def test_get_lock_path():
    """Lock path should be under poly_data."""
    p = get_lock_path()
    assert "poly_data" in str(p)
    assert p.name == ".pull_in_progress"


def test_create_and_remove_lock(tmp_path):
    """Create and remove lock file."""
    with patch("base_engine.utils.pull_lock.LOCK_PATH", tmp_path / "test_lock"):
        create_pull_lock()
        assert (tmp_path / "test_lock").exists()
        remove_pull_lock()
        assert not (tmp_path / "test_lock").exists()


def test_remove_lock_idempotent(tmp_path):
    """Removing non-existent lock should not raise."""
    with patch("base_engine.utils.pull_lock.LOCK_PATH", tmp_path / "nonexistent"):
        remove_pull_lock()
        remove_pull_lock()


def test_is_pull_in_progress_false_when_no_lock(tmp_path):
    """No lock -> not in progress."""
    with patch("base_engine.utils.pull_lock.LOCK_PATH", tmp_path / "no_lock"):
        remove_pull_lock()
        assert is_pull_in_progress() is False


def test_is_pull_in_progress_true_when_lock_exists(tmp_path):
    """Lock exists -> in progress."""
    lock_file = tmp_path / "lock"
    lock_file.touch()
    with patch("base_engine.utils.pull_lock.LOCK_PATH", lock_file):
        assert is_pull_in_progress() is True
