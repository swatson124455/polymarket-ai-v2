"""
Pull lock - Prevents backtest from running while Poly Data pull is in progress.
Creates/removes poly_data/.pull_in_progress. Stale locks (>2h) are auto-cleaned.
"""
from datetime import datetime, timezone
from typing import Optional
from pathlib import Path

# Project root: base_engine/utils -> base_engine -> polymarket-ai-v2
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
LOCK_PATH = _PROJECT_ROOT / "poly_data" / ".pull_in_progress"
STALE_HOURS = 2


def get_lock_path() -> Path:
    """Return path to pull lock file."""
    return LOCK_PATH


def create_pull_lock() -> bool:
    """Create lock file. Returns True on success."""
    try:
        LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        LOCK_PATH.touch()
        return True
    except OSError:
        return False


def remove_pull_lock() -> None:
    """Remove lock file. Idempotent."""
    try:
        if LOCK_PATH.exists():
            LOCK_PATH.unlink()
    except OSError:
        pass


def _lock_age_hours() -> Optional[float]:
    """Return lock file age in hours, or None if not found."""
    if not LOCK_PATH.exists():
        return None
    mtime = LOCK_PATH.stat().st_mtime
    age = datetime.now(timezone.utc).timestamp() - mtime
    return age / 3600.0


def is_pull_in_progress() -> bool:
    """
    Return True if pull is in progress (lock exists and not stale).
    Removes stale locks (>STALE_HOURS) and returns False.
    """
    if not LOCK_PATH.exists():
        return False
    age = _lock_age_hours()
    if age is not None and age > STALE_HOURS:
        remove_pull_lock()
        return False
    return True
