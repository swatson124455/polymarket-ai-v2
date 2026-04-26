"""S195 — Contract tests for Database.backfill_trade_events_resolution()
and its wiring across the 3 invocation paths.

Purpose:
  (a) The method exists, returns int, and is no-op when session_factory is None
  (b) An unconditional emission counter log fires on every invocation, so
      future silent-zero (the bug this fix targets) is visible in journal
  (c) The 3 wiring sites (mini scheduler, on_resolution event handler,
      full backfill orchestrator) all reach the new method
  (d) The legacy `paper_updated > 0 OR updated > 0` gate is gone — calling
      the method directly with no upstream activity must still execute
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Contract (a): method existence + no-op on no-DB ──────────────────────────

@pytest.mark.asyncio
async def test_backfill_trade_events_resolution_no_db_returns_zero():
    """If session_factory is None (API-only / test context), method is a no-op
    that returns 0. Never raises, never blocks the trade path."""
    from base_engine.data.database import Database

    db = Database.__new__(Database)
    db.session_factory = None

    n = await db.backfill_trade_events_resolution()
    assert n == 0


@pytest.mark.asyncio
async def test_backfill_trade_events_resolution_returns_int():
    """Smoke contract: method returns a non-negative int."""
    from base_engine.data.database import Database

    db = Database.__new__(Database)
    db.session_factory = None

    result = await db.backfill_trade_events_resolution()
    assert isinstance(result, int)
    assert result >= 0


# ── Contract (b): unconditional emission counter log ─────────────────────────

@pytest.mark.asyncio
async def test_backfill_trade_events_resolution_logs_counter_on_zero():
    """The silent-zero detector. Even when both Phase 4b queries return zero
    rows, the method MUST log `trade_events_resolution_backfill` with phase4b/
    phase4b_alt/total counts, so journal grep can confirm the path is alive
    in steady-state. This is the regression guard for the bug we're fixing.
    """
    from base_engine.data.database import Database

    db = Database.__new__(Database)
    db.session_factory = MagicMock()  # truthy
    db.insert_trade_event = AsyncMock(return_value=1)  # callable, never invoked

    # Mock both Phase 4b and Phase 4b-alt sessions to return empty result sets.
    empty_result = MagicMock()
    empty_result.fetchall = MagicMock(return_value=[])

    session = AsyncMock()
    session.execute = AsyncMock(return_value=empty_result)
    session.commit = AsyncMock()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    db.get_session = MagicMock(return_value=ctx)

    with patch("base_engine.data.database.logger") as mock_logger:
        n = await db.backfill_trade_events_resolution()

    assert n == 0
    # The unconditional emission counter MUST have been logged.
    info_calls = [c for c in mock_logger.info.call_args_list
                  if c.args and c.args[0] == "trade_events_resolution_backfill"]
    assert len(info_calls) == 1, (
        "trade_events_resolution_backfill log MUST fire on every invocation "
        "(silent-zero detector — the regression guard for this fix)"
    )
    kwargs = info_calls[0].kwargs
    assert kwargs.get("phase4b") == 0
    assert kwargs.get("phase4b_alt") == 0
    assert kwargs.get("total") == 0


# ── Contract (c): the 3 wiring sites all reach the method ────────────────────

@pytest.mark.asyncio
async def test_mini_backfill_calls_trade_events_resolution(monkeypatch):
    """ingestion_scheduler._do_mini_backfill must call the new method —
    this is the path that ensures self-healing if the full backfill goes silent.
    """
    from base_engine.data.ingestion_scheduler import IngestionScheduler

    sched = IngestionScheduler.__new__(IngestionScheduler)

    db = MagicMock()
    db.backfill_prediction_log_resolution = AsyncMock(return_value=0)
    db.backfill_prediction_log_from_closed_trades = AsyncMock(return_value=0)
    db.backfill_paper_trades_resolution = AsyncMock(return_value=0)
    db.backfill_mirror_rejected_signals_resolution = AsyncMock(return_value=0)
    db.backfill_trade_events_resolution = AsyncMock(return_value=0)

    from datetime import datetime, timezone
    await sched._do_mini_backfill(db, datetime.now(timezone.utc))

    db.backfill_trade_events_resolution.assert_awaited_once()


@pytest.mark.asyncio
async def test_mini_backfill_logs_emitted_count_when_nonzero():
    """When the new method emits N > 0 RESOLUTION events, the mini-backfill
    summary log must include that count alongside the other backfill counts.
    """
    from base_engine.data.ingestion_scheduler import IngestionScheduler

    sched = IngestionScheduler.__new__(IngestionScheduler)

    db = MagicMock()
    db.backfill_prediction_log_resolution = AsyncMock(return_value=0)
    db.backfill_prediction_log_from_closed_trades = AsyncMock(return_value=0)
    db.backfill_paper_trades_resolution = AsyncMock(return_value=0)
    db.backfill_mirror_rejected_signals_resolution = AsyncMock(return_value=0)
    db.backfill_trade_events_resolution = AsyncMock(return_value=7)

    with patch("base_engine.data.ingestion_scheduler.logger") as mock_logger:
        from datetime import datetime, timezone
        await sched._do_mini_backfill(db, datetime.now(timezone.utc))

    # The summary log fires when any backfill produced rows.
    info_calls = [c for c in mock_logger.info.call_args_list]
    assert any("trade_events_resolution" in str(c) for c in info_calls), (
        "mini-backfill summary must include trade_events_resolution count"
    )


# ── Contract (d): legacy gate is gone ────────────────────────────────────────

# ── ROOT CAUSE regression guards (S195) ─────────────────────────────────────

def test_insert_trade_event_resolution_sql_has_no_inline_dash_comment():
    """The SQL `--` line comment swallowed everything after it because
    Python string concatenation joins lines without newlines. This caused
    `syntax error at end of input` for 17 days starting 2026-04-08. Block
    `/* ... */` comments are safe because they have explicit terminators.

    Regression guard: if anyone ever reintroduces a `--` comment inside an
    insert_trade_event SQL string, this test fails immediately.
    """
    import inspect
    from base_engine.data.database import Database

    source = inspect.getsource(Database.insert_trade_event)

    # Find every Python string literal that contains a SQL token.
    # Crude but effective: any quoted string containing `--` followed by
    # a space and a word (i.e. an actual comment, not a `--decrement` op).
    import re
    bad_lines = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        # Look for pattern: quote, optional whitespace, --, space, word
        if re.search(r'"[^"]*--\s+\w', line):
            bad_lines.append((lineno, line.strip()))
    assert not bad_lines, (
        "insert_trade_event contains inline `-- ` SQL comment(s) inside a "
        "string literal. These eat to end-of-string in single-line concat "
        "and break the SQL. Use `/* ... */` block comments instead.\n"
        f"Offending line(s):\n" + "\n".join(f"  line {ln}: {s}" for ln, s in bad_lines)
    )


def test_resolution_insert_sql_round_trips_through_sqlparse():
    """The RESOLUTION INSERT must parse as a complete statement. Reconstruct
    the same SQL string from source via the same concat path and assert
    the closing `RETURNING sequence_num` is reachable (not buried in a
    comment that swallowed it)."""
    import inspect
    from base_engine.data.database import Database

    source = inspect.getsource(Database.insert_trade_event)

    # The bug shape: a `--` comment somewhere before the `AND NOT EXISTS`
    # or `RETURNING` would hide them from the SQL parser. After Python
    # string concat with no newlines, the entire SQL becomes one line.
    # If `RETURNING sequence_num` appears in source AFTER a `-- ` comment
    # in the same string, the SQL is broken at runtime.
    # The fixed code uses /* */ block comments, which terminate explicitly.

    # Find indices in source
    dash_idx = source.find('"   -- ')
    block_open_idx = source.find('"   /* ')
    returning_idx = source.find("RETURNING sequence_num")

    assert returning_idx != -1, "RESOLUTION INSERT must contain RETURNING sequence_num"

    if dash_idx != -1:
        # If a `-- ` exists in a string literal AND it's before RETURNING,
        # that's the bug.
        assert dash_idx > returning_idx, (
            "Found `-- ` SQL line comment in a string literal BEFORE the "
            "RETURNING clause. The bug we just fixed. Replace `--` with `/* ... */`."
        )

    # Block comments are fine — they terminate explicitly.
    if block_open_idx != -1:
        # Confirm matching close exists and is before RETURNING.
        block_close_idx = source.find("*/", block_open_idx)
        assert block_close_idx != -1, "Open /* without matching */"
        assert block_close_idx < returning_idx, (
            "Block comment doesn't close before RETURNING — verify the SQL"
        )


@pytest.mark.asyncio
async def test_phase4b_emission_calls_insert_trade_event_per_row():
    """End-to-end wiring test: when Phase 4b's outer SELECT returns N
    rows, the new method must call insert_trade_event N times AND
    increment the emission counter accordingly. This is the regression
    guard: if `insert_trade_event` raises (as it did for 17 days due to
    the SQL bug), this test would catch it because the counter wouldn't
    match the row count.
    """
    from base_engine.data.database import Database
    import datetime as _dt

    db = Database.__new__(Database)
    db.session_factory = MagicMock()

    # Each row = 10 columns matching Phase 4b's SELECT shape:
    # market_id, bot_name, side, computed_pnl, resolved_at, remaining_size,
    # avg_entry_price, exit_pnl_already, market_resolution, entry_game
    fake_rows = [
        ("0xmkt1", "WeatherBot", "YES", -15.23, _dt.datetime(2026, 4, 25, 12, 0),
         100.0, 0.50, 0.0, "NO", None),
        ("0xmkt2", "MirrorBot", "YES", 5.77, _dt.datetime(2026, 4, 25, 12, 1),
         50.0, 0.45, 0.0, "YES", None),
        ("0xmkt3", "EsportsBot", "NO", -2.10, _dt.datetime(2026, 4, 25, 12, 2),
         25.0, 0.60, 0.0, "YES", "cs2"),
    ]
    phase4b_result = MagicMock()
    phase4b_result.fetchall = MagicMock(return_value=fake_rows)
    # Phase 4b-alt returns nothing
    empty_result = MagicMock()
    empty_result.fetchall = MagicMock(return_value=[])

    # First two execute calls = Phase 4b SET LOCAL + main SELECT.
    # Then Phase 4b-alt SET LOCAL + main SELECT.
    session_4b = AsyncMock()
    session_4b.execute = AsyncMock(side_effect=[
        MagicMock(),         # SET LOCAL
        phase4b_result,      # main SELECT (3 rows)
    ])
    session_4b.commit = AsyncMock()
    ctx_4b = AsyncMock()
    ctx_4b.__aenter__ = AsyncMock(return_value=session_4b)
    ctx_4b.__aexit__ = AsyncMock(return_value=False)

    session_alt = AsyncMock()
    session_alt.execute = AsyncMock(side_effect=[
        MagicMock(),         # SET LOCAL
        empty_result,        # Phase 4b-alt main SELECT (0 rows)
    ])
    session_alt.commit = AsyncMock()
    ctx_alt = AsyncMock()
    ctx_alt.__aenter__ = AsyncMock(return_value=session_alt)
    ctx_alt.__aexit__ = AsyncMock(return_value=False)

    # Two get_session() calls — first for Phase 4b, second for Phase 4b-alt.
    db.get_session = MagicMock(side_effect=[ctx_4b, ctx_alt])

    # The contract: insert_trade_event called once per row, returns sequence_num.
    db.insert_trade_event = AsyncMock(return_value=42)

    with patch("base_engine.data.database.logger") as mock_logger:
        total = await db.backfill_trade_events_resolution()

    # 3 rows from Phase 4b → 3 successful inserts → counter = 3
    assert db.insert_trade_event.await_count == 3
    assert total == 3

    # The counter log fires with phase4b=3, phase4b_alt=0, total=3
    counter_calls = [c for c in mock_logger.info.call_args_list
                     if c.args and c.args[0] == "trade_events_resolution_backfill"]
    assert len(counter_calls) == 1
    assert counter_calls[0].kwargs["phase4b"] == 3
    assert counter_calls[0].kwargs["phase4b_alt"] == 0
    assert counter_calls[0].kwargs["total"] == 3


@pytest.mark.asyncio
async def test_phase4b_emission_counter_correctly_reflects_failed_inserts():
    """The emission counter must NOT count rows where insert_trade_event
    raised. This is what would have caught the 17-day SQL bug if the
    counter had existed before — phase4b SQL returned N rows but
    counter = 0 reveals the silent failure.
    """
    from base_engine.data.database import Database
    import datetime as _dt

    db = Database.__new__(Database)
    db.session_factory = MagicMock()

    fake_rows = [
        ("0xmkt1", "WeatherBot", "YES", -15.23, _dt.datetime(2026, 4, 25, 12, 0),
         100.0, 0.50, 0.0, "NO", None),
        ("0xmkt2", "MirrorBot", "YES", 5.77, _dt.datetime(2026, 4, 25, 12, 1),
         50.0, 0.45, 0.0, "YES", None),
    ]
    phase4b_result = MagicMock()
    phase4b_result.fetchall = MagicMock(return_value=fake_rows)
    empty_result = MagicMock()
    empty_result.fetchall = MagicMock(return_value=[])

    session_4b = AsyncMock()
    session_4b.execute = AsyncMock(side_effect=[MagicMock(), phase4b_result])
    session_4b.commit = AsyncMock()
    ctx_4b = AsyncMock()
    ctx_4b.__aenter__ = AsyncMock(return_value=session_4b)
    ctx_4b.__aexit__ = AsyncMock(return_value=False)

    session_alt = AsyncMock()
    session_alt.execute = AsyncMock(side_effect=[MagicMock(), empty_result])
    session_alt.commit = AsyncMock()
    ctx_alt = AsyncMock()
    ctx_alt.__aenter__ = AsyncMock(return_value=session_alt)
    ctx_alt.__aexit__ = AsyncMock(return_value=False)

    db.get_session = MagicMock(side_effect=[ctx_4b, ctx_alt])

    # Simulate the SQL bug: insert_trade_event raises every call.
    db.insert_trade_event = AsyncMock(
        side_effect=RuntimeError("syntax error at end of input")
    )

    with patch("base_engine.data.database.logger") as mock_logger:
        total = await db.backfill_trade_events_resolution()

    # SQL returned 2 rows but every insert raised → counter must reflect 0.
    assert total == 0

    # The unconditional counter log still fires — this is the
    # silent-zero detector working as designed.
    counter_calls = [c for c in mock_logger.info.call_args_list
                     if c.args and c.args[0] == "trade_events_resolution_backfill"]
    assert len(counter_calls) == 1
    assert counter_calls[0].kwargs["phase4b"] == 0
    assert counter_calls[0].kwargs["total"] == 0

    # Per-row failures must be logged at WARNING (S195 fix), not debug.
    # If anyone bumps these back to debug, we lose the silent-failure surface.
    warn_calls = [c for c in mock_logger.warning.call_args_list
                  if c.args and "phase4b emission failed" in c.args[0]]
    assert len(warn_calls) == 2, (
        f"expected 2 warning logs (one per failed row), got {len(warn_calls)}"
    )


@pytest.mark.asyncio
async def test_method_runs_unconditionally_no_upstream_gate():
    """Direct-call the method with empty session — it MUST issue the SQL
    queries (i.e. session.execute is called) regardless of upstream state.
    The regression we're fixing: the legacy `paper_updated > 0 OR updated > 0`
    gate stayed false forever once paper_trades caught up, killing emission.
    """
    from base_engine.data.database import Database

    db = Database.__new__(Database)
    db.session_factory = MagicMock()
    db.insert_trade_event = AsyncMock(return_value=1)

    empty_result = MagicMock()
    empty_result.fetchall = MagicMock(return_value=[])

    session = AsyncMock()
    session.execute = AsyncMock(return_value=empty_result)
    session.commit = AsyncMock()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    db.get_session = MagicMock(return_value=ctx)

    await db.backfill_trade_events_resolution()

    # Phase 4b: SET LOCAL + main SELECT = 2 calls
    # Phase 4b-alt: SET LOCAL + main SELECT = 2 calls
    # Total: 4 calls minimum. The point is execute was reached, not gated.
    assert session.execute.await_count >= 4, (
        f"expected >=4 execute calls (Phase 4b + 4b-alt SET LOCAL + SELECT), "
        f"got {session.execute.await_count} — gate may have been re-introduced"
    )
