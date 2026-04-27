"""S196 — Contract tests for auto_close_resolved_violations.

Purpose:
  - OPEN reconciliation_breaks rows whose condition self-resolved must
    transition to RESOLVED on the next clean audit run.
  - Conservative scope: do NOT auto-close based on incomplete (timed-out
    or errored) check runs. Do NOT auto-close based on zero-violation runs
    of a recon_type — only fires when today's run produced AT LEAST ONE
    violation of that type from a clean check.
  - Multi-type checks are handled per recon_type independently.
"""
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from base_engine.audit.check_result import AuditViolation, CheckResult
from base_engine.audit.result_store import auto_close_resolved_violations


def _v(recon_type: str, bot: str, market: str, sev: str = "CRITICAL") -> AuditViolation:
    """Helper: construct a minimal valid AuditViolation."""
    return AuditViolation(
        recon_type=recon_type,
        bot_name=bot,
        market_id=market,
        severity=sev,
        details={"sentinel": f"{recon_type}|{bot}|{market}"},
        internal_value=Decimal("1"),
    )


def _check_result(
    name: str,
    violations,
    *,
    timed_out: bool = False,
    summary: str = "",
    passed: bool = True,
) -> CheckResult:
    return CheckResult(
        check_name=name,
        passed=passed if not violations else False,
        violations=violations or [],
        duration_ms=10.0,
        tables_queried=[],
        summary=summary or f"{name} ran with {len(violations)} violations",
        timed_out=timed_out,
    )


def _mock_session(open_rows=None):
    """Mock session whose first execute returns the OPEN rows query result,
    subsequent executes are UPDATE statements (rowcount-based)."""
    open_rows = open_rows or []
    select_result = MagicMock()
    select_result.fetchall = MagicMock(return_value=open_rows)

    update_result = MagicMock()
    update_result.rowcount = 0  # not asserted on

    session = AsyncMock()
    # By default first execute returns SELECT, others return UPDATE.
    session.execute = AsyncMock(side_effect=lambda *args, **kwargs: select_result)
    session.commit = AsyncMock()
    return session, select_result


@pytest.mark.asyncio
async def test_auto_close_resolves_stale_rows_not_in_todays_detected_set():
    """Today's run found violations for (botA, mkt1). An OPEN row exists for
    (botA, mkt2) of the same recon_type — that row must auto-close because
    it's NOT in today's detected set."""
    open_rows = [
        (101, "botA", "mkt1"),  # in today's detected set → stays OPEN
        (102, "botA", "mkt2"),  # NOT in today's set → auto-close
        (103, "botA", "mkt3"),  # NOT in today's set → auto-close
    ]
    select_result = MagicMock(fetchall=MagicMock(return_value=open_rows))
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[select_result, MagicMock()])
    session.commit = AsyncMock()

    results = [
        _check_result("size_invariant", [_v("SIZE_INVARIANT", "botA", "mkt1")]),
    ]

    closed = await auto_close_resolved_violations(session, run_id=42, results=results)

    assert closed == 2, f"expected 2 stale rows auto-closed, got {closed}"
    # Verify the UPDATE was called with the correct break_ids.
    update_call = session.execute.call_args_list[1]
    update_kwargs = update_call[0][1]
    assert sorted(update_kwargs["ids"]) == [102, 103]
    assert "S196 auto-close" in update_kwargs["note"]
    assert "SIZE_INVARIANT" in update_kwargs["note"]
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_auto_close_skips_recon_type_when_check_timed_out():
    """If the size_invariant check timed out, OPEN rows of SIZE_INVARIANT
    must NOT be auto-closed — we don't have ground truth for today."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    results = [
        _check_result(
            "size_invariant",
            [],
            timed_out=True,
            summary="timed_out: statement timeout",
        ),
    ]

    closed = await auto_close_resolved_violations(session, run_id=42, results=results)

    assert closed == 0
    # No SQL should have been executed at all.
    assert session.execute.await_count == 0
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_close_skips_errored_check():
    """If a check's summary starts with 'error:', auto-close must skip its types."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    results = [
        _check_result(
            "size_invariant",
            [],
            timed_out=False,
            summary="error: KeyError on row 5",
        ),
    ]

    closed = await auto_close_resolved_violations(session, run_id=42, results=results)

    assert closed == 0
    assert session.execute.await_count == 0


@pytest.mark.asyncio
async def test_auto_close_no_op_when_clean_check_found_zero_violations():
    """A clean check that produced 0 violations of its type does NOT trigger
    auto-close. Mitigation against a buggy check returning empty results,
    which would otherwise mass-close legitimate OPEN rows."""
    session = AsyncMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()

    results = [
        _check_result("size_invariant", [], timed_out=False, summary="0 violations"),
    ]

    closed = await auto_close_resolved_violations(session, run_id=42, results=results)

    assert closed == 0, (
        "auto-close must NOT fire when today's run produced zero violations of "
        "this recon_type — could be a buggy check returning empty results"
    )
    assert session.execute.await_count == 0


@pytest.mark.asyncio
async def test_auto_close_handles_multi_type_check_per_recon_type():
    """A check that emits multiple recon_types (e.g., size_invariant emits
    SIZE_INVARIANT and NEGATIVE_SIZE) must auto-close each independently.
    SIZE_INVARIANT detected for (botA, mkt1) — auto-close stale SIZE_INVARIANT
    rows. NEGATIVE_SIZE detected for (botB, mkt9) — auto-close stale
    NEGATIVE_SIZE rows. Cross-type contamination must NOT happen."""
    # First SELECT: SIZE_INVARIANT OPEN rows. Second SELECT: NEGATIVE_SIZE OPEN.
    si_open_rows = [
        (201, "botA", "mkt1"),  # detected → keep OPEN
        (202, "botA", "mkt5"),  # stale → auto-close
    ]
    ns_open_rows = [
        (301, "botB", "mkt9"),  # detected → keep OPEN
        (302, "botC", "mkt9"),  # stale → auto-close
    ]
    si_select = MagicMock(fetchall=MagicMock(return_value=si_open_rows))
    ns_select = MagicMock(fetchall=MagicMock(return_value=ns_open_rows))
    update_mock = MagicMock()

    session = AsyncMock()
    # Order may vary based on dict iteration — set up both possible orders.
    # We'll instead capture by SQL parameter inspection.
    call_sequence = []

    async def execute_side_effect(stmt, params=None):
        call_sequence.append((str(stmt.text), params))
        sql_text = str(stmt.text)
        if "SIZE_INVARIANT" in str(params or {}).upper() if params else False:
            return si_select
        if params and params.get("recon_type") == "SIZE_INVARIANT":
            return si_select
        if params and params.get("recon_type") == "NEGATIVE_SIZE":
            return ns_select
        return update_mock

    session.execute = AsyncMock(side_effect=execute_side_effect)
    session.commit = AsyncMock()

    results = [
        _check_result(
            "size_invariant",
            [
                _v("SIZE_INVARIANT", "botA", "mkt1"),
                _v("NEGATIVE_SIZE", "botB", "mkt9"),
            ],
        ),
    ]

    closed = await auto_close_resolved_violations(session, run_id=42, results=results)

    assert closed == 2, f"expected 1+1=2 auto-closed (one per type), got {closed}"


@pytest.mark.asyncio
async def test_auto_close_uses_resolved_status_not_acknowledged():
    """The CHECK constraint allows ('OPEN', 'RESOLVED', 'ACKNOWLEDGED'). Auto-close
    must use 'RESOLVED' (semantically: condition no longer holds), not
    'ACKNOWLEDGED' (semantically: human reviewed and dismissed)."""
    open_rows = [(101, "botA", "mkt2")]  # stale
    select_result = MagicMock(fetchall=MagicMock(return_value=open_rows))
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[select_result, MagicMock()])
    session.commit = AsyncMock()

    results = [
        _check_result("size_invariant", [_v("SIZE_INVARIANT", "botA", "mkt1")]),
    ]

    await auto_close_resolved_violations(session, run_id=42, results=results)

    update_call = session.execute.call_args_list[1]
    update_sql = str(update_call[0][0].text)
    assert "status = 'RESOLVED'" in update_sql, (
        "auto-close must use 'RESOLVED' status (condition self-resolved), "
        "not 'ACKNOWLEDGED' (which means human-dismissed)"
    )
    assert "resolved_at = NOW()" in update_sql


@pytest.mark.asyncio
async def test_auto_close_returns_zero_when_no_open_rows_of_detected_type():
    """If no OPEN rows of the detected recon_type exist, auto-close is a no-op
    (returns 0, no UPDATE issued, no commit)."""
    select_result = MagicMock(fetchall=MagicMock(return_value=[]))
    session = AsyncMock()
    session.execute = AsyncMock(return_value=select_result)
    session.commit = AsyncMock()

    results = [
        _check_result("size_invariant", [_v("SIZE_INVARIANT", "botA", "mkt1")]),
    ]

    closed = await auto_close_resolved_violations(session, run_id=42, results=results)

    assert closed == 0
    # SELECT was called but no UPDATE.
    assert session.execute.await_count == 1
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_auto_close_preserves_currently_detected_violations():
    """Belt-and-suspenders: a row whose (bot, market) is in today's detected
    set must NOT be in the auto-close ids list."""
    open_rows = [
        (101, "botA", "mkt1"),
        (102, "botA", "mkt2"),
        (103, "botA", "mkt3"),
    ]
    select_result = MagicMock(fetchall=MagicMock(return_value=open_rows))
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[select_result, MagicMock()])
    session.commit = AsyncMock()

    results = [
        _check_result(
            "size_invariant",
            [
                _v("SIZE_INVARIANT", "botA", "mkt1"),
                _v("SIZE_INVARIANT", "botA", "mkt3"),
            ],
        ),
    ]

    closed = await auto_close_resolved_violations(session, run_id=42, results=results)

    assert closed == 1
    update_kwargs = session.execute.call_args_list[1][0][1]
    assert update_kwargs["ids"] == [102], (
        f"only mkt2 should auto-close; mkt1 and mkt3 are still detected. Got {update_kwargs['ids']}"
    )
