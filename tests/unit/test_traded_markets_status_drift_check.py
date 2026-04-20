"""
Contract test for TradedMarketsStatusDriftCheck.

Pins:
1. Kill switch: TRADED_MARKETS_STATUS_DRIFT_CHECK_ENABLED=false returns empty
   CheckResult with passed=True and a summary indicating the disabled path.
2. Happy path: check returns violations with recon_type='STALE_POSITION',
   severity='CRITICAL', non-empty details dict (violation_hash discriminator
   requirement per AuditViolation.__post_init__).
3. Empty session: zero rows → zero violations, passed=True.
"""
from typing import List

import pytest

from base_engine.audit.checks.traded_markets_status_drift_check import (
    TradedMarketsStatusDriftCheck,
)


class _FakeResult:
    def __init__(self, rows: List[tuple]) -> None:
        self._rows = rows

    def fetchall(self) -> List[tuple]:
        return self._rows


class _FakeSession:
    def __init__(self, rows: List[tuple]) -> None:
        self._rows = rows

    async def execute(self, _stmt) -> _FakeResult:
        return _FakeResult(self._rows)


@pytest.mark.asyncio
async def test_kill_switch_disables_check(monkeypatch):
    monkeypatch.setenv("TRADED_MARKETS_STATUS_DRIFT_CHECK_ENABLED", "false")
    check = TradedMarketsStatusDriftCheck()
    session = _FakeSession(rows=[("mkt_1", "MirrorBot", 3, None, None)])
    result = await check.execute(session)
    assert result.passed is True
    assert result.violations == []
    assert "disabled" in result.summary.lower()


@pytest.mark.asyncio
async def test_empty_session_produces_no_violations(monkeypatch):
    monkeypatch.delenv("TRADED_MARKETS_STATUS_DRIFT_CHECK_ENABLED", raising=False)
    check = TradedMarketsStatusDriftCheck()
    session = _FakeSession(rows=[])
    result = await check.execute(session)
    assert result.passed is True
    assert result.violations == []
    assert result.check_name == "traded_markets_status_drift"


@pytest.mark.asyncio
async def test_happy_path_emits_stale_position_critical(monkeypatch):
    monkeypatch.delenv("TRADED_MARKETS_STATUS_DRIFT_CHECK_ENABLED", raising=False)
    check = TradedMarketsStatusDriftCheck()
    session = _FakeSession(rows=[
        ("mkt_abc", "MirrorBot", 5, "2026-04-01 10:00", "2026-04-10 18:30"),
        ("mkt_xyz", "WeatherBot,MirrorBot", 1, "2026-04-05 06:00", None),
    ])
    result = await check.execute(session)
    assert result.passed is False
    assert len(result.violations) == 2

    v0 = result.violations[0]
    assert v0.recon_type == "STALE_POSITION"
    assert v0.severity == "CRITICAL"
    assert v0.bot_name == "MirrorBot"
    assert v0.market_id == "mkt_abc"
    # details must be non-empty (violation_hash discriminator contract)
    assert v0.details["resolved_trade_count"] == 5
    assert v0.details["source"] == "traded_markets_status_drift"
    assert "reason" in v0.details

    # CSV bot_names: first entry is used
    assert result.violations[1].bot_name == "WeatherBot"


def test_tables_queried_declaration():
    check = TradedMarketsStatusDriftCheck()
    assert check.tables_queried == ["traded_markets", "paper_trades"]
    assert check.name == "traded_markets_status_drift"
