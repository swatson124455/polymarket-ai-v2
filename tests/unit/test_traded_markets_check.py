"""
Contract test for TradedMarketsCheck (distinct from TradedMarketsStatusDriftCheck).

Pins Python-layer handling of rows returned by the stale-row and missing-row
queries. SQL-level changes (S190 CSV-membership fix via ANY(string_to_array))
are verified against the live VPS DB — no unit-test harness for that path.

1. Single-bot value NOT character-iterated when emitted as AuditViolation.bot_name.
   Regression guard for S190 stale-row-path bug: pre-fix code was
   ",".join(bot_names) which on a single-bot value "MirrorBot" produced
   "M,i,r,r,o,r,B,o,t" polluting 1,600 of 3,186 OPEN rows (50%) at discovery.
2. Multi-bot CSV rows attribute to the FIRST bot, matching sibling check
   TradedMarketsStatusDriftCheck (traded_markets_status_drift_check.py:65).
3. Missing-row path (unchanged in S190 fix) keeps singular bot_name treatment.
4. Empty session produces zero violations.
"""
from typing import List

import pytest

from base_engine.audit.checks.traded_markets_check import TradedMarketsCheck


class _FakeResult:
    def __init__(self, rows: List[tuple]) -> None:
        self._rows = rows

    def fetchall(self) -> List[tuple]:
        return self._rows


class _FakeSession:
    """Returns stale_rows on first execute(), missing_rows on second."""

    def __init__(self, stale_rows: List[tuple], missing_rows: List[tuple] | None = None) -> None:
        self._stale_rows = stale_rows
        self._missing_rows = missing_rows or []
        self._call = 0

    async def execute(self, _stmt) -> _FakeResult:
        self._call += 1
        if self._call == 1:
            return _FakeResult(self._stale_rows)
        return _FakeResult(self._missing_rows)


@pytest.mark.asyncio
async def test_single_bot_name_not_character_iterated():
    """S190 regression guard: stale-row path must not char-iterate single-bot TEXT."""
    check = TradedMarketsCheck()
    session = _FakeSession(stale_rows=[("MirrorBot", "mkt_abc", "2026-04-01", "2026-04-10")])
    result = await check.execute(session)
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.recon_type == "TRADED_MARKETS_DRIFT"
    assert v.bot_name == "MirrorBot"  # NOT "M,i,r,r,o,r,B,o,t"
    assert v.market_id == "mkt_abc"


@pytest.mark.asyncio
async def test_csv_bot_names_attributes_to_first_bot():
    """Multi-bot CSV rows: match sibling check's first-bot attribution."""
    check = TradedMarketsCheck()
    session = _FakeSession(stale_rows=[
        ("WeatherBot,MirrorBot", "mkt_xyz", "2026-04-05", None),
    ])
    result = await check.execute(session)
    assert len(result.violations) == 1
    assert result.violations[0].bot_name == "WeatherBot"


@pytest.mark.asyncio
async def test_empty_session_produces_no_violations():
    check = TradedMarketsCheck()
    session = _FakeSession(stale_rows=[], missing_rows=[])
    result = await check.execute(session)
    assert result.passed is True
    assert result.violations == []


@pytest.mark.asyncio
async def test_missing_row_path_uses_singular_bot_name_column():
    """L70-81 emits from trade_events.bot_name (singular TEXT) — unchanged by S190 fix."""
    check = TradedMarketsCheck()
    session = _FakeSession(
        stale_rows=[],
        missing_rows=[("EsportsBot", "mkt_def", 3)],
    )
    result = await check.execute(session)
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.bot_name == "EsportsBot"
    assert v.details["reason"] == "missing_traded_markets_row"
    assert v.details["entry_count"] == 3
