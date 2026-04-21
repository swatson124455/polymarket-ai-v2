"""
Contract test for PositionTradeEventsCheck (S186 guarded port).

Two test layers:

1. SQL-shape pins — catch the regression class directly by inspecting the
   query text. Mocked-session unit tests (layer 2) can't catch SQL-contract
   bugs per S184's lesson; SQL-shape assertions can.

   The specific failure classes being pinned:

   (a) Pre-S186 the check grouped trade_events by (bot_name, market_id, side)
       and joined to positions on all three keys. Historical EXIT events
       used side='SELL' while ENTRYs used YES/NO (S163 transition), so
       te_net(bot, mkt, 'SELL') rows — built from legacy EXIT(SELL) alone
       with zero ENTRYs and large negative net — matched legacy
       positions.side='SELL' rows and produced false-positive
       POSITION_SIZE_MISMATCH violations.

   (b) A naive mirror of the S164 size_invariant_check fix (just drop side
       from GROUP BY and JOIN) shifts false positives to dual-side-open
       markets (positions has size > 0 on BOTH YES and NO simultaneously).
       Side-agnostic te_net aggregates both sides' entries, then mismatches
       each side's positions row. S186 correction adds a NOT EXISTS guard
       that excludes such markets; dual-side-open is a separate diagnostic.

2. Mocked-session semantics — empty session yields no violations;
   a mismatch row produces a violation with correct fields; a phantom
   row produces a violation with the phantom reason.
"""
from typing import List, Optional

import pytest

from base_engine.audit.checks.position_trade_events_check import (
    PositionTradeEventsCheck,
)


class _FakeResult:
    def __init__(self, rows: List[tuple]) -> None:
        self._rows = rows

    def fetchall(self) -> List[tuple]:
        return self._rows


class _CapturingSession:
    """Captures the SQL text of each execute() call for shape assertions.

    Returns pre-canned rows per call index so tests can drive both the
    mismatch query (call 0) and the phantom query (call 1) independently.
    """

    def __init__(
        self,
        mismatch_rows: Optional[List[tuple]] = None,
        phantom_rows: Optional[List[tuple]] = None,
    ) -> None:
        self._rows_by_call = [mismatch_rows or [], phantom_rows or []]
        self.statements: List[str] = []
        self._i = 0

    async def execute(self, stmt) -> _FakeResult:
        self.statements.append(str(stmt))
        rows = self._rows_by_call[self._i] if self._i < len(self._rows_by_call) else []
        self._i += 1
        return _FakeResult(rows)


# --- Layer 1: SQL-shape pins ---

@pytest.mark.asyncio
async def test_mismatch_query_groups_by_bot_and_market_only_not_side():
    """S186 port pin: the te_net CTE must aggregate by (bot_name, market_id)
    with NO side column. Regression on this is the pre-S164 bug class."""
    check = PositionTradeEventsCheck()
    session = _CapturingSession()
    await check.execute(session)

    mismatch_sql = session.statements[0]

    # Must-not: pre-port grouping with side
    assert "GROUP BY bot_name, market_id, side" not in mismatch_sql, (
        "te_net CTE regressed to per-side GROUP BY — S164 fix reverted. "
        "This reintroduces false positives on legacy S163-encoded markets."
    )
    # Must-have: side-agnostic grouping
    assert "GROUP BY bot_name, market_id" in mismatch_sql, (
        "te_net CTE missing side-agnostic GROUP BY — port is broken."
    )


@pytest.mark.asyncio
async def test_mismatch_query_join_does_not_match_on_side():
    """S186 port pin: the JOIN from positions to te_net must NOT match on
    side. Matching on side is what coupled legacy side='SELL' EXIT events
    to side='SELL' positions rows and produced the false positives."""
    check = PositionTradeEventsCheck()
    session = _CapturingSession()
    await check.execute(session)

    mismatch_sql = session.statements[0]

    assert "AND te.side = p.side" not in mismatch_sql, (
        "JOIN regressed to match on side — S164 fix reverted. "
        "Remove side from the join condition."
    )
    # Must-have both remaining keys
    assert "ON te.bot_name  = p.source_bot" in mismatch_sql
    assert "AND te.market_id = p.market_id" in mismatch_sql


@pytest.mark.asyncio
async def test_mismatch_query_cte_select_excludes_side_column():
    """S186 port pin: the te_net CTE projection must NOT select `side`.
    If side is selected but not grouped, PostgreSQL will raise; this
    guards against a half-port."""
    check = PositionTradeEventsCheck()
    session = _CapturingSession()
    await check.execute(session)

    mismatch_sql = session.statements[0]

    # The CTE lead: "SELECT bot_name, market_id," without ", side"
    assert "SELECT bot_name, market_id,\n" in mismatch_sql, (
        "te_net CTE projection shape changed — port or query altered."
    )
    assert "SELECT bot_name, market_id, side," not in mismatch_sql, (
        "te_net CTE still projects side — port incomplete."
    )


@pytest.mark.asyncio
async def test_mismatch_query_has_multi_side_open_guard():
    """S186 correction pin: side-agnostic te_net aggregation is unsafe
    on dual-side-open markets (positions with size > 0 on BOTH YES and
    NO concurrently) because aggregating entries across sides produces
    a net that mismatches each individual side's positions row. The
    guarded port excludes such markets via NOT EXISTS sibling with
    size > 0. Without this guard, the port would introduce a different
    class of false positives on dual-side markets while fixing the
    legacy-SELL class. Any regression that drops the guard must fail
    this assertion loudly."""
    check = PositionTradeEventsCheck()
    session = _CapturingSession()
    await check.execute(session)

    mismatch_sql = session.statements[0]

    # The guard: a NOT EXISTS subquery against positions with
    # p2.side <> p.side and size > 0. Check both the NOT EXISTS keyword
    # and the side-opposite predicate are both present.
    assert "NOT EXISTS" in mismatch_sql, (
        "Mismatch query missing NOT EXISTS guard — dual-side false positives "
        "will return. See S186 correction in module docstring."
    )
    assert "p2.side" in mismatch_sql and "<> p.side" in mismatch_sql, (
        "Multi-side guard uses wrong predicate shape — must compare "
        "sibling positions' side against current row via <>."
    )
    # Ensure the guard is targeting positions, not trade_events
    assert "FROM positions p2" in mismatch_sql, (
        "Multi-side guard not scoped to positions table."
    )


# --- Layer 2: mocked-session semantics ---

@pytest.mark.asyncio
async def test_empty_session_produces_no_violations():
    check = PositionTradeEventsCheck()
    session = _CapturingSession()
    result = await check.execute(session)
    assert result.passed is True
    assert result.violations == []
    assert result.check_name == "position_size_mismatch"
    assert result.tables_queried == ["positions", "trade_events"]


@pytest.mark.asyncio
async def test_mismatch_row_produces_critical_violation_with_full_details():
    """Happy path for the mismatch query. Row shape matches SELECT clause:
    (source_bot, market_id, side, pos_size, net_size, total_entered, abs_diff)."""
    check = PositionTradeEventsCheck()
    session = _CapturingSession(
        mismatch_rows=[
            ("MirrorBot", "mkt_abc", "YES", 100.0, 50.0, 100.0, 50.0),
        ],
    )
    result = await check.execute(session)

    assert result.passed is False
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.recon_type == "POSITION_SIZE_MISMATCH"
    assert v.severity == "CRITICAL"
    assert v.bot_name == "MirrorBot"
    assert v.market_id == "mkt_abc"
    assert v.details["side"] == "YES"
    assert v.details["positions_size"] == 100.0
    assert v.details["trade_events_net"] == 50.0
    assert v.details["total_entered"] == 100.0
    assert v.details["abs_diff"] == 50.0
    # reason is NOT set on mismatch path — only on phantom path
    assert "reason" not in v.details


@pytest.mark.asyncio
async def test_phantom_row_produces_violation_with_phantom_reason():
    """Phantom path: positions row with size > 0 but no ENTRY event.
    Query shape: (source_bot, market_id, side, pos_size)."""
    check = PositionTradeEventsCheck()
    session = _CapturingSession(
        phantom_rows=[
            ("WeatherBot", "mkt_xyz", "NO", 75.0),
        ],
    )
    result = await check.execute(session)

    assert result.passed is False
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.recon_type == "POSITION_SIZE_MISMATCH"
    assert v.severity == "CRITICAL"
    assert v.bot_name == "WeatherBot"
    assert v.market_id == "mkt_xyz"
    assert v.details["reason"] == "phantom_position_no_entry_event"
    assert v.details["side"] == "NO"
    assert v.details["positions_size"] == 75.0


@pytest.mark.asyncio
async def test_tables_queried_declaration():
    check = PositionTradeEventsCheck()
    assert check.tables_queried == ["positions", "trade_events"]
    assert check.name == "position_size_mismatch"
