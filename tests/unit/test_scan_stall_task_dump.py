"""WI-21b: stall-watchdog asyncio task-stack dump.

py-spy thread dumps cannot see asyncio-suspended coroutines (2026-06-10:
post-3a00032 wedges show the loop ALIVE in asyncio runners while the scan
task hangs in an await that never resolves). `_log_stalled_task_stacks()`
runs in the watchdogs' force-exit path and logs each task's await chain
(via a manual cr_await walk — Task.get_stack() only returns the outermost
frame for suspended coroutines) so the journal names the exact hung await.

Uses structlog.testing.capture_logs so assertions are renderer-agnostic
(independent of whatever logging config other tests installed).
"""
import asyncio

import pytest
import structlog

import bots.esports_bot as _eb_mod
from bots.esports_bot import _log_stalled_task_stacks


@pytest.fixture()
def fresh_logger(monkeypatch):
    """Un-freeze the module logger for capture_logs.

    configure_logging() (called by earlier tests / conftest in full-suite runs)
    uses cache_logger_on_first_use=True; once the module-level proxy in
    bots.esports_bot has been used, its processor pipeline is FROZEN and
    capture_logs() (which swaps config processors) sees nothing from it.
    Swap in an unused proxy so first use happens inside the capture context.
    """
    monkeypatch.setattr(_eb_mod, "logger", structlog.get_logger())


@pytest.mark.asyncio
async def test_dump_names_full_await_chain(fresh_logger):
    async def _inner_hang(evt):
        await evt.wait()          # the hung await (terminal: Event waiter Future)

    async def _outer_wrapper(evt):
        await _inner_hang(evt)    # await-chain level above it

    evt = asyncio.Event()
    task = asyncio.create_task(_outer_wrapper(evt), name="wedge-probe")
    await asyncio.sleep(0)        # let it reach the suspended await

    with structlog.testing.capture_logs() as logs:
        _log_stalled_task_stacks()

    task.cancel()
    evt.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    events = [l["event"] for l in logs]
    assert "scan_stall_task_dump_begin" in events

    probe = [l for l in logs if l.get("task") == "wedge-probe"]
    assert probe, f"wedge-probe task not in dump; got tasks: {[l.get('task') for l in logs]}"
    chain = probe[0]["awaiting"]
    # full await chain, innermost-first: Event-wait terminal <- _inner_hang <- _outer_wrapper
    assert "_inner_hang" in chain and "_outer_wrapper" in chain, chain
    assert chain.index("_inner_hang") < chain.index("_outer_wrapper"), (
        f"innermost frame must lead the chain: {chain}"
    )


@pytest.mark.asyncio
async def test_dump_never_raises_and_bounds_output(fresh_logger):
    evt = asyncio.Event()

    async def _sleeper(evt):
        await evt.wait()

    tasks = [
        asyncio.create_task(_sleeper(evt), name=f"bulk-{i}") for i in range(8)
    ]
    await asyncio.sleep(0)

    with structlog.testing.capture_logs() as logs:
        _log_stalled_task_stacks(max_tasks=3)   # below live count → truncation

    evt.set()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    events = [l["event"] for l in logs]
    assert "scan_stall_task_dump_begin" in events
    assert "scan_stall_task_dump_truncated" in events
    assert sum(1 for e in events if e == "scan_stall_task_stack") == 3
