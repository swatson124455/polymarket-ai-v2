"""S195 Day 2: regression tests for the EsportsBotV2 async warmup gate.

The gate exists so a 5+ minute cold fit no longer pushes total init past
the BaseEngine 120s startup-hold. The bot's scan loop ticks during warmup,
but scan_and_trade refuses to predict until _warmup_complete() returns True.
On warmup failure, _warmup_complete() re-raises so the scan loop surfaces
the fault (fail-loud), not silently scans against an unfit model.

Pinned behaviours:
  - Warmup task absent / not done → scan_and_trade returns early
  - 110s simulated warmup → scan does not predict during the wait window
  - Warmup successful → gate opens, scan proceeds (no early-return signal)
  - Warmup raised → _warmup_complete() raises; scan loop sees the failure
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from bots.esports_bot_v2 import EsportsBotV2


def _make_bot() -> EsportsBotV2:
    """Construct an EsportsBotV2 with a minimal mocked base_engine.

    Avoids running _initialize / _heavy_warmup so tests can drive the
    warmup task by hand.
    """
    base_engine = MagicMock()
    base_engine.db = None
    base_engine.client = None
    return EsportsBotV2(base_engine=base_engine)


@pytest.mark.asyncio
async def test_scan_and_trade_returns_early_when_warmup_task_absent() -> None:
    bot = _make_bot()
    assert bot._warmup_task is None
    bot._resolve_finished_matches = AsyncMock()
    bot._predict_upcoming_matches = AsyncMock()
    bot._execute_trades = AsyncMock()

    await bot.scan_and_trade()

    bot._resolve_finished_matches.assert_not_called()
    bot._predict_upcoming_matches.assert_not_called()
    bot._execute_trades.assert_not_called()


@pytest.mark.asyncio
async def test_scan_and_trade_returns_early_during_long_warmup() -> None:
    """The 110s-warmup contract from the S195 plan: the bot must not predict
    while the warmup task is still running, regardless of its duration.
    Simulated with a controllable Future to keep the test fast.
    """
    bot = _make_bot()
    pending = asyncio.get_event_loop().create_future()

    async def _slow_warmup() -> None:
        await pending  # never completes inside this test

    bot._warmup_task = asyncio.create_task(_slow_warmup())
    try:
        bot._resolve_finished_matches = AsyncMock()
        bot._predict_upcoming_matches = AsyncMock()
        bot._execute_trades = AsyncMock()

        # Three scan ticks across the warmup window — none should predict.
        for _ in range(3):
            await bot.scan_and_trade()

        bot._predict_upcoming_matches.assert_not_called()
        bot._resolve_finished_matches.assert_not_called()
        assert bot._initialized is False
    finally:
        # Tidy: unblock and await the task so pytest doesn't warn.
        pending.set_result(None)
        await bot._warmup_task


@pytest.mark.asyncio
async def test_scan_and_trade_proceeds_after_warmup_success() -> None:
    bot = _make_bot()

    async def _fast_warmup() -> None:
        bot._initialized = True

    bot._warmup_task = asyncio.create_task(_fast_warmup())
    await bot._warmup_task

    bot._resolve_finished_matches = AsyncMock()
    bot._predict_upcoming_matches = AsyncMock()
    bot._execute_trades = AsyncMock()

    await bot.scan_and_trade()

    bot._resolve_finished_matches.assert_called_once()
    bot._predict_upcoming_matches.assert_called_once()
    # Default ESPORTS_V2_DRY_RUN=false → execute_trades runs


@pytest.mark.asyncio
async def test_warmup_complete_reraises_on_failure() -> None:
    """Fail-loud contract: a failed warmup must surface to the scan loop
    so the bot doesn't silently scan against an unfit model.
    """
    bot = _make_bot()

    class _WarmupFailed(RuntimeError):
        pass

    async def _failing_warmup() -> None:
        raise _WarmupFailed("simulated DB rebuild failure")

    bot._warmup_task = asyncio.create_task(_failing_warmup())
    # Drain the task so .exception() is populated and the loop doesn't warn.
    try:
        await bot._warmup_task
    except _WarmupFailed:
        pass

    with pytest.raises(_WarmupFailed):
        bot._warmup_complete()


@pytest.mark.asyncio
async def test_warmup_complete_handles_cancelled_task() -> None:
    """Cancellation is not fail-loud — warmup may be cancelled at shutdown."""
    bot = _make_bot()

    async def _slow_warmup() -> None:
        await asyncio.sleep(60)

    bot._warmup_task = asyncio.create_task(_slow_warmup())
    bot._warmup_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await bot._warmup_task

    assert bot._warmup_complete() is False


@pytest.mark.asyncio
async def test_initialized_flag_is_authoritative_after_warmup() -> None:
    """If _initialized is set externally (back-compat _initialize() path),
    _warmup_complete() short-circuits to True without inspecting the task.
    """
    bot = _make_bot()
    bot._initialized = True
    assert bot._warmup_complete() is True
