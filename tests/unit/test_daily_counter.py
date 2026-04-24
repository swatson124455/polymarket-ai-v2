"""Contract tests for base_engine.data.daily_counter.

S194: Pins the GREATEST(0, ...) clamp on counter writes so net-counter callers
(WeatherBot _city_exposure, _group_exposure) cannot land negative values when
exit-time decrements fire on fresh-zero counters. Live evidence at S194 close
showed 15+ days of negative rows on prod (peak 119 negative rows 2026-04-11)
prior to this clamp landing.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


def _make_mock_db():
    """Build a mock db whose get_session() returns an async context manager."""
    db = MagicMock()
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    db.get_session = MagicMock(return_value=mock_cm)
    return db, mock_session


class TestIncrementCounterClamp:
    """increment_counter() must produce SQL that clamps counter_value at 0."""

    @pytest.mark.asyncio
    async def test_sql_contains_greatest_clamp_on_update(self):
        """ON CONFLICT UPDATE branch must use GREATEST(0, ...) so decrements floor at 0."""
        from base_engine.data.daily_counter import increment_counter

        db, mock_session = _make_mock_db()
        await increment_counter(db, bot_id="WeatherBot", name="city_NewYork", amount=-50.0)

        assert mock_session.execute.await_count == 1
        sql_text = str(mock_session.execute.await_args.args[0].text)
        assert "GREATEST(0, daily_counters.counter_value + :amount)" in sql_text, \
            "ON CONFLICT UPDATE must clamp at 0 to prevent negative net-counter drift"

    @pytest.mark.asyncio
    async def test_sql_contains_greatest_clamp_on_insert(self):
        """INSERT (first-time) branch must also clamp the initial value at 0.

        Defensive: if a caller fires a decrement on a brand-new counter that
        doesn't exist yet, the INSERT path runs and must not seed a negative.
        """
        from base_engine.data.daily_counter import increment_counter

        db, mock_session = _make_mock_db()
        await increment_counter(db, bot_id="WeatherBot", name="city_NewYork", amount=-50.0)

        sql_text = str(mock_session.execute.await_args.args[0].text)
        assert "VALUES (:bot_id, CURRENT_DATE, :name, GREATEST(0, :amount))" in sql_text, \
            "INSERT branch must clamp seed value at 0"

    @pytest.mark.asyncio
    async def test_amount_parameter_passed_unchanged(self):
        """SQL receives the raw amount; the GREATEST clamp lives in the SQL itself.

        Pinning this guards against a future "fix" that pre-clamps in Python
        and drops the SQL clamp — leaving the on-disk write unprotected.
        """
        from base_engine.data.daily_counter import increment_counter

        db, mock_session = _make_mock_db()
        await increment_counter(db, bot_id="EsportsBot", name="game_cs2", amount=-123.45)

        params = mock_session.execute.await_args.args[1]
        assert params == {"bot_id": "EsportsBot", "name": "game_cs2", "amount": -123.45}, \
            "Caller's raw amount must reach SQL untouched; clamp is SQL-side only"

    @pytest.mark.asyncio
    async def test_commit_awaited(self):
        """Write-through guarantee requires await — no fire-and-forget tasks.

        Per module docstring: 'Must be called with await — do not use
        asyncio.create_task'. This test pins commit() being awaited inside
        the call rather than scheduled.
        """
        from base_engine.data.daily_counter import increment_counter

        db, mock_session = _make_mock_db()
        await increment_counter(db, bot_id="MirrorBot", name="some_counter", amount=10.0)

        assert mock_session.commit.await_count == 1, \
            "commit() must be awaited inline; fire-and-forget breaks write-through"

    @pytest.mark.asyncio
    async def test_positive_amount_unaffected(self):
        """Additive callers (EsportsBot _game_exposure) see no behavior change.

        GREATEST(0, x) where x is always positive == x. The clamp only changes
        behavior for the net-counter case (negative amount).
        """
        from base_engine.data.daily_counter import increment_counter

        db, mock_session = _make_mock_db()
        await increment_counter(db, bot_id="EsportsBot", name="game_lol", amount=99.99)

        # SQL is identical regardless of sign — that's the point. The clamp is
        # idempotent for positive values.
        sql_text = str(mock_session.execute.await_args.args[0].text)
        assert "GREATEST(0," in sql_text  # present
        params = mock_session.execute.await_args.args[1]
        assert params["amount"] == 99.99
