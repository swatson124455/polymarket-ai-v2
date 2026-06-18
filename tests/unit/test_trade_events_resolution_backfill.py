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

    # Each row = 11 columns matching Phase 4b's SELECT shape:
    # market_id, bot_name, side, computed_pnl, resolved_at, remaining_size,
    # avg_entry_price, exit_pnl_already, market_resolution, entry_game,
    # has_live_execution (row[10]: 1 if any ENTRY in the group was live)
    fake_rows = [
        ("0xmkt1", "WeatherBot", "YES", -15.23, _dt.datetime(2026, 4, 25, 12, 0),
         100.0, 0.50, 0.0, "NO", None, 0),
        ("0xmkt2", "MirrorBot", "YES", 5.77, _dt.datetime(2026, 4, 25, 12, 1),
         50.0, 0.45, 0.0, "YES", None, 1),
        ("0xmkt3", "EsportsBot", "NO", -2.10, _dt.datetime(2026, 4, 25, 12, 2),
         25.0, 0.60, 0.0, "YES", "cs2", 0),
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

    # Phase-1 live-P&L: RESOLUTION execution_mode is derived from has_live_execution
    # (row[10]) — 'live' only when an ENTRY in the group was live, else 'paper'.
    # Mode follows the entry data, never current SIMULATION_MODE.
    modes = [c.kwargs.get("execution_mode") for c in db.insert_trade_event.call_args_list]
    assert modes == ["paper", "live", "paper"], (
        f"RESOLUTION execution_mode must derive from has_live_execution; got {modes}"
    )


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

    # 11 columns (row[10] = has_live_execution); value is irrelevant here
    # because every insert raises before tagging matters.
    fake_rows = [
        ("0xmkt1", "WeatherBot", "YES", -15.23, _dt.datetime(2026, 4, 25, 12, 0),
         100.0, 0.50, 0.0, "NO", None, 0),
        ("0xmkt2", "MirrorBot", "YES", 5.77, _dt.datetime(2026, 4, 25, 12, 1),
         50.0, 0.45, 0.0, "YES", None, 0),
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


# ── S196 forward-audit: Phase 4b-alt size correctness ────────────────────────
# Pre-fix Phase 4b-alt emitted RESOLUTION with raw `positions.size`, blindly
# trusting a value that can diverge from trade_events ENTRY truth (Bug A on WB:
# positions.size=437.27 vs ENTRY total=158.65). This produced SIZE_INVARIANT
# violations whose data the audit correctly flagged. The fix clamps the emitted
# size to `max(0, min(p.size, total_entry) − total_exit)`, mirroring Phase 4b's
# correctness pattern. The legacy JOIN-mismatch use case (no trade_events ENTRY
# for the market+bot) is preserved via COALESCE → falls back to positions.size.

def test_phase4b_alt_query_joins_trade_events_entry_and_exit_aggregates():
    """Phase 4b-alt's outer query MUST LEFT JOIN aggregated trade_events
    ENTRY and EXIT sums. Without these joins, the size emitted into RESOLUTION
    is whatever positions.size happens to hold — which the WB Bug A case proved
    is unreliable.
    """
    import inspect
    from base_engine.data.database import Database

    source = inspect.getsource(Database.backfill_trade_events_resolution)
    assert "te_entry_agg" in source, (
        "Phase 4b-alt query must LEFT JOIN aggregated trade_events ENTRY sums "
        "(te_entry_agg) so size can be capped by ENTRY truth"
    )
    assert "te_exit_agg" in source, (
        "Phase 4b-alt query must LEFT JOIN aggregated trade_events EXIT sums "
        "(te_exit_agg) so size can subtract prior disposals"
    )


def test_phase4b_alt_query_clamps_effective_size_in_where():
    """The Phase 4b-alt query MUST filter out rows whose effective_size <= 0
    using the GREATEST/LEAST clamp at the SQL layer. This avoids emitting
    RESOLUTION for already-fully-disposed positions.
    """
    import inspect
    from base_engine.data.database import Database

    source = inspect.getsource(Database.backfill_trade_events_resolution)
    assert "GREATEST(0.0, LEAST(p.size," in source, (
        "Phase 4b-alt WHERE clause must clamp via "
        "GREATEST(0.0, LEAST(p.size, total_entry) - total_exit) > 0"
    )


def test_phase4b_alt_emission_uses_effective_size_not_raw_positions_size():
    """The Python emit MUST pass `_effective_size` to insert_trade_event,
    not raw `_size` (positions.size). Regression guard: the bug was passing
    `size=float(_size)` directly, which carried positions.size inflation
    straight into trade_events.RESOLUTION rows.
    """
    import inspect
    from base_engine.data.database import Database

    source = inspect.getsource(Database.backfill_trade_events_resolution)

    # Locate the Phase 4b-alt block by its marker comment.
    alt_marker = source.find("Phase 4b-alt: positions-driven RESOLUTION emission")
    assert alt_marker != -1, "Phase 4b-alt section marker not found"
    alt_section = source[alt_marker:]

    # The emit call must use _effective_size for size=
    # NOT `size=float(_size)` (the pre-fix shape).
    assert "size=_effective_size" in alt_section, (
        "Phase 4b-alt emit must use _effective_size for size= parameter "
        "(was using raw positions.size — caused SIZE_INVARIANT violations)"
    )
    assert "size=float(_size)" not in alt_section, (
        "Phase 4b-alt emit must NOT use raw float(_size) — that's the "
        "pre-fix shape that allowed positions.size inflation through"
    )


@pytest.mark.asyncio
async def test_phase4b_alt_clamps_size_when_positions_size_exceeds_entry_truth():
    """Behavioral guard: when positions.size = 437.27 but trade_events
    ENTRY total = 158.65 and EXIT total = 40.31, the emit MUST receive
    size = 158.65 − 40.31 = 118.34 (the actual remaining disposal).
    This is the exact WB Bug A shape from prod data 2026-04-26.
    """
    from base_engine.data.database import Database
    import datetime as _dt

    db = Database.__new__(Database)
    db.session_factory = MagicMock()

    # Phase 4b returns nothing.
    empty_result = MagicMock()
    empty_result.fetchall = MagicMock(return_value=[])

    session_4b = AsyncMock()
    session_4b.execute = AsyncMock(side_effect=[
        MagicMock(),    # SET LOCAL
        empty_result,   # Phase 4b main SELECT (0 rows)
    ])
    session_4b.commit = AsyncMock()
    ctx_4b = AsyncMock()
    ctx_4b.__aenter__ = AsyncMock(return_value=session_4b)
    ctx_4b.__aexit__ = AsyncMock(return_value=False)

    # Phase 4b-alt returns ONE row matching the WB Bug A shape.
    # New SELECT order: market_id, source_bot, side, size (positions),
    #   entry_price, resolution, resolved_at, exit_pnl_already, entry_game,
    #   te_total_entry, te_total_exit, is_paper
    alt_row = (
        "0xWBBugA",          # market_id
        "WeatherBot",        # source_bot
        "NO",                # side (positions)
        437.27,              # p.size — INFLATED
        0.6700,              # entry_price
        "NO",                # resolution
        _dt.datetime(2026, 4, 13, 12, 0),  # resolved_at
        -0.65,               # exit_pnl_already
        None,                # entry_game
        158.65,              # te_total_entry — trade_events truth
        40.31,               # te_total_exit
        True,                # is_paper — historical paper position
    )
    alt_result = MagicMock()
    alt_result.fetchall = MagicMock(return_value=[alt_row])

    session_alt = AsyncMock()
    session_alt.execute = AsyncMock(side_effect=[
        MagicMock(),    # SET LOCAL
        alt_result,     # Phase 4b-alt main SELECT (1 row)
        MagicMock(),    # UPDATE positions SET status='closed', size=0 inside begin_nested
    ])
    session_alt.commit = AsyncMock()
    # begin_nested returns an async context manager.
    nested_ctx = AsyncMock()
    nested_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    nested_ctx.__aexit__ = AsyncMock(return_value=False)
    session_alt.begin_nested = MagicMock(return_value=nested_ctx)
    ctx_alt = AsyncMock()
    ctx_alt.__aenter__ = AsyncMock(return_value=session_alt)
    ctx_alt.__aexit__ = AsyncMock(return_value=False)

    db.get_session = MagicMock(side_effect=[ctx_4b, ctx_alt])
    db.insert_trade_event = AsyncMock(return_value=42)

    await db.backfill_trade_events_resolution()

    # Exactly one RESOLUTION emit, with size clamped.
    assert db.insert_trade_event.await_count == 1, (
        f"expected 1 emit, got {db.insert_trade_event.await_count}"
    )
    emit_kwargs = db.insert_trade_event.call_args.kwargs
    assert emit_kwargs["event_type"] == "RESOLUTION"
    expected_size = min(437.27, 158.65) - 40.31  # = 118.34
    assert abs(emit_kwargs["size"] - expected_size) < 1e-6, (
        f"expected size={expected_size:.4f} (min(p.size, te_entry) - te_exit), "
        f"got {emit_kwargs['size']}"
    )
    # Phase-1: is_paper=True historical position → RESOLUTION tagged 'paper'.
    assert emit_kwargs["execution_mode"] == "paper", (
        f"is_paper=True must tag paper; got {emit_kwargs.get('execution_mode')}"
    )


@pytest.mark.asyncio
async def test_phase4b_alt_falls_back_to_positions_size_when_no_trade_events_entry():
    """Backward-compat guard for Phase 4b-alt's original use case: when
    trade_events has NO ENTRY for the (market, bot) — the JOIN-mismatch
    scenario from S109 — the SQL COALESCE falls back to positions.size,
    so effective_size = positions.size − 0 = positions.size. This row's
    `te_total_entry` reflects that COALESCE behaviour.
    """
    from base_engine.data.database import Database
    import datetime as _dt

    db = Database.__new__(Database)
    db.session_factory = MagicMock()

    empty_result = MagicMock()
    empty_result.fetchall = MagicMock(return_value=[])
    session_4b = AsyncMock()
    session_4b.execute = AsyncMock(side_effect=[MagicMock(), empty_result])
    session_4b.commit = AsyncMock()
    ctx_4b = AsyncMock()
    ctx_4b.__aenter__ = AsyncMock(return_value=session_4b)
    ctx_4b.__aexit__ = AsyncMock(return_value=False)

    # SQL COALESCE produces te_total_entry = positions.size when no ENTRY exists.
    alt_row = (
        "0xJoinMismatch",
        "MirrorBot",
        "YES",
        50.0,                # p.size
        0.55,
        "YES",
        _dt.datetime(2026, 4, 25, 12, 0),
        0.0,
        None,
        50.0,                # te_total_entry — COALESCE fallback to p.size
        0.0,                 # te_total_exit
        False,               # is_paper — live MirrorBot position
    )
    alt_result = MagicMock()
    alt_result.fetchall = MagicMock(return_value=[alt_row])

    session_alt = AsyncMock()
    session_alt.execute = AsyncMock(side_effect=[
        MagicMock(), alt_result, MagicMock(),
    ])
    session_alt.commit = AsyncMock()
    nested_ctx = AsyncMock()
    nested_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
    nested_ctx.__aexit__ = AsyncMock(return_value=False)
    session_alt.begin_nested = MagicMock(return_value=nested_ctx)
    ctx_alt = AsyncMock()
    ctx_alt.__aenter__ = AsyncMock(return_value=session_alt)
    ctx_alt.__aexit__ = AsyncMock(return_value=False)

    db.get_session = MagicMock(side_effect=[ctx_4b, ctx_alt])
    db.insert_trade_event = AsyncMock(return_value=42)

    await db.backfill_trade_events_resolution()

    assert db.insert_trade_event.await_count == 1
    emit_kwargs = db.insert_trade_event.call_args.kwargs
    # No ENTRY events → COALESCE → te_total_entry = p.size.
    # No EXIT events → te_total_exit = 0. Effective size = p.size = 50.0.
    assert abs(emit_kwargs["size"] - 50.0) < 1e-6, (
        f"backward-compat: legacy JOIN-mismatch case must emit p.size when "
        f"no trade_events ENTRY exists. Got {emit_kwargs['size']}, expected 50.0"
    )
    # Phase-1: is_paper=False (live) → RESOLUTION tagged 'live', even via the
    # legacy COALESCE-fallback path.
    assert emit_kwargs["execution_mode"] == "live", (
        f"is_paper=False must tag live; got {emit_kwargs.get('execution_mode')}"
    )


# ── ROOT CAUSE regression guard: phase4b candidate ordering ──────────────────

def test_phase4b_select_has_order_by_resolved_at():
    """Phase 4b SELECT must ORDER BY pt_pnl.resolved_at ASC before LIMIT 500.

    Without ORDER BY, PostgreSQL picks rows in heap order which is
    nondeterministic. With >500 candidates (observed: 722 across all bots),
    the same ~222 rows can be perpetually skipped — a starvation bug.
    Concrete case: market 0x7abae048de.. (EsportsBot NO, remaining=590.27)
    sat at heap-order position past the 500-row cutoff and never emitted
    RESOLUTION for 24h+ post-cleanup, while 8 of 10 sibling markets re-emitted
    normally within minutes.

    Regression guard: if anyone removes the ORDER BY or changes the ordering
    column away from a time-monotone field, this test fails.
    """
    import inspect
    import re

    from base_engine.data.database import Database

    source = inspect.getsource(Database.backfill_trade_events_resolution)

    # The phase4b SELECT and the phase4b-alt SELECT both end in `LIMIT 500`.
    # Phase 4b is the first one in source order. The ORDER BY must immediately
    # precede its LIMIT 500.
    # Collapse string-concat artifacts: join `"..."  "..."` chunks into one
    # logical line so the regex can match across Python concat boundaries.
    collapsed = re.sub(r'"\s*\n\s*"', "", source)

    # First LIMIT 500 = phase4b's outer LIMIT.
    first_limit_idx = collapsed.find("LIMIT 500")
    assert first_limit_idx != -1, "phase4b SELECT must still have LIMIT 500"

    # Look backward up to ~400 chars for ORDER BY on pt_pnl.resolved_at.
    window = collapsed[max(0, first_limit_idx - 400):first_limit_idx]
    assert re.search(r"ORDER BY\s+pt_pnl\.resolved_at\s+ASC", window), (
        "phase4b SELECT must have `ORDER BY pt_pnl.resolved_at ASC` "
        "immediately before its `LIMIT 500`. Without it, PostgreSQL picks "
        "rows in nondeterministic heap order; with >500 candidates this "
        "perpetually starves the same ~222 rows. Confirmed starvation in "
        "production 2026-05-27 for EB market 0x7abae048de.. (24h+ no emit).\n\n"
        f"Window before first LIMIT 500:\n{window}"
    )


# ── Phase-1 live-P&L: execution_mode derives from HISTORICAL mode ─────────────
# The regime-transition guard — the failure mode that only manifests across a
# paper→live flip. A position's RESOLUTION must carry the execution_mode it was
# ENTERED under, never the bot's CURRENT SIMULATION_MODE. Phase 4b-alt derives
# from positions.is_paper (set at entry). These tests flip SIMULATION_MODE to the
# OPPOSITE of the historical entry and assert the historical mode still wins — if
# a future change wires current mode into the derivation, they fail immediately.

def _build_alt_only_db(alt_row):
    """Database stub: Phase 4b returns 0 rows; Phase 4b-alt returns [alt_row]."""
    from base_engine.data.database import Database

    db = Database.__new__(Database)
    db.session_factory = MagicMock()

    empty = MagicMock()
    empty.fetchall = MagicMock(return_value=[])
    s4b = AsyncMock()
    s4b.execute = AsyncMock(side_effect=[MagicMock(), empty])
    s4b.commit = AsyncMock()
    c4b = AsyncMock()
    c4b.__aenter__ = AsyncMock(return_value=s4b)
    c4b.__aexit__ = AsyncMock(return_value=False)

    altres = MagicMock()
    altres.fetchall = MagicMock(return_value=[alt_row])
    salt = AsyncMock()
    salt.execute = AsyncMock(side_effect=[MagicMock(), altres, MagicMock()])
    salt.commit = AsyncMock()
    nested = AsyncMock()
    nested.__aenter__ = AsyncMock(return_value=MagicMock())
    nested.__aexit__ = AsyncMock(return_value=False)
    salt.begin_nested = MagicMock(return_value=nested)
    calt = AsyncMock()
    calt.__aenter__ = AsyncMock(return_value=salt)
    calt.__aexit__ = AsyncMock(return_value=False)

    db.get_session = MagicMock(side_effect=[c4b, calt])
    db.insert_trade_event = AsyncMock(return_value=42)
    return db


def _regime_alt_row(is_paper):
    """A resolved (won) MirrorBot position row in Phase 4b-alt's 12-col shape."""
    import datetime as _dt
    return (
        "0xRegimeFlip", "MirrorBot", "YES",
        20.0,                              # p.size
        0.40,                             # entry_price
        "YES",                            # resolution (held side won)
        _dt.datetime(2026, 5, 30, 12, 0),  # resolved_at
        0.0,                              # exit_pnl_already
        None,                             # entry_game
        20.0,                             # te_total_entry
        0.0,                              # te_total_exit
        is_paper,                         # is_paper (historical, set at entry)
    )


@pytest.mark.asyncio
async def test_phase4b_alt_live_entry_resolving_in_paper_mode_stays_live(monkeypatch):
    """is_paper=False (entered live) resolving while SIMULATION_MODE=True (paper
    mode NOW) MUST tag 'live'. The critical Q1 case: if the derivation ever reads
    current mode instead of the historical flag, this asserts it back."""
    monkeypatch.setattr("config.settings.SIMULATION_MODE", True, raising=False)
    db = _build_alt_only_db(_regime_alt_row(is_paper=False))
    await db.backfill_trade_events_resolution()
    assert db.insert_trade_event.await_count == 1
    assert db.insert_trade_event.call_args.kwargs["execution_mode"] == "live", (
        "live-entered position must stay 'live' even when current mode is paper"
    )


@pytest.mark.asyncio
async def test_phase4b_alt_paper_entry_resolving_in_live_mode_stays_paper(monkeypatch):
    """Inverse: is_paper=True (entered paper) resolving while SIMULATION_MODE=False
    (live mode NOW) MUST stay 'paper' — never pollute the live ledger with a
    paper-entered outcome."""
    monkeypatch.setattr("config.settings.SIMULATION_MODE", False, raising=False)
    db = _build_alt_only_db(_regime_alt_row(is_paper=True))
    await db.backfill_trade_events_resolution()
    assert db.insert_trade_event.await_count == 1
    assert db.insert_trade_event.call_args.kwargs["execution_mode"] == "paper", (
        "paper-entered position must stay 'paper' even when current mode is live"
    )


# ── S245 #3 (option B): stop the cross-bot RESOLUTION over-size storm ─────────
# Phase 4b-alt re-processed no-ENTRY phantoms (ENTRY_total=0 → insert_trade_event
# over-size-rejects them, so the NOT-EXISTS-RESOLUTION filter never excludes them)
# every backfill cycle forever — WB ~19k + EB ~13k + MB ~4k `RESOLUTION over-size
# rejected` over ~21h. Two source-level fixes: (1) exclude non-YES/NO rows (root-#5
# SELL corruption can't emit a valid RESOLUTION), (2) zero size on the 'closed'
# rows it processes (not just 'open') so phantoms drop from the candidate set.

def _backfill_src():
    import inspect
    from base_engine.data.database import Database
    return inspect.getsource(Database.backfill_trade_events_resolution)


def test_phase4b_alt_excludes_non_yes_no_rows():
    assert "p.side IN ('YES', 'NO')" in _backfill_src(), (
        "Phase 4b-alt must filter p.side IN ('YES','NO') — without it, root-#5 "
        "SELL rows are emitted as a corrupt side='SELL' RESOLUTION, or "
        "over-size-rejected every backfill cycle forever."
    )


def test_phase4b_alt_zeroing_covers_closed_rows():
    src = _backfill_src()
    assert "AND status IN ('open', 'closed')" in src, (
        "Phase 4b-alt's post-emit size-zeroing UPDATE must cover "
        "status IN ('open','closed')."
    )
    # The old open-only zeroing form is what left closed phantoms re-hammered.
    assert "AND source_bot = :bot AND status = 'open'" not in src, (
        "Old open-only zeroing UPDATE is back — closed no-ENTRY phantoms will "
        "re-hammer the backfill over-size guard again."
    )


def test_phase4b_resolution_dedup_is_side_agnostic():
    """S247 fix (a): the Phase-4b candidate SELECT's RESOLUTION dedup NOT-EXISTS
    must be side-agnostic — keyed on (bot, market) only, NOT on side. This matches
    insert_trade_event's side-agnostic RESOLUTION dedup (one per (bot, market),
    S167) and the side-agnostic S196 over-size guard.

    The old per-side `AND te.side = e.side` clause re-selected the other/losing
    side of a both-sides-or-resolved market every backfill cycle; the insert
    always refused it (no write), so it was a perpetual no-op re-hammer (~128
    futile candidates + ~28 `RESOLUTION over-size rejected` log lines/cycle,
    fleet-wide, WeatherBot-dominated). Aligning the SELECT to the insert writes
    nothing new — it only stops re-selecting rows the insert already rejects.
    """
    src = _backfill_src()
    assert "te.side = e.side" not in src, (
        "Phase-4b RESOLUTION dedup must NOT key on side (`te.side = e.side`). "
        "Per-side dedup against a side-agnostic insert re-hammers the other side "
        "of resolved markets every cycle (the Phase-4b over-size-reject storm)."
    )
    # The side-agnostic RESOLUTION dedup keys must still be present (market + bot
    # + event_type='RESOLUTION'), so a row is excluded once ANY RESOLUTION exists
    # for its (bot, market) — exactly what the insert enforces.
    for needle in ("te.market_id = e.market_id", "te.bot_name = e.bot_name",
                   "te.event_type = 'RESOLUTION'"):
        assert needle in src, (
            f"Phase-4b RESOLUTION dedup NOT-EXISTS must still contain `{needle}` "
            "(side-agnostic exclude-if-any-RESOLUTION-exists)."
        )
