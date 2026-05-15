"""Tests for TemporalFutureCheck — the audit check that flags future-dated
resolution-observation timestamps across the 6 affected tables.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from base_engine.audit.checks.temporal_future_check import TemporalFutureCheck, _TARGETS


def _mock_session_returning(counts: dict):
    """Build a mock session whose .execute() returns COUNT(*) results.

    counts: dict mapping table_name → int (the COUNT to return).
    Tables not in the dict default to 0.
    """
    call_count = {"i": 0}

    async def execute(query, params=None):
        i = call_count["i"]
        call_count["i"] += 1
        # _TARGETS is iterated in order; map index → table
        table = _TARGETS[i][0]
        result = MagicMock()
        result.scalar = MagicMock(return_value=counts.get(table, 0))
        return result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute)
    return session


@pytest.mark.asyncio
async def test_all_clean_returns_passed():
    check = TemporalFutureCheck()
    session = _mock_session_returning({})
    result = await check.execute(session)
    assert result.passed is True
    assert len(result.violations) == 0
    assert result.check_name == "temporal_future"


@pytest.mark.asyncio
async def test_future_rows_in_markets_flags_warning():
    check = TemporalFutureCheck()
    session = _mock_session_returning({"markets": 42})
    result = await check.execute(session)
    assert result.passed is False
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.recon_type == "TEMPORAL_FUTURE"
    assert v.severity == "WARNING"
    assert v.details["table"] == "markets"
    assert v.details["future_row_count"] == 42


@pytest.mark.asyncio
async def test_future_rows_in_trade_events_flags_critical():
    """trade_events is the P&L authority — future rows there are CRITICAL,
    not just WARNING."""
    check = TemporalFutureCheck()
    session = _mock_session_returning({"trade_events": 22})
    result = await check.execute(session)
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.details["table"] == "trade_events"
    assert v.severity == "CRITICAL"


@pytest.mark.asyncio
async def test_multiple_tables_each_get_a_violation():
    check = TemporalFutureCheck()
    session = _mock_session_returning({
        "markets": 898,
        "paper_trades": 32,
        "trade_events": 22,
        "prediction_log": 35824,
        "mirror_rejected_signals": 4120,
        "traded_markets": 2,
    })
    result = await check.execute(session)
    assert result.passed is False
    assert len(result.violations) == 6
    by_table = {v.details["table"]: v for v in result.violations}
    assert by_table["markets"].details["future_row_count"] == 898
    assert by_table["trade_events"].details["future_row_count"] == 22
    assert by_table["prediction_log"].details["future_row_count"] == 35824
    # Severity assignment honored
    assert by_table["trade_events"].severity == "CRITICAL"
    assert by_table["markets"].severity == "WARNING"


@pytest.mark.asyncio
async def test_query_errors_become_warning_violations():
    """If a table doesn't exist or the query fails, we surface as a warning
    rather than crashing the audit cycle."""
    check = TemporalFutureCheck()

    async def failing_execute(query, params=None):
        raise RuntimeError("table not found")

    session = MagicMock()
    session.execute = AsyncMock(side_effect=failing_execute)
    result = await check.execute(session)
    # 6 errors, one per target
    assert len(result.violations) == 6
    for v in result.violations:
        assert v.recon_type == "TEMPORAL_FUTURE_CHECK_ERROR"
        assert v.severity == "WARNING"
        assert "table not found" in v.details["error"]


def test_targets_covers_all_six_tables():
    """Sanity: the configured _TARGETS covers every column found corrupt in
    the audit. Drift here means somebody forgot a table; the check loses
    coverage silently."""
    expected = {
        ("markets", "resolved_at"),
        ("paper_trades", "resolved_at"),
        ("trade_events", "event_time"),
        ("prediction_log", "resolved_at"),
        ("mirror_rejected_signals", "resolved_at"),
        ("traded_markets", "resolved_at"),
    }
    actual = {(t, c) for t, c, _ in _TARGETS}
    assert actual == expected
