"""F2 — v3 heartbeat resolution backfill (docs/ALGO_REVIEW_FINDINGS_2026-07-06.md).

The silo runs no base_engine, so mirror_v3.run labels mirror_rejected_signals
itself via the shared idempotent backfill. Contract: returns the labeled count,
never raises (instrumentation upkeep must not kill collection).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from mirror_v3.run import BACKFILL_EVERY_BEATS, run_resolution_backfill


@pytest.mark.asyncio
async def test_backfill_returns_labeled_count():
    db = MagicMock()
    db.backfill_mirror_rejected_signals_resolution = AsyncMock(return_value=42)
    assert await run_resolution_backfill(db) == 42
    db.backfill_mirror_rejected_signals_resolution.assert_awaited_once()


@pytest.mark.asyncio
async def test_backfill_none_result_is_zero():
    db = MagicMock()
    db.backfill_mirror_rejected_signals_resolution = AsyncMock(return_value=None)
    assert await run_resolution_backfill(db) == 0


@pytest.mark.asyncio
async def test_backfill_exception_swallowed_returns_zero():
    db = MagicMock()
    db.backfill_mirror_rejected_signals_resolution = AsyncMock(
        side_effect=RuntimeError("db down")
    )
    assert await run_resolution_backfill(db) == 0  # must NOT raise


@pytest.mark.asyncio
async def test_backfill_missing_method_swallowed():
    """A db object without the method (stripped test double) must not crash."""
    assert await run_resolution_backfill(object()) == 0


def test_backfill_cadence_is_minutes_not_hours():
    """~10 min at the 60s heartbeat: frequent enough that labels keep up with
    resolutions, cheap enough to be negligible (idempotent UPDATE)."""
    assert 5 <= BACKFILL_EVERY_BEATS <= 30
