"""WI-21b: stall-watchdog asyncio task-stack dump (v2: dedup-proof + histogram).

py-spy thread dumps cannot see asyncio-suspended coroutines, so the stall
watchdogs log task await-chains before force-exit. v2 lessons from the first
field deployment (2026-06-10): (a) the _DedupProcessor keys on (event, level)
and silently dropped 99/100 identical-event detail lines -> every detail line
now carries a unique event suffix; (b) with a runaway task leak
(task_count ~2500 at every wedge) a per-coro histogram leads the dump — the
dominant coro name is the leak fingerprint; detail = one sample chain per
DISTINCT coro qualname.

Uses structlog.testing.capture_logs (renderer-agnostic) + a fresh-logger
fixture (cache_logger_on_first_use freezes the module proxy in full-suite
runs, blinding capture_logs otherwise).
"""
import asyncio

import pytest
import structlog

import bots.esports_bot as _eb_mod
from bots.esports_bot import _log_stalled_task_stacks


@pytest.fixture()
def fresh_logger(monkeypatch):
    monkeypatch.setattr(_eb_mod, "logger", structlog.get_logger())


@pytest.mark.asyncio
async def test_dump_names_full_await_chain(fresh_logger):
    async def _inner_hang(evt):
        await evt.wait()          # the hung await

    async def _outer_wrapper(evt):
        await _inner_hang(evt)    # await-chain level above it

    evt = asyncio.Event()
    task = asyncio.create_task(_outer_wrapper(evt), name="wedge-probe")
    await asyncio.sleep(0)

    with structlog.testing.capture_logs() as logs:
        _log_stalled_task_stacks()

    task.cancel()
    evt.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    begin = [l for l in logs if l["event"] == "scan_stall_task_dump_begin"]
    assert begin and begin[0]["task_count"] >= 1
    assert "_outer_wrapper" in begin[0]["top_coros"]   # histogram fingerprint

    probe = [l for l in logs if l.get("task") == "wedge-probe"]
    assert probe, f"wedge-probe not in detail; tasks: {[l.get('task') for l in logs]}"
    assert probe[0]["event"].startswith("scan_stall_task_stack_")  # dedup-proof
    chain = probe[0]["awaiting"]
    assert "_inner_hang" in chain and "_outer_wrapper" in chain, chain
    assert chain.index("_inner_hang") < chain.index("_outer_wrapper"), (
        f"innermost frame must lead the chain: {chain}"
    )


@pytest.mark.asyncio
async def test_detail_events_unique_and_one_per_distinct_coro(fresh_logger):
    """Dedup-immunity: N same-coro tasks -> ONE detail line; all detail event
    names distinct (the dedup processor can never collapse a dump again)."""
    evt = asyncio.Event()

    async def _sleeper(evt):
        await evt.wait()

    tasks = [
        asyncio.create_task(_sleeper(evt), name=f"bulk-{i}") for i in range(8)
    ]
    await asyncio.sleep(0)

    with structlog.testing.capture_logs() as logs:
        _log_stalled_task_stacks()

    evt.set()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    details = [l for l in logs if l["event"].startswith("scan_stall_task_stack_")]
    events = [l["event"] for l in details]
    assert len(events) == len(set(events)), f"duplicate detail event names: {events}"
    sleeper = [l for l in details if "_sleeper" in l.get("coro", "")]
    assert len(sleeper) == 1, "expected ONE sample line per distinct coro"
    assert sleeper[0]["count"] == 8, "histogram count must report all 8 instances"


@pytest.mark.asyncio
async def test_dump_truncates_by_distinct_coros_without_raising(fresh_logger):
    evt = asyncio.Event()
    holders = []

    # 5 DISTINCT coroutine functions -> 5 distinct qualnames
    for i in range(5):
        async def _distinct(evt=evt):
            await evt.wait()
        _distinct.__qualname__ = f"_distinct_{i}"
        holders.append(asyncio.create_task(_distinct(), name=f"d-{i}"))
    await asyncio.sleep(0)

    with structlog.testing.capture_logs() as logs:
        _log_stalled_task_stacks(max_tasks=2)

    evt.set()
    for t in holders:
        t.cancel()
    await asyncio.gather(*holders, return_exceptions=True)

    events = [l["event"] for l in logs]
    assert "scan_stall_task_dump_begin" in events
    assert "scan_stall_task_dump_truncated" in events
    assert sum(1 for e in events if e.startswith("scan_stall_task_stack_")) == 2
