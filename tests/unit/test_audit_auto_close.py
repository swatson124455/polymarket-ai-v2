"""S196 — Contract tests for auto_close_resolved_violations.

Two close rules covered:
  1. SELF-RESOLVED — OPEN row whose (bot, market) is NOT in today's detected
     set transitions to RESOLVED. The condition no longer holds.
  2. SUPERSEDED — OPEN row whose key IS in today's detected set AND
     recon_date < today transitions to RESOLVED. Today's detection is the
     canonical OPEN row; earlier-day snapshots are stale.

Conservative scope:
  - Do NOT auto-close based on incomplete (timed-out or errored) check runs.
  - Do NOT auto-close based on zero-violation runs of a recon_type — only
    fires when today's run produced AT LEAST ONE violation of that type
    from a clean check.
  - Multi-type checks handled per recon_type independently.
"""
from datetime import date, timedelta
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
    it's NOT in today's detected set. Detected mkt1 row stays OPEN (recon_date=today).
    """
    today = date.today()
    open_rows = [
        (101, "botA", "mkt1", today),  # in detected set, recon_date=today → stays OPEN
        (102, "botA", "mkt2", today),  # NOT in detected set → self-resolved auto-close
        (103, "botA", "mkt3", today),  # NOT in detected set → self-resolved auto-close
    ]
    select_result = MagicMock(fetchall=MagicMock(return_value=open_rows))
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[select_result, MagicMock()])
    session.commit = AsyncMock()

    results = [
        _check_result("size_invariant", [_v("SIZE_INVARIANT", "botA", "mkt1")]),
    ]

    closed = await auto_close_resolved_violations(
        session, run_id=42, results=results, today=today,
    )

    assert closed == 2, f"expected 2 stale rows auto-closed, got {closed}"
    # Verify the UPDATE was called with the correct break_ids.
    update_call = session.execute.call_args_list[1]
    update_kwargs = update_call[0][1]
    assert sorted(update_kwargs["ids"]) == [102, 103]
    assert "S196 auto-close" in update_kwargs["note"]
    assert "self-resolved" in update_kwargs["note"]
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
    today = date.today()
    si_open_rows = [
        (201, "botA", "mkt1", today),  # detected, today → keep OPEN
        (202, "botA", "mkt5", today),  # stale (key not in set) → auto-close
    ]
    ns_open_rows = [
        (301, "botB", "mkt9", today),  # detected → keep OPEN
        (302, "botC", "mkt9", today),  # stale → auto-close
    ]
    si_select = MagicMock(fetchall=MagicMock(return_value=si_open_rows))
    ns_select = MagicMock(fetchall=MagicMock(return_value=ns_open_rows))
    update_mock = MagicMock()

    session = AsyncMock()

    async def execute_side_effect(stmt, params=None):
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

    closed = await auto_close_resolved_violations(
        session, run_id=42, results=results, today=today,
    )

    assert closed == 2, f"expected 1+1=2 auto-closed (one per type), got {closed}"


@pytest.mark.asyncio
async def test_auto_close_uses_resolved_status_not_acknowledged():
    """The CHECK constraint allows ('OPEN', 'RESOLVED', 'ACKNOWLEDGED'). Auto-close
    must use 'RESOLVED' (semantically: condition no longer holds), not
    'ACKNOWLEDGED' (semantically: human reviewed and dismissed)."""
    today = date.today()
    open_rows = [(101, "botA", "mkt2", today)]  # stale (key not detected)
    select_result = MagicMock(fetchall=MagicMock(return_value=open_rows))
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[select_result, MagicMock()])
    session.commit = AsyncMock()

    results = [
        _check_result("size_invariant", [_v("SIZE_INVARIANT", "botA", "mkt1")]),
    ]

    await auto_close_resolved_violations(
        session, run_id=42, results=results, today=today,
    )

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
    set AND recon_date=today must NOT be in the auto-close ids list (neither
    self-resolved nor superseded)."""
    today = date.today()
    open_rows = [
        (101, "botA", "mkt1", today),  # detected, today → stays OPEN
        (102, "botA", "mkt2", today),  # not detected → self-resolved
        (103, "botA", "mkt3", today),  # detected, today → stays OPEN
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

    closed = await auto_close_resolved_violations(
        session, run_id=42, results=results, today=today,
    )

    assert closed == 1
    update_kwargs = session.execute.call_args_list[1][0][1]
    assert update_kwargs["ids"] == [102], (
        f"only mkt2 should auto-close; mkt1 and mkt3 are still detected. Got {update_kwargs['ids']}"
    )


# ── Supersede rule ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_auto_close_supersedes_yesterdays_row_when_today_still_detects():
    """The dominant accumulator: same (bot, market) flagged daily for permanent
    inflation. Yesterday's OPEN row must transition to RESOLVED when today's
    fresh detection supersedes it. Today's row stays OPEN as the canonical."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    open_rows = [
        (901, "botA", "mkt1", yesterday),  # superseded → close
        (902, "botA", "mkt1", today),       # canonical today's row → stays OPEN
    ]
    select_result = MagicMock(fetchall=MagicMock(return_value=open_rows))
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[select_result, MagicMock()])
    session.commit = AsyncMock()

    results = [
        _check_result("size_invariant", [_v("SIZE_INVARIANT", "botA", "mkt1")]),
    ]

    closed = await auto_close_resolved_violations(
        session, run_id=99, results=results, today=today,
    )

    assert closed == 1
    update_call = session.execute.call_args_list[1]
    update_kwargs = update_call[0][1]
    assert update_kwargs["ids"] == [901], (
        f"only yesterday's row should be superseded; today's row is canonical. Got {update_kwargs['ids']}"
    )
    assert "superseded by run #99" in update_kwargs["note"]
    assert "SIZE_INVARIANT" in update_kwargs["note"]


@pytest.mark.asyncio
async def test_auto_close_supersedes_multiple_old_rows_for_same_key():
    """Permanent issue with N days of accumulation: all N-1 older rows
    transition to RESOLVED on a single audit run; only today's row stays OPEN."""
    today = date.today()
    open_rows = [
        (1001, "botA", "mkt1", today - timedelta(days=10)),
        (1002, "botA", "mkt1", today - timedelta(days=5)),
        (1003, "botA", "mkt1", today - timedelta(days=1)),
        (1004, "botA", "mkt1", today),  # canonical
    ]
    select_result = MagicMock(fetchall=MagicMock(return_value=open_rows))
    session = AsyncMock()
    session.execute = AsyncMock(side_effect=[select_result, MagicMock()])
    session.commit = AsyncMock()

    results = [
        _check_result("size_invariant", [_v("SIZE_INVARIANT", "botA", "mkt1")]),
    ]

    closed = await auto_close_resolved_violations(
        session, run_id=99, results=results, today=today,
    )

    assert closed == 3, f"expected 3 superseded rows, got {closed}"
    update_kwargs = session.execute.call_args_list[1][0][1]
    assert sorted(update_kwargs["ids"]) == [1001, 1002, 1003]


@pytest.mark.asyncio
async def test_auto_close_self_resolved_and_superseded_in_same_run():
    """Both rules fire in one audit run: self-resolved rows on stale keys
    AND superseded rows on still-detected keys with old recon_date. Total
    closed count equals sum across both rules."""
    today = date.today()
    yesterday = today - timedelta(days=1)
    open_rows = [
        (1101, "botA", "mkt1", today),       # detected, today → keep OPEN
        (1102, "botA", "mkt1", yesterday),   # detected, old → SUPERSEDED
        (1103, "botA", "mkt2", yesterday),   # NOT detected → SELF-RESOLVED
        (1104, "botA", "mkt3", today),       # NOT detected, today → SELF-RESOLVED
    ]
    select_result = MagicMock(fetchall=MagicMock(return_value=open_rows))
    session = AsyncMock()
    # 3 executes total: SELECT, UPDATE self-resolved, UPDATE superseded.
    session.execute = AsyncMock(side_effect=[
        select_result, MagicMock(), MagicMock(),
    ])
    session.commit = AsyncMock()

    results = [
        _check_result("size_invariant", [_v("SIZE_INVARIANT", "botA", "mkt1")]),
    ]

    closed = await auto_close_resolved_violations(
        session, run_id=99, results=results, today=today,
    )

    assert closed == 3, f"expected 1 superseded + 2 self-resolved = 3, got {closed}"
    # Two UPDATE calls: self-resolved first (per code order), then superseded.
    self_resolved_kwargs = session.execute.call_args_list[1][0][1]
    superseded_kwargs = session.execute.call_args_list[2][0][1]
    assert sorted(self_resolved_kwargs["ids"]) == [1103, 1104]
    assert "self-resolved" in self_resolved_kwargs["note"]
    assert superseded_kwargs["ids"] == [1102]
    assert "superseded" in superseded_kwargs["note"]
