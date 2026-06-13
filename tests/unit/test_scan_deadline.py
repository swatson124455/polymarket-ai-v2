"""SCAN_DEADLINE_S — hard wall-clock deadline on one scan_and_trade() (2026-06-13, EB).

Systemic anti-wedge: one deadline replaces chasing each unbounded external call.
WI-21b dumps showed the scan wedging on PandaScore HTTP (get_past_matches, no
timeout) UPSTREAM of the trade step → trading collapsed while DB timeouts
couldn't reach it. _scan_once_bounded wraps the scan in asyncio.timeout when
SCAN_DEADLINE_S>0 (esports only), aborts+continues on expiry.

Safety invariants pinned here:
- DEFAULT 0 → NO asyncio.timeout wrapper → scan runs exactly as before (every
  non-opted-in service byte-identical).
- On deadline expiry the helper SWALLOWS TimeoutError (abort-and-continue) — a
  slow upstream API must not raise into the loop's max-failures-stop.
- _idle_event is always released (finally), both paths.
"""
import asyncio

import pytest


def _make_bot():
    """A BaseBot subclass minimal enough to call _scan_once_bounded."""
    from bots.base_bot import BaseBot

    class _Bot(BaseBot):
        async def scan_and_trade(self):  # overridden per-test
            pass

        async def analyze_opportunity(self, market_data):
            return None

    bot = _Bot.__new__(_Bot)
    bot.bot_name = "TestBot"
    bot._idle_event = asyncio.Event()
    return bot


@pytest.mark.asyncio
async def test_deadline_off_is_passthrough(monkeypatch):
    """SCAN_DEADLINE_S=0 → scan_and_trade runs to completion, no wrapper."""
    import bots.base_bot as bb
    monkeypatch.setattr(bb.settings, "SCAN_DEADLINE_S", 0, raising=False)
    bot = _make_bot()
    ran = {"n": 0}

    async def _scan():
        ran["n"] += 1
    bot.scan_and_trade = _scan

    await asyncio.wait_for(bot._scan_once_bounded(), timeout=2.0)
    assert ran["n"] == 1
    assert bot._idle_event.is_set()  # released


@pytest.mark.asyncio
async def test_deadline_aborts_hung_scan_and_swallows(monkeypatch):
    """SCAN_DEADLINE_S>0 + a scan that hangs forever → aborts at deadline,
    swallows TimeoutError (does NOT raise), releases idle_event."""
    import bots.base_bot as bb
    monkeypatch.setattr(bb.settings, "SCAN_DEADLINE_S", 0.05, raising=False)
    bot = _make_bot()

    async def _hang():
        await asyncio.Event().wait()  # never resolves
    bot.scan_and_trade = _hang

    # If the deadline didn't fire (or re-raised), this outer wait_for would
    # itself time out or propagate — assert neither: returns cleanly.
    await asyncio.wait_for(bot._scan_once_bounded(), timeout=2.0)
    assert bot._idle_event.is_set()


@pytest.mark.asyncio
async def test_deadline_on_fast_scan_completes_normally(monkeypatch):
    """A scan that finishes well under the deadline is unaffected."""
    import bots.base_bot as bb
    monkeypatch.setattr(bb.settings, "SCAN_DEADLINE_S", 5.0, raising=False)
    bot = _make_bot()
    ran = {"n": 0}

    async def _scan():
        await asyncio.sleep(0.01)
        ran["n"] += 1
    bot.scan_and_trade = _scan

    await asyncio.wait_for(bot._scan_once_bounded(), timeout=2.0)
    assert ran["n"] == 1
    assert bot._idle_event.is_set()


@pytest.mark.asyncio
async def test_idle_event_released_when_scan_raises(monkeypatch):
    """A real scan error still propagates AND releases idle_event (finally)."""
    import bots.base_bot as bb
    monkeypatch.setattr(bb.settings, "SCAN_DEADLINE_S", 0, raising=False)
    bot = _make_bot()

    async def _boom():
        raise ValueError("scan blew up")
    bot.scan_and_trade = _boom

    with pytest.raises(ValueError, match="scan blew up"):
        await bot._scan_once_bounded()
    assert bot._idle_event.is_set()
